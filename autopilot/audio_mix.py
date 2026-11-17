"""Narration + background music mix with deterministic -18 dB ducking.

Because we know exactly when each scene's narration plays, ducking is a
time-based gain envelope instead of a compressor: the music bed sits at
``MUSIC_BED_DB`` when nobody speaks and drops to ``MUSIC_DUCK_DB``
(default -18 dB) under the voice. The final mix is loudness-normalised to
-14 LUFS, the target YouTube/Instagram/TikTok normalise to anyway.
"""

from __future__ import annotations

import os
import random
from pathlib import Path
from typing import Optional

from autopilot.media_utils import run

MUSIC_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


def pick_music(explicit: Optional[str], library: Path, seed: Optional[str] = None) -> Optional[Path]:
    if explicit:
        if explicit.lower() == "none":
            return None
        p = Path(explicit)
        if not p.is_file():
            raise FileNotFoundError(f"music file not found: {p}")
        return p
    tracks = sorted(p for p in library.glob("**/*") if p.suffix.lower() in MUSIC_EXTS)
    if not tracks:
        return None
    return random.Random(seed).choice(tracks)


def mix(narration: Path, spans: list[tuple[float, float]], total_seconds: float,
        music: Optional[Path], out_path: Path) -> Path:
    duck_db = float(os.environ.get("MUSIC_DUCK_DB", "-18"))
    bed_db = float(os.environ.get("MUSIC_BED_DB", "-10"))
    fade_out = min(2.0, total_seconds / 4)
    voice = (f"[0:a]aresample=48000,aformat=channel_layouts=stereo,apad,"
             f"atrim=0:{total_seconds:.3f}[v]")
    if music is None:
        graph = f"{voice};[v]loudnorm=I=-14:TP=-1.5:LRA=11[out]"
        inputs = ["-i", str(narration)]
    else:
        # Duck slightly before speech starts and release slightly after it ends.
        speaking = "+".join(f"between(t,{max(0, s - 0.15):.3f},{e + 0.25:.3f})" for s, e in spans) or "0"
        gain = f"if(gt({speaking},0),{10 ** (duck_db / 20):.5f},{10 ** (bed_db / 20):.5f})"
        graph = (
            f"{voice};"
            f"[1:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{total_seconds:.3f},"
            f"volume='{gain}':eval=frame,afade=t=in:d=1.0,"
            f"afade=t=out:st={total_seconds - fade_out:.3f}:d={fade_out:.3f}[m];"
            f"[v][m]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11[out]"
        )
        inputs = ["-i", str(narration), "-stream_loop", "-1", "-i", str(music)]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[out]",
         "-ar", "48000", "-c:a", "pcm_s16le", "-t", f"{total_seconds:.3f}", str(out_path)])
    return out_path
