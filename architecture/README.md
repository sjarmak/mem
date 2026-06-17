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
