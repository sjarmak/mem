"""`grouped` — bucketed retrieval over a delegate arm (mem-r6yzk.5).

Every other wired arm returns a FLAT top-k: the k best-scoring items, which on a
corpus where one subject dominates can be k near-duplicates of the same fact. This
arm wraps any other `MemorySystem` and re-shapes its ranked result into buckets:
at most `per_group_k` items per group, at most `groups_k` groups. Retrieval width
is therefore spent on coverage rather than on depth in one group.

Two boundaries this deliberately respects:

- **Grouping is ARM-SIDE.** The `SemanticMemoryClient` Protocol is NOT widened with
  a group-by parameter (the §5b no-widening rule in `semantic_base`); doing so would
  force every third-party adapter (mem0 / NAT / A-MEM / Graphiti / nemo-embed) to
  reimplement grouping against its own SDK. Instead the delegate is asked for a
  WIDER flat result and the bucketing happens here, identically for every backend.
- **The group key is mechanical.** It is a deterministic prefix of an item's own
  text, never a semantic/keyword judgement about what the item is "about" (the ZFC
  boundary): no keyword list, no threshold, no classification. Two items group
  together when their texts START THE SAME WAY, which is a string fact. Anything
  smarter belongs in a model call, not in this layer; a caller that has a real
  grouping (session, task, rig) passes it as `group_key` explicitly.

  The key reads the CONTENT and not the id on purpose. It used to split the memory
  id on `-` and take the leading segments, which worked only while ids carried
  authoring structure. Published ids are opaque minted aliases (`k-<16 hex>`) - they
  have to be, or the id would say what the item is - so an id-derived key put every
  item in its own group, `per_group_k` had nothing to cap, and the arm silently
  degraded to the flat top-k it exists to replace.

Widening: filling the buckets needs MORE candidates than the arm will return — the
second group's best item ranks below the first group's tail, so a delegate widened
only to `per_group_k * groups_k` hands back the same one-group ranking that flat
top-k already gave. The arm therefore oversamples (`CANDIDATE_OVERSAMPLE`) and still
returns at most `per_group_k * groups_k`: the candidate scan widens, the injected
volume does not. How far below the returned width a group's second item sits is a
property of the corpus, not of the config, so one multiplier cannot be the answer:
the arm widens the scan and RE-ASKS until the buckets fill or the delegate runs out
of rows, and the oversample is only the first width tried. A fixed multiplier is what
lost an item on `world-seed132-task2` of the release this arm was built against, whose
32-candidate pool put four of the second group's five items below a 24-row scan. That
corpus was re-drawn since, and on the one that ships a 24-row scan happens to fill every
record's buckets (22 is the narrowest that does), which is the point: whether a fixed
multiplier suffices is a fact about the draw, so the arm does not bet on it. Top-k-bounded arms
(`LexicalTopKMemory`, `AbstractSemanticArm` and its subclasses) hold their width in
`_top_k`, which is temporarily raised for the duration of each delegate call and always
restored. Arms with no width to raise (`oracle`, `filesystem`, `none` — they return an
exact set, not a ranking) are called once, unchanged; the emitted event records the
width that ran, so the fetched volume is visible in telemetry rather than implied.

Volume: `effective_k` (`per_group_k * groups_k`) is a CEILING, not the width. The arm
reaches it only when the SUBSTRATE offers `groups_k` groups holding `per_group_k`
items each; below that it returns fewer items than a flat delegate at the same
effective k, and the shortfall is silent. Re-asking removes the scan as a cause of
that shortfall, so a short result now means the pool itself is short, which is a fact
about the corpus a caller can act on. A `groups_k` above the number of groups
the corpus has is therefore not a harmless loose cap — it is dead configuration that
narrows the arm. The public baseline shipped exactly that (`per_group_k=1,
groups_k=6` over pools that offer 5 groups on the released corpus, and 3 to 6 on the one
before it) and injected one item per group where the flat arms injected 6, which made
every grouped-vs-flat delta a volume comparison. A caller
comparing this arm against a flat one must pin the split to the corpus' measured group
counts and check the returned width, not assume the ceiling was reached; the emitted
event reports `groups`, `returned` and `ceiling` per retrieve so the check is
mechanical rather than inferred. What the arm does spend more of unconditionally is
delegate-side scan width (`candidate_k`), which the same event reports.
"""

import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from membench.memory_systems.base import (
    MemorySystem,
    RetrievalRequest,
    RetrieveResult,
    validate_top_k,
)
from membench.memory_systems.lexical_system import LexicalTopKMemory
from membench.runtime import StepContext
from membench.schemas.memory_event import MemoryEvent

DEFAULT_PER_GROUP_K = 3
DEFAULT_GROUPS_K = 5

# FIRST candidate-scan width, as a multiple of the returned width. The buckets past
# the first can only be filled from candidates ranked BELOW the returned width, so the
# delegate is asked for this many times `per_group_k * groups_k` rows before anything
# is bucketed. Raising it costs delegate work, never injected volume — the result is
# still capped at `effective_k`.
CANDIDATE_OVERSAMPLE = 4

# The scan doubles from `candidate_k` whenever the buckets came back short AND the
# delegate filled the width it was given, which is the delegate saying it has more
# rows. It stops on a short delegate result (the substrate is exhausted) or on a full
# set of buckets. This bound only catches a delegate that reports a full result at
# every width, which no finite store does; it is a runaway guard, not a tuning knob,
# and a shortfall it does cause stays visible as `returned` < `ceiling` on the event.
# Twenty doublings take the scan from `effective_k * 4` past twenty-five million rows.
MAX_CANDIDATE_WIDENINGS = 20

# How many leading word tokens of an item's text form the default group. Four is what
# the published corpus' subject phrase costs — "the production deploy timeout",
# "the approved rollback command" — so items about one subject group together and the
# values they carry, current and stale alike, land in one bucket. Fewer tokens merges
# distinct subjects ("the production ..."); more starts eating the value itself, which
# would put an item's current and superseded versions in DIFFERENT groups and hand the
# arm back the flat ranking. A caller whose corpus is shaped differently passes
# `group_key=`.
GROUP_KEY_TOKENS = 4

# Word tokens, matching `lexical_system`'s tokenizer, so the grouping and the ranking
# it re-shapes read the same text the same way.
_TOKEN_RE = re.compile(r"\w+")

# The attribute the top-k-bounded arms in this package hold their retrieval width in
# (`LexicalTopKMemory`, `AbstractSemanticArm`). Widening is done through it because
# the uniform `MemorySystem` contract has no width verb, and adding one would touch
# every arm; see the module docstring.
_TOP_K_ATTR = "_top_k"


def content_prefix_group_key(memory_id: str, content: str) -> str:
    """The default group of an item: the first `GROUP_KEY_TOKENS` lowercased word
    tokens of its text, space-joined. Pure, deterministic, no judgement.

    An item whose text carries no word token falls back to its own id, so it forms a
    singleton group rather than joining every other empty item in one bucket — the
    absence of text is the absence of grouping information, not evidence of a shared
    subject."""
    tokens = _TOKEN_RE.findall(content.lower())[:GROUP_KEY_TOKENS]
    if not tokens:
        return memory_id
    return " ".join(tokens)


@contextmanager
def _widened(base: MemorySystem, width: int) -> Iterator[int | None]:
    """Temporarily raise the delegate's retrieval width to `width`, restoring it on the
    way out (including on an exception). Yields the width actually in force for the
    delegate call, or None when the delegate exposes no width to raise."""
    current = getattr(base, _TOP_K_ATTR, None)
    if not isinstance(current, int) or isinstance(current, bool):
        yield None
        return
    if current >= width:
        yield current
        return
    setattr(base, _TOP_K_ATTR, width)
    try:
        yield width
    finally:
        setattr(base, _TOP_K_ATTR, current)


class GroupedRetrievalMemory(MemorySystem):
    """Bucketed retrieval over a delegate arm: `per_group_k` items from each of at
    most `groups_k` groups, instead of a flat top-k."""

    name = "grouped"

    def __init__(
        self,
        *,
        base: MemorySystem | None = None,
        per_group_k: int = DEFAULT_PER_GROUP_K,
        groups_k: int = DEFAULT_GROUPS_K,
        group_key: Callable[[str, str], str] | None = None,
    ) -> None:
        # The delegate defaults to the deterministic in-repo top-k arm so
        # `build_memory_system("grouped")` (and the MEMBENCH_MEMORY_SYSTEM launch
        # path, which constructs with no kwargs) yields a usable arm rather than a
        # TypeError. Any other arm is injected explicitly.
        validate_top_k(per_group_k)
        validate_top_k(groups_k)
        self._base = base if base is not None else LexicalTopKMemory()
        self._per_group_k = per_group_k
        self._groups_k = groups_k
        self._group_key = group_key if group_key is not None else content_prefix_group_key
        # Mirror the delegate's capability flags: the wrapper adds bucketing, it does
        # not change what the underlying arm can do or which tracks it runs under.
        self.backend = self._base.backend
        self.supports_write = self._base.supports_write
        self.uses_scope = self._base.uses_scope

    @property
    def base(self) -> MemorySystem:
        return self._base

    @property
    def per_group_k(self) -> int:
        return self._per_group_k

    @property
    def groups_k(self) -> int:
        return self._groups_k

    @property
    def effective_k(self) -> int:
        """The maximum number of items a retrieve can return."""
        return self._per_group_k * self._groups_k

    @property
    def candidate_k(self) -> int:
        """The width the delegate is FIRST widened to — `effective_k` oversampled, so
        the later buckets have candidates to draw from. A retrieve that comes back
        short doubles from here; see `_scan`. Never the returned volume."""
        return self.effective_k * CANDIDATE_OVERSAMPLE

    def reset(self, trial_id: str) -> None:
        self._base.reset(trial_id)

    def close(self) -> None:
        self._base.close()

    def seed(self, memories: dict[str, str], ctx: StepContext) -> None:
        # Seeding is the delegate's own business (an arm may override it); passing it
        # straight through keeps world-noise injection byte-identical to running the
        # delegate alone.
        self._base.seed(memories, ctx)

    def _scan(
        self, request: RetrievalRequest, ctx: StepContext
    ) -> tuple[RetrieveResult, dict[str, str], int, int | None]:
        """Ask the delegate for candidates, widening and re-asking while the buckets
        come back short and the delegate still has rows to give.

        Returns the delegate's last result, the bucketed items, how many groups its
        candidate set offered, and the width actually in force for that last call
        (None when the delegate exposes no width to raise).

        Stopping is on evidence, not on a budget: a delegate that hands back fewer
        rows than it was asked for has no more to give, so a short bucket set after
        that is the substrate being short and widening again would only cost calls.
        """
        width = self.candidate_k
        widenings = 0
        while True:
            with _widened(self._base, width) as delegate_width:
                inner = self._base.retrieve(request, ctx)
            kept, groups_offered = self._bucket(inner.payloads)
            if (
                delegate_width is None
                or len(kept) >= self.effective_k
                or len(inner.payloads) < delegate_width
                or widenings >= MAX_CANDIDATE_WIDENINGS
            ):
                return inner, kept, groups_offered, delegate_width
            # `_widened` yields the delegate's own width when that is already the wider
            # of the two, so double from what was in force rather than from `width`.
            width = delegate_width * 2
            widenings += 1

    def retrieve(self, request: RetrievalRequest, ctx: StepContext) -> RetrieveResult:
        inner, kept, groups_offered, delegate_width = self._scan(request, ctx)
        fetched = "native" if delegate_width is None else str(delegate_width)
        event: MemoryEvent = inner.event.model_copy(
            update={
                # `groups` (what the candidate set offered), `returned` and `ceiling`
                # ride on the event so a caller can tell a bound cap from an unbound
                # one WITHOUT re-deriving the bucketing: groups <= groups_k with
                # returned < ceiling is the arm running narrower than its config
                # claims, which is the failure that confounded the first public grid.
                "concrete_tool": (
                    f"grouped[{self._base.name}].search("
                    f"per_group_k={self._per_group_k},groups_k={self._groups_k},"
                    f"delegate_top_k={fetched},groups={groups_offered},"
                    f"returned={len(kept)},ceiling={self.effective_k})"
                ),
                "retrieved_ids": list(kept),
            }
        )
        return RetrieveResult(
            payloads=kept,
            event=event,
            # The delegate's candidate-set signal is reported unchanged: it describes
            # what the SUBSTRATE matched, which bucketing does not alter.
            total_matched=inner.total_matched,
            near_duplicate_top=inner.near_duplicate_top,
            fts_truncated=inner.fts_truncated,
            source_trace_ids={
                mid: chain for mid, chain in inner.source_trace_ids.items() if mid in kept
            },
        )

    def _bucket(self, payloads: dict[str, str]) -> tuple[dict[str, str], int]:
        """Take at most `per_group_k` items per group and at most `groups_k` groups,
        preserving the delegate's rank order within a group and ordering groups by the
        rank of their best item. Pure: no arm state is read or written.

        Returns the kept items and HOW MANY groups the candidate set offered before the
        `groups_k` cap was applied — the number that says whether the cap bound."""
        buckets: dict[str, dict[str, str]] = {}
        for memory_id, content in payloads.items():
            bucket = buckets.setdefault(self._group_key(memory_id, content), {})
            if len(bucket) < self._per_group_k:
                bucket[memory_id] = content
        kept: dict[str, str] = {}
        # dict preserves insertion order, so the first `groups_k` keys are the groups
        # whose best item ranked highest.
        for bucket in list(buckets.values())[: self._groups_k]:
            kept.update(bucket)
        return kept, len(buckets)

    def write(self, memory_id: str, content: str, ctx: StepContext) -> MemoryEvent:
        # Unchanged pass-through: grouping is a READ-side shape. The delegate's own
        # event (its concrete_tool, its ids) is returned verbatim so a grouped run's
        # write telemetry is comparable with the delegate's run.
        return self._base.write(memory_id, content, ctx)
