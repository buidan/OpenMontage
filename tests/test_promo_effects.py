"""Effects layer tests: real ffmpeg on a small synthetic clip (no Godot, no Remotion)."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autopilot.media_utils import probe, run  # noqa: E402
from autopilot.promo import effects as fx, templates  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


@pytest.fixture(scope="module")
def clip(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("clip") / "c.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=270x480:r=30:d=6",
         "-f", "lavfi", "-i", "sine=f=440:d=6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-shortest", str(out)])
    return out


def test_timemap_ramp_and_freeze():
    m = fx.TimeMap([fx.Segment(0, 4, rate=2.0), fx.Segment(4, freeze=3.0), fx.Segment(4, 6)])
    assert m(2) == pytest.approx(1.0)
    assert m(4) == pytest.approx(2.0)
    assert m(5) == pytest.approx(6.0)          # 2 (ramp) + 3 (freeze) + 1
    assert m.duration == pytest.approx(7.0)


def test_atempo_chain_stays_in_range():
    assert fx._atempo(4.0) == "atempo=2.0,atempo=2.0000"
    assert fx._atempo(1.5) == "atempo=1.5000"


def test_retime_output_duration(clip, tmp_path):
    out, tmap = fx.retime(clip, tmp_path / "r.mp4", [fx.Segment(0, 4, rate=2.0), fx.Segment(4, freeze=1.5),
                                                     fx.Segment(4, 6)])
    info = probe(out)
    assert float(info["format"]["duration"]) == pytest.approx(tmap.duration, abs=0.15)
    assert {s["codec_type"] for s in info["streams"]} == {"video", "audio"}


def test_sticker_and_overlay(clip, tmp_path):
    png = fx.sticker("Can you solve it?", tmp_path / "s.png", font_path=None)
    from PIL import Image
    assert Image.open(png).mode == "RGBA"
    out = fx.overlay(clip, tmp_path / "o.mp4", [fx.Placed(png, 1, 3, y=10)])
    assert float(probe(out)["format"]["duration"]) == pytest.approx(6.0, abs=0.15)


def test_split_layout_keeps_frame_size(clip, tmp_path):
    big = tmp_path / "big.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-vf", "scale=1080:1920", "-c:a", "copy", str(big)])
    top = fx.banner("Which cube?", tmp_path / "b.png", height=420, font_path=None, bg="#FEF8F1", fg="#3B2F2F",
                    accent="#F08A7E")
    out = fx.split_layout(big, tmp_path / "split.mp4", top, 420, "#FEF8F1")
    v = next(s for s in probe(out)["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1080, 1920)


def test_timed_challenge_freezes_when_bot_did_not_think(clip, tmp_path):
    manifest = {"clip_path": str(clip), "template": "timed_challenge", "events": [
        {"t": 1.0, "type": "state", "state": "p0:asking"}, {"t": 1.6, "type": "state", "state": "p0:answered"}]}
    out = templates.prepare({"path": str(tmp_path)}, manifest, tmp_path / "fx", {})
    assert out.name == "challenge.mp4"
    assert float(probe(out)["format"]["duration"]) > 6.0 + 2.5   # freeze inserted


def test_prepare_without_events_returns_raw_clip(clip, tmp_path):
    manifest = {"clip_path": str(clip), "template": "logic_execution", "events": []}
    assert templates.prepare({}, manifest, tmp_path / "fx", {}) == clip
