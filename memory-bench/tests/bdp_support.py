"""Shared paths and readers for the BDP fixture gates.

Two test modules assert against one emitted tree: `test_bdp_fixtures.py` covers
the seven generated graph shapes, and `test_bdp_collation.py` covers the
collation family. They read the same files the same way, so the readers live
here rather than in whichever module happened to need them first.

Everything here reads the tree as it shipped. Nothing re-derives an expectation
from the emitter, which is the point: a gate that recomputes what the code under
test computed asserts only that the expression was copied correctly.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from membench.bdp_fixtures.emit import DEFAULT_OUT
from membench.bdp_fixtures.topologies import TOPOLOGIES_BY_NAME

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / DEFAULT_OUT
PACKAGE = REPO / "fixtures/bdp"
SOURCE_PACKAGE = REPO / "membench/bdp_fixtures"
SCHEMA = PACKAGE / "upstream/bdp-v0.schema.json"
README = PACKAGE / "README.md"

# An absolute path from somebody's machine, in the spellings that would actually
# appear. The package is meant to be published, so one of these anywhere in it is
# a leak, not a cosmetic problem.
LOCAL_PATH = re.compile(r"(/home/[a-z]|/Users/[A-Za-z]|[A-Za-z]:\\\\)")


def load_bundle() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(SCHEMA.read_text(encoding="utf-8"))
    return loaded


def validator(bundle: dict[str, Any], definition: str) -> Draft202012Validator:
    if definition not in bundle["$defs"]:
        raise AssertionError(f"the pinned bundle has no $defs/{definition}")
    schema = dict(bundle)
    schema["$ref"] = f"#/$defs/{definition}"
    return Draft202012Validator(schema)


def families() -> list[str]:
    return sorted(TOPOLOGIES_BY_NAME)


def read(family: str, *parts: str) -> Any:
    return json.loads(FIXTURES.joinpath(family, *parts).read_text(encoding="utf-8"))


def manifest() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    return loaded


def readme() -> str:
    return README.read_text(encoding="utf-8")


def local(url: str) -> str:
    return url.rsplit("/", 1)[-1]


def chunks(items: list[str], limit: int) -> list[list[str]]:
    """Page an ordered list by accumulation.

    Deliberately not the emitter's stride-over-range formulation: a test that
    re-implements the code under test character for character asserts only that
    the expression was copied correctly.
    """

    pages: list[list[str]] = []
    current: list[str] = []
    for item in items:
        current.append(item)
        if len(current) == limit:
            pages.append(current)
            current = []
    if current:
        pages.append(current)
    return pages
