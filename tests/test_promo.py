"""Tests for the Kawaii promo assembler (no Godot, no rendering)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autopilot.promo import app_config, assemble, load_registry  # noqa: E402

APP = {
    "id": "stories", "display_name": "Kawaii Stories", "stores": ["App Store", "Google Play"],
    "approved_claims": ["Kids learn {language} through short illustrated stories"],
    "fallback_copy": {"hook": "Stories that teach {language}.", "hook_overlay": "Learn {language}",
                      "outro": "Where will {hero} go? Find out in Kawaii Stories."},
}
CTX = {"title": "Bun's Morning Walk", "blurb": "b", "hero": "Bun", "language": "Chinese",
       "choice_prompt": "Where should Bun go next?", "cover": None, "story_id": "ch01",
       "video_desc": "a story read aloud", "outro_ask": "tease the choice and name the app"}


def test_registry_only_exposes_ready_apps():
    reg = load_registry()
    assert app_config("stories", reg)["path"].endswith("kawaii-stories")
    for app_id, app in reg["apps"].items():
        cfg = app_config(app_id, reg)
        assert cfg["template"] in assemble.TEMPLATES, app_id
        assert cfg["approved_claims"] and cfg["fallback_copy"]["outro"], app_id
        assert cfg["display_name"] in cfg["fallback_copy"]["outro"], app_id
    reg["apps"]["math"] = {**reg["apps"]["math"], "status": "todo"}
    with pytest.raises(NotImplementedError):
        app_config("math", reg)
    with pytest.raises(KeyError):
        app_config("chess", reg)


def test_copy_falls_back_without_llm():
    copy, source = assemble.make_copy(APP, CTX, None)
    assert source.startswith("fallback")
    assert copy["outro"] == "Where will Bun go? Find out in Kawaii Stories."


@pytest.mark.parametrize("hook", ["90% of kids fail this", "The best way to learn", "Free Chinese lessons"])
def test_copy_rejects_invented_claims(hook):
    def chat(messages, **kw):
        return {"hook": hook, "hook_overlay": "Learn Chinese", "outro": "Find out in Kawaii Stories"}, {}
    copy, source = assemble.make_copy(APP, CTX, chat)
    assert source == "fallback" and copy["hook"] == "Stories that teach Chinese."


def test_copy_accepts_valid_llm_output():
    def chat(messages, **kw):
        assert "PARENTS" in messages[0]["content"]
        return ({"hook": "Bedtime stories that teach your child Chinese", "hook_overlay": "Stories that teach Chinese",
                 "outro": "Where will Bun go? Find out in Kawaii Stories."}, {"provider": "groq"})
    copy, source = assemble.make_copy(APP, CTX, chat)
    assert source == "llm (groq)" and copy["hook"].startswith("Bedtime")


def test_story_cliffhanger_timeline(monkeypatch, tmp_path):
    def fake_vo(text, out_dir, voice, app=None):
        return tmp_path / f"{out_dir.name}.wav", [{"word": "hi", "startMs": 0, "endMs": 500}], 2.0
    monkeypatch.setattr(assemble, "_vo", fake_vo)
    manifest = {"clip_path": "/clip.mp4", "duration_sec": 15.0,
                "events": [{"t": 0, "type": "start"}, {"t": 11.0, "type": "choice"}]}
    copy = {"hook": "h", "hook_overlay": "o", "outro": "x"}
    tl = assemble.story_cliffhanger(APP, manifest, CTX, copy, tmp_path, None)
    intro = tl.cuts[0]["out_seconds"]
    assert intro == pytest.approx(2.8)
    assert [c["id"] for c in tl.cuts] == ["hook", "gameplay", "endcard"]
    assert tl.cuts[1]["in_seconds"] == intro and tl.cuts[1]["out_seconds"] == pytest.approx(intro + 15)
    assert tl.cuts[2]["out_seconds"] == pytest.approx(tl.total)
    assert tl.overlays == []
    assert tl.game_audio == (Path("/clip.mp4"), intro)
    # Voiceover captions only (the app already shows its own text during gameplay).
    assert [c["startMs"] for c in tl.captions] == [300, int(round((intro + 15 + 0.4) * 1000))]
    assert "App Store & Google Play" in tl.cuts[2]["heroSubtitle"]


def test_designed_end_card_is_used_when_present(monkeypatch, tmp_path):
    monkeypatch.setattr(assemble, "_vo", lambda t, d, v, a=None: (tmp_path / "v.wav", [], 1.0))
    (tmp_path / "design").mkdir()
    (tmp_path / "design" / "end.png").write_bytes(b"png")
    app = {**APP, "path": str(tmp_path), "end_card": "design/end.png", "cta": "Coming soon"}
    tl = assemble.story_cliffhanger(app, {"clip_path": "/c.mp4", "duration_sec": 5, "events": []}, CTX,
                                    {"hook": "h", "hook_overlay": "o", "outro": "x"}, tmp_path, None)
    assert tl.cuts[2]["source"].endswith("design/end.png") and "type" not in tl.cuts[2]
