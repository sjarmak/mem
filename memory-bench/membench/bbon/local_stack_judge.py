"""LocalModelStack-backed comparative judge — the §4.1 shared OSS-judge backend.

This is the local-model seam the §12.6 action-impact scorer
(`membench.metrics.action_impact.score_action_impact`) and the §4.3 derailment /
§4.5 judges consume in place of headless `claude -p`. It satisfies the SAME
`membench.bbon.comparative_judge.ComparativeJudge` protocol (a `model` property and
`complete(prompt) -> str`), so every consumer accepts it unchanged: where
`ClaudeComparativeJudge` shells out to the local Claude CLI, this judge POSTs to a
self-hosted Ollama daemon serving the pinned `LocalModelStack.chat_model` (an 8B+
instruct model such as ``qwen2.5`` / ``llama3.1``).

Built ONCE for three consumers (the §12.6 fork decision, mayor gc-390342): it is
judge infrastructure, not metric logic, so the scorer stays judge-agnostic.

**No silent paid-API fallback.** A missing or unreachable daemon does not degrade to
a managed API — it raises loudly. `preflight()` delegates to `LocalModelStack.preflight`
so a real run fails fast at the boundary with the actionable `ollama pull`; and
`complete()` itself wraps a daemon-connection failure in `LocalStackUnavailableError`
rather than returning a default verdict.

**§4.5 license gate.** The model identity is whatever `LocalModelStack.chat_model`
is pinned to, so Nemotron (NVIDIA Open Model License) can be the wired default
without any code change here. That license is NOT OSI-approved: a run pinned to a
non-OSI model is publication-gated — keep an OSI-clean fallback (e.g. ``llama3.1``,
``qwen2.5``) benchmarked in parallel. This module records the model in telemetry via
the stack; it does not itself enforce the gate (a publication-time concern, not a
runtime one).

ZFC: the verdict IS the delegated model judgment. This module's own code is pure
plumbing — request assembly, HTTP IO, JSON unwrapping, and structural validation.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from membench.bbon.comparative_judge import ComparativeJudgeError, GenerationLengthError
from membench.memory_systems.local_stack import (
    HttpFetch,
    LocalModelStack,
    LocalStackUnavailableError,
)

# A local chat completion resolves in seconds on a provisioned GPU box; a minute-plus
# bound means a wedged daemon, not slow inference — same reasoning as the CLI judge.
DEFAULT_TIMEOUT_S = 120.0

# The context window every request states, rather than inheriting whatever the
# daemon was started with. Leaving it out works on this box and only on this box:
# the daemon here runs with OLLAMA_CONTEXT_LENGTH=32768, so an 83K-character probe
# prompt was read whole (prompt_eval_count 28,596) with no options sent at all.
# The same request against a daemon started with a smaller window is not an error
# -- it is a clipped prompt and a confident answer about the part that fit. This
# number is 32768 because that is what the extractor already pins and what the
# retro fixtures were measured under; a caller needing another window passes
# num_ctx in ``options`` and overrides it. What no caller gets is the daemon's
# configuration deciding the window silently.
DEFAULT_CONTEXT_TOKENS = 32768

# Ollama's non-streaming text-completion endpoint. The reply is a single JSON object
# whose ``response`` field holds the model text.
_GENERATE_PATH = "/api/generate"

# A POST callable: (url, request_body_bytes) -> raw response bytes. Injected in tests
# so the parse path is exercised with no live daemon; the default binds urllib.
ChatPost = Callable[[str, bytes], bytes]


def _urllib_post(url: str, body: bytes, *, timeout: float) -> bytes:
    # Trust-boundary call to the local daemon: a real timeout so a hung/absent Ollama
    # surfaces as an error, never an indefinite hang.
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        data: bytes = resp.read()
    return data


@dataclass(frozen=True)
class LocalStackComparativeJudge:
    """A `ComparativeJudge` backed by a self-hosted Ollama daemon serving the pinned
    `LocalModelStack.chat_model`.

    ``stack`` is the shared pinned model identity, env-resolved by default so an
    operator pins the model (e.g. Nemotron vs an OSI-clean ``llama3.1``) through
    ``MEMBENCH_LOCAL_CHAT_MODEL`` with no code change. ``post`` is injected in tests so
    the parse path runs without a live daemon; left ``None`` the default urllib POST is
    used with ``timeout_s``. Every failure mode is surfaced loudly: a daemon connection
    failure as `LocalStackUnavailableError`, a malformed reply as `ComparativeJudgeError`.

    ``options`` is forwarded as Ollama's generation options, merged over a
    ``num_ctx`` of `DEFAULT_CONTEXT_TOKENS` that every request carries. Passing
    none does not mean passing neutral ones: the daemon then applies the model's
    own defaults, which for an instruct model are sampled (temperature 0.7 and
    friends), so a caller doing structured extraction or verbatim copying should
    pin ``temperature`` and ``seed`` and not inherit them. ``num_predict`` bounds
    generation, which is otherwise unbounded: a model that falls into a repetition
    loop holds the socket until ``timeout_s``, a hang wearing the costume of a
    slow call.

    The context window is the one option the judge will not leave to the daemon,
    because overrunning it is silent in the direction that matters. A generation
    cut short comes back with ``done`` false and is refused by `_refuse_truncated`;
    an *input* clipped to fit the window comes back finished and correct-looking.
    Measured: the same prompt that answers ``CORMORANT-47`` under a 32,768-token
    window answers ``The passphrase is 1366.`` under a 2,048-token one, with
    ``done`` true and nothing in the payload marking the loss.
    `_refuse_clipped_prompt` reads that case out of the token counts and raises."""

    stack: LocalModelStack = field(default_factory=LocalModelStack.from_env)
    timeout_s: float = DEFAULT_TIMEOUT_S
    post: ChatPost | None = None
    options: Mapping[str, object] | None = None

    @property
    def model(self) -> str:
        """The pinned chat model — the identity recorded in cache keys and verdicts."""
        return self.stack.chat_model

    def preflight(self, *, fetch: HttpFetch | None = None) -> None:
        """Verify the daemon is up and the pinned chat model is pulled, raising
        `LocalStackUnavailableError` otherwise. Delegates to `LocalModelStack.preflight`
        with ``require_chat=True`` — this judge runs an instruct model. ``fetch`` is
        forwarded to the stack for test injection."""
        self.stack.preflight(require_chat=True, fetch=fetch)

    def complete(self, prompt: str) -> str:
        """Run the pinned chat model over ``prompt`` and return its raw text reply.

        POSTs a non-streaming `/api/generate` request and returns the ``response``
        field verbatim — the caller (`parse_judgment_reply` /
        `parse_action_impact_verdict`) extracts and validates the JSON verdict from it.
        A daemon connection failure raises `LocalStackUnavailableError` (fail loud, no
        paid-API fallback); a non-JSON or shape-wrong reply raises
        `ComparativeJudgeError`."""
        url = f"{self.stack.ollama_base_url.rstrip('/')}{_GENERATE_PATH}"
        request: dict[str, object] = {
            "model": self.stack.chat_model,
            "prompt": prompt,
            "stream": False,
        }
        options: dict[str, object] = {"num_ctx": DEFAULT_CONTEXT_TOKENS}
        options.update(self.options or {})
        request["options"] = options
        body = json.dumps(request).encode()
        do_post = self.post or (lambda u, b: _urllib_post(u, b, timeout=self.timeout_s))
        try:
            raw = do_post(url, body)
        except (urllib.error.URLError, OSError) as exc:
            raise LocalStackUnavailableError(
                f"Ollama daemon not reachable at {self.stack.ollama_base_url} ({exc}). "
                "Start it with `ollama serve` (or set MEMBENCH_OLLAMA_BASE_URL to its "
                "address); this judge will not fall back to a paid API."
            ) from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ComparativeJudgeError(f"Ollama reply is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("response"), str):
            raise ComparativeJudgeError(
                f"Ollama reply missing a string 'response' field: {payload!r}"
            )
        # The input fault is diagnosed before the output one, because a clipped
        # prompt frequently reports as a capped generation and the two carry
        # opposite advice. Measured: the same over-long prompt under num_ctx 2048
        # came back done_reason=length with num_predict set and done_reason=stop
        # without it. Checked the other way round the caller is told to raise
        # num_predict, which cannot help -- the prompt was already cut before the
        # model read it. Whichever fires, the window is the thing to look at first.
        _refuse_clipped_prompt(payload, options["num_ctx"])
        _refuse_truncated(payload)
        return str(payload["response"])


def _refuse_clipped_prompt(payload: dict[str, object], window: object) -> None:
    """Raise when the daemon read a prompt right up to the edge of the window.

    The input side of the truncation hazard, and the worse half of it: a
    generation the daemon cut short is reported, but a prompt clipped to fit the
    context window is not. Measured against a live daemon with a needle at the
    top of an 83K-character prompt and the question at the bottom -- under
    num_ctx 32768 the model answered ``CORMORANT-47``; under 2048 the same
    request answered ``The passphrase is 1366.``, with ``done`` true and no field
    in the payload distinguishing it from a good answer. A judge verdict or a
    write-gate decision arrives the same way: computed over a prompt the caller
    never sent, and nothing downstream can tell.

    ``prompt_eval_count`` is what the daemon says it actually read, and a clipped
    read lands one token below the window rather than on it -- 2047/2048,
    4095/4096, 8191/8192 across three measured windows, while the same prompt cut
    down to fit read 26. So the test is ``window - 1``, not ``window``: an exact
    comparison passes every real clip through. There is no ambiguity to buy off
    with a wider margin, since a prompt that fits stops thousands of tokens short.

    Only when both numbers are present. A payload without the count (older
    daemons, injected test doubles) supports no conclusion either way, and a
    guard against fabricated answers is the last place to fabricate one.
    """
    read = payload.get("prompt_eval_count")
    if not isinstance(read, int) or not isinstance(window, int) or read < window - 1:
        return
    raise ComparativeJudgeError(
        f"Ollama read {read} prompt tokens against a context window of {window}, "
        "which is the signature of a prompt clipped to fit: the model answered "
        "about the part that fit and the reply does not say so. Raise "
        "options['num_ctx'] (the daemon must also be started with a window at "
        "least that large), or send a shorter prompt. A judgment over a clipped "
        "prompt is not a judgment."
    )


def _refuse_truncated(payload: dict[str, object]) -> None:
    """Raise unless the daemon says the model finished on its own terms.

    A truncated generation is the one failure this endpoint reports without
    looking like a failure: the HTTP status is 200, ``response`` is a string, and
    the text reads like an answer that happens to stop. Observed shape --
    ``done: false``, no ``done_reason``, none of the token counts a finished
    generation carries, and text cut mid-line. The daemon log for those requests
    shows the runner task cancelled part-way through, and re-sending the
    identical request finished normally, so it is an abort rather than anything
    the prompt did.

    Every consumer here is a judge, so a partial reply is worse than no reply. It
    either fails to parse -- noise attributed to the model -- or parses into a
    verdict computed over less than the caller sent, which nothing downstream can
    detect. ``length`` is called out separately, as `GenerationLengthError`,
    because its cause is knowable from the payload alone and its fix is the
    caller's: raise ``num_predict``, or ask the prompt for less. It is also the
    one of the two that a re-send cannot help, which is why the distinction is a
    type and not just a message.

    A payload carrying no ``done`` field at all is accepted: some Ollama versions
    and every injected test double omit it, and refusing those would break
    callers over a field they never had.
    """
    done = payload.get("done")
    reason = payload.get("done_reason")
    if done is None and reason is None:
        return
    if done is True and reason in (None, "stop"):
        return
    if reason == "length":
        raise GenerationLengthError(
            "Ollama stopped generating at the token limit, so the reply is cut off. "
            "Raise options['num_predict'], or shorten what the prompt asks for. "
            "A truncated judgment is not a judgment."
        )
    raise ComparativeJudgeError(
        f"Ollama returned an unfinished generation (done={done!r}, "
        f"done_reason={reason!r}) -- the daemon stopped part-way and did not say "
        "why. This has been seen to be intermittent, so re-send the request; if "
        "it repeats on the same prompt, check the daemon log and whether "
        "options['num_ctx'] covers the prompt plus the reply. A truncated "
        "judgment is not a judgment."
    )
