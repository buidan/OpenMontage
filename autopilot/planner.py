"""Script + scene plan generation through the LLM router (JSON mode)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

WORDS_PER_SECOND = 2.5  # Piper en_US voices at length_scale 1.0 land around 145-160 wpm

TEMPLATES = {
    "explainer": (
        "An explainer: open with a curiosity hook, explain the idea step by step with one "
        "concrete example or number per beat, and land on a memorable takeaway."
    ),
    "listicle": (
        "A countdown/listicle: a hook, then numbered items (say the number aloud, e.g. "
        "'Number three:'), each with a surprising specific detail, then a quick wrap-up."
    ),
    "news_recap": (
        "A neutral news recap: what happened, why it matters, key numbers, and what to "
        "watch next. Use ONLY facts from the provided source notes; never invent quotes, "
        "dates, or figures. If the notes are thin, stay general rather than guessing."
    ),
}

SYSTEM = (
    "You are a senior short-form video scriptwriter. You write narration that sounds "
    "natural when read by a text-to-speech voice: short sentences, no stage directions, "
    "no emojis, no hashtags, no markdown, no bracketed notes, no URLs, and numbers written "
    "the way they should be spoken. Every scene must be visually searchable on free stock "
    "sites, so its visual_query is a concrete, filmable 2-4 word subject (e.g. 'city traffic "
    "night', 'scientist microscope'), never an abstract idea. You reply with JSON only."
)


@dataclass
class Scene:
    id: str
    narration: str
    visual_query: str
    fallback_query: str
    on_screen_text: str = ""


@dataclass
class Plan:
    title: str
    template: str
    scenes: list[Scene]
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def narration_text(self) -> str:
        return " ".join(s.narration for s in self.scenes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "template": self.template,
            "scenes": [s.__dict__ for s in self.scenes],
        }


def scene_budget(target_seconds: float) -> tuple[int, int]:
    """(scene_count, total_words) for a target duration."""
    words = int(target_seconds * WORDS_PER_SECOND)
    if target_seconds <= 90:
        scenes = max(4, min(9, round(target_seconds / 7)))
    else:
        scenes = max(10, min(48, round(target_seconds / 12)))
    return scenes, words


def build_messages(topic: str, template: str, target_seconds: float, aspect: str,
                   facts: Optional[str] = None, feedback: Optional[str] = None) -> list[dict[str, str]]:
    scenes, words = scene_budget(target_seconds)
    fmt = "vertical 9:16 short (YouTube Shorts / Reels / TikTok)" if aspect == "9:16" else "horizontal 16:9 YouTube video"
    user = f"""Write a {fmt} about: {topic}

Format: {TEMPLATES[template]}
Length: about {words} spoken words in total (target {int(target_seconds)} seconds), split into {scenes} scenes.
The first scene is the hook and must grab attention within 2 seconds. The last scene ends with a one-line call to follow.

Return exactly this JSON shape:
{{
  "title": "punchy video title, max 70 characters",
  "scenes": [
    {{
      "narration": "what the voice says in this scene",
      "visual_query": "concrete 2-4 word stock footage search",
      "fallback_query": "broader 1-2 word search if the first finds nothing",
      "on_screen_text": "optional 1-4 word overlay or empty string"
    }}
  ]
}}"""
    if facts:
        user += f"\n\nSource notes (the only facts you may state):\n{facts.strip()[:12000]}"
    if feedback:
        user += f"\n\nYour previous answer was rejected: {feedback}. Fix it and return the full JSON again."
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def parse_plan(data: Any, template: str, target_seconds: float) -> Plan:
    if not isinstance(data, dict):
        raise ValueError("top-level JSON must be an object")
    raw_scenes = data.get("scenes")
    if not isinstance(raw_scenes, list) or len(raw_scenes) < 2:
        raise ValueError("'scenes' must be a list with at least 2 scenes")
    scenes: list[Scene] = []
    for i, s in enumerate(raw_scenes, 1):
        if not isinstance(s, dict):
            raise ValueError(f"scene {i} is not an object")
        narration = " ".join(str(s.get("narration") or "").split())
        query = " ".join(str(s.get("visual_query") or "").split())
        if not narration or not query:
            raise ValueError(f"scene {i} is missing narration or visual_query")
        scenes.append(Scene(
            id=f"s{i:02d}",
            narration=narration,
            visual_query=query,
            fallback_query=" ".join(str(s.get("fallback_query") or "").split()) or query.split()[0],
            on_screen_text=" ".join(str(s.get("on_screen_text") or "").split())[:40],
        ))
    words = sum(len(s.narration.split()) for s in scenes)
    _, target_words = scene_budget(target_seconds)
    if words < target_words * 0.5 or words > target_words * 1.8:
        raise ValueError(f"narration has {words} words; target is about {target_words}")
    title = " ".join(str(data.get("title") or "").split())[:100] or scenes[0].narration[:70]
    return Plan(title=title, template=template, scenes=scenes, raw=data)


def make_plan(topic: str, template: str, target_seconds: float, aspect: str,
              chat_meta: Callable[..., tuple[Any, dict]], facts: Optional[str] = None,
              on_llm_call: Optional[Callable[[dict], None]] = None, attempts: int = 3) -> Plan:
    if template not in TEMPLATES:
        raise ValueError(f"unknown template {template!r}; choose from {sorted(TEMPLATES)}")
    feedback: Optional[str] = None
    max_tokens = 2048 if target_seconds <= 90 else 8192
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            data, meta = chat_meta(build_messages(topic, template, target_seconds, aspect, facts, feedback),
                                   json_mode=True, max_tokens=max_tokens)
        except ValueError as exc:  # router returned something that is not JSON
            last_error, feedback = exc, "it was not valid JSON"
            continue
        if on_llm_call:
            on_llm_call(meta)
        try:
            return parse_plan(data, template, target_seconds)
        except ValueError as exc:
            last_error, feedback = exc, str(exc)
    raise RuntimeError(f"could not get a valid script after {attempts} attempts: {last_error}")
