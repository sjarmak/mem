"""Tests for the lexical-miss recall experiment (mem-lbuvd).

The guard tests here are written so they fail when the guard is removed. A test
that passes whether or not the defence works is worse than no test, because it
reports coverage it does not have.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from pydantic import ValidationError

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
    LiteralGenerator,
    _cosine,
    document_text,
    fts_match_expression,
)
from membench.lexical_recall.models import (
    RECALL_AT_10,
    FrozenMissCorpus,
    Generator,
    LexicalMissTask,
    MissKind,
    TaskClass,
    TaskRecall,
)
from membench.lexical_recall.runner import (
    G1_DOES_NOT_RECOVER,
    G1_RECOVERS,
    G2_PRESERVES,
    G2_REGRESSES,
    LexicalRecallError,
    TaskSpec,
    _budget_diagnostic,
    _cost_ratios,
    _mean,
    _percentile,
    _rank_of,
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
    corpus_size: int = 100,
) -> TaskRecall:
    return TaskRecall(
        task_id=task_id,
        task_class=task_class,
        miss_kind=kind,
        generator=generator,
        corpus_size=corpus_size,
        n_labelled_non_primary=matched_k,
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
        # G3 is reported per class, so the miss class needs its own literal
        # denominator. Omitting it used to drop the rows silently.
        _recall_row(
            task_class=TaskClass.LEXICAL_MISS,
            generator=Generator.LITERAL,
            primary_rank=None,
            matched_k=5,
            kind=MissKind.MORPHOLOGICAL,
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


# --- Branches the first round of guard tests never reached -------------------
#
# Each test below was written against a specific mutant that survived the initial
# 16-mutant sweep because no test executed the line at all. Coverage of a module
# is not coverage of the lines that carry the arithmetic.


class _FixedGenerator:
    """A generator that replays a canned ranking, so `run_class` can be driven
    without a Beads binary, an FTS index or an Ollama daemon."""

    def __init__(self, name: Generator, ranking: Sequence[str]) -> None:
        self._name = name
        self._ranking = tuple(ranking)
        self.calls = 0

    @property
    def name(self) -> Generator:
        return self._name

    def rank(self, query: str) -> tuple[str, ...]:
        self.calls += 1
        return self._ranking


def _generators_for(
    literal: Sequence[str], other: Sequence[str]
) -> Callable[[int], dict[Generator, _FixedGenerator]]:
    arms = {
        Generator.LITERAL: _FixedGenerator(Generator.LITERAL, literal),
        Generator.FTS: _FixedGenerator(Generator.FTS, other),
    }
    return lambda _size: arms


def test_run_class_sets_matched_k_from_the_literal_arm() -> None:
    """Kills `matched_k = len(literal_ranked) + n`, which moves every headline."""

    corpus = _one_task_corpus(primary_body="an executor group note", distractor_body="worker pool")
    specs = miss_specs(corpus)
    rows = run_class(specs, _generators_for(literal=["d"], other=["d", "e", "p"]))

    literal_row = next(r for r in rows if r.generator is Generator.LITERAL)
    fts_row = next(r for r in rows if r.generator is Generator.FTS)
    assert literal_row.matched_k == 1
    assert fts_row.matched_k == 1
    # The primary sits at rank 3 against a budget of 1, so recall at matched-k is
    # False while unbounded recall is True. Any off-by-n on matched_k flips this.
    assert fts_row.primary_rank == 3
    assert fts_row.primary_at_matched_k is False
    assert fts_row.primary_unbounded is True


def test_run_class_reuses_the_literal_ranking_instead_of_re_shelling() -> None:
    corpus = _one_task_corpus(primary_body="an executor group note", distractor_body="worker pool")
    factory = _generators_for(literal=["d"], other=["d", "p"])
    arms = factory(0)
    run_class(miss_specs(corpus), factory)
    assert arms[Generator.LITERAL].calls == 1


def test_run_class_applies_the_control_equality_gate_to_control_tasks() -> None:
    """Kills a mutant that always takes the lexical-miss branch: under the subset
    gate a short control candidate set passes, under equality it must not."""

    task = OrderingTask(
        task_id="ctl",
        corpus_size=3,
        query="worker pool",
        instruction="find the accepted guidance",
        primary_relevant="p",
        acceptable_entry_points=("e",),
        distractors=("d",),
        expected_facts=(),
        forbidden_facts=(),
        expected_order=(),
    )
    specs = control_specs([task])
    with pytest.raises(LexicalRecallError, match="does not equal the labelled set"):
        run_class(specs, _generators_for(literal=["p", "e"], other=["p"]))


def test_a_rank_of_zero_cannot_be_constructed() -> None:
    """`_rank_of` returning 0 for "not found" would satisfy every `rank <= budget`
    comparison and invert recall across the board. `ge=1` makes it unrepresentable
    rather than merely untested."""

    with pytest.raises(ValidationError):
        TaskRecall(
            task_id="t",
            task_class=TaskClass.LEXICAL_MISS,
            miss_kind=MissKind.SYNONYM,
            generator=Generator.FTS,
            corpus_size=10,
            matched_k=2,
            candidate_set_size=5,
            primary_rank=0,
            useful_rank=None,
            n_labelled_non_primary=2,
            primary_at_matched_k=True,
            primary_at_10=True,
            primary_unbounded=True,
            useful_at_matched_k=False,
        )


def test_rank_of_returns_none_when_nothing_wanted_is_ranked() -> None:
    assert _rank_of(["a", "b"], frozenset({"z"})) is None
    assert _rank_of([], frozenset({"z"})) is None
    assert _rank_of(["a", "z"], frozenset({"z"})) == 2


def test_budget_flags_must_follow_from_the_ranks() -> None:
    """The four booleans are what every statistic averages, so a caller that
    computes one wrongly fails at construction instead of shifting a headline."""

    with pytest.raises(ValidationError, match="primary_at_matched_k"):
        TaskRecall(
            task_id="t",
            task_class=TaskClass.LEXICAL_MISS,
            miss_kind=MissKind.SYNONYM,
            generator=Generator.FTS,
            corpus_size=10,
            matched_k=2,
            candidate_set_size=5,
            primary_rank=4,
            useful_rank=4,
            n_labelled_non_primary=2,
            primary_at_matched_k=True,
            primary_at_10=True,
            primary_unbounded=True,
            useful_at_matched_k=False,
        )


def test_construction_rejects_a_query_that_matches_an_entry_point() -> None:
    """The class is "query absent from the primary AND every entry point". The
    stray check cannot catch an entry-point match because entry points are inside
    the labelled set, so this guard is the only thing enforcing half the class."""

    memories = (
        MemoryFixture(id="p", key="p", title="primary", body="an executor group note"),
        MemoryFixture(id="e", key="e", title="entry", body="the worker pool map"),
        MemoryFixture(id="d", key="d", title="distractor", body="worker pool"),
    )
    task = LexicalMissTask(
        task_id="t",
        miss_kind=MissKind.RENAMED_CONCEPT,
        query="worker pool",
        surface_form="executor group",
        primary_relevant="p",
        acceptable_entry_points=("e",),
        distractors=("d",),
        literal_matching_distractors=("d",),
    )
    corpus = FrozenMissCorpus(seed=5877, memories=memories, tasks=(task,))
    with pytest.raises(MissCorpusError, match="literally matches an acceptable entry point"):
        validate_miss_construction(corpus)


def test_cost_ratios_join_on_corpus_size_not_task_id_alone() -> None:
    """The control class runs the same task at three sizes. Keying on task_id alone
    is last-write-wins and compares a task against another size's denominator."""

    def row(generator: Generator, size: int, candidates: int) -> TaskRecall:
        return TaskRecall(
            task_id="shared",
            task_class=TaskClass.LEXICAL_HIT_CONTROL,
            miss_kind=None,
            generator=generator,
            corpus_size=size,
            matched_k=candidates,
            candidate_set_size=candidates,
            primary_rank=1,
            useful_rank=1,
            n_labelled_non_primary=1,
            primary_at_matched_k=True,
            primary_at_10=True,
            primary_unbounded=True,
            useful_at_matched_k=True,
        )

    literal = [row(Generator.LITERAL, 50, 10), row(Generator.LITERAL, 500, 200)]
    fts = [row(Generator.FTS, 50, 20), row(Generator.FTS, 500, 400)]
    result = _cost_ratios(fts, literal)
    assert result["n_tasks"] == 2
    # True ratio is 2.0 for both. Under a task_id-only join the 50-size row would be
    # divided by 200 and the median would read 1.05.
    assert result["median_ratio_vs_literal"] == 2.0


def test_cost_ratios_raise_rather_than_dropping_a_row_with_no_denominator() -> None:
    fts = [
        TaskRecall(
            task_id="orphan",
            task_class=TaskClass.LEXICAL_HIT_CONTROL,
            miss_kind=None,
            generator=Generator.FTS,
            corpus_size=50,
            matched_k=1,
            candidate_set_size=9,
            primary_rank=1,
            useful_rank=1,
            n_labelled_non_primary=1,
            primary_at_matched_k=True,
            primary_at_10=True,
            primary_unbounded=True,
            useful_at_matched_k=True,
        )
    ]
    with pytest.raises(LexicalRecallError, match="no literal row"):
        _cost_ratios(fts, [])


def test_percentile_uses_nearest_rank_at_small_n() -> None:
    """`round(0.9 * (n - 1))` is half-to-even, so at n=6 it returns the 5th of 6.
    The per-kind strata the preregistration reports are n=9."""

    assert _percentile([1, 2, 3, 4, 5, 6], 0.9) == 6
    assert _percentile([1, 2, 3, 4, 5, 6, 7, 8, 9], 0.9) == 9
    assert _percentile(list(range(1, 37)), 0.9) == 33
    assert _percentile([7], 0.9) == 7


def test_recall_summary_rejects_duplicate_rows_for_one_task() -> None:
    """The preregistration fixes the task as the independent unit. Repeat rows
    would turn every statistic into a repeat-weighted average in silence."""

    def row() -> TaskRecall:
        return TaskRecall(
            task_id="t",
            task_class=TaskClass.LEXICAL_MISS,
            miss_kind=MissKind.SYNONYM,
            generator=Generator.FTS,
            corpus_size=10,
            matched_k=2,
            candidate_set_size=5,
            primary_rank=1,
            useful_rank=1,
            n_labelled_non_primary=2,
            primary_at_matched_k=True,
            primary_at_10=True,
            primary_unbounded=True,
            useful_at_matched_k=True,
        )

    with pytest.raises(LexicalRecallError, match="independent unit is the task"):
        recall_summary([row(), row()])


def test_budget_diagnostic_records_that_g1_has_no_resolution() -> None:
    """On the miss class the budget is `matched_k` and there are `matched_k + 1`
    labelled non-primary documents, so G1 is bounded below the ceiling before any
    ranker runs. `analysis.json` has to say so, or `does_not_recover` reads as a
    fact about the ranker."""

    corpus = _one_task_corpus(primary_body="an executor group note", distractor_body="worker pool")
    rows = run_class(miss_specs(corpus), _generators_for(literal=["d"], other=["d", "e", "p"]))
    diagnostic = _budget_diagnostic([r for r in rows if r.generator is Generator.FTS])
    assert diagnostic["n_tasks_budget_saturable_by_non_primary_labels"] == 1
    assert diagnostic["budget_saturable_on_every_task"] is True
    assert diagnostic["primary_rank_minus_matched_k"] == {"min": 2, "median": 2, "max": 2}


def test_literal_generator_rejects_an_incomplete_page() -> None:
    """A short page shrinks matched-k and every arm's budget with it. The subset
    gate structurally cannot catch that on the lexical-miss class."""

    generator = LiteralGenerator(beads_bin="unused", workspace=Path("."))
    with pytest.raises(GeneratorError, match="incomplete page"):
        generator._require_whole_page({"complete": False, "continuation": "abc"}, 3)
    with pytest.raises(GeneratorError, match="total_matched=5 but returned 3"):
        generator._require_whole_page({"complete": True, "total_matched": 5}, 3)
    generator._require_whole_page({"complete": True, "total_matched": 3}, 3)


def test_gate_thresholds_are_read_from_the_preregistration_not_transcribed() -> None:
    """Hand-transcribed thresholds are correct today. This makes a future edit that
    drifts from the locked fixture fail rather than silently re-grade the run."""

    locked = json.loads(
        (
            Path(__file__).resolve().parents[1] / "fixtures/lexical_recall/preregistration.json"
        ).read_text(encoding="utf-8")
    )
    gates = locked["gates"]

    def threshold(gate: str, key: str) -> float:
        match = re.fullmatch(r"(>=|<=)\s*([0-9.]+)", gates[gate][key].strip())
        assert match is not None, f"{gate}.{key} is not a comparison: {gates[gate][key]!r}"
        return float(match.group(2))

    assert threshold("G1_recovery", "recovers_if") == G1_RECOVERS
    assert threshold("G1_recovery", "does_not_recover_if") == G1_DOES_NOT_RECOVER
    assert threshold("G2_no_regression", "preserves_literal_recall_if") == G2_PRESERVES
    assert threshold("G2_no_regression", "regresses_if") == G2_REGRESSES
    assert f"recall@{RECALL_AT_10}" in locked["endpoint"]["secondary"][0]


def test_cosine_is_orientation_not_magnitude() -> None:
    assert _cosine((1.0, 0.0), (2.0, 0.0)) == pytest.approx(1.0)
    assert _cosine((1.0, 0.0), (0.0, 1.0)) == pytest.approx(0.0)
    assert _cosine((1.0, 1.0), (-1.0, -1.0)) == pytest.approx(-1.0)
    with pytest.raises(GeneratorError, match="zero vector"):
        _cosine((0.0, 0.0), (1.0, 0.0))


def test_every_guard_raise_in_the_runner_is_reachable() -> None:
    """The five remaining branches are all failure paths. An untested raise is a
    line a mutant can turn into `pass` or `return 0.0` for free, and each of these
    guards a statistic rather than a side effect."""

    control = TaskSpec(
        task_id="c",
        task_class=TaskClass.LEXICAL_HIT_CONTROL,
        miss_kind=None,
        query="worker pool",
        primary_relevant="p",
        useful_ids=frozenset({"p"}),
        labelled=frozenset({"p"}),
        corpus_size=10,
    )
    with pytest.raises(LexicalRecallError, match="is not a lexical-miss task"):
        validate_lexical_miss_truth(control, ["p"])

    with pytest.raises(LexicalRecallError, match="cannot average an empty set"):
        _mean([])

    with pytest.raises(LexicalRecallError, match="cannot take a percentile of nothing"):
        _percentile([], 0.9)

    duplicated = [
        _recall_row(
            task_class=TaskClass.LEXICAL_MISS,
            generator=Generator.LITERAL,
            primary_rank=None,
            matched_k=2,
            kind=MissKind.SYNONYM,
        )
    ] * 2
    with pytest.raises(LexicalRecallError, match="collide on"):
        _cost_ratios([], duplicated)

    zero = _recall_row(
        task_class=TaskClass.LEXICAL_MISS,
        generator=Generator.LITERAL,
        primary_rank=None,
        matched_k=0,
        kind=MissKind.SYNONYM,
        candidate_set_size=0,
    )
    other = _recall_row(
        task_class=TaskClass.LEXICAL_MISS,
        generator=Generator.FTS,
        primary_rank=None,
        matched_k=0,
        kind=MissKind.SYNONYM,
        candidate_set_size=4,
    )
    with pytest.raises(LexicalRecallError, match="cost ratio is undefined"):
        _cost_ratios([other], [zero])
