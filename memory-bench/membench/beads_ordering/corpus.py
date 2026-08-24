from __future__ import annotations

from dataclasses import dataclass

from membench.beads_ordering.models import FrozenCorpus, MemoryFixture, OrderingTask


@dataclass(frozen=True)
class _Scenario:
    slug: str
    corpus_size: int
    query: str
    expected: str
    forbidden: str
    match_count: int
    primary_index: int
    entry_index: int
    instruction: str


_SCENARIOS = (
    _Scenario(
        "lease-renewal",
        50,
        "lease renewal",
        "LEASE_TTL=90s",
        "LEASE_TTL=30s",
        12,
        47,
        31,
        "Choose the safe worker lease configuration for the scheduler rollout.",
    ),
    _Scenario(
        "schema-shadow",
        50,
        "schema shadow",
        "SHADOW_TABLE_SUFFIX=_next",
        "SHADOW_TABLE_SUFFIX=_tmp",
        20,
        44,
        28,
        "Plan the online schema cutover without colliding with cleanup tables.",
    ),
    _Scenario(
        "snapshot-restore",
        50,
        "snapshot restore",
        "RESTORE_VERIFY=manifest-first",
        "RESTORE_VERIFY=checksum-last",
        30,
        49,
        35,
        "State the required verification order before restoring the production snapshot.",
    ),
    _Scenario(
        "worker-drain",
        50,
        "worker drain",
        "DRAIN_GRACE=45s",
        "DRAIN_GRACE=kill-immediate",
        40,
        46,
        38,
        "Select the drain behavior for replacing a saturated worker pool.",
    ),
    _Scenario(
        "migration-freeze",
        100,
        "migration freeze",
        "FREEZE_SENTINEL=MIGRATION-FREEZE",
        "FREEZE_SENTINEL=.freeze",
        20,
        98,
        62,
        "Name the repository sentinel that must gate all write commands during migration.",
    ),
    _Scenario(
        "cache-stampede",
        100,
        "cache stampede",
        "CACHE_LOCK_TTL=12s",
        "CACHE_LOCK_TTL=60s",
        35,
        91,
        55,
        "Choose the lock TTL for the cache stampede guard in the API gateway.",
    ),
    _Scenario(
        "outbox-replay",
        100,
        "outbox replay",
        "OUTBOX_CURSOR=commit_lsn",
        "OUTBOX_CURSOR=created_at",
        55,
        96,
        70,
        "Specify the durable cursor for replaying the PostgreSQL outbox after failover.",
    ),
    _Scenario(
        "hook-isolation",
        100,
        "hook isolation",
        "HOOKS_PATH=.git/hooks",
        "HOOKS_PATH=global",
        70,
        88,
        60,
        "State how temporary git repositories must isolate hooks in tests.",
    ),
    _Scenario(
        "index-backfill",
        500,
        "index backfill",
        "BACKFILL_BATCH=750",
        "BACKFILL_BATCH=10000",
        50,
        488,
        210,
        "Choose the production-safe batch size for the search-index backfill.",
    ),
    _Scenario(
        "replica-failover",
        500,
        "replica failover",
        "FAILOVER_FENCE=epoch",
        "FAILOVER_FENCE=timestamp",
        100,
        470,
        180,
        "Identify the fencing token required when promoting a replica.",
    ),
    _Scenario(
        "artifact-quarantine",
        500,
        "artifact quarantine",
        "QUARANTINE_RETENTION=14d",
        "QUARANTINE_RETENTION=forever",
        160,
        499,
        320,
        "Set the retention rule for artifacts rejected by the provenance verifier.",
    ),
    _Scenario(
        "session-provenance",
        500,
        "session provenance",
        "PROVENANCE_BASE=commit-by-date",
        "PROVENANCE_BASE=branch-tip",
        220,
        450,
        250,
        "Choose the base-commit rule for reconstructing an agent session.",
    ),
)


def _candidate_indices(scenario: _Scenario, reserved: set[int], *, tier_start: int) -> list[int]:
    candidates = [scenario.primary_index, scenario.entry_index]
    ordered_indices = (*range(tier_start, scenario.corpus_size), *range(tier_start))
    for index in ordered_indices:
        if index in reserved or index in candidates:
            continue
        candidates.append(index)
        if len(candidates) == scenario.match_count:
            break
    return candidates


def build_frozen_corpus(*, seed: int = 5877) -> FrozenCorpus:
    if seed != 5877:
        raise ValueError("the authored Beads ordering corpus is frozen at seed 5877")
    titles = [f"Software factory operating note {index + 1:03d}" for index in range(500)]
    aliases: list[list[str]] = [[] for _ in range(500)]
    bodies = [
        f"Operational note {index + 1}. Applies to routine CI, deployment, storage, "
        "or agent maintenance work."
        for index in range(500)
    ]
    references: list[list[str]] = [[] for _ in range(500)]
    lifecycle = ["active"] * 500
    navigation_ranks = [1000 + index for index in range(500)]
    provenance = ["human" if index % 2 == 0 else "agent" for index in range(500)]
    reserved = {scenario.primary_index for scenario in _SCENARIOS} | {
        scenario.entry_index for scenario in _SCENARIOS
    }
    tasks: list[OrderingTask] = []

    for ordinal, scenario in enumerate(_SCENARIOS):
        tier_start = {50: 0, 100: 50, 500: 100}[scenario.corpus_size]
        tier_width = scenario.corpus_size - tier_start
        candidates = _candidate_indices(scenario, reserved, tier_start=tier_start)
        primary_id = f"mem-{scenario.primary_index + 1:04d}"
        entry_id = f"mem-{scenario.entry_index + 1:04d}"
        if ordinal % 3 == 0:
            titles[scenario.primary_index] = "Corrected production safeguard"
            query_repetitions = scenario.query
        else:
            titles[scenario.primary_index] = (
                f"Corrected production rule for {scenario.slug.replace('-', ' ')}"
            )
            query_repetitions = (
                scenario.query if ordinal % 3 == 1 else f"{scenario.query} {scenario.query}"
            )
        bodies[scenario.primary_index] += (
            f" {query_repetitions}. The accepted correction is "
            f"{scenario.expected}. Do not use {scenario.forbidden}; that value came from the "
            "retired rollout."
        )
        navigation_ranks[scenario.primary_index] = 100 + ordinal

        titles[scenario.entry_index] = f"{scenario.query.title()} navigation map"
        aliases[scenario.entry_index].append(scenario.query)
        bodies[scenario.entry_index] += (
            f" Start here for {scenario.query}. Follow the correction reference before changing "
            "production configuration."
        )
        navigation_ranks[scenario.entry_index] = ordinal + 1

        candidate_remainder = list(candidates[2:])
        entry_indices = [scenario.entry_index]
        if ordinal % 2 == 1 and candidate_remainder:
            second_entry_index = candidate_remainder.pop(0)
            entry_indices.append(second_entry_index)
            titles[second_entry_index] = f"{scenario.query.title()} incident index"
            bodies[second_entry_index] += (
                f" Use this {scenario.query} index as an alternate route to the accepted "
                "correction."
            )
            references[second_entry_index].append(primary_id)
            navigation_ranks[second_entry_index] = 50 + ordinal

        distractors: list[str] = []
        for position, index in enumerate(candidate_remainder):
            memory_id = f"mem-{index + 1:04d}"
            distractors.append(memory_id)
            if position % 3 == 0:
                titles[index] += f" — {scenario.query} rollout note"
            elif position % 3 == 1:
                aliases[index].append(scenario.query)
            bodies[index] += (
                f" Historical {scenario.query} discussion used {scenario.forbidden}; "
                "this note is contextual evidence, not the accepted production rule."
            )

        # The graph intentionally contains both helpful authorities and misleading hubs.
        # These edges never change lexical candidate membership.
        entry_targets = [primary_id]
        if ordinal % 4 == 2:
            entry_targets = [*distractors[:5], primary_id]
            bodies[scenario.entry_index] += (
                " Several neighboring investigations are linked; inspect the correction after "
                "ruling out the historical branches."
            )
        references[scenario.entry_index].extend(entry_targets)

        if distractors:
            structural_distractor = distractors[0]
            structural_index = int(structural_distractor.split("-")[1]) - 1
            references[structural_index].extend(distractors[1:9])
            for offset in range(12):
                supporter = tier_start + ((ordinal * 41 + offset * 17 + 7) % tier_width)
                if supporter not in entry_indices and supporter != scenario.primary_index:
                    references[supporter].append(structural_distractor)

        if ordinal % 4 == 1:
            for offset in range(18):
                supporter = tier_start + ((ordinal * 29 + offset * 13 + 11) % tier_width)
                if supporter not in entry_indices and supporter != scenario.primary_index:
                    references[supporter].append(entry_id)

        if distractors:
            archived_index = int(distractors[0].split("-")[1]) - 1
            lifecycle[archived_index] = "archived"

        tasks.append(
            OrderingTask(
                task_id=f"ordering-{scenario.corpus_size}-{scenario.slug}",
                corpus_size=scenario.corpus_size,
                query=scenario.query,
                instruction=(
                    f"{scenario.instruction} Search project Memory using the supplied tool, recall "
                    "what you judge useful, and return a concise decision with the exact "
                    "configuration token."
                ),
                primary_relevant=primary_id,
                acceptable_entry_points=tuple(f"mem-{index + 1:04d}" for index in entry_indices),
                distractors=tuple(distractors),
                expected_facts=(scenario.expected,),
                forbidden_facts=(scenario.forbidden,),
            )
        )

    memories = tuple(
        MemoryFixture(
            id=f"mem-{index + 1:04d}",
            key=f"mem-{index + 1:04d}",
            title=titles[index],
            aliases=tuple(dict.fromkeys(aliases[index])),
            lifecycle=lifecycle[index],
            navigation_rank=navigation_ranks[index],
            references=tuple(dict.fromkeys(references[index])),
            provenance=provenance[index],
            body=bodies[index],
        )
        for index in range(500)
    )
    return FrozenCorpus(seed=seed, memories=memories, tasks=tuple(tasks))
