"""The freeze gate cannot green on a path that holds nothing (mem-r6yzk N4).

``scripts/verify_worlds.py`` is the determinism gate: it re-hashes each frozen world and
re-materialises its sequences from the manifest. Its value is entirely in the failures it
reports, so the one result it must never produce is a pass it did not earn.

It used to produce exactly that. Given a base with no manifested worlds it printed "no
manifested worlds under <base>" and returned 0, and adversarial verification ran it
against ``fixtures/does-not-exist-at-all`` and got EXIT=0. A typo in a CI path, a corpus
moved to a new directory, an ignored fixture tree absent from a fresh clone: each reads
as a green freeze gate over zero verified worlds.

The rule now is that an absent or empty base is a FAULT, not a verdict, and the exit code
says which: 1 means "a world did not reproduce", 2 means "there was nothing to ask". A
caller that treats any non-zero as failure is unaffected; a caller that wants to tell a
broken pipeline from a broken fixture now can.

A second pass (mem-r6yzk R4) found the same class of hole one step in. Over
``fixtures/worlds-tool-jev32`` — 32 worlds frozen under ``enterprise-workflow.v3``, a
generator this tree no longer holds — the script printed "0/32 worlds reproduce
deterministically" and "32/32 verified by frozen hash only, not re-materialised", and
then exited 0. Every one of those worlds had its frozen hashes checked and none of them
was re-derived, which is the weaker of the two results the script can produce, reported
under the exit code of the stronger one. That is now exit 3, and the tests below pin
both directions: a hash-only corpus must not read as 0, and the public corpus, which
really does re-materialise all 48, must still be 0.

There is no ``--allow-empty``. These tests pin the absence as behaviour, because the flag
is the obvious thing to add the first time the gate fires in CI, and adding it would
restore the hole under a name that sounds deliberate. Every call site names a corpus that
must already be frozen, so no caller has a use for a vacuous pass.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from membench.generators.enterprise_workflow import materialize_session_tier
from membench.generators.world_manifest import (
    MANIFEST_FILE,
    SEQUENCES_FILE,
    build_manifest,
    read_manifest,
    verify_world,
    write_manifest,
)
from membench.schemas.world import EnterpriseWorld, Persona, Project, Team
from tests.paths import REPO

SCRIPT = REPO / "scripts" / "verify_worlds.py"
PUBLIC_CORPUS = "fixtures/worlds-public-v1"

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_FAULT = 2
EXIT_HASH_ONLY = 3

# A generator version this tree does not hold, which is the whole condition for the
# hash-only path: verify_world re-materialises only under its own version.
_RETIRED_GENERATOR = "enterprise-workflow.v3"
_FACTS_PER_TASK = 3


def _run(base: str) -> subprocess.CompletedProcess[str]:
    """Run the gate the way an operator and CI run it: argv, cwd and PYTHONPATH."""
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    return subprocess.run(
        [sys.executable, str(SCRIPT), base],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _freeze_world(corpus: Path, seed: int, *, generator_version: str | None = None) -> Path:
    """Freeze one world into ``corpus`` the way ``scripts/generate_worlds.py`` does.

    ``generator_version`` overwrites the manifest's ``workflow_generator_version`` after
    the freeze, which is exactly the state ``fixtures/worlds-tool-jev32`` is in: the
    frozen bytes are intact and hash to what the manifest records, and the materialiser
    that produced them is gone. Nothing hashes the manifest itself, so the three content
    hashes still match and the only thing that changes is whether re-materialisation
    runs. Built here rather than pointed at the legacy fixture so the test keeps its
    meaning after that corpus is re-frozen or removed.
    """
    world = EnterpriseWorld(
        world_id=f"w{seed}",
        domain="platform",
        org_name="Acme",
        teams=[Team(team_id="t1", name="Platform")],
        personas=[
            Persona(persona_id="p1", name="Ada Lovelace", role="staff-engineer", team_id="t1"),
            Persona(persona_id="p2", name="Grace Hopper", role="sre", team_id="t1"),
        ],
        seed=seed,
    )
    project = Project(
        project_id=f"pr{seed}",
        world_id=world.world_id,
        name="Platform hardening",
        goal="Deliver the current platform initiative.",
    )
    sequences = materialize_session_tier(world, project, n_tasks=1, facts_per_task=_FACTS_PER_TASK)
    world_dir = corpus / str(seed)
    world_dir.mkdir(parents=True)
    (world_dir / "world.json").write_text(world.model_dump_json(indent=2), encoding="utf-8")
    (world_dir / "project.json").write_text(project.model_dump_json(indent=2), encoding="utf-8")
    (world_dir / SEQUENCES_FILE).write_text(
        json.dumps([s.model_dump(mode="json") for s in sequences], indent=2), encoding="utf-8"
    )
    write_manifest(
        build_manifest(
            world,
            project,
            sequences,
            nim_model="deterministic:test",
            n_tasks=1,
            facts_per_task=_FACTS_PER_TASK,
        ),
        world_dir=world_dir,
    )
    if generator_version is not None:
        manifest = json.loads((world_dir / MANIFEST_FILE).read_text(encoding="utf-8"))
        manifest["workflow_generator_version"] = generator_version
        (world_dir / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return world_dir


def test_a_missing_base_is_a_fault_not_a_pass() -> None:
    """The verifier's own probe: a path that was never there."""
    result = _run("fixtures/does-not-exist-at-all")
    assert result.returncode == EXIT_FAULT, (
        "a freeze gate pointed at a nonexistent path reported "
        f"exit {result.returncode}:\n{result.stdout}{result.stderr}"
    )


def test_a_missing_base_names_the_path_it_could_not_find() -> None:
    """A gate that fails without naming the path sends the reader to the wrong file."""
    base = "fixtures/typo-in-the-ci-argument"
    result = _run(base)
    printed = result.stdout + result.stderr
    assert base in printed, f"the failure does not name {base}:\n{printed}"


def test_an_empty_directory_is_a_fault(tmp_path: Path) -> None:
    """The path exists and is simply empty — a corpus moved, or never generated."""
    empty = tmp_path / "empty-corpus"
    empty.mkdir()
    result = _run(str(empty))
    assert (
        result.returncode == EXIT_FAULT
    ), f"an empty corpus dir reported exit {result.returncode}:\n{result.stdout}{result.stderr}"
    assert str(empty) in result.stdout + result.stderr


def test_a_directory_of_unmanifested_subdirs_is_a_fault(tmp_path: Path) -> None:
    """Subdirectories that are not frozen worlds verify nothing, however many there are.

    This is the shape a half-finished generation run leaves behind, and the one an
    existence check alone would wave through."""
    base = tmp_path / "unmanifested"
    for name in ("world-seed0", "world-seed1"):
        (base / name).mkdir(parents=True)
        (base / name / "sequences.json").write_text("[]", encoding="utf-8")
    result = _run(str(base))
    assert result.returncode == EXIT_FAULT, (
        f"a base whose subdirs hold no {MANIFEST_FILE} reported exit {result.returncode}:"
        f"\n{result.stdout}{result.stderr}"
    )
    assert MANIFEST_FILE in result.stdout + result.stderr


def test_a_file_where_a_directory_belongs_is_a_fault(tmp_path: Path) -> None:
    not_a_dir = tmp_path / "corpus.json"
    not_a_dir.write_text("{}", encoding="utf-8")
    result = _run(str(not_a_dir))
    assert result.returncode == EXIT_FAULT, (
        f"a file passed as the corpus base reported exit {result.returncode}:"
        f"\n{result.stdout}{result.stderr}"
    )


def test_the_fault_exit_is_distinct_from_the_mismatch_exit() -> None:
    """Two different things happened, so they get two different codes. Pinned because
    collapsing them back to 1 would leave a reader unable to tell a broken fixture from a
    broken path, which is how the original hole was misread."""
    assert EXIT_FAULT != EXIT_MISMATCH


def test_there_is_no_opt_out_flag_for_an_empty_corpus() -> None:
    """The decision, as a test: reach for the flag and the gate must refuse it.

    Asking the parser, rather than reading ``--help`` for the flag's spelling. The help
    text is the module docstring, which explains why the flag is absent and therefore
    contains its name; a substring check over that output fails on the very paragraph
    that records the decision."""
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    for flag in ("--allow-empty", "--ignore-missing", "--skip-missing"):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "fixtures/does-not-exist-at-all", flag],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != EXIT_OK, (
            f"{flag} is back: an empty or missing base can be declared a pass again, "
            "which is the mem-r6yzk N4 hole under a name that sounds deliberate"
        )
        assert (
            "unrecognized arguments" in result.stderr
        ), f"{flag} was not rejected as unknown: {result.stderr}"


def test_the_help_output_still_explains_the_exit_codes() -> None:
    """``--help`` is what a CI author reads when the gate fires, and the three exit codes
    are the whole reason it distinguishes a broken path from a broken fixture."""
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == EXIT_OK, result.stderr
    assert "FAULT" in result.stdout and "VERDICT" in result.stdout


def test_the_real_corpus_still_passes() -> None:
    """The fix must not turn a working gate red. Runs the gate over the corpus it guards,
    which is also what makes the fault cases above meaningful rather than a script that
    fails on everything."""
    corpus = REPO / PUBLIC_CORPUS
    assert corpus.is_dir(), f"public corpus missing at {corpus}"
    result = _run(PUBLIC_CORPUS)
    assert result.returncode == EXIT_OK, result.stdout + result.stderr
    assert "worlds reproduce deterministically" in result.stdout
    n_worlds = len(_manifested(corpus))
    assert f"{n_worlds}/{n_worlds} worlds reproduce" in result.stdout
    assert "verified by frozen hash only" not in result.stdout, (
        "the public corpus reports a hash-only world, so the exit-0 case below no longer "
        f"pins re-materialisation:\n{result.stdout}"
    )


def _manifested(corpus: Path) -> list[Path]:
    return sorted(d for d in corpus.glob("*") if (d / MANIFEST_FILE).exists())


# --------------------------------------------------------------------------- #
# mem-r6yzk R4: "hash-checked only" must not exit like "re-materialised and matched".
# --------------------------------------------------------------------------- #


def test_a_world_frozen_under_a_retired_generator_is_verified_by_hash_alone(
    tmp_path: Path,
) -> None:
    """The premise, measured on the object rather than assumed from the fixture's name.

    Without this the exit-code test below could pass over a world that re-materialised
    normally, and would be asserting nothing about the hash-only path."""
    world_dir = _freeze_world(tmp_path / "legacy", 7, generator_version=_RETIRED_GENERATOR)
    assert read_manifest(world_dir).workflow_generator_version == _RETIRED_GENERATOR
    result = verify_world(world_dir)
    assert result.ok, result.mismatches
    assert not result.rematerialised, (
        "the world re-materialised, so its frozen generator version is one this tree "
        "still holds and the hash-only path was never taken"
    )
    assert any("re-materialisation skipped" in note for note in result.notes), result.notes


def test_a_hash_only_corpus_does_not_exit_like_a_reproduced_one(tmp_path: Path) -> None:
    """The defect: 32 legacy worlds printed "0/32 worlds reproduce deterministically"
    and exited 0, so a CI step reading the exit code read the weaker result as the
    stronger one."""
    corpus = tmp_path / "legacy-corpus"
    for seed in (7, 11):
        _freeze_world(corpus, seed, generator_version=_RETIRED_GENERATOR)
    result = _run(str(corpus))
    assert result.returncode == EXIT_HASH_ONLY, (
        "a corpus where nothing was re-materialised exited "
        f"{result.returncode}:\n{result.stdout}{result.stderr}"
    )
    assert "0/2 worlds reproduce deterministically" in result.stdout, result.stdout
    assert "2/2 verified by frozen hash only" in result.stdout, result.stdout


def test_one_hash_only_world_is_enough_to_leave_exit_zero(tmp_path: Path) -> None:
    """A mixed corpus is not a reproduced corpus. The summary line already said so and
    the exit code did not, which is the half a caller reads."""
    corpus = tmp_path / "mixed-corpus"
    _freeze_world(corpus, 7)
    _freeze_world(corpus, 11, generator_version=_RETIRED_GENERATOR)
    result = _run(str(corpus))
    assert result.returncode == EXIT_HASH_ONLY, (
        f"a corpus with one un-rematerialised world exited {result.returncode}:"
        f"\n{result.stdout}{result.stderr}"
    )
    assert "1/2 worlds reproduce deterministically" in result.stdout, result.stdout


def test_a_fully_rematerialised_corpus_still_exits_zero(tmp_path: Path) -> None:
    """The control for the two above, on a corpus this test builds: exit 0 is still
    reachable, and it means every world was re-derived."""
    corpus = tmp_path / "fresh-corpus"
    for seed in (7, 11):
        _freeze_world(corpus, seed)
    result = _run(str(corpus))
    assert result.returncode == EXIT_OK, f"{result.stdout}{result.stderr}"
    assert "2/2 worlds reproduce deterministically" in result.stdout, result.stdout
    assert "verified by frozen hash only" not in result.stdout, result.stdout


def test_a_mismatch_outranks_a_hash_only_world(tmp_path: Path) -> None:
    """A tampered world is a VERDICT and has to keep exit 1 even when another world in
    the same sweep was only hash-checked. Collapsing the two would hide a real
    reproduction failure behind the weaker code."""
    corpus = tmp_path / "tampered-corpus"
    _freeze_world(corpus, 11, generator_version=_RETIRED_GENERATOR)
    tampered = _freeze_world(corpus, 7)
    sequences = json.loads((tampered / SEQUENCES_FILE).read_text(encoding="utf-8"))
    sequences[0]["steps"][0]["user_request"] = "edited after the freeze"
    (tampered / SEQUENCES_FILE).write_text(json.dumps(sequences, indent=2), encoding="utf-8")
    result = _run(str(corpus))
    assert result.returncode == EXIT_MISMATCH, (
        f"a sweep holding a tampered world exited {result.returncode}:"
        f"\n{result.stdout}{result.stderr}"
    )


def test_the_hash_only_exit_is_distinct_from_every_other_outcome() -> None:
    """Four outcomes, four codes. Pinned because the cheap fix for the defect above is to
    reuse 1, which would make a legacy corpus indistinguishable from a drifted one."""
    assert len({EXIT_OK, EXIT_MISMATCH, EXIT_FAULT, EXIT_HASH_ONLY}) == 4


def test_the_help_output_explains_the_hash_only_exit() -> None:
    """The exit code is only useful to a CI author who can find out what it means."""
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == EXIT_OK, result.stderr
    assert "hash-only" in result.stdout, result.stdout
