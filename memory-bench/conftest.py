"""Ensures the membench package (memory-bench/) is importable during tests."""

import pytest


@pytest.fixture(autouse=True)
def _scrub_ambient_anthropic_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the suite hermetic against a developer's exported ``ANTHROPIC_API_KEY``.

    The paid-run gate (mem-9bh93, ``headless_agent.a_paid_run_carries_the_metered_api_key``) refuses
    a non-dry run when this key is set. Any test that drives a paid entrypoint with an ambient key
    present would trip the gate and fail — a red suite in the exact dev shell the gate exists to
    protect, and invisible to CI's clean env. Clearing it here makes the ambient value irrelevant; a
    test that wants the key SET still sets it explicitly (``monkeypatch.setenv`` in the test body
    runs after this fixture, on the same ``monkeypatch``, so it wins)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
