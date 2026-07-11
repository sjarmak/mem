"""WorkRecord — the Python-side mirror of the TS contract (`src/schemas/workrecord.ts`,
`src/schemas/trace.ts`). Structural validation only (ZFC-safe): every constraint here
(required/optional, string patterns, numeric bounds) exists in the TS zod schema too —
this module changes WHEN that one changes, never independently.

Introduced at the seam where WorkRecord JSON enters python-land
(`membench.corpus.load_corpus` / `load_query_work`), so malformed records fail loudly
here instead of surfacing as scattered `KeyError`s downstream.

Every model sets `extra="allow"` (pydantic does not inherit this from a parent model —
each nested model needs its own), so a field added to the TS contract before this
mirror catches up round-trips instead of being dropped.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

FullCommitSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
ShortOrFullCommitSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{7,40}$")]

RepoSource = Literal["outcome", "rig-map", "unmapped"]


class StatusHistoryEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    at: str


class Lifecycle(BaseModel):
    """Bead lifecycle. `status` stays a plain string, mirroring the TS side."""

    model_config = ConfigDict(extra="allow")

    created: str
    started: str | None = None
    closed: str | None = None
    status: str = Field(min_length=1)
    status_history: list[StatusHistoryEntry] = Field(default_factory=list)


class AgentRef(BaseModel):
    """An agent/session that worked the bead."""

    model_config = ConfigDict(extra="allow")

    agent_id: str = Field(min_length=1)
    role: str | None = None
    account: str | None = None
    trace_ref: str | None = None
    sequence: int | None = Field(default=None, gt=0)
    started_at: str | None = None
    ended_at: str | None = None
    sources: list[str] | None = None
    suspect: bool | None = None


class TraceError(BaseModel):
    """A single build/test/lint error with file:line provenance."""

    model_config = ConfigDict(extra="allow")

    tool: str
    severity: Literal["error", "warning", "info"]
    message: str
    file: str
    line: int
    column: int | None = None


class Execution(BaseModel):
    """One tool execution (build/test/lint run) and its outcome."""

    model_config = ConfigDict(extra="allow")

    runner: str
    command: str
    status: Literal["pass", "fail"]
    errors: list[TraceError]


class TraceRun(BaseModel):
    """Run-level metadata for one session transcript."""

    model_config = ConfigDict(extra="allow")

    session_uuid: str = Field(min_length=1)
    model: str | None = None
    harness_version: str | None = None
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cache_creation_tokens: int = Field(ge=0)
    cache_read_tokens: int = Field(ge=0)
    n_tool_calls: int = Field(ge=0)
    tool_calls_by_type: dict[str, int] = Field(default_factory=dict)
    n_turns: int = Field(ge=0)
    started_at: str | None = None
    ended_at: str | None = None
    outcome: str | None = None


class PrLink(BaseModel):
    """A `pr-link` transcript entry — the explicit transcript→GitHub PR bridge."""

    model_config = ConfigDict(extra="allow")

    session_uuid: str = Field(min_length=1)
    pr_number: int = Field(gt=0)
    pr_url: str = Field(min_length=1)
    pr_repository: str = Field(min_length=1)
    timestamp: str | None = None


class TraceRef(BaseModel):
    """Trace pointer plus parsed signal. Parsed fields stay absent (not defaulted)
    until parsing runs, so "not yet parsed" is distinguishable from "parsed, found
    nothing"."""

    model_config = ConfigDict(extra="allow")

    jsonl_path: str = Field(min_length=1)
    n_turns: int | None = None
    tool_outcomes: list[Execution] | None = None
    errors: list[TraceError] | None = None
    run: TraceRun | None = None
    pr_links: list[PrLink] | None = None


class Outcome(BaseModel):
    """The verifiable outcome label — what makes this a benchmark, not a log."""

    model_config = ConfigDict(extra="allow")

    pr: str | None = None
    repo: str | None = None
    pr_state: Literal["merged", "closed"] | None = None
    commit_sha: str | None = None
    base_commit: str | None = None
    ci: Literal["pass", "fail"] | None = None


class Provenance(BaseModel):
    """Locally-derived environment baseline (git-provenance ingest)."""

    model_config = ConfigDict(extra="allow")

    work_dir: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    base_branch: str | None = Field(default=None, min_length=1)
    base_commit: FullCommitSha | None = None
    history_state: Literal["commit-by-date", "unresolved", "recorded"]
    work_dir_source: Literal["metadata", "rig-map"] | None = None
    base_branch_source: Literal["metadata", "default"] | None = None


class Landed(BaseModel):
    """Locally-derived OUTCOME for the direct-to-main majority (ingest/landed)."""

    model_config = ConfigDict(extra="allow")

    base_commit: FullCommitSha
    landed_commit: FullCommitSha | None = None
    n_commits: int | None = Field(default=None, ge=0)
    landed_state: Literal[
        "landed",
        "reverted",
        "abandoned",
        "empty-window",
        "ambiguous-window",
        "unresolved",
    ]


class Signal(BaseModel):
    """Extracted memory signal. Shapes are open until P1.6+/Phase 2 settle them."""

    model_config = ConfigDict(extra="allow")

    deterministic: dict[str, Any] = Field(default_factory=dict)
    semantic: dict[str, Any] = Field(default_factory=dict)


class Links(BaseModel):
    """Graph edges to other work, populated by the beads ingest."""

    model_config = ConfigDict(extra="allow")

    deps: list[str] = Field(default_factory=list)
    convoy_id: str | None = None
    supersedes: list[str] = Field(default_factory=list)
    parent: str | None = None


class SessionCommits(BaseModel):
    """Each session's OWN local commit SHAs, recovered from its trace at ingest."""

    model_config = ConfigDict(extra="allow")

    commits: list[ShortOrFullCommitSha] = Field(min_length=1)
    first_commit: ShortOrFullCommitSha
    true_base: FullCommitSha | None = None
    base_state: Literal["resolved", "commit-absent"]


class WorkRecord(BaseModel):
    """The atomic unit of the work-audit graph (ARCHITECTURE.md, "Data model").
    Mirrors `src/schemas/workrecord.ts` `WorkRecordSchema` field-for-field."""

    model_config = ConfigDict(extra="allow")

    work_id: str = Field(min_length=1)
    rig: str = Field(min_length=1)
    title: str
    task_type: str | None = None
    task_type_source: Literal["formula", "structural", "model"] | None = None
    labels: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    priority: int | None = None
    external_ref: str | None = None
    repo: str | None = Field(default=None, min_length=1)
    repo_source: RepoSource | None = None
    lifecycle: Lifecycle
    agents: list[AgentRef] = Field(default_factory=list)
    trace: TraceRef | None = None
    outcome: Outcome | None = None
    provenance: Provenance | None = None
    landed: Landed | None = None
    session_commits: SessionCommits | None = None
    signal: Signal | None = None
    links: Links = Field(default_factory=lambda: Links(deps=[], supersedes=[]))
