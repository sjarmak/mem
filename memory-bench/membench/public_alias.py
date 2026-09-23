"""Published memory ids that cannot be reversed into roles (mem-r6yzk B2).

``generators.opaque_ids.opaque_memory_id`` hashes ``(namespace, label)``. That hides
the label from a READER but not from a SEARCHER. The namespace is a sequence id or a
world id, both published verbatim in a record's provenance, and the label vocabulary is
small, fixed and readable in the generator: ``<key>``, ``<key>-v1``…``<key>-v3``,
``<key>-distractor``, ``charter``. Recomputing the hash for every published namespace
crossed with every candidate label, and matching the result against the published id
space, therefore recovers the ROLE of every published id — which candidate is the gold
fact, which is the distractor, which is the stale version. Run against the first export
of the public corpus that attack recovered 1000 of 1000 ids. The candidate pool was
telling a solver the answer.

A published id is therefore an ALIAS minted here: ``k-`` followed by 16 hex characters
of an HMAC over the internal id, keyed by a mint seed. The alias is not a function of
the label, of the namespace, or of anything else an attacker can enumerate, so the same
brute force recovers nothing. Two properties follow from using a keyed hash rather than
a draw from a seeded RNG: an alias depends only on its own internal id, so adding a
sequence to a corpus never moves an existing id's alias, and re-minting the same corpus
under the same seed is byte-identical.

WHAT IS SECRET, stated plainly. The scheme's secrecy rests on this repository being
private. The alias map and ``mint_seed`` are committed here; anyone with repository
access can de-anonymize every published id and reconstruct the distractor interleave.
Publishing this repository, or leaking ``memory-bench/fixtures/mint/``, voids the
corpus's id-anonymity and the corpus must then be re-minted with a seed held outside the
repository.

The map is the whole inverse, so a holder can label every published candidate as the
gold fact, a distractor or a stale version. It lives under ``fixtures/mint/`` — inside
this repo, never in the published tree — and the corpus ships without it. The seed sits
beside the map in the same private file, because a holder of the map already has
everything the seed would give them, and writing it down is what makes a corpus
re-mintable byte-for-byte instead of depending on someone remembering a number.

``alias()`` and ``internal()`` fail closed. An id the mint does not cover raises rather
than passing through, because a pass-through would publish exactly the reversible id
this module exists to remove, and it would do it silently.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from membench.schemas.sequence import BenchmarkSequence

# Bumped when the mint file's shape or the alias derivation changes. A reader that
# finds a different version refuses the file rather than guessing at its layout.
ALIAS_SCHEMA_VERSION = "public-mint.v1"

# What a PUBLISHED id looks like. Deliberately a different prefix from
# ``opaque_ids.OPAQUE_ID_PATTERN`` so a raw internal id leaking into a published record
# is visible on sight, in a test and in review.
PUBLIC_ID_PATTERN = re.compile(r"^k-[0-9a-f]{16}$")

# Where mint files live: inside the private benchmark repo, beside the corpora they
# cover, and never under the published tree. ``test_public_alias`` asserts the second
# half of that sentence rather than trusting it.
MINT_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "mint"
MINT_SUFFIX = ".mint.json"


class UnknownInternalIdError(KeyError):
    """An internal id the mint does not cover was asked to be published."""


class UnknownAliasError(KeyError):
    """A published alias was asked to be resolved and the mint does not hold it."""


def mint_path(corpus: str) -> Path:
    """The private mint file for ``corpus`` (a corpus DIR NAME, e.g. ``worlds-public-v1``)."""
    if not corpus:
        raise ValueError("a mint file needs a corpus name")
    return MINT_DIR / f"{corpus}{MINT_SUFFIX}"


def _alias_for(corpus: str, mint_seed: str, internal_id: str) -> str:
    """The published alias for one internal id. Keyed by ``mint_seed``; the corpus name
    is mixed in so the same internal id in two corpora does not publish as one id."""
    digest = hmac.new(
        mint_seed.encode("utf-8"),
        f"{corpus}/{internal_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"k-{digest[:16]}"


@dataclass(frozen=True)
class AliasMap:
    """The private inverse of a corpus's published id space.

    ``aliases`` is the serialized direction, alias → internal id, because that is the
    direction a holder of a published record needs. The internal → alias direction is
    derived once at construction; both are bijective, which ``__post_init__`` checks
    rather than assumes.
    """

    corpus: str
    mint_seed: str
    aliases: Mapping[str, str]
    _by_internal: Mapping[str, str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.corpus:
            raise ValueError("an alias map needs a corpus name")
        if not self.mint_seed:
            raise ValueError("an alias map needs a mint seed")
        if not self.aliases:
            raise ValueError(f"the alias map for {self.corpus!r} is empty")
        malformed = sorted(a for a in self.aliases if not PUBLIC_ID_PATTERN.match(a))
        if malformed:
            raise ValueError(
                f"alias map for {self.corpus!r} holds ids that are not published ids: "
                f"{malformed[:5]}"
            )
        by_internal: dict[str, str] = {}
        for alias, internal_id in self.aliases.items():
            if not internal_id:
                raise ValueError(f"alias {alias!r} in {self.corpus!r} maps to an empty id")
            clash = by_internal.get(internal_id)
            if clash is not None:
                raise ValueError(
                    f"alias map for {self.corpus!r} is not a bijection: internal id "
                    f"{internal_id!r} is published as both {clash!r} and {alias!r}"
                )
            by_internal[internal_id] = alias
        object.__setattr__(self, "_by_internal", by_internal)

    def __len__(self) -> int:
        return len(self.aliases)

    @property
    def internal_ids(self) -> tuple[str, ...]:
        """Every id the mint covers, sorted."""
        return tuple(sorted(self._by_internal))

    @property
    def public_ids(self) -> tuple[str, ...]:
        """Every id the mint publishes, sorted."""
        return tuple(sorted(self.aliases))

    def alias(self, internal_id: str) -> str:
        """The published id for ``internal_id``. Raises rather than passing an unminted
        id through: the pass-through IS the leak this module removes."""
        published = self._by_internal.get(internal_id)
        if published is None:
            raise UnknownInternalIdError(
                f"{internal_id!r} is not minted for corpus {self.corpus!r}; re-run "
                f"scripts/mint_public_ids.py before publishing it"
            )
        return published

    def internal(self, alias: str) -> str:
        """The internal id behind a published one. Harness side only."""
        internal_id = self.aliases.get(alias)
        if internal_id is None:
            raise UnknownAliasError(f"{alias!r} is not an alias of corpus {self.corpus!r}")
        return internal_id


def build_alias_map(
    internal_ids: Iterable[str],
    *,
    corpus: str,
    mint_seed: str,
) -> AliasMap:
    """Mint one alias per distinct internal id.

    Deterministic in ``(corpus, mint_seed, id)`` alone, so the order ids arrive in does
    not matter and re-minting is byte-identical. A 64-bit collision raises: it means
    this corpus cannot be published under this seed, and silently dropping one of the
    two ids would publish a record whose candidate pool is short an entry.
    """
    if not mint_seed:
        raise ValueError("minting public ids requires a mint seed; there is no default")
    ids = sorted(set(internal_ids))
    if not ids:
        raise ValueError(f"cannot mint an alias map for {corpus!r} over an empty id set")
    aliases: dict[str, str] = {}
    for internal_id in ids:
        if not internal_id:
            raise ValueError(f"cannot mint an alias for an empty id in {corpus!r}")
        alias = _alias_for(corpus, mint_seed, internal_id)
        clash = aliases.get(alias)
        if clash is not None:
            raise ValueError(
                f"alias collision in {corpus!r}: {clash!r} and {internal_id!r} both mint "
                f"as {alias!r}; re-mint the corpus under a different seed"
            )
        aliases[alias] = internal_id
    return AliasMap(corpus=corpus, mint_seed=mint_seed, aliases=aliases)


def write_alias_map(amap: AliasMap, *, path: str | Path | None = None) -> Path:
    """Write the private mint file (default ``fixtures/mint/<corpus>.mint.json``).

    Sorted by alias, so a re-mint is diffable and a reviewer reading the file cannot
    read publication ORDER as a hint about roles."""
    out = Path(path) if path is not None else mint_path(amap.corpus)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "schema_version": ALIAS_SCHEMA_VERSION,
        "corpus": amap.corpus,
        "mint_seed": amap.mint_seed,
        "n_aliases": len(amap),
        "aliases": {alias: amap.aliases[alias] for alias in sorted(amap.aliases)},
    }
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return out


def read_alias_map(path: str | Path) -> AliasMap:
    """Load a mint file and check it still reproduces from its own corpus and seed.

    The re-derivation is the point: a hand-edited mapping would resolve published ids
    to the wrong internal ids, and every downstream check would agree with it."""
    src = Path(path)
    raw = json.loads(src.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{src} is not a mint object")
    version = raw.get("schema_version")
    if version != ALIAS_SCHEMA_VERSION:
        raise ValueError(f"{src} claims schema {version!r}, expected {ALIAS_SCHEMA_VERSION!r}")
    missing = [key for key in ("corpus", "mint_seed", "aliases") if key not in raw]
    if missing:
        raise ValueError(f"{src} is missing mint field(s) {missing}")
    aliases = raw["aliases"]
    if not isinstance(aliases, dict):
        raise ValueError(f"{src} holds no alias mapping")
    amap = AliasMap(
        corpus=str(raw["corpus"]),
        mint_seed=str(raw["mint_seed"]),
        aliases={str(alias): str(internal) for alias, internal in aliases.items()},
    )
    reminted = build_alias_map(amap.aliases.values(), corpus=amap.corpus, mint_seed=amap.mint_seed)
    if dict(reminted.aliases) != dict(amap.aliases):
        raise ValueError(
            f"{src} does not reproduce from its own corpus and seed; the mapping has "
            f"been edited by hand and cannot be trusted to resolve published ids"
        )
    return amap


def load_alias_map(corpus: str) -> AliasMap:
    """Load ``corpus``'s mint from its default private location."""
    path = mint_path(corpus)
    if not path.is_file():
        raise FileNotFoundError(
            f"no mint file for corpus {corpus!r} at {path}; run "
            f"scripts/mint_public_ids.py --corpus <dir> --mint-seed <seed> first"
        )
    return read_alias_map(path)


def published_internal_ids(sequence: BenchmarkSequence) -> tuple[str, ...]:
    """Every id of ``sequence`` that can reach a published record, sorted.

    A superset by construction — step ids, written ids, read ids, distractors,
    superseded ids, the ids outcome checks and memory probes name — because an id the
    mint misses is an id the publisher must either drop or leak. Collecting the whole
    surface costs nothing and removes the judgement call."""
    ids: set[str] = set()
    for step in sequence.steps:
        ids.add(step.step_id)
        ids.update(step.expected_memory_writes)
        ids.update(step.expected_memory_reads)
        ids.update(step.distractor_memories)
        ids.update(step.superseded_memory_ids)
        ids.update(step.forbidden_memory_writes)
        for check in step.outcome_checks:
            ids.update(check.requires_memory)
        for probe in step.memory_probes:
            ids.add(probe.expected_memory_id)
    return tuple(sorted(ids))


__all__ = [
    "ALIAS_SCHEMA_VERSION",
    "MINT_DIR",
    "MINT_SUFFIX",
    "PUBLIC_ID_PATTERN",
    "AliasMap",
    "UnknownAliasError",
    "UnknownInternalIdError",
    "build_alias_map",
    "load_alias_map",
    "mint_path",
    "published_internal_ids",
    "read_alias_map",
    "write_alias_map",
]
