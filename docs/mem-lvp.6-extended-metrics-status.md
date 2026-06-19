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

### Action-impact (§12.6) — LANDED branch-ready (fork resolved: Path 1 + Path 3)
The fork was ratified 2026-06-19 (mayor gc-390342, Stephanie): build the §12.6
action-impact as the **Path 1 (mechanical pre-filter) + Path 3 (judge seam)** design,
reusing the bbon comparative-judge transport. Implemented in
`membench/metrics/action_impact.py` (16 tests, full suite 1291 green):

- **Path 1 — `diff_trajectories` (pure ZFC mechanism).** Index-aligns the memory-on
  vs memory-off tool-call streams and reports, per behavioral axis
  (tool_choice = `kind` sequence, plan = `(kind, input)` sequence, output =
  `(output, observation)` sequence), whether they STRUCTURALLY differ. `output_observable`
  flags whether the output fields carried data (membench tool-use steps usually do not),
  so an "identical" output axis is only treated as informative when observed.
- **Path 3 — `score_action_impact(inp, judge=None)`.** Emits the five
  `ActionImpactMetrics` booleans. The judge is the SAME `complete(prompt) -> str` seam the
  bbon comparative judge exposes (`ComparativeJudge`): reuse `ClaudeComparativeJudge`, the
  future LocalModelStack-backed OSS judge, or `StubComparativeJudge(fn=...)` offline.
- **Pre-filter + cross-check (exactly as the decision specifies).** An axis the streams
  prove identical is a sound `False` set without a judge call; a fully-identical pair with
  equal statuses skips the judge entirely as zero-impact; the judge is never allowed to
  claim memory changed an axis the streams prove identical (overridden to `False`). With
  no judge, the remaining behavioral axes and both outcome axes stay at their `None` seam —
  never guessed.

**Still a seam (by design):** the SEMANTIC counterfactual ("did MEMORY cause the
difference / prevent a known failure / improve verification") remains the model's call,
delegated to the injected judge. The module's own code is pure plumbing.

**Open dependency — flagged as its own bead (§4.1 shared LocalModelStack):** the live
OSS-judge backend the fork names (8B+ instruct model via Ollama + nomic-embed + FalkorDB,
§4.5 Nemotron default with an OSI-clean fallback) is NOT stood up here. `LocalModelStack`
(`memory_systems/local_stack.py`) is today a config container with no chat-completion call;
wiring a `ComparativeJudge`-shaped local backend over it is shared infra serving §12.6 +
§4.3 derailment + §4.5, so it deserves its own bead rather than being improvised into this
leg. The §12.6 scorer is judge-agnostic and will consume that backend unchanged.

_(The interruption leg, previously forked on mem-dsu, is now LANDED, see above, after
the mem-dsu generator merged onto this branch.)_

## Bead state
`mem-lvp.6` is **complete branch-ready**: retention, privacy, interruption (mechanical),
and now §12.6 action-impact (Path 1 + Path 3 judge seam) all landed on this branch (not
pushed). The only remaining work is the shared §4.1 LocalModelStack live-judge backend,
flagged above as its own bead. Branch-ready — mayor publishes after Stephanie approval
(HALT: no push without sign-off).
