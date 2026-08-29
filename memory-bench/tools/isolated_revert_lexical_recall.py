"""Isolated-revert check for the lexical-recall guards (mem-lbuvd).

Break one guard at a time, assert the test that claims to cover it goes red. A
guard test that stays green with the guard removed is measuring its own fixture
geometry rather than the defence, and prose quality is anti-correlated with that
bug: the better a test reads, the less anyone re-checks it.

This is not a pytest test. It rewrites source files in place, so it runs on
demand, from `memory-bench/`:

    uv run python tools/isolated_revert_lexical_recall.py

Every case names the exact test that must die. A mutant whose snippet no longer
matches uniquely is a failure too, not a skip: silently matching zero sites is how
a mutation sweep reports coverage it does not have.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
CORPUS = ROOT / "membench/lexical_recall/corpus.py"
RUNNER = ROOT / "membench/lexical_recall/runner.py"
GENS = ROOT / "membench/lexical_recall/generators.py"
MODELS = ROOT / "membench/lexical_recall/models.py"

CASES: list[tuple[pathlib.Path, str, str, str]] = [
    # --- corpus construction gates ---
    (
        CORPUS,
        "        if task.primary_relevant in matches:",
        "        if False:",
        "test_construction_rejects_a_primary_that_matches_literally",
    ),
    (
        CORPUS,
        "        if recalled_entries:",
        "        if False:",
        "test_construction_rejects_a_query_that_matches_an_entry_point",
    ),
    (
        CORPUS,
        "        if not matching_distractors:",
        "        if False:",
        "test_construction_rejects_a_task_with_no_literally_matching_distractor",
    ),
    (
        CORPUS,
        "        if stray:",
        "        if False:",
        "test_construction_rejects_an_unlabelled_incidental_match",
    ),
    (
        CORPUS,
        "    if seed != FROZEN_SEED:",
        "    if False:",
        "test_the_corpus_is_frozen_at_one_seed",
    ),
    # --- runtime truth gates ---
    (
        RUNNER,
        "    stray = returned - spec.labelled\n    if stray:",
        "    stray = returned - spec.labelled\n    if False:",
        "test_subset_gate_rejects_an_unlabelled_id",
    ),
    (
        RUNNER,
        "    if spec.primary_relevant in returned:",
        "    if False:",
        "test_subset_gate_rejects_a_recalled_primary",
    ),
    (
        RUNNER,
        "    if not returned:",
        "    if False:",
        "test_subset_gate_rejects_an_empty_candidate_set",
    ),
    (
        RUNNER,
        "    if set(literal_ids) != spec.labelled:",
        "    if False:",
        "test_control_gate_still_demands_equality",
    ),
    (
        RUNNER,
        "        if Generator.LITERAL not in generators:",
        "        if False:",
        "test_run_class_requires_the_literal_arm",
    ),
    # --- the arithmetic every headline is scaled by ---
    (
        RUNNER,
        "        matched_k = len(literal_ranked)",
        "        matched_k = len(literal_ranked) + 2",
        "test_run_class_sets_matched_k_from_the_literal_arm",
    ),
    (
        RUNNER,
        "        if spec.task_class is TaskClass.LEXICAL_MISS:",
        "        if True:",
        "test_run_class_applies_the_control_equality_gate_to_control_tasks",
    ),
    (
        RUNNER,
        "            ranked = literal_ranked if name is Generator.LITERAL else generator.rank(",
        "            ranked = generator.rank(",
        "test_run_class_reuses_the_literal_ranking_instead_of_re_shelling",
    ),
    (
        RUNNER,
        "    return None",
        "    return 0",
        "test_rank_of_returns_none_when_nothing_wanted_is_ranked",
    ),
    (
        RUNNER,
        "    index = max(0, math.ceil(fraction * len(ordered)) - 1)",
        "    index = min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))",
        "test_percentile_uses_nearest_rank_at_small_n",
    ),
    (
        RUNNER,
        "        if len(distinct) != len(selected):",
        "        if False:",
        "test_recall_summary_rejects_duplicate_rows_for_one_task",
    ),
    # --- G3's join key and its refusal to drop a row ---
    (
        RUNNER,
        "    by_task = {(r.task_id, r.corpus_size): r.candidate_set_size for r in literal_rows}",
        "    by_task = {r.task_id: r.candidate_set_size for r in literal_rows}",
        "test_cost_ratios_join_on_corpus_size_not_task_id_alone",
    ),
    (
        RUNNER,
        "        if denominator is None:",
        "        if False:",
        "test_cost_ratios_raise_rather_than_dropping_a_row_with_no_denominator",
    ),
    # --- the G1 budget diagnostic ---
    (
        RUNNER,
        "    saturable = [r for r in rows if r.n_labelled_non_primary >= r.matched_k]",
        "    saturable = [r for r in rows if r.n_labelled_non_primary > r.matched_k + 99]",
        "test_budget_diagnostic_records_that_g1_has_no_resolution",
    ),
    # --- gate bands, transcribed from the locked preregistration ---
    (
        RUNNER,
        'if g1 == "recovers" and g2 == "preserves_literal_recall":',
        'if g1 == "recovers" or g2 == "preserves_literal_recall":',
        "test_combined_recommendation_needs_both_gates",
    ),
    (
        RUNNER,
        "    if value >= G1_RECOVERS:",
        "    if value > G1_RECOVERS:",
        "test_gate_bands_match_the_preregistration",
    ),
    (
        RUNNER,
        "    if value <= G2_REGRESSES:",
        "    if value < G2_REGRESSES:",
        "test_gate_bands_match_the_preregistration",
    ),
    (
        RUNNER,
        "G1_RECOVERS = 0.50",
        "G1_RECOVERS = 0.55",
        "test_gate_thresholds_are_read_from_the_preregistration_not_transcribed",
    ),
    (
        RUNNER,
        '"n_tasks": len(stratum),',
        '"n_tasks_placeholder": len(stratum),',
        "test_summary_reports_the_stratum_size_next_to_every_stratum",
    ),
    # --- model-level invariants ---
    (
        MODELS,
        "        wrong = sorted(name for name, value in expected.items() "
        "if getattr(self, name) != value)",
        "        wrong: list[str] = []",
        "test_budget_flags_must_follow_from_the_ranks",
    ),
    (
        MODELS,
        "    primary_rank: int | None = Field(default=None, ge=1)",
        "    primary_rank: int | None = Field(default=None, ge=0)",
        "test_a_rank_of_zero_cannot_be_constructed",
    ),
    # --- generators ---
    (
        GENS,
        "    if not terms:",
        "    if False:",
        "test_fts_match_expression_is_bag_of_words_or",
    ),
    (
        GENS,
        "tokenize='porter unicode61')",
        "tokenize='unicode61')",
        "test_fts_recovers_a_morphological_variant_the_substring_matcher_cannot",
    ),
    (
        GENS,
        "    return memory.stored_value(corpus_size)",
        "    return memory.body",
        "test_every_generator_indexes_the_same_document_text",
    ),
    (
        GENS,
        "        if complete is False:",
        "        if False:",
        "test_literal_generator_rejects_an_incomplete_page",
    ),
    (
        GENS,
        "        if isinstance(total, int) and total != returned:",
        "        if False:",
        "test_literal_generator_rejects_an_incomplete_page",
    ),
    (
        GENS,
        "    if left_norm == 0.0 or right_norm == 0.0:",
        "    if False:",
        "test_cosine_is_orientation_not_magnitude",
    ),
]


def main() -> int:
    failures: list[str] = []
    for path, old, new, test in CASES:
        original = path.read_text()
        occurrences = original.count(old)
        if occurrences != 1:
            failures.append(f"MUTANT NOT UNIQUE ({occurrences}x): {test} :: {old!r}")
            continue
        path.write_text(original.replace(old, new))
        # CPython's pyc cache keys on (source mtime, source size). Two mutants
        # that change a file's byte count by the same delta inside one mtime
        # second collide, and the second run silently executes the first mutant's
        # bytecode: a false GREEN, which reads as "this test is vacuous" and gets
        # a working test deleted. Observed on 2026-08-29.
        shutil.rmtree(path.parent / "__pycache__", ignore_errors=True)
        try:
            completed = subprocess.run(
                ["uv", "run", "pytest", f"tests/test_lexical_recall.py::{test}", "-q", "-x"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            path.write_text(original)
            shutil.rmtree(path.parent / "__pycache__", ignore_errors=True)
        killed = completed.returncode != 0
        status = "RED (good)" if killed else "GREEN (VACUOUS)"
        print(f"{status:16s} {test}  <- {path.name}: {old.strip()[:58]}")
        if not killed:
            failures.append(f"VACUOUS: {test} stayed green without the guard")

    print()
    if failures:
        for line in failures:
            print(line)
        return 1
    print(f"all {len(CASES)} mutants killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
