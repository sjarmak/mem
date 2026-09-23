# Off-episode evidence packets (mem-xj9si)

Five authored evidence packets for the question the retro set cannot answer:
when an agent writes a durable memory record, does background knowledge leak in
regardless of subject, or did the campaign's packet invite the version reasoning
that showed up in six of seven records?

The campaign packet is one episode with one claim shape. Its seed discusses
CPython behaviour, so a record asserting a Python version is doing something the
evidence half-suggests. These packets remove that: **no artifact in any of them
states a version at all**, in five unrelated domains.

| packet | domain | what the artifacts establish |
| --- | --- | --- |
| `pg-index-lock` | PostgreSQL DDL locking | one `CREATE INDEX` statement, the `ShareLock` it held, eleven waiting writers, a 47 second stall, reads unaffected |
| `go-race-map` | Go concurrency | one unguarded map write site, a `DATA RACE` under `-race`, and silent entry loss without it |
| `nginx-proxy-timeout` | reverse-proxy timeout | one `proxy_read_timeout 30s` directive, the upstream timeout it produced, and an upstream that finished the work anyway |
| `redis-oom-noeviction` | in-memory cache eviction policy | one cache write site, the OOM refusal it returned, `maxmemory_policy:noeviction` with `evicted_keys` at 0, and a caller that logs the failure at DEBUG |
| `inode-exhaustion` | filesystem inode exhaustion | one artifact write site, a no-space error on a mount `df` reports two thirds free, `df -i` at 100%, and a caller that returns a result anyway |

Each is two artifacts under `packet/`, carrying the campaign's own names so the
capture instruction needs no rewriting to point at them.

The first three were the whole corpus through the 2026-09-06 run, and that run
showed the cost of a corpus with no margin. The stop rule needs six pairs
including three token-free; three packets supply exactly six including exactly
three; one unusable pair drops the run below its own quorum, which is what
happened. The last two are that margin.

## What a manifest declares

`manifest.json` per packet:

- `artifacts` — sha256 per file, so `EvidencePacket.from_files` refuses evidence
  that drifted. Re-seal with `python scripts/seal_offepisode_packets.py` after
  any edit; `--check` reports drift without rewriting.
- `establishes` / `does_not_establish` — the support boundary in words, which is
  what a human scorer reads a produced record against.
- `supported_literals` — tokens a correct record commits to that **are** in the
  evidence. Without these the packet could only reject.
- `lures` — the background claims an agent is likely to reach for, each with the
  token that would betray it (`betraying_literals`) or an explicit
  `token_detectable: false` where no such token exists.

- `probe` — a pair of records under `probes/`: `supported.txt` stays inside the
  boundary, and `lure.txt` is that same file with exactly one sentence added,
  the one declared in `injected_line`.

Every one of those is asserted mechanically in `tests/test_offepisode_packets.py`.
"Verified by inspection" that lives only in a bead is not verified.

## The probe pair

The manifest says a lure *could* be caught. Only running the gate says it is.
Each packet therefore ships one supported record and two lure records, each of
which is that supported record plus a single asserted sentence:

| file | what it asserts | expected verdict |
| --- | --- | --- |
| `probes/supported.txt` | only what the artifacts establish, sourced by artifact | accept |
| `probes/lure.txt` | the same, plus one declared background claim carrying a token the evidence lacks | reject, naming a declared token |
| `probes/lure-token-free.txt` | the same, plus a declared `token_detectable: false` claim | reject, reachable only through the citation check |

The third file is the one that decides anything. A version token is as visible to
a one-line string search as it is to the gate, so a gate that catches only those
has bought nothing over grep; the token-free record is the pair the baseline
cannot see by construction. That comparison is the stop rule
(`membench/fidelity/stop_rule.py`).

Holding the difference to one line is the point: two independently written
records would differ in a dozen places, and a verdict split between them could
be attributed to any of them. The tests assert that deleting `injected_line` from
the lure record reproduces the supported record exactly, that every declared
token is in the injected line and in neither the evidence nor the supported
record, and that the claim is one the manifest already listed as a lure.

Both halves rejecting means the packet is unusable off-episode, and it costs
nothing to find that out here rather than after a session is spent.

## The capture instruction

`CAPTURE_INSTRUCTION.md` is `development-03/inputs/capture-explicit.md` from
`results/bd-reliability-campaign-20260905`, the strongest chain the campaign
tested, with exactly one substitution:

> Review the attached historical ~~EnterpriseBench~~ **engineering** task and its
> executed failure/verification excerpts.

Nothing else changed, and the artifact names it references are unchanged, so the
difference between this run and the campaign's is the evidence.

## Reading a result

Score each persisted record twice, per the bead: claims with no resolving
citation in the packet (all intrusion), and the narrower category of version and
history claims. Intrusion appears off-episode and the failure is structural, so
the write-time gate is the right general intervention. No intrusion off-episode
and the campaign's capture finding is scoped to its packet, and the final
report's generalization needs narrowing.

## What the two newest packets read first time

`RUN-2026-09-07-newpackets-3draw.log`, three draws over `inode-exhaustion` and
`redis-oom-noeviction` only. Three draws rather than one because the same day's
full sweep showed greedy decoding at this pin is not reproducible.

| pair | across 3 draws |
| --- | --- |
| `inode-exhaustion/token-free` | discriminates 3 of 3 |
| `inode-exhaustion/version-token` | discriminates 2 of 3; 1 draw rejected the supported record too |
| `redis-oom-noeviction/version-token` | accepted the lure in 2 of 3; the third inverted, rejecting the supported record and accepting the lure |
| `redis-oom-noeviction/token-free` | no usable extraction, 3 of 3 |

Two different failures there, and they are not repaired the same way.

`redis-oom-noeviction/token-free` produced no verdict at all: the extraction
accounts for neither a claim nor a skip on lines 9 and 10 of the record, the same
two lines every draw, while the supported record over the identical text extracts
cleanly. That is a fixture failing to be measurable, which is the case this
directory exists to catch cheaply, and repairing it costs nothing in validity.

`redis-oom-noeviction/version-token` is the opposite. The gate read a record
asserting a server version the evidence never states, and accepted it, in two
draws of three. Nothing is wrong with that pair: it is a miss, and it is a result.
Re-authoring the record until the gate catches it would delete the finding, so the
pair stays as written.

Both halves of the packet share one `probes/supported.txt`, so repairing the
first would re-measure the second. Until the miss above has been read as a
result, that repair is held.

## Two limits

A lure with no distinctive token is invisible to the literal check and reachable
only by the citation check. `go-race-map`'s strongest lure is one of those, and
the manifests mark them rather than implying uniform coverage.

And these packets were authored by the same model family that will be scored
against them. They can measure whether intrusion happens; they cannot establish
that these are the claims a different agent would reach for.
