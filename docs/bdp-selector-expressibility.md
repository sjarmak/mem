# BDP Selector expressibility against observed agent retrieval

Bead: `mem-rj2mg`. Preregistration:
`memory-bench/results/bdp/selector-expressibility-preregistration.json`
(sha256 `3ffc83e06bc22216e135ca2b2a3b24b8278b99ba1bb1310c1dcae7890e4aeb35`,
locked before any classification was computed).

## The question

BDP (`gastownhall/bdp`, `docs/specs/bdp.md`) gives collection retrieval a bounded
RFC 9535 JSONPath Selector and no text search. The profile admits singular paths,
JSON literals, existence tests, `==` `!=` `<` `<=` `>` `>=`, and `&&` `||` `!`.
It excludes joins, graph traversal, projection, aggregation, recursive descent,
nested filters, functions, and regular expressions.

Whether that is enough is currently an opinion on both sides. This measures it:
what fraction of the retrieval queries agents actually issue against a bead store
does the profile express, and what does the residue need?

The result is decision-relevant only while the Read profile is still editable, so
this runs now rather than after v0 freezes.

## Why the transcripts and not the store

The work-audit store records `trace_runs.tool_calls_by_type`, a name-to-count map,
plus `n_tool_calls`. Argument text is not projected. The predicate an agent asked
for exists only in the raw transcript JSONL, and `bd` is invoked through the Bash
tool, so a query arrives as a shell command string rather than a structured tool
input. Extraction is therefore CLI-grammar parsing over transcripts, resolved
through `src/ingest/trace-resolve.ts` run from the gas-city checkout.

The locked preregistration states the same step with an absolute local path. That
copy is disclosed rather than edited, because editing a locked document
invalidates the digest the result rests on; `analysis.json` records its exact
location under `preregistration.not_redacted`.

The denominator is whatever resolves. It gets reported, not assumed.

## What is counted

One observation is one `bd` read invocation: `list`, `show`, `ready`, `query`,
`search`, `dep`, `count`, `stats`, `blocked`. Writes are a different BDP profile
and are out. Harness-generated and generator-generated invocations are out, since
they measure our code rather than an agent's retrieval behavior.

The independent unit is the session, not the invocation. A single retry loop can
emit dozens of identical queries, and per-invocation counting would let one
session set the headline. Invocations are deduplicated within a session, every
fraction is computed per session, and sessions are averaged with equal weight.
Raw invocation counts are reported next to the primary, never instead of it.

## Taxonomy

Expressible in the profile: `E0` identity fetch, `E1` exact equality or set
membership, `E2` ordered comparison, `E3` boolean combination, `E4` existence.

Expressible only with client-side work: `C1`, where the profile selects a
superset and the client filters.

Not expressible: `N1` substring or free-text match, `N2` regular expression,
`N3` traversal or join across resources (readiness computation lands here), `N4`
aggregation as the requested answer, `N5` ordering or top-k, `N6` nested-value
projection.

## Gates, fixed in advance

**G1, search predicate.** Session-averaged fraction requiring `N1` or `N2`. At or
above 0.20 we argue for a search predicate in a later profile. At or below 0.10 we
report the bounded profile as adequate for this population. Between the two we
report no recommendation.

`N5` is deliberately excluded from G1. R6 already assigned ordering to consumer
policy, and folding it in here would recount a settled question as new evidence
for a different one.

**G2, traversal.** Same statistic and same bands for `N3`. BDP assigns readiness
computation to the client on purpose. A high `N3` fraction does not overturn that
by itself; it prices the client-side work the spec hands over.

**G3, enumeration cost.** Median and p90 superset size a `C1` query enumerates.
Descriptive, no threshold.

## Method boundary

Flag and subcommand parsing is mechanical wherever the CLI grammar makes the
predicate deterministic (`--status`, `--type`, `--label`, `--priority`,
`--assignee`, positional ids). A model classifies free-text search intent only,
batched, against the closed label set above. Keyword or regex heuristics standing
in for that classification are prohibited, as is any hand-tuned threshold inside
the classifier.

## Publication

Predicate shapes, taxonomy labels, counts, and fractions. Not query literals, not
free-text arguments, not bead titles or bodies, not file paths, not model text,
not identities. This holds the line the earlier `beads_ordering` packages set.

## Known limits

The corpus is one organization's agents against one CLI, so it estimates this
population rather than every bead deployment. More importantly, `bd`'s own grammar
shapes what gets asked: a query nobody issued because the CLI could not express it
is invisible here, which biases the measured residue downward. That belongs in the
writeup, not in a footnote. Sessions are also not independent of the rigs they ran
in, which the session-level counting rule bounds but does not remove. Free-text
classification is model-dependent, so the classifier model and `bd` version get
recorded and a fixed random subsample gets hand-audited.

A finding that the bounded profile is adequate is reported at the same volume as a
finding that it is not.
