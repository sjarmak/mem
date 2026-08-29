# BDP conformance fixtures: seven graph shapes and a collation family

Seven graph families rendered as BDP v0 Read-profile records. They exist to
exercise collection ordering and pagination at shapes and densities a
hand-written fixture will not reach.

An eighth directory, `collation-edge-identifiers`, ships beside them and is
not one of the seven. It holds no interesting graph at all. Its payload is the
identifier spellings, which is a question the seven cannot ask. See
[The collation family](#the-collation-family).

Offered on [gastownhall/beads#6051](https://github.com/gastownhall/beads/issues/6051)
after the ordering gap was accepted on
[gastownhall/bdp#8](https://github.com/gastownhall/bdp/issues/8). Bead:
`mem-vn4ek`.

## Generated, not projected

The graphs are **generated from this package's own seed**
(`bdp-fixture-topology/v1`). They are **not a projection** of any corpus, and in
particular not of the frozen ordering corpus behind memory-bench.

An earlier revision did project that corpus and neutralized what it published:
opaque identifiers, synthetic text of the same length, no rank fields. That is
not enough. The corpus is authored around one distinguished node per family, so
its degree distribution is the benchmark's own answer key. Sweeping degree
predicates over the projected Links, `deg == 4` returned exactly three Beads in
one family and all three were gold; across 200 random same-sized decoy sets per
family, none reached that precision in six of the seven families, against a 1.8%
base rate. Neutralizing values does not reach a channel that lives in the shape,
and neither does any rewiring that preserves the degree sequence. Generating the
shapes does.

Two consequences follow. The family names are labels for graph shapes and
carry no domain content: nothing in a family named `incident-runbook-sparse-authority`
is about incidents or runbooks. And every Bead property is a function of that
Bead's own opaque id, so the properties are filterable but carry no meaning.

## Authored shapes, not measured frequencies

Every family is **authored to exercise a consumer policy**, picked because an
implementation can get that case wrong. These are **not measured field
frequencies**. Nothing here supports a claim about how often a 380-outdegree hub,
a disconnected component, or a repeated endpoint tuple occurs in a real store.
Use them to check that an implementation behaves correctly when the shape is
present, not to argue that the shape is common.

The numbers in the table below are measurements of these fixtures. The test
suite recomputes each one from the emitted tree, so a stale row fails the suite
rather than sitting in prose.

## The families

| Family | Links | Max outdegree | Max indegree | Repeated endpoint tuples |
|---|---:|---:|---:|---:|
| `data-schema-dependency-dag` | 885 | 3 | 7 | 0 |
| `distributed-system-clustered-components` | 275 | 11 | 1 | 0 |
| `incident-runbook-sparse-authority` | 467 | 13 | 63 | 0 |
| `migration-correction-temporal-chain` | 524 | 40 | 4 | 0 |
| `platform-documentation-hub-spoke` | 416 | 380 | 6 | 0 |
| `release-engineering-branching-playbooks` | 469 | 31 | 4 | 5 |
| `security-policy-cross-team-network` | 862 | 6 | 15 | 0 |

Every family holds 500 Beads and declares the same 2 Types. What each shape is
for:

- `platform-documentation-hub-spoke`: a selection larger than the largest
  advertised page limit.
- `incident-runbook-sparse-authority`: high indegree against low outdegree,
  separating `?target=` from `?source=`.
- `data-schema-dependency-dag`: a flat degree distribution with no hub to
  special-case.
- `migration-correction-temporal-chain`: deep paths and short cycles at low
  degree.
- `distributed-system-clustered-components`: the empty selection, and Beads that
  are in no Link at all (200 of them).
- `release-engineering-branching-playbooks`: several Links sharing one
  `(type, source, target)` tuple.
- `security-policy-cross-team-network`: incident sets that differ from both the
  inbound and the outbound set.

Each family's purpose is also recorded in `manifest.json` under `exercises`, so
a consumer that drops a family can see which conformance property it just
stopped testing.

## Layout

The tree mirrors the URL space a consumer would fetch from.

```
ordering-families/
  manifest.json              checksums, density stats, generator parameters
  types/memory.json          the Memory Type Descriptor document
  types/cites.json           the Cites Type Descriptor document
  <family>/
    discovery.json           the Read discovery document, per readDiscovery
    dataset/beads.json       {"items": [beadRecord, ...], "next": null}
    dataset/links.json       {"items": [linkRecord, ...], "next": null}
    dataset/types.json       {"items": [typeSummary, ...], "next": null}
    ordering.json            the selected sets and page partitions
  collation-edge-identifiers/
                             same four documents; identifier spellings, not a
                             graph shape. Page limits 3 and 10.
upstream/
  bdp-v0.schema.json         vendored, pinned; see PROVENANCE.json
  PROVENANCE.json            upstream commit, checksum, refresh procedure
```

`dataset/` is the load set, not a response, and it is not what the collection
URLs in `discovery.json` would return. Its `items` arrays are **deliberately not
in the reference order**: each is serialized under a digest of the record id.
Shipping the records sorted would let an authority that merely echoes the order
it loaded reproduce every recorded sequence without implementing an order at
all, and the fixture would be unable to fail. `ordering.json` holds what a
conformant authority owes.

`dataset/types.json` is the `GET /types/` inventory, whose items are exactly
`{id, name, describes}`. The full Descriptor for each Type is its own document
at the Type ID URL, under `types/`.

## Mapping

One generated node becomes one Bead. Bead ids are a fixed-width digest of a
generator-side node name that is never published, so the id space is not the
order the graph was built in. One generated edge becomes one first-class Link
record of a single `cites` Link Type, because BDP Links are not embedded in
either endpoint. Link ordinals are assigned after sorting on the published
endpoints, so a Link id is a function of the published graph. Both endpoints of
every Link are Beads in the same Scope.

Bead properties are `title`, `aliases`, `lifecycle`, `provenance` and `body`,
each derived from the Bead's own id. The emitter enforces a closed allowlist of
published property keys rather than a denylist, because a denylist only catches
the leak whose spelling someone thought of. `lifecycle` is the narrow Selector
predicate (3 to 7 Beads per family) and `provenance` the wide one (roughly half
of each family).

Revisions are a SHA-256 prefix over the record as built so far, meaning every
field except the revision itself. That is what makes the tree regenerate
byte-identically. BDP compares revisions only for equality, so any stable string
is legal. This is not a position in the content-addressed-token discussion on
gastownhall/bdp#6: nothing here claims a consumer may recompute or verify a
revision.

## What ordering.json asserts

bdp#8 as proposed has four clauses: an authority imposes a total order on the
selected set, the order is deterministic for a given selected set, it is stable
across the pages of one snapshot, and the authority documents the order it uses.
The proposal **leaves the choice of order to the implementation**. An authority
documenting "descending insertion ordinal" is conformant and returns none of the
sequences in these files.

So `ordering.json` records two things a harness may assert against any
conformant authority, whichever order it documents:

1. **Membership.** `selected_set` is the set the predicate selects.
   Concatenating every page of one snapshot must yield exactly these ids, each
   once, with no omission and no repeat.
2. **The page partition.** `page_count` and `page_item_counts` at limits 25 and
   200, with `spans_multiple_pages` saying whether a case forces a continuation
   at all.

`selected_set` is written in ascending canonical URI of the record id so two
runs can be diffed. That is a spelling choice. It is also the expected sequence,
but only for an authority that documents `ascending-canonical-uri` as its order;
asserting it against one that documents something else tests the harness
author's preference rather than conformance. The order is carried under a name
so that adopting, renaming or rejecting it is a one-line change rather than a
silent reinterpretation of the data.

Earlier revisions recorded the first id of each page instead of the item counts.
First ids pin the boundaries only under one particular order, which made the
page arithmetic unusable against any other.

The five selections, per family:

1. `links/?source=<hub>`, the outbound Links of the highest-outdegree Bead.
2. `links/?target=<hub>`, its inbound Links.
3. `links/?endpoint=<hub>`, its incident Links.
4. `beads/?selector=$[?@.properties.provenance == "agent"]`, 230 to 270 Beads,
   which is the many-page Selector case: the selected set is decided before
   pagination and must stay fixed across every continuation.
5. `beads/?selector=$[?@.properties.lifecycle == "archived"]`, 3 to 7 Beads,
   which must come back as one page with `next` null.

The first three are three pairwise-distinct sets in six of the seven families,
so an authority that treats `target` as an alias of `source`, or folds either
into `endpoint`, returns the wrong answer. In
`distributed-system-clustered-components` no Bead carries both an inbound and an
outbound Link, so the inbound set is empty and the incident set equals the
outbound one: a pass on that family **does not discriminate `endpoint` from
`source`**, and it is instead the empty-selection case that catches an authority
answering an empty selection with zero pages instead of one empty page. Which
families are which is recorded per family in the manifest under
`hub_predicates_are_pairwise_distinct`.

In `platform-documentation-hub-spoke` the outbound selection returns 380
outbound Links, more than the largest advertised page limit, so one Bead's
adjacency splits across 16 pages at a limit of 25 and 2 pages at a limit of 200.

## The collation family

`collation-edge-identifiers` is a separate directory in the same tree, under the
same Scope prefix and declaring the same two Types, so a consumer loads it in the
same pass. It is **not one of the seven**, and it is deliberately kept out of
`families` in the manifest: it has 23 Beads and 15 Links, no hub and no degree
distribution, so folding it into the density table would make every figure there
answer a question nobody asked. It is recorded under `collation_family` instead.

### Why it has to be separate

The seven exist to vary graph shape, which means holding the identifier spelling
fixed: their ids are **zero-padded, lowercase and ASCII** so that the order is
never in question while the shape varies. That is the right choice for them and
it is exactly what makes them blind here. "Ascending canonical URI" does not say
whether the comparison decodes percent-escapes, normalizes Unicode, folds case or
parses digit runs first. Over padded lowercase ASCII every one of those readings
returns the same sequence, so a conformant-looking authority that sorts
numerically, case-insensitively, or over unnormalized Unicode passes all seven.

This family is built out of the identifiers on which those readings diverge, so
it names its comparison rule in the order id: `ascending-canonical-uri-codepoint`,
ascending by Unicode code point over the canonical URI as written, with no
decoding, normalization, folding or numeric parsing. Every identifier here is
still ASCII, so a bytewise UTF-8 comparison and a codepoint comparison agree; the
non-ASCII lives inside percent-encoding, because a raw non-ASCII id would be an
IRI rather than a URI.

### The four groups

| Group | Ids | Separates |
|---|---|---|
| `unpadded-ordinals` | `1`, `2`, `9`, `10`, `11`, `100`, `101`, `2000` | `numeric-aware` |
| `mixed-case` | `GAMMA`, `Gamma`, `gamma`, `Delta`, `delta`, `Zeta` | `casefold`, `punctuation-ignoring` |
| `punctuation` | `alpha`, `alpha-two`, `alpha_one`, `alphathree` | `punctuation-ignoring` |
| `normalization` | `cafe`, `caf%C3%A9`, `cafe%CC%81`, `r%C3%A9sume`, `re%CC%81sume` | `percent-decoding`, `nfc-normalizing` |

`caf%C3%A9` is U+00E9 percent-encoded; `cafe%CC%81` is `e` followed by the
encoded combining acute accent. The two spell one label and decode to the same
text under NFC. Because `%` is 0x25 and `e` is 0x65, the codepoint order is
`caf%C3%A9`, then `cafe`, then `cafe%CC%81`, an order no rule that decodes first
will produce.

The Link ids carry the same four axes separately rather than inheriting the
claim from the Bead ids, since the Link id space is the half the seven families
pad flat. Each group declares which rules it separates and the declaration is
checked against the identifiers on every build, so a group that quietly stops
separating its axis is a build failure rather than a fixture that still reads
convincingly.

### Two kinds of failure, recorded apart

`ordering.json` records what each of the five comparison rules returns over both
collections, under `collation.comparisons`, and the two outcomes are not the same
finding:

- `casefold`, `punctuation-ignoring` and `nfc-normalizing` **tie** ids this
  family holds distinct, and a tie is not a total order, so such a rule fails
  the first clause of bdp#8 whatever order the authority documents. A tied pair also
  threatens membership: a keyset continuation built on the comparison alone can
  repeat one of the pair or drop it across a page boundary. Which pairs tie is
  recorded under `ties`; whether a given authority actually loses a record
  depends on how it paginates, so the tree records the pairs rather than
  predicting the outcome. The gates in `tests/test_bdp_collation.py` pick one
  paginator and show it: served through a keyset continuation, where the cursor
  is the sort key of the last item and the next page is everything strictly
  greater, a casefolding authority drops `Gamma` and `gamma` from `beads/` and
  `Edge` and `edge` from `links/` at a limit of 3, and an NFC-normalizing one
  drops `cafe%CC%81` and `re%CC%81sume`. An authority sorting by the recorded
  order serves every set intact.
- `numeric-aware` and `percent-decoding` are total orders, just different ones.
  Under bdp#8 as proposed, which leaves the choice of order to the
  implementation, an authority documenting either is conformant. They are
  detectable only against an authority that documents this family's order, and
  they are recorded so a harness can report which rule an authority appears to
  have used rather than only that the sequence was wrong.

A rule that ties gets no recorded `sequence`. With a tie, what a sort returns
depends on the order the items went in, so writing one down would publish this
emitter's input order as though it were the rule's answer.

Page limits here are 3 and 10 rather than 25 and 200, small enough that a page
boundary falls inside a collection of this size. Both recorded selections, the
outbound Links of the busiest Bead and the `provenance == "agent"` Beads, span
more than one page at the smaller limit: inside a single page an authority can
sort the page and be right by accident.

## What these fixtures do not cover

**Cursors, and therefore the stability clause of bdp#8.** Every shipped
collection document ends with `next` null, so a continuation URL is described
but never served. An authority that re-sorts between pages, or restarts the
snapshot on continuation, passes anyway. Determinism across two reads is
likewise something a harness gets by reading twice rather than from anything in
these files, and the documentation duty is a property of an authority rather
than of a fixture. Tracked as `mem-31sg8`.

**Collation, in the seven graph families.** Their Link ids are zero-padded to a
fixed width and their Bead ids are lowercase hex, so codepoint order, numeric
order and case-insensitive order all coincide there. Those seven test order
totality and page stability and say nothing about which comparison rule an
authority used. `collation-edge-identifiers` is where that question is asked;
the section above says what it does and does not settle.

**Everything below is also untested here**, and a pass on this tree says nothing
about any of it:

- **Out-of-Scope endpoints.** Both endpoints of every Link are in-Scope Beads,
  so `external: "opaque"` and `external: "bead"` are never exercised.
- **Self-loops.** No Link has the same Bead as source and target; the generator
  refuses one.
- **Multiple Link Types.** One `cites` Type carries every edge, so `type` and
  `conformsTo` predicates are never discriminating on `links/`.
- **Pinned references and revision conflict.** These are Read-profile fixtures.
  Nothing here reaches the update or transactional profiles.

## Regenerating

From `memory-bench/`:

```
uv run python -m membench.bdp_fixtures
uv run pytest tests/test_bdp_fixtures.py
```

The emitter reads nothing but the family names and its own seed. It prunes
documents it could have written at that exact path but did not on this run, so a
removed or renamed family cannot leave stale files behind and still show a clean
diff. Ownership is decided on the whole relative path, never on the basename. A
clean `git diff` after a re-emit is the determinism evidence and a dirty one is a
defect.

The test suite checks that, validates every document as shipped against the
vendored schema bundle, checks that each recorded expectation matches the records
that actually shipped rather than the ones the emitter intended to ship, checks
that each shape still exercises the property it exists for, and fails if any rank
label or local filesystem path reaches the published tree.
