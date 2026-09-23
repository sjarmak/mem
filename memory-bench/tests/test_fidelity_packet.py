"""EvidencePacket: hash verification and line addressing."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from membench.fidelity.packet import EvidencePacket, PacketIntegrityError, number

ARTIFACT = "PRIOR_TASK.md\n"
DIGEST = hashlib.sha256(b"one\ntwo\nthree\n").hexdigest()


def _write(tmp_path: Path, text: str = "one\ntwo\nthree\n") -> Path:
    path = tmp_path / "PRIOR_TASK.md"
    path.write_text(text)
    return path


def test_loads_when_the_digest_matches(tmp_path: Path) -> None:
    packet = EvidencePacket.from_files(
        paths={"PRIOR_TASK.md": _write(tmp_path)},
        expected={"PRIOR_TASK.md": DIGEST},
    )
    assert packet.names() == ("PRIOR_TASK.md",)
    assert packet.line_count("PRIOR_TASK.md") == 3
    assert packet.digests["PRIOR_TASK.md"] == DIGEST


def test_drifted_bytes_are_a_hard_failure(tmp_path: Path) -> None:
    path = _write(tmp_path, "one\ntwo\nTHREE\n")
    with pytest.raises(PacketIntegrityError, match="drifted since the run"):
        EvidencePacket.from_files(paths={"PRIOR_TASK.md": path}, expected={"PRIOR_TASK.md": DIGEST})


def test_an_artifact_with_no_expected_digest_is_refused(tmp_path: Path) -> None:
    with pytest.raises(PacketIntegrityError, match="no expected sha256"):
        EvidencePacket.from_files(paths={"PRIOR_TASK.md": _write(tmp_path)}, expected={})


def test_a_missing_file_is_a_packet_error_not_an_oserror(tmp_path: Path) -> None:
    with pytest.raises(PacketIntegrityError, match="cannot read artifact"):
        EvidencePacket.from_files(
            paths={"PRIOR_TASK.md": tmp_path / "absent.md"},
            expected={"PRIOR_TASK.md": DIGEST},
        )


def test_span_is_one_based_and_inclusive(tmp_path: Path) -> None:
    packet = EvidencePacket.from_files(
        paths={"PRIOR_TASK.md": _write(tmp_path)},
        expected={"PRIOR_TASK.md": DIGEST},
    )
    assert packet.span("PRIOR_TASK.md", 1, 1) == "one"
    assert packet.span("PRIOR_TASK.md", 2, 3) == "two\nthree"


@pytest.mark.parametrize("line_start,line_end", [(0, 1), (2, 1), (1, 4), (4, 5)])
def test_out_of_range_spans_raise(tmp_path: Path, line_start: int, line_end: int) -> None:
    packet = EvidencePacket.from_files(
        paths={"PRIOR_TASK.md": _write(tmp_path)},
        expected={"PRIOR_TASK.md": DIGEST},
    )
    with pytest.raises(IndexError):
        packet.span("PRIOR_TASK.md", line_start, line_end)


def test_numbered_lines_match_the_span_addressing(tmp_path: Path) -> None:
    """The prompt's line numbers must be the numbers `span` accepts, or every
    citation the model makes resolves one line off."""
    packet = EvidencePacket.from_files(
        paths={"PRIOR_TASK.md": _write(tmp_path)},
        expected={"PRIOR_TASK.md": DIGEST},
    )
    rendered = packet.numbered("PRIOR_TASK.md").splitlines()
    assert rendered == ["L1\tone", "L2\ttwo", "L3\tthree"]
    for index, line in enumerate(rendered, start=1):
        prefix, text = line.split("\t", 1)
        assert prefix == f"L{index}"
        assert packet.span("PRIOR_TASK.md", index, index) == text


def test_the_record_is_numbered_by_the_same_function_as_the_evidence(tmp_path: Path) -> None:
    """The prompt addresses two things by line -- the evidence a claim cites and
    the record a claim was read from -- and a second renderer for the second one
    is how the two numberings drift apart. `numbered` is `number` over an
    artifact, and this is what says so."""
    packet = EvidencePacket.from_files(
        paths={"PRIOR_TASK.md": _write(tmp_path)},
        expected={"PRIOR_TASK.md": DIGEST},
    )
    assert number("one\ntwo\nthree\n") == packet.numbered("PRIOR_TASK.md")
    assert number("") == ""
    assert number("solo") == "L1\tsolo"


def test_an_address_cannot_be_confused_with_a_number_the_text_contains() -> None:
    """Why the prefix is there. Numbered bare, the model read
    `retrieval_extraction.py:236` out of a pasted stack frame and wrote
    `PRIOR_SOURCE_PACKET.md 236` as its citation, in a 78-line artifact, and held
    that citation through a repair that told it the size. The prompt had already
    named that confusion in words and been ignored, so the addressing changed
    instead: no line of the evidence text offers `L236`."""
    rendered = number('  File "retrieval_extraction.py", line 236, in walk')
    assert rendered.startswith("L1\t")
    assert "L236" not in rendered
