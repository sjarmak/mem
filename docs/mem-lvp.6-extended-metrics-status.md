# mem-lvp.6 — extended metrics (§12 + DIV-4) status

Status of every spec §12 metric group + the DIV-4 privacy/interruption extensions, as
of the `mem-lvp.6-retention-schedule` branch. Scope discipline: frozen spec §12 +
DIV-4 only — no new eval-design choices were opened.

## Landed

| Leg | Spec | Where | State |
|---|---|---|---|
| Outcome | §12.1 | `runner/metrics.py::compute_metrics` (inline `TaskMetrics`) | **done** (reward / pass / final_goal_success / verifier_errors; `rubric_score`/`completion_quality` are judge seams, left None) |
| Efficiency | §12.2 | `metrics/scorers.py::score_efficiency` | **done** |
| Retrieval | §12.3 | `metrics/scorers.py::score_retrieval` | **done** (precision/recall/mrr/nDCG/distractor/stale) |
| Retention | §12.4 | `metrics/scorers.py::score_retention` + the retention-schedule leg (b8c98fc) | **done** |
| Synthesis | §12.5 | `metrics/scorers.py::score_synthesis` | **done** (judge seams left None) |
| **Privacy** | DIV-4 | `metrics/scorers.py::score_privacy` (**this commit**) | **done** — deterministic `leakage_flags` + `privacy_class` passthrough |

§12.1–12.5 were already implemented + committed (concurrent work); this branch verified
them green (the focus-review gate) and added the **privacy** scorer — the one remaining
deterministic, fully-frozen leg.

### Privacy (DIV-4) — what landed

`score_privacy(PrivacyInputs)` is pure mechanism (ZFC), matching the other scorers'
"pure function of explicit inputs" shape:

- **`leakage_flags`** — the frozen cross-rig-in-strict check: a `cross_rig` run must
  never inject SAME-rig content, so each injected id whose source rig equals the task's
  rig is flagged `cross_rig_same_rig_injection:<id>`. A `same_rig_temporal` run
  injecting same-rig content is expected and never flagged. Provenance defaults to the
  honest "not measured here" (empty) — like `distractor_ids`/`stale_ids` — so a run that
  does not thread rig provenance reports `[]`, not a fabricated clean bill.
- **`privacy_class`** — the DIV-4 model-classified bucket (`none`/`internal`/`sensitive`)
  is passed THROUGH after a vocabulary check (off-bucket raises). The classification
  itself is a **judge seam**, decided upstream by a model — never inside the scorer,
  per the `metrics/` ZFC boundary. Wiring a real classifier follows the
  `ClaudeRubricJudge` pattern (injectable, CI-stubbed) when a privacy-judge bead lands.

**Wiring note:** `score_privacy` is a pure scorer ready to call; `compute_metrics` does
NOT yet invoke it because the leak check needs `run_scope` + per-injected-item rig
provenance threaded through the runner, which is not currently plumbed. Threading that
is a mechanical follow-up — improvising provenance now would fabricate data, so it is
deliberately left to the wiring step.

## Forks — surfaced, NOT improvised (frozen-spec discipline)

### Action-impact (§12.6) — deferred judge seam
`ActionImpactMetrics` (memory_changed_tool_choice / _plan / _output /
prevented_known_failure / improved_verification) is **intentionally a judge seam**: the
`metrics/` module docstring leaves these `None` by design, and their derivation is a
counterfactual ("would the agent have chosen differently WITHOUT this memory?") that
requires either paired memory-on/off runs or a judge — a derivation method that is **not
frozen**. Building a deterministic version would violate the ZFC boundary; building a
judge version requires an eval-design decision. → **Fork: needs a frozen action-impact
derivation decision (counterfactual-pairing vs judge) before implementation.**

### Interruption (DIV-4) — blocked on mem-dsu
The interruption metric leg is the matched-pair target of **mem-dsu** (adopt the
Handoff-Debt takeover protocol: frozen-checkpoint matched pairs, 3 interruption points,
4 view arms). **mem-dsu is OPEN** — the protocol is specified but not settled into the
§11 generator. Per the dispatch instruction, the interruption eval design is NOT
improvised here. → **Fork: blocked on mem-dsu; build the interruption generator + metric
once that protocol is adopted.**

## Bead state
`mem-lvp.6` stays **OPEN**: privacy landed branch-ready (this branch, not pushed); the
two forks above remain. Branch-ready — mayor publishes after Stephanie approval.
