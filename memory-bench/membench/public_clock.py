"""The published close order, dealt so no subject group's clock points at its gold.

A published record lays its whole candidate pool on a synthetic grid, one member per
slot. Every candidate names the subject it speaks to, so an arm gets the pool's
partition into SUBJECT GROUPS for free, without retrieving anything and without reading
the question. Whatever the clock says inside one of those groups is therefore a policy
anyone can run: "for each subject group, answer with that group's latest-closing
candidate" needs no retrieval and no question.

The exporter used to lay the grid with a single uniform shuffle of the pool. That is
unbiased and it is not enough. A uniform shuffle makes the gold's rank inside its group
uniform IN EXPECTATION, and a frozen corpus is one draw: the release measured before
this module read P(gold | its group's latest closer) at 105/400 = 0.2625 on the project
tier against a 0.2049 chance rate, which is a 1-in-400 draw under the exporter's own
shuffle (400 redraws of the released partition put the mean at 0.2039, sd 0.0201, and
one redraw at or above the released figure). On that draw the zero-retrieval group-latest
policy scored recall 0.2625 and beat every real arm in the release, lexical included,
while injecting 20% fewer characters. Nothing in the pipeline looked at the realised
draw before publishing it.

So the rank is DEALT rather than drawn. Every subject group of a given tier, size and
stale count takes one card from a balanced deck of within-group ranks, and the realised
count of "gold closed k-th in its group" equals the chance count by construction, for
every k, not just for the last slot. The same deal fixes the staleness trap's reach:
among chain groups whose gold is not on the last slot, which role owns that slot is
dealt too, so the trap fires at exactly the rate the pool sets instead of at whatever
rate the shuffle happened to draw.

What is NOT dealt is the interleaving - which slots of the whole pool a given group
owns. That stays a uniform shuffle, so every whole-pool statistic keeps the null
``tests/test_public_corpus_contract.py`` scores it against, and the group-level deal
rides inside it.

The deal is keyed by the mint seed, which is not published. A downloader can recompute
the pool ORDER (``public_export.pool_order`` takes no key material) but not the deal, so
the balance is a property of the corpus rather than a rule a solver can invert.
"""

from __future__ import annotations

import hashlib
import hmac
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TypeVar

from membench.generators.enterprise_workflow import fact_subject

__all__ = [
    "ClockDeal",
    "GroupShape",
    "balanced_deck",
    "group_key",
    "shapes_of",
    "slot_order",
    "subject_groups",
]

_Face = TypeVar("_Face")


def subject_groups(pool_ids: Sequence[str], texts: Mapping[str, str]) -> dict[str, list[str]]:
    """The pool partitioned by the subject each candidate speaks to, in pool order.

    This is the partition an arm builds for free from the published texts, which is why
    it is the partition the clock has to be neutral INSIDE and not merely across.

    ``fact_subject`` is the generator's own format-anchored parse of the published
    sentence, so a subject that stops being readable off the text raises there rather
    than quietly collapsing two groups into one here."""
    groups: dict[str, list[str]] = {}
    for memory_id in pool_ids:
        groups.setdefault(fact_subject(texts[memory_id]), []).append(memory_id)
    return groups


def group_key(record_id: str, subject: str) -> str:
    """The deal's name for one subject group. Records and subjects are both free text,
    so they are joined on a separator neither can contain."""
    if "\x00" in record_id or "\x00" in subject:
        raise ValueError(f"a NUL in {record_id!r}/{subject!r} would make two groups one key")
    return f"{record_id}\x00{subject}"


@dataclass(frozen=True)
class GroupShape:
    """One published subject group, in the terms the deal balances over.

    Two balances, because the two readable ranks are fitted at different widths.

    The gold rank is dealt per CELL, (tier, size, stale count, subject). The first
    three set the chance rate a cut is scored against - the rate is 1/size, the tiers
    are reported separately, and a chain group's non-gold slots are not interchangeable
    with a plain group's - and subject joins them because subject is the one further cut
    an adversary reads off a published record for free. Balancing the coarse cut alone
    leaves each subject a free draw inside it, and a per-subject rate is a rule that can
    be fitted offline against the frozen release and then run question-blind and
    key-blind at eval time: the exact shape this module exists to remove, one cut finer.
    Cells run to about seven groups, so a cell of six over six ranks holds each rank
    once and little is left undetermined inside it. That is deliberate and it is not
    reachable: spending it needs the OTHER records' gold at eval time, which is the
    answer key, and the answer key is what the harness seam withholds.

    The trap - which role owns the last slot when the gold does not - is dealt per
    STRATUM, (tier, size, stale count), with the subjects pooled. It reaches chain
    groups only, about 27 per stratum, so per-subject cells of three would be balancing
    noise while costing the pooled rate the precision it does have."""

    record_id: str
    tier: str
    subject: str
    size: int
    n_stale: int

    def __post_init__(self) -> None:
        if self.size < 2:
            raise ValueError(f"{self.key}: a subject group of {self.size} has no clock to deal")
        if not 0 <= self.n_stale <= self.size - 2:
            raise ValueError(
                f"{self.key}: {self.n_stale} stale of {self.size} leaves no non-gold, "
                "non-stale member, so the last slot's role cannot be dealt"
            )

    @property
    def key(self) -> str:
        return group_key(self.record_id, self.subject)

    @property
    def cell(self) -> tuple[str, int, int, str]:
        """The gold rank's balancing cell."""
        return (self.tier, self.size, self.n_stale, self.subject)

    @property
    def stratum(self) -> tuple[str, int, int]:
        """The trap's balancing stratum, which is the cell with subject pooled away."""
        return (self.tier, self.size, self.n_stale)


def balanced_deck(rng: random.Random, faces: Sequence[_Face], size: int) -> list[_Face]:
    """``size`` cards over ``faces``, every face dealt within one of every other.

    A draw is not a deal. Drawing each card independently leaves the realised counts a
    binomial fluctuation around the target, and a frozen corpus publishes whatever
    fluctuation it drew - which is the defect this module exists to remove. Dealing from
    a balanced deck puts the realised count at the target by construction.

    Two details keep the balance from becoming its own rule. The residue, when ``size``
    is not a multiple of ``len(faces)``, goes to a keyed random subset of the faces
    rather than always to the first ones. And the deck is shuffled as a whole rather
    than cycle by cycle, so the cards carry no run structure: under a cycle-by-cycle
    deal a solver who read k-1 cards of a cycle would have the k-th for free."""
    if not faces:
        raise ValueError("a deck needs at least one face")
    if size < 0:
        raise ValueError(f"cannot deal {size} cards")
    cycle = list(faces)
    rng.shuffle(cycle)
    deck = [cycle[index % len(cycle)] for index in range(size)]
    rng.shuffle(deck)
    return deck


def _dealing_order(
    mint_seed: str, members: Sequence[GroupShape], cell: tuple[object, ...]
) -> list[GroupShape]:
    """``members`` in the order the deck is dealt to, which is keyed, never build order.

    A cell dealt in the order its worlds happen to be walked would hand a solver who
    knows that order a position in the deck."""
    ordered = sorted(members, key=lambda shape: _shuffle_key(mint_seed, shape.key))
    keys = [shape.key for shape in ordered]
    if len(keys) != len(set(keys)):
        raise ValueError(
            f"cell {cell} holds two groups under one key; one would overwrite "
            "the other's dealt slot"
        )
    return ordered


class ClockDeal:
    """Where each subject group's gold sits on that group's own clock, corpus-wide.

    Built over every group the corpus publishes, so the balance is a property of the
    release rather than of any one record. Built over a single record's groups - which
    is what a unit test has - each stratum holds one or two groups and the deal degrades
    to the keyed uniform draw it replaces; that is honest, because a corpus of one has no
    realised rate to balance.
    """

    def __init__(self, shapes: Iterable[GroupShape], *, mint_seed: str) -> None:
        if not mint_seed:
            raise ValueError("the clock deal is keyed by the mint seed and it is empty")
        by_cell: dict[tuple[str, int, int, str], list[GroupShape]] = {}
        by_stratum: dict[tuple[str, int, int], list[GroupShape]] = {}
        for shape in shapes:
            by_cell.setdefault(shape.cell, []).append(shape)
            by_stratum.setdefault(shape.stratum, []).append(shape)

        self._gold_rank: dict[str, int] = {}
        for cell, cell_members in sorted(by_cell.items()):
            tier, size, n_stale, subject = cell
            rng = random.Random(
                f"{mint_seed}\x00clock\x00{tier}\x00{size}\x00{n_stale}\x00{subject}"
            )
            ordered = _dealing_order(mint_seed, cell_members, cell)
            for shape, rank in zip(
                ordered, balanced_deck(rng, range(size), len(ordered)), strict=True
            ):
                self._gold_rank[shape.key] = rank

        self._stale_last: dict[str, bool] = {}
        for stratum, members in sorted(by_stratum.items()):
            tier, size, n_stale = stratum
            if n_stale == 0:
                continue
            # The trap's reach is the other half of the same channel. Among the chain
            # groups whose gold is NOT on the last slot, the role that owns it is dealt
            # over the non-gold members, so P(the latest member of a chain group is
            # stale) is exactly n_stale/size instead of a second free draw.
            rng = random.Random(f"{mint_seed}\x00trap\x00{tier}\x00{size}\x00{n_stale}")
            ordered = _dealing_order(mint_seed, members, stratum)
            tail = [shape for shape in ordered if self._gold_rank[shape.key] != size - 1]
            faces = [True] * n_stale + [False] * (size - 1 - n_stale)
            for shape, stale_last in zip(tail, balanced_deck(rng, faces, len(tail)), strict=True):
                self._stale_last[shape.key] = stale_last

    def gold_rank(self, key: str) -> int:
        """Which slot of its own group the gold closes on, counting from the earliest."""
        if key not in self._gold_rank:
            raise KeyError(f"{key!r} was not dealt; the deal and the corpus disagree")
        return self._gold_rank[key]

    def arrange(
        self,
        key: str,
        members: Sequence[str],
        *,
        gold_id: str,
        stale_ids: frozenset[str],
        rng: random.Random,
    ) -> list[str]:
        """One subject group's members in close order, earliest first.

        The dealt slots are honoured exactly; every other member is placed uniformly, so
        the deal constrains the two ranks that are readable without retrieval and
        nothing else."""
        rank = self.gold_rank(key)
        size = len(members)
        if size != len(set(members)):
            raise ValueError(f"{key}: a member appears twice in its own subject group")
        if not 0 <= rank < size:
            raise ValueError(f"{key}: dealt slot {rank} of a group of {size}")
        if gold_id not in members:
            raise ValueError(f"{key}: the gold {gold_id!r} is not in its own subject group")

        rest = [memory_id for memory_id in members if memory_id != gold_id]
        rng.shuffle(rest)
        if rank < size - 1:
            stale_last = self._stale_last.get(key)
            if stale_last is not None:
                rest = _with_role_last(key, rest, stale_ids=stale_ids, stale_last=stale_last)
        return [*rest[:rank], gold_id, *rest[rank:]]


def _with_role_last(
    key: str, rest: Sequence[str], *, stale_ids: frozenset[str], stale_last: bool
) -> list[str]:
    """``rest`` reordered so its last member is stale, or is not, as dealt.

    One member is moved to the end and the others keep the uniform order they arrived
    in, so the constraint costs exactly the one slot it names."""
    wanted = [memory_id for memory_id in rest if (memory_id in stale_ids) is stale_last]
    if not wanted:
        raise ValueError(
            f"{key}: the deal puts a {'stale' if stale_last else 'non-stale'} member on the "
            f"last slot and the group has none among {list(rest)}"
        )
    chosen = wanted[0]
    return [*(memory_id for memory_id in rest if memory_id != chosen), chosen]


def _shuffle_key(mint_seed: str, group_name: str) -> str:
    """A keyed, published-data-independent order over a stratum's groups."""
    return hmac.new(
        mint_seed.encode("utf-8"), group_name.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def slot_order(
    pool_ids: Sequence[str],
    *,
    record_id: str,
    groups: Mapping[str, Sequence[str]],
    gold_ids: Sequence[str],
    stale_ids: Sequence[str],
    deal: ClockDeal,
    rng: random.Random,
) -> list[str]:
    """The pool in close order: a uniform interleaving of the groups, each group
    internally arranged to the deal.

    The interleaving is the whole-pool shuffle the exporter has always done, and it is
    what the whole-pool guards are scored against. Re-seating a group's members among
    the slots that same shuffle gave the group changes nothing about which slots the
    group owns, so the deal buys the per-group balance without moving any whole-pool
    statistic off its null."""
    order = list(pool_ids)
    if len(order) != len(set(order)):
        raise ValueError(f"{record_id}: the pool holds a candidate twice")
    rng.shuffle(order)
    position = {memory_id: index for index, memory_id in enumerate(order)}

    gold = frozenset(gold_ids)
    stale = frozenset(stale_ids)
    dealt: set[str] = set()
    for subject, members in sorted(groups.items()):
        held = sorted(set(members) & gold)
        if len(held) != 1:
            raise ValueError(
                f"{record_id}/{subject!r}: {len(held)} gold in one subject group; the "
                "per-group clock is only neutral when each graded subject publishes "
                "exactly one current value"
            )
        slots = sorted(position[memory_id] for memory_id in members)
        arranged = deal.arrange(
            group_key(record_id, subject),
            list(members),
            gold_id=held[0],
            stale_ids=stale,
            rng=rng,
        )
        for slot, memory_id in zip(slots, arranged, strict=True):
            order[slot] = memory_id
        dealt.update(members)

    if dealt != set(order):
        raise ValueError(
            f"{record_id}: {len(set(order) - dealt)} pool candidate(s) belong to no "
            "subject group, so their slots were never dealt"
        )
    return order


def shapes_of(
    record_id: str,
    tier: str,
    groups: Mapping[str, Sequence[str]],
    stale_ids: Sequence[str],
) -> list[GroupShape]:
    """One ``GroupShape`` per subject group, for the corpus-wide deal."""
    stale = frozenset(stale_ids)
    return [
        GroupShape(
            record_id=record_id,
            tier=tier,
            subject=subject,
            size=len(members),
            n_stale=len(set(members) & stale),
        )
        for subject, members in sorted(groups.items())
    ]
