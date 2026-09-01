# E0a — memory-verb base rate over the pinned transcript corpus

Bead `mem-e4fby`. Series: MVP Memory Beads (gastownhall/beads#5877), endogenous
read-and-write measurement. Offline, mechanical, zero model calls.

Preregistration: `preregistration.json`, sha256
`301f7917fe30df503271f2ac5b045fde20654a65021acdfb55efb65def75609c`, committed
before any count was computed (git history: the prereg commit strictly precedes
the analysis commit). Population pinned by `filelist.sha256`.

## Read this label before reading a number

**Every rate below is instructed-endogenous, not spontaneous.**
`/home/ds/projects/CLAUDE.md` standingly instructs every agent in this org to use
`bd remember`, and the shipped `bd prime` teaches the verb in its own emitted
text. Nothing here is an untreated baseline, and no number here may be quoted as
one. What the corpus can support is a *floor under instruction*: this is what
memory-verb traffic looks like when agents have already been told to use it.

## Population

| | count |
|---|---|
| transcript files enumerated on disk (this study) | **12,143** |
| session transcripts *resolved* into the mem store | **874** (`README.md:4`) |
| transcript lines scanned | 1,698,464 |
| Bash blocks mentioning `bd` | 67,480 |
| counted bd invocations | 35,896 |
| sessions carrying bd traffic | 4,421 |

The two file counts are side by side deliberately. The store's resolved-transcript
population is roughly 7% of what is on disk. The sealed selector-expressibility
study preregistered `gc session logs` resolution and then recorded abandoning it
for direct enumeration for exactly this reason
(`../../bdp/selector-expressibility/report.md`, Deviations); re-running it here
would have shrunk the denominator by an order of magnitude for no gain. Direct
enumeration is the larger, and the honest, pool.

Exclusions, all published: 5 invocations at or after the preregistration lock
(this study's own traffic), 2,304 placeholder/template invocations, 964 `--help`
invocations. The sealed study's cwd-substring self-exclusion gate is **inert**
here — our own sessions run with `cwd=/home/ds/projects/mem`, which matches none
of its markers — so the timestamp lock is the mechanism that actually holds.

## Results

### E0.5 — memory-verb share of bd traffic (headline)

| statistic | value |
|---|---|
| session-averaged share of bd invocations | **0.0057** (0.57%) |
| sessions with at least one memory verb | **130 / 4,421** (2.9%) |

505 of 35,896 bd invocations are memory verbs. Under a standing instruction to
capture, 97% of sessions that touch `bd` at all never touch a memory verb.

### E0.1 — write rate, as a band

| | session-averaged share | sessions with >=1 | count |
|---|---|---|---|
| unambiguous writes | 0.00022 | 20 | 21 |
| + ambiguous (band high) | 0.00078 | 51 | 60 |

The band is not decoration. `bd remember` accepts both a bare-key form and a
`--key` form, and a single-positional invocation cannot be resolved from argv
grammar into key-plus-content versus content-only. 39 of 60 writes are in that
irreducible state. Resolving them would mean reading the argument's text, which
is the ZFC line this study does not cross, so the write rate is published as
`[0.00022, 0.00078]` and never as a point estimate.

### E0.2 — read rate, three buckets, never summed

| bucket | session-averaged share | sessions with >=1 | count |
|---|---|---|---|
| targeted read (`recall <key>`) | 0.000048 | 5 | **16** |
| search (`memories <term>`) | 0.0045 | 114 | 382 |
| list-all browse (`memories`, bare) | 0.00039 | 30 | 47 |

The split is load-bearing. Pooled, "reads" total 445 and look like the dominant
memory behaviour in the corpus. But 429 of those 445 carry **no key**: a term
search or a bare list-all cannot name a prior capture, so neither can ever join
one. Reporting a single read rate would have credited memory retrieval with
traffic that is join-ineligible by construction, by a factor of 28.

### E0.4 — read-after-write (RAW), published twice

| | value |
|---|---|
| keyed targeted reads (denominator) | 16 |
| any prior write of the same key | 1 (0.0625) |
| **cross-session** | **0 (0.000)** |
| **cross-working-directory** | **0 (0.000)** |

The single hit is a within-session, within-directory read-back: an agent writing
a key and reading it again inside the same session. Publishing RAW twice is what
makes that visible — reported once, as "6% of keyed reads join a prior write", it
would have read as carry-over. Zero reads in the corpus recover a key written by
a different session.

**RAW is a CLI-expressibility measurement, not a reuse measurement.** It bounds
how much keyed read traffic *could* refer to something captured earlier. It
cannot show that a retrieved body was read or acted on: mechanism-FIRES is not
mechanism-CONSUMED, and nothing in a transcript's argv distinguishes a consumed
retrieval from an ignored one.

### Reference buckets (not memory verbs, not in E0.5)

| bucket | session-averaged share | count |
|---|---|---|
| injection (`prime`) | 0.00073 | 47 |
| dependency writes (`link`, `dep`) | 0.0034 | 481 |

**Correction, stated because it changes a count by 8x.** `bd link` is shorthand
for `bd dep add` — an issue-dependency edge, not a memory verb. There is no
`bd unlink`. Any count that folded `link` into memory writes reported 541 writes
where there are 60.

## Declared deviations

1. **`prime` reclassified from write to injection.** The sealed
   selector-expressibility extractor lists `prime` in `WRITE_SUBCOMMANDS`
   (`../../bdp/selector-expressibility/extract.py:29-46, prime at :36`). Here it is an
   INJECTION verb. This removes a verb from the sealed profile's write kind, so
   the two studies' write counts are **not comparable verb-for-verb**. It is
   declared as a deviation, not applied as a fix: the sealed package is left
   byte-identical, because its `manifest.json` pins content hashes over
   `extract.py` and `classify.py`, and editing a sealed file to correct a label
   breaks the seal. The reason for the split is the thing this series measures:
   `bd prime` delivers context *into* the agent, and arXiv 2607.20972 found
   delivery beat storage, so conflating the two destroys the distinction before
   it can be measured.
2. **Read class split three ways** (see E0.2). The sealed profile had one read
   kind.
3. **Self-exclusion by preregistration timestamp**, not by cwd substring, because
   the sealed gate is inert against this study's own working directory.
4. **`link` bucketed as a dependency write**, with the correction stated above.

The CLI-grammar helpers were lifted into `cligrammar.py` rather than imported
from the sealed package, with per-symbol source line ranges recorded in that
module's docstring.

## What this decides

**GO for the series.** The decision rule preregistered two conditions and both
hold: memory verbs are present but rare (0.57% session-averaged, 2.9% of
sessions), and the cross-session join is **zero** against a targeted-read rate
that is itself already near zero. Under a standing instruction to capture,
agents write occasionally, search far more often than they recall, and never
recover a key another session wrote.

That is the same shape arXiv 2607.20972 reports — voluntary memory use near zero
with a pre-seeded store, and harness-side deterministic delivery beating storage
— reproduced here on a much larger real corpus. It is the gap E0b
(prime-delivery share) and E1 (the guidance-strength ladder, per arXiv
2608.25198's monotone call-rate dial) are designed to move.

Two limits on that GO. First, n is small where it matters most: 16 keyed
targeted reads is not a base rate anyone should power a study against, and a
downstream experiment must generate its own reads rather than sample these.
Second, and load-bearing for R8: this corpus was produced under the **shipped**
`bd prime`, which violates R8 by emitting `## Persistent Memories (N)` followed
by full bodies. So the near-zero read rate measured here is the read rate *when
bodies are already being auto-injected* — a condition in which an agent has
little reason to issue an explicit read. E0b must synthesize the R8-compliant
prime surface harness-side; no experiment about R8 can be obtained by wrapping
the installed binary.

## Reproducing

```bash
cd memory-bench
sha256sum -c results/memory-use/e0/preregistration.sha256
sha256sum -c results/memory-use/e0/filelist.sha256
uv run python results/memory-use/e0/rates.py \
    --filelist results/memory-use/e0/filelist.txt --json
uv run pytest tests/test_e0_rates.py
```

The corpus is a live on-disk tree and grows; `filelist.txt` pins the population
this report was computed from. Re-enumerating will yield a different, larger
filelist and different counts.
