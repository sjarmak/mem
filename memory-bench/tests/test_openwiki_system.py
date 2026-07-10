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
    _parse_background_tokens,
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
    # sources in a fresh per-call workdir, invokes `openwiki code --update --print`, then
    # reads the wiki dir back and removes the workdir.
    captured: dict[str, object] = {}

    def fake_runner(argv: list[str], cwd: Path) -> str:
        captured["argv"] = argv
        captured["cwd"] = cwd
        # The sources were materialised in this call's workdir for OpenWiki to ingest.
        assert (cwd / "sources" / "a.md").read_text(encoding="utf-8") == "alpha"
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
    (page,) = result.pages
    assert page.page_id == "openwiki-page-overview"  # namespaced away from raw ids
    assert page.content == "synthesised overview"
    assert page.source_ids == ("a", "b")  # conservative all-source provenance
    assert result.background_tokens == 128
    # The per-call workdir is hermetic and removed after synthesis (no cross-trial leak).
    assert not Path(str(captured["cwd"])).exists()


def test_cli_synthesizer_empty_sources_skips_shell(tmp_path: Path) -> None:
    def exploding_runner(argv: list[str], cwd: Path) -> str:
        raise AssertionError("must not shell openwiki for an empty source set")

    synth = _CliWikiSynthesizer(tmp_path, runner=exploding_runner)
    assert synth.synthesize(sources={}).pages == ()


@pytest.mark.parametrize("bad_id", ["../oops", "a/b", "/etc/x", "a b"])
def test_cli_synthesizer_rejects_unsafe_source_id(tmp_path: Path, bad_id: str) -> None:
    def exploding_runner(argv: list[str], cwd: Path) -> str:
        raise AssertionError("must reject an unsafe id before shelling openwiki")

    synth = _CliWikiSynthesizer(tmp_path, runner=exploding_runner)
    with pytest.raises(ValueError, match="not filesystem-safe"):
        synth.synthesize(sources={bad_id: "x"})


def test_build_openwiki_constructs_with_injected_synthesizer() -> None:
    arm = build_memory_system("openwiki", synthesizer=ConcatWikiSynthesizer())
    assert arm.name == "openwiki"
    assert arm.uses_scope is False
    assert arm.supports_write is True


@pytest.mark.parametrize(
    "stdout,expected",
    [
        ('{"total_tokens": 128}', 128),
        ('noise\n{"total_tokens": 7}\ntrailing', 7),
        ('{"total_tokens": -1}', 0),  # negative → treated as absent
        ('{"total_tokens": true}', 0),  # bool-is-int trap: not a count
        ('{"other": 5}', 0),  # missing key
        ("not json at all", 0),
        ("", 0),
        # Two JSON-looking lines: the LAST valid one wins (reversed scan).
        ('{"total_tokens": 1}\n{"total_tokens": 9}', 9),
        ("{not valid json}", 0),  # {-prefixed but unparseable → skipped, never raises
    ],
)
def test_parse_background_tokens(stdout: str, expected: int) -> None:
    assert _parse_background_tokens(stdout) == expected


class _TwoPageSynthesizer:
    """Emits a valid page then a sourceless page — to prove consolidate() is atomic."""

    def synthesize(self, *, sources: Mapping[str, str]) -> WikiSynthesisResult:
        good = WikiPage(page_id="p-good", content="c", source_ids=tuple(sources.keys()))
        bad = WikiPage(page_id="p-bad", content="c2", source_ids=())
        return WikiSynthesisResult(pages=(good, bad), background_tokens=0)


def test_consolidate_is_atomic_on_later_sourceless_page() -> None:
    arm = OpenWikiMemory(synthesizer=_TwoPageSynthesizer())
    arm.reset("t-1")
    arm.write("a", "alpha", _ctx())
    with pytest.raises(ValueError, match="no source_ids"):
        arm.consolidate(_ctx())
    # The valid page must NOT have been half-committed before the guard fired.
    from membench.memory_systems import RetrievalRequest

    res = arm.retrieve(RetrievalRequest(query_text="q", requested_ids=["p-good"]), _ctx())
    assert res.payloads == {}
    # And the source was not tombstoned by the aborted pass.
    assert "a" not in arm._tombstoned


class _MultiSourceSynthesizer:
    """One page citing many sources in a fixed order — to pin tombstone determinism."""

    def synthesize(self, *, sources: Mapping[str, str]) -> WikiSynthesisResult:
        ids = tuple(sources.keys())
        return WikiSynthesisResult(pages=(WikiPage("p", "c", ids),), background_tokens=0)


def test_tombstoned_ids_are_deterministic_first_seen_order() -> None:
    arm = OpenWikiMemory(synthesizer=_MultiSourceSynthesizer())
    arm.reset("t-1")
    for mid in ("c", "a", "b", "d"):
        arm.write(mid, mid, _ctx())
    result = arm.consolidate(_ctx())
    # Ordered dedup (dict.fromkeys), not set iteration → stable write-order tuple.
    assert result.tombstoned_ids == ("c", "a", "b", "d")


def test_page_id_namespaced_away_from_raw_id_collision() -> None:
    # A synthesised page id can never equal a raw memory_id, so a raw lookup is never
    # misrouted to wiki content. The CLI synthesiser prefixes md.stem with the namespace.
    captured: dict[str, object] = {}

    def fake_runner(argv: list[str], cwd: Path) -> str:
        captured["cwd"] = cwd
        wiki = cwd / "openwiki"
        wiki.mkdir(parents=True, exist_ok=True)
        # OpenWiki names a page identically to a raw source id ("a").
        (wiki / "a.md").write_text("WIKI CONTENT", encoding="utf-8")
        return ""

    runner: WikiCliRunner = fake_runner
    synth = _CliWikiSynthesizer(runner=runner)
    (page,) = synth.synthesize(sources={"a": "raw-a"}).pages
    assert page.page_id == "openwiki-page-a"
    assert page.page_id != "a"
