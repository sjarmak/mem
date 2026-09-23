"""§11 enterprise-workflow materialiser (Tier 0, pure Python).

Turns a NeMo-generated ``EnterpriseWorld`` + ``Project`` (the surface/cast) into N
memory-dependent ``BenchmarkSequence``s. Mirrors ``synthetic_task`` but draws its
cast (personas, channels) from the world and authors a richer fact graph:

* establishing steps write authored "decision facts" attributed to world personas;
* one subject per task — its position varied by seed — is SUPERSEDED through a chain
  of ``SUPERSESSION_DEPTH`` versions under distinct ids; each superseding step marks
  its predecessor (the Staleness signal) and the goal depends on the FINAL version
  only;
* the goal step carries ``distractor_memories`` — plausible-but-wrong values for
  every subject it grades (the Confusion signal), as many as it takes to fill that
  subject's ``draw_group_size`` draw on a published shape and exactly one on a
  tool-requiring one;
* the goal question NAMES every subject its ``OutcomeCheck`` grades (mem-r6yzk B5):
  a graded-but-unnamed decision scores an answer to a question that was never asked;
* the goal's ``OutcomeCheck`` requires every current id AND forbids stating any
  superseded value (``forbidden_values``), so staleness is reward-bearing: an arm
  that surfaces a stale version fails the goal instead of only ticking the
  ``stale_memory_retrieval_rate`` diagnostic (mem-z3gi);
* no agent-visible string separates the classes (mem-z3gi): memory ids are opaque
  content-keyed hashes (``opaque_memory_id``), and truth / stale / distractor all
  render through the SAME ``_fact`` template — only the value (and the drawn
  attribution) differs. Labels live in harness-side fields only (step ids, probe
  descriptions), which the runner never shows the agent.

A PROJECT (``materialize_project``) adds SHARED DECISIONS: facts established once at
world scope and required by the goals of later tasks, which is what makes a
project-tier record cross-session. Which subjects are shared, how many a given task
requires and which task wrote them are all drawn per world from the seed
(``draw_shared_decisions``, ``required_shared_per_task``). The first release shipped
exactly one shared decision, always the project charter, always the same subject and
always the same value within a world: every project record was answerable by one
fixed fetch, and an arm that learned "always fetch the charter" cleared the tier
without retrieving anything (mem-r6yzk R1). Variation is now a property of the
construction, and ``tests/test_public_corpus_contract.py`` measures it over the
released corpus rather than trusting this paragraph.

``facts_per_task`` is the goal's whole graded budget, shared decisions INCLUDED, so
every goal on either tier grades exactly that many subjects and the published gold
set is a constant width. The shared decisions used to be added on top of it, which
made the gold set ``facts_per_task + <cross-session count>`` and handed a downloader
the cross-session count as arithmetic over two published fields
(``GRADED_SUBJECTS_PER_GOAL`` carries the measurement).

ZFC boundary (the generators policy): NeMo supplied only the cast and prose; every
fact, value, dependency, distractor and supersession here is authored in pure
Python and is seed-reproducible. ``distractor_memories`` / ``superseded_memory_ids``
are the authored ground truth the runner seeds + scores Confusion/Staleness
against (mem-zt1c); the values are deliberately absent from the goal query, so a
naive top-k retriever cannot rank the truth above a distractor — that hardness is
the point.
"""

from __future__ import annotations

import itertools
import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from membench.generators.opaque_ids import opaque_memory_id
from membench.metrics.scorers import states_value
from membench.schemas.sequence import (
    BenchmarkSequence,
    ExpectedAction,
    MemoryProbe,
    OutcomeCheck,
    SequenceStep,
)
from membench.schemas.world import EnterpriseWorld, Persona, Project

GENERATOR_VERSION = "enterprise-workflow.v8"

# The tool the tool-requiring goal variant demands (mem-31vl): success requires
# invoking it with the CURRENT (supersession-aware) value as its argument, so a
# naive arm that surfaces a stale version drives a stale argument and fails.
_APPLY_TOOL = "apply_config"

# Supersession chain length for the one superseded subject (mem-z3gi): versions
# v1..vD under distinct opaque ids, each superseding step marking its predecessor;
# only vD is goal-required. Depth >= 3 exercises multi-hop staleness.
SUPERSESSION_DEPTH = 3

# How many candidates a graded subject publishes, gold and stale versions included.
#
# ONE DRAW PER SUBJECT, uniform over this tuple, taken from the task's own rng and
# never conditioned on the subject's role. The distractor count is then whatever fills
# the group: ``size - authored``, where ``authored`` is ``SUPERSESSION_DEPTH`` for the
# superseded subject and 1 for every other. So the superseded subject publishes 3
# versions and ``size - 3`` wrong values while its neighbours publish 1 gold and
# ``size - 1``, and the two groups are the same size with the same probability.
#
# The count was a CONSTANT 3 until mem-r6yzk R3b, and the constant was the leak: it
# made group size a function of role. The superseded subject published 3 + 3 = 6
# candidates and every other subject 1 + 3 = 4, so grouping the merged pool by subject
# named the chain on 160 of 160 released records with no timestamp, no id and no bucket
# label. Measured histograms of (group size -> count) before the fix: project
# {4: 270, 6: 80}, session {4: 160, 6: 80}; every 6 held the chain and no 4 did.
# ``tests/test_public_corpus_contract.py`` now bounds the size/role dependence
# corpus-wide with a permutation test instead of trusting the construction.
#
# The tuple starts above ``SUPERSESSION_DEPTH`` so a superseded subject always keeps at
# least one distractor, and stops at 6 because a bank of ten values has to fund the
# widest group without reusing one (``MIN_VALUES_PER_SUBJECT``).
PUBLISHED_GROUP_SIZES: tuple[int, ...] = (4, 5, 6)

# The tool-requiring shape keeps the narrow pool, and the reason is its retriever.
# A published record hands the arm its whole candidate pool, so widening the pool only
# flattens the recency prior. A tool-requiring world is run LIVE through a retriever
# bounded at ``lexical_system.DEFAULT_TOP_K`` (10), and its reward depends on the naive
# arm surfacing a STALE value and driving a stale argument. Three wrong values per
# subject push the pool to 14, past that window, so the trap can fall outside the
# retrieved set and the naive arm succeeds by not seeing it. Measured:
# ``shape_wellformedness_gate`` reads delta 0.000 on the synthetic-records world at
# width 3 and 1.000 at width 1. A corpus whose trap the retriever cannot reach is
# measuring its own window.
#
# The group-size leak this shape is exposed to is a different one, and it is bounded by
# the run rather than by the draw: a tool-requiring world is replayed live, its pool is
# whatever the retriever returned, and no static per-subject grouping is published.
TOOL_REQUIRING_DISTRACTORS_PER_SUBJECT = 1

# Every subject bank must fund the WIDEST group from distinct values, or a candidate
# reuses a chain value and grades as the gold (or as the stale value the goal forbids).
# The widest group is ``max(PUBLISHED_GROUP_SIZES)`` candidates however the split
# between versions and wrong values falls. Enforced at import by
# ``_assert_value_banks_are_gradeable``, so either shape is fundable.
MIN_VALUES_PER_SUBJECT = max(PUBLISHED_GROUP_SIZES)


def draw_group_size(rng: random.Random, *, authored: int, tool_requiring: bool) -> int:
    """How many candidates this subject publishes, gold and stale versions included.

    On the published shape the size is drawn from ``rng``, uniform over
    ``PUBLISHED_GROUP_SIZES`` and blind to ``authored`` - which is the whole point,
    because ``authored`` IS the role: ``SUPERSESSION_DEPTH`` for the superseded subject
    and 1 for every other. The tool-requiring shape keeps its narrow role-dependent
    count and consumes no rng, so this change does not move a tool-requiring draw.
    """
    if tool_requiring:
        return authored + TOOL_REQUIRING_DISTRACTORS_PER_SUBJECT
    size = rng.choice(PUBLISHED_GROUP_SIZES)
    if size <= authored:
        raise ValueError(
            f"a group of {size} cannot hold {authored} authored values and still carry a "
            f"distractor; every PUBLISHED_GROUP_SIZES entry must exceed SUPERSESSION_DEPTH "
            f"({SUPERSESSION_DEPTH})"
        )
    return size


# How many decisions a project shares across its tasks. Drawn per world, so neither the
# NUMBER of shared decisions nor (with ``required_shared_per_task``) the number a given
# record depends on is a corpus constant.
SHARED_DECISION_COUNTS: tuple[int, ...] = (2, 3)

# The floor on how many of a goal's graded subjects its OWN sequence establishes.
#
# Two, not one. Exactly one subject per task carries the supersession chain, so a goal
# with a single local subject is a goal whose only local subject is the stale one, and
# "which subject was superseded" stops being a thing an arm has to work out. At two the
# chain is one of at least two candidates for the role.
MIN_LOCAL_FACTS = 2

# The BUDGET a goal grades: the number of subjects it names, cross-session decisions
# INCLUDED, and a corpus constant on both tiers.
#
# A project goal's shared decisions come OUT of this budget rather than on top of it,
# which is the whole point of fixing it (mem-r6yzk R5). They used to be added on top:
# a goal graded ``facts_per_task`` local subjects and then however many shared decisions
# it required, so the published gold set was 3 + cross_session_gold on all 160 records
# of the release, both tiers, with no exceptions. Measured on that tree: project
# (gold 4, cross 1) x57, (5, 2) x18, (6, 3) x5; session (3, 0) x80. The count the
# project tier exists to claim was therefore a published function of another published
# field, ``len(evidence.gold_ids) - 3``, and two shipped paragraphs said it was not.
#
# Holding the budget fixed removes the channel rather than narrowing it: a constant
# carries no information about anything, so no arithmetic over the published gold set
# can recover the split, at any cross-session count, without measuring a residual. It
# also makes the tier comparison a controlled one - session and project records grade
# the same number of subjects out of pools of the same size, so a score gap between the
# tiers is the cross-session dependency and not the retrieval load.
#
# Five, because the split has to leave ``MIN_LOCAL_FACTS`` local subjects at the widest
# cross-session draw (``max(SHARED_DECISION_COUNTS)``) and still sit well inside the
# local pool a project leaves (``len(_SUBJECTS) - max(SHARED_DECISION_COUNTS)``, 7).
# A local draw that took the WHOLE pool would publish the world's shared set by
# complement: both of a world's published records would name the same local subjects,
# and whatever else each named would be its cross-session evidence.
GRADED_SUBJECTS_PER_GOAL = 5

# The ONE establishing request template. Every establishing step — including each
# link of the supersession chain, and every shared decision — uses it verbatim, so the
# request wording cannot mark the superseded subject or the cross-session one to the
# agent ("initial"/"corrected"/"charter" would).
_RECORD_REQUEST = "Record the current value of {prompt}."


@dataclass(frozen=True)
class _Subject:
    """A decision a task must recall. ``values`` are the authored ground-truth
    candidates; the materialiser picks the current one (and distinct stale/wrong
    ones) deterministically per task."""

    key: str
    prompt: str
    values: tuple[str, ...]


# Authored decision subjects — domain-agnostic operational facts an enterprise task
# would need to recall. Each bank holds ten distinct values.
#
# It held four until mem-r6yzk R3. With four, the best per-subject constant guess —
# chosen with the released answers in hand — scored 0.513 on "the approved rollback
# command" across the project tier, against a 0.231 mean over the rest: one subject was
# answerable without memory. Ten values put the expected modal share near 0.10 and the
# finite-sample maximum near 0.25, which ``tests/test_public_corpus_contract.py`` bounds
# at ``MAX_CONSTANT_GUESS_SHARE`` over the released tree.
#
# Two authoring rules hold across every bank below, both enforced at import by
# ``_assert_value_banks_are_gradeable``: at least ``MIN_VALUES_PER_SUBJECT`` distinct
# values, and no authored value may ``states_value`` another.
_SUBJECTS: tuple[_Subject, ...] = (
    _Subject(
        "deploy-timeout",
        "the production deploy timeout",
        ("15s", "30s", "45s", "60s", "90s", "120s", "180s", "300s", "600s", "900s"),
    ),
    _Subject(
        "primary-region",
        "the primary deployment region",
        (
            "us-east-1",
            "us-west-2",
            "eu-west-1",
            "ap-south-1",
            "eu-central-1",
            "sa-east-1",
            "ca-central-1",
            "ap-northeast-1",
            "af-south-1",
            "me-south-1",
        ),
    ),
    _Subject(
        "rollback-command",
        "the approved rollback command",
        (
            "kubectl rollout undo",
            "helm rollback",
            "terraform apply -refresh",
            "git revert then redeploy",
            "argocd app rollback",
            "flux suspend then resume",
            "docker service update --rollback",
            "ansible-playbook restore.yml",
            "nomad job revert",
            "spinnaker pipeline rewind",
        ),
    ),
    _Subject(
        "feature-flag",
        "the checkout_v2 feature flag state",
        (
            "enabled",
            "disabled",
            "canary-10%",
            "canary-50%",
            "canary-25%",
            "canary-75%",
            "ramped to all traffic",
            "held at zero traffic",
            "shadow-only",
            "kill-switched",
        ),
    ),
    _Subject(
        "api-version",
        "the supported API version",
        ("v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10"),
    ),
    _Subject(
        "retention-window",
        "the data retention window",
        (
            "30 days",
            "90 days",
            "1 year",
            "7 years",
            "14 days",
            "180 days",
            "2 years",
            "5 years",
            "10 years",
            "kept indefinitely",
        ),
    ),
    # The charter. It was a SEPARATE bank until mem-r6yzk R3b, reachable only through
    # ``_SHARED_CANDIDATES``, and being reachable only there was the leak: a shared
    # decision is established by an earlier task and required by a later one, so every
    # charter fact a project record graded was cross-session by construction. Measured
    # on the released tree: P(cross-session | subject = "the project charter decision")
    # = 1.000 on 13 of 13 observations. An arm that answered "whatever the charter says
    # came from another session" was right every time, without retrieving anything.
    #
    # It is an ordinary subject now, drawn for local facts and for shared decisions
    # alike, so a charter fact is cross-session about as often as any other.
    _Subject(
        "charter",
        "the project charter decision",
        (
            "freeze scope at the phase-3 milestone",
            "cut the integrations track and ship the core",
            "extend the beta by one quarter",
            "move the launch behind the compliance review",
            "split delivery into two independent releases",
            "adopt the vendor SDK instead of the in-house client",
            "hold the public interface steady through the migration",
            "retire the legacy pipeline before any new feature",
            "run the rewrite and the old stack side by side",
            "staff the reliability track ahead of the roadmap",
        ),
    ),
    _Subject(
        "oncall-rotation",
        "the on-call rotation cadence",
        (
            "daily handoff",
            "weekly on Mondays",
            "weekly on Thursdays",
            "fortnightly",
            "two-week shifts",
            "three-day shifts",
            "follow-the-sun",
            "monthly blocks",
            "split by timezone",
            "paired primary and secondary",
        ),
    ),
    _Subject(
        "paging-floor",
        "the paging severity floor",
        (
            "sev-1 only",
            "sev-2 and above",
            "sev-3 and above",
            "sev-4 and above",
            "any customer-visible regression",
            "any failed deploy",
            "any error-budget burn alert",
            "manual escalation only",
            "anything breaching the latency objective",
            "nothing below a full outage",
        ),
    ),
    _Subject(
        "artifact-store",
        "the release artifact store",
        (
            "the internal registry",
            "the vendor registry",
            "one bucket per release",
            "the shared package mirror",
            "a signed OCI repository",
            "the build cache",
            "the legacy file server",
            "a per-team namespace",
            "the compliance archive",
            "the mirrored CDN origin",
        ),
    ),
)

# What a project may share across its tasks: every subject, with nothing held back and
# nothing reserved. The alias is kept because "the shareable set" and "the subject set"
# are different claims, and the fix is precisely that they are now the same set.
_SHARED_CANDIDATES: tuple[_Subject, ...] = _SUBJECTS


def _assert_value_banks_are_gradeable() -> None:
    """Enforce at import the two authoring rules the banks above only assert in prose.

    ``states_value`` is a word-boundary literal match, so a value that states another
    value cannot be graded apart from it: a distractor holding the stated value scores
    as the gold fact, and the task keeps passing while it has stopped measuring recall.
    The rule spans every bank together because a shared-decision fact and a local
    subject fact are graded by the same check against the same answer text.

    Raised at import rather than at generation time: a bank edit is an authoring error,
    and a corpus minted from a broken bank is worse than one that was never generated.
    """
    shallow = [s.key for s in _SHARED_CANDIDATES if len(set(s.values)) < MIN_VALUES_PER_SUBJECT]
    if shallow:
        raise ValueError(
            f"every subject needs >= {MIN_VALUES_PER_SUBJECT} distinct values "
            f"(the widest group in PUBLISHED_GROUP_SIZES, whatever the split between "
            f"{SUPERSESSION_DEPTH} chain versions and its distractors); too few on {shallow}"
        )

    authored = [(s.key, value) for s in _SHARED_CANDIDATES for value in s.values]
    collisions = [
        f"{owner}:{value!r} states {other_owner}:{other!r}"
        for i, (owner, value) in enumerate(authored)
        for j, (other_owner, other) in enumerate(authored)
        if i != j and states_value(value, other)
    ]
    if collisions:
        raise ValueError(
            "authored values must be gradeable apart: no value may state another under "
            f"the word-boundary states_value contract; found {collisions}"
        )


_assert_value_banks_are_gradeable()


def _attribution(persona: Persona, channel_name: str | None) -> str:
    where = f" in #{channel_name}" if channel_name else ""
    role = f" ({persona.role})" if persona.role else ""
    return f"{persona.name}{role}{where}"


def _shared_cast(world: EnterpriseWorld, label: str) -> tuple[Persona, str | None]:
    """The persona + channel a world-scoped fact is attributed to.

    Deterministic from ``(world_id, label)``, and drawn from the SAME distribution for
    a shared decision and each of its distractors. The charter used to be attributed to
    ``personas[0]`` with no channel, which made it the only fact in a world rendering
    without an ``in #channel`` clause — an agent-visible string that separates the
    classes, exactly what ``_fact`` exists to prevent (mem-z3gi)."""
    rng = random.Random(f"{world.world_id}/{label}")
    persona = world.personas[rng.randrange(len(world.personas))]
    channel = world.channels[rng.randrange(len(world.channels))].name if world.channels else None
    return persona, channel


def _fact(prompt: str, value: str, persona: Persona, channel: str | None) -> str:
    """THE memory content template — shared verbatim by truth, stale and distractor
    entries so no verb tense or attribution shape separates the classes; only the
    value (and the drawn attribution) differs (mem-z3gi)."""
    return f"{prompt} is {value} — by {_attribution(persona, channel)}"


# The inverse of ``_fact``, anchored on the template's own two fixed separators. Non-greedy on
# the prompt so a value may itself contain " is " ("the rollback command is deploy rollback is
# not ..." would still split at the FIRST " is ", which is the prompt/value boundary because no
# authored prompt contains one); non-greedy on the value so the attribution is everything after
# the FIRST " — by ", which no authored value contains. This is a format-anchored parse of a
# string this module minted, not a heuristic over prose.
_FACT_RE = re.compile(r"^(?P<prompt>.+?) is (?P<value>.+?) — by (?P<attribution>.+)$", re.DOTALL)


def fact_value(content: str) -> str:
    """The VALUE a ``_fact``-shaped memory content states (mem-zfm0m).

    The twin corpus inlines the value of every fact a task requires, and the realistic (unscored)
    facts reach it only as ``_fact`` prose. Raises ``ValueError`` on content this template did not
    produce rather than guessing at it."""
    match = _FACT_RE.fullmatch(content)
    if match is None:
        raise ValueError(f"not a fact-shaped memory content: {content!r}")
    return match.group("value")


def fact_subject(content: str) -> str:
    """The SUBJECT a ``_fact``-shaped memory content states a value for — the same
    format-anchored parse as ``fact_value``, off the template's other named group.

    The unnecessary twin needs it to say WHICH subject each value it inlines belongs to. Without
    it the twin can only emit a bare list of opaque tokens, which does not read as a set of
    values at all."""
    match = _FACT_RE.fullmatch(content)
    if match is None:
        raise ValueError(f"not a fact-shaped memory content: {content!r}")
    return match.group("prompt")


def _assert_no_forbidden_value_leak(
    forbidden_values: list[str], surfaced_contents: list[str]
) -> None:
    """Authoring guard: a stale (forbidden) value must never appear in a REQUIRED or
    distractor content, or the goal would fail even when no stale memory was
    surfaced. Holds by construction (values are distinct within a subject, disjoint
    across subjects); raises loudly on subject-bank drift."""
    for value in forbidden_values:
        for content in surfaced_contents:
            if states_value(content, value):
                raise ValueError(
                    f"authored stale value {value!r} appears in non-stale content "
                    f"{content!r}; fix the subject bank"
                )


@dataclass(frozen=True)
class SharedDecision:
    """A decision established ONCE at world scope and required by later tasks' goals.

    The cross-session unit of the project tier: ``establish_at`` names the task whose
    steps write it, and any task after that one may require it. Minted per world, so
    every task that carries it carries the same id and the same content — the
    same-id/same-content rule ``runner.project._project_oracle_pool`` enforces.
    """

    subject: _Subject
    memory_id: str
    content: str
    distractors: Mapping[str, str]
    establish_at: int


def draw_shared_decisions(
    world: EnterpriseWorld, rng: random.Random, *, n_tasks: int, tool_requiring: bool = False
) -> tuple[SharedDecision, ...]:
    """Draw this world's shared decisions from ``rng`` (seeded by the caller).

    Four things vary per world, and each one closes a fixed-lookup attack that the
    single-charter construction left open (mem-r6yzk R1):

    * HOW MANY are shared — ``SHARED_DECISION_COUNTS``;
    * WHICH SUBJECTS — sampled from ``_SHARED_CANDIDATES``, which is every subject
      there is, so no one subject is the cross-session one corpus-wide;
    * WHAT VALUE each holds — drawn from the subject's bank, so a constant guess at a
      shared decision is worth about one over the bank size;
    * WHICH TASK writes it — ``establish_at``, inside the first ``n_shared`` tasks so
      that every later task can require any of them.

    The first decision drawn is always established by task 0, which is what guarantees
    every task after it has something cross-session to require.
    """
    if n_tasks < 2:
        raise ValueError(
            f"a project needs at least 2 tasks to have a cross-session decision, got {n_tasks}"
        )
    n_shared = rng.choice(SHARED_DECISION_COUNTS)
    subjects = rng.sample(_SHARED_CANDIDATES, n_shared)
    # Every shared decision is written inside the first ``n_shared`` tasks, so the
    # window closes early and every later task sees the whole shared set. Without the
    # bound a 40-task project could write its second shared decision at task 38 and
    # spend 37 records on one fixed lookup.
    establish_window = min(n_shared, n_tasks - 1)
    decisions: list[SharedDecision] = []
    for position, subject in enumerate(subjects):
        establish_at = 0 if position == 0 else rng.randrange(establish_window)
        value = rng.choice(subject.values)
        # A shared decision is never superseded, so it authors one value and the rest of
        # its group is wrong values. The size is drawn the same way a local subject's is.
        group_size = draw_group_size(rng, authored=1, tool_requiring=tool_requiring)
        wrong_values = rng.sample([v for v in subject.values if v != value], group_size - 1)
        label = f"shared-{subject.key}"
        persona, channel = _shared_cast(world, label)
        distractors: dict[str, str] = {}
        for index, wrong in enumerate(wrong_values):
            wrong_label = f"{label}-distractor{index}"
            wrong_persona, wrong_channel = _shared_cast(world, wrong_label)
            distractors[opaque_memory_id(world.world_id, wrong_label)] = _fact(
                subject.prompt, wrong, wrong_persona, wrong_channel
            )
        decisions.append(
            SharedDecision(
                subject=subject,
                memory_id=opaque_memory_id(world.world_id, label),
                content=_fact(subject.prompt, value, persona, channel),
                distractors=distractors,
                establish_at=establish_at,
            )
        )
    return tuple(decisions)


def required_shared_per_task(
    decisions: Sequence[SharedDecision], rng: random.Random, *, n_tasks: int
) -> tuple[tuple[SharedDecision, ...], ...]:
    """Which shared decisions each task's goal requires, one entry per task.

    Task 0 requires none: nothing has been established before it, so its record is not
    cross-session and the exporter declines to publish it under the project tier. Every
    later task draws a NON-EMPTY subset of the decisions established before it, from a
    canonical enumeration of subsets, without replacement inside a pass. So two records
    of the same world differ in WHICH shared decisions answer them, in HOW MANY, or in
    both — the property R1 asks for, and the property one fixed fetch cannot satisfy.

    The budget is ``2 ** n_shared - 1`` subsets. A project with more tasks than that
    exhausts it and starts a fresh pass, which is why the guarantee is stated per pass
    rather than globally; the released corpus runs three tasks against a budget of at
    least three, so on it the sets are distinct outright. The one case with no choice at
    all — a task that can see a single established decision, which only happens while
    the establishment window is still open — repeats rather than failing, because a
    project of two tasks has exactly one thing to ask about.
    """
    if not decisions:
        raise ValueError("a project needs at least one shared decision")
    used: set[tuple[int, ...]] = set()
    previous: tuple[int, ...] | None = None
    per_task: list[tuple[SharedDecision, ...]] = []
    for task_index in range(n_tasks):
        available = tuple(
            index for index, decision in enumerate(decisions) if decision.establish_at < task_index
        )
        if not available:
            per_task.append(())
            continue
        options = [
            combination
            for size in range(1, len(available) + 1)
            for combination in itertools.combinations(available, size)
        ]
        fresh = [c for c in options if c not in used]
        if not fresh:
            used.clear()
            fresh = [c for c in options if c != previous] or options
        chosen = fresh[rng.randrange(len(fresh))]
        used.add(chosen)
        previous = chosen
        per_task.append(tuple(decisions[index] for index in chosen))
    return tuple(per_task)


def _materialize_task(
    world: EnterpriseWorld,
    project: Project,
    *,
    task_index: int,
    facts_per_task: int,
    seed: int,
    subject_pool: Sequence[_Subject] = _SUBJECTS,
    shared_required: Sequence[SharedDecision] = (),
    shared_established: Sequence[SharedDecision] = (),
    tool_requiring: bool = False,
) -> BenchmarkSequence:
    rng = random.Random((seed << 16) ^ (task_index * 2654435761))
    seq_id = f"{world.world_id}-task{task_index}"
    # The goal grades ``facts_per_task`` subjects in total and the shared decisions it
    # requires are part of that total, so what varies with the cross-session draw is
    # WHICH of the graded subjects this sequence established, never HOW MANY it grades.
    # Adding the shared decisions on top instead is what made the cross-session count
    # readable off the gold set size (see ``GRADED_SUBJECTS_PER_GOAL``).
    n_local = facts_per_task - len(shared_required)
    if n_local < MIN_LOCAL_FACTS:
        raise ValueError(
            f"sequence {seq_id!r} grades {facts_per_task} subjects and requires "
            f"{len(shared_required)} shared decision(s), leaving {n_local} for its own "
            f"sequence; a goal needs at least {MIN_LOCAL_FACTS} local subjects, so raise "
            "facts_per_task or share fewer decisions"
        )
    if n_local > len(subject_pool) - 1:
        raise ValueError(
            f"sequence {seq_id!r} would draw {n_local} local subjects from a pool of "
            f"{len(subject_pool)}; the draw must leave at least one subject untaken, or "
            "the world's shared set is its complement and every record publishes which "
            "of its graded subjects are cross-session"
        )
    subjects = rng.sample(subject_pool, n_local)
    # The superseded subject's position is seed-varied (mem-z3gi) — a fixed i==0
    # would let position stand in for the staleness label.
    chain_position = rng.randrange(n_local)

    shared_keys = {d.subject.key for d in (*shared_required, *shared_established)}
    overlap = sorted(shared_keys & {s.key for s in subjects})
    if overlap:
        raise ValueError(
            f"sequence {seq_id!r} draws {overlap} as a local subject while the world shares "
            "it: one prompt would carry two gold values and the goal could not grade either"
        )

    def draw_persona() -> Persona:
        return world.personas[rng.randrange(len(world.personas))]

    def draw_channel() -> str | None:
        if not world.channels:
            return None
        return world.channels[rng.randrange(len(world.channels))].name

    steps: list[SequenceStep] = []
    required_ids: list[str] = []
    required_contents: list[str] = []
    # The subjects the goal QUESTION names. Grown in lockstep with ``required_ids`` so
    # the question can never ask for less than the outcome check grades (mem-r6yzk B5:
    # a shared decision was graded but unnamed, so a record was scored on a decision
    # nobody had been asked about).
    graded_prompts: list[str] = []
    probes: list[MemoryProbe] = []
    distractors: dict[str, str] = {}
    superseded: list[str] = []
    forbidden_values: list[str] = []
    # The current value of the ONE superseded subject — the value a tool-requiring
    # goal must apply (mem-31vl). None until the chain branch below sets it
    # (chain_position is always visited exactly once); a broken invariant then fails
    # loudly at the assert rather than authoring arg_values=[""] (which would match
    # any text, since states_value(text, "") is true).
    chain_current_value: str | None = None

    for i, subject in enumerate(subjects):
        if i == chain_position:
            # The supersession chain: v1 → … → vD, distinct ids, each superseding
            # step marking its predecessor; the goal depends on vD only and must
            # not state any earlier value.
            chain_values = rng.sample(list(subject.values), SUPERSESSION_DEPTH)
            version_ids = [
                opaque_memory_id(seq_id, f"{subject.key}-v{v}")
                for v in range(1, SUPERSESSION_DEPTH + 1)
            ]
            for v, (version_id, value) in enumerate(zip(version_ids, chain_values, strict=True)):
                steps.append(
                    SequenceStep(
                        step_id=f"{seq_id}-s{i}v{v + 1}-{subject.key}",
                        user_request=_RECORD_REQUEST.format(prompt=subject.prompt),
                        expected_memory_writes={
                            version_id: _fact(subject.prompt, value, draw_persona(), draw_channel())
                        },
                        superseded_memory_ids=[version_ids[v - 1]] if v else [],
                    )
                )
            current_id = version_ids[-1]
            current_value = chain_values[-1]
            chain_current_value = current_value
            superseded.extend(version_ids[:-1])
            forbidden_values.extend(chain_values[:-1])
            taken_values = list(chain_values)
        else:
            current_value = rng.choice(subject.values)
            current_id = opaque_memory_id(seq_id, subject.key)
            steps.append(
                SequenceStep(
                    step_id=f"{seq_id}-s{i}-{subject.key}",
                    user_request=_RECORD_REQUEST.format(prompt=subject.prompt),
                    expected_memory_writes={
                        current_id: _fact(
                            subject.prompt, current_value, draw_persona(), draw_channel()
                        )
                    },
                )
            )
            taken_values = [current_value]

        required_ids.append(current_id)
        required_contents.append(steps[-1].expected_memory_writes[current_id])
        graded_prompts.append(subject.prompt)
        probes.append(
            MemoryProbe(
                probe_id=f"{seq_id}-probe-{subject.key}",
                expected_memory_id=current_id,
                description=f"{subject.prompt} must be recalled at the goal",
            )
        )
        # Plausible-but-wrong values for the same subject, same template, each
        # attributed to a freshly drawn persona — the Confusion stressor the goal must
        # not be fooled by. Every value is distinct from the current one AND from every
        # stale one, so surfacing a distractor is never mis-scored as staleness.
        group_size = draw_group_size(rng, authored=len(taken_values), tool_requiring=tool_requiring)
        wrong_values = rng.sample(
            [v for v in subject.values if v not in taken_values],
            group_size - len(taken_values),
        )
        for index, wrong_value in enumerate(wrong_values):
            distractors[opaque_memory_id(seq_id, f"{subject.key}-distractor{index}")] = _fact(
                subject.prompt, wrong_value, draw_persona(), draw_channel()
            )

    # Cross-task continuity: decisions an EARLIER task of this project established and
    # this task's goal also requires. Under run_project (shared store) the earlier write
    # is visible; under isolated run_sequence it is not — that gap is the continuity
    # signal. ``shared_established`` is this task's own share of the writing.
    for offset, decision in enumerate(shared_established):
        steps.insert(
            offset,
            SequenceStep(
                step_id=f"{seq_id}-shared{offset}-{decision.subject.key}",
                user_request=_RECORD_REQUEST.format(prompt=decision.subject.prompt),
                expected_memory_writes={decision.memory_id: decision.content},
            ),
        )
    for decision in shared_required:
        if not decision.distractors:
            raise ValueError(
                f"shared decision {decision.subject.key!r} carries no distractor: naming its "
                "prompt in the question with only one candidate holding those tokens hands a "
                "lexical arm the gold value for free"
            )
        required_ids.append(decision.memory_id)
        graded_prompts.append(decision.subject.prompt)
        required_contents.append(decision.content)
        distractors.update(decision.distractors)
        probes.append(
            MemoryProbe(
                probe_id=f"{seq_id}-probe-shared-{decision.subject.key}",
                expected_memory_id=decision.memory_id,
                description=(
                    f"{decision.subject.prompt} (decided in an earlier task) must be recalled"
                ),
            )
        )

    # The graded subjects are accumulated local-first and shared-last, which puts the
    # cross-session ones in a fixed tail position. ``question.text`` names them in this
    # order and the question is handed to the arm, so an unshuffled list encodes which
    # of the graded subjects the sequence did not establish, in a field the harness seam
    # cannot withhold. One permutation over the parallel lists, from the task's own rng,
    # so position says nothing and the check still grades what the question asks.
    graded_order = list(range(len(required_ids)))
    rng.shuffle(graded_order)
    required_ids = [required_ids[i] for i in graded_order]
    required_contents = [required_contents[i] for i in graded_order]
    graded_prompts = [graded_prompts[i] for i in graded_order]
    probes = [probes[i] for i in graded_order]

    _assert_no_forbidden_value_leak(
        forbidden_values, required_contents + list(distractors.values())
    )

    prompts = ", ".join(graded_prompts)
    if tool_requiring:
        assert chain_current_value is not None, "chain_position was never visited"
        # Tool-requiring variant (mem-31vl): success requires APPLYING the current
        # value via a tool call, not merely stating it. Staleness moves off the text
        # answer (forbidden_values cleared) onto the tool argument, so the tool action
        # is the sole reward-bearing channel — a stale value fails in the argument, not
        # the prose. That makes memory quality load-bearing THROUGH the action.
        goal_request = (
            f"{project.goal} Using the tool `{_APPLY_TOOL}`, apply the current value "
            f"of: {prompts}."
        )
        goal_check = OutcomeCheck(
            check_id=f"{seq_id}-goal-check",
            description=(
                f"goal requires calling `{_APPLY_TOOL}` with the current value of the "
                "superseded subject and never a stale one"
            ),
            requires_memory=required_ids,
            requires_action=[
                ExpectedAction(
                    tool=_APPLY_TOOL,
                    arg_values=[chain_current_value],
                    forbidden_values=list(forbidden_values),
                )
            ],
        )
        available_tools = [_APPLY_TOOL]
    else:
        goal_request = f"{project.goal} State the current value of: {prompts}."
        goal_check = OutcomeCheck(
            check_id=f"{seq_id}-goal-check",
            description=(
                "goal requires the current value of each established subject "
                "and must not state a superseded value"
            ),
            requires_memory=required_ids,
            forbidden_values=list(forbidden_values),
        )
        available_tools = []
    steps.append(
        SequenceStep(
            step_id=f"{seq_id}-goal",
            user_request=goal_request,
            available_tools=available_tools,
            expected_memory_reads=required_ids,
            outcome_checks=[goal_check],
            memory_probes=probes,
            distractor_memories=distractors,
            superseded_memory_ids=superseded,
        )
    )

    return BenchmarkSequence(
        sequence_id=seq_id,
        title=f"{project.name}: reconcile {len(required_ids)} decisions",
        domain=world.domain,
        goal=project.goal,
        steps=steps,
    )


def _validate(
    world: EnterpriseWorld, project: Project, *, facts_per_task: int, n_tasks: int
) -> None:
    if not world.personas:
        raise ValueError(f"world {world.world_id!r} has no personas to attribute facts to")
    if not MIN_LOCAL_FACTS <= facts_per_task <= len(_SUBJECTS):
        raise ValueError(
            f"facts_per_task must be in {MIN_LOCAL_FACTS}..{len(_SUBJECTS)}, got "
            f"{facts_per_task}; it is the number of subjects the goal grades, shared "
            "decisions included"
        )
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
    facts_per_task: int = GRADED_SUBJECTS_PER_GOAL,
    seed: int | None = None,
    tool_requiring: bool = False,
) -> list[BenchmarkSequence]:
    """Materialise ``n_tasks`` INDEPENDENT memory-dependent sequences from a world.

    Deterministic: the same (world, project, args, seed) yields byte-identical
    sequences. ``seed`` defaults to the world's seed. Each sequence is memory-
    dependent by construction and clears ``memory_necessity_gate``. ``tool_requiring``
    (mem-31vl) makes the goal demand a tool call carrying the current value instead of
    a text answer, so retrieval quality is load-bearing through the action."""
    _validate(world, project, facts_per_task=facts_per_task, n_tasks=n_tasks)
    base_seed = world.seed if seed is None else seed
    return [
        _materialize_task(
            world,
            project,
            task_index=t,
            facts_per_task=facts_per_task,
            seed=base_seed,
            tool_requiring=tool_requiring,
        )
        for t in range(n_tasks)
    ]


def materialize_project(
    world: EnterpriseWorld,
    project: Project,
    *,
    n_tasks: int = 3,
    facts_per_task: int = GRADED_SUBJECTS_PER_GOAL,
    seed: int | None = None,
    drop_charter: bool = False,
    tool_requiring: bool = False,
) -> list[BenchmarkSequence]:
    """Materialise a PROJECT: ``n_tasks`` linked by SHARED DECISIONS established in
    early tasks and required by the goals of later ones — cross-task continuity, meant
    to run under ``runner.project.run_project`` (shared store). Under isolated
    ``run_sequence`` the later tasks fail (they never wrote those decisions); that gap
    is the continuity signal.

    Which subjects are shared, how many, which task establishes each and which of them
    a given task requires are all drawn from ``seed`` (``draw_shared_decisions`` and
    ``required_shared_per_task`` document each draw). A shared subject is removed from
    the local pool every task samples from, so one prompt never carries two gold values.

    ``drop_charter`` omits the establishing steps: a missing-context (Recovery)
    variant where the shared decisions are required but never written, so even the
    oracle pool lacks them. Recovery is a dataset annotation — the ScriptedAgent cannot
    re-derive missing memory, so the metric is for real agents under test."""
    _validate(world, project, facts_per_task=facts_per_task, n_tasks=n_tasks)
    base_seed = world.seed if seed is None else seed
    rng = random.Random(f"{world.world_id}|{base_seed}|shared-decisions")
    decisions = draw_shared_decisions(world, rng, n_tasks=n_tasks, tool_requiring=tool_requiring)
    required = required_shared_per_task(decisions, rng, n_tasks=n_tasks)
    shared_keys = {decision.subject.key for decision in decisions}
    subject_pool = tuple(s for s in _SUBJECTS if s.key not in shared_keys)
    # The widest local draw a task can make is the whole budget minus the narrowest
    # cross-session requirement, which is one: ``required_shared_per_task`` never hands
    # a published task an empty set. That draw has to leave a subject untaken, or the
    # world's shared set is readable as the complement of the local one.
    widest_local_draw = facts_per_task - 1
    if widest_local_draw > len(subject_pool) - 1:
        raise ValueError(
            f"world {world.world_id!r} shares {sorted(shared_keys)}, leaving "
            f"{len(subject_pool)} local subject(s) for a task that may draw "
            f"{widest_local_draw} of them; a project needs facts_per_task <= "
            f"{len(_SUBJECTS) - max(SHARED_DECISION_COUNTS)}"
        )
    return [
        _materialize_task(
            world,
            project,
            task_index=t,
            facts_per_task=facts_per_task,
            seed=base_seed,
            subject_pool=subject_pool,
            shared_required=required[t],
            shared_established=(
                () if drop_charter else tuple(d for d in decisions if d.establish_at == t)
            ),
            tool_requiring=tool_requiring,
        )
        for t in range(n_tasks)
    ]


# The question_type label each tier's goal step carries (mem-r6yzk.3). Harness-side
# only: the runner never renders it. The goal WORDING is no longer byte-identical
# across tiers — a project question names its shared decisions as well, because they
# are graded (mem-r6yzk B5) — so the property that keeps the question honest is the
# multi-candidate one documented on ``materialize_session_tier``, not tier-identical
# phrasing.
SESSION_QUESTION_TYPE = "current-value-recall"
PROJECT_QUESTION_TYPE = "cross-session-decision-recall"


def _retier(
    sequences: list[BenchmarkSequence], *, tier: str, question_type: str
) -> list[BenchmarkSequence]:
    """Stamp the harness-side tier labels onto already-materialised sequences.

    A copy, not a mutation: the underlying materialiser stays the single author of
    every step, so the two tiers cannot drift apart in fact structure."""
    return [s.model_copy(update={"tier": tier, "question_type": question_type}) for s in sequences]


def materialize_session_tier(
    world: EnterpriseWorld,
    project: Project,
    *,
    n_tasks: int = 3,
    facts_per_task: int = GRADED_SUBJECTS_PER_GOAL,
    seed: int | None = None,
    tool_requiring: bool = False,
) -> list[BenchmarkSequence]:
    """SESSION tier: ``n_tasks`` sequences whose goal spans ONE sequence's own scope.

    The question is "what did the last step on this thread leave behind - what is the
    current value now", and every id the goal requires was written by an earlier step
    of the SAME sequence, so an isolated ``run_sequence`` store is sufficient. That is
    exactly what ``materialize_world`` already builds; this wrapper only stamps the
    ``tier`` / ``question_type`` labels.

    Wording invariance (the constraint that makes the tier measurable): the
    establishing steps keep ``_RECORD_REQUEST`` verbatim, and every subject the goal
    question NAMES is carried by at least two further candidates holding different
    values (``PUBLISHED_GROUP_SIZES``). A prompt with a single carrier would put a
    token in the QUESTION that sits in exactly one candidate, and a lexical arm could
    rank the gold without having remembered anything - the benchmark would be
    measuring its own wording.
    Every local subject gets its distractors in ``_materialize_task`` and every shared
    decision gets them in ``draw_shared_decisions``, so the property holds on both
    tiers.

    Tier-identical phrasing is NOT the mechanism and is deliberately retired
    (mem-r6yzk B5): a project question names the shared decisions too, because they are
    graded and an ungraded-but-scored decision is the defect that retirement fixes.
    Tier leakage through wording is harmless - the two tiers ship in separate files and
    no arm ever chooses between them. The tier LABEL still lives in harness-side fields
    only.

    Byte-reproducible from ``seed`` (defaults to the world's seed)."""
    return _retier(
        materialize_world(
            world,
            project,
            n_tasks=n_tasks,
            facts_per_task=facts_per_task,
            seed=seed,
            tool_requiring=tool_requiring,
        ),
        tier="session",
        question_type=SESSION_QUESTION_TYPE,
    )


def materialize_project_tier(
    world: EnterpriseWorld,
    project: Project,
    *,
    n_tasks: int = 3,
    facts_per_task: int = GRADED_SUBJECTS_PER_GOAL,
    seed: int | None = None,
    drop_charter: bool = False,
    tool_requiring: bool = False,
) -> list[BenchmarkSequence]:
    """PROJECT tier: ``n_tasks`` sequences sharing world-scoped decisions.

    The question is "what did we decide about this project across sessions": a goal
    requires the shared decisions an earlier task wrote PLUS the current value of a
    superseded subject (the chain ``_materialize_task`` always authors), so answering
    it means spanning the shared ``run_project`` store and resolving supersession.
    Under an isolated run the later tasks cannot reach the shared decisions - that gap
    is the signal. ``drop_charter`` omits the establishing steps entirely (the Recovery
    variant), so they are required but were never written by anyone.

    Same wording invariance as the session tier: every prompt the question names is
    carried by several candidates with different values, the shared decisions included.
    WHICH decisions are shared, HOW MANY a record requires and WHAT VALUE each holds all
    vary per world, so no single fetch and no constant answer clears the tier
    (mem-r6yzk R1/R3). Byte-reproducible from ``seed``."""
    return _retier(
        materialize_project(
            world,
            project,
            n_tasks=n_tasks,
            facts_per_task=facts_per_task,
            seed=seed,
            drop_charter=drop_charter,
            tool_requiring=tool_requiring,
        ),
        tier="project",
        question_type=PROJECT_QUESTION_TYPE,
    )
