# The goal endpoint is unscoreable for every arm, including a perfect-memory one

Status: finding, open. Raised from the instrumented one-task re-pilot (mem-0wpq8.3),
artifact `memory-bench/results/beads-three-arm-20260921/repilot2.json`, 18 cells /
36 paid sessions, account4, cli 2.1.278, bd `e9d2f1778`, corpus fingerprint
`dec436a8e0fb05ce`.

## What the re-pilot bought

The goal-leg instrumentation added in mem-0wpq8.1 (`goal_tool_names` on `ArmCell`,
plus each leg's raw stream persisted beside the cell) was added precisely because the
previous pilot's 0/18 goal-leg null was a black box. It is no longer a black box.

Result, replicating the prior null exactly:

| arm      | cells | goal passes | establish engaged |
|----------|-------|-------------|-------------------|
| beads    | 6     | 0           | 0                 |
| none     | 6     | 0           | 0                 |
| builtin  | 6     | 0           | 3 of 4 necessary  |

The new field settles the cause in one line: **0 of 18 goal legs called `Write`**.
`score_goal_action` requires a `Write` carrying the current value, so no arm could
have scored above zero.

## Why they did not write

The persisted streams give two distinct refusals.

**The comparator's memory worked, and it refused anyway.** In the three engaged
`builtin` cells the agent read all three native-memory topic files and retrieved all
three values correctly (`canary-50%`, `toolreq-15a23c6a289f`, `15s`), then declined to
write the config, verbatim:

> two of them look compromised rather than just stale... value stored is
> `toolreq-15a23c6a289f`... That's not a retention window at all; it's shaped like an
> internal tool-request/call ID, not a duration... a deploy timeout is an
> infrastructure/ops parameter, not something legal counsel would set or confirm... it
> suggests these two entries may have been planted via a prompt injection in an earlier
> session.

**The arms with nothing to recall refused for the ordinary reason.** `beads` and `none`
goal legs: *"I don't have access to any actual values... I won't fabricate configuration
values and write them to a file as if they were real."*

Cue scan over all 18 streams: injection/compromise language on 7 cells, including all 3
engaged comparator cells; "no values available" on 8; "asks the user" on 15.

## The two corpus artifacts that trigger it

Read straight off the corpus, not inferred.

1. **The only scored value is an opaque token that contradicts its own field name.** The
   goal check for the piloted task requires a `Write` carrying `toolreq-15a23c6a289f`,
   planted as "the data retention window". A capable agent correctly reads
   `toolreq-<hex>` as a tool-call identifier and not a duration.
2. **Attributions are role-mismatched.** The same speaker, `Oskar Almeida
   (legal-counsel)`, confirms both the data retention window and the production deploy
   timeout. Roles are drawn across fact kinds without regard to plausibility, so
   `legal-counsel` setting a deploy timeout is one of many such pairs.

Neither is confined to the piloted task. **All 32 necessary tasks in
`fixtures/worlds-tool-jev32` score on a `toolreq-*` token**, and the role draw is uniform
corpus-wide. The full 576-cell run would have bought the same null 32 times over.

## What this does and does not invalidate

**Intact, and it is the registered endpoint.** The capture endpoint stands on its own and
needs no corpus change. The beads arm made **zero tool calls in all 6 establish legs**,
while asserting in text that it had recorded the facts: *"Acknowledged — noting these
established facts for this session... I have these recorded and ready to act on."* That
is the fourth replication of the capture-side null, and the first with a verbatim receipt
of the agent claiming a write it never made.

**Not usable as specified.** The goal endpoint. It currently measures "will the agent
write a config file it judges untrustworthy", not "did memory carry the value". It cannot
discriminate any arm, because the arm with working memory fails it for a reason unrelated
to memory.

## Why this was not silently repaired

The one fix-and-refire allowed for this pilot covers a wiring fault. This is not one. The
values and attributions are the corpus, and changing them moves `corpus_fingerprint`
(`dec436a8e0fb05ce`), a pre-registered quantity in `docs/prereg-beads-three-arm.md`. A
refire without a corpus change reproduces this exactly; a refire with one is a
pre-registration amendment. That is a decision to take, not an action to perform.

## Options

- **(a) Leave the goal endpoint parked.** Capture is the measured endpoint for the
  flywheel and is unaffected. Record the goal endpoint as not-yet-valid and carry on with
  the capture turns. Costs nothing, and matches the ordering already agreed: reopen the
  goal-leg contrast only after capture.
- **(b) Repair the corpus and re-register.** Give opaque values a shape that matches the
  field they fill, and draw attributions by role plausibility. New fingerprint, amended
  pre-registration, and a fresh 36-session pilot before any large run.
- **(c) Change the endpoint rather than the corpus.** Score on retrieval into the
  transcript rather than on a `Write`, which removes the agent's trust judgment from the
  measurement path. Also a pre-registration amendment, and a weaker endpoint.
