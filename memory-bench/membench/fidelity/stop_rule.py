"""The pre-registered stop rule for the capture-fidelity gate.

The rule this replaces could not fire. It compared the gate's 9/9 on the retro set
against a one-token matcher's 7/7 and asked whether the gate won -- but the retro
corpus names `discriminating_literals ['3.11']` on six of its seven records, so the
matcher was searching for the token the corpus was built around. A baseline tuned
to the instrument measures the instrument. Rerunning it, at any draw count, would
have measured corpus construction and reported it as gate quality.

What is registered here, before any of it was measured:

* **Instrument.** The off-episode probe pairs, not the retro corpus. Three
  authored packets, no version anywhere in their evidence, two pairs each.
* **Competitor.** `token_baseline`: reject a record spelling a version-shaped token
  the evidence lacks. Corpus-independent, one line of grep, free to run.
* **Discriminating condition.** The gate has to catch lures the baseline is
  *structurally* blind to -- the `token_detectable: false` ones, which carry no
  such token at all, so no token matcher can reach them however it is tuned.
* **Threshold.** Over at least 6 pairs including at least 3 token-free lures: the
  gate accepts every supported record, and rejects strictly more token-free lures
  than the baseline does. The baseline rejects none of them by construction, so
  the bar is at least one -- and one is a real bar, because the gate can accept a
  token-free lure by leaving the injected sentence unclaimed, which is the
  standing failure mode `GateDecision.summary` already names.

Failing it stops the design. Not "run more draws": the citation check would have
been shown to buy nothing the grep does not already buy, and a gate that costs a
model call per record has to buy something.

Nothing here decides what a verdict was. It counts verdicts the gate and the
baseline already produced, and applies arithmetic that was written down first.

## Amendment, 2026-09-07, written before the rerun it authorises

The rule fired on 2026-09-06 and read NOT MET. Its numbers are recorded in
`tests/fixtures/offepisode_packets/RUN-2026-09-06-5draw.log`. The reading was:

* The discriminating condition **passed**. The gate rejected one token-free lure
  in every draw; the baseline rejected none. That is the comparison the rule was
  written to make, and it came out in the gate's favour.
* Two hygiene preconditions failed, and both failed on the extractor rather than
  on the gate. `pg-index-lock` rejected its own supported record because a repair
  turn answered "that phrase is not a token" by joining the words, manufacturing
  `release_train_14` and `CREATE_INDEX_idx_orders_created_at_ON_orders_created_at`
  -- tokens the record does not contain, checked against evidence that could not
  contain them either. `nginx-proxy-timeout/token-free` produced no extraction at
  all, on one record line the model left unaccounted after its single repair
  round had been spent correcting three others.

What is amended, and the amendment is one sentence long: **the two broken pairs
are repaired and the rule is run again.** What is *not* amended is the threshold.
`MIN_PAIRS`, `MIN_TOKEN_FREE_PAIRS`, "accepts every supported record" and
"strictly more token-free lures than the baseline" all stand exactly as
registered, and no repair below touches a verdict:

* `verify.unrecorded_literals` adds a re-ask, never a verdict. A literal that
  survives it is verified exactly as it would have been, so no record can pass
  that would have failed. A lure's token is written into its record, so the
  complaint is structurally incapable of naming one.
* `ClaimExtractor.repair_rounds` moves 1 -> 2. A repair round is a request for a
  readable reply and says nothing about what to conclude; a record that never
  becomes readable still fails.
* `pg-index-lock`'s record gains the `Source:` line its environment section was
  missing, which every other section of every packet already carries.

The measured result the amendment rests on is the extractor's, so the honest
statement of what a rerun can and cannot do: it can show whether the gate clears
a bar that was set before any of this was seen, and it cannot make the 2026-09-06
verdict not have been NOT MET. Both runs stay in the record.

The draw protocol drops from 5 to 1 in the same amendment, for the reason
`scripts/run_offepisode_probes.py` now records: all six pairs returned the
identical outcome in all five draws, so the repeats were copies rather than
samples.

## What the rerun read, 2026-09-07, written after it

Everything above this heading is the pre-registration and is left as it was
written. This section is the result.

**NOT MET again, and for one reason instead of two.** The rerun is recorded in
`tests/fixtures/offepisode_packets/RUN-2026-09-07-1draw.log`.

* The discriminating condition passed by a wider margin: the gate rejected **two**
  of the three token-free lures (`go-race-map`, `pg-index-lock`), the baseline
  rejected none, as it must by construction. Last time it was one against none.
* `nginx-proxy-timeout/token-free` is repaired. It produces an extraction now, so
  the precondition it broke is met; the pair simply does not discriminate, which
  is a reading of the gate and not a defect in the pair.
* `pg-index-lock/version-token` still rejects its own supported record, so the
  rule is still unmet. The repairs did not reach the cause. The cause is upstream
  of them, in `verify.phrase_literals`: turn 0 declares `release train 14`, which
  is a descriptive phrase, is filed as unchecked, and harms nothing. The phrase
  re-ask complains about it anyway; turn 2 answers the complaint by joining the
  words into `release_train_14`; `verify.unrecorded_literals` then correctly says
  that token is not in the record; the model declines to change it, the budget
  runs out, and the manufactured token is checked against evidence and rejects the
  record. The re-ask provokes the fault the new check then catches. Removing the
  re-ask is a deletion, not a fourth repair, and a descriptive phrase is unchecked
  in the first place, so no record could pass because of the removal -- but it
  would still be an instrument change made after a NOT MET reading, and it is not
  made here.

**The 5-to-1 draw rationale above is falsified.** It rested on the repeats being
copies. In this rerun the identical file `pg-index-lock/probes/supported.txt`,
read by both probes of that packet inside one process, was accepted under one
probe and rejected under the other; the two recordings' extract turns hold
entirely different claim decompositions of a prompt that is a pure function of the
record text and the packet. On 2026-09-06 those same two runs were byte-identical
in all five draws. Greedy decoding at this pin is therefore not reproducible, the
saving the drop to one draw bought is not free, and what the rule now reads from a
single draw is one sample rather than a settled outcome. The retro corpus measured
the same day did agree across both its draws (`tests/test_fidelity_retro.py`), so
the instability is not everywhere, which is why the draw count is a measurement to
redo rather than a constant to guess at.

## What the 5-draw rerun read, 2026-09-07, written after it

Third reading, **NOT MET**, recorded in
`tests/fixtures/offepisode_packets/RUN-2026-09-07-3packet-5draw.log`. Same three
registered packets, five draws, and no code changed between the second reading
and this one: the gate stands exactly as it read NOT MET on the single draw.

* The discriminating condition passed for the third consecutive reading, and
  unanimously across draws. The gate rejected **two** of the three token-free
  lures (`go-race-map`, `pg-index-lock`) in every draw; the baseline rejected
  none, as it must by construction.
* `nginx-proxy-timeout` accepted both of its lures in all five draws. That is a
  stable reading of the gate, not a defect in the pair.
* `pg-index-lock/version-token` rejected its own supported record in **1 of 5**
  draws and accepted it in the other four, on the same manufactured
  `release train 14` literal the second reading traced. A supported record counts
  as accepted only if accepted in every draw, so one rejection is enough, and the
  rule is unmet on the same single precondition as before.
* Raising the draw count from 1 to 5 can only make the gate's job harder under
  that aggregation, so the reading is not softened by the larger sample -- it is
  sharpened. The rejection that decided the second reading is a minority outcome
  at 1/5 rather than the modal one, which is a second confirmation that greedy
  decoding at this pin is not reproducible.
* In all five draws the `pg-index-lock` version-token *lure* is rejected on that
  same manufactured literal rather than on its injected token `8.2`. The four
  draws the log scores as discriminating discriminate for the wrong reason -- the
  log annotates them "rejection names no declared token" -- so that pair carries
  no evidence either way about the citation check.

Read together, the three firings say one thing consistently. The comparison the
rule was written to make has come out in the gate's favour every time: the
citation check catches token-free lures a version-token matcher is structurally
blind to, two of three of them, against a baseline that catches none. And the
rule has failed all three times on a precondition produced by the extractor's own
repair loop rather than by the check under test, with three successive narrowings
of `verify.phrase_literals` failing to reach the cause. Per the pre-registration,
failing stops the design. The fact that costs the most is not the one the gate
was accused of: a write-time gate that cannot hold a supported record stable
across draws of the same question fails on reliability before it is ever asked
whether its extra model call is worth the money.

## Amendment, 2026-09-20, written before any repair it authorises

Authorised by Stephanie on 2026-09-20. It changes the scored SET. It does not
change a threshold: `MIN_PAIRS` stays 6, `MIN_TOKEN_FREE_PAIRS` stays 3,
"accepts every supported record" and "strictly more token-free lures than the
baseline" stand exactly as registered, and `evaluate` is untouched.

**What is admitted.** Both remaining packets, `inode-exhaustion` and
`redis-oom-noeviction`. That is every packet in the corpus, not a chosen two, so
the admission carries no selection degree of freedom -- the same reason the
three-arm grid buys all 32 of its tasks rather than a subset.

**Their readings were already on disk when this was authorised, and both are
bad.** `tests/fixtures/offepisode_packets/RUN-2026-09-07-newpackets-3draw.log`,
committed in fbfaf3bf, reads NOT MET over those two packets alone: 4 pairs, 2
token-free, `inode-exhaustion/version-token` rejecting its own supported record,
`redis-oom-noeviction/version-token` rejecting supported and accepting the lure,
and `redis-oom-noeviction/token-free` producing no usable extraction in all three
draws. Admitting a packet whose reading you have already seen is the move a
pre-registration exists to prevent, so it is written down here rather than left
to be noticed: these are **pre-repair** readings, taken against the extractor as
it stands, and every one of those four failures is an instance of a failure mode
the authorised repairs target directly. A no-usable-extraction is an unaccounted
record line, which sentence-level coverage addresses; a rejected supported record
on `inode-exhaustion` is the manufactured-literal path `verify.phrase_literals`
already produced on `pg-index-lock`. If the repairs do not move these readings,
that is the rule firing, and it fires NOT MET.

**Why the set had to move at all.** The registered three packets cannot yield an
evaluable run: `nginx-proxy-timeout` accepted both lures in all five draws of
`RUN-2026-09-07-3packet-5draw.log`, a stable non-discrimination that is a reading
of the gate rather than a defect to repair, so it is relabeled a known-negative
control. A control is reported and is not counted toward `MIN_PAIRS`; scoring a
pair whose expected outcome is non-discrimination against a discrimination bar
would make the bar meaningless. Relabeling it leaves the registered set at 4
pairs, below its own minimum, and the rule becomes unevaluable -- which would
leave the instrument with no defined stopping condition at all.

**The set the rerun scores.** Five packets admitted, `nginx-proxy-timeout` held
out as the known-negative control: **8 pairs, 4 token-free**, against minimums of
6 and 3. `pg-index-lock` is repaired, not retired; retiring it was available once
the corpus grew and is declined, because its fault is in the extractor's re-ask
and removing the record would hide the defect rather than fix it. The margin of
two pairs over the minimum is margin against an unusable pair, not licence to
drop one that reads badly.

**What still has to happen before the rule is read again**, in order: the
2026-09-06 report retires as score of record; this amendment lands before any
code repair; the three repairs go in; every recording is re-cut, because
`transcript.py`'s `ScriptedCompletion` refuses uncovered calls and asserts every
recorded turn is spent, so sentence-level coverage invalidates all existing
replay evidence; then the full 9-record retro rerun plus this off-episode rule,
5 draws each. The draw count stays at 5 and is not renegotiated: the 2026-09-07
readings established that greedy decoding at this pin is not reproducible, and
a single draw is one sample of an unstable quantity.

"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["MIN_PAIRS", "MIN_TOKEN_FREE_PAIRS", "PairOutcome", "StopRuleVerdict", "evaluate"]

# Registered before measuring, and deliberately not moved since. Six pairs is what
# three packets x two probes yielded when the rule was written, and three token-free
# is one per packet: the smallest set in which a single packet cannot carry the
# verdict alone.
#
# The corpus has since grown to five packets, so a full run now offers ten pairs and
# five token-free. These numbers stay where they were registered. Raising them to
# match the corpus would be a threshold change made after two NOT MET readings, and
# the whole value of a pre-registered rule is that its bar was set before anyone knew
# what the run would say. The extra pairs are margin against an unusable one, not a
# reason to ask for more. The 2026-09-20 amendment above admits the other two
# packets to the scored set and holds `nginx-proxy-timeout` out as a known-negative
# control; it moves the SET, and deliberately leaves these two numbers alone.
MIN_PAIRS = 6
MIN_TOKEN_FREE_PAIRS = 3


@dataclass(frozen=True)
class PairOutcome:
    """One probe pair as both judges saw it.

    ``gate_accepts_*`` is None when the extractor produced nothing usable for that
    record. That is a missing observation, not an acceptance, and it is counted as
    neither: a pair with a missing half cannot support the supported-record
    requirement and cannot be credited as a rejection.
    """

    packet_id: str
    probe_id: str
    token_detectable: bool
    gate_accepts_supported: bool | None
    gate_accepts_lure: bool | None
    baseline_accepts_supported: bool
    baseline_accepts_lure: bool

    @property
    def complete(self) -> bool:
        return self.gate_accepts_supported is not None and self.gate_accepts_lure is not None


@dataclass(frozen=True)
class StopRuleVerdict:
    """Whether the registered threshold was met, and every number behind it."""

    passed: bool
    pairs: int
    token_free_pairs: int
    incomplete_pairs: tuple[str, ...]
    gate_rejected_supported: tuple[str, ...]
    gate_rejected_token_free: tuple[str, ...]
    baseline_rejected_token_free: tuple[str, ...]
    failures: tuple[str, ...]

    def report(self) -> str:
        lines = [
            f"stop rule: {'MET' if self.passed else 'NOT MET'}",
            f"  pairs: {self.pairs} (need {MIN_PAIRS}), "
            f"token-free: {self.token_free_pairs} (need {MIN_TOKEN_FREE_PAIRS})",
            f"  gate rejected token-free lures: {len(self.gate_rejected_token_free)}"
            f" {list(self.gate_rejected_token_free)}",
            f"  baseline rejected token-free lures: {len(self.baseline_rejected_token_free)}"
            f" {list(self.baseline_rejected_token_free)}",
        ]
        if self.incomplete_pairs:
            lines.append(f"  pairs with no usable extraction: {list(self.incomplete_pairs)}")
        for failure in self.failures:
            lines.append(f"  UNMET: {failure}")
        return "\n".join(lines)


def evaluate(outcomes: tuple[PairOutcome, ...]) -> StopRuleVerdict:
    """Apply the registered threshold to the pairs as measured."""
    token_free = tuple(outcome for outcome in outcomes if not outcome.token_detectable)
    incomplete = tuple(_name(o) for o in outcomes if not o.complete)
    rejected_supported = tuple(_name(o) for o in outcomes if o.gate_accepts_supported is False)
    gate_caught = tuple(_name(o) for o in token_free if o.gate_accepts_lure is False)
    baseline_caught = tuple(_name(o) for o in token_free if not o.baseline_accepts_lure)

    failures: list[str] = []
    if len(outcomes) < MIN_PAIRS:
        failures.append(f"{len(outcomes)} pairs, the rule requires {MIN_PAIRS}")
    if len(token_free) < MIN_TOKEN_FREE_PAIRS:
        failures.append(
            f"{len(token_free)} token-free pairs, the rule requires {MIN_TOKEN_FREE_PAIRS}"
        )
    if incomplete:
        failures.append(
            f"{len(incomplete)} pair(s) produced no usable extraction: {', '.join(incomplete)}"
        )
    if rejected_supported:
        failures.append(
            f"the gate rejected {len(rejected_supported)} supported record(s): "
            f"{', '.join(rejected_supported)}"
        )
    if len(gate_caught) <= len(baseline_caught):
        failures.append(
            f"the gate caught {len(gate_caught)} token-free lure(s) against the baseline's "
            f"{len(baseline_caught)}; the rule requires strictly more"
        )

    return StopRuleVerdict(
        passed=not failures,
        pairs=len(outcomes),
        token_free_pairs=len(token_free),
        incomplete_pairs=incomplete,
        gate_rejected_supported=rejected_supported,
        gate_rejected_token_free=gate_caught,
        baseline_rejected_token_free=baseline_caught,
        failures=tuple(failures),
    )


def _name(outcome: PairOutcome) -> str:
    return f"{outcome.packet_id}/{outcome.probe_id}"
