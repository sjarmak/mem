"""The release's integrity statement has to cover the release.

``public/data/SHA256SUMS`` covers the two JSONL files, and ``public/README.md`` told a
downloader that checking it verified the release. It did not. The schema the validator
reads, the README stating the contract, and above all
``public/validator/membench_validate.py`` - the one file the instructions say to EXECUTE
- were all outside it. A validator edited in transit would have cleared the corpus it
was edited to clear, and the digest a downloader was pointed at would have said OK,
because that digest never looked at the validator.

``public/SHA256SUMS`` closes that: every published file, coreutils format, paths
relative to ``public/``, and the data digest sealed inside it so the two cannot drift.
Both ``sha256sum -c`` invocations keep working, the outer one from ``public/`` and the
inner one from ``public/data``.

The second half is the publish SET. ``public/validator/__pycache__/`` appears the moment
any test imports the validator by path, and it shipped: gitignored, unsealed, and
inside the directory a release is cut from. ``membench.public_seal`` refuses to seal a
tree carrying a file that is not published source, so a release carrying one cannot be
sealed and an unsealed release is not a release. ``tests/conftest.py`` stops the write
at its source as well; this file asserts the refusal, which is the end that holds when
prevention fails."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from membench.public_seal import (
    SEAL_FILE,
    is_publishable,
    parse_seal,
    seal_text,
    unpublishable_paths,
    verify_seal,
    write_seal,
)
from tests.paths import REPO, REPO_ROOT

PUBLIC = REPO_ROOT / "public"

# The published set, stated structurally rather than read off the seal, so a file that
# stopped being sealed fails here instead of agreeing with itself. The top-level README
# plus the three published directories IS the release; anything else at the top level is
# a probe or an artefact and belongs to no downloader.
PUBLISHED_DIRS = ("data", "schema", "validator")
PUBLISHED_TOP_LEVEL_FILES = ("README.md",)

# The file the whole finding is about: the one a downloader is instructed to run.
EXECUTED_FILE = "validator/membench_validate.py"


def _published_set() -> set[str]:
    """Every file of the release, as a path relative to ``public/``.

    Walks only the three published directories. ``test_public_tree_is_gated`` plants a
    transient package at the TOP level of ``public/`` while it runs, so a walk of the
    whole tree would race with it; the release never puts a file there, so this walk
    cannot see it."""
    paths = {name for name in PUBLISHED_TOP_LEVEL_FILES if (PUBLIC / name).is_file()}
    for directory in PUBLISHED_DIRS:
        root = PUBLIC / directory
        paths.update(
            path.relative_to(PUBLIC).as_posix() for path in root.rglob("*") if path.is_file()
        )
    return paths


@pytest.fixture
def sealed_copy(tmp_path: Path) -> Path:
    """A private copy of the published tree, seal included.

    Built from the seal's own path list rather than by copying the directory, for the
    same reason ``_published_set`` walks three directories: nothing transient can be
    copied in, so a tamper applied below is the only difference from the release."""
    out = tmp_path / "public"
    for path in [SEAL_FILE, *parse_seal((PUBLIC / SEAL_FILE).read_text(encoding="utf-8"))]:
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PUBLIC / path, target)
    return out


def test_the_release_is_sealed() -> None:
    """The seal exists and agrees with the tree, which is what ``--check`` reports."""
    assert (PUBLIC / SEAL_FILE).is_file(), (
        f"no {SEAL_FILE} at the root of the published tree; the release has no integrity "
        "statement covering the files a downloader runs"
    )
    assert verify_seal(PUBLIC) == []


def test_the_seal_covers_every_published_file() -> None:
    """Exactly the published set, no more and no less.

    A path sealed but absent would be a digest nobody can check; a path published but
    unsealed is the defect this file exists for, and the validator was one."""
    sealed = set(parse_seal((PUBLIC / SEAL_FILE).read_text(encoding="utf-8")))
    published = _published_set()
    assert sealed == published, (
        f"sealed and not published: {sorted(sealed - published)}; published and not "
        f"sealed: {sorted(published - sealed)}"
    )


def test_the_file_a_downloader_executes_is_sealed() -> None:
    """The finding itself, as its own row.

    ``public/README.md`` tells a downloader to run the validator. A release that
    publishes an executable outside its own integrity statement is asking to be trusted
    about the one file that decides whether anything else can be."""
    sealed = parse_seal((PUBLIC / SEAL_FILE).read_text(encoding="utf-8"))
    assert EXECUTED_FILE in sealed, f"{EXECUTED_FILE} is not covered by {SEAL_FILE}"
    digest = hashlib.sha256((PUBLIC / EXECUTED_FILE).read_bytes()).hexdigest()
    assert sealed[EXECUTED_FILE] == digest


def test_the_data_digest_is_sealed_by_the_release_digest() -> None:
    """The inner digest is a published file like any other.

    Both digests stay live, so the outer one has to cover the inner one or a rewritten
    corpus plus a rewritten ``data/SHA256SUMS`` would check out."""
    sealed = parse_seal((PUBLIC / SEAL_FILE).read_text(encoding="utf-8"))
    assert "data/SHA256SUMS" in sealed


def test_both_digests_check_out_under_coreutils() -> None:
    """``sha256sum -c`` is what the README tells a downloader to run, so run it.

    From ``public/`` for the release and from ``public/data`` for the corpus alone: a
    consumer who took only the data files keeps the command they had."""
    for directory in (PUBLIC, PUBLIC / "data"):
        done = subprocess.run(
            ["sha256sum", "-c", SEAL_FILE],
            cwd=directory,
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, f"{directory}: {done.stdout}{done.stderr}"


def test_no_build_artefact_is_published() -> None:
    """Nothing under the published directories is anything but source.

    ``public/validator/__pycache__/membench_validate.cpython-312.pyc`` was there, put
    there by the test suite importing the validator by path, and gitignored so no status
    ever showed it."""
    strays = sorted(path for path in _published_set() if not is_publishable(path))
    assert strays == [], f"not published source: {strays}"


def test_running_the_suite_leaves_no_bytecode_in_the_published_tree() -> None:
    """The prevention half, asserted from inside a run that has imported the validator.

    Four test modules import it by path; by the time this file runs, at least this
    session's own conftest redirect has had to hold, or a ``__pycache__`` would be
    sitting next to it right now."""
    assert list(PUBLIC.rglob("__pycache__")) == []
    assert list(PUBLIC.rglob("*.pyc")) == []


def test_the_seal_refuses_a_tree_carrying_an_artefact(sealed_copy: Path) -> None:
    """The by-construction half: a tree with an artefact cannot be sealed at all.

    Not filtered out of the digest, which would ship it unsealed under a digest file
    claiming to cover the release. Refused, so the publish step fails."""
    artefact = sealed_copy / "validator" / "__pycache__" / "membench_validate.cpython-312.pyc"
    artefact.parent.mkdir(parents=True, exist_ok=True)
    artefact.write_bytes(b"\x00compiled")
    assert unpublishable_paths(sealed_copy) == [
        "validator/__pycache__/membench_validate.cpython-312.pyc"
    ]
    with pytest.raises(ValueError, match="not published source"):
        seal_text(sealed_copy)
    with pytest.raises(ValueError, match="not published source"):
        write_seal(sealed_copy)


def test_an_edited_validator_fails_the_seal(sealed_copy: Path) -> None:
    """The threat the finding names: the executable edited in transit.

    The edit is a no-op comment, so the validator still imports, still validates the
    corpus, and still reports OK. Nothing inside the release contradicts it. The seal
    is the only thing that does."""
    target = sealed_copy / EXECUTED_FILE
    target.write_text(target.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
    problems = verify_seal(sealed_copy)
    assert any(
        EXECUTED_FILE in problem and "seal says" in problem for problem in problems
    ), problems


def test_an_edited_data_digest_fails_the_seal(sealed_copy: Path) -> None:
    """A corpus rewritten consistently WITH its own digest still fails the outer seal.

    ``data/SHA256SUMS`` is the tamperer's obvious second edit once they have rewritten a
    JSONL file, and before this release it was the last word."""
    corpus = sealed_copy / "data" / "synthetic-session.jsonl"
    lines = corpus.read_text(encoding="utf-8").splitlines(keepends=True)
    corpus.write_text("".join(lines[:-1]), encoding="utf-8")
    digest = hashlib.sha256(corpus.read_bytes()).hexdigest()
    inner = sealed_copy / "data" / SEAL_FILE
    inner.write_text(
        "".join(
            f"{digest}  {name}\n" if name == corpus.name else f"{sha}  {name}\n"
            for name, sha in parse_seal(inner.read_text(encoding="utf-8")).items()
        ),
        encoding="utf-8",
    )
    assert (
        subprocess.run(
            ["sha256sum", "-c", SEAL_FILE],
            cwd=sealed_copy / "data",
            capture_output=True,
            check=False,
        ).returncode
        == 0
    ), "the inner digest was supposed to still check out"
    problems = verify_seal(sealed_copy)
    assert any("data/SHA256SUMS" in problem for problem in problems), problems


def test_the_seal_script_checks_and_rewrites_the_same_bytes(sealed_copy: Path) -> None:
    """``--check`` passes on the shipped tree, and re-sealing is idempotent.

    Idempotence is what lets the seal be part of the publish recipe: the documented
    double export can run it in both directories and still diff to nothing."""
    before = (sealed_copy / SEAL_FILE).read_bytes()
    assert verify_seal(sealed_copy) == []
    write_seal(sealed_copy)
    assert (sealed_copy / SEAL_FILE).read_bytes() == before

    script = REPO / "scripts" / "seal_public_release.py"
    done = subprocess.run(
        [sys.executable, str(script), "--check", str(sealed_copy)],
        cwd=REPO,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(REPO), "PATH": "/usr/bin:/bin"},
        check=False,
    )
    assert done.returncode == 0, f"{done.stdout}{done.stderr}"
    assert "OK" in done.stdout
