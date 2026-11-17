"""Audience-language support for promos: per-locale copy, voice, captions and stickers.

Three independent languages meet in one promo:
  locale  - the parent watching: voiceover, captions, stickers, end card, metadata
  ui      - the app's interface in the captured gameplay (--lang=...)
  target  - what the child learns (Kawaii Stories only, --target=...)

apps.yaml holds a top-level ``locales:`` table (voice, ui code, sticker words, fonts) and
per-app ``locales: {<code>: {...}}`` overrides (claims, fallback copy, CTA, caption) with a
``review`` status. Only ``review: approved`` locales may be published; drafts render only.

Voices: Piper (free, offline) where a voice exists; ElevenLabs (paid) for zh / ja, used
only with allow_paid=True so credits are never spent without an explicit go-ahead.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from autopilot import narration
from autopilot.media_utils import duration, run

CJK = re.compile(r"[぀-ヿ㐀-鿿豈-﫿]")
CJK_BREAK = set("。！？!?，、,；;：:")


class PaidVoiceNotAllowed(RuntimeError):
    pass


def resolve(app: dict[str, Any], registry: dict[str, Any], locale: str = "en") -> dict[str, Any]:
    """App config with the locale's settings merged in (per-app overrides win)."""
    base = dict(registry.get("locales", {}).get(locale, {}))
    if not base:
        raise KeyError(f"unknown locale {locale!r}; known: {', '.join(registry.get('locales', {}))}")
    over = dict(app.get("locales", {}).get(locale, {}))
    if locale != "en" and not over:
        raise KeyError(f"{app['id']}: no '{locale}' copy in apps.yaml (claims / fallback copy / CTA)")
    out = {**app, **{k: v for k, v in over.items()}}
    out["locale"] = locale
    out["locale_cfg"] = base
    out["review"] = over.get("review", "approved" if locale == "en" else "draft")
    out["stickers"] = {**registry.get("locales", {}).get("en", {}).get("stickers", {}),
                       **base.get("stickers", {}), **over.get("stickers", {})}
    out["language_names"] = {**app.get("language_names", {}), **base.get("language_names", {})}
    if base.get("sticker_font"):
        out["sticker_font"] = base["sticker_font"]
    return out


def _load_elevenlabs_key() -> None:
    if os.environ.get("ELEVENLABS_API_KEY"):
        return
    key_file = Path(os.environ.get("ELEVENLABS_KEY_FILE", "~/.elevenlabs_key")).expanduser()
    if key_file.is_file():
        os.environ["ELEVENLABS_API_KEY"] = key_file.read_text(encoding="utf-8").strip()


def cjk_tokens(alignment: dict[str, Any], offset: float = 0.0) -> list[dict[str, Any]]:
    """Per-character caption tokens from an ElevenLabs alignment (no spaces in zh / ja)."""
    chars = alignment["characters"]
    starts = alignment["character_start_times_seconds"]
    ends = alignment["character_end_times_seconds"]
    out: list[dict[str, Any]] = []
    for ch, s, e in zip(chars, starts, ends):
        if not ch.strip():
            continue
        if ch in CJK_BREAK and out:  # punctuation joins the previous character and ends the page
            out[-1]["word"] += ch
            out[-1]["endMs"] = int(round((e + offset) * 1000))
            out[-1]["pageBreakAfter"] = True
            continue
        out.append({"word": ch, "startMs": int(round((s + offset) * 1000)),
                    "endMs": int(round((e + offset) * 1000)), "pageBreakAfter": False})
    if out:
        out[-1]["pageBreakAfter"] = True
    return out


def voiceover(text: str, out_dir: Path, app: dict[str, Any], allow_paid: bool = False,
              voice_override: Optional[str] = None) -> tuple[Path, list[dict], float]:
    """Synthesize one voiceover line; returns (wav, caption tokens from 0 s, seconds)."""
    cfg = app.get("locale_cfg", {"tts": "piper"})
    if cfg.get("tts", "piper") == "piper":
        scene = type("VO", (), {"id": out_dir.name, "narration": text})()
        wav, parts = narration.synthesize([scene], out_dir, voice=voice_override or cfg.get("voice"),
                                          length_scale=cfg.get("length_scale"))
        return wav, parts[0].words, parts[0].end

    if cfg["tts"] != "elevenlabs":
        raise ValueError(f"unknown tts {cfg['tts']!r}")
    if not allow_paid:
        raise PaidVoiceNotAllowed(
            f"{app.get('locale')} voiceover uses ElevenLabs (paid, ~{len(text)} characters). "
            "Re-run with --allow-paid-voice to spend credits.")
    _load_elevenlabs_key()
    from tools.audio.elevenlabs_tts import ElevenLabsTTS

    out_dir.mkdir(parents=True, exist_ok=True)
    mp3 = out_dir / f"{out_dir.name}.mp3"
    res = ElevenLabsTTS().execute({
        "text": text, "voice_id": voice_override or cfg["voice_id"], "model_id": cfg.get("model", "eleven_v3"),
        "stability": 0.6, "similarity_boost": 0.8, "style": 0.1, "speed": 0.95,
        "language_code": cfg.get("language_code"), "output_path": str(mp3), "with_timestamps": True,
    })
    if not res.success:
        raise RuntimeError(f"ElevenLabs voiceover failed: {res.error}")
    wav = out_dir / "narration.wav"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(mp3), "-ar", "48000", "-ac", "1", str(wav)])
    secs = duration(wav)
    align_path = Path(res.data.get("alignment_path", ""))
    if align_path.is_file():
        align = json.loads(align_path.read_text(encoding="utf-8"))["alignment"]
        words = cjk_tokens(align) if CJK.search(text) else [
            {"word": w["word"], "startMs": int(w["start"] * 1000), "endMs": int(w["end"] * 1000)}
            for w in res.data.get("word_timestamps", [])]
    else:
        words = narration.word_timings(text, 0.0, secs)
    return wav, words, secs


def caption_style(app: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(captionStyle, captionProps) for the locale: CJK has no word spaces, more glyphs per page."""
    if app.get("locale_cfg", {}).get("cjk"):
        return {"wordsPerPage": 9, "fontSize": 72, "bottomOffset": 520}, {"wordSeparator": ""}
    per_page = int(app.get("locale_cfg", {}).get("words_per_page", 3))
    return {"wordsPerPage": per_page, "fontSize": 76, "bottomOffset": 520}, {}
