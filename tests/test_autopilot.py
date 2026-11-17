"""Unit tests for the autopilot package (no network, no rendering)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autopilot import metadata, narration, planner, render  # noqa: E402
from autopilot.publishers import PublishError, meta, parse_platforms, tiktok  # noqa: E402
from autopilot.visuals import Shot, VisualSourcer, plan_shot_lengths  # noqa: E402
from run_pipeline import parse_duration  # noqa: E402
from tools.video.stock_sources.base import Candidate  # noqa: E402


# ---------------------------------------------------------------- planner
def _plan_json(words_per_scene=20, scenes=5):
    return {"title": "Octopus hearts", "scenes": [
        {"narration": " ".join(["word"] * words_per_scene) + ".", "visual_query": "octopus reef",
         "fallback_query": "ocean", "on_screen_text": "3 hearts"} for _ in range(scenes)]}


def test_parse_plan_ok():
    plan = planner.parse_plan(_plan_json(), "explainer", 45)
    assert len(plan.scenes) == 5 and plan.scenes[0].id == "s01"


def test_parse_plan_rejects_wrong_length():
    with pytest.raises(ValueError, match="words"):
        planner.parse_plan(_plan_json(words_per_scene=2), "explainer", 45)


def test_make_plan_retries_with_feedback():
    calls = []

    def fake_chat(messages, **kw):
        calls.append(messages[-1]["content"])
        if len(calls) == 1:
            return {"scenes": []}, {"provider": "groq", "fallbacks": 0}
        return _plan_json(), {"provider": "aistudio", "fallbacks": 1}

    seen = []
    plan = planner.make_plan("octopus", "explainer", 45, "9:16", fake_chat, on_llm_call=seen.append)
    assert plan.title == "Octopus hearts"
    assert "rejected" in calls[1]
    assert [m["provider"] for m in seen] == ["groq", "aistudio"]


# ---------------------------------------------------------------- narration
def test_word_timings_cover_span_and_break_on_sentences():
    words = narration.word_timings("Hello there. How are you?", 1.0, 3.0)
    assert words[0]["startMs"] == 1000 and words[-1]["endMs"] == 3000
    assert [w["pageBreakAfter"] for w in words] == [False, True, False, False, True]
    assert all(a["endMs"] == b["startMs"] for a, b in zip(words, words[1:]))


def test_tts_text_strips_literal_punctuation():
    assert narration._tts_text("Wait... what — *really*?") == "Wait. what , really ?"


# ---------------------------------------------------------------- visuals
def test_shot_lengths():
    assert plan_shot_lengths(9.0, 4.5) == [4.5, 4.5]
    assert len(plan_shot_lengths(9.1, 4.5)) == 3


class FakeSource:
    def __init__(self, name, results):
        self.name, self.results, self.downloads = name, results, []

    def is_available(self):
        return True

    def search(self, query, filters):
        return [c for c in self.results if c.kind == filters.kind or filters.kind == "any"]

    def download(self, c, out_path):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"x")
        self.downloads.append(c.clip_id)
        return out_path


def test_visual_sourcer_prefers_long_enough_portrait_video_then_images(tmp_path, monkeypatch):
    import autopilot.visuals as visuals_mod
    monkeypatch.setattr(visuals_mod, "run", lambda cmd, **kw: Path(cmd[-1]).write_bytes(b"jpg"))
    pexels = FakeSource("pexels", [
        Candidate("pexels", "1", "u", "https://x/1.mp4", "video", 1920, 1080, 30),   # landscape: skip
        Candidate("pexels", "2", "u", "https://x/2.mp4", "video", 1080, 1920, 2),    # too short
        Candidate("pexels", "3", "u", "https://x/3.mp4", "video", 1080, 1920, 10),   # pick
        Candidate("pexels", "4", "u", "https://x/4.jpg", "image", 1000, 1500),
    ])
    sourcer = VisualSourcer(tmp_path, "portrait", {"pexels": pexels, "wikimedia": FakeSource("wikimedia", [])})
    scene = planner.Scene("s01", "n", "octopus", "ocean")
    shots = sourcer.shots_for_scene(scene, 0, 8, 4.5, 0)
    assert [s.kind for s in shots] == ["video", "image"]
    assert pexels.downloads == ["pexels_3", "pexels_4"]
    assert shots[1].animation  # Ken Burns on stills
    # Nothing left: next scene degrades to a text card instead of black frames.
    more = sourcer.shots_for_scene(scene, 8, 10, 4.5, 2)
    assert more[0].kind == "text"


# ---------------------------------------------------------------- render props
def test_build_props_extends_last_cut_and_maps_types(tmp_path):
    shots = [Shot("a", "s1", 0, 3, "video", "/v.mp4"), Shot("b", "s1", 3, 5, "image", "/i.jpg", animation="zoom-in"),
             Shot("c", "s2", 5, 6, "text", text="Hi")]
    props = render.build_props(shots, [], tmp_path / "mix.wav", "9:16", 7.0)
    assert props["cuts"][-1]["out_seconds"] == 7.0
    assert props["cuts"][1]["animation"] == "zoom-in"
    assert props["cuts"][2]["type"] == "text_card"
    assert props["tailPaddingSeconds"] == 0
    assert props["captionStyle"]["wordsPerPage"] == 3


# ---------------------------------------------------------------- metadata
def test_metadata_limits_and_shorts_tag():
    def fake_chat(messages, **kw):
        return {"youtube_title": "T" * 200, "hashtags": ["#Ocean", "ocean", "sea life"],
                "youtube_tags": ["a"] * 40}, {"provider": "groq"}
    md = metadata.generate("Title", "script", "9:16", 40, fake_chat)
    assert len(md["youtube"]["title"]) <= 100
    assert md["hashtags"] == ["Ocean", "sealife"]
    assert "#Shorts" in md["youtube"]["description"]


def test_metadata_survives_llm_failure():
    def broken(messages, **kw):
        raise RuntimeError("router down")
    md = metadata.generate("My title", "script", "16:9", 300, broken)
    assert md["youtube"]["title"] == "My title"


# ---------------------------------------------------------------- CLI helpers
def test_parse_platforms_and_duration():
    assert parse_platforms("youtube,meta") == ["youtube", "facebook", "instagram"]
    with pytest.raises(ValueError):
        parse_platforms("myspace")
    assert parse_duration("45") == 45 and parse_duration("3m30s") == 210 and parse_duration("5m") == 300


# ---------------------------------------------------------------- publishers (mocked HTTP)
class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers, self.text = status, body or {}, headers or {}, str(body)

    def json(self):
        return self._body


def test_tiktok_chunk_plan():
    assert tiktok.chunk_plan(10_000_000) == (10_000_000, 1)
    size, count = tiktok.chunk_plan(150 * 1024 * 1024)
    assert size == 10 * 1024 * 1024 and count == 15


def test_tiktok_direct_post_flow(tmp_path, monkeypatch):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0" * 2048)
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "tok")
    monkeypatch.setattr(tiktok.time, "sleep", lambda s: None)
    calls = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append(url.rsplit("/v2", 1)[1])
        if "creator_info" in url:
            return Resp(body={"data": {"creator_username": "me", "privacy_level_options": ["SELF_ONLY"],
                                       "max_video_post_duration_sec": 600}, "error": {"code": "ok"}})
        if "video/init" in url:
            assert json["post_info"]["privacy_level"] == "SELF_ONLY" and json["post_info"]["is_aigc"] is True
            assert json["source_info"] == {"source": "FILE_UPLOAD", "video_size": 2048, "chunk_size": 2048,
                                           "total_chunk_count": 1}
            return Resp(body={"data": {"publish_id": "p1", "upload_url": "https://up"}, "error": {"code": "ok"}})
        return Resp(body={"data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": [77]},
                          "error": {"code": "ok"}})

    puts = []
    monkeypatch.setattr(tiktok.requests, "post", post)
    monkeypatch.setattr(tiktok.requests, "put",
                        lambda url, data=None, headers=None, timeout=None: puts.append(headers) or Resp(201))
    r = tiktok.publish(video, {"tiktok": {"title": "hi"}}, "9:16", 40)
    assert r.status == "published" and r.url == "https://www.tiktok.com/@me/video/77"
    assert puts[0]["Content-Range"] == "bytes 0-2047/2048"


def test_tiktok_rejects_disallowed_privacy(tmp_path, monkeypatch):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0")
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("TIKTOK_PRIVACY_LEVEL", "PUBLIC_TO_EVERYONE")
    monkeypatch.setattr(tiktok.requests, "post", lambda *a, **k: Resp(body={
        "data": {"privacy_level_options": ["SELF_ONLY"]}, "error": {"code": "ok"}}))
    with pytest.raises(PublishError, match="not allowed"):
        tiktok.publish(video, {"tiktok": {"title": "hi"}}, "9:16", 40)


def test_instagram_resumable_flow(tmp_path, monkeypatch):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0" * 10)
    monkeypatch.setenv("META_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("META_IG_USER_ID", "ig1")
    monkeypatch.setattr(meta.time, "sleep", lambda s: None)
    seq = []

    def post(url, data=None, headers=None, timeout=None):
        seq.append(url)
        if url.endswith("/ig1/media"):
            assert data["media_type"] == "REELS" and data["upload_type"] == "resumable"
            return Resp(body={"id": "c1", "uri": "https://rupload.facebook.com/ig-api-upload/v25.0/c1"})
        if "rupload" in url:
            assert headers["offset"] == "0" and headers["file_size"] == "10"
            return Resp(body={"success": True})
        return Resp(body={"id": "m1"})

    statuses = iter([{"status_code": "IN_PROGRESS"}, {"status_code": "FINISHED"}, {"permalink": "https://ig/p/1"}])
    monkeypatch.setattr(meta.requests, "post", post)
    monkeypatch.setattr(meta.requests, "get", lambda *a, **k: Resp(body=next(statuses)))
    r = meta.publish_instagram(video, {"instagram": {"caption": "c"}}, "9:16", 30)
    assert r.post_id == "m1" and r.url == "https://ig/p/1"
    assert seq[-1].endswith("/ig1/media_publish")


def test_facebook_requires_vertical(tmp_path, monkeypatch):
    monkeypatch.setenv("META_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("META_PAGE_ID", "p")
    with pytest.raises(PublishError, match="9:16"):
        meta.publish_facebook(tmp_path / "v.mp4", {}, "16:9", 300)
