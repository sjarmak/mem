"""Unit tests for bead-linked transcript archival (mem-75t.4)."""

import gzip
import os
from pathlib import Path

import pytest

from membench.transcript_archive import (
    MANIFEST_NAME,
    archive_name,
    archive_transcripts,
    load_manifest,
    restore_pruned,
    restore_transcript,
)


def _make_transcript(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_archive_and_skip_unchanged(tmp_path: Path) -> None:
    src = _make_transcript(tmp_path / "proj" / "abc.jsonl", '{"a":1}\n')
    dest = tmp_path / "archive"

    first = archive_transcripts([src], dest)
    assert first.archived == 1
    assert first.skipped_unchanged == 0

    second = archive_transcripts([src], dest)
    assert second.archived == 0
    assert second.skipped_unchanged == 1

    name = archive_name(src)
    with gzip.open(dest / name, "rt", encoding="utf-8") as handle:
        assert handle.read() == '{"a":1}\n'


def test_changed_source_is_refreshed(tmp_path: Path) -> None:
    src = _make_transcript(tmp_path / "proj" / "abc.jsonl", "v1\n")
    dest = tmp_path / "archive"
    archive_transcripts([src], dest)

    src.write_text("v1\nv2\n", encoding="utf-8")
    os.utime(src, ns=(src.stat().st_atime_ns, src.stat().st_mtime_ns + 1_000_000))
    report = archive_transcripts([src], dest)
    assert report.refreshed == 1

    with gzip.open(dest / archive_name(src), "rt", encoding="utf-8") as handle:
        assert handle.read() == "v1\nv2\n"
    # manifest keeps the latest entry for the path
    assert load_manifest(dest)[str(src)]["size"] == src.stat().st_size


def test_missing_source_is_counted_not_fatal(tmp_path: Path) -> None:
    report = archive_transcripts([tmp_path / "gone.jsonl"], tmp_path / "archive")
    assert report.missing == 1
    assert report.archived == 0


def test_same_filename_different_dirs_do_not_collide(tmp_path: Path) -> None:
    a = _make_transcript(tmp_path / "p1" / "agent-1.jsonl", "one\n")
    b = _make_transcript(tmp_path / "p2" / "agent-1.jsonl", "two\n")
    dest = tmp_path / "archive"
    report = archive_transcripts([a, b], dest)
    assert report.archived == 2
    assert archive_name(a) != archive_name(b)


def test_manifest_records_sha_and_restore_roundtrip(tmp_path: Path) -> None:
    src = _make_transcript(tmp_path / "proj" / "abc.jsonl", '{"line":1}\n{"line":2}\n')
    dest = tmp_path / "archive"
    archive_transcripts([src], dest)

    entry = load_manifest(dest)[str(src)]
    assert len(entry["sha256"]) == 64

    out = tmp_path / "restored.jsonl"
    restore_transcript(dest, str(src), out)
    assert out.read_text(encoding="utf-8") == '{"line":1}\n{"line":2}\n'


def test_restore_unknown_path_raises(tmp_path: Path) -> None:
    dest = tmp_path / "archive"
    dest.mkdir()
    (dest / MANIFEST_NAME).write_text("", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        restore_transcript(dest, "/nope.jsonl", tmp_path / "out.jsonl")


# --- pruned-transcript restore (mem-qw5: corpus window extension) -----------------------


def test_restore_pruned_restores_only_missing_sources(tmp_path: Path) -> None:
    live = _make_transcript(
        tmp_path / "proj" / "11111111-aaaa-bbbb-cccc-000000000001.jsonl", "live\n"
    )
    pruned = _make_transcript(
        tmp_path / "proj" / "22222222-aaaa-bbbb-cccc-000000000002.jsonl", "old\n"
    )
    dest = tmp_path / "archive"
    archive_transcripts([live, pruned], dest)
    pruned.unlink()  # the rolling window pruned it

    restored = restore_pruned(dest)

    assert [r.source for r in restored] == [str(pruned)]
    (item,) = restored
    # Restored under the archive, named back to the original transcript filename
    # (stem == session uuid), content intact.
    assert item.path.is_relative_to(dest)
    assert item.path.name == pruned.name
    assert item.path.read_text(encoding="utf-8") == "old\n"


def test_restore_pruned_is_idempotent(tmp_path: Path) -> None:
    pruned = _make_transcript(tmp_path / "proj" / "abc.jsonl", "x\n")
    dest = tmp_path / "archive"
    archive_transcripts([pruned], dest)
    pruned.unlink()

    first = restore_pruned(dest)
    mtime = first[0].path.stat().st_mtime_ns
    second = restore_pruned(dest)
    assert [r.path for r in second] == [r.path for r in first]
    assert second[0].path.stat().st_mtime_ns == mtime  # not rewritten


def test_restore_pruned_same_filename_different_dirs(tmp_path: Path) -> None:
    a = _make_transcript(tmp_path / "p1" / "abc.jsonl", "a\n")
    b = _make_transcript(tmp_path / "p2" / "abc.jsonl", "b\n")
    dest = tmp_path / "archive"
    archive_transcripts([a, b], dest)
    a.unlink()
    b.unlink()

    restored = restore_pruned(dest)
    assert len(restored) == 2
    assert {r.path.read_text(encoding="utf-8") for r in restored} == {"a\n", "b\n"}
    assert len({r.path for r in restored}) == 2  # digest dirs keep them apart
