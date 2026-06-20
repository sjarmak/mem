"""mem-lvp.27 — the large-N synthetic outcome-lift driver's pure helpers.

The driver composes already-tested eval functions (``synthetic_arms``,
``memory_necessity_gate``); these tests pin the thin glue it adds: a deterministic
authored world, the construct-validity admission accounting, and the held-results
payload shape. The arms eval itself is covered by ``test_synthetic_arms``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from membench.generators import materialize_world
from membench.generators.memory_necessity_gate import memory_necessity_gate
from membench.report.synthetic_arms import eval_arms_over_sequences

_SPEC = importlib.util.spec_from_file_location(
    "eval_synthetic_outcome_lift",
    Path(__file__).resolve().parents[1] / "scripts" / "eval_synthetic_outcome_lift.py",
)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError("could not load scripts/eval_synthetic_outcome_lift.py — wrong cwd?")
mod = importlib.util.module_from_spec(_SPEC)
# Register before exec so the frozen dataclasses can introspect their own module.
sys.modules[_SPEC.name] = mod
_SPEC.loader.exec_module(mod)


def test_authored_world_is_seed_deterministic() -> None:
    w1, p1 = mod.authored_world(0)
    w2, p2 = mod.authored_world(0)
    seqs1 = materialize_world(w1, p1, n_tasks=5, facts_per_task=3)
    seqs2 = materialize_world(w2, p2, n_tasks=5, facts_per_task=3)
    # Byte-reproducible: same seed ⇒ identical task ids and goal requests.
    assert [s.sequence_id for s in seqs1] == [s.sequence_id for s in seqs2]
    assert [s.steps[-1].user_request for s in seqs1] == [s.steps[-1].user_request for s in seqs2]


def test_every_authored_task_clears_the_necessity_gate() -> None:
    # The substrate's whole point: every materialised task is memory-dependent, so the
    # admission rate is 1.0 and the lift denominator is the full batch.
    w, p = mod.authored_world(0)
    seqs = materialize_world(w, p, n_tasks=12, facts_per_task=3)
    adm = mod.gate_admission(seqs)
    assert adm.total == 12
    assert adm.admitted == 12
    assert adm.rate == 1.0
    assert adm.rejected_ids == ()
    # And the gate agrees per-task (no silent disagreement with the batch accounting).
    assert all(memory_necessity_gate(s).verdict.accepted for s in seqs)


def test_results_payload_carries_role_and_lift() -> None:
    w, p = mod.authored_world(0)
    seqs = materialize_world(w, p, n_tasks=4, facts_per_task=3)
    results = eval_arms_over_sequences(seqs, ["none", "oracle", "filesystem"], fs_base_dir=None)
    payload = mod._results_payload("t", results)
    assert payload["title"] == "t"
    by_arm = {a["arm"]: a for a in payload["arms"]}
    # The none/ours/builtin role mapping is explicit in the payload, not a silent relabel.
    assert by_arm["filesystem"]["role"] == "ours (id-exact store)"
    assert by_arm["none"]["lift"] == 0.0  # the control never beats itself
    # Non-degenerate: the oracle ceiling is a real positive pass-rate (the goal step
    # recalls the facts), so the equality below is a meaningful tie, not a 0.0==0.0 pass.
    assert by_arm["oracle"]["oracle_reward"] > 0.0
    # An id-exact arm reaches the oracle ceiling by construction: it LIFTS over none and
    # closes the gap to oracle entirely.
    assert by_arm["filesystem"]["lift"] > 0.0
    assert by_arm["filesystem"]["arm_reward"] == by_arm["oracle"]["oracle_reward"]
    assert by_arm["filesystem"]["oracle_gap"] == 0.0
