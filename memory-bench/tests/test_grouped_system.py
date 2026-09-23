"""GroupedRetrievalMemory — bucketed retrieval over a delegate arm (mem-r6yzk.5).

What must hold: bucketing actually diversifies a flat top-k that would return one
group's items; `per_group_k` and `groups_k` are both respected; fewer groups than
`groups_k` degrades without error; the write path is the delegate's, unchanged; and
the injected VOLUME is pinned — the arm returns no more than `per_group_k * groups_k`
however wide the candidate scan runs, and restores the delegate's own width afterwards.
The scan itself is not one number: it starts at `candidate_k` and doubles while the
buckets come back short and the delegate still fills the width it is handed, so a
short result means a short pool rather than a narrow look at a deep one.

The group key reads CONTENT, and the last block here is why. The key used to split the
memory id on `-`, which grouped anything the generator had named with structure. The
released corpus publishes minted aliases (`k-<16 hex>`), so that key gave every item
its own group, `per_group_k` capped nothing, and the arm returned the flat top-k it
exists to replace. Those tests run against the real release rather than a fixture, so
the failure cannot come back unnoticed.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from itertools import combinations
from pathlib import Path
from typing import Any, NamedTuple

import pytest

from membench.memory_systems import build_memory_system, grouped_system, wired_memory_systems
from membench.memory_systems.base import (
    MemorySystem,
    RetrievalRequest,
    RetrieveResult,
)
from membench.memory_systems.grouped_system import (
    GROUP_KEY_TOKENS,
    GroupedRetrievalMemory,
    content_prefix_group_key,
)
from membench.memory_systems.lexical_system import LexicalTopKMemory
from membench.memory_systems.nemo_embed_system import NemoEmbedMemory
from membench.memory_systems.semantic_base import SemanticHit
from membench.runtime import IdClock, StepContext
from membench.schemas.memory_event import MemoryBackend, MemoryEvent, MemoryOperation

QUERY = "current value"

# Three groups under the default key (the first four word tokens of the CONTENT):
# "the deploy timeout current" / "the rollback command current" / "the primary region
# current". The ids carry no structure at all — they are shaped like the published
# aliases — so a key that read the id would find three groups of one.
#
# Every content overlaps the query identically (both query tokens, once each), so the
# delegate's deterministic tiebreak (id ascending) puts ALL of the first group ahead of
# the other two: exactly the flat-top-k failure the grouped arm exists to fix.
A1, A2, A3, A4 = (f"k-000000000000000{n}" for n in (1, 2, 3, 4))
B1, B2 = "k-0000000000000005", "k-0000000000000006"
C1 = "k-0000000000000007"

STORE = {
    A1: "the deploy timeout current value is one",
    A2: "the deploy timeout current value is two",
    A3: "the deploy timeout current value is three",
    A4: "the deploy timeout current value is four",
    B1: "the rollback command current value is five",
    B2: "the rollback command current value is six",
    C1: "the primary region current value is seven",
}

GROUP_A = "the deploy timeout current"
GROUP_B = "the rollback command current"
GROUP_C = "the primary region current"

# The released corpus, for the last block. Read directly: these tests are about what
# the arm does to the data a downloader actually gets.
RELEASE_DIR = Path(__file__).resolve().parents[2] / "public" / "data"
BASELINE_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_public_baseline.py"


def _baseline_driver() -> Any:
    """The public-baseline driver, path-imported (`scripts/` is not a package).

    Its width constants are IMPORTED, never restated here. They used to be copied into
    this file, and the copy went stale at 4 while the driver moved to 6: every
    released-corpus test below silently measured a configuration the grid does not run,
    which is why the mutant that widened `PUBLIC_TOP_K` never reddened a grouped test.
    A copied constant cannot guard the original."""
    spec = importlib.util.spec_from_file_location("run_public_baseline_grouped", BASELINE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_DRIVER = _baseline_driver()
PUBLIC_TOP_K: int = _DRIVER.PUBLIC_TOP_K
PUBLIC_PER_GROUP_K: int = _DRIVER.PUBLIC_PER_GROUP_K
PUBLIC_GROUPS_K: int = _DRIVER.PUBLIC_GROUPS_K


def _ctx() -> StepContext:
    return StepContext(trial_id="t1", session_id="s1", step_id="st1", clock=IdClock())


def _group(memory_id: str) -> str:
    return content_prefix_group_key(memory_id, STORE[memory_id])


def _lexical(top_k: int) -> LexicalTopKMemory:
    arm = LexicalTopKMemory(top_k=top_k)
    arm.reset("t1")
    for memory_id, content in STORE.items():
        arm.write(memory_id, content, _ctx())
    return arm


def _retrieve(arm: MemorySystem) -> RetrieveResult:
    return arm.retrieve(RetrievalRequest(query_text=QUERY), _ctx())


class _NoWidthArm(MemorySystem):
    """A delegate with NO `_top_k` to widen (the shape `oracle`/`filesystem` have:
    they return an exact set, not a ranking). Records the write calls it receives."""

    name = "no-width"
    backend = MemoryBackend.FILESYSTEM

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.resets: list[str] = []
        self.closed = 0

    def reset(self, trial_id: str) -> None:
        self.resets.append(trial_id)

    def close(self) -> None:
        self.closed += 1

    def retrieve(self, request: RetrievalRequest, ctx: StepContext) -> RetrieveResult:
        return RetrieveResult(
            payloads=dict(STORE),
            event=self._event(ctx, "no-width.search", MemoryOperation.SEARCH),
            total_matched=len(STORE),
        )

    def write(self, memory_id: str, content: str, ctx: StepContext) -> MemoryEvent:
        self.store[memory_id] = content
        return self._event(ctx, f"no-width.add({memory_id})", MemoryOperation.WRITE)

    def _event(self, ctx: StepContext, tool: str, op: MemoryOperation) -> MemoryEvent:
        return MemoryEvent(
            event_id=ctx.clock.event_id(),
            trial_id=ctx.trial_id,
            session_id=ctx.session_id,
            step_id=ctx.step_id,
            timestamp=ctx.clock.timestamp(),
            concrete_tool=tool,
            normalized_operation=op,
            backend=self.backend,
        )


def test_the_group_key_reads_content_and_not_the_id() -> None:
    assert content_prefix_group_key(A1, STORE[A1]) == GROUP_A
    assert content_prefix_group_key(C1, STORE[C1]) == GROUP_C
    # Two items with different ids and the same opening subject are ONE group. This is
    # the property an id-derived key could not have over minted aliases.
    assert _group(A1) == _group(A4) != _group(B1)
    # Case and punctuation are folded, so the same subject written two ways groups.
    assert content_prefix_group_key(A1, "The deploy timeout, current value: nine") == GROUP_A
    # Exactly GROUP_KEY_TOKENS tokens, so a longer shared prefix does not split.
    assert len(GROUP_A.split()) == GROUP_KEY_TOKENS
    # Text with no word token carries no grouping information, so the item is its own
    # group rather than joining every other empty item in one bucket.
    assert content_prefix_group_key(A1, "   ") == A1
    assert content_prefix_group_key(A1, "") == A1


def test_factory_wires_grouped() -> None:
    assert "grouped" in wired_memory_systems()
    arm = build_memory_system("grouped")
    assert isinstance(arm, GroupedRetrievalMemory)
    # The zero-kwarg factory/launch path yields a usable arm, not a half-built one.
    assert isinstance(arm.base, LexicalTopKMemory)


def test_flat_top_k_returns_one_group_and_grouping_diversifies() -> None:
    flat = _retrieve(_lexical(top_k=3)).payloads
    assert {_group(mid) for mid in flat} == {GROUP_A}

    grouped = GroupedRetrievalMemory(base=_lexical(top_k=3), per_group_k=1, groups_k=3)
    payloads = _retrieve(grouped).payloads
    assert {_group(mid) for mid in payloads} == {GROUP_A, GROUP_B, GROUP_C}
    # One per group, best-ranked member of each.
    assert list(payloads) == [A1, B1, C1]


def test_per_group_k_and_groups_k_are_both_honored() -> None:
    grouped = GroupedRetrievalMemory(base=_lexical(top_k=3), per_group_k=2, groups_k=2)
    payloads = _retrieve(grouped).payloads
    assert list(payloads) == [A1, A2, B1, B2]
    # groups_k=2 drops the third group entirely; per_group_k=2 caps the first group at
    # two of its four members.
    assert C1 not in payloads


def test_fewer_groups_than_groups_k_degrades_without_error() -> None:
    two_groups = {k: v for k, v in STORE.items() if k != C1}
    base = LexicalTopKMemory(top_k=3)
    base.reset("t1")
    for memory_id, content in two_groups.items():
        base.write(memory_id, content, _ctx())

    grouped = GroupedRetrievalMemory(base=base, per_group_k=2, groups_k=5)
    payloads = _retrieve(grouped).payloads
    assert {_group(mid) for mid in payloads} == {GROUP_A, GROUP_B}
    # 2 groups x per_group_k=2, not the 10 the effective k would allow.
    assert len(payloads) == 4


def test_custom_group_key_is_used() -> None:
    grouped = GroupedRetrievalMemory(
        base=_lexical(top_k=3),
        per_group_k=1,
        groups_k=3,
        group_key=lambda memory_id, content: content.rsplit(" ", 1)[-1],
    )
    payloads = _retrieve(grouped).payloads
    # Grouped by the TRAILING word instead of the leading subject: every item is its own
    # group, so per_group_k caps nothing and the delegate's flat order comes back.
    assert list(payloads) == [A1, A2, A3]


def test_write_path_is_the_delegates_unchanged() -> None:
    base = _NoWidthArm()
    grouped = GroupedRetrievalMemory(base=base, per_group_k=1, groups_k=2)
    event = grouped.write(A1, STORE[A1], _ctx())
    assert base.store == {A1: STORE[A1]}
    # verbatim delegate event — not re-labelled as a grouped write.
    assert event.concrete_tool == f"no-width.add({A1})"
    assert event.normalized_operation is MemoryOperation.WRITE


def test_reset_seed_and_close_pass_through() -> None:
    base = _NoWidthArm()
    grouped = GroupedRetrievalMemory(base=base)
    grouped.reset("t9")
    grouped.seed({"d1": "distractor one"}, _ctx())
    grouped.close()
    assert base.resets == ["t9"]
    assert base.store == {"d1": "distractor one"}
    assert base.closed == 1


def test_capability_flags_mirror_the_delegate() -> None:
    base = _NoWidthArm()
    base.uses_scope = True
    base.supports_write = False
    grouped = GroupedRetrievalMemory(base=base)
    assert grouped.uses_scope is True
    assert grouped.supports_write is False
    assert grouped.backend is MemoryBackend.FILESYSTEM


def test_volume_is_pinned_to_effective_k_and_never_exceeds_the_flat_delegate() -> None:
    grouped = GroupedRetrievalMemory(base=_lexical(top_k=3), per_group_k=2, groups_k=3)
    assert grouped.effective_k == 6
    got = _retrieve(grouped).payloads
    assert len(got) <= grouped.effective_k

    # The flat delegate at the SAME effective k is the volume control: grouping
    # re-shapes which items are injected, it does not inject more of them.
    flat = _retrieve(_lexical(top_k=grouped.effective_k)).payloads
    assert len(got) <= len(flat)


def test_delegate_is_widened_to_effective_k_then_restored() -> None:
    base = _lexical(top_k=3)
    grouped = GroupedRetrievalMemory(base=base, per_group_k=2, groups_k=3)
    result = _retrieve(grouped)
    assert grouped.candidate_k == 24  # effective_k=6 oversampled 4x
    # The widened width is reported in telemetry, so the scan width is visible — and so
    # is the shape actually achieved. STORE offers 3 groups (A/B/C) holding 4/2/1 items,
    # so a 2x3 config buckets to 2+2+1 = 5 against a ceiling of 6: `groups=3` says the
    # `groups_k` cap did not bind on this data, and `returned=5 < ceiling=6` says the
    # arm ran narrower than its configuration claims. That pair of numbers is what the
    # public grid needed and did not have.
    assert result.event.concrete_tool == (
        "grouped[lexical].search(per_group_k=2,groups_k=3,delegate_top_k=24,"
        "groups=3,returned=5,ceiling=6)"
    )
    assert len(result.payloads) == 5
    assert result.event.retrieved_ids == list(result.payloads)
    # ...and the delegate is handed back exactly as it was configured.
    assert base._top_k == 3


def _deep_store() -> dict[str, str]:
    """A pool that keeps the second and third groups' SECOND item below the first scan
    width. Six items carry both query tokens and rank first (ids 1-6); eighteen carry
    one and fill ranks 7-24; the two that complete groups B and C carry one token and
    the two highest ids, so they land at ranks 25 and 26 — outside a 24-row scan and
    inside a 48-row one.

    This is the shape `world-seed132-task2` had on the release the re-ask was built
    against, whose 32-candidate pool put four of one group's five items below
    `candidate_k` and cost the grouped arm an item against the flat arms' six. The
    corpus has been re-drawn since and no released record needs the widening today, so
    the shape is carried here rather than read off the corpus: a fixture keeps the
    behaviour under test when the next draw does not happen to exercise it."""
    store: dict[str, str] = {}
    for n in range(1, 5):
        store[f"k-{n:016d}"] = f"the deploy timeout current value is {n}"
    store[f"k-{5:016d}"] = "the rollback command current value is five"
    store[f"k-{6:016d}"] = "the primary region current value is six"
    for n in range(7, 25):
        store[f"k-{n:016d}"] = f"filler {n} holds current"
    store[f"k-{25:016d}"] = "the rollback command current setting is seven"
    store[f"k-{26:016d}"] = "the primary region current setting is eight"
    return store


def test_a_short_bucket_set_widens_the_scan_and_re_asks() -> None:
    """The scan is not one fixed multiple of the returned width.

    How far below the returned width a group's second item sits is a property of the
    corpus, so a single `candidate_k` cannot promise the buckets can fill. The arm
    re-asks at double the width while the buckets come back short AND the delegate
    filled the width it was given, which is the delegate saying it has more rows."""
    store = _deep_store()
    base = LexicalTopKMemory(top_k=3)
    base.reset("t1")
    for memory_id, content in store.items():
        base.write(memory_id, content, _ctx())
    grouped = GroupedRetrievalMemory(base=base, per_group_k=2, groups_k=3)
    assert grouped.candidate_k == 24

    # The witness: the FIRST width alone reaches four items, not six. Remove the
    # re-ask and this is what the arm injects against the flat arms' six.
    flat = LexicalTopKMemory(top_k=grouped.candidate_k)
    flat.reset("t1")
    for memory_id, content in store.items():
        flat.write(memory_id, content, _ctx())
    one_pass, _ = grouped._bucket(_retrieve(flat).payloads)
    assert len(one_pass) == 4, f"the one-pass scan reached {len(one_pass)} items, not 4"

    result = _retrieve(grouped)
    assert len(result.payloads) == grouped.effective_k
    groups = Counter(content_prefix_group_key(mid, store[mid]) for mid in result.payloads)
    assert sorted(groups.values()) == [2, 2, 2]
    # The width that actually ran is reported, so a re-ask is visible in telemetry
    # rather than inferred from the item count.
    assert "delegate_top_k=48" in result.event.concrete_tool
    assert "returned=6,ceiling=6" in result.event.concrete_tool
    # The delegate is handed back as configured however many times it was asked.
    assert base._top_k == 3


def test_the_scan_stops_when_the_delegate_runs_out_of_rows() -> None:
    """Re-asking is bounded by evidence: a delegate that hands back fewer rows than it
    was asked for has no more to give, so the arm stops there instead of doubling until
    the runaway guard. STORE offers 3 groups holding 4/2/1 items over 7 rows, which can
    never fill a 2x3 split, and the arm must not spend 20 delegate calls learning it."""
    calls: list[int] = []

    class _Counting(LexicalTopKMemory):
        def retrieve(self, request: RetrievalRequest, ctx: StepContext) -> RetrieveResult:
            calls.append(self._top_k)
            return super().retrieve(request, ctx)

    base = _Counting(top_k=3)
    base.reset("t1")
    for memory_id, content in STORE.items():
        base.write(memory_id, content, _ctx())
    grouped = GroupedRetrievalMemory(base=base, per_group_k=2, groups_k=3)
    result = _retrieve(grouped)
    assert calls == [24]
    assert len(result.payloads) == 5
    assert "returned=5,ceiling=6" in result.event.concrete_tool


def test_a_delegate_that_never_exhausts_stops_at_the_runaway_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one case the doubling cannot settle from evidence: a delegate that reports a
    full result at every width. No finite store does that, so the guard fires only on a
    broken adapter. The bound is lowered here because the shipped one doubles past
    twenty-five million rows, which is the point of it — a test that materialised that
    many rows would be measuring the machine. When the guard does fire the shortfall is
    still reported rather than masked, and the delegate's width is still restored."""
    monkeypatch.setattr(grouped_system, "MAX_CANDIDATE_WIDENINGS", 3)
    widths: list[int] = []

    class _Bottomless(LexicalTopKMemory):
        def retrieve(self, request: RetrievalRequest, ctx: StepContext) -> RetrieveResult:
            widths.append(self._top_k)
            inner = super().retrieve(request, ctx)
            padded = dict(inner.payloads)
            while len(padded) < self._top_k:
                padded[f"pad-{len(widths)}-{len(padded)}"] = f"pad {len(padded)} value"
            return RetrieveResult(payloads=padded, event=inner.event, total_matched=len(padded))

    base = _Bottomless(top_k=3)
    base.reset("t1")
    for memory_id, content in STORE.items():
        base.write(memory_id, content, _ctx())
    grouped = GroupedRetrievalMemory(base=base, per_group_k=2, groups_k=3)
    result = _retrieve(grouped)
    assert widths == [24, 48, 96, 192]
    assert "returned=5,ceiling=6" in result.event.concrete_tool
    assert base._top_k == 3


def test_a_wider_delegate_is_not_narrowed() -> None:
    base = _lexical(top_k=20)
    grouped = GroupedRetrievalMemory(base=base, per_group_k=1, groups_k=2)
    result = _retrieve(grouped)
    # candidate_k is 8, below the delegate's own 20 — the arm widens, never narrows.
    assert "delegate_top_k=20" in result.event.concrete_tool
    assert base._top_k == 20


def test_delegate_width_is_restored_when_the_delegate_raises() -> None:
    class _Raising(LexicalTopKMemory):
        seen_width = 0

        def retrieve(self, request: RetrievalRequest, ctx: StepContext) -> RetrieveResult:
            _Raising.seen_width = self._top_k
            raise RuntimeError("backend down")

    base = _Raising(top_k=3)
    grouped = GroupedRetrievalMemory(base=base, per_group_k=2, groups_k=3)
    with pytest.raises(RuntimeError, match="backend down"):
        _retrieve(grouped)
    # widened for the call, restored on the way out of the exception.
    assert _Raising.seen_width == 24
    assert base._top_k == 3


def test_a_delegate_with_no_width_is_called_unchanged() -> None:
    grouped = GroupedRetrievalMemory(base=_NoWidthArm(), per_group_k=1, groups_k=2)
    result = _retrieve(grouped)
    assert "delegate_top_k=native" in result.event.concrete_tool
    assert len(result.payloads) == 2
    # the delegate's substrate signal is carried through untouched.
    assert result.total_matched == len(STORE)


@pytest.mark.parametrize(("per_group_k", "groups_k"), [(0, 3), (3, 0), (-1, 1)])
def test_non_positive_widths_raise(per_group_k: int, groups_k: int) -> None:
    with pytest.raises(ValueError):
        GroupedRetrievalMemory(per_group_k=per_group_k, groups_k=groups_k)


# ---------------------------------------------------------------------------------
# mem-r6yzk B6 — against the real release, where the key went vacuous
# ---------------------------------------------------------------------------------


class _TrigramClient:
    """A deterministic embedding stand-in: hashed character trigrams, cosine ranked.

    Not the NeMo model and not a claim about it. It exists so the arm's SEAM can be
    exercised over the released pools with no weights and no network, with a ranking
    that is genuinely not token-set overlap — which is the only property the test
    below needs from it."""

    def __init__(self, dims: int = 64) -> None:
        self._dims = dims
        self._scopes: dict[str, dict[str, str]] = {}

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self._dims
        low = text.lower()
        for index in range(max(len(low) - 2, 0)):
            digest = hashlib.sha256(low[index : index + 3].encode("utf-8")).hexdigest()
            vector[int(digest[:8], 16) % self._dims] += 1.0
        return vector

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        dot = sum(x * y for x, y in zip(left, right, strict=True))
        norms = math.sqrt(sum(x * x for x in left)) * math.sqrt(sum(y * y for y in right))
        return 0.0 if norms == 0 else dot / norms

    def store(self, *, scope: str, content: str, memory_id: str) -> str:
        self._scopes.setdefault(scope, {})[memory_id] = content
        return memory_id

    def query(self, *, scope: str, query_text: str, top_k: int) -> Sequence[SemanticHit]:
        probe = self._vector(query_text)
        hits = [
            SemanticHit(memory_id=mid, content=text, score=self._cosine(probe, self._vector(text)))
            for mid, text in self._scopes.get(scope, {}).items()
        ]
        hits.sort(key=lambda hit: (-(hit.score or 0.0), hit.memory_id))
        return hits[:top_k]

    def clear(self, *, scope: str) -> None:
        self._scopes.pop(scope, None)


def _released() -> list[dict]:
    files = sorted(RELEASE_DIR.glob("*.jsonl"))
    assert files, f"no released JSONL under {RELEASE_DIR}"
    return [
        json.loads(line)
        for path in files
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _pool(record: dict) -> dict[str, str]:
    return {
        str(memory_id): str(text)
        for bucket in ("gold", "distractors", "superseded")
        for memory_id, text in record["evidence"][bucket].items()
    }


def _select(arm: MemorySystem, pool: dict[str, str], query: str) -> set[str]:
    arm.reset("t1")
    arm.seed(pool, _ctx())
    return set(arm.retrieve(RetrievalRequest(query_text=query), _ctx()).payloads)


def test_the_group_key_binds_on_the_released_pools() -> None:
    """The regression, stated over the real data: the key must put several published
    candidates in one bucket. Over minted aliases the old id-derived key gave one group
    per item, which is the state in which `per_group_k` caps nothing."""
    for record in _released():
        pool = _pool(record)
        groups = {content_prefix_group_key(mid, text) for mid, text in pool.items()}
        assert 1 < len(groups) < len(pool), (
            f"{record['record_id']}: {len(groups)} group(s) over {len(pool)} candidates; "
            "the key is either vacuous or a partition into singletons"
        )


def _public_grouped() -> GroupedRetrievalMemory:
    """The grouped arm EXACTLY as `scripts/run_public_baseline.py` builds it."""
    return GroupedRetrievalMemory(
        base=LexicalTopKMemory(top_k=PUBLIC_TOP_K),
        per_group_k=PUBLIC_PER_GROUP_K,
        groups_k=PUBLIC_GROUPS_K,
    )


def test_the_published_grouped_split_spends_exactly_the_flat_width() -> None:
    """THE GUARD the mutant should have tripped, and did not.

    The driver's comment claims the grouped arm injects "the same item width as the
    flat arms", which makes the grouped-vs-lexical delta a selection result. That claim
    was FALSE under the old `per_group_k=1, groups_k=PUBLIC_TOP_K` split: the corpus
    offers 5 groups per record on this release and 3 to 6 on the one it replaced, so a
    cap of 6 groups cut nothing and the arm returned one item per group — 5 items and
    534 chars here, against the flat arms' 6 and 655. The delta being published was a
    volume difference.

    So the claim is measured here, on the released corpus, per record and exactly — not
    as an inequality, which `len(grouped) <= PUBLIC_TOP_K` was and which a 3-item
    result satisfies."""
    assert PUBLIC_PER_GROUP_K * PUBLIC_GROUPS_K == PUBLIC_TOP_K, (
        f"the split {PUBLIC_PER_GROUP_K}x{PUBLIC_GROUPS_K} ceilings at "
        f"{PUBLIC_PER_GROUP_K * PUBLIC_GROUPS_K} items, not the flat arms' {PUBLIC_TOP_K}"
    )
    records = _released()
    for record in records:
        pool = _pool(record)
        query = str(record["question"]["text"])
        flat = _select(LexicalTopKMemory(top_k=PUBLIC_TOP_K), pool, query)
        grouped = _select(_public_grouped(), pool, query)
        groups = Counter(content_prefix_group_key(mid, pool[mid]) for mid in grouped)
        assert len(flat) == PUBLIC_TOP_K, f"{record['record_id']}: flat returned {len(flat)}"
        assert len(grouped) == len(flat), (
            f"{record['record_id']}: grouped injected {len(grouped)} items against the flat "
            f"arms' {len(flat)}; the delta would be a volume difference, not a selection one"
        )
        assert len(groups) == PUBLIC_GROUPS_K, (
            f"{record['record_id']}: grouped spread {len(grouped)} items over {len(groups)} "
            f"group(s), not the {PUBLIC_GROUPS_K} the driver claims"
        )
        assert max(groups.values()) == PUBLIC_PER_GROUP_K, (
            f"{record['record_id']}: deepest group holds {max(groups.values())} items, not "
            f"the {PUBLIC_PER_GROUP_K} the driver claims"
        )


def test_both_published_grouped_knobs_bind_on_the_released_corpus() -> None:
    """Neither knob is dead configuration: each one CUTS something the delegate offered.

    Measured against the candidate set the arm actually buckets (the delegate widened to
    `candidate_k`), because that is what the caps are applied to. `groups_k` binds when
    the candidates span more groups than it admits; `per_group_k` binds when some group
    holds more items than it admits. A knob that cuts on zero records is a knob whose
    value could be anything, which is how `groups_k=6` survived into a published grid."""
    records = _released()
    groups_k_cut = per_group_k_cut = 0
    for record in records:
        pool = _pool(record)
        query = str(record["question"]["text"])
        candidates = _select(LexicalTopKMemory(top_k=_public_grouped().candidate_k), pool, query)
        buckets = Counter(content_prefix_group_key(mid, pool[mid]) for mid in candidates)
        groups_k_cut += int(len(buckets) > PUBLIC_GROUPS_K)
        per_group_k_cut += int(any(depth > PUBLIC_PER_GROUP_K for depth in buckets.values()))
    n = len(records)
    assert groups_k_cut > 0, (
        f"groups_k={PUBLIC_GROUPS_K} cut nothing on any of {n} records; it is dead "
        "configuration and the arm is running at whatever width the corpus happens to offer"
    )
    assert per_group_k_cut > 0, (
        f"per_group_k={PUBLIC_PER_GROUP_K} cut nothing on any of {n} records; the arm is "
        "returning the flat ranking it exists to re-shape"
    )


def _flat_group_shape(pool: dict[str, str], query: str) -> tuple[int, ...]:
    """The flat top-k's group-depth multiset, descending: how many of the flat arm's
    own items fall in its first group, its second, and so on."""
    arm = LexicalTopKMemory(top_k=PUBLIC_TOP_K)
    arm.reset("t1")
    arm.seed(pool, _ctx())
    ranked = arm.retrieve(RetrievalRequest(query_text=query), _ctx()).payloads
    depths = Counter(content_prefix_group_key(mid, text) for mid, text in ranked.items())
    return tuple(sorted(depths.values(), reverse=True))


# The one flat shape the published split cannot re-shape: `PUBLIC_PER_GROUP_K` items in
# each of `PUBLIC_GROUPS_K` groups IS what the arm imposes, so on a record whose flat
# ranking already has it, bucketing is the identity map and grouped == flat by
# arithmetic. Derived from the two published knobs, never written out, so a split change
# moves it automatically.
_SPLIT_SHAPE = tuple([PUBLIC_PER_GROUP_K] * PUBLIC_GROUPS_K)


def test_grouped_agrees_with_flat_only_where_the_split_is_already_the_flat_shape() -> None:
    """Arm separation, stated EXACTLY rather than tolerated.

    This guard used to read `reshaped > n // 2`: a bound of half the corpus, which
    tolerated eighty identical selections and could not see the corpus move from zero
    identical records to nine. A bound that loose is not measuring arm separation, it is
    declaring that some exists.

    The exact claim replaces it. `grouped == flat` on a record IF AND ONLY IF the flat
    top-k's group-depth multiset is already `_SPLIT_SHAPE` — at which point the arm's
    caps cut nothing and bucketing is the identity map. Every other record must differ.
    So an identical selection is either arithmetic (and named) or a defect (and fails),
    with no tolerance in between and no count to keep in sync with the corpus.

    How many records that is depends on the draw, which is the point: it read 9 of 160 on
    the corpus round 4 audited and 3 of 160 after the release was re-drawn, and the
    identity holds on both without a number to re-sync. The identical ones are not a
    scan-width or a corpus regression -- the same records appear with the candidate scan
    pinned to the returned width -- they are what the round-4 split change from
    `1 x PUBLIC_TOP_K` to `PUBLIC_PER_GROUP_K x PUBLIC_GROUPS_K` implies. Under the old
    split the identity shape was (1, 1, 1, 1, 1, 1), which these pools never produce (the
    widest flat spread is five groups), so round 3's zero identical records was not
    evidence of separation: it was the same dead `groups_k` that let the arm return fewer
    items than the flat width.
    """
    records = _released()
    identical: list[str] = []
    forced: list[str] = []
    for record in records:
        pool = _pool(record)
        query = str(record["question"]["text"])
        flat = _select(LexicalTopKMemory(top_k=PUBLIC_TOP_K), pool, query)
        grouped = _select(_public_grouped(), pool, query)
        if grouped == flat:
            identical.append(str(record["record_id"]))
        if _flat_group_shape(pool, query) == _SPLIT_SHAPE:
            forced.append(str(record["record_id"]))

    detail = (
        f"{len(identical)}/{len(records)} identical, {len(forced)} forced by the flat "
        f"shape {_SPLIT_SHAPE}; unexplained {sorted(set(identical) - set(forced))}; "
        f"reshaped-but-forced {sorted(set(forced) - set(identical))}"
    )
    print(detail)
    assert identical == forced, detail
    # A split whose shape the corpus ALWAYS carries would make this test vacuously true
    # while the arm returned the flat ranking on every record, so the published split
    # has to leave most of the corpus re-shaped.
    assert len(identical) < len(records) // 4, detail


# The re-shaping may move gold either way on any one record; what it may not do is lose
# gold systematically. Stated as a paired sign test, so the bound is a false-positive rate
# rather than a distance read off the corpus. 0.01 matches the acceptance bounds the public
# corpus contract uses, and the direction is one-sided: grouping that HELPS is not a defect.
#
# The tolerance this replaced, ``abs(grouped - flat) / n <= 0.01``, was a number fitted to
# the corpus it was written against. The release was re-drawn and the same two arms landed
# 0.0125 apart, reddening a guard that had never measured anything: per record the
# re-shaping loses gold 43 times and gains it 32, which an exchangeable null produces at
# p = 0.1240.
MIN_RESHAPE_NEUTRALITY_P = 0.01


class _PairedRecall(NamedTuple):
    """The flat arm against a re-shaped selection, compared record by record."""

    label: str
    n_records: int
    flat: float
    other: float
    losses: int
    gains: int
    p_value: float

    def __str__(self) -> str:
        return (
            f"{self.label}: gold recall {self.other:.4f} against the flat arm's "
            f"{self.flat:.4f} over {self.n_records} records, losing on {self.losses} and "
            f"gaining on {self.gains}, one-sided p = {self.p_value:.4f}"
        )


def _paired_recall(
    label: str, select: Callable[[dict, dict[str, str], str], set[str]]
) -> _PairedRecall:
    """Score ``select`` against the flat arm on the same records, and test the losses.

    Under "the re-shaping is blind to which candidate is gold" a record is as likely to
    lose gold as to gain it, so the discordant records are exchangeable and the count of
    losses is binomial(discordant, 1/2). That is an exact null, evaluated by enumeration:
    no draws, no seed. Records that neither gain nor lose carry no information about the
    direction and are excluded, which is what makes the test a sign test.
    """
    records = _released()
    flat_total = other_total = 0.0
    losses = gains = 0
    for record in records:
        pool = _pool(record)
        gold = set(record["evidence"]["gold_ids"])
        query = str(record["question"]["text"])
        flat = len(_select(LexicalTopKMemory(top_k=PUBLIC_TOP_K), pool, query) & gold) / len(gold)
        other = len(select(record, pool, query) & gold) / len(gold)
        flat_total += flat
        other_total += other
        losses += other < flat
        gains += other > flat
    discordant = losses + gains
    tail = sum(math.comb(discordant, k) for k in range(losses, discordant + 1))
    return _PairedRecall(
        label=label,
        n_records=len(records),
        flat=flat_total / len(records),
        other=other_total / len(records),
        losses=losses,
        gains=gains,
        p_value=tail / 2**discordant,
    )


def _gold_last_split(record: dict, pool: dict[str, str], query: str) -> set[str]:
    """The published split shape, filled with distractors wherever the group allows it.

    This is the arm the guard exists to catch, spelled out: it spends exactly the flat
    width, two candidates from each of three groups, so it clears the width guard and the
    separation guard above while answering nothing.
    """
    gold = set(record["evidence"]["gold_ids"])
    groups: dict[str, list[str]] = {}
    for memory_id, text in pool.items():
        groups.setdefault(content_prefix_group_key(memory_id, text), []).append(memory_id)
    picked: set[str] = set()
    for key in sorted(groups)[:PUBLIC_GROUPS_K]:
        members = sorted(groups[key], key=lambda memory_id: (memory_id in gold, memory_id))
        picked.update(members[:PUBLIC_PER_GROUP_K])
    return picked


def test_grouping_the_released_selection_does_not_cost_gold() -> None:
    """The other half of the old guard: the re-shaping is not paid for in gold.

    An arm that returned six distractors spread over three groups would satisfy the
    separation guard above and fail here. The comparison is per record and one-sided, so
    what it asserts is that grouping does not lose gold systematically -- not that the two
    arms land within some distance of each other, which is a property of the corpus rather
    than of the arms and moves whenever the release is re-drawn.
    """
    published = _paired_recall(
        "grouped", lambda record, pool, query: _select(_public_grouped(), pool, query)
    )
    print(published)
    assert published.p_value >= MIN_RESHAPE_NEUTRALITY_P, (
        f"the published split loses gold the flat arm found more often than a blind "
        f"re-shaping would -- {published}. The split is the thing to change, not the bound"
    )
    assert published.other > 0, f"the grouped arm recalls no gold at all -- {published}"

    # Power. A split that keeps the shape and drops the answer has to fail, or the pass
    # above is only saying that two arms agree.
    witness = _paired_recall("gold-last split witness", _gold_last_split)
    print(witness)
    assert witness.p_value < MIN_RESHAPE_NEUTRALITY_P, (
        "a selection that spends the published split on distractors is not read as losing "
        f"gold, so the guard above asserts nothing -- {witness}"
    )


def _offline_arm_selections(record: dict) -> dict[str, set[str]]:
    """Every published arm whose selection is a function of the released record alone,
    each driven in the request shape `run_public_baseline.retrieve_for` gives it: `none`
    retrieves nothing, `oracle` is handed the gold ids exactly, and the three ranking
    arms rank the seeded candidate pool against the question.

    `ours` is the sixth published arm and is absent deliberately: it reads a live mem
    store through the built `mem` CLI, so it has no selection recoverable from the
    release and no test here can speak for it."""
    pool = _pool(record)
    query = str(record["question"]["text"])
    return {
        "none": _select(build_memory_system("none"), pool, query),
        "lexical": _select(LexicalTopKMemory(top_k=PUBLIC_TOP_K), pool, query),
        "nemo-embed": _select(
            NemoEmbedMemory(client=_TrigramClient(), top_k=PUBLIC_TOP_K), pool, query
        ),
        "grouped": _select(_public_grouped(), pool, query),
        "oracle": {str(memory_id) for memory_id in record["evidence"]["gold_ids"]},
    }


def test_no_two_released_arms_make_the_same_selection_unless_the_split_forces_it() -> None:
    """The regression that a half-corpus bound cannot see, measured over EVERY pair of
    arms this benchmark can drive offline rather than over the one pair that moved.

    Two arms that select identically are one arm reported twice, so the published table
    is a column narrower than it claims. Ten pairs over the five offline arms, and the
    only pair allowed to agree on ANY record is lexical-vs-grouped, on exactly the
    records where the flat ranking already carries the published split — the identity
    case the test above pins. Everything else must be zero, with no tolerance: this is
    where a future width, key or split change that collapses two arms shows up.

    This also replaces a `differ > n // 2` bound that covered the lexical/nemo-embed
    pair alone. Its premise stands and is asserted below — an arm whose selection is the
    whole pool has not ranked anything — but half the corpus was never the right bound
    for it either."""
    records = _released()
    pairs = list(combinations(sorted(_offline_arm_selections(records[0])), 2))
    agree: dict[tuple[str, str], list[str]] = {pair: [] for pair in pairs}
    forced: list[str] = []
    for record in records:
        record_id = str(record["record_id"])
        pool = _pool(record)
        selections = _offline_arm_selections(record)
        for name in ("lexical", "nemo-embed", "grouped"):
            # Choosing at all is the precondition: a selection equal to the pool is not
            # a ranking result, it is the absence of one, and every pair of arms wide
            # enough to return everything agrees trivially.
            assert selections[name] < set(pool), f"{record_id}: {name} returned the whole pool"
        if _flat_group_shape(pool, str(record["question"]["text"])) == _SPLIT_SHAPE:
            forced.append(record_id)
        for left, right in pairs:
            if selections[left] == selections[right]:
                agree[(left, right)].append(record_id)

    table = "; ".join(f"{left}/{right} {len(ids)}" for (left, right), ids in agree.items())
    # Printed on the way past, not only on the way out: the whole point of the guard is
    # the table, and a reviewer asking "how separated are the arms" should not have to
    # break one to read it.
    print(f"identical selections over {len(records)} records -- {table}")
    for pair, ids in agree.items():
        expected = forced if pair == ("grouped", "lexical") else []
        assert ids == expected, (
            f"{pair[0]} and {pair[1]} made the same selection on {len(ids)} of "
            f"{len(records)} records; {sorted(set(ids) ^ set(expected))} is not what the "
            f"published split forces -- the two arms are not separated. Whole table over "
            f"{len(records)} records: {table}"
        )


def test_an_id_derived_group_key_degrades_to_the_flat_ranking() -> None:
    """The defect, reproduced. Keyed on the published id, every candidate is its own
    group, so the arm returns exactly what the flat delegate returned — which is why
    the key reads content.

    The split here is `1 x PUBLIC_TOP_K` on purpose and is NOT the driver's: it is the
    one that makes "every item is its own group" produce a result the same size as the
    flat ranking, so the equality below is about the KEY alone and no width difference
    can account for it."""
    identical = 0
    records = _released()
    for record in records:
        pool = _pool(record)
        query = str(record["question"]["text"])
        flat = _select(LexicalTopKMemory(top_k=PUBLIC_TOP_K), pool, query)
        by_id = _select(
            GroupedRetrievalMemory(
                base=LexicalTopKMemory(top_k=PUBLIC_TOP_K),
                per_group_k=1,
                groups_k=PUBLIC_TOP_K,
                group_key=lambda memory_id, content: memory_id,
            ),
            pool,
            query,
        )
        identical += int(by_id == flat)
    assert identical == len(records)
