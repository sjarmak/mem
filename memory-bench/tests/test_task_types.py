"""Unit tests for model task-type classification (mem-75t.11).

The model runner is a stub — these pin the mechanical predicate (mirror of
the TS rules), prompt assembly, response validation, and batch accounting.
"""

from membench.task_types import (
    TAXONOMY,
    BeadItem,
    classification_prompt,
    classify,
    mechanical_task_type,
    parse_classification,
)

# --- mechanical predicate (mirror of src/ingest/task-type.ts) -----------------


def test_mechanical_formula_and_step() -> None:
    assert mechanical_task_type("mol-focus-review", {}) == "mol-focus-review"
    assert (
        mechanical_task_type("Signal completion", {"gc.step_ref": "mol-do-work.drain"})
        == "mol-do-work.drain"
    )


def test_mechanical_structural_grammars() -> None:
    assert mechanical_task_type("Rollup(mem): stuff", {}) == "rollup"
    assert mechanical_task_type("input convoy for mem-1", {}) == "convoy"
    assert mechanical_task_type("anything", {"gc.synthetic": "true"}) == "convoy"
    assert mechanical_task_type("Iterate copilot review 42 on o/r PR #1", {}) == (
        "pr-review-iterate"
    )
    assert mechanical_task_type("Human review checkpoint", {}) == "review-checkpoint"
    assert mechanical_task_type("sling-gc-336i3", {}) == "sling-dispatch"


def test_mechanical_freeform_is_none() -> None:
    assert mechanical_task_type("fix: dashboard blank", {}) is None


# --- prompt + parse ------------------------------------------------------------


def test_prompt_lists_items_and_taxonomy() -> None:
    prompt = classification_prompt([BeadItem("mem-1", "mem", "fix the thing")])
    assert "mem-1 | mem | fix the thing" in prompt
    for label in TAXONOMY:
        assert label in prompt


def test_parse_keeps_only_valid_expected_labels() -> None:
    text = 'Here you go:\n{"mem-1": "bugfix", "mem-2": "nonsense", "intruder-9": "docs"}'
    result = parse_classification(text, ["mem-1", "mem-2"])
    assert result.labels == {"mem-1": "bugfix"}
    assert result.n_invalid_label == 1
    assert result.n_unknown_id == 1
    assert result.n_missing == 1


def test_parse_garbage_counts_all_missing() -> None:
    result = parse_classification("I cannot help with that", ["mem-1", "mem-2"])
    assert result.labels == {}
    assert result.n_missing == 2


# --- batch classification --------------------------------------------------------


def test_classify_batches_and_records_provenance() -> None:
    items = [BeadItem(f"mem-{i}", "mem", f"task {i}") for i in range(5)]
    prompts: list[str] = []

    def runner(prompt: str) -> str:
        prompts.append(prompt)
        import re

        ids = re.findall(r"^(mem-\d+) \|", prompt, re.MULTILINE)
        return str(dict.fromkeys(ids, "other")).replace("'", '"')

    entries, counters = classify(
        items, runner, model="haiku", classified_at="2026-06-11T00:00:00Z", batch_size=2
    )
    assert counters["batches"] == 3
    assert len(entries) == 5
    assert entries["mem-0"] == {
        "task_type": "other",
        "model": "haiku",
        "classified_at": "2026-06-11T00:00:00Z",
    }
