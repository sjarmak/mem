# Architecture diagram (LikeC4)

Architecture-as-code model of `mem`, rendered with [LikeC4](https://likec4.dev).
The model is the source of truth across [`spec.c4`](spec.c4) (element kinds,
tags, deployment node kinds), [`model.c4`](model.c4) (the system), and
[`views.c4`](views.c4) (structure, walkthrough, and risk views), with the
deployment model in [`deployment.c4`](deployment.c4). The narrative companion is
the repo-root [`ARCHITECTURE.md`](../ARCHITECTURE.md).

Every element `link`s to its source (`src/…`, `memory-bench/…`) and, where one
exists, to the relevant entry in the chronological decision log
([`docs/architecture-decisions.md`](../docs/architecture-decisions.md), Decisions
1–17) — so any box in the explorer is one click from the code and the rationale
behind it.

## Delivery state is tagged, not guessed

Every element carries a tag so **planned and research work renders distinctly
from what is already built** (legend in `spec.c4`):

| Tag | Meaning | Render |
|---|---|---|
| `#built` | code path exists and is exercised | solid |
| `#evolving` | built, but the science/contract is still moving | solid |
| `#planned` | designed; not yet implemented (or v1 is a stub/heuristic) | **dashed, dimmed** |
| `#research` | speculative `research/` track | **dashed, indigo** |

Planned items in the model: the 6-stage memory controller (MCP server), the
multi-session sequence eval object, and the fine-tuning / RL reranker
(research track).

## Views

**Structure** — the static map:

| View | Scope |
|---|---|
| `index` | system landscape — `mem` in context of the orchestrator, GitHub, Harbor, inference models |
| `memSystem` | the `mem` system decomposed into containers (built vs planned) |
| `storeContainer` | TypeScript half (`src/`) — ingest / parse / store / retrieve / distill internals |
| `benchContainer` | Python eval harness (`memory-bench/`) component internals |
| `armsView` | the competitive memory arms (none / ours / oracle / filesystem / mem0 / A-MEM / NAT / Graphiti / …) |
| `gradingView` | the validity gates + scoring stack |
| `planned` | planned + research work, with built dependencies dimmed |
| `deployment` | where each piece runs — process & data boundaries (Node CLI + SQLite sidecar, Python harness, Harbor, inference host) |

**Walkthrough flows** (dynamic / numbered-step views) — the narrative spine for
a design-review walkthrough:

| View | Flow |
|---|---|
| `buildStore` | building the store from the audit (ingest → parse → store → distill) |
| `evalRun` | one benchmark run end-to-end (assemble → soundness gate → 3-condition replay → grade → report) |
| `retrievalFlow` | failure-triggered retrieval at agent runtime (error → signature → LOO query → progressive disclosure) |
| `controllerLoop` | the planned 6-stage memory-controller loop |

**Risk lens:**

| View | Scope |
|---|---|
| `risks` | the `#risk`-flagged elements with each open question stated in-box (outcome sparsity, base-commit capture, small-N oracle pool, headline still being pinned) |

### Running the walkthrough

For a design review, present in this order: `index` → `memSystem` (orient on
structure) → the four walkthrough flows in sequence (what actually happens) →
`deployment` (where it runs) → `risks` (what to probe) → `planned` (what's next).
In `npx likec4 start`, the dynamic views animate step-by-step and each view's
notes panel carries the gotchas (the `gc`-cwd / verify-before-swap caveat, the
three-condition contract, the determinism guarantee).

## Viewing & regenerating

```bash
# Interactive, hot-reloading explorer (recommended)
npx likec4 start architecture

# Re-export the static PNGs in exports/ (needs a one-time browser download:
#   npx playwright install chromium-headless-shell)
npx likec4 export png architecture -o architecture/exports

# Validate the model (strict — the source of truth for correctness)
npx likec4 validate architecture
```

Pre-rendered PNGs live in [`exports/`](exports/).

### Viewing the interactive explorer over SSH (headless remote)

`likec4 start` serves a Vite dev server on `localhost:5173`. From a headless
remote, forward that port to your laptop and open it locally — three options,
easiest first:

1. **VS Code / Cursor Remote-SSH** — run `npx likec4 start architecture` in the
   integrated terminal; the editor auto-forwards 5173 and offers "Open in
   Browser". Nothing else to configure.
2. **SSH local port-forward** — on your laptop:
   ```bash
   ssh -N -L 5173:localhost:5173 user@remote   # leave running
   ```
   then on the remote `npx likec4 start architecture` and open
   <http://localhost:5173> locally. (Already in an SSH session? Add the tunnel
   without reconnecting: press `~C` then type `-L 5173:localhost:5173`.)
3. **Bind + reach directly** — `npx likec4 start architecture --listen 0.0.0.0`
   and browse to `http://<remote-ip>:5173` (only if that port is reachable /
   firewall-open; the tunnel in option 2 is safer).

No browser at all? The pre-rendered [`exports/`](exports/) PNGs (and
`npx likec4 export png` to refresh them) need no display — `scp` them down, or
view inline if your terminal supports images.

## Observed-code review pilot

The local reviewer adds a source-derived dependency map alongside the authored
LikeC4 model. It currently scans TypeScript and JavaScript implementation files
under `src/`. It does not scan the Python benchmark harness.

```bash
npm ci
npm run review:serve
# One-time service startup; open http://127.0.0.1:4173/ in your browser.
```

Use the HTTP address above rather than clicking `index.html` in an agent chat:
some clients route local file links to their Git diff viewer. The preview server
runs on the machine hosting this checkout. From another machine, forward port
4173 over SSH (for example `ssh -N -L 4173:127.0.0.1:4173 ds-5090`), then open
that same HTTP address. Stop the server with Ctrl-C. Once running, no CLI commands are needed for reviews.

Choose a baseline commit in **Compare working tree against**, then click
**Refresh changes**. The service rescans current files, including untracked source,
and keeps the chosen commit fixed even if an agent creates new commits. Choose
an earlier commit to review committed work, or the latest commit for uncommitted
edits. The dropdown includes the latest 40 commits; older commits or branch refs
can also be opened with `/?baseline=<ref>` and resolve to a pinned commit.

Open **Review with an agent** and click **Launch review agent**. A separate local
Codex process reviews the pinned baseline against the working tree and returns
findings on this page. Select a module and enter a concern to focus it; use
**Package overview** to review the whole diff. **Stop review** cancels the run.
Reloading reconnects to the latest run while the service stays up. Results are
held in memory until the next run or service restart; copy findings to retain them.
The copyable agent request remains available for other conversations.

The launcher requires an installed, signed-in `codex` on the service's PATH.
It uses the existing sign-in and Codex's default model with user configuration
ignored, a read-only sandbox, and approvals disabled. It instructs the agent to
inspect code without editing files, creating issues, running tests, or launching
other agents. This review uses your Codex account. Runtime/auth failures appear
as failed reviews, never successful empty results. One run per service is
allowed; duplicate request IDs do not relaunch. Runs stop after 30 minutes.

Launches validate the scanned source fingerprint; refresh if source changed.
Files can still change during a review, and the fingerprint covers only the
scanner's supported scope. Findings retain the baseline and scan identifier.
After edits, click **Refresh changes**, then launch a new review as needed.

The Node service serves known UI assets, review snapshots and the fixed agent
API. Launch and stop require a page token and same-origin JSON request. It binds
to loopback by default; use `-- --host <trusted-interface-IP>` to bind another
interface. Anyone who can access this private service can read source and use
the local Codex account to launch reviews; it is not a public authenticated app.
For a portable static snapshot, `npm run review:build -- --baseline HEAD~1`
remains available; exported pages have no live refresh or agent-launch controls.

The default **Architecture diff** shows added, removed and code-changed modules
plus their unchanged neighboring modules. Green arrows are added imports, dashed
red arrows are removed imports, and muted arrows preserve unchanged context.
**What changed** counts module and import changes; expand **Dependency changes**
to jump to the exact current or baseline source line. Changed external and unresolved imports appear as labeled import nodes in the
diagram and in this list; selecting one opens its importing module.
A code-only edit still highlights its module. A clean scan explicitly shows no
changes; choose an earlier baseline to inspect committed work.

Use **Package overview** for the full architecture, choose a module, and follow its import or
reverse-import evidence. `L…` buttons open the exact captured source line.
Use **Changed modules** to inspect additions, deletions and content changes;
select the baseline version to read earlier source. Search matches paths and
named function, class, interface and type declarations. The diagram's arrows
point from importer to dependency; type-only imports are included in cycles.
Package arrows aggregate current and removed imports; individual module views
label dependency changes.

The default baseline is `HEAD`, so a clean tree has no changes. Both revisions,
source/configuration fingerprints, generation time, scan exclusions, parser
errors and unresolved imports are available in the page. Click Refresh changes
after edits; there is no automatic watcher. Missing refs and missing source
roots show an error rather than producing an empty report. Git must have at
least one commit. Node 20 or newer is recommended for the full validation suite.

**Prepare a finding** produces a JSON packet containing the selected module's
hash, dependency evidence, concern and snapshot identifiers. Copy/download it
for an agent; the agent should verify freshness, investigate, and record an
accepted finding with `bd create` before implementing and regenerating the map.
Draft concerns survive module navigation and live refreshes in the same browser
tab when session storage is available; download packets for durable records. Package overview clears active filters.
Large diagrams scroll within the graph panel to keep labels readable.
The page creates no issues. Agent findings are separate from the observed graph.

Coverage, complexity, mutation scores and architectural policy checks are
explicitly **not measured**. Module resolution uses the TypeScript compiler API
against captured files. Inherited `tsconfig` settings, package exports/imports,
external installations and roots outside `src/` are not loaded; aliases can
therefore remain unresolved. Imports called through a shadowed `require` can
be false positives. Dependency comparisons collapse repeated imports with the
same source, target, specifier, kind and resolution into one record.

Generated HTML and `report.json` contain current and baseline source text. They
are ignored local artifacts. The existing Pages workflow does not publish them.
To assemble a combined site locally, generate the review into `_site/review`
and pass `--review ./review/` to `architecture/site/build-page.mjs` alongside its
usual model/figure arguments. The review header links to the published mem
architecture overview.

```bash
npm run review:test
npx playwright install chromium
npm run review:e2e
```

Scanner/build tests run in `npm run check`; CI also runs the browser journey and
retains screenshots/traces on failure. The browser test uses a temporary Git
repository, so it does not change project source or history.
