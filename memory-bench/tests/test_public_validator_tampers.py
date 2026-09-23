"""The adversarial suite the standalone validator is held to.

An adversarial pass over the first release applied nine tampers to the published
records and the shipped validator cleared three of them with exit 0: an expected
value edited in the key alone, an expected value edited in the key AND the gold text
together, and a question swapped for an unrelated one. Its finding was that the
validator "checks structure, leakage and LOO arithmetic; it does not check that the
question, the evidence and the key still agree".

This file is that pass, written down, so the catch rate is a test rather than a
memory. Each tamper is a named edit applied to a COPY of the release under the
pytest tmp directory - ``public/data`` is never written - and each carries the
verdict the validator must reach for it. A rule deleted from the agreement layer
turns a ``True`` here into a failure on the tamper it stopped catching.

A third pass added five more: a pool reordered to lead with the gold, a necessity
verdict flipped, a necessity delta negated, a cross-session count inflated past the
gold set, and a cross-session count contradicting the tier it certifies. All five
cleared the validator, because every rule it had read either the record's structure
or its texts, and none of them read what the record claimed about its own production.

A fourth pass found the cross-session claim itself was arithmetic: ``cross_session_gold``
was ``len(evidence.gold_ids)`` minus 3 on 160 records of 160, and an inflation that
stayed INSIDE the gold set cleared every rule the validator had. bench-record.v3 answers
both halves - the generator makes the gold set a constant width so the arithmetic
returns nothing, and the claim ships as ``cross_session_gold_ids``, a set a validator
can read against the corpus rather than a scalar it can only bound. Eight rows below
exercise the new field, and five of those are the sharp case: exactly one id moved
between the two halves of one record's gold set, nothing else touched. Four of the five
are caught.

The fifth is not, and the reason is structural rather than a rule anybody forgot. A
memory two sequences of a world both need is minted once at world scope and publishes
under one alias in both their records; a memory a sequence wrote for itself appears in
its own record and nowhere else. That repetition is the only published attribution of a
memory to a sequence, so a relabelled gold whose alias does not repeat, whose subject no
sibling grades, and which is not the supersession chain's, leaves no read-time trace at
all. ``test_public_corpus_contract.py`` sweeps every such relabelling in the release and
pins the coverage; here the case ships as its own row, typed as a miss, beside the two
that are caught.

So does the value rewritten consistently across the gold text and the key: the result is
a perfectly well-formed record that simply is not the one that was released. Only the
release digest separates those, so
``test_the_consistent_rewrite_is_caught_by_the_release_digest`` pins that case to
``public/SHA256SUMS`` instead of pretending the validator sees it.

A sixth pass added the last two rows, one in each direction. The caught one is a
shortcut rather than a forgery: close instants permuted WITHIN each subject group so
that every gold is the newest memory of its own subject. Nothing about the record is
false afterwards - the instants are the same instants, the cut is the same cut, the pool
is the same pool in the same order - and an arm that keeps the latest memory per subject
scores a perfect run on it without reading one text. Before ``_recency_problems`` landed
in the validator that edit cleared it on 160 records of 160.

The missed one is the other half of a published figure. Relabelling a gold the other way,
cross-session to session-local, is reported at 0.7407, and that number is two populations
added together: 57 of the 108 demotions available take the record's LAST cross-session
gold, which the tier rule refuses outright, and the other 51 leave a plausible claim
standing and are caught 23 times. Every one of the 28 misses is in the competent half.
The row below is one of them, picked by the trace it leaves rather than by index, and
``test_the_relabel_rate_is_two_populations_not_one`` pins the split so the pooled rate
cannot be read as a rate against a competent tamperer.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from collections import defaultdict
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_DIR = REPO_ROOT / "public"
VALIDATOR_PATH = PUBLIC_DIR / "validator" / "membench_validate.py"
DATA_DIR = PUBLIC_DIR / "data"
SESSION_FILE = "synthetic-session.jsonl"
PROJECT_FILE = "synthetic-project.jsonl"


def _load_validator() -> Any:
    """Import the standalone validator by path, the way a public consumer runs it."""
    spec = importlib.util.spec_from_file_location("membench_validate_tampers", VALIDATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def validator() -> Any:
    return _load_validator()


@pytest.fixture
def release(tmp_path: Path) -> Path:
    """A private copy of the published release, digests included.

    Every tamper edits this copy. Writing ``public/data`` from a test would leave a
    corrupted release behind on any failure, and the whole point of the suite is that
    a tampered file is indistinguishable from a published one until it is checked."""
    out = tmp_path / "data"
    out.mkdir()
    for name in (SESSION_FILE, PROJECT_FILE, "SHA256SUMS"):
        shutil.copy2(DATA_DIR / name, out / name)
    return out


def _read(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


# --- the tampers ------------------------------------------------------------------
#
# Most edit the FIRST record of the session-tier file, whose published ids appear in no
# other record. That is the hard case on purpose: a tamper confined to one record cannot
# be caught by cross-record agreement, so what catches it is the rule under test and
# nothing else.
#
# The cross-session rows are the exception and have to be. The field is empty on every
# session record, so a session-file edit can only test the tier rule; the claim itself
# only exists on the project tier, where the world's other records are what it is
# checked against. Those rows say so by naming PROJECT_FILE in the table, and they pick
# their target by the trace it leaves rather than by index, so a regenerated release
# cannot silently retype a row to an easier case.


def _first(records: list[dict[str, Any]]) -> dict[str, Any]:
    return records[0]


def _project_first(records: list[dict[str, Any]]) -> dict[str, Any]:
    """The first record of the project file, which is where a cross-session edit lands.

    A session record has nothing to relabel: its cross-session set is empty and the tier
    rule refuses a non-empty one outright."""
    record = records[0]
    assert record["tier"] == "project", f"{record['record_id']} is tier {record['tier']!r}"
    return record


def _subject_of(text: str) -> str:
    """The subject a published fact is about, read off the text the same way the
    validator reads it: everything before the ``ss is `` the generator writes."""
    head, _, _ = text.partition(" is ")
    assert head and head != text, f"no subject clause in {text!r}"
    return head


def _relabel_target(
    records: list[dict[str, Any]], *, chain: bool, sibling_local: bool
) -> tuple[dict[str, Any], str]:
    """The first (record, session-local gold) pair with the asked-for read-time trace.

    Three traces can survive a gold being relabelled cross-session, and each row above
    picks one so the rule that catches it is named rather than guessed at:

    * ``chain`` - the record publishes a superseded value for that gold's subject;
    * ``sibling_local`` - another record of the same scope grades that subject as its
      own sequence's work;
    * neither, and the alias does not repeat in the scope - nothing published attributes
      it to any sequence, which is the miss this suite ships as a row.

    Searched in file order and asserted rather than defaulted: a release that stopped
    offering one of the three would otherwise quietly retype the row it belongs to."""
    by_scope: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_scope[record["question"]["scope_id"]].append(record)

    for record in records:
        if record["tier"] != "project":
            continue
        cross = set(record["provenance"]["cross_session_gold_ids"])
        gold: Mapping[str, str] = record["evidence"]["gold"]
        stale_subjects = {_subject_of(text) for text in record["evidence"]["superseded"].values()}
        siblings = [
            other
            for other in by_scope[record["question"]["scope_id"]]
            if other["record_id"] != record["record_id"]
        ]
        sibling_subjects = {
            _subject_of(other["evidence"]["gold"][memory_id])
            for other in siblings
            for memory_id in other["evidence"]["gold_ids"]
            if memory_id not in set(other["provenance"]["cross_session_gold_ids"])
        }
        sibling_ids = {
            memory_id for other in siblings for memory_id in other["candidate_pool"]["ids"]
        }
        for memory_id in record["evidence"]["gold_ids"]:
            if memory_id in cross:
                continue
            subject = _subject_of(gold[memory_id])
            if (subject in stale_subjects) != chain:
                continue
            if (subject in sibling_subjects) != sibling_local:
                continue
            if not chain and not sibling_local and memory_id in sibling_ids:
                continue
            return record, memory_id
    raise AssertionError(
        f"no project record offers a gold with chain={chain}, sibling_local={sibling_local}"
    )


def _forbidden_field_injected(records: list[dict[str, Any]]) -> None:
    """A published record carrying the commit it came from."""
    _first(records)["provenance"]["commit_sha"] = "0" * 40


def _answer_value_leaked_into_question(records: list[dict[str, Any]]) -> None:
    """The key's own value pasted into the question, so the question scores itself.

    Prefixed rather than appended, so the question keeps its trailing subject clause
    and this tamper tests the leak scan alone."""
    record = _first(records)
    value = record["answer"]["expected_values"][0]
    record["question"]["text"] = f"Context: the value is {value}. {record['question']['text']}"


def _loo_boundary_moved(records: list[dict[str, Any]]) -> None:
    """The cut walked backwards, so the published pool holds candidates that closed
    after the question was asked. ``asked_at`` is moved with it, which is what a
    tamperer who read the validator would do."""
    record = _first(records)
    instants = sorted(c["closed"] for c in record["loo"]["candidates"] if c["closed"] is not None)
    moved = instants[len(instants) // 2]
    record["loo"]["boundary"] = moved
    record["question"]["asked_at"] = moved


def _pool_id_smuggled(records: list[dict[str, Any]]) -> None:
    """The excluded query itself added to the pool an arm is allowed to see."""
    record = _first(records)
    record["candidate_pool"]["ids"] = sorted(
        [*record["candidate_pool"]["ids"], record["loo"]["query"]["id"]]
    )


def _evidence_bucket_overlap(records: list[dict[str, Any]]) -> None:
    """A gold entry republished as a distractor, so the record grades its own trap."""
    record = _first(records)
    gold_id = record["evidence"]["gold_ids"][0]
    record["evidence"]["distractors"][gold_id] = record["evidence"]["gold"][gold_id]


def _close_instants_collapsed(records: list[dict[str, Any]]) -> None:
    """Two candidates given one close instant, which groups them on the grid."""
    record = _first(records)
    closed = [c for c in record["loo"]["candidates"] if c["closed"] is not None]
    closed[1]["closed"] = closed[0]["closed"]


def _gold_value_changed_in_answer_only(records: list[dict[str, Any]]) -> None:
    """The key edited away from the evidence: the record now grades a value nothing
    in the pool carries, so a perfect retrieval scores zero."""
    record = _first(records)
    record["answer"]["expected_values"][0] = "9000s"


def _question_string_replaced(records: list[dict[str, Any]]) -> None:
    """The question swapped for an unrelated one: the agent is asked about subjects
    the key never grades, and graded on subjects it was never asked about."""
    donor = next(
        candidate
        for candidate in records[1:]
        if candidate["question"]["text"] != records[0]["question"]["text"]
    )
    records[0]["question"]["text"] = donor["question"]["text"]


def _pool_reordered_gold_first(records: list[dict[str, Any]]) -> None:
    """The pool re-sorted to lead with the gold, membership untouched.

    Every set-level rule above still passes: the pool holds exactly the eligible ids,
    the buckets still union to it, nothing is in two roles. Only the order moved, and
    the order is what `public/README.md` publishes as the seeding order, so this turns
    any runner that truncates its pool into a perfect retriever."""
    record = _first(records)
    gold = set(record["evidence"]["gold_ids"])
    ids = record["candidate_pool"]["ids"]
    record["candidate_pool"]["ids"] = [i for i in ids if i in gold] + [
        i for i in ids if i not in gold
    ]


def _necessity_verdict_flipped(records: list[dict[str, Any]]) -> None:
    """The gate's verdict turned around under rewards that still say the opposite.

    `accepted` is `delta > epsilon` by construction, so flipping it alone leaves the
    record asserting a decision its own three numbers refuse. Nothing re-ran the gate,
    and nothing can: a downloader cannot repeat a two-arm sweep."""
    _first(records)["necessity"]["accepted"] = False


def _necessity_delta_sign_flipped(records: list[dict[str, Any]]) -> None:
    """The margin negated while the two rewards it was computed from stay put.

    A negative delta with `accepted` true is a record claiming the no-memory arm won
    and publishing itself anyway."""
    necessity = _first(records)["necessity"]
    necessity["delta"] = -necessity["delta"]


def _cross_session_set_names_a_non_gold_id(records: list[dict[str, Any]]) -> None:
    """The cross-session set naming an id the record does not publish as gold.

    The field names WHICH gold memories another sequence wrote, so it is a subset of
    the gold set by definition. A record that names one outside it is claiming evidence
    it does not carry, and a consumer ranking records by difficulty would rank on it."""
    record = _first(records)
    record["provenance"]["cross_session_gold_ids"] = [record["evidence"]["superseded_ids"][0]]


def _cross_session_set_contradicts_tier(records: list[dict[str, Any]]) -> None:
    """A session-tier record claiming a cross-session gold fact.

    The set is the evidence for the tier, so a non-empty one on a session record is the
    two fields naming different scopes. It stays inside the gold set, so the subset rule
    above does not see it."""
    _first(records)["provenance"]["cross_session_gold_ids"] = [
        _first(records)["evidence"]["gold_ids"][0]
    ]


def _cross_session_set_swallows_the_local_gold(records: list[dict[str, Any]]) -> None:
    """Every gold but one declared cross-session, on a project record.

    A goal grades at least ``MIN_LOCAL_GOLD`` subjects its own sequence established -
    one of them carries the supersession chain, and a goal with a single local subject
    would make "which subject was superseded" free. A set this long claims a
    construction the generator refuses to build."""
    record = _project_first(records)
    record["provenance"]["cross_session_gold_ids"] = sorted(record["evidence"]["gold_ids"][1:])


def _cross_session_set_out_of_order(records: list[dict[str, Any]]) -> None:
    """The set published in descending alias order.

    Every published id list is in ascending alias order, which is what keeps "the first
    one" from meaning anything. A producer free to choose the order is a producer with
    a channel the schema does not describe."""
    record = next(r for r in records if len(r["provenance"]["cross_session_gold_ids"]) > 1)
    record["provenance"]["cross_session_gold_ids"] = sorted(
        record["provenance"]["cross_session_gold_ids"], reverse=True
    )


def _cross_session_set_claims_the_chain_subject(records: list[dict[str, Any]]) -> None:
    """A supersession chain's surviving version relabelled as another session's work.

    A chain is authored inside one sequence, version by version, and the record ships
    the earlier versions as ``evidence.superseded``. So the gold answering that subject
    is that sequence's own on either tier, and naming it cross-session contradicts the
    trap the record publishes beside it."""
    record, memory_id = _relabel_target(records, chain=True, sibling_local=False)
    record["provenance"]["cross_session_gold_ids"] = sorted(
        {*record["provenance"]["cross_session_gold_ids"], memory_id}
    )


def _cross_session_set_claims_a_sibling_local_subject(records: list[dict[str, Any]]) -> None:
    """A gold relabelled cross-session whose subject the world's other record keeps.

    A world draws its shared decisions OUT of the pool its sequences draw local subjects
    from, so within a scope a subject is world-shared or it is nobody's but its own
    sequence's. A subject cross-session in one record and session-local in another is a
    world that shared a subject it also kept."""
    record, memory_id = _relabel_target(records, chain=False, sibling_local=True)
    record["provenance"]["cross_session_gold_ids"] = sorted(
        {*record["provenance"]["cross_session_gold_ids"], memory_id}
    )


def _cross_session_set_claims_an_unreachable_subject(records: list[dict[str, Any]]) -> None:
    """The same relabelling, on the gold that leaves no trace. THIS ONE IS NOT CAUGHT.

    The round-4 audit's confirmed miss, carried over: ``world-seed100-task1`` with its
    cross-session claim inflated by one, inside every bound the validator checks. The
    alias does not repeat in the world's other record, no sibling grades its subject,
    and it is not the chain's, so nothing published attributes it to a sequence. It is
    here so the gap is a row rather than a footnote, and
    ``test_public_corpus_contract.py`` measures how much of the release it covers."""
    record, memory_id = _relabel_target(records, chain=False, sibling_local=False)
    record["provenance"]["cross_session_gold_ids"] = sorted(
        {*record["provenance"]["cross_session_gold_ids"], memory_id}
    )


def _cross_session_set_disowns_a_shared_memory(records: list[dict[str, Any]]) -> None:
    """A world-scoped memory relabelled as the record's own work.

    The other direction, and the one the corpus does fix: a memory two sequences of a
    world need is minted once and published under one alias in both their records. An id
    the release publishes twice cannot be a gold only one sequence wrote."""
    by_scope: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_scope[record["question"]["scope_id"]].append(record)
    for record in records:
        cross = record["provenance"]["cross_session_gold_ids"]
        if len(cross) < 2:
            continue
        elsewhere = {
            memory_id
            for other in by_scope[record["question"]["scope_id"]]
            if other["record_id"] != record["record_id"]
            for memory_id in other["candidate_pool"]["ids"]
        }
        shared = [memory_id for memory_id in cross if memory_id in elsewhere]
        if shared:
            record["provenance"]["cross_session_gold_ids"] = [
                memory_id for memory_id in cross if memory_id != shared[0]
            ]
            return
    raise AssertionError("no project record publishes two cross-session golds, one repeated")


def _forbidden_values_emptied(records: list[dict[str, Any]]) -> None:
    """The trap set left in the pool and struck from the key.

    Every structural rule still passes: the stale texts ship, they sit in the pool, the
    buckets still union to it, the gold is untouched. What stops is the grading - an
    answer that states a superseded value costs nothing now - so every arm's reward
    inflates and the staleness axis silently stops being measured. The one-sided rule
    "no gold states a forbidden value" is vacuous on an empty list; its converse, that
    every published stale text is itself forbidden, is what sees this."""
    _first(records)["answer"]["forbidden_values"] = []


def _check_kind_flipped(records: list[dict[str, Any]]) -> None:
    """``value-set`` relabelled ``tool-call``, so the key is graded by the wrong rule.

    A tool-call record grades one value, the call it expects; a value-set record grades
    one per gold subject. The label picks the grader, so flipping it on a record that
    ships five expected values is a record whose key nothing will read correctly."""
    _first(records)["answer"]["check_kind"] = "tool-call"


def _expected_values_padded(records: list[dict[str, Any]]) -> None:
    """A duplicate expected value appended, so the key grades more values than it has
    gold to carry them.

    Each graded subject contributes exactly one expected value, so the count is fixed by
    the gold set. A padded key makes a partial answer look complete to a scorer that
    counts matches."""
    record = _first(records)
    record["answer"]["expected_values"] = [
        *record["answer"]["expected_values"],
        record["answer"]["expected_values"][0],
    ]


def _scope_id_rewritten(records: list[dict[str, Any]]) -> None:
    """A record moved into a scope of its own.

    ``question.scope_id`` is the scope the answer must span, and it is what every
    cross-record rule groups by. A record with a scope nobody shares is a record no
    sibling can contradict, which makes the whole scope layer vacuous one record at a
    time."""
    _first(records)["question"]["scope_id"] = "world-not-in-this-release"


def _provenance_sequence_id_mismatched(records: list[dict[str, Any]]) -> None:
    """The provenance pointed at a different sequence from the record it rides on."""
    _first(records)["provenance"]["sequence_id"] = records[1]["record_id"]


def _leak_guard_version_backdated(records: list[dict[str, Any]]) -> None:
    """A v3 record claiming it was cleared by the v2 guard.

    The guard is versioned with the schema, so this is a record asserting it was checked
    by a rule set that did not have the checks its own schema version requires."""
    _first(records)["leak_guard"]["validator_version"] = "membench-validate.v2"


def _distractor_subject_swapped(records: list[dict[str, Any]]) -> None:
    """A distractor rewritten to be about a subject the record never grades.

    A distractor exists to be a plausible wrong answer to the question that was asked.
    One about an ungraded subject is filler: it cannot be mistaken for the answer, so it
    inflates the pool without stressing retrieval, and precision at a fixed width reads
    higher than the record earns."""
    record = _first(records)
    graded = {_subject_of(text) for text in record["evidence"]["gold"].values()}
    donor = next(
        text
        for other in records[1:]
        for text in other["evidence"]["gold"].values()
        if _subject_of(text) not in graded
    )
    memory_id = sorted(record["evidence"]["distractors"])[0]
    record["evidence"]["distractors"][memory_id] = donor


def _untraceable_demotion_target(records: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    """The first (project record, cross-session gold) pair whose demotion leaves no trace.

    Two published things can contradict a gold being relabelled session-local, and this
    picks a gold with neither, so the row is typed a miss by what the release carries and
    not by what the validator happens to do today:

    * the alias is published by another record, which makes a session-local claim about
      it a claim two records both make about one memory;
    * another record of the same scope grades that gold's SUBJECT as cross-session,
      and a world shares a subject or keeps it.

    The record must also keep a non-empty claim, or the tier rule refuses it without
    reading anything else - that is the trivial half of the published rate, and it is
    exactly what this row is here not to be."""
    by_scope: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_scope[record["question"]["scope_id"]].append(record)

    for record in records:
        if record["tier"] != "project":
            continue
        cross = list(record["provenance"]["cross_session_gold_ids"])
        if len(cross) < 2:
            continue
        siblings = [
            other
            for other in by_scope[record["question"]["scope_id"]]
            if other["record_id"] != record["record_id"]
        ]
        published_elsewhere = {
            memory_id
            for other in records
            if other["record_id"] != record["record_id"]
            for bucket in ("gold", "distractors", "superseded")
            for memory_id in other["evidence"][bucket]
        }
        sibling_cross_subjects = {
            _subject_of(other["evidence"]["gold"][memory_id])
            for other in siblings
            for memory_id in other["provenance"]["cross_session_gold_ids"]
        }
        for memory_id in cross:
            if memory_id in published_elsewhere:
                continue
            if _subject_of(record["evidence"]["gold"][memory_id]) in sibling_cross_subjects:
                continue
            return record, memory_id
    raise AssertionError("no project record offers a cross-session gold with no read-time trace")


def _cross_session_set_shrunk_to_a_plausible_claim(records: list[dict[str, Any]]) -> None:
    """One cross-session gold relabelled session-local, leaving a claim that still stands.

    THIS ONE IS NOT CAUGHT, and it is the case the published demotion rate is mostly not
    about. Dropping a record's only cross-session gold is caught by the tier rule and
    needs nothing else; dropping one of two, on a gold nothing published attributes to a
    sequence, is caught 23 times in 51 across the release and not here."""
    record, memory_id = _untraceable_demotion_target(records)
    record["provenance"]["cross_session_gold_ids"] = [
        other for other in record["provenance"]["cross_session_gold_ids"] if other != memory_id
    ]


def _gold_moved_to_every_subject_latest_slot(records: list[dict[str, Any]]) -> None:
    """Close instants permuted within each subject group, so every gold closes last.

    The record stays true about everything it states. Only pool members move, only among
    the instants of their own subject group, so the instant multiset is unchanged, every
    instant is still strictly before the boundary, no two collide, the eligible set is
    the same set and the published pool is still in the order ``pool_order`` fixes. What
    changes is that "keep the newest memory of each subject" now returns the gold set
    exactly, which answers the record off the grid without retrieving anything."""
    record = _first(records)
    texts = {
        memory_id: text
        for bucket in ("gold", "distractors", "superseded")
        for memory_id, text in record["evidence"][bucket].items()
    }
    groups: dict[str, list[str]] = defaultdict(list)
    for memory_id in record["candidate_pool"]["ids"]:
        groups[_subject_of(texts[memory_id])].append(memory_id)

    gold = set(record["evidence"]["gold_ids"])
    closed = {c["id"]: c["closed"] for c in record["loo"]["candidates"]}
    moved: dict[str, str] = {}
    for ids in groups.values():
        instants = sorted(closed[memory_id] for memory_id in ids)
        targets = [memory_id for memory_id in ids if memory_id in gold]
        assert len(targets) == 1, f"{record['record_id']} has {len(targets)} golds in one group"
        others = sorted(memory_id for memory_id in ids if memory_id not in gold)
        moved.update(dict(zip(others, instants[:-1], strict=True)))
        moved[targets[0]] = instants[-1]
    for candidate in record["loo"]["candidates"]:
        if candidate["id"] in moved:
            candidate["closed"] = moved[candidate["id"]]


def _record_id_duplicated(records: list[dict[str, Any]]) -> None:
    """One record published twice in one file.

    A duplicated record is counted twice by every aggregate a consumer computes, and the
    id it duplicates is what a join keys on."""
    records.append(json.loads(json.dumps(records[0])))


def _gold_value_changed_consistently(records: list[dict[str, Any]]) -> None:
    """The evidence AND the key edited together. The record stays self-consistent -
    it is a valid record of a different task - so agreement cannot see it."""
    record = _first(records)
    gold_id = record["evidence"]["gold_ids"][0]
    text = record["evidence"]["gold"][gold_id]
    # The pair is looked up rather than assumed: an edit that moved the key without
    # moving the text would silently become the answer-only tamper above.
    index, old = next(
        (i, value) for i, value in enumerate(record["answer"]["expected_values"]) if value in text
    )
    record["evidence"]["gold"][gold_id] = text.replace(old, "9000s")
    record["answer"]["expected_values"][index] = "9000s"


# (name, the file the edit lands in, edit, the validator must catch it)
_TAMPERS: tuple[tuple[str, str, Callable[[list[dict[str, Any]]], None], bool], ...] = (
    ("forbidden-field-injected", SESSION_FILE, _forbidden_field_injected, True),
    ("answer-value-leaked-into-question", SESSION_FILE, _answer_value_leaked_into_question, True),
    ("loo-boundary-moved", SESSION_FILE, _loo_boundary_moved, True),
    ("pool-id-smuggled", SESSION_FILE, _pool_id_smuggled, True),
    ("evidence-bucket-overlap", SESSION_FILE, _evidence_bucket_overlap, True),
    ("close-instants-collapsed", SESSION_FILE, _close_instants_collapsed, True),
    ("pool-reordered-gold-first", SESSION_FILE, _pool_reordered_gold_first, True),
    ("necessity-verdict-flipped", SESSION_FILE, _necessity_verdict_flipped, True),
    ("necessity-delta-sign-flipped", SESSION_FILE, _necessity_delta_sign_flipped, True),
    (
        "cross-session-set-names-a-non-gold-id",
        SESSION_FILE,
        _cross_session_set_names_a_non_gold_id,
        True,
    ),
    ("cross-session-set-contradicts-tier", SESSION_FILE, _cross_session_set_contradicts_tier, True),
    (
        "cross-session-set-swallows-the-local-gold",
        PROJECT_FILE,
        _cross_session_set_swallows_the_local_gold,
        True,
    ),
    ("cross-session-set-out-of-order", PROJECT_FILE, _cross_session_set_out_of_order, True),
    (
        "cross-session-set-claims-the-chain-subject",
        PROJECT_FILE,
        _cross_session_set_claims_the_chain_subject,
        True,
    ),
    (
        "cross-session-set-claims-a-sibling-local-subject",
        PROJECT_FILE,
        _cross_session_set_claims_a_sibling_local_subject,
        True,
    ),
    (
        "cross-session-set-disowns-a-shared-memory",
        PROJECT_FILE,
        _cross_session_set_disowns_a_shared_memory,
        True,
    ),
    ("forbidden-values-emptied", SESSION_FILE, _forbidden_values_emptied, True),
    ("check-kind-flipped", SESSION_FILE, _check_kind_flipped, True),
    ("expected-values-padded", SESSION_FILE, _expected_values_padded, True),
    ("scope-id-rewritten", SESSION_FILE, _scope_id_rewritten, True),
    ("provenance-sequence-id-mismatched", SESSION_FILE, _provenance_sequence_id_mismatched, True),
    ("leak-guard-version-backdated", SESSION_FILE, _leak_guard_version_backdated, True),
    ("distractor-subject-swapped", SESSION_FILE, _distractor_subject_swapped, True),
    ("record-id-duplicated", SESSION_FILE, _record_id_duplicated, True),
    ("gold-value-changed-in-answer-only", SESSION_FILE, _gold_value_changed_in_answer_only, True),
    ("question-string-replaced", SESSION_FILE, _question_string_replaced, True),
    (
        "cross-session-set-claims-an-unreachable-subject",
        PROJECT_FILE,
        _cross_session_set_claims_an_unreachable_subject,
        False,
    ),
    ("gold-value-changed-consistently", SESSION_FILE, _gold_value_changed_consistently, False),
    (
        "gold-moved-to-every-subject-latest-slot",
        SESSION_FILE,
        _gold_moved_to_every_subject_latest_slot,
        True,
    ),
    (
        "cross-session-set-shrunk-to-a-plausible-claim",
        PROJECT_FILE,
        _cross_session_set_shrunk_to_a_plausible_claim,
        False,
    ),
)


def _apply(
    release_dir: Path,
    edit: Callable[[list[dict[str, Any]]], None],
    name: str = SESSION_FILE,
) -> None:
    path = release_dir / name
    records = _read(path)
    edit(records)
    _write(path, records)


def _validate(validator: Any, release_dir: Path) -> Mapping[str, Any]:
    return validator.validate_release(
        [release_dir / SESSION_FILE, release_dir / PROJECT_FILE],
        schema_path=PUBLIC_DIR / "schema" / f"{validator.SCHEMA_VERSION}.schema.json",
    )


def test_the_pristine_release_validates(validator: Any, release: Path) -> None:
    """The baseline every tamper below is measured against. A suite whose pristine
    case already fails proves nothing about what the edits did."""
    report = _validate(validator, release)
    assert report["ok"], report
    assert [r["n_records"] for r in report["reports"]] == [80, 80]
    assert all(r["n_failed"] == 0 for r in report["reports"])


@pytest.mark.parametrize(
    ("name", "file_name", "edit", "detected"), _TAMPERS, ids=[case[0] for case in _TAMPERS]
)
def test_tamper(
    validator: Any,
    release: Path,
    name: str,
    file_name: str,
    edit: Callable[[list[dict[str, Any]]], None],
    detected: bool,
) -> None:
    _apply(release, edit, file_name)
    report = _validate(validator, release)
    assert report["ok"] is not detected, (
        f"{name}: expected the validator to {'reject' if detected else 'clear'} it; "
        f"got ok={report['ok']}"
    )


def test_the_catch_rate_is_twenty_seven_of_thirty() -> None:
    """The rate itself, pinned, and split where the table can check the split. Six of the
    nine tampers in the first pass, eight of nine once the agreement layer landed.
    Twenty-one rows below say nothing about ``cross_session_gold_ids`` and twenty of those
    are caught; the nine that exercise the field are caught seven times, which is
    twenty-seven of thirty. The split ships as assertions rather than as prose alone, so a
    number in a sentence cannot drift away from the rows it counts.

    The three misses are named, not rounded away: a value rewritten consistently across
    the gold text and the key, a gold relabelled cross-session whose alias does not repeat
    and whose subject no sibling grades, and the same relabelling in the other direction
    on a record that keeps a claim standing. All three are records that are internally
    sound and are simply not the ones that were released, which is what the digest is for.
    A rule removed from the validator flips a row above, and a row quietly retyped to match
    a weakened validator fails here."""
    assert len(_TAMPERS) == 30
    assert sum(1 for _, _, _, detected in _TAMPERS if detected) == 27
    field_rows = [row for row in _TAMPERS if row[0].startswith("cross-session-set-")]
    other_rows = [row for row in _TAMPERS if not row[0].startswith("cross-session-set-")]
    assert len(field_rows) == 9
    assert sum(1 for _, _, _, detected in field_rows if detected) == 7
    assert len(other_rows) == 21
    assert sum(1 for _, _, _, detected in other_rows if detected) == 20
    assert sorted(name for name, _, _, detected in _TAMPERS if not detected) == [
        "cross-session-set-claims-an-unreachable-subject",
        "cross-session-set-shrunk-to-a-plausible-claim",
        "gold-value-changed-consistently",
    ]


def _released() -> list[dict[str, Any]]:
    """Every published record, project file after session file."""
    return [*_read(DATA_DIR / SESSION_FILE), *_read(DATA_DIR / PROJECT_FILE)]


def _relabel_split(validator: Any) -> dict[str, tuple[int, int]]:
    """Every single-id relabelling available in the release, caught count over count.

    Four populations, because two of them are refused by a rule that reads one field and
    needs nothing from the corpus, and averaging those in is what made one number look
    like a rate against a competent tamperer:

    * ``empties-the-claim`` - the demotion that takes a project record's last
      cross-session gold, refused by the tier rule;
    * ``competent-shrink`` - a demotion that leaves a claim standing;
    * ``under-min-local`` - the promotion that takes the gold set below
      ``MIN_LOCAL_GOLD``, refused by the local-floor rule;
    * ``competent-inflate`` - a promotion that leaves the floor intact."""
    records = _released()
    base = [validator.scope_claims(record) for record in records]

    def caught(index: int, cross: set[str]) -> bool:
        record = records[index]
        tampered = {
            **record,
            "provenance": {**record["provenance"], "cross_session_gold_ids": sorted(cross)},
        }
        if validator._cross_session_problems(tampered) + validator._identity_problems(tampered):
            return True
        claims = list(base)
        claims[index] = validator.scope_claims(tampered)
        return bool(validator.scope_problems(claims))

    tally = {
        name: [0, 0]
        for name in (
            "empties-the-claim",
            "competent-shrink",
            "under-min-local",
            "competent-inflate",
        )
    }
    for index, record in enumerate(records):
        if record["tier"] != "project":
            continue
        cross = set(record["provenance"]["cross_session_gold_ids"])
        local = [g for g in record["evidence"]["gold_ids"] if g not in cross]
        for memory_id in sorted(cross):
            cell = tally["empties-the-claim" if len(cross) == 1 else "competent-shrink"]
            cell[1] += 1
            cell[0] += caught(index, cross - {memory_id})
        for memory_id in sorted(local):
            below = len(local) - 1 < validator.MIN_LOCAL_GOLD
            cell = tally["under-min-local" if below else "competent-inflate"]
            cell[1] += 1
            cell[0] += caught(index, cross | {memory_id})
    return {name: (caught_n, total) for name, (caught_n, total) in tally.items()}


def test_the_relabel_rate_is_two_populations_not_one(validator: Any) -> None:
    """The published coverage figures, decomposed into the halves they average.

    0.7407 of demotions and 0.6815 of promotions are both real and both dominated by a
    move a competent tamperer does not make. Emptying a project record's cross-session
    claim is caught 57 times in 57, by a rule that reads one field: a project record is
    cross-session or it is not a project record. Taking the gold set below the local floor
    is caught 10 in 10 the same way. Strip those and the rate against someone who keeps
    the record plausible is 0.4510 for demotions and 0.6702 for promotions.

    Pinned as exact counts because the point is the decomposition, not the ratio, and
    because the pooled figures are what ``public/README.md`` and the validator's own
    docstring state. A corpus regenerated to a different shape moves all six numbers
    together, and the prose that quotes them has to move with this test."""
    split = _relabel_split(validator)
    assert split["empties-the-claim"] == (57, 57)
    assert split["competent-shrink"] == (23, 51)
    assert split["under-min-local"] == (10, 10)
    assert split["competent-inflate"] == (189, 282)

    demotion = tuple(
        sum(part)
        for part in zip(split["empties-the-claim"], split["competent-shrink"], strict=True)
    )
    promotion = tuple(
        sum(part) for part in zip(split["under-min-local"], split["competent-inflate"], strict=True)
    )
    assert demotion == (80, 108)
    assert promotion == (199, 292)
    assert demotion[0] - split["empties-the-claim"][0] == 23, (
        "every demotion miss must be in the competent half; a miss that empties the claim "
        "would mean the tier rule stopped firing"
    )


def test_the_consistent_rewrite_is_caught_by_the_release_digest(release: Path) -> None:
    """The one tamper the validator clears, caught where it can be caught.

    ``public/README.md`` tells a downloader to run ``sha256sum -c SHA256SUMS`` before
    the validator, and this is the case that instruction exists for."""
    published = {
        line.split()[1]: line.split()[0]
        for line in (release / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    assert (
        published[SESSION_FILE] == hashlib.sha256((release / SESSION_FILE).read_bytes()).hexdigest()
    )
    _apply(release, _gold_value_changed_consistently)
    assert (
        published[SESSION_FILE] != hashlib.sha256((release / SESSION_FILE).read_bytes()).hexdigest()
    )


def test_a_consistent_rewrite_of_a_shared_memory_is_caught(validator: Any, release: Path) -> None:
    """Where a consistently rewritten memory is ALSO published by another record, the
    one-id-one-memory rule does see it.

    The project tier publishes the charter its world established once as gold under
    BOTH of that world's records, so that id carries one text in two places. Editing
    one copy makes the release contradict itself, which is the only read-time signal
    a consistent rewrite ever leaves - and the reason the nine-tamper suite edits a
    record whose ids appear nowhere else."""
    project = _read(release / PROJECT_FILE)
    holders: dict[str, list[dict[str, Any]]] = {}
    for record in project:
        for memory_id in record["evidence"]["gold_ids"]:
            holders.setdefault(memory_id, []).append(record)
    memory_id, records = next((mid, rs) for mid, rs in sorted(holders.items()) if len(rs) > 1)
    record = records[0]
    value = next(
        v
        for v in record["answer"]["expected_values"]
        if validator.states_value(record["evidence"]["gold"][memory_id], v)
    )
    record["evidence"]["gold"][memory_id] = record["evidence"]["gold"][memory_id].replace(
        value, "9000s"
    )
    record["answer"]["expected_values"][record["answer"]["expected_values"].index(value)] = "9000s"
    _write(release / PROJECT_FILE, project)

    report = _validate(validator, release)
    assert not report["ok"]
    problems = [
        *report["release_problems"],
        *(p for r in report["reports"] for p in r["file_problems"]),
    ]
    assert any("is published with two different texts" in problem for problem in problems), problems


def _problems(report: Mapping[str, Any]) -> list[str]:
    """Every finding the run produced, flattened."""
    return [
        *report["release_problems"],
        *(p for r in report["reports"] for p in r["file_problems"]),
        *(p for r in report["reports"] for f in r["failures"] for p in f["problems"]),
    ]


def test_the_forbidden_field_scan_reports_the_field_it_found(validator: Any, release: Path) -> None:
    """The scan names the leaked field, which is what makes it more than a schema echo.

    Every object in bench-record.v3 sets ``additionalProperties: false``, so a record
    carrying ``commit_sha`` trips the schema too - and behind the schema's early return
    the scan never ran at all. Replacing its body with ``for path in ()`` left all of
    this file green. It runs ahead of the schema now, so the leak is reported by name
    and by path rather than as an unexpected-property message."""
    _apply(release, _forbidden_field_injected)
    problems = _problems(_validate(validator, release))
    assert "forbidden field published at provenance.commit_sha" in problems, problems


def test_the_forbidden_field_scan_is_not_dead_code(validator: Any, release: Path) -> None:
    """The mutant that proves it. Stubbing the scan out must cost a finding.

    ``_forbidden_fields`` is rebound to a no-op for the length of this test, which is
    exactly the mutation that used to leave the suite green. The schema still rejects
    the record, so the run still fails; what disappears is the named finding, and that
    difference is the whole value of the scan."""
    _apply(release, _forbidden_field_injected)
    original = validator._forbidden_fields
    validator._forbidden_fields = lambda node, path="": []
    try:
        mutant = _problems(_validate(validator, release))
    finally:
        validator._forbidden_fields = original
    live = _problems(_validate(validator, release))

    assert "forbidden field published at provenance.commit_sha" in live
    assert "forbidden field published at provenance.commit_sha" not in mutant
    assert any(p.startswith("schema: ") for p in mutant), mutant


def test_the_pool_order_check_is_not_dead_code(validator: Any, release: Path) -> None:
    """The mutant for the order rule: make ``pool_order`` the identity and the
    reordered pool walks through.

    Membership is unchanged by that tamper, so every other rule in the file clears it.
    The order check is the only thing standing between a reordered release and a
    runner that truncates its pool."""
    _apply(release, _pool_reordered_gold_first)
    original = validator.pool_order
    validator.pool_order = lambda record_id, ids: list(ids)
    try:
        mutant = _validate(validator, release)
    finally:
        validator.pool_order = original

    assert mutant["ok"], "the identity mutant must clear the reorder, or this proves nothing"
    assert not _validate(validator, release)["ok"]


def test_the_published_pool_is_in_the_normative_order(validator: Any) -> None:
    """The release itself, held to the rule. A validator that enforces an order the
    exporter does not write would fail every record, and one that enforces nothing
    would pass a release in any order at all."""
    for name in (SESSION_FILE, PROJECT_FILE):
        for record in _read(DATA_DIR / name):
            ids = record["candidate_pool"]["ids"]
            assert ids == validator.pool_order(record["record_id"], ids), record["record_id"]
            assert ids != sorted(ids) or len(ids) == 1, (
                f"{record['record_id']}: the normative order happens to equal ascending "
                "id order here, so this record cannot tell the two apart"
            )
