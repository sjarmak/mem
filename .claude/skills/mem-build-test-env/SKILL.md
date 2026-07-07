---
name: mem-build-test-env
description: >
  Recreate and verify both mem development environments from scratch and get
  green locally the way CI is green: root TypeScript half (npm ci + npm run
  build + npm run check) and Python eval half (memory-bench/ pip install -e
  ".[dev]" or uv), the two CI jobs, pre-commit parity, mypy --strict and
  black-is-sole-formatter discipline, optional-dependency skip semantics, and
  the build-first / wrong-cwd traps. Load when setting up a fresh clone, when
  CI fails and local passes (or vice versa), when tests skip unexpectedly,
  when the mem CLI behaves stale after a src/ edit, or when adding a dev
  dependency. NOT for running the eval end-to-end — use mem-eval-harness-run.
  NOT for rebuilding .mem/store.db or ingest — use mem-ingest-and-provenance
  and the ingest-trace-substrate skill. NOT for schema bumps — use
  mem-store-schema-and-rebuild. NOT for what mem IS — use mem-orientation.
---

# mem — build and test environment

`mem` is two projects in one repo, each with its own dependency world and its
own CI gate. There is no `make`; each half runs its own project's commands.

| Half                | Where                                   | Language                           | Install                   | Gate                         |
| ------------------- | --------------------------------------- | ---------------------------------- | ------------------------- | ---------------------------- |
| Store builder + CLI | repo root (`src/`, `tests/`, `bin/mem`) | TypeScript, Node >=18 (CI runs 20) | `npm ci`                  | `npm run check`              |
| Eval harness        | `memory-bench/` (`membench/`, `tests/`) | Python >=3.12 (CI runs 3.12)       | `pip install -e ".[dev]"` | ruff + black + mypy + pytest |

Ignore the `mem-*/` directories at the repo root: they are stale agent-worktree
copies of the whole tree, not source. The real project is `src/`, `bin/`,
`tests/`, `scripts/`, `memory-bench/`, `docs/`.

## When NOT to use this skill

| You want to...                         | Use instead                                       |
| -------------------------------------- | ------------------------------------------------- |
| Run the benchmark / eval conditions    | mem-eval-harness-run                              |
| Rebuild or refresh `.mem/store.db`     | mem-ingest-and-provenance, ingest-trace-substrate |
| Bump the store schema version          | mem-store-schema-and-rebuild                      |
| Understand the repo / thesis           | mem-orientation                                   |
| Know how change is gated / pushed here | mem-git-and-dispatch-workflow                     |

## 1. TypeScript half (repo root)

### Setup from scratch

```bash
cd <repo-root>
npm ci            # exact install from package-lock.json (better-sqlite3 is a
                  # native module; npm ci fetches a prebuilt binary or compiles it)
npm run build     # tsc -p tsconfig.build.json -> dist/  (REQUIRED before using the CLI)
```

### TRAP: `./bin/mem` runs `dist/`, not `src/`

`bin/mem` line 3 does `import { main } from '../dist/main.js'`. If you edit
anything under `src/` and run `./bin/mem` without `npm run build`, the CLI
**silently executes stale compiled code**. No warning, no error. Build first,
always. `scripts/check-env.sh` in this skill detects the stale-dist condition.

Two related facts:

- `node dist/main.js` alone does nothing — `main.ts` only defines the
  function; the entrypoint is `./bin/mem` (or `node bin/mem`).
- `dist/` and `node_modules/` are gitignored; a fresh clone has neither.

### TRAP: `--with-traces` needs the gas-city working directory

Not a build issue, but it bites right after setup: `mem build-store
--with-traces` / `mem ingest-traces` shell out to `gc session logs`
(`src/ingest/trace-resolve.ts`), which loads `city.toml` from the cwd. Run
from the wrong directory and it **exits 0 with zero traces resolved** — a
silently corrupted rebuild. A missing `gc` binary DOES propagate as an error;
a missing `city.toml` does not (deliberate distinction). Full-rebuild runs are
executed from the orchestrator workspace directory (on this machine,
`/home/ds/gas-city`) with an absolute `--store` path — this is an operational
precondition of the orchestrator host, not a code path you can satisfy in a
bare clone. PROVISIONAL pending Stephanie (Q1, placement/audience). Default
flagless builds are spine-only and have no cwd dependency. Details:
mem-ingest-and-provenance and the checked-in `ingest-trace-substrate` skill.

### The gate: `npm run check`

`npm run check` = exactly what the CI `typescript` job runs after `npm ci`.
It chains four sub-commands and stops at the first failure:

| Step                   | Command                                                     | What it checks                                                                                         |
| ---------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `npm run typecheck`    | `tsc --noEmit`                                              | types, full project (tsconfig.json)                                                                    |
| `npm run lint`         | `eslint src tests`                                          | type-checked lint: `recommended-type-checked` rules, `no-explicit-any: error`, prettier-as-eslint-rule |
| `npm run format:check` | `prettier --check "src/**/*.ts" "tests/**/*.ts" "bin/**/*"` | formatting only (read-only)                                                                            |
| `npm test`             | `vitest --run`                                              | 43 `tests/**/*.test.ts` files (count as of 2026-07-07)                                                 |

Fix-up variants: `npm run lint:fix`, `npm run format` (writes). Vitest
discovery is deliberately scoped to `tests/` in `vitest.config.ts` so it never
walks generated dirs like `.mem/` (whole-project scan used to crash on
`EACCES`); don't "fix" that by widening `include`.

Note `npm run check` does NOT run `npm run build` — a green check does not
mean `dist/` is fresh. Building is a separate, deliberate step.

## 2. Python half (`memory-bench/`)

### Setup from scratch — CI-canonical path

```bash
cd <repo-root>/memory-bench
python3 -m venv .venv && source .venv/bin/activate   # any venv tool works
pip install -e ".[dev]"    # membench editable + pytest, ruff, mypy, black, types-toml
```

`[dev]` = `pytest>=8.0, ruff>=0.6, mypy>=1.11, black>=24.0, types-toml>=0.10`
(pyproject.toml). This is exactly what CI installs; Harbor and every
third-party memory SDK are NOT included and NOT needed for a green gate
(section 4). Installing the package also gives you the `membench` console
script (`membench = "membench.cli:main"`).

Local convention on this machine (as of 2026-07-07): the checkout carries a
`uv`-managed venv at `memory-bench/.venv` (no `pip` binary inside — use
`uv pip ...` or `uv sync`) with membench installed editable plus the `harbor`
extra. `uv.lock` is checked in. Either path works; CI uses plain pip.

Optional extra: `pip install -e ".[dev,harbor]"` adds `harbor>=0.3`
(harbor-framework/harbor, Apache-2.0), the execution substrate for real
`harbor run` invocations. Only needed to execute real runs; the test suite
skips Harbor-dependent tests when it is absent.

### The gate: four commands, same order as CI

Run from `memory-bench/` (wrong cwd = "file not found" or an empty run):

```bash
ruff check membench tests
black --check membench tests
mypy --strict membench
pytest -q
```

Expected as of 2026-07-07 (main @ 4e819e1): 157 `tests/test_*.py` files,
2121 tests collected; a fresh-clone dev-only install passes with a number of
skips (section 4) and zero failures.

### Formatter/linter/typechecker discipline

- **black is the sole formatter.** ruff lints but never formats; the two are
  configured not to fight (pyproject comment, shared `line-length = 100`).
  Never run `ruff format`, never hand-align against black.
- **ruff select** is broad: `E, F, I, UP, B, SIM, C4, RUF, N` — includes
  import-sorting (`I`), so ruff owns import order, black owns everything else.
- **`mypy --strict` applies to `membench` only, not `tests/`.** Adding strict
  errors to test files is not required; adding un-strict code to `membench/`
  fails CI. Three deliberate relaxations in pyproject.toml — do not widen
  them casually: `untyped_calls_exclude = ["opentelemetry"]` (callee-based,
  our own code stays strict), `ignore_missing_imports` for the optional
  memory SDKs (`mem0`, `agentic_memory`, `nat.*`, `graphiti_core`,
  `data_designer`, `sentence_transformers`), and the same for `harbor.*`
  (lazily imported, no stubs, required at runtime only where imported).
- Version pinning quirk (as configured, verbatim): `requires-python = ">=3.12"`
  while black/ruff `target-version` is py311 and mypy `python_version = "3.11"`.
  Leave as-is unless a Decision changes it.

## 3. CI and pre-commit parity

CI (`.github/workflows/ci.yml`) is the real bar: two independent jobs.

| Job                                                              | Runs                                                                                                                              |
| ---------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `python` (ubuntu, Python 3.12, working-directory `memory-bench`) | `pip install -e ".[dev]"` → `ruff check membench tests` → `black --check membench tests` → `mypy --strict membench` → `pytest -q` |
| `typescript` (ubuntu, Node 20)                                   | `npm ci` → `npm run check`                                                                                                        |

`.pre-commit-config.yaml` mirrors CI as **local `language: system` hooks** so
the rules live in exactly one place (pyproject / package.json):

- `py-black`, `py-ruff`, `py-mypy` — each `cd memory-bench && <tool> ...`,
  triggered by changes under `memory-bench/` (mypy hook only for
  `memory-bench/membench/`).
- `ts-check` — `npm run typecheck && npm run lint && npm run format:check`
  on ts changes. Note: pre-commit does NOT run vitest or pytest; only CI does.

`language: system` means the hooks use whatever `black`/`ruff`/`mypy`/`npm`
are on your PATH — activate the venv (or ensure the tools resolve) before
committing. Enable once per clone with `pre-commit install`; as of 2026-07-07
this checkout has no `.git/hooks/pre-commit` installed, so the gate discipline
here is "run the commands yourself before claiming done" (CLAUDE.md Quality
gates section says the same).

A second workflow, `likec4-pages.yml`, auto-deploys the architecture page; it
is not part of the code gate.

## 4. Optional-dependency skip semantics — skips are by design

CI runs with NO Harbor, NO GPU, NO paid API, NO third-party memory SDK
installed. The suite is engineered so that is still a meaningful green:

| Optional dep                                                                      | Where gated                                                                                                                               | Behavior when absent                                                                                                                                                                                               |
| --------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `harbor` (extra)                                                                  | `pytest.importorskip("harbor...")` / `skipif(find_spec("harbor") is None)` in `tests/test_harbor_exec.py`, `tests/test_harbor_adapter.py` | Harbor-touching tests skip; task dirs still emitted/validated structurally                                                                                                                                         |
| `data_designer` (NeMo)                                                            | `tests/test_nemo_world_builder.py` importorskip                                                                                           | NeMo world-builder tests skip                                                                                                                                                                                      |
| `mem0`, `agentic_memory` (A-MEM), `nat`, `graphiti_core`, `sentence_transformers` | lazily imported inside each arm's default-client factory (e.g. `default_mem0_client` in `membench/memory_systems/mem0_system.py`)         | importing the module needs neither SDK nor network; arm tests inject `tests/semantic_fakes.py::FakeSemanticClient` (deterministic token-overlap scoring, scope-isolated, mints its own ids like the real backends) |
| TS build (`dist/`) + node + `node_modules`                                        | `tests/paths.py::require_mem_cli`                                                                                                         | `ours`-arm / CLI-driving integration tests skip with "TS build missing (run `npm run build`)" etc.                                                                                                                 |

Rules of interpretation:

1. **A skip from these guards is never a failure.** Do not "fix" it by adding
   the SDK to `[dev]`, un-gating the import, or deleting the test.
2. **`require_mem_cli` deliberately checks `node_modules` too** — so a runtime
   "Cannot find module" inside a test is always a real packaging regression
   (hard failure), never an environment gap to skip over. Keep that property.
3. If you want those tests to RUN locally: `npm ci && npm run build` at the
   root un-skips the CLI integration tests; `pip install -e ".[dev,harbor]"`
   un-skips the Harbor tests.
4. Arms are tested against deterministic fakes in CI on purpose (no network,
   no model). Real-backend runs are an eval concern — mem-competitive-arms.

## 5. Fresh-clone green checklist (both halves)

```bash
# --- TypeScript half ---
cd <repo-root>
npm ci
npm run build          # dist/ must exist before any ./bin/mem use
npm run check          # tsc + eslint + prettier --check + vitest

# --- Python half ---
cd memory-bench
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check membench tests
black --check membench tests
mypy --strict membench
pytest -q              # expect skips (Harbor/NeMo/SDK/dist guards), zero failures

# --- Optional: enable the local hook mirror ---
pre-commit install     # from repo root; hooks call your PATH's tools
```

Diagnostic helper (read-only, this skill):

```bash
.claude/skills/mem-build-test-env/scripts/check-env.sh
```

It reports node/python versions, `node_modules`/`dist` presence, the
stale-dist condition (src newer than dist), venv + tool availability, and
which optional deps are importable — it changes nothing.

## 6. Troubleshooting table

| Symptom                                               | Cause                                        | Fix                                                   |
| ----------------------------------------------------- | -------------------------------------------- | ----------------------------------------------------- |
| CLI ignores your `src/` edit                          | stale `dist/` (build-first trap)             | `npm run build`                                       |
| `node dist/main.js` prints nothing                    | wrong entrypoint                             | use `./bin/mem`                                       |
| `Cannot find module '../dist/main.js'` from `bin/mem` | never built                                  | `npm run build`                                       |
| better-sqlite3 install/ABI error on `npm ci`          | native module vs Node version                | use Node 20 (CI's version); reinstall `npm ci`        |
| Many pytest skips                                     | optional-dep guards (section 4)              | expected; install extras only if you need those paths |
| `mypy --strict` errors only in CI                     | ran it on `tests/` locally or from wrong cwd | run `mypy --strict membench` from `memory-bench/`     |
| black and ruff appear to disagree                     | you ran `ruff format`                        | don't; black is the sole formatter                    |
| pytest collects 0 tests                               | wrong cwd                                    | run from `memory-bench/` (`testpaths = ["tests"]`)    |
| vitest crashes scanning `.mem/`                       | `include` widened in vitest.config.ts        | restore `include: ['tests/**/*.test.ts']`             |
| `--with-traces` "succeeds" with zero traces           | wrong cwd, `city.toml` not found             | see section 1 trap; mem-ingest-and-provenance         |

## Provenance and maintenance

Verified 2026-07-07 against `/home/ds/projects/mem`, branch `main`, HEAD
`4e819e1` (every command, path, count, and config value above read from the
repo this session; pytest collect and `./bin/mem --help` executed live).
Volatile facts pinned to that date: 43 vitest test files, 157 pytest files /
2121 collected, Node 20 + Python 3.12 in CI, the local uv venv, the absent
pre-commit hook.

Re-verify before trusting drift-prone claims:

```bash
git -C <repo-root> log -1 --format='%h %s'                      # has the pin moved?
grep -n '"check":' package.json                                  # gate composition
sed -n '12,41p' .github/workflows/ci.yml                         # the two CI jobs
grep -n 'dev = ' memory-bench/pyproject.toml                     # [dev] extra contents
ls tests/*.test.ts | wc -l                                       # vitest file count
ls memory-bench/tests/test_*.py | wc -l                          # pytest file count
(cd memory-bench && pytest --collect-only -q | tail -1)          # collected count
head -5 bin/mem                                                  # dist-import trap still true?
grep -n 'importorskip\|skipif' memory-bench/tests/*.py           # skip-guard locations
```
