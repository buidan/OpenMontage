"""Per-scene Piper narration + word-level caption timings.

Each scene is synthesised separately, so scene boundaries are exact and
captions can never drift by more than one scene. Inside a scene, word
timings are distributed by spoken length (characters, with extra weight on
punctuation pauses) — accurate enough for word-by-word highlight captions
without needing a speech-to-text pass.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from autopilot.media_utils import duration, run

SCENE_GAP_SECONDS = 0.25  # breath between scenes


@dataclass
class SceneAudio:
    scene_id: str
    path: Path
    start: float
    end: float
    words: list[dict]


def _word_weights(words: list[str]) -> list[float]:
    weights = []
    for w in words:
        weight = len(re.sub(r"[^\w']", "", w)) + 1.5
        if w.endswith((",", ";", ":")):
            weight += 3
        elif w.endswith((".", "!", "?")):
            weight += 5
        weights.append(weight)
    return weights


def word_timings(text: str, start: float, end: float) -> list[dict]:
    words = text.split()
    if not words:
        return []
    weights = _word_weights(words)
    span = max(0.1, end - start)
    unit = span / sum(weights)
    out, t = [], start
    for i, (w, wt) in enumerate(zip(words, weights)):
        w_end = t + wt * unit
        out.append({
            "word": w,
            "startMs": int(round(t * 1000)),
            "endMs": int(round(w_end * 1000)),
            "pageBreakAfter": i == len(words) - 1 or w.endswith((".", "!", "?")),
        })
        t = w_end
    return out


def _tts_text(text: str) -> str:
    """Strip characters Piper reads literally ("dot", "dash")."""
    text = text.replace("—", ", ").replace("–", ", ").replace("…", ".").replace("...", ".")
    text = re.sub(r"[*_#`\[\]{}<>|~^]", " ", text)
    return " ".join(text.split())


def synthesize(scenes, out_dir: Path, voice: Optional[str] = None,
               tts_execute: Optional[Callable[[dict], object]] = None,
               length_scale: Optional[float] = None) -> tuple[Path, list[SceneAudio]]:
    """Render each scene to WAV, then concatenate into one narration track."""
    out_dir.mkdir(parents=True, exist_ok=True)
    voice = voice or os.environ.get("PIPER_VOICE", "en_US-lessac-medium")
    length_scale = float(length_scale or os.environ.get("PIPER_LENGTH_SCALE", "1.0"))
    if tts_execute is None:
        from tools.audio.piper_tts import PiperTTS
        tts_execute = PiperTTS().execute

    results: list[SceneAudio] = []
    t = 0.0
    concat_list = out_dir / "concat.txt"
    silence = out_dir / "gap.wav"
    lines: list[str] = []
    for scene in scenes:
        wav = out_dir / f"{scene.id}.wav"
        res = tts_execute({"text": _tts_text(scene.narration), "model": voice,
                           "length_scale": length_scale, "sentence_silence": 0.2,
                           "output_path": str(wav)})
        if not getattr(res, "success", False):
            raise RuntimeError(f"TTS failed for {scene.id}: {getattr(res, 'error', res)}")
        d = duration(wav)
        if d <= 0.2:
            raise RuntimeError(f"TTS produced empty audio for {scene.id}")
        results.append(SceneAudio(scene.id, wav, t, t + d, word_timings(scene.narration, t, t + d)))
        lines.append(f"file '{wav.resolve()}'")
        t += d
        if scene is not scenes[-1]:
            lines.append(f"file '{silence.resolve()}'")
            t += SCENE_GAP_SECONDS

    # Match the gap's format to the first TTS clip so concat can stream-copy.
    info = run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                "stream=sample_rate,channels", "-of", "default=nw=1:nk=1", str(results[0].path)]).stdout.split()
    rate, channels = (info + ["22050", "1"])[:2]
    layout = "mono" if channels == "1" else "stereo"
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"anullsrc=r={rate}:cl={layout}",
         "-t", str(SCENE_GAP_SECONDS), str(silence)])
    concat_list.write_text("\n".join(lines) + "\n", encoding="utf-8")
    narration = out_dir / "narration.wav"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat_list),
         "-c", "copy", str(narration)])
    return narration, results
