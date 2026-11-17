"""Platform publishers. Each exposes ``publish(video, metadata, aspect, seconds) -> PublishResult``."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

PLATFORMS = ("youtube", "facebook", "instagram", "tiktok")
ALIASES = {"meta": ("facebook", "instagram"), "fb": ("facebook",), "ig": ("instagram",), "yt": ("youtube",)}


@dataclass
class PublishResult:
    platform: str
    status: str                    # published | draft | failed | skipped
    post_id: Optional[str] = None
    url: Optional[str] = None
    error: Optional[str] = None
    detail: dict[str, Any] = field(default_factory=dict)


class PublishError(RuntimeError):
    pass


def parse_platforms(raw: str) -> list[str]:
    out: list[str] = []
    for item in (p.strip().lower() for p in (raw or "").split(",") if p.strip()):
        for p in ALIASES.get(item, (item,)):
            if p not in PLATFORMS:
                raise ValueError(f"unknown platform {p!r}; choose from {', '.join(PLATFORMS)} (or meta)")
            if p not in out:
                out.append(p)
    return out


def get_publisher(platform: str):
    if platform == "youtube":
        from autopilot.publishers import youtube as mod
        return mod.publish
    if platform == "facebook":
        from autopilot.publishers import meta as mod
        return mod.publish_facebook
    if platform == "instagram":
        from autopilot.publishers import meta as mod
        return mod.publish_instagram
    if platform == "tiktok":
        from autopilot.publishers import tiktok as mod
        return mod.publish
    raise ValueError(platform)


def check_response(resp, what: str) -> dict:
    """Raise PublishError with the API's own message on non-2xx."""
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text[:500]}
    if resp.status_code >= 300:
        raise PublishError(f"{what} failed: HTTP {resp.status_code} {str(body)[:600]}")
    return body
