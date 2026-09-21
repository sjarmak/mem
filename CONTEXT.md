# Glossary: beads memory eval flywheel

Vocabulary only. No implementation detail lives here.

- **Subject under test**: the beads (`bd`) memory system. Never the mem SQLite store, never the harness.
- **Arm**: one memory condition an agent runs under. Three arms exist: **treatment** (beads), **floor** (`none`: no durable store), **comparator** (`builtin`: the agent runtime's own native memory).
- **Cell**: one (arm, variant, task, repeat) tuple. A cell is bought once and never repurchased.
- **Leg**: one agent session inside a cell. The **establish leg** is told the facts and asked to record them. The **goal leg** is a fresh session asked to act on those facts. The goal leg is the only leg the primary endpoint is scored on.
- **Capture**: what the establish leg did with the facts. Two counted quantities, per arm:
  - **reached**: the arm's own store was called at all, whatever came back.
  - **engaged**: the arm's own store holds the task's value afterwards (content, never file existence).
  The floor arm cannot reach or engage by construction.
- **Capture endpoint**: engaged rate per arm over a set of establish-only cells. Registered separately from the goal endpoint.
- **Goal endpoint**: paired goal-leg success delta, treatment minus floor, on the memory-necessary variant.
- **Variant**: **necessary** (the value exists only in the establish leg) or **unnecessary** (the value is derivable without memory; the twin the floor should pass).
- **Leak**: a goal pass with no engagement. Booked against the floor as evidence nothing was measured, never as a win.
- **Unmeasured**: a result the pre-registration declares void of meaning (a leak, a floor failing the unnecessary twin, a disabled comparator). Reported as such, never written up as a null.
- **Turn**: one iteration of the flywheel: a **candidate** build of beads measured on the capture endpoint against the **baseline** build.
- **Candidate**: a specific beads build, named by commit, that differs from the baseline in what beads ships (prime text, hooks, capability text, budget). The harness instruction is never a candidate.
- **Baseline**: the beads build all candidates in a turn are compared to. Measured once per (beads build, agent runtime version).
- **Shared protocol**: the fields no arm may differ in (corpus, goal-leg tools, scorer, model, runtime version, timeout, sandbox policy).
