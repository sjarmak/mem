"""§Determinism — freeze a world with a provenance manifest, and verify reproduction.

NeMo world generation is NOT byte-reproducible (the LLM surface varies run to run),
so the FROZEN ``world.json`` is the durable artifact, not the seed. What IS
deterministic is everything downstream: given a frozen world + project, the Phase-2
materialiser re-derives byte-identical sequences from the recorded seed + args,
with no model call.

This module pins that contract:

* ``build_manifest`` records provenance (seed, NIM model, generator versions, task
  args) plus SHA-256 hashes of the frozen world, project and sequences.
* ``write_manifest`` / ``read_manifest`` persist it as ``manifest.json`` beside the
  world (the ``write_world`` location).
* ``verify_world`` reads a frozen world dir, re-hashes the frozen files (detects
  tampering) and RE-MATERIALISES the sequences from the manifest's args (detects
  materialiser drift / non-determinism) — proving the fixture reproduces its task
  instances WITHOUT NeMo. CI-safe: no SDK, no model. Re-materialisation applies to a
  corpus frozen under the generator version this tree holds; an older corpus is
  verified by its frozen hashes and the skip is reported.

Re-materialisation alone is NOT a tamper gate over the sequences (mem-r6yzk B7). It
compares the manifest to freshly generated objects and never opens
``<world>/sequences.json`` — which is the only sequence authority every downstream
consumer reads (``necessity_sweep.load_corpus_sequences``, ``public_export``). Editing
a gold value or a question in that file therefore used to pass the gate, the necessity
sweep and the export untouched. ``verify_world`` now hashes the on-disk file too,
under the manifest's own projection, so the frozen bytes and the manifest must agree.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from membench.generators.enterprise_workflow import (
    GENERATOR_VERSION as WORKFLOW_GENERATOR_VERSION,
)
from membench.generators.enterprise_workflow import (
    materialize_project_tier,
    materialize_session_tier,
)
from membench.generators.nemo.world_builder import read_world
from membench.schemas.sequence import BenchmarkSequence
from membench.schemas.world import WORLD_SCHEMA_VERSION, EnterpriseWorld, Project

# v2 adds ``tier`` / ``drop_charter`` and hashes the sequence fields BenchmarkSequence
# grew with them (``tier``, ``question_type``). v1 manifests were frozen before those
# fields existed, so hashing a freshly materialised sequence with them included would
# make every v1 fixture report "materialiser drifted" on a purely additive schema
# change. ``_hash_sequences`` therefore projects the dump back to the field set the
# recorded hash was taken over, keyed off the manifest's own schema version.
WORLD_MANIFEST_VERSION = "world-manifest.v2"
LEGACY_MANIFEST_VERSION = "world-manifest.v1"
# BenchmarkSequence fields that did not exist when a v1 manifest was hashed.
_POST_V1_SEQUENCE_FIELDS = ("tier", "question_type")
MANIFEST_FILE = "manifest.json"
# The materialised sequences frozen beside the world (the ``generate_worlds`` layout).
# Declared here, in the lowest-level module of the freeze, because ``verify_world``
# hashes it; ``necessity_sweep`` and ``public_export`` read the same file.
SEQUENCES_FILE = "sequences.json"

# The tiers a frozen world can be materialised in. "prompt" is a BenchmarkSequence
# tier but has no materialiser (a prompt-tier task needs no cross-step carry, so
# nothing freezes one), and admitting it here would let verify_world dispatch to a
# generator that does not exist.
FreezeTier = Literal["session", "project"]


def _canonical_sha256(payload: Any) -> str:
    """SHA-256 over canonical JSON (sorted keys) so the hash is stable across
    serializer key-order changes."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _hash_model(model: BaseModel) -> str:
    return _canonical_sha256(model.model_dump(mode="json"))


def _hash_sequences(sequences: list[BenchmarkSequence], *, schema_version: str) -> str:
    """Hash the sequences over the field set ``schema_version`` recorded them with.

    Raises on an unknown version rather than guessing at a projection: a silently
    wrong field set would turn a real materialiser drift into a passing verify."""
    dumps = [s.model_dump(mode="json") for s in sequences]
    if schema_version == WORLD_MANIFEST_VERSION:
        return _canonical_sha256(dumps)
    if schema_version == LEGACY_MANIFEST_VERSION:
        return _canonical_sha256(
            [{k: v for k, v in d.items() if k not in _POST_V1_SEQUENCE_FIELDS} for d in dumps]
        )
    raise ValueError(f"unknown world-manifest schema version: {schema_version!r}")


class WorldManifest(BaseModel):
    """Provenance + integrity record for one frozen world (mem-ge51)."""

    schema_version: str = WORLD_MANIFEST_VERSION
    world_schema_version: str = WORLD_SCHEMA_VERSION
    workflow_generator_version: str = WORKFLOW_GENERATOR_VERSION
    seed: int
    nim_model: str
    n_tasks: int
    facts_per_task: int
    # Whether the sequences were materialised in the tool-requiring shape (mem-31vl).
    # Load-bearing for verify_world: it must re-materialise with the same flag or the
    # sequence hashes diverge. Defaults False so manifests frozen before this field
    # (text-answer shape) still read back and verify.
    tool_requiring: bool = False
    # Which materialiser produced the sequences (mem-r6yzk.3). Load-bearing exactly as
    # ``tool_requiring`` is: verify_world dispatches on it, so a project-tier freeze
    # verified as a session-tier one would compare against a DIFFERENT object rather
    # than detect drift. Defaults "session" because every pre-tier manifest was frozen
    # by ``materialize_world``, which is what the session tier wraps.
    tier: FreezeTier = "session"
    # Project-tier only: whether the charter establishing step was omitted (the
    # Recovery variant). Recorded for the same reason as ``tier`` - re-materialising
    # with the wrong value yields different sequences, not a drift signal.
    drop_charter: bool = False
    world_sha256: str
    project_sha256: str
    sequences_sha256: str


def build_manifest(
    world: EnterpriseWorld,
    project: Project,
    sequences: list[BenchmarkSequence],
    *,
    nim_model: str,
    n_tasks: int,
    facts_per_task: int,
    seed: int | None = None,
    tool_requiring: bool = False,
    tier: FreezeTier = "session",
    drop_charter: bool = False,
) -> WorldManifest:
    """Build the manifest for a frozen world. ``seed`` defaults to the world's seed
    (the seed the materialiser used). ``tool_requiring``, ``tier`` and ``drop_charter``
    must match the arguments the sequences were materialised with so ``verify_world``
    reproduces them."""
    return WorldManifest(
        seed=world.seed if seed is None else seed,
        nim_model=nim_model,
        n_tasks=n_tasks,
        facts_per_task=facts_per_task,
        tool_requiring=tool_requiring,
        tier=tier,
        drop_charter=drop_charter,
        world_sha256=_hash_model(world),
        project_sha256=_hash_model(project),
        sequences_sha256=_hash_sequences(sequences, schema_version=WORLD_MANIFEST_VERSION),
    )


def write_manifest(manifest: WorldManifest, *, world_dir: str | Path) -> Path:
    """Write ``manifest.json`` into a frozen world dir (the ``write_world`` location)."""
    path = Path(world_dir) / MANIFEST_FILE
    path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return path


def read_manifest(world_dir: str | Path) -> WorldManifest:
    text = (Path(world_dir) / MANIFEST_FILE).read_text(encoding="utf-8")
    return WorldManifest.model_validate_json(text)


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of reproducing a frozen world. ``ok`` is true only when the frozen
    files — world, project AND ``sequences.json`` — hash to what the manifest records
    and the materialiser re-derives byte-identical sequences; ``mismatches`` names
    every failed check. ``notes`` names the checks that did not run and why, so a
    partial verification cannot read as a full one."""

    ok: bool
    mismatches: tuple[str, ...]
    notes: tuple[str, ...] = ()
    # False when the manifest's generator version is not this tree's, so the sequences
    # were checked against their frozen hashes and never re-derived. A caller that
    # reports "reproduces deterministically" has to be able to tell the two apart, and
    # "notes is non-empty" is the wrong key for that: the day a second kind of note
    # exists, every world carrying one would be counted as unreproduced.
    rematerialised: bool = True


def _frozen_sequences_mismatches(world_dir: Path, manifest: WorldManifest) -> list[str]:
    """Compare the ON-DISK ``sequences.json`` to the manifest hash.

    Read and validated with the same types ``load_corpus_sequences`` uses, then hashed
    under ``manifest.schema_version`` — the manifest's own projection — so a v1 world
    gains the check with no re-freeze and no new manifest field.

    A file that is missing, unparseable or not a list of sequences is reported as a
    mismatch rather than raised: those are tamper signals, and a sweep over a corpus
    must be able to name the one bad world instead of dying on it. Only
    ``JSONDecodeError`` and pydantic's ``ValidationError`` are caught; an ``OSError``
    is a fault in the caller's environment and propagates.
    """
    path = world_dir / SEQUENCES_FILE
    if not path.is_file():
        return [f"{SEQUENCES_FILE} missing at {path} (every freeze writes it)"]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return [f"{SEQUENCES_FILE} is not a list of sequences"]
        parsed = [BenchmarkSequence.model_validate(item) for item in raw]
    except (json.JSONDecodeError, ValidationError) as exc:
        return [f"{SEQUENCES_FILE} does not parse as frozen sequences: {exc}"]
    got = _hash_sequences(parsed, schema_version=manifest.schema_version)
    if got != manifest.sequences_sha256:
        return [
            f"{SEQUENCES_FILE} sequences_sha256 {got[:12]} != manifest "
            f"{manifest.sequences_sha256[:12]} (the frozen file was edited)"
        ]
    return []


def verify_world(world_dir: str | Path) -> VerifyResult:
    """Verify a frozen world reproduces its task instances with no model call.

    Re-hashes the frozen world/project and the frozen ``sequences.json`` (detects
    edited fixtures) and re-materialises the sequences from the manifest's seed + args
    (detects materialiser drift), comparing every hash to the manifest. The on-disk
    and re-materialised sequence checks are BOTH required: the first catches a tampered
    freeze, the second catches a drifted generator, and neither implies the other.

    Re-materialisation runs only when the manifest's ``workflow_generator_version``
    is the one this tree holds. A corpus frozen under an older generator was produced
    by code that is no longer here, so re-deriving it with today's materialiser
    compares two different task sets and would report drift on every world of every
    older corpus the moment the generator changes at all. That check is skipped and
    named in ``notes`` instead. Nothing else is relaxed: the world, project and
    on-disk ``sequences.json`` hashes are the tamper gate, they are version
    independent, and they still run."""
    world, project = read_world(world_dir)
    manifest = read_manifest(world_dir)
    mismatches: list[str] = []
    notes: list[str] = []
    rematerialised = True

    if (got := _hash_model(world)) != manifest.world_sha256:
        mismatches.append(f"world_sha256 {got[:12]} != manifest {manifest.world_sha256[:12]}")
    if (got := _hash_model(project)) != manifest.project_sha256:
        mismatches.append(f"project_sha256 {got[:12]} != manifest {manifest.project_sha256[:12]}")

    if manifest.workflow_generator_version != WORKFLOW_GENERATOR_VERSION:
        rematerialised = False
        notes.append(
            f"re-materialisation skipped: frozen under "
            f"{manifest.workflow_generator_version}, this tree has "
            f"{WORKFLOW_GENERATOR_VERSION} (frozen hashes still checked)"
        )
    else:
        # Dispatch on the recorded tier: re-materialising a project-tier freeze with
        # the session-tier generator would compare the manifest against a different
        # object.
        if manifest.tier == "session":
            sequences = materialize_session_tier(
                world,
                project,
                n_tasks=manifest.n_tasks,
                facts_per_task=manifest.facts_per_task,
                seed=manifest.seed,
                tool_requiring=manifest.tool_requiring,
            )
        else:
            sequences = materialize_project_tier(
                world,
                project,
                n_tasks=manifest.n_tasks,
                facts_per_task=manifest.facts_per_task,
                seed=manifest.seed,
                drop_charter=manifest.drop_charter,
                tool_requiring=manifest.tool_requiring,
            )
        got = _hash_sequences(sequences, schema_version=manifest.schema_version)
        if got != manifest.sequences_sha256:
            mismatches.append(
                f"sequences_sha256 {got[:12]} != manifest {manifest.sequences_sha256[:12]} "
                "(materialiser is non-deterministic or drifted)"
            )

    mismatches.extend(_frozen_sequences_mismatches(Path(world_dir), manifest))

    return VerifyResult(
        ok=not mismatches,
        mismatches=tuple(mismatches),
        notes=tuple(notes),
        rematerialised=rematerialised,
    )
