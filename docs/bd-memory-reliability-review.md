# bd memory: reliable capture and retrieval

Reviewed 2026-09-04. Objective: an agent chooses `bd` when useful, successfully
stores knowledge, and recovers it in a later session to complete real work.
The initial review changed instrumentation and the next experiment's design.
The subsequently completed comparison is reported in the
[experiment results](../memory-bench/results/bd-reliability-20260904/REPORT.md).
The initial recommendations below are retained as the pre-execution review.

## What the existing evidence establishes

The [interior-480 run](../memory-bench/results/e1-guidance-ladder/interior-480/README.md)
measured 457 native read verbs, 136 native write verbs, and zero bd verbs.
The agent was never told bd existed. Removing generated store guidance protected
the guidance contrast but also removed discovery. Those results cannot establish
a preference between native memory and bd.

Recomputed from the persisted legs, each necessary arm has 40 session pairs:

| Rung | Establish legs attempting writes | Goal legs attempting reads | Goal tool-error results |
| ---- | -------------------------------: | -------------------------: | ----------------------: |
| R1   |                            18/40 |                      32/40 |                      17 |
| R2   |                             7/40 |                      40/40 |                      33 |
| R3   |                             9/40 |                      39/40 |                      31 |

All these memory attempts were native. The error column counts all tool-error
results, not independently classified memory failures. A concrete
[R2 goal leg](../memory-bench/results/e1-guidance-ladder/interior-480/summary.json.legs/R2__necessary__world-seed0-task0__1.json)
reads a missing memory file and refuses the task because no values were stored.
A 100% read-attempt rate therefore does not establish reliable memory use.

The [E0 corpus report](../memory-bench/results/memory-use/e0/report.md) supplies
complementary deployment evidence: 129/4,404 sessions with bd traffic used a
memory verb despite standing capture instructions. It records 38 attempted reads
through invalid `remember` flags versus 16 valid targeted-read invocations.
Successful use depends on an accurate command contract as well as discovery.
E0 has different grammar/version and population pins; its counts must not be
pooled with E1.

## Improvements in this change

- Give the agent the correct capture grammar: one quoted positional argument
  supplies content; `--key` chooses a key. Document search and keyed recall
  alongside capture.
- Say that the goal arrives in a **separate session**. The earlier establish
  prompt promised another turn in the same session, although the harness starts
  fresh processes. That mismatch makes relying on conversation context rational.
- Add a `.bd_reliability` report to future E1 summaries. Keep bd and native
  operations separate and split establish from goal roles.
- Record acknowledged operations, coverage of required opaque values in capture
  and retrieval, and the observed paired handoff. The required content must be
  retrieved before the correct goal action is acknowledged. Read-result keys,
  query headings, and not-found metadata cannot satisfy content coverage.
- Grade writes on storage acknowledgments matching any explicit key. Refused or
  unanswered captures earn no retention credit. E1 and E3 share a conservative
  policy that leaves compound commands and wrappers unattributed, since another
  command could supply their output. Valid wrapped operations can be undercounted.
- Fix resume to expect two legs per repeat, preserving completed session pairs
  and their evidence instead of silently buying them again.
- Advance the execution protocol to 4. Historical results retain their original
  evidence and cannot resume into the changed protocol.

Run the focused checks from `memory-bench/`; they use stub agents and spend no
model calls:

```sh
.venv/bin/pytest -q tests/test_e1_reliability.py tests/test_bd_deployment_context.py
```

The existing named deployment context contains upstream instructions plus an
additional memory instruction block. It is an **instruction bundle**, not a
visibility-only treatment. Attribute any future contrast to that complete bundle
unless the experiment holds its instructions fixed and varies only naming.

## Endpoints and decisions

| Endpoint               | Definition                                                                                                                       | Decision it informs                                            |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| Establish capture      | Successful bd capture containing required opaque values / scheduled establish legs                                               | Whether capture instructions or syntax need improvement        |
| Goal retrieval         | Successful bd retrieval containing required opaque values / scheduled necessary goal legs                                        | Whether discovery or retrieval needs improvement               |
| Observed bd handoff    | Same pair has qualifying establish capture, then goal retrieval before a correct acknowledged action / scheduled necessary pairs | Whether the complete bd workflow is reliable enough to advance |
| Unnecessary goal reads | bd read attempts / scheduled unnecessary goal legs                                                                               | Cost of excessive retrieval after reliability is established   |
| Operation failure      | Failed or unacknowledged attempts, reported separately from successes                                                            | Whether the interface needs better feedback or recovery        |

Report scheduled, complete, and incomplete counts with every endpoint. Unknown
outcomes remain unknown; show lower and upper bounds that treat unresolved pairs
as failures and successes respectively. Do not drop incomplete pairs from the
headline denominator. Publish read-given-capture as a diagnostic beside the joint
endpoint, since conditioning on successful captures hides the capture failures.

Pair by task and repeat, preserving establish-before-goal order. Required-value
coverage is an exact fixture check; it is not a judgment of general memory
quality. An observed handoff establishes the behavior happened together. Native
memory or another source may also supply the answer, so it does not establish
causal dependence on bd. Historical logs without the new evidence are unmeasured.

Goal-specific unnecessary reads are a secondary diagnostic, not the primary
optimization target. Pooling establish and goal legs dilutes the retrieval
contrast and makes capture behavior harder to interpret. Strict monotonicity of
sample call rates is a treatment-shape diagnostic, not a prerequisite for a valid
experiment: a real treatment can have a nonmonotonic effect.

## Next experiment: specification to lock before execution

1. **Conditions.** Compare generic guidance, the explicit bd instruction bundle,
   and the same bundle plus native-memory redirection. Hold model, corpus,
   allowed tools, store lifecycle, budgets, and native-memory settings fixed.
   Current R0 disables native auto-memory while other rungs enable it; R0 versus
   those rungs changes settings as well as guidance. Do not label that contrast
   a pure guidance effect.
2. **Treatment isolation.** Keep an observe-only hook in the first two conditions.
   Redirection is an additional intervention, reported separately from voluntary
   bd choice. Its [mechanism tests](e1-native-memory-interception.md) do not
   establish that an agent follows the redirect.
3. **Tasks.** Retain necessary/unnecessary twins and independent sessions. Add
   held-out task families covering incidental useful discoveries, no useful new
   knowledge, outdated memories, and distractors. Keep opaque answers absent
   from the necessary goal prompt and fresh workspace. Separate these deployment
   tests from the existing synthetic configuration probes.
4. **Primary contrast.** Compare observed bd handoff on necessary pairs between
   the named bundle and generic guidance. Treat the redirect contrast and
   unnecessary-read costs as secondary. Lock acceptable reliability and cost
   thresholds before inspecting new results; choose sample size against those
   decisions, not a desired p-value.
5. **Inference.** Randomize or counterbalance condition order. Repeats of the
   same eight tasks are not forty independent task families. For generalization
   to tasks, preserve matched variants and cluster uncertainty by task; report
   paired task-level intervals and correct for any additional primary contrasts.
6. **Reproduction.** Freeze task bytes, prompts, native settings, CLI and bd
   versions, model identity, hook mode, harness revision, and protocol. Retain
   redacted operation receipts and both roles' evidence. Never merge the old
   single-leg and two-leg runs into a common treatment grid.

The specification above is not a sealed or executed preregistration. Pin its
sample, thresholds, randomization schedule, and exact launch commands before a
new run. Existing results cover one model and a small synthetic task set; they
do not yet show production reliability or a benefit from the named bundle or
redirect treatment.

## Execution receipts (2026-09-05)

The live preflight exposed successful compound Bash writes that the direct-command
scorer deliberately left unattributed. The controlled comparison therefore uses an
identical receipt instrument in all three conditions. A neutral PreToolUse hook
attaches the actual tool-use ID to each Bash process; the isolated bd wrapper saves
argv, per-stream output bytes, exit status, and start/finish records. Each leg saves
its receipts before temporary-store cleanup. A receipt proves backend execution;
recall additionally requires that returned bd content appears in the matching tool
result. Before-action credit requires delivery before the successful action's tool
use, including when tools run concurrently.

A pinned Claude Code 2.1.261 mechanism check executed a compound remember/recall
command and produced two successful receipts with matching tool IDs. The recorded
conversation retained the original command, without injected environment-variable
names. This checks instrumentation, not spontaneous memory use. Artifacts are in
`memory-bench/results/bd-reliability-20260904/hook-mechanism/`.

The wrapper preserves bytes within stdout and stderr but buffers and replays them
separately; it does not preserve interleaving when a shell merges the streams.
Receipt metadata is harness instrumentation, not a security boundary against an
agent deliberately forging files. Missing or incomplete evidence remains explicit
in the comparison report. The earlier receipt-free preflight remains diagnostic
and is not pooled with the controlled comparison.

## Completed comparison and scorer correction (2026-09-05)

Confirmation completed all 96 pairs / 192 sessions. Necessary-task bd handoff was
0/16 for generic guidance, 8/16 for explicit guidance, and 12/16 for explicit
guidance plus native-memory redirection. Qualifying task actions were 16/16,
14/16, and 12/16 respectively. Redirection had the highest measured bd adoption;
these eight synthetic tasks do not establish production reliability or improved
overall task completion. See the results for paired uncertainty and costs.

After execution ended, scorer version 3 replaced the broad Write-argument check
with an acknowledged Write to `cwd/config.json`, strict JSON, and required tokens
in decoded string values. Recall must arrive before that qualifying Write itself.
Legacy cached evidence retains its version and cannot silently acquire the stricter
contract. Both post hoc audits agreed with all 96 saved action and handoff outcomes
and found no contradictory ordering verdicts. One frozen unknown ordering verdict
became false because no qualifying Write occurred; raw evidence was preserved.
