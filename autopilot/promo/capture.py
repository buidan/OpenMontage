"""Stage 1: Godot Movie Maker capture -> normalised MP4 + sidecar manifest in the inbox.

The project's window_*_override (432x864 preview size) beats --resolution, so the
capture size is forced with a temporary ``override.cfg`` in the game repo, a standard
Godot mechanism. It is created only if absent and always removed afterwards. The window
is borderless so macOS does not shrink it to the screen (which clips the UI's edges).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from autopilot.media_utils import MediaError, probe, run

log = logging.getLogger("autopilot.promo.capture")


def _format_args(app: dict[str, Any], params: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    locale_ui = {"ui": app["locale_cfg"]["ui"]} if app.get("locale_cfg", {}).get("ui") else {}
    values = {**app["capture"].get("defaults", {}), **locale_ui, **{k: v for k, v in params.items() if v is not None}}
    return [str(a).format(**values) for a in app["capture"]["args"]], values


def capture(app: dict[str, Any], registry: dict[str, Any], inbox: Path,
            params: Optional[dict[str, Any]] = None) -> Path:
    """Record one promo clip; returns the manifest path."""
    params = params or {}
    repo = Path(app["path"])
    args, values = _format_args(app, params)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    item = next((values[k] for k in ("story", "song", "lesson", "type") if values.get(k)), "demo")
    name = "_".join(str(p) for p in (app["id"], item, values.get("target", ""), app.get("locale", ""), stamp) if p)
    work = inbox / "raw"
    work.mkdir(parents=True, exist_ok=True)
    raw = work / f"{name}.avi"
    events_path = work / f"{name}.events.json"
    w, h = registry.get("size", [1080, 1920])
    fps = int(registry.get("fps", 30))

    override = repo / "override.cfg"
    if override.exists():
        raise MediaError(f"{override} already exists; refusing to overwrite it")
    # Borderless: macOS constrains a titled window taller than the screen, and the UI then
    # lays out for the constrained size (edges clipped). A borderless window is not constrained.
    override.write_text(f"[display]\nwindow/size/window_width_override={w}\nwindow/size/window_height_override={h}\n"
                        "window/size/borderless=true\n", encoding="utf-8")
    try:
        cmd = [registry["godot"], "--path", str(repo), "--position", "0,0",
               "--write-movie", str(raw), "--fixed-fps", str(fps),
               app["capture"]["scene"], "--", *args, f"--promo-events={events_path}"]
        log.info("Capturing %s: %s", app["display_name"], " ".join(args))
        # Movie Maker renders slower than real time; allow ~6x the clip's max length.
        proc = run(cmd, timeout=float(app["capture"].get("max_seconds", 120)) * 6 + 60)
    finally:
        override.unlink(missing_ok=True)

    if "PROMO END" not in (proc.stdout or ""):
        raise MediaError("Godot exited without PROMO END; PromoBot did not finish:\n" + (proc.stdout or "")[-1500:])
    timeline = json.loads(events_path.read_text(encoding="utf-8"))

    # FR-G3: trim the first trim_start seconds, H.264 + AAC, constant frame rate.
    trim = float(registry.get("trim_start", 0.5))
    clip = inbox / f"{name}.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{trim}", "-i", str(raw), "-vf", f"scale={w}:{h},fps={fps}",
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(clip)], timeout=900)
    raw.unlink(missing_ok=True)
    dur = float(probe(clip)["format"]["duration"])

    events = [dict(e, t=round(max(0.0, float(e["t"]) - trim), 3)) for e in timeline.get("events", [])]
    manifest = {
        "app_id": app["id"],
        "locale": app.get("locale", "en"),
        "app_display_name": app["display_name"],
        "template": app["template"],
        "clip_path": str(clip),
        "duration_sec": round(dur, 3),
        "preserve_game_audio": True,
        "capture_params": values,
        "stopped": timeline.get("stopped"),
        "events": events,
        "cta_store_badges": app.get("stores", []),
    }
    out = inbox / f"{name}.json"
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log.info("Captured %.1fs -> %s", dur, out)
    return out
