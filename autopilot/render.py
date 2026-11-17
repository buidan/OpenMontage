"""Build Explainer props and render through OpenMontage's Remotion path."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Optional

from autopilot.visuals import Shot

PROFILES = {"9:16": "youtube_shorts", "16:9": "youtube_landscape"}


def build_props(shots: list[Shot], captions: list[dict], audio_path: Path, aspect: str,
                total_seconds: float) -> dict[str, Any]:
    cuts: list[dict[str, Any]] = []
    for s in shots:
        cut: dict[str, Any] = {"id": s.id, "in_seconds": round(s.start, 3), "out_seconds": round(s.end, 3)}
        if s.kind == "text":
            cut.update(type="text_card", source="", text=s.text)
        else:
            cut.update(source=s.path, transition_in="fade", transition_out="fade", transition_duration=0.25)
            if s.animation:
                cut["animation"] = s.animation
        cuts.append(cut)
    # The last shot must reach the very end so the tail never shows a bare background.
    if cuts:
        cuts[-1]["out_seconds"] = round(total_seconds, 3)
    vertical = aspect == "9:16"
    return {
        "renderer_family": "explainer-data",
        "playbook": os.environ.get("AUTOPILOT_THEME", "flat-motion-graphics"),
        "tailPaddingSeconds": 0,
        "cuts": cuts,
        "overlays": [],
        "captions": captions,
        "captionStyle": ({"wordsPerPage": 3, "fontSize": 68, "bottomOffset": 560} if vertical
                         else {"wordsPerPage": 6, "fontSize": 46, "bottomOffset": 90}),
        "audio": {"narration": {"src": str(audio_path), "volume": 1.0}},
    }


def render(props: dict[str, Any], output_path: Path, aspect: str,
           execute: Optional[Callable[[dict], Any]] = None) -> Path:
    if execute is None:
        from tools.video.video_compose import VideoCompose
        execute = VideoCompose().execute
    seconds = max((c["out_seconds"] for c in props["cuts"]), default=60)
    # video_compose kills the render after max(600s, timeout_ms/1000 + 60). CPU-only boxes
    # render 1080p at a few fps (slower still when other jobs share the CPU), so budget
    # ~40s of wall time per second of video, never less than 15 minutes.
    timeout_ms = int(os.environ.get("REMOTION_TIMEOUT_MS") or max(900_000, seconds * 40_000))
    inputs: dict[str, Any] = {
        "operation": "remotion_render",
        "edit_decisions": props,
        "output_path": str(output_path),
        "profile": PROFILES[aspect],
        "remotion_timeout_ms": timeout_ms,
    }
    if os.environ.get("REMOTION_CONCURRENCY"):
        inputs["remotion_concurrency"] = int(os.environ["REMOTION_CONCURRENCY"])
    result = execute(inputs)
    if not getattr(result, "success", False):
        raise RuntimeError(f"Remotion render failed: {getattr(result, 'error', result)}")
    if not output_path.exists():
        raise RuntimeError(f"render reported success but {output_path} is missing")
    return output_path
