"""§11 enterprise-workflow materialiser (Tier 0, pure Python).

Turns a NeMo-generated ``EnterpriseWorld`` + ``Project`` (the surface/cast) into N
memory-dependent ``BenchmarkSequence``s. Mirrors ``synthetic_task`` but draws its
cast (personas, channels) from the world and authors a richer fact graph:

* establishing steps write authored "decision facts" attributed to world personas;
* one subject per task is SUPERSEDED — an earlier value (v1) is made stale by a
  newer value (v2) under a distinct id; the v2 step carries ``superseded_memory_ids``
  (the Staleness signal) and the goal depends on v2 only;
* the goal step carries ``distractor_memories`` — plausible-but-wrong values for the
  same subjects (the Confusion signal);
* the goal's ``OutcomeCheck`` requires every current id, so the task is memory-
  dependent by construction (oracle passes, no-memory cannot) and clears
  ``memory_necessity_gate``.

ZFC boundary (the generators policy): NeMo supplied only the cast and prose; every
fact, value, dependency, distractor and supersession here is authored in pure
Python and is seed-reproducible. ``distractor_memories`` / ``superseded_memory_ids``
are the authored ground truth the runner now seeds + scores Confusion/Staleness
against (mem-zt1c); the values are deliberately absent from the goal query, so a
naive top-k retriever cannot rank the truth above a distractor — that hardness is
the point.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from membench.schemas.sequence import (
    BenchmarkSequence,
    MemoryProbe,
    OutcomeCheck,
    SequenceStep,
)
from membench.schemas.world import EnterpriseWorld, Persona, Project

GENERATOR_VERSION = "enterprise-workflow.v1"


@dataclass(frozen=True)
class _Subject:
    """A decision a task must recall. ``values`` are the authored ground-truth
    candidates; the materialiser picks the current one (and a distinct stale/wrong
    one) deterministically per task."""

    key: str
    prompt: str
    values: tuple[str, ...]


# Authored decision subjects — domain-agnostic operational facts an enterprise task
# would need to recall. Each has >=3 distinct values so current/stale/distractor can
# differ.
_SUBJECTS: tuple[_Subject, ...] = (
    _Subject("deploy-timeout", "the production deploy timeout", ("15s", "30s", "45s", "60s")),
    _Subject(
        "primary-region",
        "the primary deployment region",
        ("us-east-1", "us-west-2", "eu-west-1", "ap-south-1"),
    ),
    _Subject(
        "rollback-command",
        "the approved rollback command",
        (
            "kubectl rollout undo",
            "helm rollback",
            "terraform apply -refresh",
            "git revert then redeploy",
        ),
    ),
    _Subject(
        "feature-flag",
        "the checkout_v2 feature flag state",
        ("enabled", "disabled", "canary-10%", "canary-50%"),
    ),
    _Subject("api-version", "the supported API version", ("v1", "v2", "v3", "v2-beta")),
    _Subject(
        "retention-window", "the data retention window", ("30 days", "90 days", "1 year", "7 years")
    ),
)


def _attribution(persona: Persona, channel_name: str | None) -> str:
    where = f" in #{channel_name}" if channel_name else ""
    role = f" ({persona.role})" if persona.role else ""
    return f"{persona.name}{role}{where}"


def _fact(prompt: str, verb: str, value: str, persona: Persona, channel: str | None) -> str:
    """One authored decision-fact memory content line, attributed to a world persona."""
    return f"{prompt} {verb} {value} — by {_attribution(persona, channel)}"


def _materialize_task(
    world: EnterpriseWorld,
    project: Project,
    *,
    task_index: int,
    facts_per_task: int,
    seed: int,
    charter: tuple[str, str] | None = None,
    establish_charter: bool = False,
) -> BenchmarkSequence:
    rng = random.Random((seed << 16) ^ (task_index * 2654435761))
    subjects = rng.sample(_SUBJECTS, facts_per_task)
    seq_id = f"{world.world_id}-task{task_index}"

    steps: list[SequenceStep] = []
    required_ids: list[str] = []
    probes: list[MemoryProbe] = []
    distractors: dict[str, str] = {}
    superseded: list[str] = []

    for i, subject in enumerate(subjects):
        persona = world.personas[rng.randrange(len(world.personas))]
        channel = (
            world.channels[rng.randrange(len(world.channels))].name if world.channels else None
        )
        current_value = rng.choice(subject.values)

        if i == 0:
            # The superseded subject: an earlier value (v1) then the current (v2).
            stale_value = rng.choice([v for v in subject.values if v != current_value])
            v1_id = f"{seq_id}-{subject.key}-v1"
            v2_id = f"{seq_id}-{subject.key}-v2"
            steps.append(
                SequenceStep(
                    step_id=f"{seq_id}-s{i}a-{subject.key}-old",
                    user_request=f"Record the initial value of {subject.prompt}.",
                    expected_memory_writes={
                        v1_id: _fact(subject.prompt, "was", stale_value, persona, channel)
                    },
                )
            )
            steps.append(
                SequenceStep(
                    step_id=f"{seq_id}-s{i}b-{subject.key}-new",
                    user_request=f"Record the corrected value of {subject.prompt}.",
                    expected_memory_writes={
                        v2_id: _fact(subject.prompt, "is now", current_value, persona, channel)
                    },
                    superseded_memory_ids=[v1_id],
                )
            )
            current_id = v2_id
            superseded.append(v1_id)
        else:
            current_id = f"{seq_id}-{subject.key}"
            steps.append(
                SequenceStep(
                    step_id=f"{seq_id}-s{i}-{subject.key}",
                    user_request=f"Record {subject.prompt}.",
                    expected_memory_writes={
                        current_id: _fact(subject.prompt, "is", current_value, persona, channel)
                    },
                )
            )

        required_ids.append(current_id)
        probes.append(
            MemoryProbe(
                probe_id=f"{seq_id}-probe-{subject.key}",
                expected_memory_id=current_id,
                description=f"{subject.prompt} must be recalled at the goal",
            )
        )
        # A plausible-but-wrong value for the same subject, attributed to a different
        # persona — the Confusion stressor the goal must not be fooled by.
        wrong_value = rng.choice([v for v in subject.values if v != current_value])
        other = world.personas[rng.randrange(len(world.personas))]
        distractors[f"{seq_id}-{subject.key}-distractor"] = (
            f"{other.name} recalled {subject.prompt} as {wrong_value}"
        )

    # Cross-task continuity: a project charter established in an EARLIER task that
    # this task's goal also requires. Under run_project (shared store) the earlier
    # write is visible; under isolated run_sequence it is not — that gap is the
    # continuity signal. ``establish_charter`` (task 0 only) writes it.
    if charter is not None:
        charter_id, charter_content = charter
        if establish_charter:
            steps.insert(
                0,
                SequenceStep(
                    step_id=f"{seq_id}-charter",
                    user_request="Record the project charter decision.",
                    expected_memory_writes={charter_id: charter_content},
                ),
            )
        required_ids.append(charter_id)
        probes.append(
            MemoryProbe(
                probe_id=f"{seq_id}-probe-charter",
                expected_memory_id=charter_id,
                description="the project charter (set in an earlier task) must be recalled",
            )
        )

    prompts = ", ".join(s.prompt for s in subjects)
    steps.append(
        SequenceStep(
            step_id=f"{seq_id}-goal",
            user_request=f"{project.goal} State the current value of: {prompts}.",
            expected_memory_reads=required_ids,
            outcome_checks=[
                OutcomeCheck(
                    check_id=f"{seq_id}-goal-check",
                    description="goal requires the current value of each established subject",
                    requires_memory=required_ids,
                )
            ],
            memory_probes=probes,
            distractor_memories=distractors,
            superseded_memory_ids=superseded,
        )
    )

    return BenchmarkSequence(
        sequence_id=seq_id,
        title=f"{project.name}: reconcile {facts_per_task} decisions",
        domain=world.domain,
        goal=project.goal,
        steps=steps,
    )


def _validate(
    world: EnterpriseWorld, project: Project, *, facts_per_task: int, n_tasks: int
) -> None:
    if not world.personas:
        raise ValueError(f"world {world.world_id!r} has no personas to attribute facts to")
    if not 1 <= facts_per_task <= len(_SUBJECTS):
        raise ValueError(f"facts_per_task must be in 1..{len(_SUBJECTS)}, got {facts_per_task}")
    if n_tasks < 1:
        raise ValueError(f"n_tasks must be >= 1, got {n_tasks}")
    if project.world_id != world.world_id:
        raise ValueError(
            f"project.world_id {project.world_id!r} != world.world_id {world.world_id!r}"
        )


def materialize_world(
    world: EnterpriseWorld,
    project: Project,
    *,
    n_tasks: int = 2,
    facts_per_task: int = 3,
    seed: int | None = None,
) -> list[BenchmarkSequence]:
    """Materialise ``n_tasks`` INDEPENDENT memory-dependent sequences from a world.

    Deterministic: the same (world, project, args, seed) yields byte-identical
    sequences. ``seed`` defaults to the world's seed. Each sequence is memory-
    dependent by construction and clears ``memory_necessity_gate``."""
    _validate(world, project, facts_per_task=facts_per_task, n_tasks=n_tasks)
    base_seed = world.seed if seed is None else seed
    return [
        _materialize_task(
            world, project, task_index=t, facts_per_task=facts_per_task, seed=base_seed
        )
        for t in range(n_tasks)
    ]


def materialize_project(
    world: EnterpriseWorld,
    project: Project,
    *,
    n_tasks: int = 3,
    facts_per_task: int = 3,
    seed: int | None = None,
    drop_charter: bool = False,
) -> list[BenchmarkSequence]:
    """Materialise a PROJECT: ``n_tasks`` linked by a shared charter established in
    task 0 and required by EVERY task's goal — cross-task continuity, meant to run
    under ``runner.project.run_project`` (shared store). Under isolated
    ``run_sequence`` the later tasks fail (they never wrote the charter); that gap is
    the continuity signal.

    ``drop_charter`` omits the establishing step: a missing-context (Recovery)
    variant where the charter is required but never written, so even the oracle pool
    lacks it. Recovery is a dataset annotation — the ScriptedAgent cannot re-derive
    missing memory, so the metric is for real agents under test."""
    _validate(world, project, facts_per_task=facts_per_task, n_tasks=n_tasks)
    base_seed = world.seed if seed is None else seed
    charter_id = f"{world.world_id}-charter"
    charter_content = _fact(
        "the project charter decision", "is", "freeze scope at v3", world.personas[0], None
    )
    charter = (charter_id, charter_content)
    return [
        _materialize_task(
            world,
            project,
            task_index=t,
            facts_per_task=facts_per_task,
            seed=base_seed,
            charter=charter,
            establish_charter=(t == 0 and not drop_charter),
        )
        for t in range(n_tasks)
    ]
