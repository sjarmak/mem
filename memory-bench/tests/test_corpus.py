"""Tests for the real corpus loader (hermetic — injected `mem query` runner)."""

import pytest

from membench.corpus import load_corpus, load_query_work
from membench.validity import WorkRef


def _record(
    work_id, rig="rigA", closed="2026-01-05T00:00:00Z", started="2026-01-02T00:00:00Z", **kw
):
    return {
        "work_id": work_id,
        "rig": rig,
        "lifecycle": {"created": "2026-01-01T00:00:00Z", "started": started, "closed": closed},
        "links": kw.get("links", {"supersedes": []}),
        "outcome": kw.get("outcome", {}),
        **{k: v for k, v in kw.items() if k not in ("links", "outcome")},
    }


def test_load_corpus_projects_records_to_workrefs():
    data = {"count": 2, "records": [_record("a"), _record("b", rig="rigB", external_ref="gh-9")]}
    corpus = load_corpus("store.db", runner=lambda args: data)
    assert corpus == [
        WorkRef(work_id="a", rig="rigA", closed="2026-01-05T00:00:00Z"),
        WorkRef(work_id="b", rig="rigB", closed="2026-01-05T00:00:00Z", external_ref="gh-9"),
    ]


def test_load_corpus_passes_query_args():
    captured = {}

    def runner(args):
        captured["args"] = args
        return {"records": []}

    load_corpus("/p/store.db", runner=runner)
    assert captured["args"] == ["query", "--store", "/p/store.db"]


def test_load_query_work_builds_boundary_from_record():
    data = {"records": [_record("B", started="2026-03-01T00:00:00Z")]}
    q = load_query_work("store.db", "B", runner=lambda args: data)
    assert q.work_id == "B"
    assert q.started == "2026-03-01T00:00:00Z"


def test_load_query_work_raises_on_missing():
    with pytest.raises(ValueError, match="no record for work_id"):
        load_query_work("store.db", "ghost", runner=lambda args: {"records": []})


def test_loader_requires_runner_or_bin():
    with pytest.raises(ValueError, match="injected `runner` or a `mem_bin`"):
        load_corpus("store.db")
