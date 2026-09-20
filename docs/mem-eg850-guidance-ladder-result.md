# E1 interior result: stronger guidance buys calls, not discrimination (`mem-eg850`)

Fired 2026-09-04 in one session, closed out here. The artifact is
`memory-bench/results/e1-guidance-ladder/interior-480/`, now under version
control: `summary.json` plus all 480 leg streams. Every number below was
re-derived from those leg files rather than carried over from the summary, and
the re-derivation is pinned by
`memory-bench/tests/test_e1_guidance_ladder_interior.py`.

## What was measured

The E1 ladder asks whether guidance text makes an agent reach for memory *when
it needs to* rather than merely more often. The endpoint is the discrimination
margin

    d(rung) = P(memory call | memory-necessary task) - P(memory call | memory-unnecessary task)

A rung that lifts both halves equally buys nothing; d is what separates a
treatment that changes the decision from one that changes the rate.

The interior fire covers the three interior rungs R1 (10 guidance words), R2
(23) and R3 (38). Design: 3 rungs x 2 variants x 8 tasks x 5 repeats = 48
cells, each a two-leg pair. The establish leg states the task's values and is
byte-identical across the twin; the cwd is emptied; the goal leg then runs.
480 legs, all 480 measured, zero timeouts and zero errors. Model
`claude-sonnet-4-6`, Claude Code CLI `2.1.260`, `execution_protocol` 2,
`corpus_fingerprint` `c5f13bbe8c877a2b`, channel `recalled`, paid.

Counting rule: a leg is measured when `status == "ok"` and calling when
`memory_calls > 0`.

## Per-rung margins, as re-derived

| rung | words | necessary | unnecessary | d (goal leg) | Fisher p | d (pooled) |
| ---- | ----: | --------: | ----------: | -----------: | -------: | ---------: |
| R1   |    10 |     32/40 |       15/40 |      +0.4250 |   0.0002 |    +0.2125 |
| R2   |    23 |     40/40 |       33/40 |      +0.1750 |   0.0117 |    +0.0875 |
| R3   |    38 |     39/40 |       27/40 |      +0.3000 |   0.0007 |    +0.1500 |

n = 40 legs per arm, 80 scored goal legs per rung, out of 160 leg files per
rung. The published table field `n_legs=80` is the per-rung scored count, not
a per-arm one; read it that way.

Three things the table does not say on its face:

- **The goal-leg column is the effect.** The establish leg's memory call is
  instructed, not chosen, and its margin is exactly 0.0000 at every rung
  (0.95/0.95, 1.00/1.00, 1.00/1.00). Pooling both roles is what `summary.json`
  publishes and it halves the endpoint without adding information.
- **Discrimination peaks at the weakest interior rung.** The necessary arm
  saturates from R2 onward, so extra guidance can only lift the unnecessary arm
  (0.375 at R1 to 0.825 at R2) and it eats the margin. The ordering
  R1 > R3 > R2 is a 0.25-wide swing in the primary endpoint.
- **All three separate from zero. Neither ends margin did.** For reference
  only, from the ends fire: d(R0) = +0.175 (p = 0.147), d(R4) = +0.025
  (p = 1.000).

Supporting counts, all reproduced from the legs: goal-leg `write_calls` is 0 on
all 240 goal legs; the verb census is native_read 457, native_write 136, and
zero bd verbs; 931 hook reaches; `native_memory_pinned_off` is false on all 480
legs.

## The monotonicity gate: FAILED, reported not waved off

`call_rate_gates.monotonicity` reports `monotone=false`, `comparable=true`,
`tolerance=0.0`, one violation on the pair R2->R3: 1.000 falls to 0.9875, a
drop of 0.0125. The rate in question is the necessary-half call rate across
both leg roles, so the whole violation is one leg out of eighty: 79 of 80
necessary legs called at R3 against 80 of 80 at R2, with all 40 R3 necessary
establish legs calling.

The gate stays red in the artifact and in this note. It does not red the bead.

The reasoning. The gate exists to catch a ladder whose rates do not order with
its treatment, which would mean the treatment is not what moves the agent.
Here R2 sits at the hard ceiling of 1.000, so the only directions available at
R3 are flat or down, and a zero-tolerance test against a rate pinned at 1.0 can
be passed only by an exact tie. That makes it a coin flip on a single leg
rather than a test of ordering. The observed deficit, 1/80 = 0.0125, is well
inside binomial noise at n = 80; the 95% interval on 79/80 covers 1.000, and
the neighbouring pair orders correctly (R1 0.875 to R2 1.000).

Two things keep that from being a wave-off. A genuine violation at this sample
size would look identical, so the gate cannot distinguish the two cases and the
raw call rate is simply uninformative once it saturates. And there is a real
non-monotonicity in this fire, but it is in the primary endpoint rather than
the gated rate: d orders R1 0.4250 > R3 0.3000 > R2 0.1750, a 0.25-wide swing
that no single leg explains. That is the finding worth following up.

Disposition: record the gate as FAILED with the stated cause (ceiling plus zero
tolerance at n = 80), claim no pass, and file a follow-up to make the gate
ceiling-aware, for instance exempting pairs whose lower rate is at or above
1 - 1/n, or applying a binomial-consistency tolerance. Record also that the raw
call rate should not be the monotonicity subject at all once rungs saturate;
the ladder's ordering claim belongs on d.

## ends-160 and interior-480 are not one five-rung table

They cannot be merged. The fields that decide it:

| field | interior-480 | staged-160 (ends) |
| ----- | ------------ | ----------------- |
| `cli_version` | 2.1.260 | 2.1.258 |
| `execution_protocol` | 2 | null (bought under protocol 1; not resumable by this rig) |
| `corpus_fingerprint` | c5f13bbe8c877a2b | f78c508a7886fd56 |
| `surface_fingerprint` | 69fa4562ba901a0a | c09cd475f30bb545 |
| `settings_fingerprint` | 4848e2882ef184e4 | null |
| cell shape | two-leg pair, 48 cells x 10 legs = 480 | single leg, 32 cells x 5 = 160 |
| writes | 136 | 0, structurally impossible on a single-leg cell |

`experiment`, `channel` (`recalled`), `model` (`claude-sonnet-4-6`), `repeats`
(5) and `paid` (true) are shared, and nothing else is. d(R0) and d(R4) may be
quoted beside the interior table for reference, as the README does, and never
inside it.

## README against the data

No discrepancy. The README's goal-leg column (+0.4250 / +0.1750 / +0.3000), its
pooled column (+0.2125 / +0.0875 / +0.1500), its 2x2 tables
(R1 [[32,8],[15,25]], R2 [[40,0],[33,7]], R3 [[39,1],[27,13]]), its pooled
measured/calling table, its write table (establish write legs 34/14/20, write
calls 68/28/40, goal legs 0 of 80 at every rung), its verb census and its 931
hook reaches all reproduce from the leg files. The caveats it carries are the
ones three independent re-derivations raised.

One wording point, not a number. The README calls the gate's falling rate "the
pooled call rate on the necessary half". `monotonicity.call_rate_by_rung`
(0.875 / 1.0 / 0.9875) is the necessary-half read rate pooled over *roles*, not
over both variants; on these legs it equals the any-call rate. The pooled-over-
both-variants rates are 0.769 / 0.956 / 0.913.

## What remains unanswered

- **The differences between interior rungs are not claimed.** At n = 40 per arm
  each margin separates from zero, but R1 against R3 against R2 does not. The
  peak-at-R1 shape is the finding; its exact ordering is not established.
- **d is a reach rate, not a retrieval-success rate.** An attempted read of a
  memory file that was never written still counts as a call.
- **No bd-versus-native preference conclusion is available.** Every counted
  call was native Claude Code memory-file access and zero of 480 legs emitted a
  bd verb, which means bd was never discovered rather than discovered and
  declined.
- **`native_memory_pinned_off` is false on all 480 legs**, so the native path
  was live throughout.
- **One model, one session, no cross-model replication.**
- **The monotonicity gate needs a ceiling-aware rewrite** before a future fire's
  rung-ordering check can be informative.

## Artifacts

- Summary and all 480 leg streams:
  `memory-bench/results/e1-guidance-ladder/interior-480/`
- Fire README, with the full pooled, write and census tables:
  `memory-bench/results/e1-guidance-ladder/interior-480/README.md`
- Ends fire, for reference only:
  `memory-bench/results/e1-guidance-ladder/staged-160/`
- Re-derivation pinned by
  `memory-bench/tests/test_e1_guidance_ladder_interior.py`
