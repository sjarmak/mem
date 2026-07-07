#!/usr/bin/env python3
"""Read-only diagnostic: print the wired membench arm roster with contract traits.

Introspects the live registry (membench.memory_systems._systems_registry) so the
table can never drift from the code. Run from anywhere:

    python3 .claude/skills/mem-competitive-arms/scripts/arm-roster.py

Requires only the membench package to be importable (pip install -e
memory-bench/[dev], or run with memory-bench/ on PYTHONPATH). No store, no TS
build, no network, no SDKs: the registry imports lazily-loading modules only.
"""

from __future__ import annotations

import sys
from pathlib import Path

# repo root = parents[4] of this file:
# .claude/skills/mem-competitive-arms/scripts/arm-roster.py
REPO_ROOT = Path(__file__).resolve().parents[4]
MEMORY_BENCH = REPO_ROOT / "memory-bench"


def main() -> int:
    sys.path.insert(0, str(MEMORY_BENCH))
    try:
        from membench.memory_systems import __init__ as _  # noqa: F401
        from membench.memory_systems import _systems_registry  # type: ignore[attr-defined]
    except ImportError as exc:
        print(f"cannot import membench from {MEMORY_BENCH}: {exc}", file=sys.stderr)
        print("fix: cd memory-bench && pip install -e '.[dev]'", file=sys.stderr)
        return 1

    registry = _systems_registry()
    rows = []
    for name in sorted(registry):
        cls = registry[name]
        rows.append(
            (
                name,
                cls.__name__,
                Path(sys.modules[cls.__module__].__file__ or "?").name,
                "yes" if getattr(cls, "supports_write", True) else "no",
                "yes" if getattr(cls, "uses_scope", False) else "no",
                str(getattr(cls, "backend", "?")),
                str(getattr(cls, "trigger", "")),  # ours family only
            )
        )

    headers = ("arm", "class", "module", "write", "scope", "backend", "trigger")
    widths = [
        max(len(headers[i]), *(len(r[i]) for r in rows)) for i in range(len(headers))
    ]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for row in rows:
        print(fmt.format(*row))
    print(
        f"\n{len(rows)} wired arms (source: _systems_registry in membench/memory_systems/__init__.py)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
