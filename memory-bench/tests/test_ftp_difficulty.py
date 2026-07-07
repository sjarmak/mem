"""Difficulty banding for ftp-oracle anchor bundles (mem-on3f)."""

from membench.bundle.replay import ReplayResult
from membench.grading.ftp_difficulty import band_pool, ftp_test_count, gold_file_count
from membench.schemas.bundle import BundleEnv, BundleVerification, FtpOracle, TaskBundle


def _bundle(work_id: str, *, n_files: int, n_tests: int) -> TaskBundle:
    file_diffs = tuple((f"src/f{i}.py", "x") for i in range(n_files))
    tests = tuple(f"tests/test_x.py::test_{i}" for i in range(n_tests))
    return TaskBundle(
        work_id=work_id,
        rig="demo",
        issue_title="t",
        issue_body="",
        trace_ref="/tmp/t.jsonl",
        output=ReplayResult(calls=(), file_diffs=file_diffs, replay_success_rate=0.0),
        env=BundleEnv(repo="demo", base_commit="0" * 40, base_image="python:3.12-bookworm"),
        loo_excluded_work_ids=(work_id,),
        verification=BundleVerification(
            ftp_oracle=FtpOracle(
                commit="1" * 40,
                parent="2" * 40,
                ftp_tests=tests,
                behavioral=tests,
                type="behavioral",
            )
        ),
    )


def test_gold_file_count_and_ftp_test_count() -> None:
    bundle = _bundle("demo-a", n_files=3, n_tests=5)
    assert gold_file_count(bundle) == 3
    assert ftp_test_count(bundle) == 5


def test_ftp_test_count_zero_when_no_oracle() -> None:
    bundle = _bundle("demo-a", n_files=1, n_tests=1).model_copy(
        update={"verification": BundleVerification(ftp_oracle=None)}
    )
    assert ftp_test_count(bundle) == 0


def test_band_pool_splits_into_tertiles_by_gold_file_count() -> None:
    # 9 bundles, gold-file counts 1..9 -- a clean tertile split.
    bundles = [_bundle(f"demo-{i}", n_files=i, n_tests=1) for i in range(1, 10)]
    stats = band_pool(bundles)
    by_id = {s.work_id: s for s in stats}
    assert [by_id[f"demo-{i}"].band for i in range(1, 4)] == ["easy"] * 3
    assert [by_id[f"demo-{i}"].band for i in range(4, 7)] == ["medium"] * 3
    assert [by_id[f"demo-{i}"].band for i in range(7, 10)] == ["hard"] * 3


def test_band_pool_ties_break_deterministically_by_work_id() -> None:
    # All bundles share the same gold-file count -- the ordering (and therefore
    # the band split) must be driven by work_id, not dict/glob order.
    bundles = [_bundle(f"demo-{i}", n_files=1, n_tests=1) for i in (3, 1, 2)]
    stats_a = band_pool(bundles)
    stats_b = band_pool(list(reversed(bundles)))
    assert [s.band for s in stats_a] == [s.band for s in stats_b]
    assert [s.work_id for s in stats_a] == sorted(b.work_id for b in bundles)


def test_band_pool_handles_pool_smaller_than_three() -> None:
    bundles = [_bundle("demo-a", n_files=1, n_tests=1)]
    stats = band_pool(bundles)
    assert len(stats) == 1
    assert stats[0].band in ("easy", "medium", "hard")


def test_band_pool_empty_pool() -> None:
    assert band_pool([]) == []
