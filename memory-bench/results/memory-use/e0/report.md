# E0a — memory-verb base rate over the pinned transcript corpus

Bead `mem-e4fby`. Series: MVP Memory Beads (gastownhall/beads#5877), endogenous
read-and-write measurement. Offline, mechanical, zero model calls.

Preregistration: `preregistration.json`, sha256
`301f7917fe30df503271f2ac5b045fde20654a65021acdfb55efb65def75609c`, committed
before any count was computed (the prereg commit strictly precedes the analysis
commit). Population pinned by `filelist.sha256`.

Amendments: `preregistration-amendment-1.json` (sha256
`83214c5f2397f85e204f5a0f5d5409dbd54a48d88fbd1e57bcbd47efb7c9a7fa`) and
`preregistration-amendment-2.json` (sha256
`928e5912fe9b275d7b3be31c3211a7d8a2a93472182a9f73e61922686b0635ae`). The locked
file is **byte-identical** to what was sealed, amendment 1 is byte-identical to
what it recorded, and both digests still verify. Six measurement rules are
superseded across the two appended amendments, and the numbers below are the ones
produced under all six. All three digests are emitted into `analysis.json`, so a
published number names its exact rule set. See §Amendment 2 for what changed in
this round and what it cost.

## Read this label before reading a number

**Every rate below is instructed-endogenous, not spontaneous.**
`/home/ds/projects/CLAUDE.md` standingly instructs every agent in this org to use
the capture verb, and the shipped `bd prime` teaches it in its own emitted text.
Nothing here is an untreated baseline, and no number here may be quoted as one.
What the corpus can support is a *floor under instruction*: this is what
memory-verb traffic looks like when agents have already been told to use it.

## Population

| | count |
|---|---|
| transcript files enumerated on disk (this study) | **12,143** |
| session transcripts *resolved* into the mem store | **874** (`README.md:4`) |
| files in the pinned filelist no longer readable | 37 |
| transcript lines scanned | 1,698,910 |
| Bash blocks mentioning `bd` | 67,522 |
| counted bd invocations | 35,860 |
| sessions carrying bd traffic | 4,418 |

The two file counts are side by side deliberately. The store's resolved-transcript
population is roughly 7% of what is on disk. The sealed selector-expressibility
study preregistered `gc session logs` resolution and then recorded abandoning it
for direct enumeration for exactly this reason
(`../../bdp/selector-expressibility/report.md`, Deviations); re-running it here
would have shrunk the denominator by an order of magnitude for no gain. Direct
enumeration is the larger, and the honest, pool.

**The filelist and the population definition did not move this round.**
`filelist.sha256` is unchanged, so E0b — which rebases onto this work — inherits
the same pinned population it was built against. The population *counts* moved a
little, for the reason §Reproducing gives: the filelist pins paths in a live tree,
not bytes. Fifteen more pinned files have since been deleted (22 → 37) and the
surviving ones kept appending.

Exclusions are published in two groups, because they do not have the same
stability:

| group | count |
|---|---|
| **drifting** — at or after the preregistration lock | 66 |
| **frozen** — placeholder/template invocations | 2,324 |
| **frozen** — `--help` invocations | 962 |

The lock is screened **first**, before every other exclusion. In the first run it
was screened last, so post-lock traffic could still move a published count: the
help count read 964 on one run and 966 on the next over a growing corpus. Only
one exclusion may drift with the corpus, and it is the one that names the drift.
The sealed study's cwd-substring self-exclusion gate is **inert** here — our own
sessions run with `cwd=/home/ds/projects/mem`, which matches none of its markers —
so the timestamp lock is the mechanism that actually holds.

The placeholder count rises 2,215 → 2,324 this round. That is not corpus drift:
it is amendment A1.5 restoring a preregistered screen that had gone silent. See
§Amendment 2.

## Results

### E0.5 — memory-verb share of bd traffic (headline)

| statistic | value |
|---|---|
| session-averaged share of bd invocations | **0.0057** (0.57%) |
| sessions with at least one memory verb | **129 / 4,418** (2.9%) |

502 of 35,860 bd invocations are memory verbs. Under a standing instruction to
capture, 97% of sessions that touch `bd` at all never touch a memory verb.

This share is unchanged by this round's reclassification, and deliberately so:
the 38 invocations that leave the write bucket below are still an agent reaching
for the memory surface, and E0.5 counts *reaches*, not successful ones.

### E0.1 — write rate, as a band

| | session-averaged share | sessions with >=1 | count |
|---|---|---|---|
| unambiguous (keyed) writes | 0.000054 | 9 | **9** |
| + unkeyed (band high) | 0.00032 | 17 | **21** |

**Writes are 21, not 59.** The 59 published last round included 38 invocations
of a write verb carrying a flag the shipped `bd 1.3.0-rc.1` does not declare
(`--show`, `--get`, `--list`). The binary rejects those on the undeclared flag,
so nothing was stored: they are read *attempts* spelled with a write verb, and
they are published as their own bucket in E0.2. Amendment 1 already said so — it
used exactly that argument to delete the one join hit the first run reported —
and then left the other 38 in the write bucket. A write rate that was 64% read
attempts was a parsing artifact, and it is withdrawn.

Of the 38, two carried an explicit key flag *and* an undeclared one, so keyed
writes fall 11 → 9 and unkeyed writes 48 → 12.

The band is over **key resolvability**, not over whether the invocation is a
write; both ends are write counts. On the shipped CLI the positional argument of
the capture verb is the memory **content**, and the key is auto-generated from it
unless `--key` is given — so a positional never names the memory being stored,
and only 9 of 21 writes say what they are storing under. Deciding what the other
12 were "really" keyed as would mean reading the argument's text, which is the
ZFC line this study does not cross. Only the keyed 9 can supply a key to E0.4.

### E0.2 — read rate, four buckets, never summed

| bucket | session-averaged share | sessions with >=1 | count |
|---|---|---|---|
| targeted read (keyed) | 0.000045 | 5 | **14** |
| search (by term) | 0.0034 | 108 | 291 |
| list-all browse (bare) | 0.0015 | 67 | 138 |
| **attempted read via a write verb** | 0.00043 | 36 | **38** |

The split is load-bearing. Pooled, "reads" total 481 and look like the dominant
memory behaviour in the corpus. But 467 of those carry **no key that the binary
would have accepted**, and 429 carry no key at all: a term search or a bare
list-all cannot name a prior capture, so neither can ever join one. Reporting a
single read rate would have credited memory retrieval with traffic that is
join-ineligible by construction, by a factor of 30.

The fourth bucket is new this round and is the 38 the write rate lost. It is
worth its own line for a reason beyond bookkeeping: **more sessions attempt a
keyed read through the wrong verb (36) than issue a correct keyed read (5)**.
That is a CLI-affordance finding, not a memory-behaviour one, and it is the sort
of thing a pooled bucket buries.

Membership in this bucket is decided by flag **name** against the set the shipped
binary declares in its own `--help`, captured verbatim into `shipped-cli-help/`
(re-capture with `shipped-cli-help/capture.sh`; digests in
`shipped-cli-help/shipped-cli-help.sha256`) and extracted from that text by
`cligrammar.help_flag_names`. The set is pinned as a literal in `verbs.py`, so no
published number depends on whichever `bd` is on `PATH` at run time, and the test
suite re-derives it from the committed help text and fails on drift. No argument
value is read anywhere: ZFC holds.

### E0.4 — read-after-write (RAW), over two denominators

The primary denominator is the preregistered one — keyed reads the binary
actually executes:

| | value |
|---|---|
| keyed targeted reads (denominator) | **14** |
| any prior write of the same key | **0 (0.000)** |
| **cross-session** | **0 (0.000)** |
| **cross-working-directory** | **0 (0.000)** |

The widened denominator adds the 36 keyed attempted reads from the bucket above:

| | value |
|---|---|
| keyed reads (denominator) | **50** |
| any prior write of the same key | **1 (0.020)** |
| **cross-session** | **1 (0.020)** |
| **cross-working-directory** | **0 (0.000)** |

**Both are published because the same rule fixes both.** The rule that moved 38
invocations out of the write bucket is the rule that decides whether they belong
in this denominator, and the headline null sits over a denominator that rule
moves. Publishing one number and staying quiet about the other would let the
choice of denominator do work the reader cannot see.

The argument for the primary denominator: E0.4 asks whether a read that *ran*
recovered an earlier capture. An invocation the binary rejected retrieved
nothing, so it cannot evidence reuse; admitting it enlarges the denominator with
invocations that could not have produced a hit, which deflates RAW by
construction — the mirror of the pooling error the read split exists to prevent.

The argument for the widened one: E0.4 is explicitly a *CLI-expressibility*
statistic, and under that reading the question is how often an agent **named a
key it expected to be there**. A rejected invocation names one exactly as an
executed one does. On that reading the corpus is not quite empty: one agent, in a
session other than the writing one, named a key an earlier keyed write had
stored — and got an unknown-flag error instead of the memory. That single
invocation is the strongest carry-over signal 1.7M lines of transcript contain,
and it failed on CLI grammar rather than on memory.

Note what this does *not* rescue. Under either denominator no executed read
recovers an earlier write, and 0/14 is a weaker null than 0/16 was: a zero over
14 is consistent with any true join rate below roughly 19% (one-sided 95%),
against 17% before.

**RAW is a CLI-expressibility measurement, not a reuse measurement.** It bounds
how much keyed read traffic *could* refer to something captured earlier. It
cannot show that a retrieved body was read or acted on: mechanism-FIRES is not
mechanism-CONSUMED, and nothing in a transcript's argv distinguishes a consumed
retrieval from an ignored one.

`join_eligibility_drops` ships beside these numbers and is **0** on this corpus.
A keyed invocation with no record timestamp has no position in corpus time, so it
cannot enter the join — but it was counted, bucketed, and is inside every rate
above. It is a join drop, not a screen; the first two runs filed it under
exclusions, which overstates what the screens removed. Count 0, taxonomy fixed
before a non-zero value can be misread (A1.6).

### Reference buckets (not memory verbs, not in E0.5)

| bucket | session-averaged share | count |
|---|---|---|
| injection (`prime`) | 0.00073 | 47 |
| dependency writes (`link`, `dep`) | 0.0034 | 481 |

**Correction, stated because it changes a count by an order of magnitude.**
`bd link` is shorthand for `bd dep add` — an issue-dependency edge, not a memory
verb. There is no `bd unlink`. Any count that folded `link` into memory writes
reported 502 writes where there are 21.

## Amendment 2 — what changed this round, and what it cost

Full record in `preregistration-amendment-2.json`. Every number published under
amendment 1 alone is withdrawn.

| id | superseded rule | amended rule |
|---|---|---|
| A1.4 | the write bucket is decided by subcommand alone | a write verb carrying a flag the shipped binary does not declare is an **attempted read**, published as its own bucket |
| A1.5 | the placeholder screen runs on the classified argv | it runs on **both** tokenizations — with and without the redirection strip |
| A1.6 | an undated keyed event is an exclusion | it is a **join-eligibility drop**, reported with the join |

**A1.4** is the round's blocker. Its consequences are in E0.1 (59 → 21 writes),
E0.2 (a fourth bucket of 38) and E0.4 (a second denominator, argued in the open
above). E0.5 is unchanged by design.

**A1.5** revives an arm the *previous* fix silenced. A1.2 moved redirection
stripping ahead of tokenization, and that stripper consumes `<key>` as an input
redirection — which is precisely the shape the preregistered
`placeholder_or_template` regex `^<.+>$` exists to catch. Documentation-example
invocations therefore reached the classifier with the placeholder gone: the keyed
read verb arrived as a bare verb, the capture verb as an unkeyed write.

Measured by isolated revert on the pinned filelist (screen on, screen off,
nothing else changed): **109 invocations**, of which 101 landed in `other`, 6 in
the dependency bucket, and **2 in the targeted-read bucket**. The review that
requested this arm estimated 5 into the dependency bucket and 0 into memory
buckets; the memory-bucket count is 2, not 0, and those 2 sit in the E0.4 primary
denominator, which is why it reads 14 here and 16 last round. A published null
whose denominator moves under a screen that was supposed to be running is the
same failure mode A1.4 is about, which is why the two ship together.

While there: unquoted `#` comments are now dropped before tokenization. No
counted invocation in this corpus carries one, so nothing moved; a comment word
would otherwise tokenize into a positional and move an invocation between
buckets.

## Isolated-revert probes

Each fix was reverted alone, with `results/memory-use/e0/__pycache__` cleared
between mutants (stale bytecode produced phantom results twice in earlier
rounds), and `tests/test_e0_rates.py` re-run:

| revert | suite | the join test (`…attempted_read_cannot_manufacture_a_join_hit`) |
|---|---|---|
| A1.2 (redirections reach argv) | 8 failed | **green** |
| A1.1 (bare-key write rule returns) | 1 failed | **green** |
| A1.1 + A1.2 combined | 10 failed | **green** |
| A1.4 (undeclared-flag reads counted as writes) | 7 failed | **red** |
| A1.5 (placeholder screen off the raw argv) | 1 failed | green |
| A1.6 (join drop filed as an exclusion) | 1 failed | green |

**Correction to a claim made last round.** That round's report said the join test
"fails under either reverted defect". It does not. Against the amendment-1 code
as reviewed, it stayed green under the A1.1 revert alone and redded only under
A1.2 and under the combined revert. Against the code as it now stands the claim
is narrower still: the test is green under A1.1, A1.2 *and* the combined revert,
because A1.4 removes that invocation from the write side ahead of both — and it
reds under the A1.4 revert. Every fix is still pinned by a test that reds when it
is removed; only the attribution was wider than the evidence.

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
2. **Read class split four ways** (see E0.2). The sealed profile had one read
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

**GO for the series**, on the same reading as before and with the same two
preregistered conditions holding: memory verbs are present but rare (0.57%
session-averaged, 2.9% of sessions), and no executed keyed read in the corpus
recovers a key any earlier write named.

The corrected numbers sharpen the shape rather than change it. Agents search far
more often than they recall (291 searches to 14 keyed reads), attempt keyed reads
through the wrong verb in more sessions than they issue them correctly (36 to 5),
and write 21 times across 1.7M lines of transcript under a standing instruction
to write. The one key ever named across a session boundary was named in an
invocation the CLI rejected.

That is the same shape arXiv 2607.20972 reports — voluntary memory use near zero
with a pre-seeded store, and harness-side deterministic delivery beating storage
— reproduced here on a much larger real corpus. It is the gap E0b
(prime-delivery share) and E1 (the guidance-strength ladder, per arXiv
2608.25198's monotone call-rate dial) are designed to move.

Three limits on that GO. First, n is small where it matters most: 14 keyed
targeted reads is not a base rate anyone should power a study against, and a
downstream experiment must generate its own reads rather than sample these. A
zero over 14 rules out a *common* carry-over, not a rare one. Second, the
corpus was produced under the **shipped** `bd prime`, which violates R8 by
emitting `## Persistent Memories (N)` followed by full bodies — so the near-zero
read rate here is the read rate *when bodies are already being auto-injected*, a
condition in which an agent has little reason to issue an explicit read. E0b must
synthesize the R8-compliant prime surface harness-side; no experiment about R8
can be obtained by wrapping the installed binary. Third, this round found that
one of the corpus's apparent behaviours (writing) was largely a mis-parse of
another (attempting to read). Any downstream number keyed on a verb name alone
should be assumed to carry the same risk until it is checked against the shipped
binary's grammar.

## Reproducing

```
sha256sum -c results/memory-use/e0/preregistration.sha256
sha256sum -c results/memory-use/e0/preregistration-amendment-1.sha256
sha256sum -c results/memory-use/e0/preregistration-amendment-2.sha256
sha256sum -c results/memory-use/e0/filelist.sha256
(cd results/memory-use/e0/shipped-cli-help && sha256sum -c shipped-cli-help.sha256)
uv run python results/memory-use/e0/rates.py \
    --filelist results/memory-use/e0/filelist.txt --json
uv run pytest tests/test_e0_rates.py
```

All of these run from `memory-bench/`.

`filelist.txt` pins the population this report was computed from;
re-enumerating the live tree would yield a different, larger filelist and
different counts.

The pinned filelist does **not** freeze the bytes behind it. It names paths in a
live tree, so a re-run can differ in two directions: already-pinned sessions keep
appending (`lines_scanned` grows, and post-lock invocations land in the drifting
exclusion), and pinned files can be deleted
(`files_in_filelist_no_longer_readable` grows, and every population count falls
with it). Across the runs behind this report and the last, readable files fell by
15 and counted invocations by 108 — of which 109 are A1.5's restored screen, so
the corpus itself moved by a handful in the other direction. Every bucket count
and every published rate moved only for the reasons named in §Amendment 2; a
future re-run may move the population line without moving a result, and that is
the expected behaviour, not a discrepancy.
