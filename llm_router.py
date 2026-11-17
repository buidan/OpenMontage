"""OpenAI-compatible LLM router with manual pinning and automatic failover.

Serves ``POST /v1/chat/completions`` on 127.0.0.1:4000 and forwards each
request to Groq Cloud, Google AI Studio, or a local Ollama, all of which
expose OpenAI-compatible endpoints.

Modes (``LLM_ROUTING_MODE`` in ``.env``):

* ``manual`` - every request goes to ``LLM_MANUAL_PROVIDER``; errors are
  returned as-is (no failover, cooldowns ignored).
* ``auto``   - walk ``LLM_FALLBACK_ORDER``; fail over on 429, 5xx, auth
  errors, timeouts, connection errors, or non-JSON content when the caller
  asked for ``response_format`` JSON. A 429 puts the provider on cooldown
  (``LLM_COOLDOWN_SECONDS``, default 60) so later calls skip it instantly.

``.env`` is re-read whenever it changes on disk, so switching modes or
providers takes effect on the next request without restarting anything.

Model selection: send ``model="auto-router"`` (each provider's default model)
or ``"auto-router-fast"`` (each provider's ``*_FAST_MODEL``). Sending
``"<provider>/<model>"`` (e.g. ``"groq/llama-3.1-8b-instant"``) pins that one
request to that provider and model.

Run:  python llm_router.py            (or: uvicorn llm_router:app --port 4000)
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

ROOT = Path(__file__).resolve().parent
ENV_PATH = Path(os.environ.get("LLM_ROUTER_ENV_FILE", ROOT / ".env"))

PROVIDERS = ("groq", "aistudio", "local")
AUTO_MODELS = {"auto-router", "auto", "default"}
FAST_MODELS = {"auto-router-fast", "fast"}
# Failover on everything non-2xx in auto mode; these also trigger cooldown.
RATE_LIMIT_STATUS = {429}
AUTH_STATUS = {401, 403}

log = logging.getLogger("llm_router")


# --------------------------------------------------------------------------- #
# Configuration (hot-reloaded from .env)
# --------------------------------------------------------------------------- #


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        value = value.strip()
        if value[:1] in ("'", '"'):
            end = value.find(value[0], 1)
            value = value[1:end] if end != -1 else value[1:]
        else:
            match = re.search(r"(^|\s)#", value)  # inline comment
            if match:
                value = value[: match.start()]
            value = value.strip()
        values[key.strip()] = value
    return values


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    api_key: str
    model: str
    fast_model: str
    timeout: float
    requires_key: bool = True

    @property
    def configured(self) -> bool:
        return bool(self.base_url) and (bool(self.api_key) or not self.requires_key)


@dataclass(frozen=True)
class RouterConfig:
    mode: str
    manual_provider: str
    fallback_order: tuple[str, ...]
    providers: dict[str, Provider]
    cooldown_seconds: float
    auth_cooldown_seconds: float
    router_api_key: str
    event_log: Optional[Path]

    @classmethod
    def from_env(cls, env: dict[str, str]) -> "RouterConfig":
        def get(key: str, default: str = "") -> str:
            # Real process env wins over the file, matching OpenMontage's loader.
            val = os.environ.get(key)
            if val is not None and val.strip():
                return val.strip()
            return env.get(key, default).strip() or default

        cloud_timeout = float(get("LLM_CLOUD_TIMEOUT_SECONDS", "15"))
        local_timeout = float(get("LLM_LOCAL_TIMEOUT_SECONDS", "45"))
        providers = {
            "groq": Provider(
                name="groq",
                base_url=get("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
                api_key=get("GROQ_API_KEY"),
                model=get("GROQ_MODEL", "llama-3.3-70b-versatile"),
                fast_model=get("GROQ_FAST_MODEL", "llama-3.1-8b-instant"),
                timeout=cloud_timeout,
            ),
            "aistudio": Provider(
                name="aistudio",
                base_url=get(
                    "AISTUDIO_BASE_URL",
                    "https://generativelanguage.googleapis.com/v1beta/openai",
                ),
                api_key=get("LLM_GEMINI_API_KEY") or get("GEMINI_API_KEY") or get("GOOGLE_API_KEY"),
                model=get("GEMINI_MODEL", "gemini-2.5-flash"),
                fast_model=get("GEMINI_FAST_MODEL", "gemini-2.5-flash"),
                timeout=cloud_timeout,
            ),
            "local": Provider(
                name="local",
                base_url=get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"),
                api_key=get("OLLAMA_API_KEY", "ollama"),
                model=get("OLLAMA_MODEL", "qwen2.5:7b"),
                fast_model=get("OLLAMA_FAST_MODEL", "") or get("OLLAMA_MODEL", "qwen2.5:7b"),
                timeout=local_timeout,
                requires_key=False,
            ),
        }
        order = tuple(
            p for p in (s.strip().lower() for s in get("LLM_FALLBACK_ORDER", "groq,aistudio,local").split(","))
            if p in providers
        ) or PROVIDERS
        event_log = get("LLM_ROUTER_EVENT_LOG")
        return cls(
            mode=get("LLM_ROUTING_MODE", "auto").lower(),
            manual_provider=get("LLM_MANUAL_PROVIDER", "groq").lower(),
            fallback_order=order,
            providers=providers,
            cooldown_seconds=float(get("LLM_COOLDOWN_SECONDS", "60")),
            auth_cooldown_seconds=float(get("LLM_AUTH_COOLDOWN_SECONDS", "300")),
            router_api_key=get("LLM_ROUTER_API_KEY"),
            event_log=(ROOT / event_log) if event_log else None,
        )


class ConfigCache:
    """Reload RouterConfig whenever the .env file's mtime changes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._mtime: Optional[float] = None
        self._config: Optional[RouterConfig] = None

    def get(self) -> RouterConfig:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            mtime = -1.0
        if self._config is None or mtime != self._mtime:
            self._config = RouterConfig.from_env(_parse_env_file(self.path))
            if self._mtime is not None:
                log.info("reloaded %s: mode=%s", self.path, self._config.mode)
            self._mtime = mtime
        return self._config


# --------------------------------------------------------------------------- #
# Health / cooldown state
# --------------------------------------------------------------------------- #


@dataclass
class Health:
    cooldown_until: dict[str, float] = field(default_factory=dict)
    last_error: dict[str, str] = field(default_factory=dict)

    def cooling(self, name: str) -> float:
        return max(0.0, self.cooldown_until.get(name, 0.0) - time.monotonic())

    def trip(self, name: str, seconds: float, reason: str) -> None:
        if seconds > 0:
            self.cooldown_until[name] = time.monotonic() + seconds
        self.last_error[name] = reason

    def clear(self, name: str) -> None:
        self.cooldown_until.pop(name, None)


class Attempt(Exception):
    """A single provider attempt failed in a way that permits failover."""

    def __init__(self, provider: str, reason: str, status: int = 502, body: Any = None,
                 cooldown: float = 0.0) -> None:
        super().__init__(f"{provider}: {reason}")
        self.provider = provider
        self.reason = reason
        self.status = status
        self.body = body
        self.cooldown = cooldown


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #


def _wants_json(payload: dict[str, Any]) -> bool:
    fmt = payload.get("response_format")
    return isinstance(fmt, dict) and fmt.get("type") in {"json_object", "json_schema"}


def _content_is_json(data: dict[str, Any]) -> bool:
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return False
    if not isinstance(content, str):
        return False
    text = content.strip()
    if text.startswith("```"):  # tolerate fenced JSON from smaller models
        text = text.strip("`")
        text = text[4:] if text.lower().startswith("json") else text
    try:
        json.loads(text)
        return True
    except ValueError:
        return False


def _retry_after(resp: httpx.Response) -> float:
    try:
        return float(resp.headers.get("retry-after", "0"))
    except ValueError:
        return 0.0


def plan_route(cfg: RouterConfig, health: Health, requested_model: str) -> tuple[list[tuple[Provider, str]], bool]:
    """Return ([(provider, upstream_model), ...], failover_allowed)."""
    model = (requested_model or "auto-router").strip()
    prefix, sep, rest = model.partition("/")
    if sep and prefix.lower() in cfg.providers:  # explicit "<provider>/<model>"
        return [(cfg.providers[prefix.lower()], rest)], False

    fast = model in FAST_MODELS
    custom = None if (model in AUTO_MODELS or fast) else model

    def pick(p: Provider) -> str:
        return custom or (p.fast_model if fast else p.model)

    if cfg.mode == "manual":
        p = cfg.providers.get(cfg.manual_provider)
        if p is None:
            raise HTTPException(500, f"LLM_MANUAL_PROVIDER={cfg.manual_provider!r} is not one of {PROVIDERS}")
        return [(p, pick(p))], False

    chain = [cfg.providers[n] for n in cfg.fallback_order if cfg.providers[n].configured]
    ready = [p for p in chain if health.cooling(p.name) == 0]
    # If every provider is cooling down, try them anyway rather than fail outright.
    return [(p, pick(p)) for p in (ready or chain)], True


class Router:
    def __init__(self, env_path: Path = ENV_PATH, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self.config = ConfigCache(env_path)
        self.health = Health()
        self._transport = transport
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(transport=self._transport)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    def _event(self, cfg: RouterConfig, **fields: Any) -> None:
        fields["ts"] = time.time()
        log.info("event %s", json.dumps(fields))
        if cfg.event_log:
            try:
                cfg.event_log.parent.mkdir(parents=True, exist_ok=True)
                with cfg.event_log.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(fields) + "\n")
            except OSError as exc:  # logging must never break routing
                log.warning("event log write failed: %s", exc)

    def _classify(self, cfg: RouterConfig, p: Provider, resp: httpx.Response) -> Attempt:
        try:
            body: Any = resp.json()
        except ValueError:
            body = {"error": {"message": resp.text[:500]}}
        cooldown = 0.0
        if resp.status_code in RATE_LIMIT_STATUS:
            cooldown = max(cfg.cooldown_seconds, min(_retry_after(resp), 3600))
        elif resp.status_code in AUTH_STATUS:
            cooldown = cfg.auth_cooldown_seconds
        return Attempt(p.name, f"HTTP {resp.status_code}", resp.status_code, body, cooldown)

    def _request(self, p: Provider, model: str, payload: dict[str, Any]) -> tuple[str, dict, dict, httpx.Timeout]:
        body = dict(payload, model=model)
        headers = {"Content-Type": "application/json"}
        if p.api_key:
            headers["Authorization"] = f"Bearer {p.api_key}"
        url = p.base_url.rstrip("/") + "/chat/completions"
        timeout = httpx.Timeout(p.timeout, connect=min(5.0, p.timeout))
        return url, body, headers, timeout

    async def _call(self, cfg: RouterConfig, p: Provider, model: str, payload: dict[str, Any]) -> dict[str, Any]:
        url, body, headers, timeout = self._request(p, model, payload)
        try:
            resp = await self.client.post(url, json=body, headers=headers, timeout=timeout)
        except httpx.TimeoutException:
            raise Attempt(p.name, f"timeout after {p.timeout:.0f}s", 504)
        except httpx.HTTPError as exc:
            raise Attempt(p.name, f"connection error: {exc.__class__.__name__}", 502)
        if resp.status_code >= 300:
            raise self._classify(cfg, p, resp)
        try:
            data = resp.json()
        except ValueError:
            raise Attempt(p.name, "upstream returned non-JSON body", 502)
        if _wants_json(payload) and not _content_is_json(data):
            raise Attempt(p.name, "response_format=json requested but content is not valid JSON", 502, data)
        return data

    async def _open_stream(self, cfg: RouterConfig, p: Provider, model: str,
                           payload: dict[str, Any]) -> tuple[httpx.Response, AsyncIterator[bytes]]:
        """Open a stream and wait for its first chunk, so failover can still happen
        before any bytes are sent to the caller."""
        url, body, headers, timeout = self._request(p, model, payload)
        req = self.client.build_request("POST", url, json=body, headers=headers, timeout=timeout)
        try:
            resp = await self.client.send(req, stream=True)
        except httpx.TimeoutException:
            raise Attempt(p.name, f"timeout after {p.timeout:.0f}s", 504)
        except httpx.HTTPError as exc:
            raise Attempt(p.name, f"connection error: {exc.__class__.__name__}", 502)
        if resp.status_code >= 300:
            await resp.aread()
            await resp.aclose()
            raise self._classify(cfg, p, resp)
        chunks = resp.aiter_raw()
        try:
            first = await chunks.__anext__()
        except StopAsyncIteration:
            first = b""
        except httpx.HTTPError as exc:
            await resp.aclose()
            raise Attempt(p.name, f"stream failed before first byte: {exc.__class__.__name__}", 502)

        async def body_iter() -> AsyncIterator[bytes]:
            try:
                if first:
                    yield first
                async for chunk in chunks:
                    yield chunk
            finally:
                await resp.aclose()

        return resp, body_iter()

    async def complete(self, payload: dict[str, Any]):
        cfg = self.config.get()
        route, failover = plan_route(cfg, self.health, str(payload.get("model", "")))
        if not route:
            raise HTTPException(503, "No LLM provider is configured (set GROQ_API_KEY, GEMINI_API_KEY, or run Ollama).")

        stream = bool(payload.get("stream"))
        attempts: list[dict[str, Any]] = []
        last: Optional[Attempt] = None
        for p, model in route:
            started = time.monotonic()
            try:
                if stream:
                    resp, body = await self._open_stream(cfg, p, model, payload)
                else:
                    data = await self._call(cfg, p, model, payload)
            except Attempt as exc:
                last = exc
                if failover:
                    self.health.trip(p.name, exc.cooldown, exc.reason)
                attempts.append({"provider": p.name, "model": model, "error": exc.reason,
                                 "ms": int((time.monotonic() - started) * 1000)})
                self._event(cfg, kind="attempt_failed", mode=cfg.mode, **attempts[-1],
                            cooldown_s=exc.cooldown if failover else 0)
                if not failover:
                    break
                continue

            self.health.clear(p.name)
            headers = {
                "x-llm-router-provider": p.name,
                "x-llm-router-model": model,
                "x-llm-router-fallbacks": str(len(attempts)),
            }
            self._event(cfg, kind="routed", mode=cfg.mode, provider=p.name, model=model,
                        fallbacks=[a["provider"] for a in attempts], stream=stream,
                        ms=int((time.monotonic() - started) * 1000))
            if stream:
                return StreamingResponse(body, media_type=resp.headers.get("content-type", "text/event-stream"),
                                         headers=headers)
            data.setdefault("model", model)
            return JSONResponse(data, headers=headers)

        assert last is not None
        message = ("pinned provider failed" if not failover else "all providers in the fallback chain failed")
        detail = {"error": {"message": f"llm_router: {message}", "type": "llm_router_error",
                            "attempts": attempts, "upstream": last.body}}
        status = last.status if (not failover and last.status >= 400) else 503
        return JSONResponse(detail, status_code=status)

    def status(self) -> dict[str, Any]:
        cfg = self.config.get()
        return {
            "mode": cfg.mode,
            "manual_provider": cfg.manual_provider,
            "fallback_order": list(cfg.fallback_order),
            "providers": {
                n: {
                    "configured": p.configured,
                    "base_url": p.base_url,
                    "model": p.model,
                    "fast_model": p.fast_model,
                    "timeout_s": p.timeout,
                    "cooldown_remaining_s": round(self.health.cooling(n), 1),
                    "last_error": self.health.last_error.get(n),
                }
                for n, p in cfg.providers.items()
            },
        }


# --------------------------------------------------------------------------- #
# HTTP app
# --------------------------------------------------------------------------- #


def create_app(router: Optional[Router] = None) -> FastAPI:
    router = router or Router()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await router.aclose()

    app = FastAPI(title="OpenMontage LLM Router", version="1.0", lifespan=lifespan)
    app.state.router = router

    def _auth(request: Request) -> None:
        key = router.config.get().router_api_key
        if key and request.headers.get("authorization", "") != f"Bearer {key}":
            raise HTTPException(401, "invalid router API key")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, **router.status()}

    @app.get("/v1/models")
    async def models(request: Request) -> dict[str, Any]:
        _auth(request)
        cfg = router.config.get()
        ids = sorted(AUTO_MODELS | FAST_MODELS) + [
            f"{n}/{m}" for n, p in cfg.providers.items() for m in dict.fromkeys((p.model, p.fast_model))
        ]
        return {"object": "list", "data": [{"id": i, "object": "model", "owned_by": "llm_router"} for i in ids]}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        _auth(request)
        try:
            payload = await request.json()
        except ValueError:
            raise HTTPException(400, "body must be JSON")
        if not isinstance(payload, dict) or not payload.get("messages"):
            raise HTTPException(400, "'messages' is required")
        return await router.complete(payload)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=os.environ.get("LLM_ROUTER_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    env = _parse_env_file(ENV_PATH)
    host = os.environ.get("LLM_ROUTER_HOST") or env.get("LLM_ROUTER_HOST", "127.0.0.1")
    port = int(os.environ.get("LLM_ROUTER_PORT") or env.get("LLM_ROUTER_PORT", "4000"))
    uvicorn.run(app, host=host, port=port, log_level="info")
