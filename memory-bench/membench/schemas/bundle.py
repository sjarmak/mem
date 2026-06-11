"""Task-bundle schema (mem-75t.7.2, plan §2 + §9.3) -- the grid's eval object.

Mirrors codeprobe ``models/task.py`` (``Task``/``TaskMetadata``/``TaskVerification``)
re-shaped for a bead-sourced, PR-less corpus:

- the **issue** leg is the bead's title/body (leak-guarded at assembly, never here --
  a schema cannot know which strings are outcome labels);
- the **output** leg embeds the P0 `ReplayResult` verbatim (per-file gold diffs +
  classified replay outcomes + ``replay_success_rate``) rather than re-projecting it:
  the replay types ARE the output contract, and re-stating them would let the two
  drift;
- ``oracle_context`` is None until oracle curation (mem-75t.7.3) fills it -- absence
  is "not yet curated", distinguishable from a curated-but-empty oracle;
- ``env`` makes the bundle a self-contained RUNNABLE eval object (plan §9.3): repo +
  base_commit are the checkout anchors, base_image the toolchain -- no out-of-band
  rig map needed downstream;
- ``loo_excluded_work_ids`` is the bundle-level LOO INVARIANT: the record ids any
  grid run must withhold from memory arms (own work + siblings), stored IN the
  bundle so the exclusion is mechanical, not a convention;
- ``verification`` carries the dual sub-score slots (mem-75t.7.5 fills them); both
  legs stay None until scored, so "unscored" never reads as 0.0.

All models are frozen value objects (membench schema idiom). This module depends on
`membench.bundle.replay` for the embedded output types; neither package ``__init__``
imports this module, keeping the import graph acyclic.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from membench.bundle.replay import ReplayResult

# Plan §9.5's scoring-policy vocabulary; "direct" is the current probe-grade leg.
ScoringPolicy = Literal["direct", "min", "mean", "weighted"]


class CuratedOracle(BaseModel):
    """The P2 oracle-context leg (codeprobe ``oracle_answer``/``oracle_tiers``).

    A placeholder TYPE today: mem-75t.7.3 populates it via grep/AST/SG consensus +
    curator. ``oracle_backends_consensus`` is the anti-tautology provenance --
    which backends contributed a kept oracle file."""

    model_config = ConfigDict(frozen=True)

    oracle_answer: tuple[str, ...] = ()
    # file -> tier ("required" | "supplementary" | "context"), codeprobe-shaped.
    oracle_tiers: tuple[tuple[str, str], ...] = ()
    oracle_backends_consensus: tuple[str, ...] = ()


class BundleVerification(BaseModel):
    """Dual-verifier slots (codeprobe ``TaskVerification``, ported weights).

    Both sub-scores are None until mem-75t.7.5 scores a run against the bundle --
    the direct leg (gold-diff / test reproduction) and the comprehension leg
    (artifact F1 vs the oracle file list). None means "not scored", never 0.0."""

    model_config = ConfigDict(frozen=True)

    scoring_policy: ScoringPolicy = "direct"
    weight_direct: float = 0.5
    weight_artifact: float = 0.5
    score_direct: float | None = Field(default=None, ge=0.0, le=1.0)
    score_artifact: float | None = Field(default=None, ge=0.0, le=1.0)


class BundleEnv(BaseModel):
    """The env-recon anchors that make the bundle runnable (plan §9.3): check out
    ``repo`` at ``base_commit``, run the agent in ``base_image``. All required --
    a bundle with a missing anchor is not admissible, so the schema refuses it."""

    model_config = ConfigDict(frozen=True)

    repo: str = Field(min_length=1)
    base_commit: str = Field(min_length=1)
    base_image: str = Field(min_length=1)


class TaskBundle(BaseModel):
    """One evaluable task bundle: ``issue -> trace -> output (+ oracle_context)``."""

    model_config = ConfigDict(frozen=True)

    work_id: str = Field(min_length=1)
    rig: str = Field(min_length=1)
    # The bead-sourced issue leg. Body may legitimately be empty: current ingest
    # carries only the title (see assess._body_text's forward-shaped read).
    issue_title: str
    issue_body: str = ""
    # The resolved transcript path (record.trace.jsonl_path) -- the mined source.
    trace_ref: str = Field(min_length=1)
    # The P0 replay product, embedded whole: gold diffs + outcomes + fidelity rate.
    output: ReplayResult
    oracle_context: CuratedOracle | None = None
    env: BundleEnv
    # Sorted, deduplicated: own work_id + supersedes closure + convoy/pr/branch
    # siblings (validity semantics). Never empty -- self-exclusion is unconditional.
    loo_excluded_work_ids: tuple[str, ...] = Field(min_length=1)
    verification: BundleVerification = BundleVerification()
