"""Synthetic BenchmarkSequence generators (Tier-0 pure-Python oracle authoring).

Per the PRD synthetic-generation tooling decision: WE author the ground truth —
the latent rule, the structural constraints (episode ⊨ rule; rule ∉ any episode) —
in pure Python, deterministically and seed-reproducibly. A local model may LATER
fill only the NL surface text of episodes, run offline into a frozen,
``generator_version``-tagged fixture; CI never calls a model.
"""

from membench.generators.schema_induction import (
    GENERATOR_VERSION,
    generate_schema_induction_sequence,
)

__all__ = ["GENERATOR_VERSION", "generate_schema_induction_sequence"]
