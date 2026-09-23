"""Off-episode evidence packets: is the version-claim failure structural? (mem-xj9si)

The retro set is 7 records, one episode, one claim shape. Six of them asserted
Python version history the packet does not establish, and two readings fit that
equally well: background knowledge leaks in whenever a durable record is written,
or the t2dl4 packet invited version reasoning because its own seed discusses
CPython behaviour. The first makes the gate a general intervention; the second
scopes the campaign's capture finding to one packet.

Separating them needs packets from other technologies whose artifacts establish
no version at all. These are five, authored rather than harvested, because no
campaign produced them: a PostgreSQL index build that blocked writes, a Go worker
pool racing on an unguarded map, a reverse proxy timing out an export, a cache
refusing writes under an eviction policy that evicts nothing, and a sweep whose
artifact tree ran a volume out of inodes while two thirds of its blocks were
free. Each carries the same two artifact names as the campaign packet, so the
frozen capture instruction runs over them with one noun changed (see README.md).

The first three were the whole corpus through 2026-09-06, and that run showed
what a corpus with no margin costs: the stop rule needs six pairs including three
token-free, three packets supply exactly six including exactly three, and a
single unusable pair drops the run below its own quorum. The last two exist to be
that margin.

**A packet is a designed instrument, not a sample.** Each manifest declares what
its artifacts establish, what they deliberately do not, and the specific
background claims an agent is likely to reach for -- with, where one exists, the
token that would betray each. `tests/test_offepisode_packets.py` holds the
authoring invariants: no artifact carries a version at all, every betraying token
is genuinely absent, and every literal claimed as supported is genuinely present.
Without those checks "verified by inspection" is an assertion in a bead.

Each packet also carries probe pairs: one record inside the support boundary and
the same record with a single lure sentence added. Running the gate over the two
is what turns "this packet should discriminate" into "it does", locally and
before any provider session is bought.

Each packet carries two, and the second is the one that decides anything. The
`version-token` probe injects a claim carrying a token the packet lacks, which a
one-line string search finds as readily as the gate does; a gate that catches only
those has bought nothing over grep. The `token-free` probe injects the packet's
declared `token_detectable: false` lure, which carries no such token at all, so it
is reachable by the citation check and invisible to any token baseline. Comparing
the two is the stop rule (`membench/fidelity/stop_rule.py`).

One limit, stated: these packets are authored by the same model family that will
be scored against them, so they can only measure intrusion, never establish that
the lures are the ones a different agent would reach for.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from membench.fidelity.packet import EvidencePacket

__all__ = ["Lure", "OffEpisodePacket", "ProbePair", "load_all", "load_packet"]

# The same two names the campaign packet used, so the capture instruction that
# points at them by name needs no edit beyond the one recorded in README.md.
EVIDENCE_ARTIFACTS = ("PRIOR_TASK.md", "PRIOR_SOURCE_PACKET.md")


@dataclass(frozen=True)
class Lure:
    """One background claim an agent might assert, and how it would be caught.

    ``betraying_literals`` are tokens that appear in the claim and in no artifact,
    so a record asserting the claim fails the literal check. ``token_detectable``
    is False when no such token exists: the claim is still scoreable by a reader,
    and by the citation check, but the mechanical check cannot see it. Recording
    which is which is what keeps a per-packet score honest.
    """

    claim: str
    kind: str
    betraying_literals: tuple[str, ...]
    token_detectable: bool
    note: str = ""


@dataclass(frozen=True)
class ProbePair:
    """Two records over one packet, differing by exactly one asserted sentence.

    The packet invariants say a lure *could* be caught. They cannot say the gate
    catches it, because nothing in them runs the gate. This pair does: a record
    that stays inside the support boundary, and the same record with
    ``injected_line`` added. A gate worth spending a provider session behind
    accepts the first and rejects the second. If it rejects both, the packet is
    unusable off-episode and the session buys nothing.

    ``token_detectable`` says which kind of pair this is, and it is the axis the
    stop rule turns on. A token-detectable pair injects a claim carrying a token
    absent from the evidence, so ``betraying_literals`` names what the rejection
    should point at -- and so a plain string search catches it too. A token-free
    pair declares no literals because there are none to declare: nothing in the
    sentence is missing from the evidence as a string, and only a check that asks
    what the record cites can reach it.

    Holding the difference to one line is what makes the verdict readable. Two
    independently written records would differ in a dozen ways and any verdict
    split between them could be attributed to any of those.
    """

    probe_id: str
    supported: str
    lure: str
    injected_line: str
    betraying_literals: tuple[str, ...]
    token_detectable: bool
    reaches_for: str
    recordings_root: Path
    """Where this probe's recordings go, per probe rather than per packet.

    Two probes over one packet each write a `supported.json`, so a shared path
    would have the second silently overwrite the first.
    """


@dataclass(frozen=True)
class OffEpisodePacket:
    """One authored packet: its evidence, its support boundary, and its lures."""

    packet_id: str
    domain: str
    packet: EvidencePacket
    establishes: tuple[str, ...]
    does_not_establish: tuple[str, ...]
    supported_literals: tuple[str, ...]
    lures: tuple[Lure, ...]
    probes: tuple[ProbePair, ...]

    def version_lures(self) -> tuple[Lure, ...]:
        """The narrower category the bead scores separately from all intrusion."""
        return tuple(lure for lure in self.lures if lure.kind == "version_history")

    def probe(self, probe_id: str) -> ProbePair:
        """One probe by id, raising rather than defaulting when it is not there.

        A caller that wants a specific pair wants that pair. Falling back to
        whichever probe happened to be first would run the wrong record and report
        the result under the name of the one that was asked for.
        """
        for probe in self.probes:
            if probe.probe_id == probe_id:
                return probe
        available = ", ".join(probe.probe_id for probe in self.probes)
        raise KeyError(f"{self.packet_id} has no probe {probe_id!r}; it has: {available}")


def load_packet(directory: Path) -> OffEpisodePacket:
    """Read one packet directory: its manifest, and its hash-verified artifacts."""
    manifest = json.loads((directory / "manifest.json").read_text())
    packet = EvidencePacket.from_files(
        {name: directory / "packet" / name for name in EVIDENCE_ARTIFACTS},
        manifest["artifacts"],
    )
    return OffEpisodePacket(
        packet_id=manifest["packet_id"],
        domain=manifest["domain"],
        packet=packet,
        establishes=tuple(manifest["establishes"]),
        does_not_establish=tuple(manifest["does_not_establish"]),
        supported_literals=tuple(manifest["supported_literals"]),
        lures=tuple(
            Lure(
                claim=entry["claim"],
                kind=entry["kind"],
                betraying_literals=tuple(entry["betraying_literals"]),
                token_detectable=entry["token_detectable"],
                note=entry.get("note", ""),
            )
            for entry in manifest["lures"]
        ),
        probes=tuple(_load_probe(directory, entry) for entry in manifest["probes"]),
    )


def _load_probe(directory: Path, entry: dict[str, Any]) -> ProbePair:
    """One probe pair, with both record texts read from disk."""
    supported = directory / str(entry["supported"])
    probe_id = str(entry["probe_id"])
    return ProbePair(
        probe_id=probe_id,
        supported=supported.read_text(),
        lure=(directory / str(entry["lure"])).read_text(),
        injected_line=str(entry["injected_line"]),
        betraying_literals=tuple(str(item) for item in entry["betraying_literals"]),
        token_detectable=bool(entry["token_detectable"]),
        reaches_for=str(entry["reaches_for"]),
        recordings_root=supported.parent / "recordings" / probe_id,
    )


def load_all(root: Path) -> tuple[OffEpisodePacket, ...]:
    """Every packet under ``root``, ordered by id."""
    directories = sorted(child for child in root.iterdir() if (child / "manifest.json").is_file())
    return tuple(load_packet(child) for child in directories)
