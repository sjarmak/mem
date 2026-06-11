"""Batch bundle assembly script (mem-75t.7.2 first real batch).

The script under test is `scripts/assemble_batch.py` -- not a package module, so it
is loaded from its file path. Pure helpers (candidate selection, histogram, report
rendering) are unit-tested; the worktree lifecycle is integration-tested against a
real temp git repo (the same no-monkeypatch idiom as test_bundle_replay).
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "assemble_batch.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("assemble_batch", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE exec: dataclasses resolves postponed annotations through
    # sys.modules[cls.__module__].
    sys.modules["assemble_batch"] = module
    spec.loader.exec_module(module)
    return module


batch = _load_script()


# --- top_candidates ---------------------------------------------------------------


def _entry(rank: int, work_id: str, mutation_calls: int) -> dict:
    return {"rank": rank, "work_id": work_id, "rig": "demo", "mutation_calls": mutation_calls}


def test_top_candidates_filters_zero_mutation_signal():
    ranking = [_entry(1, "a", 5), _entry(2, "b", 0), _entry(3, "c", 2)]
    assert batch.top_candidates(ranking, limit=25) == ("a", "c")


def test_top_candidates_respects_limit_and_rank_order():
    # Scrambled input order: rank, not list position, decides.
    ranking = [_entry(3, "c", 1), _entry(1, "a", 9), _entry(2, "b", 4), _entry(4, "d", 1)]
    assert batch.top_candidates(ranking, limit=3) == ("a", "b", "c")


# --- rejection_histogram ------------------------------------------------------------


def test_rejection_histogram_is_compact_and_count_ordered():
    rejections = [
        batch.BatchRejection(work_id="a", rig="demo", reason="shared_trace"),
        batch.BatchRejection(work_id="b", rig="demo", reason="low_replay_fidelity"),
        batch.BatchRejection(work_id="c", rig="demo", reason="shared_trace"),
        batch.BatchRejection(work_id="d", rig="demo", reason="checkout_failed"),
    ]
    assert (
        batch.rejection_histogram(rejections)
        == "SHARED_TRACE x2, CHECKOUT_FAILED x1, LOW_REPLAY_FIDELITY x1"
    )


def test_rejection_histogram_empty():
    assert batch.rejection_histogram([]) == "(none)"


# --- record_work_dir ----------------------------------------------------------------


def test_record_work_dir_prefers_provenance():
    record = {
        "provenance": {"work_dir": "/prov/dir"},
        "metadata": {"gc.work_dir": "/meta/dir"},
    }
    assert batch.record_work_dir(record, "/clone") == "/prov/dir"


def test_record_work_dir_falls_back_to_metadata_then_clone():
    assert batch.record_work_dir({"metadata": {"gc.work_dir": "/meta/dir"}}, "/clone") == (
        "/meta/dir"
    )
    assert batch.record_work_dir({}, "/clone") == "/clone"


# --- diff_line_count ----------------------------------------------------------------


def test_diff_line_count_excludes_headers():
    diff = (
        "diff --git a/f b/f\n"
        "--- a/f\n"
        "+++ b/f\n"
        "@@ -1,2 +1,2 @@\n"
        " ctx\n"
        "-old line\n"
        "+new line\n"
        "+added line\n"
    )
    assert batch.diff_line_count(diff) == 3


# --- render_report ------------------------------------------------------------------


def _admission(work_id: str) -> "batch.Admission":
    return batch.Admission(
        work_id=work_id,
        rig="demo",
        adjusted_rate=0.95,
        diff_files=3,
        diff_lines=120,
        bundle_path=f"/bundles/{work_id}.json",
    )


def test_render_report_no_go_below_gate_target():
    report = batch.render_report(
        store=Path("/store.db"),
        ranking_path=Path("/r.json"),
        candidates=("a", "b"),
        admissions=[_admission("a")],
        rejections=[batch.BatchRejection(work_id="b", rig="demo", reason="empty_output")],
        pool_size=113,
    )
    assert "NO-GO" in report
    assert "EMPTY_OUTPUT x1" in report
    assert "| a | demo | 0.95 | 3 | 120 |" in report


def test_render_report_go_at_gate_target():
    admissions = [_admission(f"w{i}") for i in range(batch.GATE_TARGET)]
    report = batch.render_report(
        store=Path("/store.db"),
        ranking_path=Path("/r.json"),
        candidates=tuple(a.work_id for a in admissions),
        admissions=admissions,
        rejections=[],
        pool_size=113,
    )
    assert "**GO**" in report


# --- process_candidate: integration against a real temp git repo ---------------------


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _make_clone(tmp_path: Path) -> tuple[Path, str]:
    clone = tmp_path / "clone"
    clone.mkdir()
    _git(clone, "init", "-q")
    _git(clone, "config", "user.email", "t@example.com")
    _git(clone, "config", "user.name", "t")
    (clone / "src").mkdir()
    (clone / "src" / "app.txt").write_text("hello old world\n", encoding="utf-8")
    _git(clone, "add", ".")
    _git(clone, "commit", "-qm", "base")
    return clone, _git(clone, "rev-parse", "HEAD")


def _make_record(base_commit: str, trace_path: Path) -> dict:
    return {
        "work_id": "t-asm-1",
        "rig": "demo",
        "title": "Swap old for new in app.txt",
        "lifecycle": {
            "created": "2026-06-01T00:00:00Z",
            "started": "2026-06-02T00:00:00Z",
            "closed": "2026-06-03T00:00:00Z",
            "status": "closed",
        },
        "links": {"deps": [], "supersedes": []},
        "provenance": {
            "work_dir": "/orig/work",
            "repo": "demo",
            "base_commit": base_commit,
        },
        "trace": {
            "jsonl_path": str(trace_path),
            "tool_outcomes": [{"runner": "pytest", "command": "pytest", "status": "pass"}],
            "errors": [],
        },
    }


def _write_transcript(tmp_path: Path) -> Path:
    event = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Edit",
                    "input": {
                        "file_path": "/orig/work/src/app.txt",
                        "old_string": "old",
                        "new_string": "new",
                    },
                }
            ]
        },
    }
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    return trace_path


def test_process_candidate_admits_and_cleans_worktree(tmp_path):
    clone, base_commit = _make_clone(tmp_path)
    record = _make_record(base_commit, _write_transcript(tmp_path))
    bundles_dir = tmp_path / "bundles"
    worktree_root = tmp_path / "wt"
    worktree_root.mkdir()

    outcome = batch.process_candidate(
        record,
        corpus=[record],
        rig_repos={"demo": clone},
        bundles_dir=bundles_dir,
        worktree_root=worktree_root,
    )

    assert isinstance(outcome, batch.Admission)
    assert outcome.adjusted_rate == 1.0
    assert outcome.diff_files == 1
    bundle = json.loads(Path(outcome.bundle_path).read_text(encoding="utf-8"))
    assert bundle["work_id"] == "t-asm-1"
    assert bundle["loo_excluded_work_ids"] == ["t-asm-1"]
    assert "new" in dict(bundle["output"]["file_diffs"])["src/app.txt"]
    # Worktree removed and the clone's worktree list is clean of bundle-asm dirs.
    assert not (worktree_root / "bundle-asm-t-asm-1").exists()
    assert batch.stale_bundle_worktrees(clone) == ()


def test_process_candidate_parses_the_transcript_once(tmp_path, monkeypatch):
    # The parsed calls feed BOTH effective_work_dir and the replay -- a second
    # parse of a multi-MB transcript is pure waste.
    clone, base_commit = _make_clone(tmp_path)
    record = _make_record(base_commit, _write_transcript(tmp_path))
    worktree_root = tmp_path / "wt"
    worktree_root.mkdir()

    calls = {"n": 0}
    real_parse = batch.parse_mutation_calls

    def counting_parse(stream):
        calls["n"] += 1
        return real_parse(stream)

    monkeypatch.setattr(batch, "parse_mutation_calls", counting_parse)
    outcome = batch.process_candidate(
        record,
        corpus=[record],
        rig_repos={"demo": clone},
        bundles_dir=tmp_path / "bundles",
        worktree_root=worktree_root,
    )
    assert isinstance(outcome, batch.Admission)
    assert calls["n"] == 1


def test_process_candidate_missing_commit_is_checkout_failed_skip(tmp_path):
    clone, _ = _make_clone(tmp_path)
    record = _make_record("0" * 40, _write_transcript(tmp_path))
    worktree_root = tmp_path / "wt"
    worktree_root.mkdir()

    outcome = batch.process_candidate(
        record,
        corpus=[record],
        rig_repos={"demo": clone},
        bundles_dir=tmp_path / "bundles",
        worktree_root=worktree_root,
    )

    assert isinstance(outcome, batch.BatchRejection)
    assert outcome.reason == batch.CHECKOUT_FAILED
    assert not (worktree_root / "bundle-asm-t-asm-1").exists()
    assert batch.stale_bundle_worktrees(clone) == ()


def test_checkout_failure_cleans_partly_created_worktree_dir(tmp_path):
    # `git worktree add` can fail AFTER creating the dest dir without registering
    # it -- such a dir is invisible to the worktree-list sweep, so the except
    # path itself must clear it.
    clone, base_commit = _make_clone(tmp_path)
    record = _make_record(base_commit, _write_transcript(tmp_path))
    worktree_root = tmp_path / "wt"
    worktree_root.mkdir()
    dest = worktree_root / "bundle-asm-t-asm-1"

    def partial_add_runner(cmd, **kwargs):
        if "add" in cmd and "worktree" in cmd:
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ".git").write_text("gitdir: nowhere\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="boom")
        return subprocess.run(cmd, **kwargs)

    outcome = batch.process_candidate(
        record,
        corpus=[record],
        rig_repos={"demo": clone},
        bundles_dir=tmp_path / "bundles",
        worktree_root=worktree_root,
        runner=partial_add_runner,
    )

    assert isinstance(outcome, batch.BatchRejection)
    assert outcome.reason == batch.CHECKOUT_FAILED
    assert not dest.exists()


def test_process_candidate_unmapped_rig_is_no_rig_clone(tmp_path):
    record = _make_record("a" * 40, _write_transcript(tmp_path))
    outcome = batch.process_candidate(
        record,
        corpus=[record],
        rig_repos={},
        bundles_dir=tmp_path / "bundles",
        worktree_root=tmp_path,
    )
    assert isinstance(outcome, batch.BatchRejection)
    assert outcome.reason == batch.NO_RIG_CLONE


# --- run_batch: stale ranking entries -------------------------------------------


def _make_store(tmp_path: Path, records: list[dict]) -> Path:
    import sqlite3

    store = tmp_path / "store.db"
    conn = sqlite3.connect(store)
    conn.execute(
        "CREATE TABLE work_records (work_id TEXT PRIMARY KEY, rig TEXT, status TEXT,"
        " trace_path TEXT, base_commit TEXT, record TEXT NOT NULL)"
    )
    for record in records:
        conn.execute(
            "INSERT INTO work_records VALUES (?, ?, ?, ?, ?, ?)",
            (
                record["work_id"],
                record["rig"],
                record["lifecycle"]["status"],
                record.get("trace", {}).get("jsonl_path"),
                (record.get("provenance") or {}).get("base_commit"),
                json.dumps(record),
            ),
        )
    conn.commit()
    conn.close()
    return store


def test_run_batch_records_stale_ranking_id_as_typed_skip(tmp_path):
    # A select-ranking.json built against an older store can rank a work_id the
    # rebuilt eligible pool no longer contains -- a recorded skip, never a KeyError.
    clone, base_commit = _make_clone(tmp_path)
    record = _make_record(base_commit, _write_transcript(tmp_path))
    store = _make_store(tmp_path, [record])
    ranking = tmp_path / "ranking.json"
    ranking.write_text(
        json.dumps(
            {
                "ranking": [
                    {"rank": 1, "work_id": "t-gone-9", "rig": "demo", "mutation_calls": 3},
                    {"rank": 2, "work_id": "t-asm-1", "rig": "demo", "mutation_calls": 1},
                ]
            }
        ),
        encoding="utf-8",
    )
    worktree_root = tmp_path / "wt"
    worktree_root.mkdir()

    admissions, rejections = batch.run_batch(
        store=store,
        ranking_path=ranking,
        bundles_dir=tmp_path / "bundles",
        report_out=tmp_path / "report.md",
        limit=25,
        rig_repos={"demo": clone},
        worktree_root=worktree_root,
    )

    assert [a.work_id for a in admissions] == ["t-asm-1"]
    stale = [r for r in rejections if r.reason == batch.STALE_RANKING]
    assert [r.work_id for r in stale] == ["t-gone-9"]
    assert "STALE_RANKING x1" in (tmp_path / "report.md").read_text(encoding="utf-8")
