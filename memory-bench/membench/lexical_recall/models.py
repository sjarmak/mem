"""Frozen types for the lexical-miss recall experiment (mem-lbuvd).

This experiment measures candidate-generation recall, which the ordering
experiment cannot: `beads_ordering.runner.validate_rank_truth` requires the
literal candidate set to EQUAL the labelled set, so the primary Memory is
recalled with probability 1 by construction there.

A `MissKind` names *why* the literal matcher fails, so recall can be reported per
capability rather than as one undifferentiated average.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from membench.beads_ordering.models import MemoryFixture


class MissKind(StrEnum):
    SYNONYM = "synonym"
    ABBREVIATION = "abbreviation"
    RENAMED_CONCEPT = "renamed-concept"
    MORPHOLOGICAL = "morphological"


class TaskClass(StrEnum):
    LEXICAL_MISS = "lexical-miss"
    LEXICAL_HIT_CONTROL = "lexical-hit-control"


class Generator(StrEnum):
    LITERAL = "literal"
    FTS = "fts"
    EMBEDDING = "embedding"


class LexicalMissTask(BaseModel):
    """One task whose gold Memory does NOT literally contain the query.

    Two construction invariants define the class and are enforced mechanically at
    freeze time by `corpus.validate_miss_construction`, never by a model:

    - `query` is absent from the primary and every acceptable entry point;
    - `query` is present in at least one distractor, so the literal matcher
      returns a non-empty *wrong* candidate set rather than nothing. Without that
      second condition recall would be 0 for a degenerate reason.
    """

    model_config = ConfigDict(frozen=True)

    task_id: str
    miss_kind: MissKind
    query: str
    surface_form: str
    primary_relevant: str
    acceptable_entry_points: tuple[str, ...] = ()
    distractors: tuple[str, ...]
    literal_matching_distractors: tuple[str, ...]


class FrozenMissCorpus(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    seed: int
    memories: tuple[MemoryFixture, ...]
    tasks: tuple[LexicalMissTask, ...]


class CandidateSet(BaseModel):
    """What one generator returned for one task, before any budget is applied."""

    model_config = ConfigDict(frozen=True)

    generator: Generator
    task_id: str
    ranked_ids: tuple[str, ...]
    elapsed_ms: float = Field(ge=0)
    repeat: int = Field(default=0, ge=0)


class TaskRecall(BaseModel):
    """Recall of one task under one generator, at every preregistered budget.

    `matched_k` is the size of the LITERAL candidate set for this same task.
    Recall without a size control is gameable: a generator returning the whole
    corpus scores 1.0. Holding the budget at what the shipped matcher already
    costs is what makes the arms comparable.
    """

    model_config = ConfigDict(frozen=True)

    task_id: str
    task_class: TaskClass
    miss_kind: MissKind | None
    generator: Generator
    matched_k: int = Field(ge=0)
    candidate_set_size: int = Field(ge=0)
    primary_rank: int | None = None
    useful_rank: int | None = None
    primary_at_matched_k: bool
    primary_at_10: bool
    primary_unbounded: bool
    useful_at_matched_k: bool
