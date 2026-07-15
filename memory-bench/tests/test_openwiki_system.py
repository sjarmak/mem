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

import shutil
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path

import pytest

from membench.memory_systems import build_memory_system
from membench.memory_systems.consolidation import ConsolidationCapable
from membench.memory_systems.openwiki_system import (
    ConcatWikiSynthesizer,
    OpenWikiCliError,
    OpenWikiMemory,
    WikiCliRunner,
    WikiPage,
    WikiSynthesisResult,
    WikiSynthesizer,
    _CliWikiSynthesizer,
    _default_cli_runner,
    _git_snapshot,
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
    # Exercise _CliWikiSynthesizer's verified shell shape (openwiki v0.1.0, mem-nul9j)
    # with an injected runner: sources written to the workdir root + git-snapshotted,
    # `openwiki code --init --print --modelId <m>` invoked with env-driven provider
    # config, nested pages read back via rglob, workdir removed.
    captured: dict[str, object] = {}

    def fake_runner(argv: list[str], cwd: Path, env: Mapping[str, str]) -> str:
        captured["argv"] = argv
        captured["cwd"] = cwd
        captured["env"] = dict(env)
        # Sources were written to the workdir root and committed (code mode needs git).
        assert (cwd / "a.md").read_text(encoding="utf-8") == "alpha"
        assert (cwd / ".git").is_dir()
        # Simulate OpenWiki writing NESTED synthesised pages (as the real CLI does).
        (cwd / "openwiki" / "architecture").mkdir(parents=True, exist_ok=True)
        (cwd / "openwiki" / "quickstart.md").write_text("qs", encoding="utf-8")
        (cwd / "openwiki" / "architecture" / "overview.md").write_text("ov", encoding="utf-8")
        return "no token json in --print prose output"

    runner: WikiCliRunner = fake_runner
    synth = _CliWikiSynthesizer(tmp_path, runner=runner)
    result = synth.synthesize(sources={"a": "alpha", "b": "beta"})

    argv = captured["argv"]
    assert isinstance(argv, list)
    assert argv[:4] == ["openwiki", "code", "--init", "--print"]
    assert "--modelId" in argv
    env = captured["env"]
    assert isinstance(env, dict)
    # no-paid-API: local Ollama openai-compatible endpoint, not a paid provider.
    assert env["OPENWIKI_PROVIDER"] == "openai-compatible"
    assert env["OPENAI_COMPATIBLE_BASE_URL"].endswith("/v1")
    assert env["OPENWIKI_MODEL_ID"]  # model pinned from the LocalModelStack
    # Nested pages are read recursively and namespaced by relative path.
    by_id = {p.page_id: p for p in result.pages}
    assert set(by_id) == {"openwiki-page-quickstart", "openwiki-page-architecture_overview"}
    assert by_id["openwiki-page-architecture_overview"].content == "ov"
    assert by_id["openwiki-page-quickstart"].source_ids == ("a", "b")  # all-source provenance
    assert result.background_tokens == 0  # --print emits prose, no token accounting
    # The per-call workdir is hermetic and removed after synthesis (no cross-trial leak).
    assert not Path(str(captured["cwd"])).exists()


def test_cli_synthesizer_empty_sources_skips_shell(tmp_path: Path) -> None:
    def exploding_runner(argv: list[str], cwd: Path, env: Mapping[str, str]) -> str:
        raise AssertionError("must not shell openwiki for an empty source set")

    synth = _CliWikiSynthesizer(tmp_path, runner=exploding_runner)
    assert synth.synthesize(sources={}).pages == ()


@pytest.mark.parametrize("bad_id", ["../oops", "a/b", "/etc/x", "a b"])
def test_cli_synthesizer_rejects_unsafe_source_id(tmp_path: Path, bad_id: str) -> None:
    def exploding_runner(argv: list[str], cwd: Path, env: Mapping[str, str]) -> str:
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


def _openwiki_stack_available() -> bool:
    """True iff the real OpenWiki CLI is installed AND the local Ollama daemon has the
    pinned chat model pulled. Gates the end-to-end integration test so it skips cleanly
    in CI (no CLI/daemon) and wherever the pinned model isn't provisioned — rather than
    failing on a MODEL_NOT_FOUND. Set MEMBENCH_LOCAL_CHAT_MODEL to a pulled model to run."""
    if shutil.which("openwiki") is None:
        return False
    import json

    from membench.memory_systems.local_stack import LocalModelStack

    stack = LocalModelStack.from_env()
    base = stack.ollama_base_url.rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=3) as resp:
            tags = [m.get("name", "") for m in json.loads(resp.read()).get("models", [])]
    except (urllib.error.URLError, OSError):
        return False
    return any(t == stack.chat_model or t.split(":", 1)[0] == stack.chat_model for t in tags)


@pytest.mark.skipif(
    not _openwiki_stack_available(),
    reason="requires the provisioned OpenWiki CLI (npm i -g openwiki) + a local Ollama daemon",
)
def test_openwiki_real_end_to_end() -> None:
    # Full real path (verified for mem-nul9j): write sources → git snapshot → shell the
    # real `openwiki code --init --print` against Ollama → read nested pages back. LLM
    # output is nondeterministic, so we assert the plumbing (env/git/argv/parse/read)
    # runs clean and returns a well-formed result, not a specific page set.
    synth = default_openwiki_synthesizer()
    result = synth.synthesize(
        sources={
            "deploy": "The deploy script is scripts/deploy.sh; it needs AWS_PROFILE=prod.",
            "orders": "The orders table needs an index on created_at for the nightly report.",
        }
    )
    assert isinstance(result, WikiSynthesisResult)
    assert result.background_tokens >= 0
    for page in result.pages:
        assert page.page_id.startswith("openwiki-page-")
        assert page.source_ids == ("deploy", "orders")


def test_page_id_namespaced_away_from_raw_id_collision() -> None:
    # A synthesised page id can never equal a raw memory_id, so a raw lookup is never
    # misrouted to wiki content. The CLI synthesiser prefixes md.stem with the namespace.
    captured: dict[str, object] = {}

    def fake_runner(argv: list[str], cwd: Path, env: Mapping[str, str]) -> str:
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


# --------------------------------------------------------------------------- #
# the shell seam's failure ladder (mem-o9plh)
#
# `_default_cli_runner` and `_git_snapshot` both spawn; both are on the shared
# `spawn.run_checked` ladder, which owns the rung ORDER. What each site owns is
# its own error TYPE and its own install hint -- that is what these assert. The
# rungs themselves are proven once in tests/test_spawn.py.
# --------------------------------------------------------------------------- #
def test_cli_runner_missing_binary_names_the_openwiki_fix(tmp_path) -> None:
    with pytest.raises(OpenWikiCliError, match="npm i -g openwiki"):
        _default_cli_runner([str(tmp_path / "absent-openwiki")], tmp_path, {})


def test_cli_runner_unspawnable_binary_surfaces_as_openwiki_error(tmp_path) -> None:
    # Present but not executable -> PermissionError, NOT FileNotFoundError. This rung
    # was omitted here until mem-o9plh, letting a raw OSError escape the seam's contract.
    binary = tmp_path / "unexecutable-openwiki"
    binary.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    binary.chmod(0o644)
    with pytest.raises(OpenWikiCliError, match="could not spawn"):
        _default_cli_runner([str(binary)], tmp_path, {})


def test_cli_runner_nonzero_exit_carries_stderr(tmp_path) -> None:
    binary = tmp_path / "failing-openwiki"
    binary.write_text("#!/bin/sh\necho 'no provider configured' >&2\nexit 4\n", encoding="utf-8")
    binary.chmod(0o755)
    with pytest.raises(OpenWikiCliError, match=r"exit 4.*no provider configured"):
        _default_cli_runner([str(binary)], tmp_path, {})


def test_cli_runner_overlays_env_on_the_process_environment(tmp_path) -> None:
    # The seam's contract: `env` overlays os.environ rather than replacing it, so the
    # spawn keeps PATH while taking OpenWiki's provider config.
    binary = tmp_path / "echoing-openwiki"
    binary.write_text('#!/bin/sh\necho "$OPENWIKI_MODEL_ID"\n', encoding="utf-8")
    binary.chmod(0o755)
    assert _default_cli_runner([str(binary)], tmp_path, {"OPENWIKI_MODEL_ID": "m1"}) == "m1\n"


def test_git_snapshot_commits_the_sources(tmp_path) -> None:
    (tmp_path / "a.md").write_text("raw-a", encoding="utf-8")
    _git_snapshot(tmp_path)
    assert (tmp_path / ".git").is_dir()


def test_git_snapshot_failure_names_the_step_that_failed(tmp_path) -> None:
    # Nothing to commit -> `git commit` exits non-zero. The message must name THAT step,
    # not just "git": per-step error identity is what the loop had before the sweep and
    # must keep after it.
    with pytest.raises(OpenWikiCliError, match=r"git .*commit.*failed \(exit 1\)"):
        _git_snapshot(tmp_path)
