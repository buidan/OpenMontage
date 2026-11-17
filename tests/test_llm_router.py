"""Tests for llm_router.py using mocked upstream providers (no network)."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import llm_router  # noqa: E402

HOSTS = {"api.groq.com": "groq", "generativelanguage.googleapis.com": "aistudio", "127.0.0.1": "local"}
ROUTER_KEYS = [
    "LLM_ROUTING_MODE", "LLM_MANUAL_PROVIDER", "LLM_FALLBACK_ORDER", "LLM_COOLDOWN_SECONDS",
    "LLM_AUTH_COOLDOWN_SECONDS", "LLM_CLOUD_TIMEOUT_SECONDS", "LLM_LOCAL_TIMEOUT_SECONDS",
    "GROQ_API_KEY", "GROQ_MODEL", "GROQ_FAST_MODEL", "GROQ_BASE_URL", "LLM_GEMINI_API_KEY",
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_MODEL", "GEMINI_FAST_MODEL", "AISTUDIO_BASE_URL",
    "OLLAMA_BASE_URL", "OLLAMA_MODEL", "OLLAMA_FAST_MODEL", "OLLAMA_API_KEY", "LLM_ROUTER_API_KEY",
    "LLM_ROUTER_EVENT_LOG",
]
BASE_ENV = """
LLM_ROUTING_MODE=auto
LLM_MANUAL_PROVIDER=groq
LLM_FALLBACK_ORDER=groq,aistudio,local
GROQ_API_KEY=gsk_test
LLM_GEMINI_API_KEY=AIza_test
"""


def ok(provider: str, content: str = "hello") -> httpx.Response:
    return httpx.Response(200, json={
        "id": "x", "object": "chat.completion", "model": provider,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
    })


class Upstreams:
    """Per-provider scripted behaviour; records every call."""

    def __init__(self) -> None:
        self.behaviour: dict[str, object] = {p: None for p in HOSTS.values()}
        self.calls: list[tuple[str, dict]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        provider = HOSTS[request.url.host]
        body = json.loads(request.content)
        self.calls.append((provider, body))
        b = self.behaviour[provider]
        if callable(b):
            return b(request)
        if isinstance(b, int):
            return httpx.Response(b, json={"error": {"message": f"{provider} {b}"}})
        return ok(provider)

    def providers_called(self) -> list[str]:
        return [p for p, _ in self.calls]


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    for k in ROUTER_KEYS:
        monkeypatch.delenv(k, raising=False)
    path = tmp_path / ".env"
    path.write_text(BASE_ENV)
    return path


@pytest.fixture
def setup(env_file):
    up = Upstreams()
    router = llm_router.Router(env_path=env_file, transport=httpx.MockTransport(up.handler))
    client = TestClient(llm_router.create_app(router))
    return client, up, router, env_file


def post(client, **extra):
    body = {"model": "auto-router", "messages": [{"role": "user", "content": "hi"}], **extra}
    return client.post("/v1/chat/completions", json=body)


def rewrite_env(path: Path, text: str) -> None:
    path.write_text(text)
    # Make sure the mtime changes even on coarse-resolution filesystems.
    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime + 1))


def test_auto_uses_first_healthy_provider_and_default_model(setup):
    client, up, _, _ = setup
    r = post(client)
    assert r.status_code == 200
    assert r.headers["x-llm-router-provider"] == "groq"
    assert up.calls[0][1]["model"] == "llama-3.3-70b-versatile"


@pytest.mark.parametrize("status", [401, 429, 500, 502, 503, 504])
def test_auto_fails_over_on_error_status(setup, status):
    client, up, _, _ = setup
    up.behaviour["groq"] = status
    r = post(client)
    assert r.status_code == 200
    assert r.headers["x-llm-router-provider"] == "aistudio"
    assert r.headers["x-llm-router-fallbacks"] == "1"
    assert up.calls[1][1]["model"] == "gemini-2.5-flash"


def test_both_cloud_fail_falls_back_to_local(setup):
    client, up, _, _ = setup
    up.behaviour["groq"] = 401
    up.behaviour["aistudio"] = 429
    r = post(client)
    assert r.headers["x-llm-router-provider"] == "local"
    assert up.providers_called() == ["groq", "aistudio", "local"]
    assert up.calls[2][1]["model"] == "qwen2.5:7b"


def test_rate_limit_puts_provider_on_cooldown(setup):
    client, up, router, _ = setup
    up.behaviour["groq"] = 429
    post(client)
    assert router.health.cooling("groq") > 55
    up.calls.clear()
    up.behaviour["groq"] = None  # recovered, but still cooling down
    r = post(client)
    assert r.headers["x-llm-router-provider"] == "aistudio"
    assert up.providers_called() == ["aistudio"]


def test_cooldown_expires(setup):
    client, up, router, _ = setup
    router.health.cooldown_until["groq"] = time.monotonic() - 1
    post(client)
    assert up.providers_called() == ["groq"]


def test_timeout_fails_over(setup):
    client, up, _, _ = setup

    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    up.behaviour["groq"] = slow
    r = post(client)
    assert r.headers["x-llm-router-provider"] == "aistudio"


def test_malformed_json_fails_over_when_json_requested(setup):
    client, up, _, _ = setup
    up.behaviour["groq"] = lambda req: ok("groq", "Sure! here is your scene plan: {broken")
    up.behaviour["aistudio"] = lambda req: ok("aistudio", '```json\n{"scenes": []}\n```')
    r = post(client, response_format={"type": "json_object"})
    assert r.headers["x-llm-router-provider"] == "aistudio"


def test_non_json_content_is_fine_without_response_format(setup):
    client, up, _, _ = setup
    up.behaviour["groq"] = lambda req: ok("groq", "plain prose")
    assert post(client).headers["x-llm-router-provider"] == "groq"


def test_all_fail_returns_503_with_attempts(setup):
    client, up, _, _ = setup
    for p in ("groq", "aistudio", "local"):
        up.behaviour[p] = 503
    r = post(client)
    assert r.status_code == 503
    attempts = r.json()["error"]["attempts"]
    assert [a["provider"] for a in attempts] == ["groq", "aistudio", "local"]


def test_unconfigured_cloud_providers_are_skipped(setup):
    client, up, _, env = setup
    rewrite_env(env, "LLM_ROUTING_MODE=auto\n")
    r = post(client)
    assert r.headers["x-llm-router-provider"] == "local"
    assert up.providers_called() == ["local"]


def test_manual_pins_provider_even_when_others_healthy(setup):
    client, up, _, env = setup
    rewrite_env(env, BASE_ENV.replace("LLM_ROUTING_MODE=auto", "LLM_ROUTING_MODE=manual")
                .replace("LLM_MANUAL_PROVIDER=groq", "LLM_MANUAL_PROVIDER=aistudio"))
    for _ in range(3):
        assert post(client).headers["x-llm-router-provider"] == "aistudio"
    assert up.providers_called() == ["aistudio"] * 3


def test_manual_fails_explicitly_without_failover(setup):
    client, up, router, env = setup
    rewrite_env(env, BASE_ENV.replace("LLM_ROUTING_MODE=auto", "LLM_ROUTING_MODE=manual"))
    up.behaviour["groq"] = 429
    r = post(client)
    assert r.status_code == 429
    assert up.providers_called() == ["groq"]
    assert router.health.cooling("groq") == 0  # manual mode never trips cooldown


def test_mode_switch_is_hot_reloaded(setup):
    client, up, _, env = setup
    assert post(client).headers["x-llm-router-provider"] == "groq"
    rewrite_env(env, BASE_ENV.replace("LLM_ROUTING_MODE=auto", "LLM_ROUTING_MODE=manual")
                .replace("LLM_MANUAL_PROVIDER=groq", "LLM_MANUAL_PROVIDER=local"))
    assert post(client).headers["x-llm-router-provider"] == "local"


def test_fast_alias_and_explicit_provider_model(setup):
    client, up, _, _ = setup
    post(client, model="auto-router-fast")
    assert up.calls[-1] == ("groq", up.calls[-1][1]) and up.calls[-1][1]["model"] == "llama-3.1-8b-instant"
    r = post(client, model="local/llama3.2:3b")
    assert r.headers["x-llm-router-provider"] == "local"
    assert up.calls[-1][1]["model"] == "llama3.2:3b"


def test_streaming_fails_over_before_first_byte(setup):
    client, up, _, _ = setup
    up.behaviour["groq"] = 429
    sse = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'
    up.behaviour["aistudio"] = lambda req: httpx.Response(
        200, stream=httpx.ByteStream(sse), headers={"content-type": "text/event-stream"})
    r = post(client, stream=True)
    assert r.status_code == 200
    assert r.headers["x-llm-router-provider"] == "aistudio"
    assert r.content == sse


def test_router_api_key_enforced(setup):
    client, _, _, env = setup
    rewrite_env(env, BASE_ENV + "LLM_ROUTER_API_KEY=secret\n")
    assert post(client).status_code == 401
    r = client.post("/v1/chat/completions", headers={"Authorization": "Bearer secret"},
                    json={"model": "auto-router", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200


def test_env_parser_handles_inline_comments(tmp_path):
    p = tmp_path / ".env"
    p.write_text('A=groq   # note\nB=   # empty\nC="x # y"\nexport D=1\n# E=2\n')
    assert llm_router._parse_env_file(p) == {"A": "groq", "B": "", "C": "x # y", "D": "1"}


def test_health_and_models_endpoints(setup):
    client, _, _, _ = setup
    h = client.get("/health").json()
    assert h["mode"] == "auto" and h["providers"]["local"]["configured"] is True
    ids = [m["id"] for m in client.get("/v1/models").json()["data"]]
    assert "auto-router" in ids and "groq/llama-3.3-70b-versatile" in ids
