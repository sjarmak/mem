"""Shared, cwd-independent paths for tests."""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURE = str(REPO / "fixtures" / "sequences" / "gascity_backend_conventions.json")
