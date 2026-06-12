"""Oracle-context curation (mem-75t.7.3): consensus gate + Tier-1/Tier-2 curator +
gold-diff-seeded build glue. Fully offline -- StubResolver and StubOracleCurator
stand in for grep/Sourcegraph and the OAuth `claude -p` curator, and every
subprocess backend is exercised through an injected runner."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from membench.bundle.replay import CallReplay, ReplayOutcome, ReplayResult
from membench.oracle import (
    BackendResult,
    ClaudeOracleCurator,
    GrepResolver,
    OracleCuratorError,
    SourcegraphResolver,
    StubOracleCurator,
    StubResolver,
    build_oracle_context,
    canonicalize_repo_path,
    compute_consensus,
    compute_pair_metrics,
    curate_bundle,
    curate_consensus,
    parse_curator_reply,
)
from membench.oracle.backends import ENV_SG_ENDPOINT, ENV_SG_TOKEN
from membench.oracle.build import GOLD_DIFF_BACKEND
from membench.schemas.bundle import BundleEnv, TaskBundle

REPO = Path(".")


def _completed(
    stdout: str = "", returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _repo_with(root: Path, *files: str) -> Path:
    """Write each ``files`` path under ``root`` so a Tier-2 curator can read its
    snippet (the curator quarantines candidates it cannot read)."""
    for rel in files:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"// {rel}\n", encoding="utf-8")
    return root


# --- canonicalize_repo_path ------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("src/a.ts", "src/a.ts"),  # already canonical -- unchanged
        ("./src/a.ts", "src/a.ts"),  # leading ./ stripped
        ("src/./a.ts", "src/a.ts"),  # interior . collapsed
        ("src/sub/../a.ts", "src/a.ts"),  # .. collapsed
        ("  src/a.ts  ", "src/a.ts"),  # whitespace trimmed
        ("", ""),  # empty stays empty
    ],
)
def test_canonicalize_relative_forms(raw, expected):
    assert canonicalize_repo_path(raw, Path("/repo")) == expected


def test_canonicalize_absolute_under_repo_is_rebased():
    assert canonicalize_repo_path("/repo/src/a.ts", Path("/repo")) == "src/a.ts"


def test_canonicalize_absolute_outside_repo_left_absolute():
    # A genuine divergence the consensus report should show, not silently rewrite.
    assert canonicalize_repo_path("/elsewhere/a.ts", Path("/repo")) == "/elsewhere/a.ts"


def test_consensus_counts_divergent_path_shapes_as_one_file():
    # Two backends naming the SAME files in different shapes must AGREE (F1=1.0),
    # not be defeated by the path shape -- the dormant 2nd-backend failure mode.
    a = StubResolver("grep", {"s": frozenset({"src/a.ts", "src/b.ts"})})
    b = StubResolver("sourcegraph", {"s": frozenset({"./src/a.ts", "src/./b.ts"})})
    d = compute_consensus(
        symbol="s", defining_file="s.ts", repo_root=Path("/repo"), resolvers=[a, b]
    )
    assert d.shipped
    assert d.max_pair_f1 == 1.0
    assert d.consensus_files == frozenset({"src/a.ts", "src/b.ts"})


# --- compute_pair_metrics --------------------------------------------------------


def test_pair_metrics_both_empty_agree_vacuously():
    assert compute_pair_metrics(frozenset(), frozenset())["f1"] == 1.0


def test_pair_metrics_disjoint_zero_f1():
    m = compute_pair_metrics(frozenset({"a"}), frozenset({"b"}))
    assert m["f1"] == 0.0 and m["n_overlap"] == 0


def test_pair_metrics_partial_overlap():
    m = compute_pair_metrics(frozenset({"a", "b"}), frozenset({"a"}))
    assert m["precision"] == 1.0 and m["recall"] == 0.5 and m["n_overlap"] == 1


# --- compute_consensus -----------------------------------------------------------


def test_consensus_ships_when_two_backends_agree():
    a = StubResolver("grep", {"s": frozenset({"a", "b"})})
    b = StubResolver("sourcegraph", {"s": frozenset({"a", "b"})})
    d = compute_consensus(symbol="s", defining_file="s.ts", repo_root=REPO, resolvers=[a, b])
    assert d.shipped and d.consensus_files == frozenset({"a", "b"})
    assert d.divergence_report["decision"] == "shipped"


def test_consensus_quarantines_below_f1_threshold():
    a = StubResolver("grep", {"s": frozenset({"a", "b", "c"})})
    b = StubResolver("sourcegraph", {"s": frozenset({"a"})})
    d = compute_consensus(
        symbol="s", defining_file="s.ts", repo_root=REPO, resolvers=[a, b], threshold=0.8
    )
    assert not d.shipped and d.max_pair_f1 < 0.8
    assert d.divergence_report["decision"] == "quarantined"


def test_consensus_single_available_backend_never_ships():
    a = StubResolver("grep", {"s": frozenset({"a"})})
    b = StubResolver("sourcegraph", available=False, error="no auth")
    d = compute_consensus(symbol="s", defining_file="s.ts", repo_root=REPO, resolvers=[a, b])
    assert not d.shipped and d.available_backends == ("grep",)


def test_consensus_intersection_is_high_precision():
    a = StubResolver("grep", {"s": frozenset({"a", "b", "x"})})
    b = StubResolver("sourcegraph", {"s": frozenset({"a", "b", "y"})})
    d = compute_consensus(
        symbol="s", defining_file="s.ts", repo_root=REPO, resolvers=[a, b], threshold=0.5
    )
    assert d.consensus_files == frozenset({"a", "b"})


def test_consensus_union_is_high_recall():
    a = StubResolver("grep", {"s": frozenset({"a"})})
    b = StubResolver("sourcegraph", {"s": frozenset({"b"})})
    d = compute_consensus(
        symbol="s",
        defining_file="s.ts",
        repo_root=REPO,
        resolvers=[a, b],
        mode="union",
        threshold=0.0,
    )
    assert d.consensus_files == frozenset({"a", "b"})


def test_consensus_resolver_exception_becomes_unavailable():
    class Boom:
        name = "boom"

        def resolve(self, symbol, *, defining_file, repo_root):
            raise RuntimeError("kaboom")

    a = StubResolver("grep", {"s": frozenset({"a"})})
    d = compute_consensus(symbol="s", defining_file="s.ts", repo_root=REPO, resolvers=[a, Boom()])
    boom = next(r for r in d.backend_results if r.backend == "boom")
    assert not boom.available and "kaboom" in (boom.error or "")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"threshold": 1.5},
        {"mode": "bogus"},
    ],
)
def test_consensus_rejects_bad_params(kwargs):
    a = StubResolver("grep", {"s": frozenset({"a"})})
    with pytest.raises(ValueError):
        compute_consensus(symbol="s", defining_file="s.ts", repo_root=REPO, resolvers=[a], **kwargs)


def test_consensus_rejects_empty_resolvers_and_symbol():
    with pytest.raises(ValueError):
        compute_consensus(symbol="s", defining_file="s.ts", repo_root=REPO, resolvers=[])
    with pytest.raises(ValueError):
        compute_consensus(
            symbol="", defining_file="s.ts", repo_root=REPO, resolvers=[StubResolver("grep")]
        )


def test_consensus_rejects_duplicate_resolver_names():
    # Same name -> one result would silently overwrite the other in the results dict.
    a = StubResolver("grep", {"s": frozenset({"a"})})
    b = StubResolver("grep", {"s": frozenset({"b"})})
    with pytest.raises(ValueError, match="unique"):
        compute_consensus(symbol="s", defining_file="s.ts", repo_root=REPO, resolvers=[a, b])


def test_consensus_three_backends_ships_on_one_agreeing_pair(tmp_path):
    # Two agree (F1=1.0), the third diverges; ships on the agreeing pair even though
    # the strict 3-way intersection (consensus_files) is empty.
    repo = _repo_with(
        tmp_path, "z"
    )  # single-backend candidate must be readable to reach the curator
    a = StubResolver("grep", {"s": frozenset({"a", "b"})})
    b = StubResolver("sourcegraph", {"s": frozenset({"a", "b"})})
    c = StubResolver("ast", {"s": frozenset({"z"})})
    d = compute_consensus(symbol="s", defining_file="s.ts", repo_root=repo, resolvers=[a, b, c])
    assert d.shipped and d.consensus_files == frozenset()  # diagnostic field, not the oracle
    out = curate_consensus(
        backend_results=d.backend_results,
        symbol="s",
        defining_file="s.ts",
        repo_root=repo,
        curator=StubOracleCurator(keep=False),
    )
    # a, b are Tier-1 (two backends); z is single-backend -> Tier-2 reject -> quarantined.
    assert {(i.path, i.tier) for i in out.items} == {("a", "required"), ("b", "required")}
    assert out.quarantined == (("z", "LLM rejected: stub verdict"),)


# --- curate_consensus ------------------------------------------------------------


def _two_backend_results(grep_files, sg_files) -> list[BackendResult]:
    return [
        BackendResult(backend="grep", files=frozenset(grep_files)),
        BackendResult(backend="sourcegraph", files=frozenset(sg_files)),
    ]


def test_curate_tier1_required_no_llm():
    res = _two_backend_results({"a", "b"}, {"a", "b"})
    out = curate_consensus(backend_results=res, symbol="s", defining_file="s.ts", repo_root=REPO)
    assert {(i.path, i.tier) for i in out.items} == {("a", "required"), ("b", "required")}
    assert not out.llm_used and out.backends_consensus == ("grep", "sourcegraph")


def test_curate_tier2_keep_is_supplementary(tmp_path):
    repo = _repo_with(tmp_path, "a", "c")
    res = _two_backend_results({"a", "c"}, {"a"})
    out = curate_consensus(
        backend_results=res,
        symbol="s",
        defining_file="s.ts",
        repo_root=repo,
        curator=StubOracleCurator(keep=True, rationale="aliased import"),
    )
    supp = [i for i in out.items if i.tier == "supplementary"]
    assert supp and supp[0].path == "c" and supp[0].via_llm_review
    assert supp[0].llm_rationale == "aliased import" and out.llm_used


def test_curate_tier2_unreadable_candidate_quarantined(tmp_path):
    # 'c' is single-backend but does not exist on disk: no snippet to judge, so it
    # must be quarantined -- never kept on a placeholder snippet, never silently sent.
    repo = _repo_with(tmp_path, "a")  # 'c' deliberately absent
    res = _two_backend_results({"a", "c"}, {"a"})
    out = curate_consensus(
        backend_results=res,
        symbol="s",
        defining_file="s.ts",
        repo_root=repo,
        curator=StubOracleCurator(keep=True),  # would keep if asked -- but it is not asked
    )
    assert "c" not in {i.path for i in out.items}
    assert out.quarantined and "unreadable" in out.quarantined[0][1]


def test_curate_tier2_reject_is_quarantined():
    res = _two_backend_results({"a", "c"}, {"a"})
    out = curate_consensus(
        backend_results=res,
        symbol="s",
        defining_file="s.ts",
        repo_root=REPO,
        curator=StubOracleCurator(keep=False, rationale="unrelated symbol"),
    )
    assert "c" not in {i.path for i in out.items}
    assert out.quarantined and out.quarantined[0][0] == "c"


def test_curate_tier2_no_curator_quarantines_not_drops():
    res = _two_backend_results({"a", "c"}, {"a"})
    out = curate_consensus(
        backend_results=res, symbol="s", defining_file="s.ts", repo_root=REPO, use_llm=False
    )
    assert out.quarantined == (("c", "single-backend, LLM curator unavailable"),)


def test_curate_single_backend_fallback_keeps_all_required():
    res = [BackendResult(backend="grep", files=frozenset({"a", "b"}))]
    out = curate_consensus(backend_results=res, symbol="s", defining_file="s.ts", repo_root=REPO)
    assert {(i.path, i.tier) for i in out.items} == {("a", "required"), ("b", "required")}
    assert out.backends_consensus == ("grep",)


def test_curate_no_available_backends_is_empty():
    res = [BackendResult(backend="grep", available=False)]
    out = curate_consensus(backend_results=res, symbol="s", defining_file="s.ts", repo_root=REPO)
    assert out.items == () and out.backends_consensus == ()


def test_curate_rejects_bad_min_backends():
    with pytest.raises(ValueError):
        curate_consensus(
            backend_results=[], symbol="s", defining_file="s.ts", repo_root=REPO, min_backends=0
        )


def test_curate_llm_error_quarantines_conservatively(tmp_path):
    repo = _repo_with(tmp_path, "a", "c")
    res = _two_backend_results({"a", "c"}, {"a"})
    out = curate_consensus(
        backend_results=res,
        symbol="s",
        defining_file="s.ts",
        repo_root=repo,
        curator=StubOracleCurator(fn=lambda _p: "not json at all"),
    )
    assert out.quarantined and "non-JSON" in out.quarantined[0][1]


# --- parse_curator_reply ---------------------------------------------------------


def test_parse_reply_valid():
    v = parse_curator_reply('{"keep": true, "rationale": "ok"}')
    assert v.keep and v.rationale == "ok" and v.error is None


def test_parse_reply_strips_code_fence():
    v = parse_curator_reply('```json\n{"keep": false, "rationale": "no"}\n```')
    assert not v.keep and v.error is None


@pytest.mark.parametrize(
    "reply",
    ["not json", "[1, 2]", '{"rationale": "missing keep"}', '{"keep": "yes"}'],
)
def test_parse_reply_malformed_forces_reject(reply):
    v = parse_curator_reply(reply)
    assert v.keep is False and v.error is not None


def test_parse_reply_truncates_long_rationale():
    v = parse_curator_reply('{"keep": true, "rationale": "%s"}' % ("x" * 1000))
    assert len(v.rationale) == 500


# --- _read_snippet boundary ------------------------------------------------------


def test_read_snippet_reads_in_repo_file(tmp_path):
    from membench.oracle.curator import _read_snippet

    (tmp_path / "a.ts").write_text("inside", encoding="utf-8")
    assert _read_snippet(tmp_path, "a.ts") == "inside"


def test_read_snippet_rejects_path_traversal(tmp_path):
    from membench.oracle.curator import _read_snippet

    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _read_snippet(repo, "../secret.txt") == ""


# --- StubOracleCurator / ClaudeOracleCurator -------------------------------------


def test_stub_curator_needs_exactly_one_mode():
    with pytest.raises(ValueError):
        StubOracleCurator()
    with pytest.raises(ValueError):
        StubOracleCurator(keep=True, fn=lambda p: p)


def test_claude_curator_unwraps_cli_json():
    def runner(argv, **kw):
        return _completed(stdout='{"result": "{\\"keep\\": true, \\"rationale\\": \\"r\\"}"}')

    cur = ClaudeOracleCurator(runner=runner)
    assert parse_curator_reply(cur.complete("p")).keep


def test_claude_curator_nonzero_exit_raises():
    cur = ClaudeOracleCurator(runner=lambda argv, **kw: _completed(returncode=2, stderr="boom"))
    with pytest.raises(OracleCuratorError):
        cur.complete("p")


def test_claude_curator_missing_binary_raises():
    def runner(argv, **kw):
        raise FileNotFoundError("no claude")

    with pytest.raises(OracleCuratorError):
        ClaudeOracleCurator(runner=runner).complete("p")


def test_claude_curator_timeout_raises():
    def runner(argv, **kw):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1)

    with pytest.raises(OracleCuratorError):
        ClaudeOracleCurator(runner=runner).complete("p")


def test_claude_curator_passes_model_when_pinned():
    seen: dict = {}

    def runner(argv, **kw):
        seen["argv"] = argv
        return _completed(stdout='{"keep": true}')

    ClaudeOracleCurator(model="haiku", runner=runner).complete("p")
    assert "--model" in seen["argv"] and "haiku" in seen["argv"]


# --- GrepResolver ----------------------------------------------------------------


def test_grep_resolver_lists_matches():
    r = GrepResolver(runner=lambda argv, **kw: _completed(stdout="a.ts\nb.ts\n"))
    res = r.resolve("sym", defining_file="s.ts", repo_root=REPO)
    assert res.available and res.files == frozenset({"a.ts", "b.ts"})


def test_grep_resolver_no_match_is_empty_available():
    r = GrepResolver(runner=lambda argv, **kw: _completed(returncode=1))
    res = r.resolve("sym", defining_file="s.ts", repo_root=REPO)
    assert res.available and res.files == frozenset()


def test_grep_resolver_real_error_is_unavailable():
    r = GrepResolver(runner=lambda argv, **kw: _completed(returncode=128, stderr="not a git repo"))
    res = r.resolve("sym", defining_file="s.ts", repo_root=REPO)
    assert not res.available and "not a git repo" in (res.error or "")


def test_grep_resolver_git_missing_is_unavailable():
    def runner(argv, **kw):
        raise FileNotFoundError("git")

    res = GrepResolver(runner=runner).resolve("sym", defining_file="s.ts", repo_root=REPO)
    assert not res.available


# --- SourcegraphResolver ---------------------------------------------------------


def test_sg_resolver_unavailable_without_repo():
    res = SourcegraphResolver().resolve("sym", defining_file="s.ts", repo_root=REPO)
    assert not res.available and "sg_repo" in (res.error or "")


def test_sg_resolver_unavailable_without_endpoint_env():
    res = SourcegraphResolver(sg_repo="github.com/x/y", env={}).resolve(
        "sym", defining_file="s.ts", repo_root=REPO
    )
    assert not res.available and ENV_SG_ENDPOINT in (res.error or "")


def test_sg_resolver_parses_results_with_injected_runner():
    env = {ENV_SG_ENDPOINT: "https://sg", ENV_SG_TOKEN: "tok"}
    stdout = '{"Results": [{"file": "a.ts"}, {"path": "b.ts"}, {"nope": 1}]}'
    r = SourcegraphResolver(
        sg_repo="github.com/x/y", env=env, runner=lambda argv, **kw: _completed(stdout=stdout)
    )
    res = r.resolve("sym", defining_file="s.ts", repo_root=REPO)
    assert res.available and res.files == frozenset({"a.ts", "b.ts"})


def test_sg_resolver_quotes_multiword_symbol_in_query():
    # An unquoted content:<symbol> would split a multi-word stem into two terms;
    # the query must wrap the symbol in double quotes.
    env = {ENV_SG_ENDPOINT: "https://sg", ENV_SG_TOKEN: "tok"}
    seen: dict = {}

    def runner(argv, **kw):
        seen["argv"] = argv
        return _completed(stdout='{"Results": []}')

    SourcegraphResolver(sg_repo="github.com/x/y", env=env, runner=runner).resolve(
        "build store", defining_file="s.ts", repo_root=REPO
    )
    query = next(a for a in seen["argv"] if "content:" in a)
    assert 'content:"build store"' in query


def test_sg_resolver_non_json_is_unavailable():
    env = {ENV_SG_ENDPOINT: "https://sg", ENV_SG_TOKEN: "tok"}
    r = SourcegraphResolver(
        sg_repo="r", env=env, runner=lambda argv, **kw: _completed(stdout="<html>")
    )
    res = r.resolve("sym", defining_file="s.ts", repo_root=REPO)
    assert not res.available


# --- build_oracle_context --------------------------------------------------------


def _two_resolvers(symbol_map_grep, symbol_map_sg):
    return [StubResolver("grep", symbol_map_grep), StubResolver("sourcegraph", symbol_map_sg)]


def test_build_required_from_gold_diff_plus_consensus_context():
    resolvers = _two_resolvers(
        {"writer": frozenset({"src/store/writer.ts", "src/store/schema.ts"})},
        {"writer": frozenset({"src/store/writer.ts", "src/store/schema.ts"})},
    )
    ob = build_oracle_context(
        modified_files=["src/store/writer.ts"], repo_root=REPO, resolvers=resolvers, threshold=0.5
    )
    answer = dict(ob.oracle.oracle_tiers)
    assert answer["src/store/writer.ts"] == "required"  # gold-diff modified
    assert answer["src/store/schema.ts"] == "required"  # Tier-1 consensus
    assert GOLD_DIFF_BACKEND in ob.oracle.oracle_backends_consensus
    assert not ob.symbol_quarantines


def test_build_excludes_gold_file_re_found_in_divergent_shape():
    # ORACLE-LEAK GUARD: a backend re-finding the modified file in a different path
    # shape (absolute / ./-prefixed) must NOT slip past the self-exclusion and
    # re-enter the oracle as context -- it stays required-from-ground-truth only.
    resolvers = _two_resolvers(
        {"writer": frozenset({"/repo/src/store/writer.ts", "src/store/dep.ts"})},
        {"writer": frozenset({"./src/store/writer.ts", "src/store/dep.ts"})},
    )
    ob = build_oracle_context(
        modified_files=["src/store/writer.ts"],
        repo_root=Path("/repo"),
        resolvers=resolvers,
        threshold=0.5,
    )
    tiers = dict(ob.oracle.oracle_tiers)
    # The gold file appears exactly once, as required ground truth -- not duplicated
    # under a divergent shape, not downgraded to context.
    assert tiers["src/store/writer.ts"] == "required"
    assert [p for p in ob.oracle.oracle_answer if p.endswith("writer.ts")] == [
        "src/store/writer.ts"
    ]
    assert tiers["src/store/dep.ts"] == "required"  # the genuine Tier-1 consensus context


def test_build_symbol_quarantine_on_single_backend():
    resolvers = [StubResolver("grep", {"writer": frozenset({"a.ts"})})]
    ob = build_oracle_context(
        modified_files=["src/store/writer.ts"], repo_root=REPO, resolvers=resolvers
    )
    # The modified file is still required; no context admitted; symbol quarantined.
    assert ob.oracle.oracle_answer == ("src/store/writer.ts",)
    assert ob.symbol_quarantines and "single_backend" in ob.symbol_quarantines[0].reason


def test_build_tier2_supplementary_via_curator(tmp_path):
    repo = _repo_with(tmp_path, "src/store/writer.ts", "helper.ts")
    resolvers = _two_resolvers(
        {"writer": frozenset({"src/store/writer.ts", "helper.ts"})},
        {"writer": frozenset({"src/store/writer.ts"})},
    )
    ob = build_oracle_context(
        modified_files=["src/store/writer.ts"],
        repo_root=repo,
        resolvers=resolvers,
        curator=StubOracleCurator(keep=True),
        threshold=0.4,
    )
    assert dict(ob.oracle.oracle_tiers)["helper.ts"] == "supplementary"


def test_build_volume_guard_truncates_supplementary_first(tmp_path):
    supp = {f"ctx{i}.ts" for i in range(10)}
    repo = _repo_with(tmp_path, "src/store/writer.ts", *supp)
    resolvers = _two_resolvers(
        {"writer": frozenset({"src/store/writer.ts"}) | supp},
        {"writer": frozenset({"src/store/writer.ts"})},
    )
    ob = build_oracle_context(
        modified_files=["src/store/writer.ts"],
        repo_root=repo,
        resolvers=resolvers,
        curator=StubOracleCurator(keep=True),
        threshold=0.0,
        max_oracle_files=3,
    )
    assert len(ob.oracle.oracle_answer) == 3
    assert "src/store/writer.ts" in ob.oracle.oracle_answer  # required never dropped
    assert ob.truncated == 8
    # the 8 dropped supplementary paths are RECORDED, never silently lost
    truncated_q = [p for p, why in ob.file_quarantines if "volume_guard" in why]
    assert len(truncated_q) == 8


def test_build_skips_dotfile_seed():
    # A dotfile's stem (".env") is no module identity -> no consensus call, but the
    # dotfile itself is still required ground truth.
    resolvers = _two_resolvers({}, {})
    ob = build_oracle_context(modified_files=[".env"], repo_root=REPO, resolvers=resolvers)
    assert ob.oracle.oracle_answer == (".env",)
    assert not ob.symbol_quarantines  # skipped before consensus, not quarantined


def test_build_empty_modified_files_yields_empty_oracle():
    ob = build_oracle_context(modified_files=[], repo_root=REPO, resolvers=[StubResolver("grep")])
    assert ob.oracle.oracle_answer == () and ob.oracle.oracle_backends_consensus == ()


def test_build_provenance_drops_backend_only_in_truncated_files(tmp_path):
    # 'x.ts' is grep-only (Tier-2 supplementary, LLM-kept); truncating it away must
    # drop grep/sourcegraph from provenance, leaving only the gold-diff required file.
    repo = _repo_with(tmp_path, "src/store/writer.ts", "x.ts")
    resolvers = _two_resolvers(
        {"writer": frozenset({"src/store/writer.ts", "x.ts"})},
        {"writer": frozenset({"src/store/writer.ts"})},
    )
    ob = build_oracle_context(
        modified_files=["src/store/writer.ts"],
        repo_root=repo,
        resolvers=resolvers,
        curator=StubOracleCurator(keep=True),
        threshold=0.4,
        max_oracle_files=1,
    )
    assert ob.truncated == 1
    assert ob.oracle.oracle_backends_consensus == (GOLD_DIFF_BACKEND,)


def test_build_provenance_unions_backends_across_symbols(tmp_path):
    # 'shared.ts' is kept grep-only for module 'writera' and sourcegraph-only for
    # module 'writerb'. Its provenance must UNION both backends, not keep only the
    # last contributor's (the build.py:185 overwrite bug Codex flagged).
    repo = _repo_with(tmp_path, "src/writera.ts", "src/writerb.ts", "shared.ts")
    resolvers = [
        StubResolver(
            "grep",
            {
                "writera": frozenset({"src/writera.ts", "shared.ts"}),
                "writerb": frozenset({"src/writerb.ts"}),
            },
        ),
        StubResolver(
            "sourcegraph",
            {
                "writera": frozenset({"src/writera.ts"}),
                "writerb": frozenset({"src/writerb.ts", "shared.ts"}),
            },
        ),
    ]
    ob = build_oracle_context(
        modified_files=["src/writera.ts", "src/writerb.ts"],
        repo_root=repo,
        resolvers=resolvers,
        curator=StubOracleCurator(keep=True),
        threshold=0.4,
    )
    assert dict(ob.oracle.oracle_tiers)["shared.ts"] == "supplementary"
    assert ob.oracle.oracle_backends_consensus == ("gold_diff", "grep", "sourcegraph")


def test_build_rejects_bad_max_oracle_files():
    with pytest.raises(ValueError):
        build_oracle_context(
            modified_files=["a.ts"],
            repo_root=REPO,
            resolvers=[StubResolver("grep")],
            max_oracle_files=0,
        )


# --- curate_bundle (immutability + wiring) ---------------------------------------


def _bundle(file_diffs: dict[str, str]) -> TaskBundle:
    replay = ReplayResult(
        calls=(
            CallReplay(
                index=0,
                tool="Edit",
                path="/orig/src/store/writer.ts",
                rebased_path="/co/src/store/writer.ts",
                outcome=ReplayOutcome.APPLIED,
            ),
        ),
        file_diffs=file_diffs,
        replay_success_rate=1.0,
    )
    return TaskBundle(
        work_id="mem-test",
        rig="mem",
        issue_title="t",
        trace_ref="/tmp/trace.jsonl",
        output=replay,
        env=BundleEnv(repo="x/y", base_commit="c1", base_image="img"),
        loo_excluded_work_ids=("mem-test",),
    )


def test_curate_bundle_returns_new_bundle_with_oracle():
    bundle = _bundle({"src/store/writer.ts": "diff ..."})
    assert bundle.oracle_context is None
    resolvers = _two_resolvers(
        {"writer": frozenset({"src/store/writer.ts", "src/store/schema.ts"})},
        {"writer": frozenset({"src/store/writer.ts", "src/store/schema.ts"})},
    )
    new_bundle, build = curate_bundle(bundle, REPO, resolvers=resolvers, threshold=0.5)
    assert bundle.oracle_context is None  # original untouched
    assert new_bundle.oracle_context is not None
    assert "src/store/schema.ts" in new_bundle.oracle_context.oracle_answer
    assert build.oracle is new_bundle.oracle_context
