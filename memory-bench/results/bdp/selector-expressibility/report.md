# BDP Selector expressibility against observed agent retrieval

Bead `mem-rj2mg`. Preregistration locked at commit `2beb545` before any
classification ran, sha256
`3ffc83e06bc22216e135ca2b2a3b24b8278b99ba1bb1310c1dcae7890e4aeb35`.

## What was measured

BDP gives collection retrieval a bounded RFC 9535 JSONPath Selector and no text
search. Whether that is enough has been an opinion on both sides. This counts it.

9,172 agent transcripts (6.8 GB) yielded 23,076 `bd` read invocations across
3,889 sessions, 16,354 after deduplicating exact repeats within a session. Each
invocation was classified mechanically from the `bd` CLI grammar into the
preregistered taxonomy: expressible (`E0` identity, `E1` equality or membership,
`E2` comparison, `E3` boolean, `E4` existence), client-side-filterable (`C1`), or
not expressible (`N1` text, `N2` regex, `N3` traversal, `N4` aggregation, `N5`
ordering, `N6` projection).

## Results

| Gate | Session-averaged | Per-invocation | Preregistered verdict |
|---|---|---|---|
| G1 text search (`N1`/`N2`) | 0.0105 | 0.0464 | profile adequate (≤ 0.10) |
| G2 traversal (`N3`) | 0.3569 | 0.2408 | argues for capability (≥ 0.20) |
| G2 excluding `bd ready` | 0.0051 | | |
| `C1` label containment | 0.0073 | 0.0209 | no gate |

**The Selector's text-search exclusion costs almost nothing.** G1 lands at 1.1%
of queries per session, an order of magnitude below the threshold that would have
argued for a search predicate. This holds under the most hostile assumption
available: the classifier labels *every* non-id positional argument as `N1`, so
0.0105 is an upper bound on the text residue, not an estimate of it. Agents
overwhelmingly fetch by id (`E0`, 5,897) or filter on equality (`E1`, 8,915).

**The traversal number is one operation, not a pattern.** G2 crosses its
threshold at 0.3569, but removing the single subcommand `bd ready` collapses it
to 0.0051. Ad-hoc graph traversal essentially does not occur; what occurs is one
named, repeated question ("what is unblocked and mine") asked 4,439 times. BDP
already assigns readiness to the client. The measurement does not overturn that
call, and it does say something more useful than a raw fraction would: the entire
observed traversal demand has one shape, so it is servable by a named derived
collection rather than by general traversal in the Selector.

**Array containment is a real but small gap.** A singular path compared to a JSON
literal cannot express "this array contains X", and nested filters are excluded,
so label filters need a superset plus a client-side pass. That is 0.7% of queries
per session. G3: 91.9% of those keep a narrowing predicate (almost always
`--status`), so the enumerated superset is the open beads rather than the whole
collection. Measured against one present-day store as a proxy, that is 107
resources against 1,708. `limit` and `cursor` cover it.

Ordering (`N5`, 197 invocations) was excluded from G1 by preregistration, because
R6 already assigned ordering to consumer policy and recounting it here would
launder a settled question into evidence for a different one. It remains the
subject of `gastownhall/bdp#8`.

## What this does and does not support

Supported: the bounded Selector is adequate for this population's read traffic.
A search predicate is not the missing piece.

Not supported: any claim about bead deployments in general. This is one
organization's agents against one CLI. More importantly, `bd`'s own grammar
shapes what gets asked, so a query nobody issued because the CLI could not
express it is invisible here. That biases the measured residue downward, and it
is the main reason to treat G1 as evidence rather than proof.

## Deviations from the preregistration

Three, all recorded in `analysis.json`. The corpus was enumerated directly from
on-disk transcript directories rather than resolved through `gc session logs`,
which yields a larger denominator. The planned model pass over free-text intent
was not run, because the mechanical rule already assigns the maximally
conservative label to every free-text argument. A reporting bucket `E_NONE` was
added for queries carrying no predicate; they stay expressible and no gate keys
on the split.

Five classifier defects were found and fixed during the audit, listed in
`analysis.json`. The largest discarded 27,939 of 31,133 rows by reading shell
redirection tokens as argv placeholders. Each was caught by hand-auditing a
random sample against the raw command text, not by the aggregate numbers looking
wrong.

## Reproducing

`extract.py` scans transcripts for `bd` invocations inside Bash tool calls.
`classify.py` applies the taxonomy. `g3.py` derives superset shapes.
`package.py` assembles this directory. The intermediate that holds command text
stays in a session scratchpad and is not published: this package carries
predicate shapes, labels and counts only.
