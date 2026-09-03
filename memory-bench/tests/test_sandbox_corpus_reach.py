"""mem-zfm0m item 7: the corpus a paid leg is graded against must be unreachable from the leg.

The leg's cwd is a sandbox minted empty, and the corpus reaches the agent through the prompt
only. This guard is what makes that a checked property rather than a construction accident: an
env value naming the corpus (an operator's ``PWD``, a harness variable, a ``PATH`` entry) or a
sandbox entry resolving into it would put the graded values one ``cat`` away from the agent."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from membench.runner.sandbox import (
    CorpusReachableError,
    SandboxContaminationError,
    assert_corpus_unreachable,
)


def _corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "fixtures" / "corpus"
    (corpus / "0").mkdir(parents=True)
    (corpus / "0" / "sequences.json").write_text("[]", encoding="utf-8")
    return corpus


def _clean_env(tmp_path: Path) -> dict[str, str]:
    return {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home"), "PWD": str(tmp_path / "cwd")}


def test_a_clean_env_and_an_empty_cwd_pass(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    assert_corpus_unreachable(env=_clean_env(tmp_path), cwd=cwd, corpus_root=corpus)


def test_the_guard_is_a_sandbox_contamination(tmp_path: Path) -> None:
    """One family with the ancestry guard: both refuse a sandbox the harness cannot vouch for."""
    assert issubclass(CorpusReachableError, SandboxContaminationError)


@pytest.mark.parametrize(
    "value",
    [
        "{corpus}",
        "{corpus}/0",
        "{corpus}/0/sequences.json",
        "/usr/bin:{corpus}/0:/bin",
        "{corpus}/",
    ],
    ids=["root", "shard", "file", "path-entry", "trailing-slash"],
)
def test_an_env_value_naming_the_corpus_is_refused(tmp_path: Path, value: str) -> None:
    corpus = _corpus(tmp_path)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    env = {**_clean_env(tmp_path), "MEMBENCH_LEAK": value.format(corpus=corpus)}
    with pytest.raises(CorpusReachableError, match="MEMBENCH_LEAK"):
        assert_corpus_unreachable(env=env, cwd=cwd, corpus_root=corpus)


def test_an_env_value_naming_an_ancestor_of_the_corpus_is_not_a_reach(tmp_path: Path) -> None:
    """``HOME`` contains everything; a value ABOVE the corpus names no corpus path. The reach
    this guard refuses is the corpus root or something under it."""
    corpus = _corpus(tmp_path)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    env = {**_clean_env(tmp_path), "ABOVE": str(tmp_path / "fixtures")}
    assert_corpus_unreachable(env=env, cwd=cwd, corpus_root=corpus)


def test_a_relative_env_value_reaching_the_corpus_from_the_leg_cwd_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review F3: a relative value resolves against the LEG's cwd. A sandbox that is a sibling
    of the corpus tree puts ``../fixtures/corpus/...`` one ``cat`` away, and a guard that
    skipped relative segments passed it."""
    corpus = _corpus(tmp_path)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    elsewhere = tmp_path / "elsewhere" / "deep"  # from HERE the value reaches nothing
    elsewhere.mkdir(parents=True)
    monkeypatch.chdir(elsewhere)
    env = {**_clean_env(tmp_path), "RELATIVE": "../fixtures/corpus/0/sequences.json"}
    with pytest.raises(CorpusReachableError, match="RELATIVE"):
        assert_corpus_unreachable(env=env, cwd=cwd, corpus_root=corpus)


def test_a_relative_env_value_is_not_resolved_against_the_harness_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolving against the harness process's cwd would refuse on the operator's shell: run
    from inside the corpus, ``0/sequences.json`` names the answer file there and nothing under
    the leg's cwd."""
    corpus = _corpus(tmp_path)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(corpus)
    env = {**_clean_env(tmp_path), "RELATIVE": "0/sequences.json", "DOT": "."}
    assert_corpus_unreachable(env=env, cwd=cwd, corpus_root=corpus)


def test_a_symlinked_corpus_root_is_seen_through(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    link = tmp_path / "corpus-link"
    link.symlink_to(corpus, target_is_directory=True)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    env = {**_clean_env(tmp_path), "VIA_LINK": str(link / "0")}
    with pytest.raises(CorpusReachableError, match="VIA_LINK"):
        assert_corpus_unreachable(env=env, cwd=cwd, corpus_root=corpus)


@pytest.mark.parametrize("target", ["", "0", "0/sequences.json"], ids=["root", "shard", "file"])
def test_a_cwd_entry_resolving_into_the_corpus_is_refused(tmp_path: Path, target: str) -> None:
    corpus = _corpus(tmp_path)
    cwd = tmp_path / "cwd"
    (cwd / "deeper").mkdir(parents=True)
    (cwd / "deeper" / "planted").symlink_to(corpus / target if target else corpus)
    with pytest.raises(CorpusReachableError, match="planted"):
        assert_corpus_unreachable(env=_clean_env(tmp_path), cwd=cwd, corpus_root=corpus)


def test_a_cwd_inside_the_corpus_is_refused(tmp_path: Path) -> None:
    """An EMPTY directory inside the corpus: the tree walk finds nothing under it, so only the
    check on the cwd itself can refuse this (an isolated revert of that check stayed green on a
    cwd whose entries the walk caught instead)."""
    corpus = _corpus(tmp_path)
    inside = corpus / "0" / "empty"
    inside.mkdir()
    with pytest.raises(CorpusReachableError, match=r"the leg's cwd .* is inside the corpus"):
        assert_corpus_unreachable(env=_clean_env(tmp_path), cwd=inside, corpus_root=corpus)


@pytest.mark.parametrize("target", ["fixtures", ""], ids=["parent", "tmp-root"])
def test_a_cwd_entry_resolving_to_an_ancestor_of_the_corpus_is_refused(
    tmp_path: Path, target: str
) -> None:
    """Review F3: a sandbox link to a directory ABOVE the corpus reaches it in one ``ls``; the
    walk used to accept it because the link's target is not UNDER the corpus."""
    corpus = _corpus(tmp_path)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "up").symlink_to(tmp_path / target if target else tmp_path, target_is_directory=True)
    with pytest.raises(CorpusReachableError, match="up"):
        assert_corpus_unreachable(env=_clean_env(tmp_path), cwd=cwd, corpus_root=corpus)


def test_a_copied_file_is_not_a_reach(tmp_path: Path) -> None:
    """The sandbox may hold a COPY of anything; what it may not hold is a path INTO the corpus."""
    corpus = _corpus(tmp_path)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "sequences.json").write_text((corpus / "0" / "sequences.json").read_text())
    assert_corpus_unreachable(env=_clean_env(tmp_path), cwd=cwd, corpus_root=corpus)


def test_the_message_names_the_leak_and_the_corpus(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    env = {**_clean_env(tmp_path), "PWD": str(corpus)}
    with pytest.raises(CorpusReachableError) as info:
        assert_corpus_unreachable(env=env, cwd=cwd, corpus_root=corpus)
    assert "PWD" in str(info.value) and str(corpus.resolve()) in str(info.value)
    assert os.pathsep not in "PWD"
