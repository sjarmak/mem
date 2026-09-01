# E0a — memory-verb base rate over the pinned transcript corpus

Bead `mem-e4fby`. Series: MVP Memory Beads (gastownhall/beads#5877), endogenous
read-and-write measurement. Offline, mechanical, zero model calls.

Preregistration: `preregistration.json`, sha256
`301f7917fe30df503271f2ac5b045fde20654a65021acdfb55efb65def75609c`, committed
before any count was computed (the prereg commit strictly precedes the analysis
commit). Population pinned by `filelist.sha256`.

Amendments: `preregistration-amendment-1.json` (sha256
`83214c5f2397f85e204f5a0f5d5409dbd54a48d88fbd1e57bcbd47efb7c9a7fa`),
`preregistration-amendment-2.json` (sha256
`928e5912fe9b275d7b3be31c3211a7d8a2a93472182a9f73e61922686b0635ae`) and
`preregistration-amendment-3.json` (sha256
`d9a03f8763d0686dc46d89d3a3373b32e4adafd6dc5c32342592efab72753563`). The locked
file is **byte-identical** to what was sealed, each amendment is byte-identical to
what it recorded, and all four digests still verify. Ten measurement rules
(A1.1–A1.10) are superseded across the three appended amendments, and the numbers
below are the ones produced under all ten. All four digests are emitted into
`analysis.json`, so a published number names its exact rule set. See §Amendment 3
for what changed in this round and what it cost.

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
| files in the pinned filelist no longer readable | 191 |
| transcript lines scanned | 1,687,975 |
| Bash blocks mentioning `bd` | 67,144 |
| counted bd invocations | 35,667 |
| sessions carrying bd traffic | 4,404 |

The two file counts are side by side deliberately. The store's resolved-transcript
population is roughly 7% of what is on disk. The sealed selector-expressibility
study preregistered `gc session logs` resolution and then recorded abandoning it
for direct enumeration for exactly this reason
(`../../bdp/selector-expressibility/report.md`, Deviations); re-running it here
would have shrunk the denominator by an order of magnitude for no gain. Direct
enumeration is the larger, and the defensible, pool.

**The filelist and the population definition did not move this round.**
`filelist.sha256` is unchanged, so E0b — which rebases onto this work — inherits
the same pinned population it was built against.

Exclusions are published in two groups:

| group | count |
|---|---|
| **drifting** — at or after the preregistration lock | 88 |
| **frozen against corpus growth** — placeholder/template invocations | 2,255 |
| **frozen against corpus growth** — `--help` invocations | 922 |

The lock is screened **first**, before every other exclusion. In the first run it
was screened last, so post-lock traffic could still move a published count: the
help count read 964 on one run and 966 on the next over a growing corpus.
Screening the lock first freezes the other counts against corpus **growth**. The
sealed study's cwd-substring self-exclusion gate is **inert** here — our own
sessions run with `cwd=/home/ds/projects/mem`, which matches none of its markers —
so the timestamp lock is the mechanism that actually holds.

**Withdrawal (A1.10).** Two rounds of this report said the after-lock exclusion is
*the only exclusion that can drift*. That is false in the other direction. The
filelist pins **paths in a live tree, not bytes**, so a pinned transcript can be
deleted or rotated away and take its invocations out of every count below it.
Re-running the analysis unchanged, over the same pinned filelist, has moved
`help_invocation` 962 → 937 → 922, `dep_write` 481 → 477 → 474 and
`files_in_filelist_no_longer_readable` 37 → 66 → 191 across three runs. Lock-first
ordering buys freezing against **growth** and nothing against **attrition**. Every
count in `analysis.json` is as-of its run, and a re-run months from now will read a
smaller corpus. The claim is withdrawn rather than repaired: no ordering can make
the files persist.

## Results

### E0.5 — memory-verb share of bd traffic (headline)

| statistic | value |
|---|---|
| session-averaged share of bd invocations | **0.0057** (0.57%) |
| sessions with at least one memory verb | **129 / 4,404** (2.9%) |

504 of 35,667 bd invocations are memory verbs. Under a standing instruction to
capture, 97% of sessions that touch `bd` at all never touch a memory verb.

This share is **unmoved by every reclassification in this round and the last**, and
deliberately so. An invocation the shipped binary refuses is still an agent
reaching for the memory surface, and E0.5 counts *reaches*, not successful ones.
Each round below redistributes invocations *within* the memory verbs; none of them
changes what any agent did.

### E0.1 — write rate, as two nested bands

| | session-averaged share | sessions with >=1 | count |
|---|---|---|---|
| unambiguous (keyed) writes | 7.6e-06 | 4 | **4** |
| + unkeyed accepted writes (inner band high) | 1.2e-05 | 5 | **5** |
| + bare single-positional captures (outer band high) | 2.6e-04 | 11 | **14** |

**Writes are 4 to 5, with an outer bound of 14 — not 21, and not the 59 of two
rounds ago.** The two bands measure different uncertainties and are nested so they
cannot be read as one:

- The **inner** band is the preregistered one and is over **key resolvability**.
  Both of its ends are invocations the shipped grammar accepts as writes; they
  differ only in whether the stored memory is named. On the shipped CLI the
  positional argument of the capture verb is the memory *content* and the key is
  auto-generated from it unless `--key` is given, so a positional never names the
  memory being stored. Only the low end can supply a key to E0.4.
- The **outer** band is over **whether it is a write at all**, and it exists
  because the shipped help documents a bare single-positional capture as a
  *recall* when the argument names an existing memory (see A1.9 below).

Seven further invocations previously published as writes are now their own bucket:
a write verb carrying a positional count its own usage line refuses. Five of the
seven are the keyed-but-contentless form — a key that says where to put something,
with nothing to put. That form stored nothing, and E0.4 below turns on it.

### E0.2 — read rate, six buckets, never summed

| bucket | session-averaged share | sessions with >=1 | count |
|---|---|---|---|
| targeted read (keyed) | 4.8e-05 | 5 | **16** |
| search (by term) | 0.0034 | 108 | 291 |
| list-all browse (bare) | 0.0015 | 67 | 138 |
| **attempted read via a write verb** | 4.3e-04 | 36 | **38** |
| **bare capture, read-or-write ambiguous** | 2.5e-04 | 7 | **9** |
| **refused by the shipped grammar** | 5.5e-05 | 7 | **7** |

The split is load-bearing. Pooled, "reads" total 445 and look like the dominant
memory behaviour in the corpus. But 429 of those carry **no key at all**: a term
search or a bare list-all cannot name a prior capture, so neither can ever join
one. Reporting a single read rate would have credited memory retrieval with
traffic that is join-ineligible by construction, by a factor of nearly 30.

The last three buckets are the ones the last two review rounds added, and each is
published rather than folded because folding it would move a headline:

- **Attempted read via a write verb (38).** A write verb carrying a flag the
  shipped `bd 1.3.0-rc.1` does not declare (`--show`, `--get`, `--list`). The
  binary rejects those on the undeclared flag, so nothing was stored: they are read
  *attempts* spelled with a write verb. **More sessions attempt a keyed read
  through the wrong verb (36) than issue a correct keyed read (5).** That is a
  CLI-affordance finding, not a memory-behaviour one, and a pooled bucket buries it.
- **Bare capture, ambiguous (9).** Published as the outer end of E0.1 *and* the
  outer end of the targeted-read rate (16 → 25, 10 sessions), and inside neither
  point estimate.
- **Refused by the shipped grammar (7).** Neither a write nor a read: nothing was
  stored and nothing was retrieved.

Membership in the attempted-read bucket is decided by flag **name**, and membership
in the refused bucket by positional **count**, both against what the shipped binary
declares in its own `--help`, captured verbatim into `shipped-cli-help/`
(re-capture with `shipped-cli-help/capture.sh`; digests in
`shipped-cli-help/shipped-cli-help.sha256`) and extracted from that text by
`cligrammar.help_flag_names` and `cligrammar.help_usage_positionals`. Both are
pinned as literals in `verbs.py`, so no published number depends on whichever `bd`
is on `PATH` at run time, and the test suite re-derives both from the committed
help text and fails on drift. No argument value is read anywhere: ZFC holds, and
the arity rule sees a count, never a body.

### E0.4 — read-after-write (RAW), over two denominators

The primary denominator is the preregistered one — keyed reads the binary actually
executes:

| | value |
|---|---|
| keyed targeted reads (denominator) | **16** |
| any prior write of the same key | **0 (0.000)** |
| **cross-session** | **0 (0.000)** |
| **cross-working-directory** | **0 (0.000)** |

The widened denominator adds the 36 keyed attempted reads from the bucket above:

| | value |
|---|---|
| keyed reads (denominator) | **52** |
| any prior write of the same key | **0 (0.000)** |
| **cross-session** | **0 (0.000)** |
| **cross-working-directory** | **0 (0.000)** |

**The one apparent hit was reading back a write that stored nothing.** Last round
the widened denominator carried a single cross-session hit, described there as
"the strongest carry-over signal 1.7M lines of transcript contain". A1.8 dissolves
it: the write it joined against was `--key <k>` with no content, which the shipped
usage line refuses. Nothing was stored under that key, so nothing could be
recovered from it. The corpus contains **zero** read-after-write joins under either
denominator. This strengthens the null rather than weakening it, and it is the
second time in this series that an apparent memory behaviour turned out to be an
artifact of not checking the invocation against the shipped grammar.

**Both denominators are published because the same rules fix both.** The rules that
moved 45 invocations out of the write bucket are the rules that decide whether
those invocations belong in this denominator, and the headline null sits over a
denominator they move. Publishing one number and staying quiet about the other
would let the choice of denominator do work the reader cannot see.

The argument for the primary denominator: E0.4 asks whether a read that *ran*
recovered an earlier capture. An invocation the binary rejected retrieved nothing,
so it cannot evidence reuse; admitting it enlarges the denominator with invocations
that could not have produced a hit, which deflates RAW by construction — the mirror
of the pooling error the read split exists to prevent.

The argument for the widened one: E0.4 is explicitly a *CLI-expressibility*
statistic, and under that reading the question is how often an agent **named a key
it expected to be there**. A rejected invocation names one exactly as an executed
one does. Under the corrected rules both readings now give the same answer.

A zero over 16 is consistent with any true join rate below roughly 17% (one-sided
95%); over 52, below roughly 5.6%. The wider denominator is the tighter bound, and
it is the one that is not preregistered — so the preregistered 0/16 is the number
that carries, and the 0/52 is reported beside it.

**RAW is a CLI-expressibility measurement, not a reuse measurement.** It bounds how
much keyed read traffic *could* refer to something captured earlier. It cannot show
that a retrieved body was read or acted on: mechanism-FIRES is not
mechanism-CONSUMED, and nothing in a transcript's argv distinguishes a consumed
retrieval from an ignored one.

`join_eligibility_drops` ships beside these numbers and is **0** on this corpus. A
keyed invocation with no record timestamp has no position in corpus time, so it
cannot enter the join — but it was counted, bucketed, and is inside every rate
above. It is a join drop, not a screen; the first two runs filed it under
exclusions, which overstates what the screens removed. Count 0, taxonomy fixed
before a non-zero value can be misread (A1.6).

### Reference buckets (not memory verbs, not in E0.5)

| bucket | session-averaged share | count |
|---|---|---|
| injection (`prime`) | 0.00073 | 47 |
| dependency writes (`link`, `dep`) | 0.0034 | 474 |

**Correction, stated because it changes a count by an order of magnitude.**
`bd link` is shorthand for `bd dep add` — an issue-dependency edge, not a memory
verb. There is no `bd unlink`. Any count that folded `link` into memory writes
reported 478 writes where there are 5.

## Amendment 3 — what changed this round, and what it cost

Full record in `preregistration-amendment-3.json`. Every number published under
amendments 1 and 2 is withdrawn.

| id | superseded rule | amended rule |
|---|---|---|
| A1.7 | A1.5's raw-form screen ran the full placeholder alternation | on the pre-strip tokenization it is narrowed to `^<.+>$`, the one shape the redirection strip erases |
| A1.8 | A1.4's rejection argument was applied at the flag layer only | a write verb whose **positional count** its own usage line refuses is `REJECTED_BY_SHIPPED_GRAMMAR` |
| A1.9 | A1.1's ambiguity band treated a bare capture as a write | it is `BARE_KEY_AMBIGUOUS`: its own bucket, and the outer end of two rates |
| A1.10 | the claim that only the after-lock exclusion can drift | withdrawn; see §Population |

**A1.7 is a fix to a fix.** A1.5 restored the preregistered placeholder screen by
re-running it on the pre-strip tokenization — correct, because `strip_redirections`
consumes `<key>` as an input redirection and a documentation example therefore
reached the classifier as a bare verb. But it ran the *full* alternation there, and
the pre-strip form still contains redirection **targets**. The `^\$` arm fired on
shapes like `$SP/allbeads.json`, throwing away 20 invocations whose offending token
the strip had already removed. Two of them are executed keyed reads. The raw form
exists to see what the strip removed; screening it for shapes the strip never
touches judges tokens the analysed argv does not contain.

Of A1.5's 109 screened invocations, **89 are `<...>` placeholders** and 20 are not:
18 belong in `other`, 2 in the targeted-read bucket. A1.5's own attribution ("101
into other, 6 into dep_write, 2 into targeted read") described the over-wide screen
and is superseded.

**A1.8 is last round's rejection, one layer down.** A1.4's argument is that the
shipped binary *rejects* the invocation, so nothing was stored, so it is not a
write. Nothing in that argument is about flags. The same commit that shipped A1.4
also committed the help text showing that both write verbs declare exactly one
positional — so a zero-positional form (including the keyed-but-contentless one)
and a two-positional form are refused too, and seven such invocations sat inside
the published write rate. Applying an argument at one layer and not the next is the
defect that got the last two rounds rejected; it is applied to both layers now, and
mechanically to *both* write verbs rather than special-cased.

**A1.9 is a decision the committed help text forced.** That text says the
positional is the memory *content*, and then says that if it is a bare key naming
an existing memory it is **recalled** instead — the same operation as the keyed read
verb — and refused if it names nothing. Three outcomes, selected by store state that
no transcript records. Grammar cannot decide it, and this study does not pretend to:
calling all nine writes assumes the reading that inflates the write rate, and calling
all nine reads assumes the one that deflates it. They are a bucket and a band on both
rates they could belong to. The clause is documented for the capture verb only, so
the removal verb's single positional stays a write; it carries no key into the join,
because a removal can never be the *writer* half of one.

## Isolated-revert probes

Each rule was reverted alone in a fresh copy of the package with `__pycache__`
cleared, and baseline and mutants were run **back to back in one window** so corpus
attrition could not be mistaken for a rule effect. Baseline
`counted_invocations` = 35,667.

| revert | what moves | suite |
|---|---|---|
| A1.7 (full alternation on the raw form) | counted 35,667 → 35,647; `placeholder_or_template` 2,255 → 2,275; targeted read 16 → 14; `other` 34,642 → 34,624; E0.4 denominators 16/52 → 14/50 | 1 failed |
| A1.8 (no arity rule) | `memory_write` 5 → 10; refused 7 → 0; bare-key 9 → 11; E0.1 unambiguous 4 → 9; **E0.4 widened hits 0 → 1** | 5 failed |
| A1.9 (no bare-key bucket) | `memory_write` 5 → 14; bare-key 9 → 0; E0.1 inner band high 1 → 10 | 5 failed |

The suite column is a second, independent probe: each rule was also reverted with
the *committed* test suite run against it, and the named test reds. A1.7 is pinned
by `test_the_raw_screen_does_not_judge_a_redirection_target`, A1.8 by
`test_a_rejected_write_never_supplies_a_prior_write_to_the_join` (and three bucket
cases), A1.9 by `test_a_bare_capture_bands_both_rates_and_is_inside_neither` (and
the band test). No rule in this round is asserted by a count alone.

The earlier rounds' probes still stand:

| revert | suite | the join test (`…attempted_read_cannot_manufacture_a_join_hit`) |
|---|---|---|
| A1.2 (redirections reach argv) | 8 failed | **green** |
| A1.1 (bare-key write rule returns) | 1 failed | **green** |
| A1.1 + A1.2 combined | 10 failed | **green** |
| A1.4 (undeclared-flag reads counted as writes) | 7 failed | **red** |
| A1.5 (placeholder screen off the raw argv) | 1 failed | green |
| A1.6 (join drop filed as an exclusion) | 1 failed | green |

**Correction to a claim made two rounds ago.** That round's report said the join
test "fails under either reverted defect". It does not. Against the code as it now
stands the test is green under A1.1, A1.2 and the combined revert, because A1.4
removes that invocation from the write side ahead of all three — and it reds under
the A1.4 revert. Every rule is still pinned by a test that reds when it is removed;
only the attribution was wider than the evidence.

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
2. **Read class split six ways** (see E0.2). The sealed profile had one read kind.
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
more often than they recall (291 searches to 16 keyed reads), attempt keyed reads
through the wrong verb in seven times more sessions than they issue them correctly
(36 to 5), and write between 4 and 14 times across 1.7M lines of transcript under
a standing instruction to write. No key is ever recovered across a session
boundary, and the one invocation that looked like it was reading back a key
someone had stored was reading back a capture that stored nothing.

That is the same shape arXiv 2607.20972 reports — voluntary memory use near zero
with a pre-seeded store, and harness-side deterministic delivery beating storage —
reproduced here on a much larger real corpus. It is the gap E0b (prime-delivery
share) and E1 (the guidance-strength ladder, per arXiv 2608.25198's monotone
call-rate dial) are designed to move.

Three limits on that GO. First, n is small where it matters most: 16 keyed
targeted reads is not a base rate anyone should power a study against, and a
downstream experiment must generate its own reads rather than sample these. A zero
over 16 rules out a *common* carry-over, not a rare one. Second, the corpus was
produced under the **shipped** `bd prime`, which violates R8 by emitting
`## Persistent Memories (N)` followed by full bodies — so the near-zero read rate
here is the read rate *when bodies are already being auto-injected*, a condition in
which an agent has little reason to issue an explicit read. E0b must synthesize the
R8-compliant prime surface harness-side; no experiment about R8 can be obtained by
wrapping the installed binary. Third, three rounds running, an apparent behaviour
in this corpus turned out to be a mis-parse of another: writing was mostly
attempted reading (A1.4), then partly refused grammar (A1.8), then partly
indistinguishable from reading (A1.9). Any downstream number keyed on a verb name
alone should be assumed to carry the same risk until it is checked against the
shipped binary's grammar, at every layer of that grammar and not just the first one
someone happened to look at.

## Reproducing

```
(cd results/memory-use/e0 && sha256sum -c \
    preregistration.sha256 \
    preregistration-amendment-1.sha256 \
    preregistration-amendment-2.sha256 \
    preregistration-amendment-3.sha256 \
    filelist.sha256)
(cd results/memory-use/e0/shipped-cli-help && sha256sum -c shipped-cli-help.sha256)
uv run python results/memory-use/e0/rates.py \
    --filelist results/memory-use/e0/filelist.txt --json
uv run pytest tests/test_e0_rates.py
```

All of these run from `memory-bench/`.

`filelist.txt` pins the population this report was computed from; re-enumerating
the live tree would yield a different, larger filelist and different counts.

The pinned filelist does **not** freeze the bytes behind it. It names paths in a
live tree, so a re-run can differ in two directions: already-pinned sessions keep
appending (`lines_scanned` grows, and post-lock invocations land in the drifting
exclusion), and pinned files can be deleted
(`files_in_filelist_no_longer_readable` grows, and every population count falls
with it). Across the three runs behind this report and the last two, unreadable
files rose 37 → 66 → 191 and every frozen count fell with them. See A1.10: this is
attrition, not a rule change, and no screen ordering prevents it.

---

# E0b — prime-delivery share on the hook surface

Bead `mem-h9pum`. Same pinned population, same preregistration lock, same
offline mechanical rules; a different surface. `injection.py`, artifact
`injection.json`, tests `tests/test_e0_injection.py`.

## Why E0.3 as specced could not size the R8 bet

E0.3 counted `bd prime` as one more bucket in the agent's own `Bash` traffic. On
this corpus that bucket holds **47** invocations. It was billed as the exact size
of what R8 proposes to remove, and it is not: it measures how often an agent
*typed* `bd prime`, which is nearly never, because prime is fired by a
**SessionStart hook**. Three specific defects, each answered by this pass:

| defect in E0.3 | what E0b does instead |
|---|---|
| the `tool_use`-only gate drops hook-fired prime | reads the host's `type: "attachment"` records (`attachment.type == "hook_success"`), which carry `hookEvent`, the exact `command`, and the hook's `stdout` |
| the payload that would show delivery was dropped at the `type != "tool_use"` skip | keeps the payload, and for the agent-typed form pairs each `bd prime` `tool_use` with its `tool_result` by id |
| whether memories were carried depends on store state and the `prime.max-memories` / `max-memory-chars` caps, none of which appear in argv | delivery is not inferred from argv at all — it is read off the emitted text's own structural markers |

## What was measured

**Delivery, not consumption.** A carried payload proves memory bodies were placed
in the agent's context. It cannot show they were read or acted on;
mechanism-FIRES is not mechanism-CONSUMED, and a delivery is one step past a
fire, not the whole distance.

Detection is format-anchored. The current build emits `## Persistent Memories
(N)` and the count is authoritative; an older build in the corpus emits an
uncounted `## Memories` heading followed by one `- **key**:` bullet per memory,
counted by bullets to the next heading. Neither is keyword matching: the
boilerplate names `bd remember` in prose in **every** payload, empty store or
not, which is precisely why the prose may not be evidence. No memory body is
inspected; judging whether an injected memory was useful is semantic and is not
done in this layer.

## Result

| | count |
|---|---|
| prime deliveries with a recoverable payload | **5,879** |
| carried at least one memory | **3,102** |
| carried none | 2,777 |
| undetermined (truncated, unrecoverable) | **0** |
| **delivery carry share** | **52.8%** |
| sessions with a prime delivery | 4,486 |
| sessions with at least one carried delivery | 2,420 (53.9%) |
| memories delivered, summed over deliveries | **132,491** |
| largest single payload | 99 memories |

One caveat on that last row's unit: the current build injects each memory's body
in full, while the older build truncates each to a preview line. Both are
delivery; only the current one is delivery of the *whole* body, so 132,491 counts
memories delivered, not full bodies delivered.

By surface: 4,873 `SessionStart:startup`, 870 `SessionStart:compact`, 70
`resume`, 23 `clear`, 1 `fork`, 12 through the compaction stdout surface, and
**30** typed by an agent. That last column is the one E0.3 could see.

Every payload resolved: 5,837 off the hook's own `stdout` and 42 inline, none
left undetermined. The host elides the inline copy of a large payload behind a
`<persisted-output>` banner, but keeps the complete `stdout` beside it, so the
elision never cost a verdict here. The unresolvable case is still handled and
still tested: it scores *undetermined*, never *not carried*, because defaulting a
truncation to not-carried would understate delivery by exactly the payloads large
enough to be elided — the ones most likely to be large because they carried
memories.

## Reconciling E0a's 47 typed primes against E0b's 30 typed deliveries

The two counts are not the same event, and the arithmetic between them ships in
`injection.json` under `agent_typed_prime_reconciliation`, not only here. E0a
counts an agent-typed `bd prime` **invocation** in Bash argv. E0b counts a typed
**delivery**, which additionally requires the paired `tool_result` to carry a
prime payload. On this run: **49** typed calls seen, **30** paired to a prime
payload, **4** paired to a result carrying no payload, **15** never paired to any
`tool_result` in the transcript. A call whose output never reached the transcript
is an invocation for E0a and not a delivery for E0b; the residual between 49 and
E0a's published 47 is population drift plus E0a's own help/placeholder screens,
not a disagreement about any individual record.

## The finding

**Agents received 132,491 memories across 3,102 carrying deliveries;
over the same corpus they issued 16 keyed targeted reads, wrote 11 unambiguously
keyed memories, and recovered a previously written key 0 times.**

Two ratios, and the sentence must say which one it quotes, because they are not
the same comparison:

| ratio | units | value |
|---|---|---|
| memories delivered : keyed targeted reads | delivered **items** vs read **invocations** | 132,491 : 16 = 8,281x (**3.9 OOM**) |
| prime deliveries : keyed targeted reads | **invocations** on both sides | 5,879 : 16 = 367x (**2.6 OOM**) |

The like-for-like comparison is the second: **deliberate keyed retrieval is
outnumbered by automatic delivery by about 2.6 orders of magnitude.** The first
row is larger only because a single delivery carries ~43 memories on average, and
it is the right number when the question is how much *material* arrives without
being asked for. Both inputs are published; neither ratio may be quoted without
its units.

Practically all of what an agent in this corpus "knew" from the memory store
arrived by **delivery**, not by **choice** — reproducing arXiv 2607.20972's
result (harness delivery beats storage; voluntary use near zero) on a corpus
three orders of magnitude larger than that paper's.

Both standing labels are part of this finding, not a footnote to it. **Every rate
here is INSTRUCTED-endogenous**, never spontaneous, and **the 52.8% carry share is
DELIVERY, not consumption** — it proves bodies were placed in the agent's context
and says nothing about whether any of them were read. Both ship in
`injection.json` as `interpretation_label`, `r8_label` and `measured_quantity`.
The full statement of each is below.

That is the size of the R8 bet, stated in the units R8 acts on: R8 proposes to
stop auto-loading bodies, so it removes ~43 memories per carrying prime
(mean, 132,491 / 3,102) and asks agent choice to replace them, from a measured
choice rate of essentially zero. E0a's near-zero read rate was measured *inside* that delivered
context; it is not evidence agents would not recall if nothing were delivered,
and it is not evidence they would.

## Two labels this number may not travel without

1. **Instructed-endogenous.** Same standing instruction as E0a.
2. **R8-violating source.** This share was produced by the shipped `bd prime`,
   which auto-injects bodies. R8 asks for no automatic body loading. The share
   therefore sizes what R8 would *remove*; it is not a measurement of an
   R8-conformant surface.

**The R8-conformant counterfactual cannot be measured from historical transcripts
at all.** No prime payload in this corpus was emitted by a binary that withholds
bodies, because no such binary was ever installed here — 5,879 of 5,879
deliveries come from the auto-injecting build. So the read rate under
guidance-only prime has no observational estimate, at any corpus size. E1's
guidance-strength ladder must **synthesize** the prime surface harness-side and
generate its own reads; shelling out to the installed `bd` would silently pin
every rung to the R8-violating condition. That constraint is a result of E0b, not
an implementation preference.

## Reproducing

```
uv run python results/memory-use/e0/injection.py \
    --filelist results/memory-use/e0/filelist.txt --json
uv run pytest tests/test_e0_injection.py
```

`injection.json` publishes the aggregates, including the session-level ones. The
full 4,486-row per-session map is reproducible but not committed — it is 704KB of
artifact for one acceptance criterion — so add `--per-session` to obtain it:

```
uv run python results/memory-use/e0/injection.py \
    --filelist results/memory-use/e0/filelist.txt --per-session --json
```

Both run from `memory-bench/`. The same live-tree caveat as E0a applies: the
filelist pins paths, not bytes, so population lines can move between runs.
