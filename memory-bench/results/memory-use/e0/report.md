# E0a — memory-verb base rate over the pinned transcript corpus

Bead `mem-e4fby`. Series: MVP Memory Beads (gastownhall/beads#5877), endogenous
read-and-write measurement. Offline, mechanical, zero model calls.

Preregistration: `preregistration.json`, sha256
`301f7917fe30df503271f2ac5b045fde20654a65021acdfb55efb65def75609c`, committed
before any count was computed (the prereg commit strictly precedes the analysis
commit). Population pinned by `filelist.sha256`.

Amendment: `preregistration-amendment-1.json`, sha256
`83214c5f2397f85e204f5a0f5d5409dbd54a48d88fbd1e57bcbd47efb7c9a7fa`. The locked
file is **byte-identical** to what was sealed and its digest still verifies.
Three measurement rules are superseded there, in an appended record, and the
numbers below are the ones produced under the amended rules. Both digests are
emitted into `analysis.json`, so a published number names its exact rule set.
See §Amendment 1 for what changed and what it cost.

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
| files in the pinned filelist no longer readable | 22 |
| transcript lines scanned | 1,697,979 |
| Bash blocks mentioning `bd` | 67,498 |
| counted bd invocations | 35,968 |
| sessions carrying bd traffic | 4,418 |

The two file counts are side by side deliberately. The store's resolved-transcript
population is roughly 7% of what is on disk. The sealed selector-expressibility
study preregistered `gc session logs` resolution and then recorded abandoning it
for direct enumeration for exactly this reason
(`../../bdp/selector-expressibility/report.md`, Deviations); re-running it here
would have shrunk the denominator by an order of magnitude for no gain. Direct
enumeration is the larger, and the honest, pool.

Exclusions are published in two groups, because they do not have the same
stability:

| group | count |
|---|---|
| **drifting** — at or after the preregistration lock | 45 |
| **frozen** — placeholder/template invocations | 2,215 |
| **frozen** — `--help` invocations | 962 |

The lock is screened **first**, before every other exclusion. In the first run it
was screened last, so post-lock traffic could still move a published count: the
help count read 964 on one run and 966 on the next over a growing corpus. Only
one exclusion may drift with the corpus, and it is the one that names the drift.
The sealed study's cwd-substring self-exclusion gate is **inert** here — our own
sessions run with `cwd=/home/ds/projects/mem`, which matches none of its markers —
so the timestamp lock is the mechanism that actually holds.

## Results

### E0.5 — memory-verb share of bd traffic (headline)

| statistic | value |
|---|---|
| session-averaged share of bd invocations | **0.0056** (0.56%) |
| sessions with at least one memory verb | **129 / 4,418** (2.9%) |

504 of 35,968 bd invocations are memory verbs. Under a standing instruction to
capture, 97% of sessions that touch `bd` at all never touch a memory verb.

### E0.1 — write rate, as a band

| | session-averaged share | sessions with >=1 | count |
|---|---|---|---|
| unambiguous (keyed) writes | 0.000094 | 11 | 11 |
| + unkeyed (band high) | 0.00074 | 50 | 59 |

The band is over **key resolvability**, not over whether the invocation is a
write; both ends are write counts. On the shipped CLI (bd 1.3.0-rc.1) the
positional argument of `bd remember` is the memory **content**, and the key is
auto-generated from it unless `--key` is given — so a positional never names the
memory being stored, and only 11 of 59 writes say what they are storing under.
Deciding what the other 48 were "really" keyed as would mean reading the
argument's text, which is the ZFC line this study does not cross. Only the keyed
11 can supply a key to E0.4.

### E0.2 — read rate, three buckets, never summed

| bucket | session-averaged share | sessions with >=1 | count |
|---|---|---|---|
| targeted read (`recall <key>`) | 0.000047 | 5 | **16** |
| search (`memories <term>`) | 0.0034 | 108 | 291 |
| list-all browse (`memories`, bare) | 0.0014 | 67 | 138 |

The split is load-bearing. Pooled, "reads" total 445 and look like the dominant
memory behaviour in the corpus. But 429 of those 445 carry **no key**: a term
search or a bare list-all cannot name a prior capture, so neither can ever join
one. Reporting a single read rate would have credited memory retrieval with
traffic that is join-ineligible by construction, by a factor of 28.

### E0.4 — read-after-write (RAW), published twice

| | value |
|---|---|
| keyed targeted reads (denominator) | 16 |
| any prior write of the same key | **0 (0.000)** |
| **cross-session** | **0 (0.000)** |
| **cross-working-directory** | **0 (0.000)** |

**The join is empty.** Not one keyed read in the corpus recovers a key that any
earlier write named — not across sessions, not across directories, not even
within a single session.

The first run of this study reported 1/16 here. That hit was an artifact of both
defects Amendment 1 corrects, compounded: the invocation was
`bd remember --get <term> 2>/dev/null`, an **attempted read**, whose redirection
target survived tokenization as a positional and whose first positional was then
read as a key under the superseded bare-key rule. It joined to a
`bd recall <term>` four seconds later in the same session. Under the amended
rules it names no key and is not in the join at all. (`--get` is not a flag of
the shipped `bd remember`; the amended rule keeps it out without anyone having to
decide what it meant, because it carries no key flag.) The corrected result moves
further toward a null. The null is the finding.

**RAW is a CLI-expressibility measurement, not a reuse measurement.** It bounds
how much keyed read traffic *could* refer to something captured earlier. It
cannot show that a retrieved body was read or acted on: mechanism-FIRES is not
mechanism-CONSUMED, and nothing in a transcript's argv distinguishes a consumed
retrieval from an ignored one. A zero here is an upper bound that happens to be
zero, which is a stronger statement than a zero reuse estimate would be.

### Reference buckets (not memory verbs, not in E0.5)

| bucket | session-averaged share | count |
|---|---|---|
| injection (`prime`) | 0.00073 | 47 |
| dependency writes (`link`, `dep`) | 0.0034 | 486 |

**Correction, stated because it changes a count by 9x.** `bd link` is shorthand
for `bd dep add` — an issue-dependency edge, not a memory verb. There is no
`bd unlink`. Any count that folded `link` into memory writes reported 545 writes
where there are 59.

## Amendment 1 — what changed, and what it cost

Full record in `preregistration-amendment-1.json`. Every number in the first run
of this study is withdrawn; these three rules produced all of the ones above.

| id | superseded rule | amended rule |
|---|---|---|
| A1.1 | a write is unambiguous with `--key` **or two or more positionals** | a write is unambiguous **only** with an explicit key flag |
| A1.2 | redirections stripped from argv, token by token | redirections stripped from the raw command text, quote-aware, **before** tokenization |
| A1.3 | help/placeholder screened before the lock | the lock is screened **first** |

A1.1 was locked against a CLI grammar the shipped binary contradicts: `bd
remember --help` states the positional is content with an auto-generated key, and
that a bare key naming an existing memory is *recalled* rather than stored. The
locked clause stays in place as the record of what was believed at lock time.

A1.2 is the defect that drove the first run's numbers. `shlex` emits an attached
redirection as **one token** (`2>/dev/null`), which a bare-operator regex does not
match, so the target reached argv and was counted as a positional. The first run
called 21 writes unambiguous: 11 carried an explicit key flag, and 10 qualified
only under the bare-key rule. **Nine of those 10 owe their second positional to a
redirect target**, and all 10 are in fact `bd remember --show <key>` or
`bd remember --get <key>` — attempted READS, counted as keyed writes. A1.2 also
moved redirected `bd memories 2>/dev/null` invocations out of browse into search
(on correction, search falls 382 -> 291 and browse rises 47 -> 138). The fix has
to run on text that still carries its quotes: once `shlex` has discarded quoting,
a redirection token and a `>` inside a quoted memory body are indistinguishable,
so no token-level test can separate them. `strip_shell` was removed rather than
patched, and `tests/test_e0_rates.py` pins both directions — a redirection never
survives into argv, and a quoted `>` always does.

## Declared deviations from the sealed profile

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
5. **The sealed `strip_shell` is not reused.** See A1.2; the replacement and its
   reason are recorded in `cligrammar.py`'s provenance docstring.

The CLI-grammar helpers were lifted into `cligrammar.py` rather than imported
from the sealed package, with per-symbol source line ranges recorded in that
module's docstring.

## What this decides

**GO for the series.** The decision rule preregistered two conditions and both
hold, more sharply than before the correction: memory verbs are present but rare
(0.56% session-averaged, 2.9% of sessions), and the join against a targeted-read
rate that is already near zero is itself **exactly zero**. Under a standing
instruction to capture, agents write occasionally, search far more often than they
recall, and never recover a key any earlier invocation wrote.

That is the same shape arXiv 2607.20972 reports — voluntary memory use near zero
with a pre-seeded store, and harness-side deterministic delivery beating storage
— reproduced here on a much larger real corpus. It is the gap E0b
(prime-delivery share) and E1 (the guidance-strength ladder, per arXiv
2608.25198's monotone call-rate dial) are designed to move.

Two limits on that GO. First, n is small where it matters most: 16 keyed targeted
reads is not a base rate anyone should power a study against, and a downstream
experiment must generate its own reads rather than sample these. A zero over a
denominator of 16 is consistent with any true join rate below roughly 17% (the
one-sided 95% bound), so it rules out a *common* carry-over, not a rare one.
Second, and load-bearing for R8: this corpus was produced under the **shipped**
`bd prime`, which violates R8 by emitting `## Persistent Memories (N)` followed by
full bodies. So the near-zero read rate measured here is the read rate *when
bodies are already being auto-injected* — a condition in which an agent has little
reason to issue an explicit read. E0b must synthesize the R8-compliant prime
surface harness-side; no experiment about R8 can be obtained by wrapping the
installed binary.

## Reproducing

```
sha256sum -c results/memory-use/e0/preregistration.sha256
sha256sum -c results/memory-use/e0/preregistration-amendment-1.sha256
sha256sum -c results/memory-use/e0/filelist.sha256
uv run python results/memory-use/e0/rates.py \
    --filelist results/memory-use/e0/filelist.txt --json
uv run pytest tests/test_e0_rates.py
```

All four run from `memory-bench/`.

`filelist.txt` pins the population this report was computed from;
re-enumerating the live tree would yield a different, larger filelist and
different counts.

The pinned filelist does **not** freeze the bytes behind it. It names paths in a
live tree, so a re-run can differ in two directions: already-pinned sessions keep
appending (`lines_scanned` grows, and post-lock invocations land in the drifting
exclusion), and pinned files can be deleted (`files_in_filelist_no_longer_readable`
grows, and every population count falls with it). Between the two runs behind this
report the readable-file count fell by 7 and counted invocations by 3. Every bucket
count, every published rate, and the empty join are unchanged across those runs; a
future re-run may move the population line without moving a result, and that is the
expected behaviour, not a discrepancy.
