"""§11 pilot filter — admit a synthetic task only if oracle beats no-memory.

The gate is pure arithmetic over a pilot run's oracle/no-memory rewards, using the
same ``EPSILON`` the §4 task-validity interpretation uses (DIV-3).
"""

from __future__ import annotations

from membench.generators.pilot_filter import pilot_filter
from membench.report.comparison import EPSILON


def test_accepts_when_oracle_clearly_beats_no_memory() -> None:
    v = pilot_filter(oracle_reward=1.0, no_memory_reward=0.0)
    assert v.accepted is True
    assert v.delta == 1.0


def test_rejects_when_oracle_ties_no_memory() -> None:
    # A non-discriminating task measures nothing about memory — reject it.
    v = pilot_filter(oracle_reward=0.6, no_memory_reward=0.6)
    assert v.accepted is False
    assert v.delta == 0.0
    assert "discriminate" in v.reason


def test_rejects_when_delta_within_epsilon() -> None:
    v = pilot_filter(oracle_reward=0.52, no_memory_reward=0.50)  # delta 0.02 <= 0.05
    assert v.accepted is False


def test_boundary_at_epsilon_is_rejected_strictly() -> None:
    # delta == epsilon does not count as beating (strict >).
    v = pilot_filter(oracle_reward=EPSILON, no_memory_reward=0.0, epsilon=EPSILON)
    assert v.accepted is False
    # Just past the boundary is admitted.
    assert pilot_filter(oracle_reward=EPSILON + 1e-6, no_memory_reward=0.0).accepted is True


def test_default_epsilon_is_the_shared_div3_tolerance() -> None:
    # The gate must agree with report.comparison on what "beats" means.
    v = pilot_filter(oracle_reward=1.0, no_memory_reward=0.0)
    assert v.epsilon == EPSILON


def test_custom_epsilon_is_honored() -> None:
    # A stricter gate can reject a delta the default would admit.
    assert pilot_filter(oracle_reward=0.2, no_memory_reward=0.0).accepted is True
    assert pilot_filter(oracle_reward=0.2, no_memory_reward=0.0, epsilon=0.5).accepted is False
