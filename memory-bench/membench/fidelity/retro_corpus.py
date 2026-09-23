"""The labelled retro-validation set: 7 real memory writes with known verdicts.

The bd reliability campaign already ran the experiment this gate needs. Across 4
batches, 8 opportunity-capture sessions were given the same 2-artifact evidence
packet (one sha256 pair, verified identical in all 8); 7 of them persisted a
record; independent reviewers, working from sealed evidence and no provider call,
labelled exactly 1 supported and 6 unsupported, and named the offending claim in
each. That is a ready-made ground truth, and it costs nothing to reuse.

`build` lifts those records out of the campaign's session artifacts into a
committed fixture. `load` reads the fixture. The campaign results tree is not in
version control, so `build` runs where it exists and `load` runs anywhere.

Read the labels for what they are. Seven records, one episode, one claim shape.
Clearing this set qualifies the gate for a live trial; it does not measure
production reliability, and nothing here is a substitute for running the gate
against writes it has not already seen.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from membench.fidelity.packet import EvidencePacket

# The evidence artifacts. AGENTS.md and CLAUDE.md were also in the session
# workspace but they are instructions to the agent, not evidence about the world,
# and no reviewer treated them as support.
EVIDENCE_ARTIFACTS = ("PRIOR_TASK.md", "PRIOR_SOURCE_PACKET.md")

# Session directory for each record, relative to a campaign results root.
_SESSIONS = {
    "dev01-explicit": "development-01/capture-opportunity-explicit",
    "dev02-current": "development-02/capture-opportunity-current",
    "dev02-explicit": "development-02/capture-opportunity-explicit",
    "dev03-current": "development-03/capture-opportunity-current",
    "dev03-explicit": "development-03/capture-opportunity-explicit",
    "conf-r1": "confirmation-01/capture-opportunity-r1",
    "conf-r2": "confirmation-01/capture-opportunity-r2",
}


@dataclass(frozen=True)
class _FaultSpec:
    """A fault definition: one anchored single-edit mutation and its expected verdict."""

    fault_id: str
    base: str
    expected_accept: bool
    why: str
    find: str
    replace: str


# Two single-edit mutations of real records, one in each direction. Without them a
# gate that rejects everything scores 6 of 7 on the base set and reads as working.
# The edits are defined here rather than in a test so they are version-controlled,
# applied mechanically, and fail loudly if the base text ever changes under them.
_FAULTS = (
    _FaultSpec(
        fault_id="dev03-explicit__version-history-injected",
        base="dev03-explicit",
        expected_accept=False,
        why=(
            "The one accepted record, with a single version-history sentence added. "
            "A gate that still accepts it is not reading claims, only records."
        ),
        find=(
            "- Exact patch text absent; GREEN run is consistent with fix applied "
            "but does not prove the exact change"
        ),
        replace=(
            "- Exact patch text absent; GREEN run is consistent with fix applied "
            "but does not prove the exact change\n"
            "- The int-to-str digit guard was introduced in CPython 3.11; earlier "
            "versions impose no limit."
        ),
    ),
    _FaultSpec(
        fault_id="dev01-explicit__version-claims-stripped",
        base="dev01-explicit",
        expected_accept=True,
        why=(
            "A rejected record with exactly the two sentences the reviewers named "
            "removed, the rest untouched. A gate that still rejects it is rejecting "
            "for something other than the reviewers' reason."
        ),
        find=(
            " The CVE-2020-10735 guard (default_max_str_digits=4300) applies to "
            "CPython >=3.11. RecursionError from deep nesting is general CPython "
            "behavior."
        ),
        replace="",
    ),
)


@dataclass(frozen=True)
class FaultRecord:
    """A deliberately mutated record with the verdict the mutation should force."""

    fault_id: str
    base: str
    text: str
    expected_accept: bool
    why: str


@dataclass(frozen=True)
class LabelledRecord:
    """One persisted memory write with the independent reviewers' verdict.

    ``named_unsupported_claims`` holds the claims a reviewer actually wrote down.
    ``discriminating_literals`` are the tokens that make one of those claims
    unsupported (a version number the packet never establishes, say). They let a
    test check that a gate rejected a record *for the reviewers' reason* rather
    than for some other reason, without asking two free-text sentences to match.
    """

    record_id: str
    session: str
    memory_key: str
    text: str
    supported: bool
    named_unsupported_claims: tuple[str, ...]
    discriminating_literals: tuple[str, ...]
    label_source: str


@dataclass(frozen=True)
class RetroCorpus:
    """The fixture: one shared packet plus the labelled records."""

    packet: EvidencePacket
    records: tuple[LabelledRecord, ...]
    faults: tuple[FaultRecord, ...] = ()

    def by_id(self, record_id: str) -> LabelledRecord:
        for record in self.records:
            if record.record_id == record_id:
                return record
        raise KeyError(f"no record {record_id!r} in the retro corpus")


def load(fixture_dir: Path) -> RetroCorpus:
    """Read the committed fixture, verifying the packet against its digests."""
    meta = json.loads((fixture_dir / "corpus.json").read_text())
    packet = EvidencePacket.from_files(
        paths={name: fixture_dir / "packet" / name for name in meta["packet"]},
        expected=meta["packet"],
    )
    records = tuple(
        LabelledRecord(
            record_id=row["record_id"],
            session=row["session"],
            memory_key=row["memory_key"],
            text=(fixture_dir / "records" / f"{row['record_id']}.txt").read_text(),
            supported=row["supported"],
            named_unsupported_claims=tuple(row["named_unsupported_claims"]),
            discriminating_literals=tuple(row["discriminating_literals"]),
            label_source=row["label_source"],
        )
        for row in meta["records"]
    )
    faults = tuple(
        FaultRecord(
            fault_id=row["fault_id"],
            base=row["base"],
            text=(fixture_dir / "records" / f"{row['fault_id']}.txt").read_text(),
            expected_accept=row["expected_accept"],
            why=row["why"],
        )
        for row in meta.get("faults", ())
    )
    return RetroCorpus(packet=packet, records=records, faults=faults)


def build(campaign_root: Path, fixture_dir: Path, labels: Path) -> None:
    """Extract packet, records and labels from a campaign results tree.

    The record text is read from the accepted `bd remember` invocation recorded in
    each session's component-result, which is the exact string that reached the
    store. Every session's packet digests must agree, or the records would not be
    comparable and the build fails rather than picking one.
    """
    label_rows = {row["record_id"]: row for row in json.loads(labels.read_text())}
    (fixture_dir / "packet").mkdir(parents=True, exist_ok=True)
    (fixture_dir / "records").mkdir(parents=True, exist_ok=True)

    digests: dict[str, str] = {}
    rows: list[dict[str, object]] = []
    for record_id, session in _SESSIONS.items():
        batch = campaign_root / "ledger/batches" / _batch(session)
        run = batch / "run/sessions" / _row(session) / "run"
        session_digests = {
            name: digest
            for name, digest in json.loads((run / "leg-0-input-files.json").read_text()).items()
            if name in EVIDENCE_ARTIFACTS
        }
        if digests and session_digests != digests:
            raise RuntimeError(
                f"{session} was given a different evidence packet than earlier sessions; "
                "these records are not comparable"
            )
        digests = session_digests
        key, text = _accepted_write(run)
        (fixture_dir / "records" / f"{record_id}.txt").write_text(text)
        label = label_rows[record_id]
        rows.append(
            {
                "record_id": record_id,
                "session": session,
                "memory_key": key,
                "supported": label["supported"],
                "named_unsupported_claims": label["named_unsupported_claims"],
                "discriminating_literals": label["discriminating_literals"],
                "label_source": label["label_source"],
            }
        )
    _extract_packet(campaign_root, fixture_dir, digests)
    faults = _write_faults(fixture_dir)
    (fixture_dir / "corpus.json").write_text(
        json.dumps({"packet": digests, "records": rows, "faults": faults}, indent=1) + "\n"
    )


def _write_faults(fixture_dir: Path) -> list[dict[str, object]]:
    """Apply each fault edit to its base record, refusing a no-op edit.

    A mutation that silently fails to apply produces a fault test that passes
    because it is testing the unmutated record, which is the exact failure this
    whole fixture exists to rule out.
    """
    written: list[dict[str, object]] = []
    for fault in _FAULTS:
        base_text = (fixture_dir / "records" / f"{fault.base}.txt").read_text()
        if fault.find not in base_text:
            raise RuntimeError(
                f"fault {fault.fault_id} does not apply: its anchor is absent from "
                f"{fault.base}; the base record changed under the mutation"
            )
        mutated = base_text.replace(fault.find, fault.replace, 1)
        if mutated == base_text:
            raise RuntimeError(f"fault {fault.fault_id} is a no-op edit")
        (fixture_dir / "records" / f"{fault.fault_id}.txt").write_text(mutated)
        written.append(
            {
                "fault_id": fault.fault_id,
                "base": fault.base,
                "expected_accept": fault.expected_accept,
                "why": fault.why,
            }
        )
    return written


def _batch(session: str) -> str:
    return session.split("/", 1)[0]


def _row(session: str) -> str:
    return session.split("/", 1)[1]


def _accepted_write(run: Path) -> tuple[str, str]:
    """The key and text of the single accepted `bd remember` in this session."""
    result = json.loads((run / "component-result.json").read_text())
    writes = [op for op in result["agent"]["memory"]["operations"] if op.get("accepted_write")]
    if len(writes) != 1:
        raise RuntimeError(f"{run} has {len(writes)} accepted writes, expected exactly 1")
    argv = writes[0]["argv"]
    return argv[argv.index("--key") + 1], argv[1]


def _extract_packet(campaign_root: Path, fixture_dir: Path, digests: dict[str, str]) -> None:
    """Copy the evidence artifacts out of one session's sealed input tar."""
    import tarfile

    source = (
        campaign_root
        / "ledger/batches/confirmation-01/run/sessions/capture-opportunity-r1/run/leg-0-input.tar"
    )
    with tarfile.open(source) as archive:
        for name in digests:
            member = archive.extractfile(name)
            if member is None:
                raise RuntimeError(f"{name} missing from {source}")
            (fixture_dir / "packet" / name).write_bytes(member.read())
