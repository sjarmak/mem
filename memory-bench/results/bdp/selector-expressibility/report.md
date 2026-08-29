# BDP Selector expressibility against observed agent retrieval

Bead `mem-rj2mg`. Preregistration locked at commit `2beb545` before any
classification ran, sha256
`3ffc83e06bc22216e135ca2b2a3b24b8278b99ba1bb1310c1dcae7890e4aeb35`.

## What was measured

BDP gives collection retrieval a bounded RFC 9535 JSONPath Selector and no text
search. Whether that is enough has been an opinion on both sides. This counts it.

9,172 agent transcripts (6.8 GB) yielded 23,076 `bd` read invocations across
3,889 sessions, 16,354 after deduplicating exact repeats within a session. The
independent unit is the session, so one retry loop cannot set the headline. Each
invocation was classified mechanically from the `bd` CLI grammar into the
preregistered taxonomy: expressible (`E0` identity, `E1` equality or membership,
`E2` comparison, `E3` boolean, `E4` existence), client-side-filterable (`C1`), or
not expressible (`N1` text, `N2` regex, `N3` traversal, `N4` aggregation, `N5`
ordering, `N6` projection).

## Results

| Gate | Session-averaged | Per-invocation | Preregistered verdict |
|---|---|---|---|
| G1 text search (`N1`/`N2`) | 0.0114 | 0.0508 | profile adequate (≤ 0.10) |
| G2 traversal (`N3`) | 0.3569 | 0.2408 | argues for capability (≥ 0.20) |
| G2 excluding `bd ready` | 0.0051 | | |
| `C1` label containment | 0.0073 | 0.0209 | no gate |

**The Selector's text-search exclusion costs almost nothing.** G1 lands at 1.1%
of queries per session, an order of magnitude below the threshold that would have
argued for a search predicate. The classifier labels every positional on `bd
search` that is not a generated bd id as `N1`, and the planned model pass was
dropped because it could only move arguments out of `N1`. So 0.0114 is an upper
bound on the text residue rather than an estimate of it, subject to the
instrument limit below. Agents overwhelmingly fetch by id (`E0`, 5,809) or filter
on equality (`E1`, 8,915).

**The traversal number is one operation, not a pattern.** G2 crosses its
threshold at 0.3569, but removing the single subcommand `bd ready` collapses it
to 0.0051. Ad-hoc graph traversal essentially does not occur; what occurs is one
named, repeated question ("what is unblocked and mine") asked 4,439 times. BDP
already assigns readiness to the client, and the preregistered taxonomy defines
`N3` to include readiness computation, so this outcome was anticipated rather
than discovered. The measurement does not overturn that call, and it says
something more useful than the raw fraction: the entire observed traversal demand
has one shape, so it is servable by a named derived collection rather than by
general traversal in the Selector.

**Array containment is a real but small gap.** A singular path compared to a JSON
literal cannot express "this array contains X", and nested filters are excluded,
so label filters need a superset plus a client-side pass. That is 0.7% of queries
per session, 342 invocations. G3: 89.8% of those keep a narrowing predicate
(almost always `--status`), so the enumerated superset is the open beads rather
than the whole collection. Against one present-day store that is 107 resources
against 1,708. At this scale, `limit` and `cursor` cover it. That store is the
analyst's own project and the corpus spans 118 working directories whose sizes
are not measured, so this supports a claim about collections of a few thousand
resources, not a general property of the mechanism.

Ordering (`N5`, 197 invocations) was excluded from G1 by preregistration, because
R6 already assigned ordering to consumer policy and recounting it here would
launder a settled question into evidence for a different one. The exclusion is
not load-bearing: folding `N5` back into G1 moves it to 0.0179, still an order of
magnitude under the threshold. Ordering remains the subject of
`gastownhall/bdp#8`.

## What this does and does not support

Supported: the bounded Selector is adequate for this population's read traffic.
A search predicate is not the missing piece.

Not supported: any claim about bead deployments in general.

The sharpest limit is that the instrument and the subject share a design. `bd`
is bounded, non-fuzzy, and regex-free in the same way BDP's Selector is, so an
agent that wanted a capability `bd` does not offer would not leave a failed `bd`
call behind. It would route around `bd` entirely, by grepping an export, asking
the model from memory, or reaching for another tool, and none of that appears in
this corpus at all. This is not the generic caveat that a CLI shapes its queries;
it is closer to circular, and it biases the measured residue downward by an
amount this study cannot bound. G1 is evidence, not proof.

## Corrections after review

Two independent reviewers audited the classifier and the method after the first
result was committed. Three defects changed a published number, all recorded in
`analysis.json`:

- The id pattern matched any hyphenated lowercase token, so free-text topic
  queries on `bd search` scored as identity fetches. 88 positionals moved from
  `E0` to `N1`; G1 went 0.0105 to 0.0114. This one mattered beyond its size: it
  undercounted the text residue, the single direction the writeup claimed was
  impossible.
- `g3.py` counted raw rows while every other statistic deduped within a session,
  so `analysis.json` reported both 342 and 577 for the same quantity. Corrected
  to 342, and the retains-a-narrowing-predicate share to 89.8% from 91.9%.
- `E3` fired on any two co-occurring labels, including `E1`+`N1`, inflating the
  expressible side with queries the Selector cannot serve. 3,820 to 39. No gate
  reads `E3`.

Five further classifier defects measured zero on this corpus and are listed in
`analysis.json` with the evidence for the zero. One known gap is left unfixed:
`extract.py` can build a fake argv from prose containing the token `bd`, about 12
rows in 31,133, of which 2 reach the counted population without changing their
label. A prose detector there would be a semantic heuristic inside a layer that
is mechanical by design.

## Deviations from the preregistration

Four, all recorded in `analysis.json`. The corpus was enumerated directly from
on-disk transcript directories rather than resolved through `gc session logs`,
which yields a larger denominator. The planned model pass over free-text intent
was not run. A reporting bucket `E_NONE` was added for queries carrying no
predicate. G3's statistic was substituted: the preregistration asks for median
and p90 superset size, and historical result sizes are not recoverable from
transcripts, so superset shapes are counted exactly and sized against one
present-day store.

The preregistered exclusion of harness-issued and self-issued invocations is
implemented and removed 4 rows.

Five earlier classifier defects, found by hand-auditing samples against raw
command text during the build, are also listed. The largest discarded 27,939 of
31,133 rows by reading shell redirection tokens as argv placeholders.

## Reproducing

`extract.py` scans transcripts for `bd` invocations inside Bash tool calls.
`classify.py` applies the taxonomy. `g3.py` derives superset shapes.
`package.py` assembles this directory. The intermediate that holds command text
stays in a session scratchpad and is not published: this package carries
predicate shapes, labels and counts only.
