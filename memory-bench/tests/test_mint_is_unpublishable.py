"""The mint cannot reach a public remote, and the guard that says so has teeth.

``fixtures/mint/<corpus>.mint.json`` is the seed plus the whole alias -> internal-id
inverse: a holder can label every published candidate as the gold fact, a distractor or
a stale version, which is exactly the capability the published corpus withholds. This
repository's ``origin`` is a PUBLIC GitHub repo, so "in the repo but not in the release"
is not a boundary — a tracked mint is a published mint on the next push.

Before mem-r6yzk N3 the file was untracked AND un-ignored: ``git check-ignore`` returned
1 and ``git status`` showed ``?? memory-bench/fixtures/mint/``, so one ``git add -A``
staged 2920 mappings and the seed. The decision (``fixtures/mint/README.md``) is that the
mint is never tracked; the map is regenerable from the frozen corpus plus the seed, so
only the seed is backed up, outside the repo.

What this file enforces, and why in this shape:

* The ignore rules hold, for paths that do not exist yet as well as the one that does.
  Checking only the current file would pass the day a second corpus is minted.
* Nothing in the PUBLISH SET carries mint material, where the publish set is everything
  under ``public/``, every tracked file, and every untracked file ``git add -A`` would
  stage. A path rule alone is not enough, so both detectors read CONTENT: a copy renamed
  to ``notes.txt`` is caught the same as the original.
* Each detector is proven to fire, by planting real mint-shaped material under
  ``public/`` and asserting on it. The planted mint is SYNTHESIZED — its own seed, its
  own ids, minted by the same code path — so proving the guard works never writes the
  live secret into the published tree, not even for the length of one test.

Two detectors, because they fail in different conditions. ``_structural_findings``
recognises a mint file by its own serialized markers and needs no secret, so it runs in
CI where the mint is absent by construction. ``_secret_findings`` knows the actual seed
and the actual alias pairs, so it catches a copy that has been reshaped past every
marker; it needs a mint on disk, and says so when there is none rather than passing
quietly.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from membench.public_alias import (
    ALIAS_SCHEMA_VERSION,
    MINT_DIR,
    MINT_SUFFIX,
    PUBLIC_ID_PATTERN,
    build_alias_map,
    read_alias_map,
    write_alias_map,
)
from tests.paths import REPO_ROOT

_PUBLIC = REPO_ROOT / "public"

# The mint dir, repo-relative, for the git queries below.
_MINT_REL = MINT_DIR.relative_to(REPO_ROOT).as_posix()

# The one file under the mint dir that is allowed to be tracked: it is the decision, not
# the material.
_MINT_README = f"{_MINT_REL}/README.md"

# `PUBLIC_ID_PATTERN` is anchored because it validates a whole id; scanning a file needs
# the same shape unanchored. Derived rather than re-spelled so the two cannot drift.
_ALIAS_IN_TEXT = re.compile(PUBLIC_ID_PATTERN.pattern.strip("^$"))

# A hyphen-joined lowercase identifier, the shape every internal id has.
# `test_every_internal_id_is_visible_to_the_scanner` is what keeps that true.
_TOKEN_RUN = re.compile(r"[0-9a-z]+(?:-[0-9a-z]+)*")

# How a mint file names itself once serialized, in JSON or in any quoting a copy might
# be reshaped into. These are keyed fields, not bare words, so prose that merely DISCUSSES
# the mint (this file, the READMEs, `public_alias.py`) does not trip them.
_SCHEMA_FAMILY = ALIAS_SCHEMA_VERSION.split(".")[0]
_SCHEMA_MARKER = re.compile(rf"""["']schema_version["']\s*:\s*["']{re.escape(_SCHEMA_FAMILY)}\.""")
_SEED_FIELD = re.compile(r"""["']mint_seed["']\s*:\s*["'][0-9a-fA-F]{16,}["']""")

# An alias used as a KEY onto a bare identifier is the inverse map's own shape. Published
# records also key on aliases, but onto a sentence (`"k-...": "the deploy timeout is 15s
# — by ..."`), and a value with spaces in it is not an internal id. Counted rather than
# matched once: a single such pair occurs in documentation of the format.
_ALIAS_MAPPING_ENTRY = re.compile(
    rf"""["']{PUBLIC_ID_PATTERN.pattern.strip("^$")}["']\s*:\s*["'][0-9a-z][0-9a-z-]*["']"""
)
_MIN_MAPPING_ENTRIES = 8

# Process-unique so two agents sharing this worktree cannot delete each other's probe.
_PROBE_DIR = f"mint_guard_probe_{os.getpid()}"

# The synthesized mint the probes plant. Sixteen distinct ids is more than the one pair a
# finding needs, so a probe that fires on only some of them still reports which.
_PROBE_CORPUS = "mint-guard-probe-corpus"
_PROBE_SEED = "00112233445566778899aabbccddeeff0011223344556677"
_PROBE_INTERNAL_IDS = tuple(f"probe-world-seed{n}-task0-s0v1-probe-label" for n in range(16))


@dataclass(frozen=True)
class MintSecrets:
    """What a mint gives away: the seeds, and the alias -> internal id pairs."""

    seeds: frozenset[str]
    by_alias: Mapping[str, str]

    @property
    def max_id_parts(self) -> int:
        """Longest internal id, in hyphen-separated parts. Bounds the scan below."""
        return max((len(v.split("-")) for v in self.by_alias.values()), default=1)


def _run_git(*argv: str) -> str:
    """Run git at the repo root, failing loudly. A guard that reads a git error as an
    empty result reports a clean tree because it could not look at the tree."""
    proc = subprocess.run(
        ["git", *argv],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(f"`git {' '.join(argv)}` failed ({proc.returncode}): {proc.stderr}")
    return proc.stdout


def _git_paths(*argv: str) -> list[str]:
    return [line for line in _run_git(*argv, "-z").split("\0") if line]


def _is_ignored(rel_path: str) -> bool:
    """Whether git would ignore `rel_path`, which need not exist.

    `git check-ignore` exits 0 for a NEGATED pattern too (`!README.md` is still a match),
    so the exit code alone answers "a pattern applies", not "it is ignored". The verbose
    output names the pattern, and a leading `!` is the negation."""
    proc = subprocess.run(
        ["git", "check-ignore", "-v", "--no-index", "--", rel_path],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 1:
        return False
    if proc.returncode != 0:
        raise AssertionError(f"`git check-ignore {rel_path}` failed: {proc.stderr}")
    pattern = proc.stdout.strip().split("\t")[0].rsplit(":", 1)[-1]
    return not pattern.startswith("!")


def _publish_set() -> dict[str, str]:
    """Every file a public publish would carry, as repo-relative path -> text.

    Three sources, because each covers a case the others miss: the release tree on disk
    (a file dropped into `public/` that git has never heard of), everything tracked (the
    push itself), and everything `git add -A` would stage next (the careless commit this
    guard exists for). Binary files are decoded lossily rather than skipped — a mint
    renamed to `.png` is still a mint."""
    relative: set[str] = set()
    for path in _PUBLIC.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            relative.add(path.relative_to(REPO_ROOT).as_posix())
    relative.update(_git_paths("ls-files"))
    relative.update(_git_paths("ls-files", "--others", "--exclude-standard"))

    contents: dict[str, str] = {}
    for rel in sorted(relative):
        path = REPO_ROOT / rel
        try:
            contents[rel] = path.read_bytes().decode("utf-8", errors="replace")
        except (FileNotFoundError, IsADirectoryError):
            # A tracked path deleted in the worktree, or a submodule entry. Neither
            # carries content to leak; every other OSError is a real fault and raises.
            continue
    return contents


def _tokens(text: str, max_parts: int) -> set[str]:
    """Every hyphen-delimited identifier in `text`, including sub-runs.

    Sub-runs are the point: an internal id embedded in a longer name
    (`copy-of-world-seed3-task1-s1v1-rollback-command`) leaks exactly what the bare id
    leaks, and a scan that only looked at maximal runs would miss a copy whose keys were
    prefixed. Capped at the longest id the mint actually holds, so a pathological run
    cannot make this quadratic in its own length."""
    found: set[str] = set()
    for run in _TOKEN_RUN.findall(text):
        parts = run.split("-")
        for start in range(len(parts)):
            stop = min(start + max_parts, len(parts))
            for end in range(start + 1, stop + 1):
                found.add("-".join(parts[start:end]))
    return found


def _structural_findings(text: str) -> list[str]:
    """Mint markers that need no knowledge of any actual mint.

    The schema marker is corroboration, never a verdict on its own:
    ``tests/test_public_alias.py`` writes a literal ``public-mint.v0`` payload to test
    that ``read_alias_map`` refuses an unknown version, and a fixture that names the
    format carries no secret. The two verdict-bearing signals are the material itself —
    a seed, or enough of the inverse map to be the inverse map."""
    findings: list[str] = []
    if _SEED_FIELD.search(text):
        findings.append("a mint seed field")
    if len(_ALIAS_MAPPING_ENTRY.findall(text)) >= _MIN_MAPPING_ENTRIES:
        findings.append("a mint alias mapping")
    if findings and _SCHEMA_MARKER.search(text):
        findings.append("a mint schema marker")
    return findings


# How many de-anonymized aliases a finding names before it stops. A failure report is
# read in a terminal and written to a CI log, and the first run of this guard printed all
# 2920 pairs — 247 KB, with every internal id in it. A guard whose failure message
# republishes the secret to a build log is its own leak. The alias half of a pair is
# already published, so naming it costs nothing and locates the file; the internal id is
# the secret and is never printed, here or anywhere else in this module.
_MAX_REPORTED_PAIRS = 3


def _secret_findings(text: str, secrets: MintSecrets) -> list[str]:
    """Mint material identified by the actual secret: the seed, and any alias printed
    together with the internal id it hides. Findings are REDACTED, see above."""
    findings = ["a mint seed literal" for seed in sorted(secrets.seeds) if seed in text]
    aliases = {a for a in _ALIAS_IN_TEXT.findall(text) if a in secrets.by_alias}
    if not aliases:
        # Without an alias there is no pair, so the expensive scan is skipped. This is
        # also what keeps the frozen corpus (full of internal ids, no aliases) cheap.
        return findings
    tokens = _tokens(text, secrets.max_id_parts)
    resolved = sorted(alias for alias in aliases if secrets.by_alias[alias] in tokens)
    if resolved:
        shown = ", ".join(resolved[:_MAX_REPORTED_PAIRS])
        hidden = len(resolved) - _MAX_REPORTED_PAIRS
        more = f" (+{hidden} more)" if hidden > 0 else ""
        findings.append(f"{len(resolved)} de-anonymized alias(es), e.g. {shown}{more}")
    return findings


def _mint_files() -> list[Path]:
    """Every mint file in this worktree, wherever it was copied to."""
    return sorted(p for p in REPO_ROOT.rglob(f"*{MINT_SUFFIX}") if p.is_file())


def _live_secrets() -> MintSecrets:
    seeds: set[str] = set()
    by_alias: dict[str, str] = {}
    for path in _mint_files():
        amap = read_alias_map(path)
        seeds.add(amap.mint_seed)
        by_alias.update(amap.aliases)
    return MintSecrets(seeds=frozenset(seeds), by_alias=by_alias)


@pytest.fixture
def probe_root() -> Iterator[Path]:
    """A directory inside the published tree, removed afterwards including on failure.

    It has to be inside `public/` for the probe to be faithful: the guard scans the real
    release tree, so a probe under `tmp_path` would prove nothing about it. A leftover
    probe would sit in the release directory and fail every later run, which is why
    cleanup is in a `finally`."""
    root = _PUBLIC / _PROBE_DIR
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def probe_mint(tmp_path: Path) -> tuple[Path, MintSecrets]:
    """A real mint file over invented ids, minted by the production code path.

    Synthesized rather than copied: proving the detectors fire must never put the live
    seed or a live pair under `public/`, even transiently."""
    amap = build_alias_map(_PROBE_INTERNAL_IDS, corpus=_PROBE_CORPUS, mint_seed=_PROBE_SEED)
    path = write_alias_map(amap, path=tmp_path / f"{_PROBE_CORPUS}{MINT_SUFFIX}")
    return path, MintSecrets(seeds=frozenset({_PROBE_SEED}), by_alias=dict(amap.aliases))


# --------------------------------------------------------------------------- #
# The ignore rules.


def test_the_live_mint_is_ignored() -> None:
    present = _mint_files()
    assert present, (
        "no mint file in this worktree, so this assertion would pass over nothing. The "
        "mint is untracked by decision, so a fresh clone has none — but a worktree that "
        "has minted the public corpus must have one for the rest of this file to mean "
        "anything about the live secret."
    )
    for path in present:
        rel = path.relative_to(REPO_ROOT).as_posix()
        assert _is_ignored(rel), f"{rel} is not git-ignored; one `git add -A` publishes it"


@pytest.mark.parametrize(
    "rel_path",
    [
        f"{_MINT_REL}/a-second-corpus{MINT_SUFFIX}",
        f"{_MINT_REL}/anything-at-all.json",
        f"public/data/notes{MINT_SUFFIX}",
        f"memory-bench/results/scratch{MINT_SUFFIX}",
    ],
)
def test_a_mint_that_does_not_exist_yet_is_already_ignored(rel_path: str) -> None:
    """Ignoring one path by name would pass today and leak the next corpus minted."""
    assert _is_ignored(rel_path), f"{rel_path} would be staged by `git add -A`"


def test_the_mint_readme_stays_trackable() -> None:
    """The decision has to survive in git; only the material is excluded."""
    assert not _is_ignored(_MINT_README), (
        f"{_MINT_README} is ignored — the mint's decision record would not be in the "
        "repo, leaving only prose nobody can read after a fresh clone"
    )
    visible = set(_git_paths("ls-files")) | set(
        _git_paths("ls-files", "--others", "--exclude-standard")
    )
    assert _MINT_README in visible, f"git cannot see {_MINT_README}"


def test_no_mint_material_is_tracked_or_staged() -> None:
    """The mint dir contributes exactly one path to a commit: its README."""
    visible = set(_git_paths("ls-files")) | set(
        _git_paths("ls-files", "--others", "--exclude-standard")
    )
    under_mint = {path for path in visible if path.startswith(f"{_MINT_REL}/")}
    assert under_mint <= {_MINT_README}, (
        f"git would carry {sorted(under_mint - {_MINT_README})} out of {_MINT_REL} — "
        "that directory holds the inverse of every published id"
    )


# --------------------------------------------------------------------------- #
# The detectors, proven against planted material.


def test_the_structural_detector_finds_a_mint_renamed_past_the_ignore_rules(
    probe_root: Path, probe_mint: tuple[Path, MintSecrets]
) -> None:
    source, _ = probe_mint
    planted = probe_root / "release-notes.txt"
    planted.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    findings = _structural_findings(planted.read_text(encoding="utf-8"))
    assert sorted(findings) == [
        "a mint alias mapping",
        "a mint schema marker",
        "a mint seed field",
    ], (
        f"a whole mint file planted at {planted.name} produced findings {findings}; the "
        "structural detector no longer recognises a mint and this file is checking nothing"
    )


def test_the_schema_marker_alone_is_not_a_verdict() -> None:
    """The narrowing that keeps this guard usable, pinned so it is not widened back.

    A fixture naming the mint format is not mint material, and a guard that reds on the
    repo's own tests for the format is a guard someone deletes."""
    mentions_only = json.dumps({"schema_version": ALIAS_SCHEMA_VERSION, "aliases": {}})
    assert _structural_findings(mentions_only) == []


def test_the_secret_detector_finds_a_seed_and_a_pair_in_reshaped_material(
    probe_root: Path, probe_mint: tuple[Path, MintSecrets]
) -> None:
    """Every marker stripped, the mapping rewritten as prose: still caught."""
    _, secrets = probe_mint
    alias, internal = sorted(secrets.by_alias.items())[0]
    reshaped = (
        "build log\n"
        f"keyed with {_PROBE_SEED}\n"
        f"entry: copy-of-{internal} was published as {alias}\n"
    )
    planted = probe_root / "build.log"
    planted.write_text(reshaped, encoding="utf-8")
    assert _structural_findings(reshaped) == [], (
        "this probe is meant to have no mint markers left; if it does, it is not testing "
        "the secret detector"
    )
    findings = _secret_findings(planted.read_text(encoding="utf-8"), secrets)
    assert len(findings) == 2, f"expected the seed and the pair, got {findings}"


def test_the_secret_detector_does_not_fire_on_an_alias_alone(
    probe_mint: tuple[Path, MintSecrets],
) -> None:
    """Published records are full of aliases beside prose. If that read as a leak the
    guard would be red on the release itself and would be turned off."""
    _, secrets = probe_mint
    alias = sorted(secrets.by_alias)[0]
    text = f'{{"gold_ids": ["{alias}"], "gold": {{"{alias}": "the deploy timeout is 15s"}}}}'
    assert _secret_findings(text, secrets) == []


def test_every_internal_id_is_visible_to_the_scanner() -> None:
    """An id the tokenizer cannot see is an id the pair detector silently skips."""
    secrets = _live_secrets()
    if not secrets.by_alias:
        pytest.skip("no mint in this worktree; there are no live ids to scan for")
    unseen = [
        alias for alias, internal in secrets.by_alias.items() if not _TOKEN_RUN.fullmatch(internal)
    ]
    assert not unseen, (
        f"{len(unseen)} internal id(s) do not match the scanned token shape — the pair "
        f"detector cannot find them and reports clean. Published aliases of the first "
        f"few: {sorted(unseen)[:3]} (their internal ids are the secret and are not printed)"
    )


# --------------------------------------------------------------------------- #
# The real assertion.


def test_the_publish_set_carries_no_mint_markers() -> None:
    """Runs everywhere, including a fresh clone that has no mint at all."""
    offenders = {
        rel: findings
        for rel, text in _publish_set().items()
        if (findings := _structural_findings(text))
    }
    assert not offenders, f"mint-shaped material in the publish set: {offenders}"


def test_the_publish_set_carries_no_live_mint_secret() -> None:
    secrets = _live_secrets()
    if not secrets.seeds:
        pytest.skip(
            "no mint file in this worktree, so there is no live secret for the publish "
            "set to carry; test_the_publish_set_carries_no_mint_markers still covers a "
            "stray copy"
        )
    offenders = {
        rel: findings
        for rel, text in _publish_set().items()
        if (findings := _secret_findings(text, secrets))
    }
    assert not offenders, f"live mint material in the publish set: {offenders}"


def test_the_published_tree_reveals_no_internal_id() -> None:
    """The narrow, load-bearing half of the above, stated on its own: whatever else is
    in the repo, nothing a downloader receives may name an internal id."""
    secrets = _live_secrets()
    if not secrets.by_alias:
        pytest.skip("no mint in this worktree; the published ids cannot be resolved")
    by_internal = {internal: alias for alias, internal in secrets.by_alias.items()}
    max_parts = secrets.max_id_parts
    offenders: dict[str, str] = {}
    for path in sorted(_PUBLIC.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        text = path.read_bytes().decode("utf-8", errors="replace")
        leaked = sorted(by_internal.keys() & _tokens(text, max_parts))
        if leaked:
            # Reported by the ALIAS each leaked id belongs to. The alias is already
            # published; printing the internal id would put the secret in the build log.
            aliases = sorted(by_internal[internal] for internal in leaked)
            offenders[path.relative_to(REPO_ROOT).as_posix()] = (
                f"{len(leaked)} internal id(s), published as {aliases[:_MAX_REPORTED_PAIRS]}"
            )
    assert not offenders, f"internal ids under public/: {offenders}"


def test_the_release_ships_no_mint_file() -> None:
    """A path check, kept alongside the content ones because it names the obvious case
    in the obvious way when it fires."""
    named = [p.relative_to(REPO_ROOT).as_posix() for p in _PUBLIC.rglob(f"*{MINT_SUFFIX}")]
    assert not named, f"mint file(s) inside the published tree: {named}"
    for path in _PUBLIC.rglob("*.json"):
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(raw, dict):
            assert (
                raw.get("schema_version") != ALIAS_SCHEMA_VERSION
            ), f"{path.relative_to(REPO_ROOT)} is a mint file under a non-mint name"
