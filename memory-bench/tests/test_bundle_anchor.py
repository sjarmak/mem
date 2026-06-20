"""Landing-commit anchor bundles (mem-bxhh.3.2).

The pure constructor (`anchor_bundle`) and the oracle mapper run with no IO; the
git-backed helpers (`landing_gold_diff`, `commit_subject_body`,
`materialize_rig_anchors`) run against a stub `Runner`, the injectable
subprocess seam the harbor/replay code already uses. No docker, no real clone.
"""

import json
import subprocess
from pathlib import Path

import pytest

from membench.bundle.anchor import (
    anchor_bundle,
    commit_subject_body,
    ftp_oracle_from_payload,
    landing_gold_diff,
    materialize_rig_anchors,
)
from membench.schemas.bundle import FtpOracle, TaskBundle

COMMIT = "c635ffe72c67e7c0ce4cd83c560ed5a32cc45ac7"
PARENT = "3a8d18988ea5aaaaaaaaaaaaaaaaaaaaaaaaaaaa"

ORACLE_ENTRY = {
    "commit": COMMIT,
    "parent": PARENT,
    "ftp_tests": ["t::a", "t::b", "t::c"],
    "behavioral": ["t::a", "t::b", "t::c"],
    "feature_presence": [],
    "type": "behavioral",
}


def _ftp(**overrides) -> FtpOracle:
    return ftp_oracle_from_payload({**ORACLE_ENTRY, **overrides})


def _ok(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


# --- ftp_oracle_from_payload --------------------------------------------------


def test_oracle_mapper_maps_fields_onto_model():
    ftp = _ftp()
    assert ftp.commit == COMMIT
    assert ftp.parent == PARENT
    assert ftp.behavioral == ("t::a", "t::b", "t::c")
    assert ftp.type == "behavioral"


def test_oracle_mapper_rejects_unknown_type():
    with pytest.raises(ValueError, match="unknown type"):
        ftp_oracle_from_payload({**ORACLE_ENTRY, "type": "regression"})


def test_oracle_mapper_rejects_string_for_list_field():
    # A bare string is a Sequence -- the guard must reject it, not iterate chars.
    with pytest.raises(ValueError, match="must be a list"):
        ftp_oracle_from_payload({**ORACLE_ENTRY, "behavioral": "t::a"})


def test_oracle_mapper_rejects_missing_required_field():
    entry = {k: v for k, v in ORACLE_ENTRY.items() if k != "parent"}
    with pytest.raises(ValueError, match="missing required field"):
        ftp_oracle_from_payload(entry)


def test_ftp_oracle_rejects_incoherent_split():
    # behavioral not a subset of ftp_tests -> classify_ftp's contract is violated
    with pytest.raises(ValueError, match="must equal ftp_tests"):
        FtpOracle(
            commit="c", parent="p", ftp_tests=("t::a",), behavioral=("t::b",), type="behavioral"
        )


def test_ftp_oracle_rejects_type_disagreeing_with_split():
    with pytest.raises(ValueError, match="disagrees with the split"):
        FtpOracle(
            commit="c",
            parent="p",
            ftp_tests=("t::a",),
            behavioral=("t::a",),
            type="feature-presence",
        )


def test_ftp_oracle_rejects_overlapping_partition():
    with pytest.raises(ValueError, match="overlap"):
        FtpOracle(
            commit="c",
            parent="p",
            ftp_tests=("t::a",),
            behavioral=("t::a",),
            feature_presence=("t::a",),
            type="behavioral",
        )


def test_feature_presence_oracle_has_empty_behavioral():
    ftp = _ftp(
        ftp_tests=["t::new"], behavioral=[], feature_presence=["t::new"], type="feature-presence"
    )
    assert ftp.behavioral == ()
    assert ftp.feature_presence == ("t::new",)
    assert ftp.type == "feature-presence"


# --- anchor_bundle (pure) -----------------------------------------------------


def test_anchor_bundle_is_schema_valid_and_carries_ftp():
    bundle = anchor_bundle(
        rig="codeprobe",
        repo="codeprobe",
        base_image="codeprobe-base:py3.11",
        ftp=_ftp(),
        gold_file_diffs={"src/x.py": "@@ diff @@"},
        issue_title="contract: token rollups",
    )
    assert isinstance(bundle, TaskBundle)
    assert bundle.work_id == "codeprobe-c635ffe72c67"  # <rig>-<sha12>
    assert bundle.env.base_commit == PARENT
    assert bundle.env.base_image == "codeprobe-base:py3.11"
    assert bundle.verification.ftp_oracle is not None
    assert bundle.verification.ftp_oracle.behavioral == ("t::a", "t::b", "t::c")
    # ground-truth diff, not a replay
    assert bundle.output.file_diffs == (("src/x.py", "@@ diff @@"),)
    assert bundle.output.calls == ()
    assert bundle.output.replay_success_rate == 1.0
    # self-exclusion is the whole LOO boundary for a landing commit
    assert bundle.loo_excluded_work_ids == ("codeprobe-c635ffe72c67",)
    # the mined source is the commit, not a transcript
    assert bundle.trace_ref == f"git:codeprobe@{COMMIT}"


def test_anchor_bundle_round_trips_through_json():
    bundle = anchor_bundle(
        rig="codeprobe",
        repo="codeprobe",
        base_image="img",
        ftp=_ftp(),
        gold_file_diffs={"a": "d"},
        issue_title="t",
    )
    reloaded = TaskBundle.model_validate_json(bundle.model_dump_json())
    assert reloaded == bundle


def test_anchor_bundle_rejects_empty_gold_diff():
    with pytest.raises(ValueError, match="empty gold diff"):
        anchor_bundle(
            rig="codeprobe",
            repo="codeprobe",
            base_image="img",
            ftp=_ftp(),
            gold_file_diffs={},
            issue_title="t",
        )


def test_anchor_bundle_rejects_empty_issue_title():
    with pytest.raises(ValueError, match="empty issue title"):
        anchor_bundle(
            rig="codeprobe",
            repo="codeprobe",
            base_image="img",
            ftp=_ftp(),
            gold_file_diffs={"a": "d"},
            issue_title="   ",
        )


def test_anchor_bundle_explicit_work_id_overrides_default():
    bundle = anchor_bundle(
        rig="codeprobe",
        repo="codeprobe",
        base_image="img",
        ftp=_ftp(),
        gold_file_diffs={"a": "d"},
        issue_title="t",
        work_id="codeprobe-anchor-1",
    )
    assert bundle.work_id == "codeprobe-anchor-1"
    assert bundle.loo_excluded_work_ids == ("codeprobe-anchor-1",)


# --- git helpers (stub Runner) ------------------------------------------------


def test_landing_gold_diff_splits_per_file():
    calls: list[list[str]] = []

    def runner(argv, **kwargs):
        calls.append(argv)
        if "--name-only" in argv:
            return _ok("src/a.py\0src/b.py\0")
        # per-file diff: echo which file was asked for
        path = argv[-1]
        return _ok(f"diff for {path}")

    diffs = landing_gold_diff(Path("/clone"), PARENT, COMMIT, runner=runner)
    assert diffs == {"src/a.py": "diff for src/a.py", "src/b.py": "diff for src/b.py"}
    # one name-only call + one diff call per file
    assert sum(1 for c in calls if "--name-only" in c) == 1
    assert sum(1 for c in calls if "--name-only" not in c) == 2
    # the diff range is parent..landing
    assert f"{PARENT}..{COMMIT}" in calls[0]


def test_landing_gold_diff_raises_on_git_failure():
    def runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, returncode=128, stdout="", stderr="bad rev")

    with pytest.raises(RuntimeError, match="bad rev"):
        landing_gold_diff(Path("/clone"), PARENT, COMMIT, runner=runner)


def test_commit_subject_body_strips():
    def runner(argv, **kwargs):
        fmt = next(a for a in argv if a.startswith("--format="))
        return _ok("the subject\n" if fmt.endswith("%s") else "body line\n\n")

    subject, body = commit_subject_body(Path("/clone"), COMMIT, runner=runner)
    assert subject == "the subject"
    assert body == "body line"


# --- materialize_rig_anchors (stub Runner, tmp out) ---------------------------


def test_materialize_writes_one_schema_valid_bundle_per_commit(tmp_path):
    def runner(argv, **kwargs):
        if "rev-parse" in argv:
            # echo back the requested rev with the ^{commit} peel stripped, as a
            # full SHA would resolve (here the inputs are already 40-char)
            return _ok(argv[-1].replace("^{commit}", "") + "\n")
        if "show" in argv:
            fmt = next(a for a in argv if a.startswith("--format="))
            return _ok("subj\n" if fmt.endswith("%s") else "\n")
        if "--name-only" in argv:
            return _ok("src/x.py\0")
        return _ok("@@ real diff @@")

    out = tmp_path / "bundles-codeprobe"
    written = materialize_rig_anchors(
        "codeprobe",
        Path("/clone"),
        [
            ORACLE_ENTRY,
            {
                **ORACLE_ENTRY,
                "commit": "a" * 40,
                "type": "feature-presence",
                "behavioral": [],
                "feature_presence": ["t::n"],
                "ftp_tests": ["t::n"],
            },
        ],
        out,
        repo="codeprobe",
        base_image="codeprobe-base:py3.11",
        runner=runner,
    )
    assert len(written) == 2
    for path in written:
        # each file is a schema-valid TaskBundle carrying its ftp set
        bundle = TaskBundle.model_validate_json(path.read_text())
        assert bundle.verification.ftp_oracle is not None
        assert bundle.output.file_diffs == (("src/x.py", "@@ real diff @@"),)
    first = json.loads(written[0].read_text())
    assert first["verification"]["ftp_oracle"]["behavioral"] == ["t::a", "t::b", "t::c"]


def test_materialize_expands_abbreviated_sha_to_full(tmp_path):
    full = "6f0c65e26b3659bddd2f1aad79f4ac6702223c67"

    def runner(argv, **kwargs):
        if "rev-parse" in argv:
            return _ok(full + "\n")  # abbreviation -> full SHA
        if "show" in argv:
            return _ok("subj\n")
        if "--name-only" in argv:
            return _ok("src/x.py\0")
        return _ok("@@ diff @@")

    out = tmp_path / "b"
    written = materialize_rig_anchors(
        "codeprobe",
        Path("/clone"),
        [{**ORACLE_ENTRY, "commit": "6f0c65"}],
        out,
        repo="codeprobe",
        base_image="img",
        runner=runner,
    )
    bundle = TaskBundle.model_validate_json(written[0].read_text())
    assert bundle.work_id == f"codeprobe-{full[:12]}"
    assert bundle.trace_ref == f"git:codeprobe@{full}"
    assert bundle.verification.ftp_oracle.commit == full
