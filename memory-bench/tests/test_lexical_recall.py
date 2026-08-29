"""Tests for the lexical-miss recall experiment (mem-lbuvd).

The guard tests here are written so they fail when the guard is removed. A test
that passes whether or not the defence works is worse than no test, because it
reports coverage it does not have.
"""

from __future__ import annotations

import pytest

from membench.beads_ordering.models import MemoryFixture, OrderingTask
from membench.lexical_recall.corpus import (
    MissCorpusError,
    as_seedable_corpus,
    build_frozen_corpus,
    corpus_digest,
    literal_matches,
    validate_miss_construction,
)
from membench.lexical_recall.generators import (
    Fts5Generator,
    GeneratorError,
    document_text,
    fts_match_expression,
)
from membench.lexical_recall.models import (
    FrozenMissCorpus,
    Generator,
    LexicalMissTask,
    MissKind,
    TaskClass,
    TaskRecall,
)
from membench.lexical_recall.runner import (
    LexicalRecallError,
    TaskSpec,
    control_specs,
    g1_verdict,
    g2_verdict,
    gate_verdicts,
    measure,
    miss_specs,
    recall_summary,
    run_class,
    validate_control_truth,
    validate_lexical_miss_truth,
)
from membench.lexical_recall.scenarios import PAIRS_BY_KIND


def test_every_pair_is_a_genuine_miss_before_any_corpus_is_built() -> None:
    """Neither member of a pair may contain the other, or the substring matcher
    would find the gold Memory and the task would not be a miss at all."""

    for kind, pairs in PAIRS_BY_KIND.items():
        assert len(pairs) == 9, kind
        for query, surface_form in pairs:
            assert query.lower() not in surface_form.lower(), (kind, query)
            assert surface_form.lower() not in query.lower(), (kind, query)


def test_frozen_corpus_shape() -> None:
    corpus = build_frozen_corpus()
    assert len(corpus.tasks) == 36
    assert len(corpus.memories) == 680
    assert len({task.task_id for task in corpus.tasks}) == 36
    assert len({memory.id for memory in corpus.memories}) == 680


def test_matched_k_is_not_a_single_constant() -> None:
    """A budget that is the same on every task cannot show how recall responds to
    it, so the distractor count is deliberately varied."""

    corpus = build_frozen_corpus()
    sizes = {len(literal_matches(corpus, task.query)) for task in corpus.tasks}
    assert sizes == {2, 3, 4}


def test_the_corpus_is_frozen_at_one_seed() -> None:
    with pytest.raises(MissCorpusError, match="frozen at seed"):
        build_frozen_corpus(seed=1)


def test_corpus_digest_is_stable_across_builds() -> None:
    assert corpus_digest(build_frozen_corpus()) == corpus_digest(build_frozen_corpus())


def test_seedable_adapter_preserves_every_memory() -> None:
    corpus = build_frozen_corpus()
    adapted = as_seedable_corpus(corpus)
    assert adapted.memories == corpus.memories
    assert adapted.tasks == ()


def _one_task_corpus(
    *, primary_body: str, distractor_body: str, query: str = "worker pool"
) -> FrozenMissCorpus:
    memories = (
        MemoryFixture(id="p", key="p", title="primary", body=primary_body),
        MemoryFixture(id="e", key="e", title="entry", body="an entry point"),
        MemoryFixture(id="d", key="d", title="distractor", body=distractor_body),
    )
    task = LexicalMissTask(
        task_id="t",
        miss_kind=MissKind.RENAMED_CONCEPT,
        query=query,
        surface_form="executor group",
        primary_relevant="p",
        acceptable_entry_points=("e",),
        distractors=("d",),
        literal_matching_distractors=("d",),
    )
    return FrozenMissCorpus(seed=5877, memories=memories, tasks=(task,))


def test_construction_rejects_a_primary_that_matches_literally() -> None:
    """Isolated failure: only the primary's body changes, and the check must fire."""

    corpus = _one_task_corpus(
        primary_body="the worker pool was resized", distractor_body="retired worker pool thread"
    )
    with pytest.raises(MissCorpusError, match="not a lexical miss"):
        validate_miss_construction(corpus)


def test_construction_rejects_a_task_with_no_literally_matching_distractor() -> None:
    corpus = _one_task_corpus(
        primary_body="the executor group was resized", distractor_body="an unrelated note"
    )
    with pytest.raises(MissCorpusError, match="degenerate reason"):
        validate_miss_construction(corpus)


def test_construction_rejects_an_unlabelled_incidental_match() -> None:
    corpus = _one_task_corpus(
        primary_body="the executor group was resized", distractor_body="retired worker pool thread"
    )
    polluted = corpus.model_copy(
        update={
            "memories": (
                *corpus.memories,
                MemoryFixture(id="bg", key="bg", title="filler", body="worker pool trivia"),
            )
        }
    )
    with pytest.raises(MissCorpusError, match="unlabelled Memories match"):
        validate_miss_construction(polluted)


def test_the_real_corpus_passes_construction() -> None:
    validate_miss_construction(build_frozen_corpus())


def _miss_spec() -> TaskSpec:
    corpus = _one_task_corpus(
        primary_body="the executor group was resized", distractor_body="retired worker pool thread"
    )
    return miss_specs(corpus)[0]


def test_subset_gate_accepts_a_strict_subset() -> None:
    """The gate must NOT require equality: a lexical miss returns strictly less
    than the labelled set, which is the whole point of the class."""

    validate_lexical_miss_truth(_miss_spec(), ["d"])


def test_subset_gate_rejects_an_unlabelled_id() -> None:
    with pytest.raises(LexicalRecallError, match="not a subset"):
        validate_lexical_miss_truth(_miss_spec(), ["d", "stray"])


def test_subset_gate_rejects_a_recalled_primary() -> None:
    with pytest.raises(LexicalRecallError, match="not a lexical miss at run time"):
        validate_lexical_miss_truth(_miss_spec(), ["d", "p"])


def test_subset_gate_rejects_an_empty_candidate_set() -> None:
    """Recall 0 because the matcher found nothing is a different claim from recall
    0 because it found the wrong things, and the gate must separate them."""

    with pytest.raises(LexicalRecallError, match="degenerate reason"):
        validate_lexical_miss_truth(_miss_spec(), [])


def test_control_gate_still_demands_equality() -> None:
    """The ordering corpus's property is untouched: relaxing it here would quietly
    weaken the guarantee the ordering result depends on."""

    task = OrderingTask(
        task_id="o",
        corpus_size=50,
        query="lease renewal",
        instruction="",
        primary_relevant="p",
        acceptable_entry_points=("e",),
        distractors=("d",),
        expected_facts=(),
        forbidden_facts=(),
    )
    spec = control_specs([task])[0]
    validate_control_truth(spec, ["p", "e", "d"])
    with pytest.raises(LexicalRecallError, match="does not equal"):
        validate_control_truth(spec, ["p", "e"])


def test_fts_match_expression_is_bag_of_words_or() -> None:
    assert fts_match_expression("renewing leases") == '"renewing" OR "leases"'
    with pytest.raises(GeneratorError, match="no FTS terms"):
        fts_match_expression("---")


def test_fts_recovers_a_morphological_variant_the_substring_matcher_cannot() -> None:
    """The capability claim, tested directly: porter stemming finds a document the
    literal matcher provably misses."""

    memories = [
        MemoryFixture(id="gold", key="gold", title="Lease renewal", body="the lease renewal rule"),
        MemoryFixture(id="other", key="other", title="Unrelated", body="the backup window"),
    ]
    corpus = _one_task_corpus(
        primary_body="the lease renewal rule",
        distractor_body="an unrelated note",
        query="renewing leases",
    )
    assert literal_matches(corpus, "renewing leases") == set()
    ranked = Fts5Generator(memories=memories, corpus_size=2).rank("renewing leases")
    assert ranked[0] == "gold"


def test_every_generator_indexes_the_same_document_text() -> None:
    """The fairness control. If one arm saw richer text than another, the recall
    comparison would be measuring the text, not the generator."""

    memory = MemoryFixture(id="m", key="m", title="t", body="b")
    assert document_text(memory, 3) == memory.stored_value(3)


def test_run_class_requires_the_literal_arm() -> None:
    with pytest.raises(LexicalRecallError, match="sets matched-k"):
        run_class([_miss_spec()], lambda _size: {})


def _recall_row(
    *,
    task_class: TaskClass,
    generator: Generator,
    primary_rank: int | None,
    matched_k: int,
    kind: MissKind | None = None,
    task_id: str = "t",
    candidate_set_size: int = 10,
) -> TaskRecall:
    return TaskRecall(
        task_id=task_id,
        task_class=task_class,
        miss_kind=kind,
        generator=generator,
        matched_k=matched_k,
        candidate_set_size=candidate_set_size,
        primary_rank=primary_rank,
        useful_rank=primary_rank,
        primary_at_matched_k=primary_rank is not None and primary_rank <= matched_k,
        primary_at_10=primary_rank is not None and primary_rank <= 10,
        primary_unbounded=primary_rank is not None,
        useful_at_matched_k=primary_rank is not None and primary_rank <= matched_k,
    )


def test_measure_reads_rank_off_the_ranking() -> None:
    spec = _miss_spec()
    row = measure(spec, Generator.FTS, ["d", "e", "p"], matched_k=2)
    assert row.primary_rank == 3
    assert row.primary_at_matched_k is False
    assert row.primary_at_10 is True
    assert row.useful_rank == 2


def test_gate_bands_match_the_preregistration() -> None:
    assert g1_verdict(0.50) == "recovers"
    assert g1_verdict(0.20) == "does_not_recover"
    assert g1_verdict(0.35) == "inconclusive"
    assert g2_verdict(0.95) == "preserves_literal_recall"
    assert g2_verdict(0.80) == "regresses"
    assert g2_verdict(0.90) == "inconclusive"


def test_combined_recommendation_needs_both_gates() -> None:
    """A generator that recovers misses while dropping literal hits must not be
    recommended, so this asserts the AND rather than either half."""

    rows = [
        _recall_row(
            task_class=TaskClass.LEXICAL_MISS,
            generator=Generator.FTS,
            primary_rank=1,
            matched_k=5,
            kind=MissKind.MORPHOLOGICAL,
        ),
        _recall_row(
            task_class=TaskClass.LEXICAL_HIT_CONTROL,
            generator=Generator.FTS,
            primary_rank=None,
            matched_k=5,
        ),
        _recall_row(
            task_class=TaskClass.LEXICAL_HIT_CONTROL,
            generator=Generator.LITERAL,
            primary_rank=1,
            matched_k=5,
        ),
        _recall_row(
            task_class=TaskClass.LEXICAL_MISS,
            generator=Generator.EMBEDDING,
            primary_rank=1,
            matched_k=5,
            kind=MissKind.SYNONYM,
        ),
        _recall_row(
            task_class=TaskClass.LEXICAL_HIT_CONTROL,
            generator=Generator.EMBEDDING,
            primary_rank=1,
            matched_k=5,
        ),
    ]
    verdicts = gate_verdicts(rows)
    fts = verdicts["fts"]
    embedding = verdicts["embedding"]
    assert isinstance(fts, dict) and isinstance(embedding, dict)
    assert fts["G1_recovery"]["verdict"] == "recovers"  # type: ignore[index]
    assert fts["G2_no_regression"]["verdict"] == "regresses"  # type: ignore[index]
    combined = verdicts["combined_recommendation"]
    assert isinstance(combined, dict)
    assert combined["generators_meeting_both_gates"] == ["embedding"]


def test_summary_reports_the_stratum_size_next_to_every_stratum() -> None:
    """A per-kind fraction without its n invites reading nine tasks as a rate."""

    rows = [
        _recall_row(
            task_class=TaskClass.LEXICAL_MISS,
            generator=Generator.FTS,
            primary_rank=1,
            matched_k=5,
            kind=MissKind.MORPHOLOGICAL,
        )
    ]
    summary = recall_summary(rows)
    miss = summary[TaskClass.LEXICAL_MISS.value]
    assert isinstance(miss, dict)
    fts = miss["fts"]
    assert isinstance(fts, dict)
    by_kind = fts["by_miss_kind"]
    assert isinstance(by_kind, dict)
    assert by_kind["morphological"]["n_tasks"] == 1  # type: ignore[index]
