# mem-747nj — Write-time capture-fidelity gate: STOP

Three firings of a pre-registered stop rule, all NOT MET. The rule and all three
readings live together in `memory-bench/membench/fidelity/stop_rule.py`; the raw
runs are in `memory-bench/tests/fixtures/offepisode_packets/RUN-*.log`.

The recommendation is not "the gate does not work". Its core check does work, and
the numbers below say so. The recommendation is that the check is in the wrong
place: blocking a write on a judgment that changes its mind between two identical
runs converts a model's flakiness into permanent memory loss.

## What was under test

Every memory a `bd`-backed agent writes carries claims. Some of them are supported
by the evidence that memory cites; some are the agent asserting something it never
saw. The gate asks a model, at write time, to decompose the record into claims and
point each one at a line of the evidence. A record with an uncitable claim is
refused.

The competitor it has to beat is one line of grep: reject a record that spells a
version-shaped token the evidence does not contain (`1.9`, `8.2`). That baseline is
free, corpus-independent, and needs no model. The gate has to earn its cost by
catching what the grep is *structurally* blind to, so the probe corpus ships lures
of both kinds. Each lure is the packet's own supported record with exactly one
sentence added: one version carries a betraying token, the other (`token_detectable:
false`) carries none at all.

## The three firings

| firing | date | draws | pairs | discriminating condition | verdict |
|---|---|---:|---:|---|---|
| 1 | 2026-09-06 | 5 | 6 | PASS, gate 1 vs baseline 0 | NOT MET |
| 2 | 2026-09-07 | 1 | 6 | PASS, gate 2 vs baseline 0 | NOT MET |
| 3 | 2026-09-07 | 5 | 6 | PASS, gate 2 vs baseline 0 | NOT MET |

Firing 3 changed no code. It is firing 2's instrument re-read at full sample size,
so the reading cannot be a patch fitted to the result.

A fourth run over two newly authored packets (`inode-exhaustion`,
`redis-oom-noeviction`, 3 draws) is recorded but does not decide anything: it is
short of the registered sample size (4 pairs of 6, 2 token-free of 3) and one of
its pairs produces no usable extraction at all. It is listed here because it
contains the one reading in which the free competitor beats the paid gate outright:
on `redis-oom-noeviction/version-token` the grep rejects the lure (`3.0` is absent
from the evidence) and the gate accepted it in all three draws, while rejecting
that pair's supported record on a manufactured literal of its own. Grep 1, gate 0,
on a pair that cost 2.7 model calls per record.

## Firing 3, pair by pair, across 5 draws

| pair | grep sees the lure? | gate on the lure | gate on the supported record |
|---|---|---|---|
| `go-race-map/version-token` | yes, `1.9` | reject 5/5 | accept 5/5 |
| `go-race-map/token-free` | no | reject 5/5 | accept 5/5 |
| `pg-index-lock/version-token` | yes, `8.2` | reject 5/5, never on `8.2` | **reject 1/5** |
| `pg-index-lock/token-free` | no | reject 5/5 | accept 5/5 |
| `nginx-proxy-timeout/version-token` | no, `60s` is not version-shaped | accept 5/5 | accept 5/5 |
| `nginx-proxy-timeout/token-free` | no | accept 5/5 | accept 5/5 |

**The case for the gate, stated at its strongest.** Four of the six lures carry
nothing a version-token matcher can see. The gate caught two of those four, in
every draw, and the baseline caught zero of them by construction. That is a real
capability and no amount of tuning gets the grep there.

**The cost.** 60 record runs at a mean 2.7 model calls each (one extraction plus
1.7 repair turns; the distribution is 9 records at zero repairs, 31 at one, 5 at
two, 9 at three, 1 at four, 5 at six). Wall clock 8.5 minutes for 60 records, so
roughly 8.5 s of added latency per memory written, on the write path, at the local
pin (`qwen3:30b-a3b-instruct-2507-q4_K_M`, greedy).

**The number that stops it.** One of 30 supported-record runs was rejected. Per
record, one of the six supported records failed to survive all five draws. A
supported record counts as accepted only if accepted in every draw, so raising the
draw count can only make the gate's job harder; the larger sample sharpened the
verdict rather than softening it.

## The rejection is not the citation check failing

`pg-index-lock`'s supported record says the work happened during "staging deploy,
release train 14". That phrase is in both source documents verbatim
(`PRIOR_SOURCE_PACKET.md:3`, `PRIOR_TASK.md:9`). The record is genuinely inside its
support boundary. What rejects it is the gate's own repair loop:

1. Turn 0 declares `release train 14` as a descriptive phrase. Descriptive phrases
   are filed as unchecked. Nothing is wrong yet.
2. `verify.phrase_literals` complains about it anyway, because it contains a digit.
3. The model answers the complaint by joining the words into `release_train_14`.
4. `verify.unrecorded_literals` correctly reports that token is absent from the
   record.
5. The model declines to change it. The repair budget runs out.
6. The manufactured token is checked against evidence that cannot contain it, and
   a supported record is rejected.

The re-ask provokes the fault the next check then catches. Three successive
narrowings of step 2 have failed to stop the joining habit. A fourth would be a
deletion, and deleting a check after a NOT MET reading is a post-hoc instrument
change, so it was not made.

## The finding that actually decides the design

On 2026-09-07 the identical file `pg-index-lock/probes/supported.txt`, read by both
probes of that packet inside a single process, through an extract prompt that is a
pure function of the record text and the packet, produced entirely different claim
decompositions and opposite verdicts. The same two runs had been byte-identical in
all five draws the day before. Greedy decoding at this pin is not reproducible.

Firing 3 confirms it from the other side: the rejection that decided firing 2 is a
1-in-5 minority outcome, not the modal one.

So the gate does not have a fixed answer to give. It has a distribution.

## Why that is disqualifying for a write-time gate specifically

The memory tables in this store are append-only and non-regenerable (`lessons`,
`memory_events`, producer-source `provenance_events`; see `CLAUDE.md`). That makes
the two error directions wildly asymmetric:

- **A false reject is permanent loss.** The memory never entered the store. No
  reader can contest it, no later evidence can rehabilitate it, and a rebuild from
  the spine cannot reconstruct it. The agent that would have used it simply never
  learns the thing.
- **A false accept is a contestable record.** The unsupported claim is in the store
  with its citations attached, where a reader, a contradiction flag, or a
  retrieval-time check can catch it.

Measured, the gate's error sits on the expensive side at roughly 3% per
record-draw. This argument does not depend on the price of the model call. Even if
inference were free, blocking a write on a check that changes its mind is the wrong
shape for an append-only layer.

## The design options

**A. Ship it as built: a blocking write-time gate.** Its own pre-registered rule
says no, three times, and the rule was written before any of it was measured.

**B. Drop semantic gating at write time entirely.** Keep the token baseline as the
only synchronous check: one regex, zero model calls, no added latency, catches the
version-shaped subset. Gives up the two grep-blind catches.

**C. Keep the citation check and move it off the write path.** The same code, run
non-blocking, annotating a memory with the claims it could not cite instead of
refusing the write.

**Recommended: C, with B's regex as the only synchronous check.**

C keeps the one capability the gate demonstrably has (two of four grep-blind lures,
stably, across every draw), and it turns the failure mode from "a true memory that
never existed" into "a wrong flag on a record that still exists and can be read".
The reproducibility finding lands hard on A and barely touches C: an annotation
that varies between runs is noise a reader can discount, while a rejection that
varies between runs is data loss. It also matches what we already argued upstream
on beads#6051, that contradiction belongs in a memory system as a flag rather than
a delete.

What C costs and does not solve: the annotation needs somewhere to live, which is a
schema question for the Memory bead type (a field for uncited-claim flags), and the
check still burns ~2.7 model calls per memory, just off the critical path.

## What would have to change to revisit A

Not more draws. Option A becomes viable only if the extractor returns the same
decomposition for the same question, and that is a property of the extraction
prompt and the decode pin, not of the citation check that was under test here. It
is a separate measurement, and the retro corpus agreeing across both its draws on
the same day says the instability is not everywhere.
