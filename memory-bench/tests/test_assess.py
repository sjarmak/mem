"""Tests for the SELECT/assess rubric (mem-75t.7.4, plan §4 P3 + §9.4).

These pin the rubric's structural signal extraction (no semantic keyword
matching), the env-reconstructable criterion with an injected rig map +
checkout probe, the explicit tiebreaker rules, and that ranking a mixed pool
puts the demonstrably self-contained candidates on top — with the
env-reconstructable gate dominating regardless of the other sub-scores.
"""

from pathlib import Path

import pytest

from membench.assess import (
    RUBRIC_V1,
    WEIGHTS,
    CandidateAssessment,
    CriterionScore,
    RepoSignals,
    assess_candidate,
    default_mutation_count_provider,
    gather_pool_signals,
    rank_candidates,
)

# ---------------------------------------------------------------------------
# Record builders — Mapping-shaped WorkRecords (validity.query_from_record shape)
# ---------------------------------------------------------------------------


def make_record(
    work_id: str = "w-1",
    rig: str = "rigA",
    *,
    title: str = "Fix the flaky retry loop in the ingest worker",
    description: str | None = None,
    acceptance_criteria: str | None = None,
    status: str = "closed",
    started: str | None = "2026-01-05T00:00:00Z",
    closed: str | None = "2026-01-06T00:00:00Z",
    trace: dict | None = None,
    outcome: dict | None = None,
    provenance: dict | None = None,
) -> dict:
    record: dict = {
        "work_id": work_id,
        "rig": rig,
        "title": title,
        "lifecycle": {
            "created": "2026-01-04T00:00:00Z",
            "status": status,
        },
    }
    if started is not None:
        record["lifecycle"]["started"] = started
    if closed is not None:
        record["lifecycle"]["closed"] = closed
    if description is not None:
        record["description"] = description
    if acceptance_criteria is not None:
        record["acceptance_criteria"] = acceptance_criteria
    if trace is not None:
        record["trace"] = trace
    if outcome is not None:
        record["outcome"] = outcome
    if provenance is not None:
        record["provenance"] = provenance
    return record


def rich_trace(n_turns: int = 40) -> dict:
    return {"jsonl_path": "/traces/x.jsonl", "n_turns": n_turns}


RICH_BODY = (
    "## Context\n\n"
    "The ingest worker retries failed batches forever, masking the real error.\n\n"
    "## Approach\n\n"
    "Bound the retry loop and surface the terminal failure to the caller so the\n"
    "run record carries the root cause instead of a timeout.\n"
)


def env_rig_map(tmp_path: Path) -> dict[str, Path]:
    repo = tmp_path / "rigA-clone"
    repo.mkdir(exist_ok=True)
    return {"rigA": repo}


def counts(n: int):
    """A file-free mutation-count provider returning a constant count."""

    def provider(work_id: str, trace_path: str) -> int:
        return n

    return provider


def assess(record: dict, pool: list[dict] | None = None, **kw) -> CandidateAssessment:
    records = pool if pool is not None else [record]
    signals = gather_pool_signals(records)
    kw.setdefault("mutation_count_provider", counts(12))
    return assess_candidate(record, pool_signals=signals, **kw)


def rank(pool: list[dict], **kw) -> list[CandidateAssessment]:
    kw.setdefault("mutation_count_provider", counts(12))
    return rank_candidates(pool, **kw)


def score_of(assessment: CandidateAssessment, name: str) -> float:
    return assessment.criterion(name).score


# ---------------------------------------------------------------------------
# Rubric shape
# ---------------------------------------------------------------------------


class TestRubricShape:
    def test_weights_cover_rubric_and_sum_to_one(self):
        assert set(WEIGHTS) == set(RUBRIC_V1)
        assert sum(WEIGHTS.values()) == pytest.approx(1.0)

    def test_assessment_carries_every_criterion_with_reasoning(self, tmp_path):
        a = assess(make_record(), rig_repos=env_rig_map(tmp_path))
        assert tuple(s.name for s in a.scores) == RUBRIC_V1
        assert all(isinstance(s, CriterionScore) for s in a.scores)
        assert all(s.reasoning for s in a.scores)
        assert all(0.0 <= s.score <= 1.0 for s in a.scores)
        assert 0.0 <= a.overall <= 1.0

    def test_overall_is_the_weighted_sum(self, tmp_path):
        a = assess(make_record(), rig_repos=env_rig_map(tmp_path))
        expected = sum(s.score * WEIGHTS[s.name] for s in a.scores)
        assert a.overall == pytest.approx(expected)


# ---------------------------------------------------------------------------
# spec_quality — structural title/body signals, no keyword matching
# ---------------------------------------------------------------------------


class TestSpecQuality:
    def test_bare_short_title_scores_lowest(self):
        a = assess(make_record(title="fix"))
        assert score_of(a, "spec_quality") == pytest.approx(0.1)

    def test_title_only_is_weak(self):
        a = assess(make_record())
        assert score_of(a, "spec_quality") == pytest.approx(0.4)

    def test_title_plus_body_is_moderate(self):
        a = assess(make_record(description="One short unstructured sentence."))
        assert score_of(a, "spec_quality") == pytest.approx(0.7)

    def test_sectioned_body_scores_full(self):
        a = assess(make_record(description=RICH_BODY))
        assert score_of(a, "spec_quality") == pytest.approx(1.0)

    def test_acceptance_criteria_field_scores_full(self):
        a = assess(
            make_record(
                description="Bound the retry loop; surface the terminal failure to the caller.",
                acceptance_criteria="Retry loop bounded; terminal failure surfaced.",
            )
        )
        assert score_of(a, "spec_quality") == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# trace_signal — presence, then volume
# ---------------------------------------------------------------------------


class TestTraceSignal:
    def test_no_trace_scores_zero(self):
        assert score_of(assess(make_record()), "trace_signal") == pytest.approx(0.0)

    def test_path_without_turn_count_is_unparsed(self):
        a = assess(make_record(trace={"jsonl_path": "/traces/x.jsonl"}))
        assert score_of(a, "trace_signal") == pytest.approx(0.4)

    def test_tiny_trace_is_weak(self):
        a = assess(make_record(trace=rich_trace(n_turns=2)))
        assert score_of(a, "trace_signal") == pytest.approx(0.3)

    def test_moderate_trace(self):
        a = assess(make_record(trace=rich_trace(n_turns=10)))
        assert score_of(a, "trace_signal") == pytest.approx(0.7)

    def test_rich_trace_scores_full(self):
        a = assess(make_record(trace=rich_trace(n_turns=40)))
        assert score_of(a, "trace_signal") == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# closed — lifecycle agreement
# ---------------------------------------------------------------------------


class TestClosed:
    def test_closed_status_and_timestamp_scores_full(self):
        assert score_of(assess(make_record()), "closed") == pytest.approx(1.0)

    def test_status_without_timestamp_is_partial(self):
        a = assess(make_record(closed=None))
        assert score_of(a, "closed") == pytest.approx(0.5)

    def test_timestamp_without_status_is_partial(self):
        a = assess(make_record(status="in_progress"))
        assert score_of(a, "closed") == pytest.approx(0.5)

    def test_open_scores_zero(self):
        a = assess(make_record(status="open", closed=None))
        assert score_of(a, "closed") == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# repo_activity — pool-level bead/commit counts per rig
# ---------------------------------------------------------------------------


class TestRepoActivity:
    def test_pool_signals_count_beads_and_commit_anchors(self):
        pool = [
            make_record("w-1", outcome={"commit_sha": "a" * 40}),
            make_record("w-2", provenance={"repo": "r", "base_commit": "b" * 40}),
            make_record("w-3"),
            make_record("w-4", rig="rigB"),
        ]
        signals = gather_pool_signals(pool)
        assert signals["rigA"] == RepoSignals(rig="rigA", bead_count=3, commit_count=2)
        assert signals["rigB"] == RepoSignals(rig="rigB", bead_count=1, commit_count=0)

    def test_rich_rig_outscores_singleton_rig(self):
        rich_pool = [make_record(f"w-{i}", outcome={"commit_sha": f"{i:040d}"}) for i in range(60)]
        lone = make_record("lone", rig="rigB")
        pool = [*rich_pool, lone]
        rich = assess(rich_pool[0], pool=pool)
        single = assess(lone, pool=pool)
        assert score_of(rich, "repo_activity") > score_of(single, "repo_activity")
        assert score_of(rich, "repo_activity") == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# env_reconstructable — injected rig map + checkout probe (plan §9.4)
# ---------------------------------------------------------------------------


class TestEnvReconstructable:
    def test_unmapped_rig_scores_zero(self):
        a = assess(make_record(rig="ghost"), rig_repos={})
        assert score_of(a, "env_reconstructable") == pytest.approx(0.0)
        assert a.env_reconstructable is False

    def test_failed_checkout_probe_scores_zero(self, tmp_path):
        a = assess(
            make_record(),
            rig_repos=env_rig_map(tmp_path),
            checkout_probe=lambda path: False,
        )
        assert score_of(a, "env_reconstructable") == pytest.approx(0.0)

    def test_default_probe_rejects_missing_repo_path(self, tmp_path):
        a = assess(make_record(), rig_repos={"rigA": tmp_path / "absent"})
        assert score_of(a, "env_reconstructable") == pytest.approx(0.0)

    def test_repo_plus_base_commit_anchor_scores_full(self, tmp_path):
        a = assess(
            make_record(provenance={"repo": "ds/rigA", "base_commit": "c" * 40}),
            rig_repos=env_rig_map(tmp_path),
        )
        assert score_of(a, "env_reconstructable") == pytest.approx(1.0)
        assert a.env_reconstructable is True

    def test_outcome_anchor_also_scores_full(self, tmp_path):
        a = assess(
            make_record(outcome={"repo": "ds/rigA", "base_commit": "c" * 40}),
            rig_repos=env_rig_map(tmp_path),
        )
        assert score_of(a, "env_reconstructable") == pytest.approx(1.0)

    def test_timestamp_anchor_is_resolvable_but_approximate(self, tmp_path):
        a = assess(make_record(), rig_repos=env_rig_map(tmp_path))
        assert score_of(a, "env_reconstructable") == pytest.approx(0.7)
        assert a.env_reconstructable is True

    def test_no_anchor_at_all_scores_zero(self, tmp_path):
        record = make_record(started=None)
        del record["lifecycle"]["created"]
        a = assess(record, rig_repos=env_rig_map(tmp_path))
        assert score_of(a, "env_reconstructable") == pytest.approx(0.0)

    def test_probe_receives_the_mapped_repo_path(self, tmp_path):
        seen: list[Path] = []

        def probe(path: Path) -> bool:
            seen.append(path)
            return True

        rig_repos = env_rig_map(tmp_path)
        assess(make_record(), rig_repos=rig_repos, checkout_probe=probe)
        assert seen == [rig_repos["rigA"]]


# ---------------------------------------------------------------------------
# mutation_signal — injectable mutation-call count, tiered on structural volume
# ---------------------------------------------------------------------------


class TestMutationSignal:
    def test_no_trace_scores_zero_without_calling_provider(self):
        def exploding(work_id: str, trace_path: str) -> int:
            raise AssertionError("provider must not be called when there is no trace")

        a = assess(make_record(), mutation_count_provider=exploding)
        assert score_of(a, "mutation_signal") == pytest.approx(0.0)
        assert a.replayable is False
        assert a.mutation_calls == 0

    def test_zero_mutation_calls_is_the_hard_gate_zero(self):
        a = assess(make_record(trace=rich_trace()), mutation_count_provider=counts(0))
        assert score_of(a, "mutation_signal") == pytest.approx(0.0)
        assert a.replayable is False

    @pytest.mark.parametrize(
        ("count", "expected"),
        [(1, 0.4), (2, 0.4), (3, 0.7), (9, 0.7), (10, 1.0), (57, 1.0)],
    )
    def test_volume_tiers(self, count, expected):
        a = assess(make_record(trace=rich_trace()), mutation_count_provider=counts(count))
        assert score_of(a, "mutation_signal") == pytest.approx(expected)
        assert a.replayable is True
        assert a.mutation_calls == count

    def test_provider_receives_work_id_and_trace_path(self):
        seen: list[tuple[str, str]] = []

        def provider(work_id: str, trace_path: str) -> int:
            seen.append((work_id, trace_path))
            return 5

        assess(make_record("w-9", trace=rich_trace()), mutation_count_provider=provider)
        assert seen == [("w-9", "/traces/x.jsonl")]

    def test_negative_count_fails_loud(self):
        with pytest.raises(ValueError, match="mutation"):
            assess(make_record(trace=rich_trace()), mutation_count_provider=counts(-1))

    def test_default_provider_counts_via_parse_mutation_calls(self, tmp_path):
        def event(name: str, args: dict) -> str:
            import json

            return json.dumps(
                {"message": {"content": [{"type": "tool_use", "name": name, "input": args}]}}
            )

        transcript = tmp_path / "t.jsonl"
        transcript.write_text(
            "\n".join(
                [
                    event("Edit", {"file_path": "/w/a.py", "old_string": "x", "new_string": "y"}),
                    event("Bash", {"command": "ls"}),
                    event("Write", {"file_path": "/w/b.py", "content": "print()\n"}),
                ]
            ),
            encoding="utf-8",
        )
        assert default_mutation_count_provider("w-1", str(transcript)) == 2

    def test_default_provider_fails_loud_on_missing_transcript(self, tmp_path):
        with pytest.raises(OSError):
            default_mutation_count_provider("w-1", str(tmp_path / "absent.jsonl"))


class TestMutationGateRanking:
    def test_zero_mutation_gate_dominates_overall(self, tmp_path):
        strong_but_shell_only = full_candidate("shell-only")
        weak_but_replayable = make_record("replayable", title="fix", trace=rich_trace(2))
        per_bead = {"shell-only": 0, "replayable": 4}
        ranked = rank(
            [strong_but_shell_only, weak_but_replayable],
            rig_repos=env_rig_map(tmp_path),
            mutation_count_provider=lambda work_id, trace_path: per_bead[work_id],
        )
        assert [a.work_id for a in ranked] == ["replayable", "shell-only"]

    def test_env_gate_outranks_mutation_gate(self, tmp_path):
        env_dead = full_candidate("env-dead", rig="ghost")  # unmapped rig, has mutations
        shell_only = full_candidate("shell-only")  # runnable env, zero mutations
        per_bead = {"env-dead": 12, "shell-only": 0}
        ranked = rank(
            [env_dead, shell_only],
            rig_repos=env_rig_map(tmp_path),
            mutation_count_provider=lambda work_id, trace_path: per_bead[work_id],
        )
        assert [a.work_id for a in ranked] == ["shell-only", "env-dead"]

    def test_mutation_count_breaks_ties_before_trace_turns(self, tmp_path):
        # Same tier (both 1.0), same turns: the raw count is the finer volume signal.
        a = full_candidate("aa")
        b = full_candidate("bb")
        per_bead = {"aa": 10, "bb": 60}
        ranked = rank(
            [a, b],
            rig_repos=env_rig_map(tmp_path),
            mutation_count_provider=lambda work_id, trace_path: per_bead[work_id],
        )
        assert [r.work_id for r in ranked] == ["bb", "aa"]


# ---------------------------------------------------------------------------
# Ranking — gate, tiebreakers, determinism, top-N self-containment
# ---------------------------------------------------------------------------


def full_candidate(work_id: str, *, n_turns: int = 40, **kw) -> dict:
    return make_record(
        work_id,
        description=RICH_BODY,
        acceptance_criteria="Done when the retry loop is bounded.",
        trace=rich_trace(n_turns=n_turns),
        provenance={"repo": "ds/rigA", "base_commit": "c" * 40},
        **kw,
    )


class TestRanking:
    def test_env_gate_dominates_other_scores(self, tmp_path):
        strong_but_dead = full_candidate("dead", rig="ghost")  # unmapped rig
        weak_but_runnable = make_record("runnable", title="fix")
        ranked = rank([strong_but_dead, weak_but_runnable], rig_repos=env_rig_map(tmp_path))
        assert [a.work_id for a in ranked] == ["runnable", "dead"]

    def test_higher_overall_ranks_first_within_gate(self, tmp_path):
        strong = full_candidate("strong")
        weak = make_record("weak", title="fix", status="open", closed=None)
        ranked = rank([weak, strong], rig_repos=env_rig_map(tmp_path))
        assert [a.work_id for a in ranked] == ["strong", "weak"]

    def test_tiebreak_on_trace_turns_then_work_id(self, tmp_path):
        # Both saturate the trace tier (equal overall); more turns wins.
        a = full_candidate("aa", n_turns=40)
        b = full_candidate("bb", n_turns=400)
        ranked = rank([a, b], rig_repos=env_rig_map(tmp_path))
        assert [r.work_id for r in ranked] == ["bb", "aa"]
        # Fully identical signals: work_id ascending is the total-order anchor.
        c = full_candidate("cc")
        d = full_candidate("dd")
        ranked = rank([d, c], rig_repos=env_rig_map(tmp_path))
        assert [r.work_id for r in ranked] == ["cc", "dd"]

    def test_ranking_is_input_order_independent(self, tmp_path):
        pool = [
            full_candidate("p-1"),
            make_record("p-2", trace=rich_trace(10)),
            make_record("p-3", title="fix", status="open", closed=None),
            full_candidate("p-4", rig="ghost"),
        ]
        rig_repos = env_rig_map(tmp_path)
        forward = [a.work_id for a in rank(pool, rig_repos=rig_repos)]
        backward = [a.work_id for a in rank(pool[::-1], rig_repos=rig_repos)]
        assert forward == backward

    def test_top_n_of_mixed_pool_is_the_self_contained_subset(self, tmp_path):
        self_contained = [full_candidate(f"good-{i}") for i in range(3)]
        dead_weight = [
            make_record("open-no-trace", status="open", closed=None),
            make_record("no-trace"),
            full_candidate("unmapped-rig", rig="ghost"),
            make_record("tiny-trace", title="fix", trace=rich_trace(2)),
        ]
        ranked = rank(self_contained + dead_weight, rig_repos=env_rig_map(tmp_path), top_n=3)
        assert len(ranked) == 3
        assert {a.work_id for a in ranked} == {"good-0", "good-1", "good-2"}

    def test_top_n_none_returns_everything(self, tmp_path):
        pool = [full_candidate("x"), make_record("y")]
        assert len(rank(pool, rig_repos=env_rig_map(tmp_path))) == 2


# ---------------------------------------------------------------------------
# Immutability / value semantics
# ---------------------------------------------------------------------------


class TestValueSemantics:
    def test_assessment_is_frozen(self, tmp_path):
        a = assess(make_record(), rig_repos=env_rig_map(tmp_path))
        with pytest.raises(AttributeError):
            a.overall = 0.0  # type: ignore[misc]

    def test_unknown_criterion_lookup_fails_loud(self, tmp_path):
        a = assess(make_record(), rig_repos=env_rig_map(tmp_path))
        with pytest.raises(KeyError):
            a.criterion("nope")
