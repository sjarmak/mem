"""Tests for the shared self-hosted model stack (mem-lvp.5).

Hermetic: env resolution is exercised with an injected dict and ``preflight`` with
an injected fetcher, so nothing here touches ``os.environ`` or a live Ollama daemon.
The two invariants under test are (1) the stack is the single source of truth for the
pinned model identity (V2 confound control) and (2) ``preflight`` fails LOUD rather
than letting an arm silently fall back to a paid API.
"""

from __future__ import annotations

import json

import pytest

from membench.memory_systems.local_stack import (
    DEFAULT_CHAT_MODEL,
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_OLLAMA_EMBEDDING_MODEL,
    DEFAULT_SENTENCE_TRANSFORMER_MODEL,
    ENV_CHAT_MODEL,
    ENV_OLLAMA_BASE_URL,
    ENV_OLLAMA_EMBEDDING_MODEL,
    ENV_SENTENCE_TRANSFORMER_MODEL,
    STACK_VERSION,
    LocalModelStack,
    LocalStackUnavailableError,
)


def _tags(*names: str) -> bytes:
    return json.dumps({"models": [{"name": n} for n in names]}).encode()


# --- env resolution --------------------------------------------------------------


def test_from_env_uses_pinned_defaults_when_unset():
    stack = LocalModelStack.from_env(env={})
    assert stack.ollama_base_url == DEFAULT_OLLAMA_BASE_URL
    assert stack.chat_model == DEFAULT_CHAT_MODEL
    assert stack.ollama_embedding_model == DEFAULT_OLLAMA_EMBEDDING_MODEL
    assert stack.sentence_transformer_model == DEFAULT_SENTENCE_TRANSFORMER_MODEL


def test_from_env_overrides_every_field():
    stack = LocalModelStack.from_env(
        env={
            ENV_OLLAMA_BASE_URL: "http://gpu-box:11434",
            ENV_CHAT_MODEL: "qwen2",
            ENV_OLLAMA_EMBEDDING_MODEL: "mxbai-embed-large",
            ENV_SENTENCE_TRANSFORMER_MODEL: "all-mpnet-base-v2",
        }
    )
    assert stack.ollama_base_url == "http://gpu-box:11434"
    assert stack.chat_model == "qwen2"
    assert stack.ollama_embedding_model == "mxbai-embed-large"
    assert stack.sentence_transformer_model == "all-mpnet-base-v2"


# --- telemetry pin (V2) ----------------------------------------------------------


def test_telemetry_dict_pins_models_and_version_but_not_base_url():
    stack = LocalModelStack(ollama_base_url="http://host:1", chat_model="c")
    pin = stack.telemetry_dict()
    assert pin["stack_version"] == STACK_VERSION
    assert pin["chat_model"] == "c"
    assert pin["ollama_embedding_model"] == DEFAULT_OLLAMA_EMBEDDING_MODEL
    # base_url is a deployment detail, not model identity.
    assert "ollama_base_url" not in pin


def test_stack_is_immutable():
    stack = LocalModelStack()
    with pytest.raises((AttributeError, TypeError)):
        stack.chat_model = "other"  # type: ignore[misc]


# --- preflight: fail loud, never silent paid-API fallback ------------------------


def test_preflight_passes_when_daemon_and_models_present():
    stack = LocalModelStack()

    # Ollama tags carry a :tag suffix; the bare config name must still match.
    def fetch(url: str) -> bytes:
        return _tags(f"{DEFAULT_CHAT_MODEL}:latest", f"{DEFAULT_OLLAMA_EMBEDDING_MODEL}:latest")

    stack.preflight(fetch=fetch)  # does not raise


def test_preflight_queries_the_tags_endpoint():
    seen: list[str] = []

    def fetch(url: str) -> bytes:
        seen.append(url)
        return _tags(f"{DEFAULT_CHAT_MODEL}:latest", DEFAULT_OLLAMA_EMBEDDING_MODEL)

    LocalModelStack().preflight(fetch=fetch)
    assert seen == [f"{DEFAULT_OLLAMA_BASE_URL}/api/tags"]


def test_preflight_raises_actionable_error_when_daemon_unreachable():
    def fetch(url: str) -> bytes:
        raise OSError("connection refused")

    with pytest.raises(LocalStackUnavailableError, match="ollama serve"):
        LocalModelStack().preflight(fetch=fetch)


def test_preflight_raises_with_pull_command_for_missing_models():
    stack = LocalModelStack(chat_model="llama3", ollama_embedding_model="nomic-embed-text")

    # Only the embedder is pulled; the chat model is missing.
    def fetch(url: str) -> bytes:
        return _tags("nomic-embed-text:latest")

    with pytest.raises(LocalStackUnavailableError, match="ollama pull llama3"):
        stack.preflight(fetch=fetch)


def test_preflight_skips_chat_check_when_not_required():
    stack = LocalModelStack()

    # No chat model pulled, but an embed-only arm must still pass.
    def fetch(url: str) -> bytes:
        return _tags(f"{DEFAULT_OLLAMA_EMBEDDING_MODEL}:latest")

    stack.preflight(require_chat=False, fetch=fetch)  # does not raise


def test_preflight_missing_embedder_always_fails_even_without_chat():
    stack = LocalModelStack()

    def fetch(url: str) -> bytes:
        return _tags("some-other-model:latest")

    with pytest.raises(LocalStackUnavailableError, match="ollama pull"):
        stack.preflight(require_chat=False, fetch=fetch)
