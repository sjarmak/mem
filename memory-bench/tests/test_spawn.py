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

import errno
import subprocess

import pytest

from membench.spawn import run_checked


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
# reaches a log verbatim. Two properties are asserted here rather than at the six
# call sites, for the reason the module exists: the child is untrusted output, and
# every hand-rolled copy would have to re-derive both.
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


def test_a_token_on_stdout_is_redacted_when_stdout_is_the_diagnosis() -> None:
    # `claude -p --output-format stream-json` puts the WHOLE event stream on stdout, so
    # a non-zero exit with an empty stderr routes stdout into the printed exception.
    # Redacting only stderr would cover the half least likely to carry the secret.
    with pytest.raises(RuntimeError) as caught:
        _run(_ok_runner(stdout="boom sk-ant-oat01-LEAK", stderr="", returncode=1))
    assert "sk-ant-oat01-LEAK" not in str(caught.value)
    assert "boom" in str(caught.value)


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
