"""Minimal chat client for OpenMontage code that needs a text LLM.

Reads the ``llm:`` block of config.yaml and, when ``provider: router``, sends
OpenAI-compatible requests to the local LLM router (``llm_router.py``), which
handles Groq / Google AI Studio / Ollama selection and failover.

The router endpoint comes from ``LLM_ROUTER_BASE_URL`` / ``LLM_ROUTER_API_KEY``
rather than ``OPENAI_BASE_URL`` / ``OPENAI_API_KEY`` on purpose: OpenMontage's
OpenAI image, TTS and Sora tools read those two variables, and pointing them at
the router would break those tools and make them falsely appear configured.

Usage:
    from lib.llm_client import chat
    text = chat([{"role": "user", "content": "Write a hook about black holes"}])
    data = chat(messages, json_mode=True)        # returns parsed dict
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

import httpx

from lib.config_model import OpenMontageConfig

# Importing base_tool loads .env into os.environ (same loader the tools use).
import tools.base_tool  # noqa: F401


def _router_settings() -> tuple[str, str]:
    base = os.environ.get("LLM_ROUTER_BASE_URL", "http://127.0.0.1:4000/v1").rstrip("/")
    key = os.environ.get("LLM_ROUTER_API_KEY", "")
    return base, key


def chat(
    messages: list[dict[str, Any]],
    *,
    model: Optional[str] = None,
    json_mode: bool = False,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    timeout: float = 180.0,
) -> Any:
    """Send a chat completion through the router; return text (or dict if json_mode)."""
    return chat_meta(messages, model=model, json_mode=json_mode, temperature=temperature,
                     max_tokens=max_tokens, timeout=timeout)[0]


def chat_meta(
    messages: list[dict[str, Any]],
    *,
    model: Optional[str] = None,
    json_mode: bool = False,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    timeout: float = 180.0,
) -> tuple[Any, dict[str, Any]]:
    """Like ``chat`` but also returns routing metadata:
    ``{"provider", "model", "fallbacks"}`` taken from the router's response headers."""
    llm = OpenMontageConfig.load().llm
    if llm.provider != "router":
        raise RuntimeError(
            f"config.yaml llm.provider is {llm.provider!r}; set it to 'router' to use lib.llm_client"
        )
    base, key = _router_settings()
    payload: dict[str, Any] = {
        "model": model or llm.model or "auto-router",
        "messages": messages,
        "temperature": llm.temperature if temperature is None else temperature,
        "max_tokens": llm.max_tokens if max_tokens is None else max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    resp = httpx.post(f"{base}/chat/completions", json=payload, headers=headers, timeout=timeout)
    if resp.status_code >= 300:
        raise RuntimeError(f"LLM router error {resp.status_code}: {resp.text[:1000]}")
    meta = {
        "provider": resp.headers.get("x-llm-router-provider"),
        "model": resp.headers.get("x-llm-router-model"),
        "fallbacks": int(resp.headers.get("x-llm-router-fallbacks", "0") or 0),
    }
    content = resp.json()["choices"][0]["message"]["content"]
    if not json_mode:
        return content, meta
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[4:] if text.lower().startswith("json") else text
    return json.loads(text), meta
