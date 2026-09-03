"""The spawn-and-diagnose ladder suite (mem-o9plh).

Each rung is proven here against the choke point, so a rung's SEMANTICS are
argued once rather than six times against six hand-rolled copies. The ordering
rung (`test_file_not_found_is_not_claimed_by_the_generic_oserror_clause`) is the
reason the module exists: three of the six ladders it replaced omitted the
OSError clause, and no test at those sites would have caught it.

The converted sites deliberately KEEP their own failure tests rather than
deferring to this file. They assert a different thing: that the site wires the
ladder up correctly -- its own error TYPE surfaces and its own install hint is
the one named -- and they do it against REAL subprocesses (an absent path, a
chmod 0o644 binary) at the trust boundary. That is wiring, not rung semantics,
and it is exactly what a shared helper cannot prove on a caller's behalf.
"""

import contextlib
import errno
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from membench import spawn
from membench.spawn import (
    _MIN_REDACTABLE_SECRET_LEN,
    redact_secret,
    run_checked,
    run_in_session,
    timeout_partial_stdout,
)


class _BoomError(RuntimeError):
    """A caller-specific error type -- stands in for MemCliError/OpenWikiCliError."""


def _ok_runner(stdout: str = "out", stderr: str = "", returncode: int = 0):
    def runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    return runner


def _raising_runner(exc: BaseException):
    def runner(argv, **kwargs):
        raise exc

    return runner


def _run(runner, **overrides):
    kwargs = {
        "what": "the thing",
        "not_found_hint": "install the thing",
        "timeout_s": 30.0,
        "runner": runner,
    }
    kwargs.update(overrides)
    return run_checked(["thing", "--go"], **kwargs)


# --------------------------------------------------------------------------- #
# success
# --------------------------------------------------------------------------- #
def test_returns_completed_process_on_zero_exit() -> None:
    completed = _run(_ok_runner(stdout="hello"))
    assert completed.returncode == 0
    assert completed.stdout == "hello"


def test_forwards_spawn_parameters_to_the_runner(tmp_path) -> None:
    seen: dict[str, object] = {}

    def runner(argv, **kwargs):
        seen["argv"] = argv
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "", "")

    _run(runner, cwd=tmp_path, env={"K": "V"}, input="stdin-payload")

    assert seen["argv"] == ["thing", "--go"]
    assert seen["cwd"] == tmp_path
    assert seen["env"] == {"K": "V"}
    assert seen["input"] == "stdin-payload"
    assert seen["timeout"] == 30.0
    # capture_output/text are what make stdout/stderr readable strings; check=False is
    # what keeps the non-zero diagnosis here instead of a CalledProcessError upstack.
    assert seen["capture_output"] is True
    assert seen["text"] is True
    assert seen["check"] is False


# --------------------------------------------------------------------------- #
# rung 1: missing binary -- names the fix, not just the fact
# --------------------------------------------------------------------------- #
def test_missing_binary_names_the_hint_and_the_binary() -> None:
    with pytest.raises(RuntimeError, match="not found") as caught:
        _run(_raising_runner(FileNotFoundError("thing")))
    message = str(caught.value)
    assert "'thing'" in message
    assert "install the thing" in message


def test_missing_binary_diagnosed_against_a_real_spawn(tmp_path) -> None:
    # No stub: let the REAL spawn fail, so the rung is exercised end-to-end rather
    # than against a hand-fed error.
    absent = str(tmp_path / "absent-binary")
    with pytest.raises(RuntimeError, match="not found"):
        run_checked([absent], what="the thing", not_found_hint="install it", timeout_s=30.0)


# --------------------------------------------------------------------------- #
# rung 2: the rest of the OSError family -- THE omission that motivated the sweep
# --------------------------------------------------------------------------- #
def test_permission_error_reaches_the_spawn_clause() -> None:
    boom = PermissionError(errno.EACCES, "Permission denied", "thing")
    with pytest.raises(RuntimeError, match="could not spawn the thing"):
        _run(_raising_runner(boom))


def test_enoexec_reaches_the_spawn_clause() -> None:
    # ENOEXEC (non-executable binary) stays a plain OSError. ENOENT would NOT work
    # here: Python auto-maps it to FileNotFoundError, which the earlier clause claims.
    boom = OSError(errno.ENOEXEC, "Exec format error", "thing")
    with pytest.raises(RuntimeError, match="could not spawn the thing"):
        _run(_raising_runner(boom))


def test_file_not_found_is_not_claimed_by_the_generic_oserror_clause() -> None:
    # THE subclass-ordering trap. FileNotFoundError IS an OSError, so a ladder that
    # catches OSError first silently swallows the install hint. Assert the specific
    # wording wins -- reversing the clauses in spawn.py must fail this test.
    with pytest.raises(RuntimeError) as caught:
        _run(_raising_runner(FileNotFoundError("thing")))
    message = str(caught.value)
    assert "install the thing" in message
    assert "could not spawn" not in message


# --------------------------------------------------------------------------- #
# rung 3: timeout -- not an OSError, needs its own clause
# --------------------------------------------------------------------------- #
def test_timeout_quotes_the_bound_the_exception_carries() -> None:
    boom = subprocess.TimeoutExpired(cmd=["thing"], timeout=30.0)
    with pytest.raises(RuntimeError, match=r"did not finish within 30\.0s"):
        _run(_raising_runner(boom))


def test_timeout_reports_the_enforced_bound_when_the_caller_set_none() -> None:
    # `timeout_s=None` is subprocess's unbounded sentinel; a TimeoutExpired can still
    # arrive from a runner that bounds itself. Quoting `exc.timeout` keeps the message
    # accurate where re-deriving from `timeout_s` would print "None".
    boom = subprocess.TimeoutExpired(cmd=["thing"], timeout=5.0)
    with pytest.raises(RuntimeError, match=r"did not finish within 5\.0s"):
        _run(_raising_runner(boom), timeout_s=None)


# --------------------------------------------------------------------------- #
# rung 4: non-zero exit
# --------------------------------------------------------------------------- #
def test_nonzero_exit_reports_code_and_stderr() -> None:
    with pytest.raises(RuntimeError, match=r"exit 3.*no such rig"):
        _run(_ok_runner(returncode=3, stderr="no such rig"))


def test_nonzero_exit_falls_back_to_stdout_when_stderr_is_empty() -> None:
    with pytest.raises(RuntimeError, match=r"exit 2.*bad flag"):
        _run(_ok_runner(stdout="bad flag", stderr="", returncode=2))


def test_nonzero_exit_falls_back_to_stdout_when_stderr_is_only_whitespace() -> None:
    # A CLI that emits a bare newline on stderr must not blank the diagnosis: the
    # fallback tests stderr AFTER stripping, so the real message on stdout wins.
    with pytest.raises(RuntimeError, match=r"exit 2.*bad flag"):
        _run(_ok_runner(stdout="bad flag", stderr="  \n", returncode=2))


# --------------------------------------------------------------------------- #
# the factory: one ladder, each site's own error TYPE
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "boom",
    [
        FileNotFoundError("thing"),
        PermissionError(errno.EACCES, "Permission denied", "thing"),
        subprocess.TimeoutExpired(cmd=["thing"], timeout=30.0),
    ],
    ids=["missing-binary", "spawn-failure", "timeout"],
)
def test_error_factory_routes_every_raising_rung_to_the_callers_type(boom) -> None:
    with pytest.raises(_BoomError):
        _run(_raising_runner(boom), error=_BoomError)


def test_error_factory_routes_the_nonzero_rung_to_the_callers_type() -> None:
    with pytest.raises(_BoomError, match="exit 1"):
        _run(_ok_runner(returncode=1, stderr="nope"), error=_BoomError)


def test_default_error_type_is_runtime_error() -> None:
    with pytest.raises(RuntimeError) as caught:
        _run(_ok_runner(returncode=1, stderr="nope"))
    assert type(caught.value) is RuntimeError


def test_root_cause_is_chained_for_diagnosis() -> None:
    boom = PermissionError(errno.EACCES, "Permission denied", "thing")
    with pytest.raises(_BoomError) as caught:
        _run(_raising_runner(boom), error=_BoomError)
    assert caught.value.__cause__ is boom


# --------------------------------------------------------------------------- #
# rung 5: the non-zero diagnosis is a LOG artifact -- bound it and redact it
#
# The message this rung builds is printed to the console/CI log by the paid grid
# drivers (`grid_toolreq_builtin.py`'s SWEEP HALT arm), so the child's own output
# reaches a log verbatim. Two properties are asserted here rather than at each call
# site, for the reason the module exists: the child is untrusted output, and every
# hand-rolled copy would have to re-derive both.
#
# Redaction runs AFTER the stderr/stdout selection above, never before: the rung-4
# whitespace test pins that a blank stderr falls through to stdout, and redacting
# first could blank a real message and silently move that selection.
# --------------------------------------------------------------------------- #
def test_an_oauth_token_on_the_childs_stderr_is_redacted() -> None:
    # mem never puts the token on this channel; this fires if the CLI echoes its own
    # auth on failure. Reproduced by the security reviewer with a canary.
    leaky = "AuthenticationError: invalid token sk-ant-oat01-AAAA-BBBB_CC"
    with pytest.raises(RuntimeError) as caught:
        _run(_ok_runner(stderr=leaky, returncode=1))
    assert "sk-ant-oat01-AAAA-BBBB_CC" not in str(caught.value)
    assert "sk-ant" not in str(caught.value)
    assert "AuthenticationError" in str(caught.value)  # the diagnosis survives


def test_an_api_key_shape_is_redacted_too() -> None:
    with pytest.raises(RuntimeError) as caught:
        _run(_ok_runner(stderr="401 from sk-ant-api03-ZZZZ999", returncode=1))
    assert "sk-ant-api03-ZZZZ999" not in str(caught.value)
    assert "401 from" in str(caught.value)


def test_a_non_anthropic_vendor_key_is_redacted_too() -> None:
    """`sk-ant-` was too narrow for the callers that actually exist.

    `openwiki_system._openwiki_env` reads OPENAI_COMPATIBLE_API_KEY off the operator's
    environment and hands it to a `run_checked` child; an OpenAI key is `sk-proj-...`, which
    `sk-ant-` does not match. Reproduced by review against the shipped code, so this is a
    real caller's real shape and not a speculative pattern."""
    with pytest.raises(RuntimeError) as caught:
        _run(
            _ok_runner(
                stderr="401 Unauthorized: invalid api key sk-proj-REALCANARY7788", returncode=1
            )
        )
    assert "sk-proj-REALCANARY7788" not in str(caught.value)
    assert "401 Unauthorized" in str(caught.value)  # the diagnosis survives


def test_a_token_on_stdout_is_redacted_when_stdout_is_the_diagnosis() -> None:
    # `claude -p --output-format stream-json` puts the WHOLE event stream on stdout, so
    # a non-zero exit with an empty stderr routes stdout into the printed exception.
    # Redacting only stderr would cover the half least likely to carry the secret.
    with pytest.raises(RuntimeError) as caught:
        _run(_ok_runner(stdout="boom sk-ant-oat01-LEAK", stderr="", returncode=1))
    assert "sk-ant-oat01-LEAK" not in str(caught.value)
    assert "boom" in str(caught.value)


@pytest.mark.parametrize(
    "diagnosis",
    [
        pytest.param(
            "no task-types.json found at /home/ds/.mem/task-types.json", id="artifact-path"
        ),
        pytest.param("unknown flag --task-types", id="real-flag"),
        pytest.param("disk-usage high, risk-scored run aborted", id="ordinary-words"),
    ],
)
def test_a_word_containing_sk_is_not_mistaken_for_a_credential(diagnosis: str) -> None:
    """`sk-` is a SUBSTRING of this codebase's own vocabulary: taSK-types, diSK-, riSK-.

    Without a word boundary the redaction ate them -- `no task-types.json found` surfaced as
    `no ta<redacted-credential>.json`, swallowing the filename the operator needs, on a real
    flag (`--task-types`) and a real artifact path. Over-redaction is only the "safe
    direction" while it costs nothing to read; a redaction that eats real diagnostics trains
    people to ignore it."""
    with pytest.raises(RuntimeError) as caught:
        _run(_ok_runner(stderr=diagnosis, returncode=1))
    assert diagnosis in str(caught.value)  # survives intact, nothing redacted
    assert "redacted" not in str(caught.value)


def test_an_unbounded_child_output_is_truncated() -> None:
    with pytest.raises(RuntimeError) as caught:
        _run(_ok_runner(stderr="x" * 100_000, returncode=1))
    assert len(str(caught.value)) < 10_000


def test_truncation_keeps_the_head_and_the_tail() -> None:
    # `mem query` and `harbor run` put the actionable line at the END; a head-only
    # window would drop exactly the line the operator needs.
    payload = "FIRST-LINE" + ("x" * 100_000) + "LAST-LINE"
    with pytest.raises(RuntimeError) as caught:
        _run(_ok_runner(stderr=payload, returncode=1))
    assert "FIRST-LINE" in str(caught.value)
    assert "LAST-LINE" in str(caught.value)


def test_a_short_diagnosis_is_passed_through_unmarked() -> None:
    # Truncation must not announce itself on output that was never truncated.
    with pytest.raises(RuntimeError) as caught:
        _run(_ok_runner(stderr="no such rig", returncode=3))
    assert "truncated" not in str(caught.value)


# --------------------------------------------------------------------------- #
# redact_secret -- value-based complement to redact_credentials (mem-zls5s).
# Why value not shape, and the short-value guard, live on the function docstring;
# the cases below exercise each claim.
# --------------------------------------------------------------------------- #
def test_redact_secret_replaces_the_exact_value() -> None:
    token = "sgp_" + "x" * 36
    out = redact_secret(f"401 Unauthorized presenting {token} to the endpoint", token)
    assert token not in out
    assert "401 Unauthorized" in out  # the diagnosis survives


def test_redact_secret_catches_a_shape_the_regex_misses() -> None:
    # A legacy Sourcegraph token is a bare 40-hex string with NO `sgp_` prefix, so
    # `redact_credentials` (sk-/vendor-shaped) would not touch it. The value does.
    legacy = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"
    out = redact_secret(f"auth failed for token {legacy}", legacy)
    assert legacy not in out


def test_redact_secret_skips_empty_so_it_cannot_shred_the_diagnosis() -> None:
    # str.replace("", X) injects X between every character; an unset env var must be a
    # no-op, never a shredder.
    text = "no such repository configured"
    assert redact_secret(text, "") == text
    assert redact_secret(text, "   ") == text


def test_redact_secret_skips_a_too_short_value_to_avoid_over_redaction() -> None:
    # A misconfigured short token (env is operator-controlled and only checked truthy)
    # must not turn into a substring shredder: redacting "ab" would eat "grab". Below the
    # floor we accept the theoretical miss of an implausibly-short secret to protect the
    # diagnosis -- the same both-directions bar the `\b` word-boundary rule holds.
    assert _MIN_REDACTABLE_SECRET_LEN >= 4
    short = "a" * (_MIN_REDACTABLE_SECRET_LEN - 1)
    text = f"grab {short}bra the file"
    assert redact_secret(text, short) == text  # untouched -- no shredding


def test_redact_secret_leaves_unrelated_text_untouched() -> None:
    token = "sgp_" + "z" * 36
    text = "a perfectly ordinary diagnostic line with no secret in it"
    assert redact_secret(text, token) == text


def test_redact_secret_matches_the_child_echo_when_the_env_value_is_padded() -> None:
    # An env token sourced as `$(cat token)` or from a k8s/Vault secret file carries a
    # trailing newline; the child trims it before echoing. Matching the RAW padded value
    # would find nothing and leak the token in full -- the strip on both sides is what
    # closes that gap.
    token = "sgp_" + "d" * 36
    child_echo = f"auth failed presenting {token} (401)"  # the BARE token, as the child prints it
    out = redact_secret(child_echo, f"  {token}\n")  # env value carries whitespace
    assert token not in out
    assert "auth failed" in out  # the diagnosis survives


def test_redact_secret_removes_every_occurrence() -> None:
    token = "sgp_" + "e" * 36
    out = redact_secret(f"{token} retried then {token} again", token)
    assert token not in out


# --------------------------------------------------------------------------- #
# mem-zfm0m item 8: the child runs in its own session and a timeout kills the whole group
# --------------------------------------------------------------------------- #

# A child that forks two grandchildren and then outlives the bound. Their pids come FIRST so the
# partial stdout carries them; "partial" is the line a scorer must still see. One grandchild
# detaches from the pipes and one holds stdout open: the holder is what makes a child-only kill
# visible (the drain behind it blocks until the holder dies), the detached one is what a
# child-only kill leaves running. 600s outlives every bound below by three orders.
_FORKING = [
    "sh",
    "-c",
    "sleep 600 >/dev/null 2>&1 & echo $!; sleep 600 & echo $!; echo partial; wait",
]
_LINUX_ONLY = pytest.mark.skipif(sys.platform != "linux", reason="reads /proc")


def _gone(pid: int) -> bool:
    """Dead or reaped: no /proc entry (or one that vanished between open and read, which the
    kernel reports as ESRCH), or a zombie waiting on nobody."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return True
    return state == "Z"


def _wait_gone(pid: int, seconds: float = 3.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _gone(pid):
            return True
        time.sleep(0.05)
    return _gone(pid)


def _reap(*pids: int) -> None:
    for pid in pids:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, 9)


def _grandchildren(partial: str) -> tuple[int, int]:
    detached, holder = (int(line) for line in partial.splitlines()[:2])
    return detached, holder


@_LINUX_ONLY
def test_run_in_session_kills_the_grandchild_on_timeout() -> None:
    """`subprocess.run` kills the child it spawned and nothing else: a `claude -p` that timed out
    left its own children running against the next leg's sandbox. The child is started as a
    session leader and the timeout signals the GROUP."""
    with pytest.raises(subprocess.TimeoutExpired) as info:
        run_in_session(_FORKING, capture_output=True, text=True, check=False, timeout=0.5)
    grandchildren = _grandchildren(info.value.stdout)
    try:
        for pid in grandchildren:
            assert _wait_gone(pid), f"grandchild {pid} survived the timeout"
    finally:
        _reap(*grandchildren)


@_LINUX_ONLY
def test_a_plain_run_leaves_the_grandchild_alive() -> None:
    """The control for the test above — the behaviour item 8 replaces, kept so the contrast is
    measured rather than assumed. Cleaned up by hand because nothing else will."""
    with pytest.raises(subprocess.TimeoutExpired) as info:
        subprocess.run(_FORKING, capture_output=True, text=True, check=False, timeout=0.5)
    grandchildren = _grandchildren(timeout_partial_stdout(info.value))
    try:
        assert not any(_gone(pid) for pid in grandchildren)
    finally:
        _reap(*grandchildren)


@_LINUX_ONLY
def test_run_in_session_carries_the_partial_stdout_on_the_timeout() -> None:
    """mem-zfm0m item 4: what the child wrote before the bound is on the exception, DECODED, so
    a scorer can count the calls the partial stream carries."""
    with pytest.raises(subprocess.TimeoutExpired) as info:
        run_in_session(_FORKING, capture_output=True, text=True, check=False, timeout=0.5)
    assert isinstance(info.value.stdout, str)
    assert "partial" in info.value.stdout
    assert info.value.timeout == 0.5
    assert timeout_partial_stdout(info.value) == info.value.stdout


@_LINUX_ONLY
def test_run_in_session_reaches_run_checked_as_the_timeout_rung() -> None:
    """Through the ladder: the caller's error type, CAUSED BY the TimeoutExpired that carries the
    partial stdout — the shape ``e1_grid`` classifies and scores."""
    with pytest.raises(_BoomError, match=r"did not finish within 0\.5s") as info:
        run_checked(
            _FORKING,
            what="the thing",
            not_found_hint="install it",
            timeout_s=0.5,
            error=_BoomError,
            runner=run_in_session,
        )
    cause = info.value.__cause__
    assert isinstance(cause, subprocess.TimeoutExpired)
    assert "partial" in timeout_partial_stdout(cause)
    _reap(*_grandchildren(timeout_partial_stdout(cause)))


@_LINUX_ONLY
def test_run_in_session_drain_does_not_hang_when_the_kill_is_a_no_op(tmp_path, monkeypatch) -> None:
    """Review F7: after a group kill that did nothing, the drain behind it used to be an unbounded
    ``communicate()`` — a grandchild holding stdout held the whole rig with it. The drain is
    bounded and a group that survives is reported by name, never waited out."""
    pidfile = tmp_path / "holder.pid"
    holder = ["sh", "-c", f"sleep 600 & echo $! > {pidfile}; wait"]
    monkeypatch.setattr(spawn, "_kill_group", lambda proc: None)
    monkeypatch.setattr(spawn, "DRAIN_TIMEOUT_S", 1.0)
    outcome: list[BaseException] = []

    def _run() -> None:
        try:
            run_in_session(holder, capture_output=True, text=True, check=False, timeout=0.5)
        except BaseException as exc:
            outcome.append(exc)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(15.0)
    try:
        assert not thread.is_alive(), "the drain blocked on the grandchild holding stdout"
        assert len(outcome) == 1
        assert isinstance(outcome[0], RuntimeError)
        assert re.search(r"process group \d+ survived", str(outcome[0]))
        assert "1.0s" in str(outcome[0])
    finally:
        _reap(int(pidfile.read_text()))
        thread.join(5.0)


@_LINUX_ONLY
def test_run_in_session_abandon_does_not_wait_unbounded_for_a_leader_that_will_not_die(
    tmp_path, monkeypatch
) -> None:
    """Review G4: after the bounded drain, ``_abandon`` waited on the leader UNBOUNDED, so a
    leader that outlives its own SIGKILL held the rig exactly as the drain used to. The wait
    is bounded by the same DRAIN_TIMEOUT_S; on expiry the error still names the surviving
    group and says the zombie is leaked."""
    pidfile = tmp_path / "holder.pid"
    holder = ["sh", "-c", f"sleep 600 & echo $! > {pidfile}; wait"]
    leaders: list[int] = []

    def _no_kill(self: subprocess.Popen[str]) -> None:
        leaders.append(self.pid)

    monkeypatch.setattr(spawn, "_kill_group", lambda proc: None)
    monkeypatch.setattr(subprocess.Popen, "kill", _no_kill)
    monkeypatch.setattr(spawn, "DRAIN_TIMEOUT_S", 1.0)
    outcome: list[BaseException] = []

    def _run() -> None:
        try:
            run_in_session(holder, capture_output=True, text=True, check=False, timeout=0.5)
        except BaseException as exc:
            outcome.append(exc)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(15.0)
    try:
        assert not thread.is_alive(), "_abandon waited unbounded on the surviving leader"
        assert len(outcome) == 1
        assert isinstance(outcome[0], RuntimeError)
        assert re.search(r"process group \d+ survived", str(outcome[0]))
        assert "left unreaped" in str(outcome[0])
        assert str(leaders[0]) in str(outcome[0])
    finally:
        _reap(int(pidfile.read_text()), *leaders)
        thread.join(5.0)


def test_run_in_session_returns_a_completed_process_with_cwd_env_and_input(tmp_path) -> None:
    completed = run_in_session(
        ["sh", "-c", "pwd; echo $MARK; cat"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10.0,
        cwd=tmp_path,
        env={"MARK": "here", "PATH": os.environ["PATH"]},
        input="from-stdin\n",
    )
    assert completed.returncode == 0
    assert completed.stdout.splitlines() == [str(tmp_path.resolve()), "here", "from-stdin"]
    assert completed.stderr == ""


def test_run_in_session_refuses_a_shape_run_checked_never_asks_for() -> None:
    """It serves ``run_checked``'s spawn shape and nothing wider — a silent ``check=True`` would
    raise CalledProcessError past the ladder's own non-zero rung."""
    with pytest.raises(ValueError, match="check=False"):
        run_in_session(["true"], capture_output=True, text=True, check=True, timeout=1.0)
    with pytest.raises(ValueError, match="capture_output=True"):
        run_in_session(["true"], capture_output=False, text=True, check=False, timeout=1.0)


def test_timeout_partial_stdout_decodes_bytes_and_tolerates_none() -> None:
    """``subprocess.run`` hands the partial stdout over as BYTES even in text mode; a runner that
    produced nothing before the bound hands ``None``."""
    as_bytes = subprocess.TimeoutExpired(cmd=["x"], timeout=1.0, output=b"partial \xff")
    assert timeout_partial_stdout(as_bytes) == "partial \ufffd"
    as_str = subprocess.TimeoutExpired(cmd=["x"], timeout=1.0, output="partial")
    assert timeout_partial_stdout(as_str) == "partial"
    assert timeout_partial_stdout(subprocess.TimeoutExpired(cmd=["x"], timeout=1.0)) == ""
