"""Platform-specific titles, descriptions and hashtags via the LLM router."""

from __future__ import annotations

import re
from typing import Any, Callable, Optional

SYSTEM = (
    "You write social video metadata. Be accurate to the script, no clickbait claims the "
    "video does not support, no emojis in titles. Reply with JSON only."
)


def _tags(raw: Any, limit: int) -> list[str]:
    items = raw if isinstance(raw, list) else re.split(r"[\s,]+", str(raw or ""))
    out: list[str] = []
    for t in items:
        t = re.sub(r"[^\w]", "", str(t).lstrip("#"))
        if t and t.lower() not in {x.lower() for x in out}:
            out.append(t)
    return out[:limit]


def _clip(text: Any, n: int) -> str:
    text = " ".join(str(text or "").split()) if n <= 150 else str(text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def generate(title: str, script: str, aspect: str, seconds: float,
             chat_meta: Callable[..., tuple[Any, dict]],
             on_llm_call: Optional[Callable[[dict], None]] = None, language: str = "English") -> dict[str, Any]:
    user = f"""Write every field in {language} (hashtags may mix {language} and English).
Video title draft: {title}
Format: {"vertical short" if aspect == "9:16" else "horizontal long-form"}, {int(seconds)} seconds.
Narration script:
{script[:6000]}

Return JSON:
{{
  "youtube_title": "max 90 characters",
  "youtube_description": "2-4 short paragraphs, plain text",
  "youtube_tags": ["8-15 keyword tags"],
  "facebook_description": "1-3 sentences",
  "instagram_caption": "1-3 sentences, hook first",
  "tiktok_caption": "one punchy line",
  "hashtags": ["5-8 relevant hashtags without #"]
}}"""
    data: dict[str, Any] = {}
    try:
        data, meta = chat_meta([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                               model="auto-router-fast", json_mode=True, max_tokens=1200)
        if on_llm_call:
            on_llm_call(meta)
    except Exception:
        data = {}  # fall back to the script title below; metadata must not block publishing
    if not isinstance(data, dict):
        data = {}

    hashtags = _tags(data.get("hashtags"), 8)
    tag_line = " ".join(f"#{h}" for h in hashtags)
    short = aspect == "9:16" and seconds < 60
    yt_title = _clip(data.get("youtube_title") or title, 100)
    if short and "#shorts" not in yt_title.lower() and len(yt_title) <= 92:
        yt_title = f"{yt_title} #Shorts"
    yt_desc = str(data.get("youtube_description") or title).strip()
    yt_desc = _clip(f"{yt_desc}\n\n{tag_line}{' #Shorts' if short else ''}".strip(), 5000)
    yt_tags = _tags(data.get("youtube_tags"), 15)
    while yt_tags and sum(len(t) + 1 for t in yt_tags) > 480:  # YouTube's 500-char tag budget
        yt_tags.pop()

    return {
        "hashtags": hashtags,
        "youtube": {"title": yt_title, "description": yt_desc, "tags": yt_tags},
        "facebook": {"description": _clip(f"{data.get('facebook_description') or title}\n\n{tag_line}", 2000)},
        "instagram": {"caption": _clip(f"{data.get('instagram_caption') or title}\n\n{tag_line}", 2200)},
        "tiktok": {"title": _clip(f"{data.get('tiktok_caption') or title} {tag_line}", 2200)},
    }
