"""Unit tests for the E0a memory-verb base rate (bead mem-e4fby).

The properties under test are the ones that make the published numbers mean what
the report says they mean:

- **Bucket assignment is grammar-only.** `bd recall <key>` is a targeted read,
  `bd memories <term>` is a search, and bare `bd memories` is a list-all BROWSE
  that carries no key. Only the targeted bucket can enter the read-after-write
  join, so collapsing the three into one "read rate" would inflate apparent
  retrieval with traffic that is join-ineligible by construction.
- **The write ambiguity band is real, not decorative.** Only a `--key` form
  names the memory it stores; the shipped CLI auto-generates the key from the
  positional content, so no positional may be read as a key.
- **Redirections never reach argv.** `shlex` emits `2>/dev/null` as ONE token, so
  a token-level redirect test lets the target through as a positional. Stripping
  runs on the raw command text, before tokenization, and a `>` inside a quoted
  memory body must survive it.
- **The join is cross-session.** A read whose only prior write is in the SAME
  session must not count as a cross-session hit, or RAW measures continuation
  rather than carry-over.
- **The ZFC gate itself.** A verb token appearing outside the verb tables would
  mean some layer started reading meaning out of text; that is a bead failure,
  so it is asserted here rather than left to a reviewer's grep.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.paths import REPO

E0 = REPO / "results" / "memory-use" / "e0"


def _load(name: str) -> ModuleType:
    """Import an E0 module by path without leaving bytecode in the results tree."""
    if str(E0) not in sys.path:
        sys.path.insert(0, str(E0))
    spec = importlib.util.spec_from_file_location(name, E0 / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


cligrammar = _load("cligrammar")
verbs = _load("verbs")
rates = _load("rates")


def argv(command: str) -> list[str]:
    """The production path from a shell line to one bd argv (the classified form)."""
    found = list(cligrammar.bd_invocations(command))
    assert len(found) == 1, command
    return found[0][0]


def raw_argv(command: str) -> list[str]:
    """The same command tokenized WITHOUT the redirection strip."""
    found = list(cligrammar.bd_invocations(command))
    assert len(found) == 1, command
    return found[0][1]


# --- bucket assignment --------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "bucket"),
    [
        ("bd recall deploy-runbook", verbs.TARGETED_READ),
        ("bd recall --key deploy-runbook", verbs.TARGETED_READ),
        ("bd memories rollback", verbs.SEARCH_READ),
        ("bd memories", verbs.BROWSE_READ),
        ("bd recall", verbs.BROWSE_READ),
        # Two positionals: the shipped usage line declares one, so the binary
        # refuses it and nothing is stored (A1.8).
        (
            "bd remember deploy-runbook 'drain the queue first'",
            verbs.REJECTED_BY_SHIPPED_GRAMMAR,
        ),
        # One positional, no key: content, or a bare key the shipped help says is
        # RECALLED instead. Grammar cannot tell (A1.9).
        ("bd remember body 2>/dev/null", verbs.BARE_KEY_AMBIGUOUS),
        ("bd remember --key k body", verbs.MEMORY_WRITE),
        # Arity is refused whether or not a key flag is present: the key says where
        # to put it, not what to put.
        ("bd remember --key k", verbs.REJECTED_BY_SHIPPED_GRAMMAR),
        ("bd remember", verbs.REJECTED_BY_SHIPPED_GRAMMAR),
        (
            "bd remember --get deploy-runbook 2>/dev/null",
            verbs.ATTEMPTED_READ_VIA_WRITE_VERB,
        ),
        ("bd remember --show deploy-runbook", verbs.ATTEMPTED_READ_VIA_WRITE_VERB),
        ("bd remember --list", verbs.ATTEMPTED_READ_VIA_WRITE_VERB),
        ("bd forget deploy-runbook", verbs.MEMORY_WRITE),
        ("bd prime", verbs.INJECTION),
        ("bd link mem-1a2b mem-3c4d", verbs.DEP_WRITE),
        ("bd dep add mem-1a2b mem-3c4d", verbs.DEP_WRITE),
        ("bd ready", verbs.OTHER),
    ],
)
def test_bucket_assignment(command: str, bucket: str) -> None:
    assert verbs.classify(argv(command)).bucket == bucket


def test_bare_list_all_carries_no_key_and_cannot_join() -> None:
    """The BROWSE bucket is join-ineligible by construction, not by measurement."""
    assert verbs.classify(argv("bd memories")).key is None
    assert verbs.classify(argv("bd recall")).key is None
    assert verbs.classify(argv("bd recall")).browse_from_bare_targeted is True
    # ...while the search bucket carries a term but still no key.
    assert verbs.classify(argv("bd memories rollback")).key is None


def test_a_value_flag_does_not_swallow_the_positional_that_makes_it_a_search() -> None:
    """`--limit 5 term` is a search; a boolean flag must not eat the term either."""
    assert verbs.classify(argv("bd memories --limit 5 rollback")).bucket == verbs.SEARCH_READ
    assert verbs.classify(argv("bd memories --json rollback")).bucket == verbs.SEARCH_READ
    assert verbs.classify(argv("bd memories --json")).bucket == verbs.BROWSE_READ


# --- redirections --------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("bd remember body 2>/dev/null", ["bd", "remember", "body"]),
        ("bd remember --get k 2>/dev/null", ["bd", "remember", "--get", "k"]),
        ("bd recall k > out.txt", ["bd", "recall", "k"]),
        ("bd recall k >>log 2>&1", ["bd", "recall", "k"]),
        ("bd memories 2>&1", ["bd", "memories"]),
        ("bd memories < terms.txt", ["bd", "memories"]),
    ],
)
def test_a_redirection_never_survives_into_argv(command: str, expected: list[str]) -> None:
    """`shlex` emits an attached redirection as one token; argv must not see it."""
    assert argv(command) == expected


def test_a_redirect_character_inside_a_quoted_argument_survives() -> None:
    """The stripper is quote-aware, so a `>` in content is content."""
    assert argv("bd remember --key k 'a > b'") == ["bd", "remember", "--key", "k", "a > b"]


def test_a_digit_that_is_part_of_a_word_is_not_a_file_descriptor() -> None:
    assert argv("bd-memory-ordering-5877 ready >out") == ["bd-memory-ordering-5877", "ready"]


def test_a_redirected_write_is_not_promoted_out_of_the_ambiguity_band() -> None:
    """The regression the rework exists for: the target was counted as a key."""
    result = verbs.classify(argv("bd remember body 2>/dev/null"))
    assert result.unambiguous is False
    assert result.key is None


def test_a_redirected_attempted_read_cannot_manufacture_a_join_hit() -> None:
    """The write verb with an undeclared flag is a READ attempt, never a prior write."""
    tally = rates.Tally()
    _feed(tally, "s1", "/rig/a", "2026-01-01T00:00:00Z", "bd remember --get carried 2>/dev/null")
    _feed(tally, "s2", "/rig/b", "2026-01-01T00:00:04Z", "bd recall carried")
    result = rates.join(tally)
    assert result["keyed_targeted_reads"] == 1
    assert result["hits"]["any"] == 0
    # ...and it is on the READ side of the widened denominator, not the write side.
    assert result["denominator_choice"]["widened_result"]["keyed_reads"] == 2
    assert result["denominator_choice"]["widened_result"]["hits"]["any"] == 0


# --- write ambiguity band -----------------------------------------------------


def test_only_an_explicit_key_flag_resolves_a_write_key() -> None:
    """The shipped CLI auto-generates the key from content, so a positional is not one.

    The removal verb is used here because it is the write verb whose single
    positional the shipped help does NOT document a recall clause for, so the
    key-resolution rule can be exercised without the A1.9 ambiguity on top of it.
    """
    result = verbs.classify(argv("bd forget deploy-runbook"))
    assert result.bucket == verbs.MEMORY_WRITE
    assert result.unambiguous is False
    assert result.key is None


def test_flag_key_write_resolves_to_the_flag_value_not_the_body() -> None:
    result = verbs.classify(argv("bd remember --key deploy-runbook 'drain the queue first'"))
    assert result.unambiguous is True
    assert result.key == "deploy-runbook"


def test_whitespace_content_write_is_still_a_write() -> None:
    """Judging whether content is 'empty' would mean reading it. Count, don't read."""
    result = verbs.classify(argv("bd remember --key deploy-runbook '   '"))
    assert result.bucket == verbs.MEMORY_WRITE
    assert result.unambiguous is True
    assert result.key == "deploy-runbook"


def test_single_positional_capture_is_its_own_bucket_and_supplies_no_key() -> None:
    """A1.9: the shipped help documents this shape as a RECALL when the argument
    names an existing memory, so grammar cannot call it a write or a read."""
    result = verbs.classify(argv("bd remember 'drain the queue first'"))
    assert result.bucket == verbs.BARE_KEY_AMBIGUOUS
    assert result.unambiguous is False
    assert result.key is None


def test_the_removal_verb_has_no_bare_key_ambiguity() -> None:
    """Its positional is unambiguously a key it deletes, so it stays a write - but
    a removal can never be the WRITER half of a join, so it carries no key."""
    result = verbs.classify(argv("bd forget deploy-runbook"))
    assert result.bucket == verbs.MEMORY_WRITE
    assert result.unambiguous is False
    assert result.key is None


def test_a_global_value_flag_does_not_hide_the_subcommand() -> None:
    """`--db X` consumes X; without that, the subcommand scan stops on the value."""
    result = verbs.classify(argv("bd --db /tmp/x.db remember --key k body"))
    assert result.bucket == verbs.MEMORY_WRITE
    assert result.key == "k"
    assert verbs.classify(argv("bd -C /tmp/rig remember body")).bucket == verbs.BARE_KEY_AMBIGUOUS


def test_write_rate_is_published_as_a_band() -> None:
    tally = rates.Tally()
    _feed(tally, "s1", "/rig/a", "2026-01-01T00:00:00Z", "bd remember --key k body")
    _feed(tally, "s1", "/rig/a", "2026-01-01T00:01:00Z", "bd remember body-only")
    _feed(tally, "s1", "/rig/a", "2026-01-01T00:02:00Z", "bd ready")
    _feed(tally, "s1", "/rig/a", "2026-01-01T00:03:00Z", "bd ready")
    band = rates.write_band(tally)
    assert band["counts"] == {
        "unambiguous": 1,
        "ambiguous": 0,
        "bare_key_ambiguous": 1,
        "rejected_by_shipped_grammar": 0,
    }
    assert band["unambiguous"]["session_averaged_share"] == pytest.approx(0.25)
    assert band["ambiguity_band_high"]["session_averaged_share"] == pytest.approx(0.25)
    # The OUTER band adds the bare capture, which may not be a write at all.
    assert band["bare_key_band_high"]["session_averaged_share"] == pytest.approx(0.50)


# --- the join -----------------------------------------------------------------


def _feed(tally: object, session: str, cwd: str, ts: str, command: str) -> None:
    for found, raw in cligrammar.bd_invocations(command):
        rates.record_invocation(found, ts, session, cwd, "2099-01-01T00:00:00Z", tally, raw)


def test_cross_session_read_after_write_counts_but_within_session_does_not() -> None:
    tally = rates.Tally()
    # written in s1 / rig a, read back in s2 / rig b -> a cross-session, cross-cwd hit
    _feed(tally, "s1", "/rig/a", "2026-01-01T00:00:00Z", "bd remember --key carried body")
    _feed(tally, "s2", "/rig/b", "2026-01-02T00:00:00Z", "bd recall carried")
    # written and read inside s3 -> a near miss: any-hit, but no cross-session hit
    _feed(tally, "s3", "/rig/c", "2026-01-03T00:00:00Z", "bd remember --key local body")
    _feed(tally, "s3", "/rig/c", "2026-01-03T00:05:00Z", "bd recall local")

    result = rates.join(tally)
    assert result["keyed_targeted_reads"] == 2
    assert result["hits"] == {"any": 2, "cross_session": 1, "cross_working_directory": 1}
    assert result["raw_cross_session"] == pytest.approx(0.5)


def test_a_read_before_its_write_is_not_a_hit() -> None:
    """Ordering is by corpus time; a later write cannot explain an earlier read."""
    tally = rates.Tally()
    _feed(tally, "s2", "/rig/b", "2026-01-01T00:00:00Z", "bd recall carried")
    _feed(tally, "s1", "/rig/a", "2026-01-02T00:00:00Z", "bd remember --key carried body")
    assert rates.join(tally)["hits"]["any"] == 0


def test_ambiguous_write_supplies_no_key_so_it_cannot_manufacture_a_hit() -> None:
    tally = rates.Tally()
    _feed(tally, "s1", "/rig/a", "2026-01-01T00:00:00Z", "bd remember carried")
    _feed(tally, "s2", "/rig/b", "2026-01-02T00:00:00Z", "bd recall carried")
    assert rates.join(tally)["hits"]["any"] == 0


def test_search_and_browse_reads_stay_out_of_the_join_denominator() -> None:
    tally = rates.Tally()
    _feed(tally, "s1", "/rig/a", "2026-01-01T00:00:00Z", "bd memories carried")
    _feed(tally, "s1", "/rig/a", "2026-01-01T00:01:00Z", "bd memories")
    assert rates.join(tally)["keyed_targeted_reads"] == 0


# --- exclusions ---------------------------------------------------------------


def test_invocations_at_or_after_the_preregistration_lock_are_excluded_and_counted() -> None:
    tally = rates.Tally()
    lock = "2026-09-01T17:38:07Z"
    rates.record_invocation(
        ["bd", "remember", "k", "body"], "2026-09-01T17:38:07Z", "self", "/mem", lock, tally
    )
    rates.record_invocation(
        ["bd", "remember", "k", "body"], "2026-08-01T00:00:00Z", "prior", "/mem", lock, tally
    )
    assert tally.excluded_after_prereg_lock == 1
    assert tally.invocations == 1


def test_the_lock_is_screened_before_every_other_exclusion() -> None:
    """A post-lock help invocation must not move a published exclusion count."""
    tally = rates.Tally()
    lock = "2026-09-01T17:38:07Z"
    rates.record_invocation(
        ["bd", "remember", "--help"], "2026-09-02T00:00:00Z", "self", "/mem", lock, tally
    )
    assert tally.excluded_after_prereg_lock == 1
    assert tally.skipped["help_invocation"] == 0


# --- ZFC gate -----------------------------------------------------------------


def test_verb_tokens_appear_only_in_the_verb_tables() -> None:
    """Acceptance criterion, asserted rather than left to a reviewer's grep."""
    pattern = re.compile(r"remember|recall|memories")
    offenders: list[str] = []
    # The E0a classification layer only. E0b's injection.py names the verbs in its
    # prose and in the labels it emits, and is held to the property that matters
    # for it - no matcher keyed on a verb token - by its own gate in
    # tests/test_e0_injection.py.
    for path in sorted(E0.glob("*.py")):
        if path.name == "injection.py":
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if pattern.search(line):
                offenders.append(f"{path.name}: {line.strip()}")
    # Anchored on file and text, not on line numbers: a line number moves whenever a
    # comment is edited, which would fail this gate for a reason it is not about.
    assert offenders == [
        'verbs.py: TARGETED_READ_VERBS = {"recall"}',
        'verbs.py: SEARCH_OR_BROWSE_VERBS = {"memories"}',
        'verbs.py: MEMORY_WRITE_VERBS = {"remember", "forget"}',
        'verbs.py: WRITE_VERB_REQUIRED_POSITIONALS = {"remember": 1, "forget": 1}',
        'verbs.py: BARE_KEY_RECALL_VERBS = {"remember"}',
    ]


def test_no_module_reads_the_body_of_a_write() -> None:
    """Positive control for the gate above: the classifier sees a key, never a body.

    A classifier that inspected content would have to differ on two invocations
    whose bodies differ and whose grammar does not. It must not.
    """
    a = verbs.classify(argv("bd remember k 'the queue must be drained first'"))
    b = verbs.classify(argv("bd remember k 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'"))
    assert (a.bucket, a.unambiguous, a.key) == (b.bucket, b.unambiguous, b.key)


# --- A1.4: the write verb carrying a flag the shipped binary does not declare --


def test_the_supported_flag_set_is_the_one_the_shipped_help_declares() -> None:
    """The pinned literal must equal what the captured help text says, or it drifts.

    Derivation is mechanical and re-runnable: `shipped-cli-help/capture.sh` writes
    the help text, `help_flag_names` reads flag NAMES out of it by line grammar.
    Re-deriving here (from the committed files, never from the live binary) is what
    keeps the analysis path hermetic without letting the literal rot.
    """
    captured = sorted(E0.glob("shipped-cli-help/*.help.txt"))
    assert len(captured) == 2, "both write verbs' help texts must be committed"
    derived: set[str] = set()
    for path in captured:
        derived |= cligrammar.help_flag_names(path.read_text(encoding="utf-8"))
    assert derived == verbs.WRITE_VERB_SUPPORTED_FLAGS
    # The specific facts the reclassification rests on.
    assert "--key" in derived
    assert derived.isdisjoint({"--show", "--get", "--list"})
    assert "-k" not in derived, "the shipped binary declares only the long key flag"


def test_help_flag_names_reads_names_only_and_ignores_prose() -> None:
    text = "Usage:\n  bd x --not-a-flag\n\nFlags:\n  -k, --key string   set --nope\n"
    assert cligrammar.help_flag_names(text) == {"-k", "--key"}


def test_an_undeclared_flag_moves_a_write_verb_out_of_the_write_bucket() -> None:
    """The defect this amendment exists for: 38 of 59 'writes' were read attempts."""
    result = verbs.classify(argv("bd remember --show deploy-runbook"))
    assert result.bucket == verbs.ATTEMPTED_READ_VIA_WRITE_VERB
    assert result.key == "deploy-runbook"
    assert result.unambiguous is True
    # A declared flag leaves the invocation where the rest of the grammar puts it.
    assert verbs.classify(argv("bd remember --json --key k body")).bucket == verbs.MEMORY_WRITE


def test_an_undeclared_flag_without_a_key_still_leaves_the_write_bucket() -> None:
    result = verbs.classify(argv("bd remember --list"))
    assert result.bucket == verbs.ATTEMPTED_READ_VIA_WRITE_VERB
    assert result.key is None
    assert result.unambiguous is False


def test_attempted_reads_are_in_the_memory_share_but_not_in_the_write_band() -> None:
    """E0.5 counts reaches at the memory surface; E0.1 counts stores."""
    assert verbs.ATTEMPTED_READ_VIA_WRITE_VERB in verbs.MEMORY_BUCKETS
    tally = rates.Tally()
    _feed(tally, "s1", "/rig/a", "2026-01-01T00:00:00Z", "bd remember --show k")
    _feed(tally, "s1", "/rig/a", "2026-01-01T00:01:00Z", "bd ready")
    assert rates.write_band(tally)["counts"] == {
        "unambiguous": 0,
        "ambiguous": 0,
        "bare_key_ambiguous": 0,
        "rejected_by_shipped_grammar": 0,
    }
    assert rates.session_rate(tally, verbs.MEMORY_BUCKETS)["session_averaged_share"] == 0.5


def test_an_attempted_read_never_supplies_a_prior_write_to_the_join() -> None:
    """It stores nothing, so it must not be joinable from the WRITE side either."""
    tally = rates.Tally()
    _feed(tally, "s1", "/rig/a", "2026-01-01T00:00:00Z", "bd remember --show carried")
    _feed(tally, "s2", "/rig/b", "2026-01-02T00:00:00Z", "bd recall carried")
    result = rates.join(tally)
    assert result["hits"]["any"] == 0
    assert result["denominator_choice"]["widened_result"]["hits"]["any"] == 0


# --- A1.5: the placeholder screen, restored ------------------------------------


def test_a_placeholder_argument_is_screened_even_when_it_looks_like_a_redirection() -> None:
    """`<key>` is eaten by the redirection strip, so the screen runs on raw argv too."""
    assert raw_argv("bd recall <key>") == ["bd", "recall", "<key>"]
    assert argv("bd recall <key>") == ["bd", "recall"]
    tally = rates.Tally()
    _feed(tally, "s1", "/rig/a", "2026-01-01T00:00:00Z", "bd recall <key>")
    _feed(tally, "s1", "/rig/a", "2026-01-01T00:01:00Z", "bd remember <key> <body>")
    _feed(tally, "s1", "/rig/a", "2026-01-01T00:02:00Z", "bd dep add <from> <to>")
    assert tally.skipped["placeholder_or_template"] == 3
    assert tally.invocations == 0
    assert tally.buckets == {}


def test_a_real_key_is_not_mistaken_for_a_placeholder() -> None:
    tally = rates.Tally()
    _feed(tally, "s1", "/rig/a", "2026-01-01T00:00:00Z", "bd recall deploy-runbook")
    assert tally.invocations == 1
    assert tally.buckets[verbs.TARGETED_READ] == 1


def test_an_unquoted_comment_is_not_a_positional() -> None:
    assert argv("bd memories # list them all") == ["bd", "memories"]
    assert verbs.classify(argv("bd memories # list them all")).bucket == verbs.BROWSE_READ
    # ...while a quoted `#`, and a `#` inside a word, are content.
    assert argv("bd remember --key k 'a # b'") == ["bd", "remember", "--key", "k", "a # b"]
    assert argv("bd recall mem-1#2") == ["bd", "recall", "mem-1#2"]


# --- A1.6: a join drop is not an exclusion -------------------------------------


def test_an_undated_keyed_event_is_a_join_drop_not_an_exclusion() -> None:
    tally = rates.Tally()
    rates.record_invocation(
        ["bd", "recall", "carried"], "", "s1", "/rig/a", "2099-01-01T00:00:00Z", tally
    )
    assert tally.invocations == 1, "it is counted, so it is not screened out"
    assert tally.buckets[verbs.TARGETED_READ] == 1
    assert tally.skipped["keyed_event_without_timestamp"] == 0
    assert tally.join_eligibility_drops["keyed_event_without_timestamp"] == 1
    assert rates.join(tally)["join_eligibility_drops"] == {"keyed_event_without_timestamp": 1}


# --- A1.7: the raw screen tests only what the strip erases ---------------------


def test_the_raw_screen_catches_the_placeholder_the_strip_ate() -> None:
    """The narrow rule still does A1.5's whole job: `<key>` never reaches a bucket."""
    assert cligrammar.is_skippable_raw(["bd", "recall", "<key>"]) == "placeholder_or_template"
    assert cligrammar.is_skippable_raw(["bd", "recall", "deploy-runbook"]) is None


def test_the_raw_screen_does_not_judge_a_redirection_target() -> None:
    """The defect A1.7 fixes: `$SP/out.txt` survives the raw tokenization but is gone
    from the analysed argv, so screening the raw form for `^$` condemned invocations
    for a token no bucket decision ever sees."""
    command = "bd recall deploy-runbook > $SP/out.txt 2>&1"
    assert raw_argv(command)[-3:] == [">", "$SP/out.txt", "2>&1"]
    assert argv(command) == ["bd", "recall", "deploy-runbook"]
    assert cligrammar.is_skippable_raw(raw_argv(command)) is None
    # ...and the full alternation, which is correct on the ANALYSED form, would not be.
    assert cligrammar.is_skippable(raw_argv(command)) == "placeholder_or_template"
    tally = rates.Tally()
    _feed(tally, "s1", "/rig/a", "2026-01-01T00:00:00Z", command)
    assert tally.buckets[verbs.TARGETED_READ] == 1
    assert tally.skipped["placeholder_or_template"] == 0


def test_the_full_alternation_still_runs_on_the_analysed_argv() -> None:
    """A1.7 narrows the RAW screen only. A template that survives the strip is
    screened exactly as preregistered."""
    tally = rates.Tally()
    _feed(tally, "s1", "/rig/a", "2026-01-01T00:00:00Z", "bd recall $KEY")
    _feed(tally, "s1", "/rig/a", "2026-01-01T00:01:00Z", "bd recall {{key}}")
    assert tally.skipped["placeholder_or_template"] == 2
    assert tally.invocations == 0


# --- A1.8: positional arity, derived from the same captured help ---------------


def test_the_required_positional_count_is_the_one_the_shipped_help_declares() -> None:
    """Mirror of the flag-drift test: the pinned literal must equal what the
    committed usage lines say, re-derived here and never from the live binary."""
    derived = {}
    for path in sorted(E0.glob("shipped-cli-help/*.help.txt")):
        sub_name = path.name.split(".")[0]
        derived[sub_name] = cligrammar.help_usage_positionals(
            path.read_text(encoding="utf-8"), sub_name
        )
    assert derived == verbs.WRITE_VERB_REQUIRED_POSITIONALS
    assert derived == {"remember": 1, "forget": 1}


def test_help_usage_positionals_counts_required_words_only() -> None:
    text = 'Usage:\n  bd remember "<insight>" [flags]\n\nFlags:\n  --key string\n'
    assert cligrammar.help_usage_positionals(text, "remember") == 1
    two = "Usage:\n  bd dep add <from> <to> [flags]\n"
    assert cligrammar.help_usage_positionals(two, "dep") == 3
    with pytest.raises(ValueError):
        cligrammar.help_usage_positionals(text, "forget")


def test_a_keyed_write_with_no_content_stores_nothing_and_leaves_the_write_bucket() -> None:
    """The invocation A1.8 was written for. It is the one the corpus's only
    apparent cross-session join hit was reading back."""
    result = verbs.classify(argv("bd remember --key carried"))
    assert result.bucket == verbs.REJECTED_BY_SHIPPED_GRAMMAR
    assert result.key is None
    assert result.unambiguous is False


def test_a_rejected_write_never_supplies_a_prior_write_to_the_join() -> None:
    tally = rates.Tally()
    _feed(tally, "s1", "/rig/a", "2026-01-01T00:00:00Z", "bd remember --key carried")
    _feed(tally, "s2", "/rig/b", "2026-01-02T00:00:00Z", "bd recall carried")
    result = rates.join(tally)
    assert result["keyed_targeted_reads"] == 1
    assert result["hits"]["any"] == 0
    assert result["denominator_choice"]["widened_result"]["hits"]["any"] == 0


def test_the_refused_and_ambiguous_buckets_are_memory_verbs_but_not_reads() -> None:
    """E0.5 counts reaches at the memory surface and is unmoved by A1.8 and A1.9."""
    assert verbs.REJECTED_BY_SHIPPED_GRAMMAR in verbs.MEMORY_BUCKETS
    assert verbs.BARE_KEY_AMBIGUOUS in verbs.MEMORY_BUCKETS
    assert verbs.REJECTED_BY_SHIPPED_GRAMMAR not in verbs.READ_BUCKETS
    assert verbs.BARE_KEY_AMBIGUOUS not in verbs.READ_BUCKETS
    tally = rates.Tally()
    _feed(tally, "s1", "/rig/a", "2026-01-01T00:00:00Z", "bd remember --key k")
    _feed(tally, "s1", "/rig/a", "2026-01-01T00:01:00Z", "bd remember bare-key")
    _feed(tally, "s1", "/rig/a", "2026-01-01T00:02:00Z", "bd ready")
    _feed(tally, "s1", "/rig/a", "2026-01-01T00:03:00Z", "bd ready")
    share = rates.session_rate(tally, verbs.MEMORY_BUCKETS)["session_averaged_share"]
    assert share == pytest.approx(0.5)


def test_a_bare_capture_bands_both_rates_and_is_inside_neither() -> None:
    """A1.9's publication rule: the same 1 invocation is the outer end of the write
    band and the outer end of the targeted-read rate, and neither point estimate."""
    tally = rates.Tally()
    _feed(tally, "s1", "/rig/a", "2026-01-01T00:00:00Z", "bd remember bare-key")
    _feed(tally, "s1", "/rig/a", "2026-01-01T00:01:00Z", "bd ready")
    band = rates.write_band(tally)
    assert band["unambiguous"]["session_averaged_share"] == 0.0
    assert band["ambiguity_band_high"]["session_averaged_share"] == 0.0
    assert band["bare_key_band_high"]["session_averaged_share"] == pytest.approx(0.5)
    assert rates.session_rate(tally, (verbs.TARGETED_READ,))["session_averaged_share"] == 0.0
    banded = rates.session_rate(tally, (verbs.TARGETED_READ, verbs.BARE_KEY_AMBIGUOUS))
    assert banded["session_averaged_share"] == pytest.approx(0.5)
    assert rates.join(tally)["keyed_targeted_reads"] == 0


# --- the CLI the acceptance criteria name -------------------------------------


def test_cli_emits_the_preregistered_statistics(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    rows = [
        _row("2026-01-01T00:00:00Z", "s1", "/rig/a", "bd remember --key carried body"),
        _row("2026-01-02T00:00:00Z", "s2", "/rig/b", "bd recall carried"),
        _row("2026-01-02T00:01:00Z", "s2", "/rig/b", "bd memories"),
        _row("2026-01-02T00:02:00Z", "s2", "/rig/b", "bd prime"),
        # s3 issues an injection and a dependency edge and NOTHING else, so it is
        # the session that separates "link is a memory verb" from "link is not".
        _row("2026-01-03T00:00:00Z", "s3", "/rig/c", "bd prime"),
        _row("2026-01-03T00:01:00Z", "s3", "/rig/c", "bd link mem-1a2b mem-3c4d"),
    ]
    transcript.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    filelist = tmp_path / "filelist.txt"
    filelist.write_text(f"{transcript}\n", encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(E0 / "rates.py"), "--filelist", str(filelist), "--json"],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["population"]["counted_invocations"] == 6
    assert out["E0.1_memory_write_rate"]["counts"] == {
        "unambiguous": 1,
        "ambiguous": 0,
        "bare_key_ambiguous": 0,
        "rejected_by_shipped_grammar": 0,
    }
    read = out["E0.2_memory_read_rates"]
    assert set(read) == {
        "targeted_read",
        "search_read",
        "browse_read",
        "attempted_read_via_write_verb",
        "attempted_read_note",
        "targeted_read_bare_key_band_high",
        "bare_key_ambiguous",
        "bare_key_ambiguous_note",
        "rejected_by_shipped_grammar",
        "rejected_by_shipped_grammar_note",
        "note",
    }
    assert out["E0.4_read_after_write"]["hits"]["cross_session"] == 1
    # s3 has an injection and a dependency edge and no memory verb, so it must not
    # appear here: `bd link` is `bd dep add` shorthand, not a memory verb.
    assert out["E0.5_memory_verb_share_of_bd_traffic"]["sessions_with_at_least_one"] == 2
    assert out["E0.5_memory_verb_share_of_bd_traffic"]["session_prevalence"] == pytest.approx(2 / 3)
    # both reference buckets are reported, and neither is folded into that share
    assert out["reference_buckets"]["injection"]["sessions_with_at_least_one"] == 2
    assert out["reference_buckets"]["dep_write"]["sessions_with_at_least_one"] == 1
    assert "INSTRUCTED-endogenous" in out["interpretation_label"]
    # A1.10: the frozen group is frozen against corpus GROWTH, not against attrition,
    # and its published name now says so.
    assert set(out["exclusions"]) == {
        "drifting",
        "frozen_against_corpus_growth",
        "frozen_against_corpus_growth_note",
    }
    assert "ATTRITION" in out["exclusions"]["frozen_against_corpus_growth_note"]
    # a join drop is reported with the join, never with the screens
    assert "keyed_event_without_timestamp" not in out["exclusions"]["frozen_against_corpus_growth"]
    assert "join_eligibility_drops" in out["E0.4_read_after_write"]
    assert out["preregistration_amendment_2_sha256"]
    assert out["preregistration_amendment_3_sha256"]


def _row(ts: str, session: str, cwd: str, command: str) -> dict[str, object]:
    return {
        "timestamp": ts,
        "sessionId": session,
        "cwd": cwd,
        "message": {
            "content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": command}},
            ]
        },
    }
