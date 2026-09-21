# 3. Capture is its own registration, not a gate on the three-arm one

Date: 2026-09-21

## Status

Accepted.

## Context

`docs/prereg-beads-three-arm.md` registers one question: does beads raise goal-leg success. It
also lists the conditions under which a run is declared UNMEASURED rather than null, and one of
them is "the beads arm issues zero bd calls". That was the right call when it was written. A
treatment nobody reached is not evidence about the treatment, and the 480-leg silence that
prompted the clause was a rig problem.

It stopped being a rig problem. Zero bd calls has now come back from four pilots, the most recent
with the streams kept, showing the agent stating in prose that it has recorded the facts while
making no tool call at all. That is not noise and it is not a wiring fault. It is the most robust
result the series has produced, and the registration as written throws it away: a finding that
replicates four times is classified as an experimental failure and excluded from analysis.

Three ways out were available.

1. **Relax the UNMEASURED clause in place** so a zero-call run reads as a null on the goal-leg
   endpoint. Rejected. It is not a null: a goal-leg contrast in which the treatment was never
   applied genuinely measured nothing, and the clause is correct about that. Removing it would let
   a broken rig report a null.
2. **Add capture as a second endpoint to the same document.** Rejected. The two questions want
   different samples, different fixed quantities and different validity conditions -- capture is
   read off the establish leg only, on the necessary variant only, against a treatment-only sample
   with the floor and comparator reused across turns. Folding that in would mean amending the
   three-arm registration on a cadence set by the capture series, and a registration amended every
   turn is not a registration.
3. **Register capture separately.** Chosen.

## Decision

Capture gets its own pre-registration, `docs/prereg-beads-capture.md`, with `reached` and `engaged`
as its endpoints. The three-arm document is amended by exactly one paragraph, which reclassifies
the zero-call item from UNMEASURED to measured-elsewhere and changes nothing else.

The two documents govern different runs and are not pooled. A zero-call beads arm still means the
three-arm goal-leg contrast measured nothing; it also means the capture endpoint measured zero,
which is a result.

## Consequences

**Good.** The finding that replicates is reportable. The three-arm registration stays fixed while
the capture series iterates, which is what lets the flywheel vary one thing per turn -- the beads
build -- without touching either registration. And the endpoints are honest about their own
scope: neither document claims the other's result.

**Costs.** Two registrations to keep consistent, and a shared fixed set (establish instruction,
protocol, floor and comparator paragraphs, task sample) that now has two readers. A change to any
of those ends both series, not one.

**Deferred.** The goal-leg contrast is currently unscoreable for reasons unrelated to memory
(`docs/finding-goal-endpoint-unscoreable.md`), so the three-arm registration has no runnable
endpoint until that is decided. That decision is independent of this one: capture does not wait
on it.
