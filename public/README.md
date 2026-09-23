# membench public benchmark records

This directory holds the published form of the benchmark: a JSON Schema, a
standalone validator, and the exported corpus. Everything here is meant to be
usable without a checkout of this repository and without installing `membench`.

```
public/
  README.md                            this document
  SHA256SUMS                           digests of every file below it
  schema/bench-record.v3.schema.json   the published contract
  validator/membench_validate.py       the standalone checker (stdlib + jsonschema)
  data/*.jsonl                         one file per origin and tier
  data/SHA256SUMS                      coreutils-format digests of the JSONL files
```

Two digest files ship, and neither replaces the other. `data/SHA256SUMS` covers the
two corpus files, for a consumer who took the corpus alone. The seal at the root
covers every published file, this document, the schema and the validator included,
with paths relative to `public/`, and it covers `data/SHA256SUMS` as well, so the
two cannot drift apart without one of them saying so.

## The record

One record is one memory-dependent question, with the memories an arm is allowed
to see while answering it and the values the answer is graded on. Every record in
every file has the same shape. `origin` is the only field that says where it came
from: `synthetic` for a record materialized from a frozen generated world, `real`
for a record projected from the work-audit graph. The two origins are never
written into the same file, and the exporter raises rather than pooling them.

Required top-level fields:

| field | what it carries |
| --- | --- |
| `schema_version` | always `bench-record.v3` |
| `record_id` | stable id, bead-id shaped, flows into no path |
| `origin` | `synthetic` or `real` |
| `tier` | `prompt`, `session` or `project` |
| `question` | the request put to the agent, the instant it is asked, and the scope the answer must span |
| `evidence` | `gold` and `gold_ids`, `distractors`, `superseded` and `superseded_ids`, each bucket id to text |
| `candidate_pool` | every id an arm may see for this record, in the order it must be seeded in |
| `answer` | `expected_values`, `forbidden_values`, and whether text or a tool call is graded |
| `necessity` | the memory-necessity gate verdict, including the agent that produced it |
| `loo` | the leave-one-out boundary, the excluded ids, the axes that fired, and enough of the candidates to recompute all of it |
| `provenance` | generator and exporter version, seed, world, sequence, the ids of the gold memories another sequence wrote, and a digest of the source object |
| `leak_guard` | which validator cleared the record and over which field names |

The schema sets `additionalProperties: false` at the top level and inside every
object, so a published record cannot carry `pr`, `commit_sha`, `base_commit`,
`repo`, `jsonl_path` or raw transcript text by construction. The validator scans
for those names again at any depth, because a schema keyword only constrains the
shapes it names.

Evidence is raw text, keyed by memory id. It is never a graph. A consumer that
wants a graph builds one; a consumer that wants to feed the text to a retriever
can do so with no traversal code at all.

## What a downloader can do with it

The release ships the answer key. Running an arm over this corpus and grading the
result takes nothing from us: no server, no withheld split, no submission. The
corpus is anonymized in one respect only, which is that a published id does not
say what role its memory plays.

- **Build the pool.** `candidate_pool.ids` is the ordered list an arm ranks over,
  and the text for every one of those ids is in `evidence.gold`,
  `evidence.distractors` or `evidence.superseded`. The three buckets union to the
  pool exactly, with nothing left over and nothing missing. The list order is
  normative, and the pool-order section below says what it is and why a run that
  ignores it is not a run on this benchmark.
- **Recompute the cut.** `loo.candidates` carries a close instant and four equality
  tokens for every id that was considered, and `loo.query` carries those same four
  tokens for the work being asked about, so `loo.excluded_ids`,
  `loo.exclusion_axes` and `candidate_pool.ids` are all re-derivable from
  published fields. The validator does exactly that on every line rather than
  trusting the producer.
- **Grade a run.** `answer.expected_values` is the set of values a correct answer
  states and `answer.forbidden_values` is the set that zeroes it. The rule is
  word-boundary matching, the same one the leak guard uses.
- **Score retrieval directly.** `evidence.gold_ids` is the answer key for
  retrieval, and `evidence.superseded_ids` is the trap set, so recall, precision
  and stale-injection rate are all computable per record without running a model.

Anonymization removes the ability to read a candidate's role off its id string.
It removes neither the answer key nor the pool.

### What to withhold from an arm under test

A record holds both halves, the task and its key, so whoever runs the benchmark
is the one who has to keep them apart. Hand an arm the question and the pool, and
hold back every field that answers what the run is measuring.

Give the arm `question.text`, `question.asked_at`, `candidate_pool.ids`, and the
candidate texts as one flat id-to-text mapping.

Build that mapping **in `candidate_pool.ids` order**. Read the ids off that list,
in the order it lists them, and look each one up in whichever of `evidence.gold`,
`evidence.distractors` and `evidence.superseded` holds it. Do not iterate the
three buckets and merge them: `{**gold, **distractors, **superseded}` is a
mapping whose first entries are the gold set in every record, and any policy that
favors early entries then scores a perfect ceiling off the merge itself.
Measured on this release, taking the first 6 entries of that merge and answering
with them recalls 1.000 of the gold, earns reward 1.000, and passes 160 records of
160. The same first-6 policy over `candidate_pool.ids` order recalls 0.245 against
a chance 0.242, earns reward 0.154, and passes 0 of 160 against a chance of 0.02.

Chance there is the pass rate of a uniformly drawn 6-id head, and it is not the
probability that the head happens to be the gold set. A head that carries all the
gold and one superseded candidate states a forbidden value, which zeroes the
answer, so the count runs over the candidates that state nothing forbidden: 0.0180
records of 160, against the 0.0201 the naive count reads. The gap is narrow on this
release because a gold set of five leaves a six-wide head one slot to go wrong in;
on the release before it, whose gold sets were narrower and left the head more
room, the same two numbers differed by about a factor of two. The correction is
the grader's rule rather than a size of effect, and it runs in the one direction
that flatters us:
counting the heads that hold the gold and ignoring the ones a stale neighbour
zeroes makes the published order look further below chance than it is.

The order is part of the contract, not a formatting detail. `candidate_pool.ids`
is a per-record permutation, and the validator recomputes it and rejects a record
whose pool is in any other order, so a run seeded in a different order is not a
run on this benchmark. The [pool order](#the-pool-order-is-part-of-the-record)
section below gives the derivation and the number that made it necessary.

Withhold these:

- `evidence.gold_ids` and `evidence.gold`. This is the retrieval answer key. An
  arm that sees it is measuring nothing, which is why the one arm we give it to
  is the oracle ceiling.
- The bucket partition itself. The arm gets one mapping, never the three. Passing
  the three separately labels every candidate by role even when the ids do not,
  and so does merging them in bucket order.
- `evidence.superseded_ids`. The trap set names the stale candidates, and
  avoiding stale candidates is the thing being scored.
- `answer.expected_values` and `answer.forbidden_values`. The first is the answer
  to the question. The second names the subjects the stale texts are about.
- `provenance.cross_session_gold_ids`. It names which of the gold memories another
  session wrote, so it is a labelled subset of the retrieval key and it says where
  to look for the rest.

Our own driver, `memory-bench/scripts/run_public_baseline.py`, works this way:
every ranking arm is seeded with the pool in `candidate_pool.ids` order, unlabeled,
and queried on `question.text`, and `gold_ids` reaches exactly one arm, the oracle.

### The pool order is part of the record

`candidate_pool.ids` ships in one specific order and a conforming runner seeds in
it. The order is

    sort ascending by HMAC-SHA256(key=record_id, message=id).hexdigest(),
    ties broken by the id string

over the record's own `record_id` and its own ids, both published. It takes no key
material, so a downloader recomputes it from the file in front of them, and
`validator/membench_validate.py` does exactly that before it will accept a record.

Every order says something, and the two obvious ones say too much. Bucket order
hands over the whole answer key, as the numbers above show. Sorted-by-id order
looks neutral and is not: an id is a keyed alias, so it is one fixed pseudorandom
draw per memory, and the same draw returns in every record that carries that
memory. A few low-sorting memories that happen to be gold are then counted once
per record they appear in, and the corpus inherits the accident. On the release
where that was found, the lowest id in the pool was gold in 47 records of 160.

Whether the accident shows is itself a draw, and this release is the demonstration.
It was re-minted, and the same alias rule over the new id space puts gold first in
35 records of 160 (0.2188) against a per-record chance of 0.2017, p = 0.3191 over
20,000 permutation draws; per tier that is p = 0.8496 in the session half and
p = 0.0668 in the project half. A clean figure is not a fixed order, because the
next mint is another draw of the same accident. Keying the draw by the record id is
what removes the sharing that produces it: a memory gets an independent position in
every record it appears in, so no one memory can accumulate at all, whatever the
mint. The published order reads 41 in 160 (0.2562) at p = 0.0546; per tier that is
p = 0.0430 in the session half and p = 0.3338 in the project half. Across all 800
published gold entries, the mean normalized gold rank under the published order is
0.4950 against a uniform 0.5000, at p = 0.6152.

The session half's 0.0430 is the lowest of those six figures and it ships as it
reads. The acceptance bound is 0.01, one figure in six landing near 0.05 is what a
finite sample of 80 records looks like, and a re-draw that crosses the bound is a
release to re-order rather than a bound to widen.

`memory-bench/tests/test_public_corpus_contract.py` runs that permutation test
corpus-wide and per tier on every release. It scores the bound against a witness it
builds rather than against the order this release replaced: the bucket merge leads
with gold on 160 records of 160 by construction and has to fail the bound, which is
a property of the order rather than of the draw.

### Why the ids are opaque

The first export of this corpus keyed published memories by an id the generator
hashed from `(namespace, label)`. That hides the label from a reader but not from
a searcher. The namespace is a sequence or world id, both published in the
record's provenance, and the label vocabulary is small, fixed and readable in the
generator. Recomputing the hash for every published namespace crossed with every
candidate label, and matching the results against the published id space,
recovers the role of every published id: which candidate was the gold fact, which
was the distractor, which was the stale version. Rerun that brute force over the
corpus shipped here, against the internal ids the first export would have given
it: 208 published namespaces crossed with 230 label spellings is 47840 guesses,
and they recover 3891 of the 6494 memory ids the frozen worlds hold. That is every
one of the 3877 a published record can show, each one labelled by the guess that
produced it: 780 gold, 2777 distractor, 320 stale. The candidate pool was telling a
solver the answer.

A published id is now an alias: `k-` followed by 16 hex characters of an HMAC
over the internal id, keyed by a mint seed held outside the published tree. The
alias is not a function of the label, the namespace, or anything else an attacker
can enumerate. The same brute force run against this release names 0 of the 3877
memory ids it publishes. Two properties follow from a keyed hash rather than a draw from
a seeded shuffle: an alias depends only on its own internal id, so adding a
sequence to a corpus never moves an existing id's alias, and re-minting the same
corpus under the same seed is byte-identical.

The published corpus ships no mint file and no seed. Ids are stable across
releases of the same corpus, which is what makes a retrieval result from one
release comparable with another.

### What the opacity rests on

The alias scheme protects this corpus for exactly as long as the mint stays off
every remote, and it rests on nothing else. The repository that holds the
generator, the frozen worlds and this release is itself public. The map from
alias to internal id, and the seed that generated it, live in one file at
`memory-bench/fixtures/mint/`, which is git-ignored, never committed, and covered
by a content-matching test that refuses to let a seed literal or an
alias-to-internal-id pair reach the publish set under any filename. Nothing in
this directory carries either one. Anyone holding that file can label every
published candidate as gold, distractor or stale, and can reconstruct the
distractor interleave.

If the mint leaks, this corpus's id anonymity is void and the corpus has to be
re-minted under a fresh seed, which renumbers every published id and breaks
comparability with the release already out. Treat the opacity as worth exactly as
much as the secrecy of that one file.

## The leave-one-out block is recomputable

`loo` publishes the cut rather than asserting it. `loo.candidates` carries every
id that was considered, with its close instant and four equality tokens for the
same-work axes. The query is one of those entries, carrying a null instant because
it never closed. `loo.query` carries the same four tokens for the work being asked
about. From those two a consumer re-derives `excluded_ids`, `exclusion_axes` and
`candidate_pool.ids` without trusting the producer, and the validator does
exactly that on every line.

The rule has two parts. The temporal cut is strict: a candidate is eligible only
when it closed strictly before `loo.boundary`, and a candidate that never closed
is never eligible. The five same-work axes are `convoy`, `pr`, `external_ref`,
`epic_parent` and `child`, and each fires only when the query names a value on
that axis, so two nulls never match. The query record itself is excluded on the
`self` axis.

`convoy_key` and `parent_key` are work ids in the clear, because the
`epic_parent` and `child` axes compare a parent against a record id directly, and
record ids are published anyway. `pr_key` and `external_ref_key` are only ever
compared for equality, so the raw value is never published: a real record carries
a salted digest, and a synthetic record carries null. That keeps a pull-request
number and a branch name out of the corpus while leaving both axes checkable.

### What the timeline does and does not say

A synthetic record carries a synthetic clock. A generated sequence has an order
but no timestamps, and the leave-one-out block needs instants, so candidates are
laid on a grid: slot *k* is `2020-01-01T00:00:00Z` plus *k* minutes, and the
boundary is one slot past the last. The epoch is deliberately a round, obviously
synthetic date. These are positions in a sequence, not a claim about when
anything happened.

One candidate occupies each slot, so every close instant in a record is distinct.
The order of the grid is a uniform random permutation of the pool. The published
timeline is positional, not evidential: it says a record's candidates are
distinguishable and orderable, and it says nothing about which of them is current.

That is the third thing this order has been. The first release filled the grid
from slot zero forwards along the supersession partial order, taking a uniform
pick each time among the candidates whose predecessors were already placed. A
chain's final version cannot be placed until the two before it are, so it drifted
toward the late slots, and "whichever candidate closed last" landed on a gold fact
0.700 of the time in the session tier and 0.725 in the project tier, against base
rates of 0.375 and 0.400.

The release after that filled the grid from the last slot backwards, which fixed
the end an attacker reads first and left the other end standing. A superseded
version still closed before the version replacing it, so the earliest slot was
stale on 64 records of 160 against an expected 19.7, and inside a chain's own
subject group the latest-closing member was that subject's current value on 45 of
160 against 26.7. Both are answer keys: one names the trap set, the other names
the answer, and neither costs a retrieval.

A partial order cannot be half published. This release draws the whole timeline
uniformly, so a value that was replaced is as likely to be dated after its
replacement as before it, and every rate above sits at chance. Measured on the
release: the earliest candidate is stale on 16 records of 160 against an expected
12.91 (p = 0.3813), the latest is gold on 36 against 32.27 (p = 0.4898), and the
latest-closing member of a chain's group is that group's gold on 34 against 33.65
(p = 0.9226). By tier, P(gold | closed last) is 0.1750 against a 0.2028 base rate
in the session tier and 0.2750 against 0.2005 in the project tier. That project
figure is the widest gap any of these cuts opens, and an exact Poisson-binomial
null still places it at p = 0.1226.

That costs the corpus something. Supersession is the ordering the task depends
on, and the published clock no longer carries it, so an arm cannot reach the
current value by asking which value is newest. It has to read the texts. An
answer that hedges by listing a chain's old and new value states a forbidden
value and scores zero, and the timeline will not sort the two for you. Answering
with each subject's latest-closing candidate scores 0.1250 in the project half and
0.1350 in the session half, 80 records each, and 0.1300 pooled; it states a
superseded value on 66 records of 160, which is the rate these pools imply (67.30
expected, p = 0.8716). The pooled score is an average over a mixture and bounds
neither half, so read the half you intend to run. Under the previous order that
same policy was zeroed on none of the 160: the trap was in the data and
unreachable in practice.

A published candidate carries no supersession edges, because those edges gave
the answer away. `bench-record.v1` put a `supersedes` tuple on every candidate,
and the ids a candidate supersedes are exactly the stale ones. Restore those
edges over this release and subtract every id they name: the survivors keep every
gold id on 160 records out of 160 and drop every stale id on 160 out of 160,
without reading a single text. The trap set comes free. The pool it leaves is
wide enough now that precision only moves from a chance 0.2017 to 0.2195, but
handing over the whole trap set is a leak whatever it does to precision.

With the edges gone, reconstructing the gold set from the published timeline
alone is at chance. Taking the *k* latest-closing candidates, *k* being the gold
size the record states, recovers the exact gold set on 0 records in 160 against a
closed-form chance rate of 0.0034 in 160, and its mean precision is 0.2038 against
a chance 0.2017.

## The three evidence buckets

`gold` holds the memories that answer the question. `distractors` hold plausible
memories about other subjects. `superseded` holds the stale versions of the gold
subjects: earlier values of the same fact, each one a member of
`answer.forbidden_values`.

Those stale texts ship. Shipping them is what makes retrieval quality observable
at all, because surfacing one costs the whole record: grading zeroes an answer
that states any forbidden value, so an arm that hands the agent the entire pool
hands it both the current and the stale value of the same subject. Measured on
this release, a policy that copies every candidate in the pool into its answer
passes 0 of 160 records at a mean reward of 0.0000, and the same policy
restricted to `evidence.gold` passes 160 of 160. Every one of the 160 records
carries at least one forbidden value, so no record is missing its trap. Without
the stale bucket the pool would hold no penalty for over-retrieval, and an arm
that retrieved nothing useful would score like one that retrieved well.

`candidate_pool.ids` is the union of the three buckets and nothing else, in the
normative order, and the validator checks both rather than assuming either.

## The two tiers in this corpus

Tier is the memory scope the goal spans, and it is a property of the sequence the
record was materialized from.

- `prompt`: the goal is answerable from the step request and environment alone.
  No record of this tier is published here, because such a record does not
  require memory and the necessity gate rejects it.
- `session`: the goal needs values established by earlier steps of the same
  sequence. The earlier steps are the establishing steps; the goal step is the
  query.
- `project`: the goal additionally needs a memory written by a different sequence
  in the same world, which is what makes it cross-session.

The project tier is enforced, not declared. A project-tier sequence whose goal
its own steps fully answered is skipped rather than published under a tier it
does not satisfy. `provenance.cross_session_gold_ids` names which of a published
record's gold memories another sequence of the same world wrote. Across the 80
project records it names one on 57, two on 18 and three on 5; on a session record
it is empty. Re-deriving that set from the frozen worlds, without reading the
field, agrees on all 80.

It ships as ids because it used to ship as a count, and the count was arithmetic. A
goal graded a fixed budget of local subjects and then whatever shared decisions it
needed on top, so the gold set grew with the count and the two sat one subtraction
apart on every record of that release: the field a runner is told to withhold was
recoverable from a field it is handed. The budget now covers the whole graded set,
shared decisions included, so the gold set is five wide on all 160 records and no
arithmetic over it says anything about the split.

Ids also give a downloader something to check, which a count did not. The validator
holds the set to what one record fixes: it is a subset of `evidence.gold_ids`, in
the alias order every published id list uses; it agrees with the tier, so a project
record with an empty set and a session record with a non-empty one are both
refused; it leaves at least two of the gold local, which the construction fixes;
and it never names the subject the record publishes a supersession chain for,
because a chain is written version by version inside one sequence. A second layer
reads the records of a world against each other. A memory two sequences both need
is minted once at world scope and publishes under one alias in both of their
records, so a repeated alias is a proof of cross-session authorship, and a gold
declared session-local that turns up in a sibling record contradicts the release.

What neither layer reaches is an arbitrary id, and the reason is structural rather
than incidental: a non-repeating alias proves nothing either way. The world may
have shared that memory with a sequence this release does not publish, or with one
whose goal never asked for it. On this release 40 of the 108 cross-session golds
repeat and 68 do not, and no session-local gold repeats at all, so the signal is
exact where it exists and simply absent elsewhere. What that leaves is measured
rather than assumed: moving one gold across the boundary, the tamper an inflated
count used to hide behind, is caught on 199 of the 292 promotions available in this
release (0.6815) and 80 of the 108 demotions (0.7407), and a move is caught exactly
when the release contradicts it somewhere. The misses are the moves nothing
published contradicts, and the digests are what separate a release from an edited
copy of it: `data/SHA256SUMS` over the two corpus files, and the seal at the root
over every published file, the validator a downloader executes included.

The set varies because the subject does. A world draws two or three decisions
to share across its sequences, from the same ten subjects a local fact can be
about, and each later sequence depends on a different subset of them. In the
first release the shared decision was the project charter in all 40 worlds, both
records of a world resolved to the same single memory, and one learned fetch
answered the cross-session half of the entire tier.

Two different questions have to come out flat, and only one of them did at first.
The charter's share of all cross-session facts fell to 0.118, well inside any
share bound, while the conditional ran the other way: every question about the
charter was cross-session, all 13 of them, because the charter was reachable only
as a shared decision and a shared decision is by construction established by an
earlier task. An arm that answered "the charter clause is answered from an earlier
session" was right every time the subject came up, having retrieved nothing. The
charter is an ordinary subject now, drawn for local facts like any other, and it
carries 9 of the 108 cross-session facts this release publishes. No subject carries
more than 0.130 of them and the lightest carries 0.065, while P(cross-session |
subject) runs from 0.175 to 0.412 around a pooled 0.270, a spread a permutation of
the same flags produces at p = 0.2974. No two records of a world share a
cross-session set, and the size of that set is not a constant an arm can assume.

The currently published corpus is synthetic only:

| file | records | pool | gold | distractors | superseded |
| --- | --- | --- | --- | --- | --- |
| `data/synthetic-session.jsonl` | 80 | 20 to 29 | 5 | 13 to 22 | 2 |
| `data/synthetic-project.jsonl` | 80 | 21 to 30 | 5 | 14 to 23 | 2 |

A pool is built subject by subject. Every subject the question asks about
publishes a group of four, five or six candidates, drawn uniformly and
independently, holding that subject's current value and wrong values about the
same subject; one subject's group holds two stale versions of the current value
instead of two of those wrong ones. Both tiers grade five subjects, so a pool is
the sum of five such draws and runs 20 to 30 by construction; this release draws 20
to 29 in the session half and 21 to 30 in the project half. A session record's five
subjects are all established by earlier steps of its own sequence. A project
record's five are the one to three its world shared plus the two to four its own
sequence established, which is the only structural difference between the halves:
the same gold width and the same pool, a different authorship.

The group size is drawn rather than fixed because a fixed count per subject makes
the size a function of the role. With three wrong values everywhere, the subject
carrying the supersession chain published 3 + 3 = 6 candidates and every other
published 1 + 3 = 4. Grouping the pool by subject, which every published text
invites since each names its own subject, named the chain on 160 records of 160
with no id, timestamp or bucket label read. Drawing the whole group size and
deriving the distractor count as what is left over closes it: over the release
the two conditional size distributions sit 0.083 apart in total variation, which
a permutation of the labels produces at p = 0.120.

The pooled figure is an average over a mixture and is not a bound on either half.
Total variation compares two pooled distributions, so a residual in the project
half and an opposite one in the session half cancel in the sum, and the pooled
distance can sit below both. The halves are separate draws besides, 8 worlds of 10
records against 40 worlds of 2, and they ship as separate files a downloader can
run one at a time. Each half is therefore measured on its own, and
reads 0.088 in the project half and 0.078 in the session half.

Every record excludes exactly one candidate, the query itself, on the `self` and
`temporal` axes together: the query record never closed, so it fails the strict
temporal cut as well. The five same-work axes are published and checked but do
not fire in a synthetic corpus, where those keys are null.

### Choosing a retrieval width

Three constraints bound the width over these pools. A width below the gold set (5)
caps every arm below a pass by arithmetic rather than by retrieval. A width at or
above the smallest pool (20) makes "retrieve everything" and "retrieve well" the
same policy, which the stale traps then fail. Leaving half the pool behind, so that
choosing still costs something, requires twice the width to fit inside the smallest
pool, which caps it at 10. Any width from 5 to 10 satisfies all three, so the pools
no longer pin one: our own baseline runs every ranking arm at 6, the width the
previous and narrower pools pinned at equality, held there so retrieval numbers
stay comparable across releases. A run at another width in that band is still a run
on this benchmark, as long as it reports the width it used.

## The memory-necessity gate

A task an agent can solve without memory measures nothing about memory. Every
candidate sequence is run twice before publication, once with the oracle memory
and once with none, and it is admitted only when the oracle beats no-memory by
more than `epsilon`. The verdict travels with the record in `necessity`, so a
consumer can see the two rewards, the delta, the epsilon, and the reason.

The verdict is published with its working shown, so the validator redoes the
arithmetic: `delta` has to be `oracle_reward` minus `no_memory_reward`, and
`accepted` has to be what that delta and that `epsilon` imply. A record whose
verdict was flipped, or whose delta had its sign turned around, contradicts the two
rewards it ships beside them and is refused.

For this corpus: `epsilon` 0.05, 200 candidate sequences gated, 200 admitted,
none rejected, `rejection_rate` 0.0000. That rate is the share of candidates the
gate refused out of the candidates it was handed, and nothing more. A zero says
this generator configuration produced no goal the scripted no-memory arm could
answer. It is not evidence that the gate bites, and a corpus is not more valid
for having a low rate. What shows the gate can refuse is a test in this
repository that feeds it a sequence answerable without memory and requires a
rejection whose reason names the cause.

Read the margin before reading the rate. On this corpus all 200 candidates sit
at delta exactly 1.0000 (oracle 1.0000 against no-memory 0.0000) with epsilon
0.0500, so the closest candidate clears the threshold by 0.9500. Nothing came
near the boundary, no candidate was a near miss, and `epsilon` set anywhere below
1.0 would have admitted the same 200. So `rejection_rate` here reports that the
construction separates completely under the reference agent; it reports nothing
about where the threshold sits, because the threshold was never consulted on a
close case. A corpus whose deltas spread out is the one whose rate would carry
information about `epsilon`, and the gate now prints this distribution beside the
rate on every run so the two are never read apart.

Of the 200 admitted, 160 are published. The other 40 are the first sequence of
each project-tier world. That sequence establishes the memories its siblings
need and then answers its own goal inside its own session, so a session-scoped
retriever would score it, and publishing it under the project tier would
overstate what the tier measures. The exporter reports every one of those skips
by sequence id with that reason, so the drop is accounted rather than silent.

The agent that produced those rewards is recorded as `reference_agent`, and here
it is `scripted-ref`, a deterministic `ScriptedAgent`. No model is called at any
point in the gate. A ScriptedAgent answers by reading the memories it was given,
so a delta it produces shows that the record is constructed correctly: the gold
memories carry the answer, and the answer is not recoverable from the question
alone. Treat the rewards as a construction-integrity signal. They say nothing
about any real agent, which can carry world knowledge, guess a plausible default,
or infer the value from surrounding prose, none of which appears in a scripted
no-memory run. A necessity claim about a real agent needs the same two conditions
run with that agent.

## The leak guard

Two mechanical checks run over every record before it is written, and again in
the standalone validator.

The field scan walks the record to any depth and fails on a key named `pr`,
`commit_sha`, `base_commit`, `repo` or `jsonl_path`. The first three mirror
`membench.grading.leak_guard.IDENTIFYING_KEYS`, and a test in this repository
fails if the vendored copy drifts from the original.

The answer scan collects everything an agent reads before it retrieves, meaning
the question text, the distractor texts and the superseded texts, and fails if
any of them states a value from `answer.expected_values`. "States" here is the
word-boundary match the grader itself uses (`metrics.scorers.states_value`), not
a substring match. That choice is deliberate and it matters: a case-insensitive
substring scan rejected the authored prompt "the checkout_v2 feature flag state"
because `v2` is the gold value of an unrelated subject in the same world. Using
the grading rule makes the guard and the score agree about what counts as stating
a value.

The question and the distractors are scanned for expected and forbidden values
alike. The superseded texts are scanned for expected values only: a stale text
states a forbidden value by construction, since being the wrong answer in the
pool is its whole job, and scanning it for those would refuse every well-formed
record. A stale text that stated the current answer would be handing it over, and
that is still a failure.

What the guard cannot catch:

- A paraphrase. The scan compares exact values. A distractor that says "roughly a
  month" next to a gold value of "30 days" passes.
- A truncated or reformatted identifier. The field scan matches names, and the
  value scan matches whole tokens. A seven-character SHA prefix embedded in prose
  under an allowed field name passes both.
- Semantic inference. Text that makes the answer deducible without containing it
  passes. Nothing in this layer judges meaning.
- Anything in a field it does not read. The answer scan covers the question and
  the pre-retrieval texts. A leak placed in `provenance.world_id` would be caught
  by the schema only if it broke a pattern.

The guard is a mechanical floor, not a proof that a record is memory-dependent.
The necessity gate is the check that a record needs memory at all.

## Running the validator

Check the digests first. The validator is itself a published file and the seal at
the root covers it, so verify that before running anything out of this tree:

```bash
cd public && sha256sum -c SHA256SUMS
```

A consumer who took the two corpus files and nothing else checks those alone:

```bash
cd public/data && sha256sum -c SHA256SUMS
```

The validator depends on the standard library plus `jsonschema`. It does not
import `membench`, and it cannot: importing even a leaf module such as
`membench.grading.leak_guard` executes the package `__init__`, which pulls in
pydantic and the OpenTelemetry SDK. The parts it needs are vendored, and a parity
test in this repository pins them to the originals.

```bash
pip install jsonschema
python3 public/validator/membench_validate.py public/data/*.jsonl
```

It exits 0 when every record passes and 1 otherwise, printing one line per file
and one line per failing record. It resolves the schema next to itself; pass
`--schema PATH` or set `MEMBENCH_PUBLIC_SCHEMA` to point somewhere else.

If `jsonschema` is not installed, the validator raises `MissingValidatorError`
and exits non-zero. It does not skip. A gate that reports success for files it
never checked is worse than no gate.

## Producing the corpus

The rest of this document describes the producer's tree rather than the release.
Nothing below is needed to use the corpus; it is here so a reader can see what
the published bytes came out of, and so anyone holding the generator and the mint
seed can rebuild them.

The frozen corpus is 48 worlds, generated offline so no model is called and the
bytes are reproducible. From `memory-bench/`:

```bash
# 8 session-tier worlds, seeds 0 to 7, 10 sequences each
for seed in $(seq 0 7); do
  PYTHONPATH=. python3 scripts/generate_worlds.py --tier session --offline \
      --seed "$seed" --personas 4 --tasks 10 --facts 5 --out fixtures/worlds-public-v1
done

# 40 project-tier worlds, seeds 100 to 139, 3 sequences each
for seed in $(seq 100 139); do
  PYTHONPATH=. python3 scripts/generate_worlds.py --tier project --offline \
      --seed "$seed" --personas 4 --tasks 3 --facts 5 --out fixtures/worlds-public-v1
done
```

Each world writes its own manifest, and `scripts/verify_worlds.py
fixtures/worlds-public-v1` re-materializes all 48 against those manifests and
fails on any drift. Then gate, mint and export:

```bash
PYTHONPATH=. python3 -m membench.cli gate-corpus fixtures/worlds-public-v1
PYTHONPATH=. python3 scripts/mint_public_ids.py \
    --corpus fixtures/worlds-public-v1 --mint-seed "$MEMBENCH_MINT_SEED"
```

Minting is keyed, so re-minting the same corpus under the same seed reproduces
the same id space byte for byte, and a corpus regenerated from the same seeds
re-exports to the same digests.

## Producing the export

From `memory-bench/`, over a corpus that has already been gated and minted:

```bash
PYTHONPATH=. python3 -m membench.cli export-public fixtures/worlds-public-v1 --out ../public/data
```

The exporter writes one JSONL per origin and tier, plus `SHA256SUMS`. It refuses
to write two origins or two tiers into one file, refuses a corpus with no
necessity artifact, refuses a corpus with no mint, refuses a world that does not
reproduce its own manifest, and refuses any record that fails either leak check.
Each refusal is a raise, and nothing is written. Sequences it declines to publish
on the cross-session rule are reported by id rather than dropped quietly.

The export covers `data/` and stops there, so sealing the release is its own last
step:

```bash
PYTHONPATH=. python3 scripts/seal_public_release.py
```

That writes `public/SHA256SUMS` over every published file, this document, the
schema and the validator included. It refuses a tree carrying anything that is
not published source, so a `__pycache__` directory left behind by a test that
imported the validator by path fails the seal rather than shipping inside it.
`--check` verifies an existing seal instead of writing one and exits 1 on drift,
which is what a release edited after its last seal looks like. Seal after the
export, and after any edit to this document, or the seal names bytes the release
no longer carries.
