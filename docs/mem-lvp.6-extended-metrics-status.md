# mem-lvp.6 — extended metrics (§12 + DIV-4) status

Status of every spec §12 metric group + the DIV-4 privacy/interruption extensions, as
of the `mem-lvp.6-retention-schedule` branch. Scope discipline: frozen spec §12 +
DIV-4 only; no new eval-design choices were opened.

## Landed

| Leg | Spec | Where | State |
|---|---|---|---|
| Outcome | §12.1 | `runner/metrics.py::compute_metrics` (inline `TaskMetrics`) | **done** (reward / pass / final_goal_success / verifier_errors; `rubric_score`/`completion_quality` are judge seams, left None) |
| Efficiency | §12.2 | `metrics/scorers.py::score_efficiency` | **done** |
| Retrieval | §12.3 | `metrics/scorers.py::score_retrieval` | **done** (precision/recall/mrr/nDCG/distractor/stale) |
| Retention | §12.4 | `metrics/scorers.py::score_retention` + the retention-schedule leg (b8c98fc) | **done** |
| Synthesis | §12.5 | `metrics/scorers.py::score_synthesis` | **done** (judge seams left None) |
| **Privacy** | DIV-4 | `metrics/scorers.py::score_privacy` | **done** — deterministic `leakage_flags` + `privacy_class` passthrough |
| **Interruption** | DIV-4 | `metrics/scorers.py::score_interruption` (**this commit**) | **done (mechanical)** — `inject_timing` wired against the mem-dsu generator; `derailment_signal` left a judge seam |

§12.1–12.5 were already implemented + committed (concurrent work); this branch verified
them green (the focus-review gate) and added the **privacy** then **interruption** scorers,
the remaining deterministic, frozen-spec legs.

### Interruption (DIV-4) — what landed

The mem-dsu interruption generator (`generators/interruption.py`, merged from
`mem-dsu-interruption-generator`) is now on this branch, so the previously-forked
interruption leg is wired: `score_interruption(InterruptionInputs)` (pure mechanism).

- **`inject_timing`** (`on_failure` / `off_failure`): read from the predecessor
  trajectory's ACTUAL validation outcomes at the frozen checkpoint: `on_failure` iff a
  validation FAILED on or before the checkpoint (the takeover lands on a live failure
  signal), never hardcoded by point name; a trajectory whose first validation passes
  classifies `off_failure`. The 4 view arms of a point share one timing (it is
  a property of the point, verified end-to-end against `generate_handoff_tasks`).
- **`derailment_signal`** magnitude is left `None`: the added-iterations / abandonment
  effort proxy rides the efficiency metrics (`handoff_efficiency`), and the semantic
  derailment scalar is the model's call (ZFC), not decided in the scorer.

### Privacy (DIV-4) — what landed

`score_privacy(PrivacyInputs)` is pure mechanism (ZFC), matching the other scorers'
"pure function of explicit inputs" shape:

- **`leakage_flags`**: the frozen cross-rig-in-strict check: a `cross_rig` run must
  never inject SAME-rig content, so each injected id whose source rig equals the task's
  rig is flagged `cross_rig_same_rig_injection:<id>`. A `same_rig_temporal` run
  injecting same-rig content is expected and never flagged. Provenance defaults to
  "not measured here" (empty), like `distractor_ids`/`stale_ids`, so a run that
  does not thread rig provenance reports `[]`, not a fabricated clean bill.
- **`privacy_class`**: the DIV-4 model-classified bucket (`none`/`internal`/`sensitive`)
  is passed THROUGH after a vocabulary check (off-bucket raises). The classification
  itself is a **judge seam**, decided upstream by a model, never inside the scorer,
  per the `metrics/` ZFC boundary. Wiring a real classifier follows the
  `ClaudeRubricJudge` pattern (injectable, CI-stubbed) when a privacy-judge bead lands.

**Wiring note:** `score_privacy` is a pure scorer ready to call; `compute_metrics` does
NOT yet invoke it because the leak check needs `run_scope` + per-injected-item rig
provenance threaded through the runner, which is not currently plumbed. Threading that
is a mechanical follow-up; improvising provenance now would fabricate data, so it is
deliberately left to the wiring step.

## Open fork — surfaced, NOT improvised (frozen-spec discipline)

### Action-impact (§12.6) — OPEN judge seam (deliberately NOT frozen)
`ActionImpactMetrics` (memory_changed_tool_choice / _plan / _output /
prevented_known_failure / improved_verification) is **intentionally a judge seam**: the
`metrics/` module docstring leaves these `None` by design, and their derivation is a
counterfactual ("would the agent have chosen differently WITHOUT this memory?") that
requires either paired memory-on/off runs or a judge; a derivation method that is **not
frozen**. Building a deterministic version would violate the ZFC boundary; building a
judge version requires an eval-design decision. → **Fork: kept OPEN; needs a frozen
action-impact derivation decision (counterfactual-pairing vs judge) before
implementation. Do not freeze the counterfactual derivation here.**

_(The interruption leg, previously forked on mem-dsu, is now LANDED, see above, after
the mem-dsu generator merged onto this branch.)_

## Bead state
`mem-lvp.6` stays **OPEN**: retention, privacy, and interruption (mechanical) all landed
branch-ready on this branch (not pushed); only the §12.6 action-impact judge-seam fork
remains. Branch-ready. Mayor publishes after Stephanie approval.
