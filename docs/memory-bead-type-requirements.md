# Requirements for a Memory bead type as a general agentic-memory substrate

**Status:** draft, for review · **Date:** 2026-09-07 · **Bead:** mem-4e405 · **Upstream:** gastownhall/beads#5877

## The question

Beads is a typed-node graph store. Issues are one node kind; Memory would be
another. So the design question is not what a task tracker owes a memory system.
It is narrower and more answerable:

> What must the Memory node kind guarantee so that an arbitrary agentic memory
> system can consume it as its knowledge substrate, instead of carrying its own
> store?

The consumers to design against are the ones that already exist: mem0's
OpenMemory MCP, Letta, Zep/Graphiti, LangMem, the Anthropic memory tool, and our
own membench arms. Each currently ships its own storage layer. A Memory bead type
earns its place only if a working memory system could be re-pointed at it and
lose nothing it depends on.

## What this is derived from

Three sources, all already in hand. Where a requirement rests on only one, that
is said.

1. **mem's own store schema** (`src/store/schema.ts`, version 11). This is a
   memory system that already sits on a bead spine and has been rebuilt enough
   times to have learned what it cannot afford to lose. Requirements derived
   from it are one implementation's experience, not a survey.
2. **The competing-systems adoption survey**, 2026-09-06, covering the five
   projects named above.
3. **Campaign measurements** from the E0/E1 experiment series and the
   capture-fidelity work.

## A. Identity and content

**A1. The content is the record. Every queryable field is a rebuilt projection
of it.**

mem's store holds the full validated record as JSON and rebuilds every indexed
column and child table from that JSON on each upsert, so a projection cannot
drift from the thing it projects. A consumer that wants its own index (an
embedding, a different tokenizer, a domain-specific field) must be able to build
it from content alone.

*Consequence:* no field may be authoritative only in its projection. A Memory
whose real value lives in an index the substrate computed is a Memory no other
consumer can reproduce.

**A2. Producer-assigned deterministic ids, so a re-fired write is a no-op.**

Capture hooks fire more than once. mem's append-only tables use the id as the
dedup key and insert-or-ignore, which makes a duplicate capture a no-op rather
than a duplicate row. A server-assigned autoincrement cannot do this, because the
producer has nothing to deduplicate against.

## B. Time

**B1. Two clocks, always.** Event time (when the fact was observed) is separate
from ingest time (when the store learned it), and event time is the ordering key.

Producers record after the fact: a git hook captures a base commit at worktree
setup, a CI webhook reports a landing minutes later. mem's provenance event log
carries both columns for exactly this reason, and it is the one place that log
deliberately deviates from the existing beads `events` table idiom.

**B2. An as-of read.** A consumer must be able to ask what the store held as of
time T and receive an answer that excludes everything recorded later.

This is the requirement nothing in the surveyed field provides, and the one our
own evaluation could not exist without. mem's reader enforces a strict
`closedBefore` boundary plus sibling and supersession exclusions, and weakening
any of them leaks the answer into the evaluation context. The same primitive is
what lets anyone reconstruct why an agent decided what it decided, rather than
inspecting a store that has since been corrected.

**B3. Supersession is an edge, not a delete.** Corrections are new rows; the
closure is computed at read time.

mem resolves supersedes chains through a recursive closure in the reader rather
than by mutating or removing the superseded record. A memory substrate that
implements correction as update destroys the history that B2 exists to expose.

## C. Provenance

**C1. Every memory carries who produced it, by what route, and opaque pointers
to what supports it.** The substrate validates that a reference is well formed
and never interprets what it means.

**C2. Edges carry a soundness tier and the set of sources that established
them, and one logical edge re-derived from several sources is one row whose
provenance accretes those sources.**

mem's provenance links table enforces this with a uniqueness constraint over
(record, entity, relation, key type). Two independent derivations of the same
edge are evidence that it is sound, not two edges.

**C3. A contradicted edge is flagged and retained, never deleted.** mem marks
these `suspect` and excludes them from analysis populations while keeping them
for audit. Deleting a contradiction destroys the record of the disagreement,
which is usually the interesting part.

**C4. Citations are snapshotted at append time, never joined live.**

This is the sharpest and least obvious lesson in mem's schema, and it runs
against the normalized instinct. mem's distilled lessons carry the record id and
commit they cite as literal values with no foreign key, because a live join lets
a re-ingested outcome silently rewrite an existing citation. The memory would
then say something it never said, with no event marking the change.

## D. Lifecycle classes

**D1. Memories do not share one lifecycle, and the type must say which one a
memory has.**

A factual observation, a distilled lesson, and a verdict about a tool's quality
decay differently and are contested differently. A verdict such as "this tool is
unreliable" propagates fleet-wide from one bad session and, with no expiry or
contest path, never stops propagating. If the substrate does not declare a
lifecycle class, every consumer invents one and they do not compose.

*This requirement is reasoned from the failure mode, not yet measured. It is the
first thing worth an experiment.*

**D2. Some memory classes are non-regenerable, and the migration story must say
so out loud.**

Three of mem's tables cannot be rebuilt from the bead spine: distilled lessons,
runtime memory events captured at execution time, and provenance events from
external producers. They must be exported and re-imported across any schema
change. A substrate whose upgrade path is "rebuild from source" silently
destroys exactly the memory layer, and the destruction is invisible because the
rebuild succeeds.

## E. Delivery and adoption

**E1. Bulk delivery is the primary read path. Query is the rare one.**

Measured across the gas-city fleet: the context-priming command delivered
132,551 memory bodies over 5,891 invocations, against 16 reads by key and zero
joins. Design the projection and the size budget for the bulk path first. A
Memory type optimized for a query interface is optimizing the path nobody takes.

**E2. Because delivery is bulk and bounded, ordering before pagination is part
of the type contract, not a display concern.**

Over 12 realistic tasks against 50/100/500-memory corpora with literal match
sets of 12 to 220, the first useful memory fell outside page one at page size 5
for 7 of 12 tasks, worst frozen rank 159. First-page visibility ranged from 50%
under some structural priors to 100% under BM25F. Which policy wins is not
settled (see G), but that the choice materially buries useful memories is.

**E3. Adoption guidance belongs to the substrate, emitted where the tool is
exposed.**

Measured: a 480-call grid with the memory tool available but unmentioned
produced 457 reads and 136 writes to the agent's own native memory file, and
zero calls to the tool. Across the surveyed systems, adoption surface sits on a
four-rung ladder, and only the top rungs work. Rung 0 is a capability
description with no trigger rules (mem0 OpenMemory, LangMem, our own adapters).
Rung 1 is prose the integrator must paste, and the vendor example has already
drifted off its own tool grammar: Zep/Graphiti's shipped rules file says
`search_facts` while the registered tool is `search_memory_facts`. Rung 2 is
guidance the platform injects whenever the tool is present, which the integrator
cannot forget. Rung 3 is structural enforcement through tool-call ordering.

*Consequence:* the Memory type should carry its own invocation guidance, with
literal invocations and a reason to act on this turn, rather than leaving it to
each project's instructions file.

## F. What the substrate must not own

**F1. No write-time semantic validation.** The substrate checks structure. Truth
is the consumer's judgment, which requirements C exist to make possible.

We built the opposite and measured it. A model-based gate checking whether a
written record is supported by its cited evidence scored 5 of 7 per record
against a one-line substring baseline's 7 of 7, and across 35 verdicts produced
zero true positives, 25 true negatives, 5 false positives and 5 unusable
extractions. Its pre-registered replacement acceptance bar has now read NOT MET
three times, on 2026-09-06 and twice on 2026-09-07, the third with no code
changed between readings.

The cost argument is the weaker half and would expire the moment inference got
cheap. The structural half does not. Asked the identical question twice inside
one process, through a prompt that is a pure function of the record and its
evidence, the gate returned different claim decompositions and opposite verdicts;
the reading that decided one firing turned out to be a 1-in-5 minority outcome.
A non-deterministic check cannot be a *blocking* gate on an append-only store at
any price, because its two errors are not symmetric: a false accept leaves a
contestable row with its citations attached, and a false reject is a record that
never existed and that no rebuild can reconstruct. Moving the same check off the
write path, to annotate rather than refuse, costs nothing in capability and
removes the destructive error entirely. See
`docs/mem-747nj-fidelity-gate-verdict.md`.

**F2. No interpretation of actor or reference values.** The moment the schema
understands a Claude session identifier it stops being a primitive. Keep them
opaque and namespaced by a kind field.

**F3. No content in the capture layer.** mem's write-time memory event log
stores only join keys: the operation, which memory, where it was used, the
session, the work id. It carries no memory content and no outcome field, so the
capture layer cannot itself decide what is allowed into an evaluation input.
That decision belongs to a separate firewall, and it can only stay separate if
capture is content-free.

## G. What this does not settle

Stated so the gaps are not mistaken for answers.

- **Ranking policy.** Whether query-specific lexical ranking earns its
  complexity over a structural prior is unrun. The pilot that exists is one task
  and one sample, and validates instrumentation only.
- **The write-time gate.** F1 rests on three NOT MET readings of a rule whose
  one identified remaining defect has deliberately not been repaired, to avoid
  changing an instrument after reading its result. Two limits on how far it
  generalises. The corpus is six probe pairs over three packets whose lures we
  authored, so it estimates nothing about how agents falsify memories in the
  wild. And the deciding defect sits in our extractor's repair loop at one local
  decode pin, so the measured claim is that *this* gate is unreliable, not that
  semantic checking is. What survives both limits is the asymmetry argument,
  which is structural rather than measured: it holds for any non-deterministic
  check placed in front of an append-only store.
- **Portability.** Every requirement here is derived from one implementation plus
  a survey of what other systems ship. None is derived from actually porting a
  general consumer onto this shape. Until mem0 or Letta has been re-pointed at a
  Memory bead type and something has been found missing, this document is a
  hypothesis about generality.
- **Lifecycle classes (D1)** are reasoned from a failure mode we have not yet
  reproduced in a measurement.

## H. Relation to the open upstream requirements

The numbered requirements in the proposal cover the write verb (R4), retrieval
ordering and pagination (R6), adoption guidance (R8), owned links (R22), and the
compatibility projection (R24, R25). This document is orthogonal to those: it
states what the node kind must guarantee, where those state what the interface
must offer. The requirements above that have no counterpart in the proposal, and
would need to be added, are B2 (the as-of read), C4 (snapshotted citations), D1
(declared lifecycle class), D2 (non-regenerable classes named in the migration
contract), and F3 (content-free capture).
