"""Tests for the V1 LOO leakage guard — the harness-owned validity invariant.

These pin the exact D6 semantics the guard shares with retrieval-v1: strict +
null-safe temporal cut, undirected supersedes closure, null-safe sibling test.
"""

import pytest

from membench.validity import (
    LeakageError,
    QueryWork,
    WorkRef,
    assert_no_leak,
    is_sibling,
    loo_bounded,
    query_from_record,
    supersedes_closure,
    work_ref_from_record,
)


def _ref(work_id, closed=None, **kw):
    return WorkRef(work_id=work_id, rig=kw.pop("rig", "rigA"), closed=closed, **kw)


def _query(started="2026-01-10T00:00:00Z", **kw):
    return QueryWork(
        work_id=kw.pop("work_id", "B"), rig=kw.pop("rig", "rigA"), started=started, **kw
    )


def test_strict_temporal_cut_excludes_boundary_equal():
    # closed == started must be EXCLUDED (strict <), or the query could see work
    # that closed at its own boundary instant.
    corpus = [
        _ref("a", closed="2026-01-09T00:00:00Z"),  # before → eligible
        _ref("b", closed="2026-01-10T00:00:00Z"),  # == boundary → excluded
        _ref("c", closed="2026-01-11T00:00:00Z"),  # after → excluded
    ]
    eligible = [r.work_id for r in loo_bounded(corpus, _query())]
    assert eligible == ["a"]


def test_null_closed_never_eligible():
    corpus = [_ref("open-work", closed=None), _ref("a", closed="2026-01-01T00:00:00Z")]
    assert [r.work_id for r in loo_bounded(corpus, _query())] == ["a"]


def test_self_excluded():
    corpus = [_ref("B", closed="2026-01-01T00:00:00Z"), _ref("a", closed="2026-01-01T00:00:00Z")]
    assert [r.work_id for r in loo_bounded(corpus, _query(work_id="B"))] == ["a"]


def test_convoy_sibling_excluded():
    corpus = [
        _ref("a", closed="2026-01-01T00:00:00Z", convoy_id="cv-1"),
        _ref("b", closed="2026-01-01T00:00:00Z", convoy_id="cv-2"),
    ]
    q = _query(convoy_id="cv-1")
    assert [r.work_id for r in loo_bounded(corpus, q)] == ["b"]


def test_pr_and_branch_siblings_excluded():
    corpus = [
        _ref("a", closed="2026-01-01T00:00:00Z", pr="gh-100"),
        _ref("b", closed="2026-01-01T00:00:00Z", external_ref="gh-branch"),
        _ref("c", closed="2026-01-01T00:00:00Z"),
    ]
    q = _query(pr="gh-100", external_ref="gh-branch")
    assert [r.work_id for r in loo_bounded(corpus, q)] == ["c"]


def test_sibling_test_is_null_safe():
    # Absence on the query side must never match absence on the record side.
    ref_no_convoy = _ref("a", closed="2026-01-01T00:00:00Z")
    assert is_sibling(ref_no_convoy, _query()) is False
    # And a record's value must not match a query that names none.
    ref_with_pr = _ref("a", closed="2026-01-01T00:00:00Z", pr="gh-1")
    assert is_sibling(ref_with_pr, _query()) is False


def test_supersedes_closure_is_undirected_and_transitive():
    # b supersedes a; c supersedes b. Querying b must exclude both a (descendant)
    # and c (ancestor) — undirected, multi-hop.
    corpus = [
        _ref("a", closed="2026-01-01T00:00:00Z"),
        _ref("b", closed="2026-01-01T00:00:00Z", supersedes=("a",)),
        _ref("c", closed="2026-01-01T00:00:00Z", supersedes=("b",)),
        _ref("d", closed="2026-01-01T00:00:00Z"),
    ]
    assert supersedes_closure(corpus, "b") == {"a", "c"}
    eligible = [r.work_id for r in loo_bounded(corpus, _query(work_id="b"))]
    assert eligible == ["d"]


def test_result_is_sorted_by_work_id():
    corpus = [
        _ref("z", closed="2026-01-01T00:00:00Z"),
        _ref("a", closed="2026-01-01T00:00:00Z"),
        _ref("m", closed="2026-01-01T00:00:00Z"),
    ]
    assert [r.work_id for r in loo_bounded(corpus, _query())] == ["a", "m", "z"]


def test_assert_no_leak_passes_for_eligible():
    corpus = [_ref("a", closed="2026-01-01T00:00:00Z")]
    assert_no_leak(["a"], corpus, _query())  # no raise


def test_assert_no_leak_raises_on_future_record():
    corpus = [_ref("future", closed="2026-02-01T00:00:00Z")]
    with pytest.raises(LeakageError) as ei:
        assert_no_leak(["future"], corpus, _query())
    assert "future" in ei.value.offenders


def test_assert_no_leak_raises_on_unknown_id():
    corpus = [_ref("a", closed="2026-01-01T00:00:00Z")]
    with pytest.raises(LeakageError):
        assert_no_leak(["ghost"], corpus, _query())


def test_work_ref_from_record_projects_loo_fields():
    record = {
        "work_id": "w1",
        "rig": "rigA",
        "external_ref": "gh-7",
        "lifecycle": {"created": "2026-01-01T00:00:00Z", "closed": "2026-01-05T00:00:00Z"},
        "links": {"convoy_id": "cv", "supersedes": ["w0"]},
        "outcome": {"pr": "gh-100"},
    }
    ref = work_ref_from_record(record)
    assert ref == WorkRef(
        work_id="w1",
        rig="rigA",
        closed="2026-01-05T00:00:00Z",
        convoy_id="cv",
        pr="gh-100",
        external_ref="gh-7",
        supersedes=("w0",),
    )


def test_query_from_record_falls_back_to_created():
    record = {"work_id": "B", "rig": "rigA", "lifecycle": {"created": "2026-01-01T00:00:00Z"}}
    q = query_from_record(record)
    assert q.started == "2026-01-01T00:00:00Z"


def test_query_from_record_raises_without_boundary():
    record = {"work_id": "B", "rig": "rigA", "lifecycle": {"status": "open"}}
    with pytest.raises(ValueError, match="leak-safe"):
        query_from_record(record)
