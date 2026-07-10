"""OpenWiki arm — the LLM-wiki-synthesis consolidating-filesystem competitive arm.

Contract mirrored from ``ConsolidatingMemory`` (the two-speed sibling):

* ``write`` is an O(1) wake-append — ZERO synthesiser calls on the hot path.
* ``consolidate`` shells the injected ``WikiSynthesizer`` ONCE to build wiki pages,
  metering the offline LLM cost as ``background_tokens``.
* ``retrieve`` returns the synthesised pages; a subsumed raw source redirects to the
  page it was folded into; every returned item carries non-empty provenance (M7).
* subtractive ops are tombstone-only (soft, content re-derivable) — no hard delete.
* the synthesiser is injected behind a Protocol, so CI runs a deterministic fake with
  no Node CLI, no Ollama, and no network.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from membench.memory_systems import build_memory_system
from membench.memory_systems.consolidation import ConsolidationCapable
from membench.memory_systems.openwiki_system import (
    ConcatWikiSynthesizer,
    OpenWikiMemory,
    WikiCliRunner,
    WikiPage,
    WikiSynthesisResult,
    WikiSynthesizer,
    _CliWikiSynthesizer,
    default_openwiki_synthesizer,
)
from membench.runtime import IdClock, StepContext
from membench.schemas.memory_event import MemoryOperation


def _ctx(trial: str = "t-1", step: str = "s") -> StepContext:
    return StepContext(trial_id=trial, session_id="sess", step_id=step, clock=IdClock())


class _CountingSynthesizer:
    """Stands in for the OpenWiki LLM: records call count and reports a positive
    offline token cost, so the write-path-model-free contract and the ``background_tokens``
    meter are both testable without a real CLI. One page citing every source."""

    def __init__(self) -> None:
        self.calls = 0

    def synthesize(self, *, sources: Mapping[str, str]) -> WikiSynthesisResult:
        self.calls += 1
        ids = tuple(sources.keys())
        page = WikiPage(
            page_id="wiki-0", content="::".join(sources[i] for i in ids), source_ids=ids
        )
        return WikiSynthesisResult(pages=(page,), background_tokens=42)


def test_write_is_hot_path_no_synthesizer_call() -> None:
    synth = _CountingSynthesizer()
    arm = OpenWikiMemory(synthesizer=synth)
    arm.reset("t-1")
    arm.write("a", "alpha", _ctx())
    arm.write("b", "beta", _ctx())
    # The hot path never synthesises — all model cost is deferred to consolidate().
    assert synth.calls == 0


def test_consolidate_synthesizes_once_and_meters_tokens() -> None:
    synth = _CountingSynthesizer()
    arm = OpenWikiMemory(synthesizer=synth)
    arm.reset("t-1")
    arm.write("a", "alpha", _ctx())
    arm.write("b", "beta", _ctx())
    result = arm.consolidate(_ctx())
    assert synth.calls == 1
    assert result.background_tokens == 42
    assert result.notes["pages"] == "1"
    (item,) = result.items
    assert item.memory_id == "wiki-0"
    assert item.source_trace_ids == ("a", "b")
    # Both raw sources were folded into the page → soft-deleted, still re-derivable.
    assert set(result.tombstoned_ids) == {"a", "b"}
    assert arm.is_live("a") and arm.is_live("b")


def test_consolidate_empty_is_noop() -> None:
    arm = OpenWikiMemory(synthesizer=_CountingSynthesizer())
    arm.reset("t-1")
    result = arm.consolidate(_ctx())
    assert result.items == ()
    assert result.background_tokens == 0
    assert result.notes["pages"] == "0"


def test_retrieve_raw_before_consolidation() -> None:
    arm = OpenWikiMemory(synthesizer=ConcatWikiSynthesizer())
    arm.reset("t-1")
    arm.write("a", "alpha", _ctx())
    from membench.memory_systems import RetrievalRequest

    res = arm.retrieve(RetrievalRequest(query_text="q", requested_ids=["a"]), _ctx())
    # Before consolidation the raw source is returned, provenance = itself.
    assert res.payloads == {"a": "alpha"}
    assert res.source_trace_ids == {"a": ("a",)}
    assert res.event.normalized_operation == MemoryOperation.SEARCH


def test_retrieve_redirects_subsumed_source_to_page() -> None:
    arm = OpenWikiMemory(synthesizer=ConcatWikiSynthesizer())
    arm.reset("t-1")
    arm.write("a", "alpha", _ctx())
    arm.write("b", "beta", _ctx())
    arm.consolidate(_ctx())
    from membench.memory_systems import RetrievalRequest

    # Ask for a raw source that was subsumed → the synthesised page comes back instead.
    res = arm.retrieve(RetrievalRequest(query_text="q", requested_ids=["a"]), _ctx())
    page_id = ConcatWikiSynthesizer.PAGE_ID
    assert set(res.payloads) == {page_id}
    assert res.payloads[page_id] == "alpha\n\nbeta"
    assert res.source_trace_ids[page_id] == ("a", "b")


def test_retrieve_page_directly() -> None:
    arm = OpenWikiMemory(synthesizer=ConcatWikiSynthesizer())
    arm.reset("t-1")
    arm.write("a", "alpha", _ctx())
    arm.consolidate(_ctx())
    from membench.memory_systems import RetrievalRequest

    page_id = ConcatWikiSynthesizer.PAGE_ID
    res = arm.retrieve(RetrievalRequest(query_text="q", requested_ids=[page_id]), _ctx())
    assert res.payloads == {page_id: "alpha"}


def test_reset_clears_pages_and_sources() -> None:
    arm = OpenWikiMemory(synthesizer=ConcatWikiSynthesizer())
    arm.reset("t-1")
    arm.write("a", "alpha", _ctx())
    arm.consolidate(_ctx())
    arm.reset("t-1")
    from membench.memory_systems import RetrievalRequest

    res = arm.retrieve(RetrievalRequest(query_text="q", requested_ids=["a"]), _ctx())
    assert res.payloads == {}


class _SourcelessSynthesizer:
    def synthesize(self, *, sources: Mapping[str, str]) -> WikiSynthesisResult:
        return WikiSynthesisResult(pages=(WikiPage("p", "x", ()),), background_tokens=0)


def test_sourceless_page_raises() -> None:
    arm = OpenWikiMemory(synthesizer=_SourcelessSynthesizer())
    arm.reset("t-1")
    arm.write("a", "alpha", _ctx())
    with pytest.raises(ValueError, match="no source_ids"):
        arm.consolidate(_ctx())


def test_arm_satisfies_consolidation_capable() -> None:
    assert isinstance(OpenWikiMemory(synthesizer=ConcatWikiSynthesizer()), ConsolidationCapable)


def test_default_synthesizer_constructs_without_shelling() -> None:
    # Building the real CLI-backed synthesiser must NOT shell openwiki; construction is
    # binary-free, only a real consolidate() would invoke the CLI.
    synth = default_openwiki_synthesizer()
    assert isinstance(synth, WikiSynthesizer)


def test_cli_synthesizer_shell_shape(tmp_path: Path) -> None:
    # Exercise _CliWikiSynthesizer's shell shape with an injected runner: it materialises
    # sources, invokes `openwiki code --update --print`, then reads the wiki dir back.
    captured: dict[str, object] = {}

    def fake_runner(argv: list[str], cwd: Path) -> str:
        captured["argv"] = argv
        captured["cwd"] = cwd
        # Simulate OpenWiki writing a synthesised page + a token-accounting summary line.
        wiki = cwd / "openwiki"
        wiki.mkdir(parents=True, exist_ok=True)
        (wiki / "overview.md").write_text("synthesised overview", encoding="utf-8")
        return '{"total_tokens": 128}'

    runner: WikiCliRunner = fake_runner
    synth = _CliWikiSynthesizer(tmp_path, runner=runner)
    result = synth.synthesize(sources={"a": "alpha", "b": "beta"})

    argv = captured["argv"]
    assert isinstance(argv, list)
    assert argv[:4] == ["openwiki", "code", "--update", "--print"]
    assert "openai-compatible" in argv  # no-paid-API: local endpoint, not a paid provider
    # Sources were materialised as Markdown files for OpenWiki to ingest.
    assert (tmp_path / "sources" / "a.md").read_text(encoding="utf-8") == "alpha"
    (page,) = result.pages
    assert page.page_id == "overview"
    assert page.content == "synthesised overview"
    assert page.source_ids == ("a", "b")  # conservative all-source provenance
    assert result.background_tokens == 128


def test_cli_synthesizer_empty_sources_skips_shell(tmp_path: Path) -> None:
    def exploding_runner(argv: list[str], cwd: Path) -> str:
        raise AssertionError("must not shell openwiki for an empty source set")

    synth = _CliWikiSynthesizer(tmp_path, runner=exploding_runner)
    assert synth.synthesize(sources={}).pages == ()


def test_build_openwiki_constructs_with_injected_synthesizer() -> None:
    arm = build_memory_system("openwiki", synthesizer=ConcatWikiSynthesizer())
    assert arm.name == "openwiki"
    assert arm.uses_scope is False
    assert arm.supports_write is True
