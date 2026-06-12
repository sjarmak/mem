# mem — Agent Operating Notes

> The **intention + failure-mode-prevention** layer for agents working in this repo.
> It holds only what lives nowhere else; everything general is referenced, not copied.
> Keep it under ~120 lines.

## What this project is

`mem` turns the dolt bead spine plus agent transcripts into a queryable
work-audit graph, so retrieval and the memory-bench eval can learn from past
work without leaking answers. Invariants that must hold:

- **The work-audit graph is the source of truth.** SQLite + FTS5 sidecar at
  `.mem/store.db` (`src/cli/store.ts`), schema version 5
  (`src/store/schema.ts`). Every projected column is rebuilt from the
  `work_records.record` JSON on upsert — never write projections directly.
- **The `lessons` table is append-only** — deliberately no foreign key to
  `work_records`; citations are snapshotted at append time, never joined live
  (`src/store/schema.ts`). There is no in-place schema migration: a version
  bump means rebuilding from the bead spine, and lessons are the one thing a
  rebuild cannot regenerate. Always round-trip them:
  `mem export-lessons` before the rebuild, `mem import-lessons` after
  (README §Building the store).
- **Deterministic signal is mechanical, never model judgment.** Build/test/lint
  outcomes are parsed from tool output by runner matching
  (`src/parse/runners.ts`) and format-anchored `file:line` extractors
  (`src/parse/error-extractors.ts`). Do not add semantic/keyword heuristics to
  this layer; the model is reserved for semantic annotation only (task-type
  residue classification, root-cause extraction).
- **Temporal leave-one-out is load-bearing for eval validity.** Retrieval only
  sees records closed strictly before the target work started — the reader's
  strict `closedBefore` (`src/store/reader.ts`) — and excludes convoy
  siblings, PR/branch sharers (`src/retrieve/exclusions.ts`), and supersedes
  chains via the reader's recursive closure. Weakening any exclusion leaks the
  answer into the eval context.
- **Trace resolution depends on the working directory.** `--with-traces`
  shells `gc session logs` (`src/ingest/trace-resolve.ts`), which loads
  `city.toml` from the cwd — run full rebuilds from `/home/ds/gas-city`.
  Gotcha: run from this repo, a missing `city.toml` exits 0 with zero traces
  resolved; no error is raised. Default (flagless) builds are spine-only and
  fast — keep them that way.
- **CLI contract:** the entrypoint is `./bin/mem` (runs `dist/`, so build
  first); `--json` emits the envelope `{apiVersion, cmd, ok, data?, errors?}`
  (`src/schemas/envelope.ts`).

## Quality gates

CI (`.github/workflows/ci.yml`) and `.pre-commit-config.yaml` mirror each
other; run the gates green before claiming done:

- Python (`memory-bench/`): `ruff check`, `black --check`, `mypy --strict`,
  `pytest`
- TypeScript (root): `npm run check` = `tsc --noEmit` + eslint + prettier
  `--check` + vitest

## Failure-mode preventions

<!-- Append-only log of "don't do X here, it breaks Y" lessons from real incidents.
     One line each: the prevention, then the consequence it avoids. -->

## Where to look (references)

- **Why work records, pipeline, data model, store-building:** `README.md`
- **System design:** `ARCHITECTURE.md`
- **Decision records (oracle curation, gate verdicts):** `docs/`
