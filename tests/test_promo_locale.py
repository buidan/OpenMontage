"""Audience-language support: locale files, merging, voices, captions, publish gate."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autopilot.promo import app_config, assemble, load_registry, locale  # noqa: E402

REG = load_registry()


def test_every_ready_app_has_vietnamese_copy_marked_draft():
    for app_id, a in REG["apps"].items():
        vi = a.get("locales", {}).get("vi")
        assert vi, f"{app_id} has no vi copy"
        assert vi["review"] in {"draft", "approved"}
        assert vi["approved_claims"] and vi["fallback_copy"]["outro"] and vi["cta"]
        assert a["display_name"] in vi["fallback_copy"]["outro"], app_id


def test_resolve_merges_locale_over_app():
    app = app_config("stories", REG, "vi")
    assert app["locale"] == "vi" and app["review"] == "draft"
    assert app["cta"].startswith("Sắp ra mắt")
    assert app["language_names"]["zh"] == "tiếng Trung"
    assert app["locale_cfg"]["voice"] == "vi_VN-vais1000-medium"
    assert app["stickers"]["challenge"] == "Bé giải được không?"
    assert app["stickers"]["tada"] == "Ta-da!"
    en = app_config("stories", REG, "en")
    assert en["review"] == "approved" and en["cta"].startswith("Coming soon")


def test_locale_without_app_copy_is_refused():
    with pytest.raises(KeyError, match="no 'ja' copy"):
        app_config("math", REG, "ja")


def test_draft_locale_cannot_publish(tmp_path):
    manifest = tmp_path / "m.json"
    manifest.write_text('{"app_id": "math", "template": "timed_challenge", "locale": "vi", '
                        '"clip_path": "x.mp4", "duration_sec": 5, "events": []}')
    with pytest.raises(PermissionError, match="not approved"):
        assemble.assemble(manifest, ["youtube"], dry_run=False, use_llm=False)


def test_paid_voice_needs_explicit_permission(tmp_path):
    reg = load_registry()
    reg["apps"]["math"].setdefault("locales", {})["ja"] = {"approved_claims": ["x"], "fallback_copy": {}}
    app = locale.resolve({**reg["apps"]["math"], "id": "math"}, reg, "ja")
    with pytest.raises(locale.PaidVoiceNotAllowed, match="--allow-paid-voice"):
        locale.voiceover("こんにちは", tmp_path / "vo", app, allow_paid=False)


def test_cjk_tokens_per_character_with_punctuation_breaks():
    align = {"characters": list("你好。再见！"),
             "character_start_times_seconds": [0, .2, .4, .6, .8, 1.0],
             "character_end_times_seconds": [.2, .4, .5, .8, 1.0, 1.1]}
    toks = locale.cjk_tokens(align)
    assert [t["word"] for t in toks] == ["你", "好。", "再", "见！"]
    assert [t["pageBreakAfter"] for t in toks] == [False, True, False, True]
    assert toks[1]["endMs"] == 500


def test_caption_style_per_locale():
    style, props = locale.caption_style(app_config("stories", REG, "vi"))
    assert style["wordsPerPage"] == 4 and props == {}
    reg = load_registry()
    reg["apps"]["math"].setdefault("locales", {})["zh"] = {"approved_claims": ["x"], "fallback_copy": {}}
    style, props = locale.caption_style(locale.resolve({**reg["apps"]["math"], "id": "math"}, reg, "zh"))
    assert props == {"wordSeparator": ""} and style["wordsPerPage"] == 9


def test_vietnamese_copy_limits_and_banned_terms():
    app = app_config("stories", REG, "vi")
    ctx = {"language": "tiếng Trung", "hero": "Bun", "video_desc": "d", "outro_ask": "a", "title": "t"}
    good = {"hook": "Truyện trước giờ ngủ giúp bé học tiếng Trung mỗi tối.",
            "hook_overlay": "Học tiếng Trung qua truyện",
            "outro": "Bun sẽ đi đâu tiếp theo? Cùng khám phá trong Kawaii Stories, sắp ra mắt."}
    copy, source = assemble.make_copy(app, ctx, lambda m, **k: (good, {"provider": "groq"}))
    assert source == "llm (groq)" and copy == good
    bad = {**good, "hook": "Ứng dụng học tiếng Trung miễn phí cho bé."}
    copy, source = assemble.make_copy(app, ctx, lambda m, **k: (bad, {}))
    assert source == "fallback" and copy["hook"] == "Truyện trước giờ ngủ giúp bé học tiếng Trung."


def test_story_context_uses_audience_language_title():
    app = app_config("stories", REG, "vi")
    ctx = assemble.story_context(app, {"capture_params": {"story": "ch01_morning_walk", "target": "ja"}})
    assert ctx["title"] == "Buổi sáng đi dạo của Bun"
    assert ctx["language"] == "tiếng Nhật"
    zh = {**app, "locale": "zh"}
    assert assemble.story_context(zh, {"capture_params": {"story": "ch01_morning_walk"}})["title"] == "Bun的早晨散步"
