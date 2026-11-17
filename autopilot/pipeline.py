"""End-to-end unattended run: plan -> narrate -> source visuals -> mix -> render -> QA -> publish."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from autopilot import audio_mix, metadata as meta_mod, narration, planner, qa, render
from autopilot.db import RunLog
from autopilot.media_utils import require_binaries
from autopilot.publishers import PublishResult, get_publisher
from autopilot.visuals import Shot, VisualSourcer

log = logging.getLogger("autopilot")
ROOT = Path(__file__).resolve().parent.parent
SIZES = {"9:16": (1080, 1920), "16:9": (1920, 1080)}
TAIL_SECONDS = 0.8


@dataclass
class RunOptions:
    topic: str
    platforms: list[str]
    aspect: str = "9:16"
    seconds: float = 45
    template: str = "explainer"
    facts: Optional[str] = None
    music: Optional[str] = None
    voice: Optional[str] = None
    project_id: Optional[str] = None
    dry_run: bool = False


@dataclass
class RunResult:
    run_id: int
    project_dir: Path
    video: Optional[Path] = None
    qa: dict[str, Any] = field(default_factory=dict)
    posts: list[PublishResult] = field(default_factory=list)
    status: str = "failed"


def slugify(text: str, max_len: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:max_len].strip("-")
    return slug or "video"


def _save(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def publish_all(db: RunLog, run_id: int, platforms: list[str], video: Path, md: dict, aspect: str,
                seconds: float, artifacts: Path) -> list[PublishResult]:
    """Publish to each platform; one platform failing never stops the others."""
    posts: list[PublishResult] = []
    for platform in platforms:
        log.info("Publishing to %s ...", platform)
        try:
            pr = get_publisher(platform)(video, md, aspect, seconds)
        except Exception as exc:
            pr = PublishResult(platform, "failed", error=str(exc))
        posts.append(pr)
        db.post(run_id, platform, pr.status, pr.post_id, pr.url, pr.error, pr.detail or None)
        log.info("  %s: %s %s", platform, pr.status, pr.url or pr.error or "")
    _save(artifacts / "publish_report.json", [p.__dict__ for p in posts])
    return posts


def finish_status(db: RunLog, run_id: int, posts: list[PublishResult]) -> str:
    ok = [p for p in posts if p.status in {"published", "draft"}]
    status = "published" if len(ok) == len(posts) else ("partial" if ok else "failed")
    db.update_run(run_id, status=status, error=None if status == "published" else
                  "; ".join(f"{p.platform}: {p.error}" for p in posts if p.error))
    return status


def run(opts: RunOptions) -> RunResult:
    from lib.checkpoint import init_project
    from lib.llm_client import chat_meta

    if opts.aspect not in SIZES:
        raise ValueError("aspect must be 9:16 or 16:9")
    require_binaries("ffmpeg", "ffprobe", "npx")

    project_id = opts.project_id or f"{slugify(opts.topic)}-{time.strftime('%Y%m%d-%H%M%S')}"
    project_dir = init_project(project_id, title=opts.topic, pipeline_type="autopilot",
                               series="autopilot")
    db = RunLog()
    run_id = db.start_run(project_id, opts.topic, opts.template, opts.aspect, opts.seconds)
    result = RunResult(run_id=run_id, project_dir=project_dir)
    artifacts = project_dir / "artifacts"

    def llm_logger(stage: str):
        def _log(m: dict) -> None:
            db.llm_call(run_id, stage, m)
            note = f" after {m['fallbacks']} fallback(s)" if m.get("fallbacks") else ""
            log.info("[%s] LLM served by %s (%s)%s", stage, m.get("provider"), m.get("model"), note)
        return _log

    try:
        # 1. Script + scene plan
        log.info("Planning %s %s about %r (%ss)", opts.template, opts.aspect, opts.topic, int(opts.seconds))
        plan = planner.make_plan(opts.topic, opts.template, opts.seconds, opts.aspect, chat_meta,
                                 facts=opts.facts, on_llm_call=llm_logger("script"))
        _save(artifacts / "plan.json", plan.to_dict())
        log.info("Plan: %r, %d scenes, %d words", plan.title, len(plan.scenes), len(plan.narration_text.split()))

        # 2. Narration (Piper, per scene)
        audio_dir = project_dir / "assets" / "audio"
        narration_wav, scene_audio = narration.synthesize(plan.scenes, audio_dir, voice=opts.voice)
        total = scene_audio[-1].end + TAIL_SECONDS
        captions = [w for sa in scene_audio for w in sa.words]
        _save(artifacts / "captions.json", captions)
        log.info("Narration: %.1fs", total)

        # 3. Visuals
        sourcer = VisualSourcer(project_dir / "assets", "portrait" if opts.aspect == "9:16" else "landscape")
        max_shot = 4.5 if opts.aspect == "9:16" else 7.0
        shots: list[Shot] = []
        for i, (scene, sa) in enumerate(zip(plan.scenes, scene_audio)):
            nxt = scene_audio[i + 1].start if i + 1 < len(scene_audio) else total
            shots.extend(sourcer.shots_for_scene(scene, sa.start, nxt, max_shot, len(shots)))
        _save(artifacts / "shots.json", [s.__dict__ for s in shots])
        counts = {k: sum(1 for s in shots if s.kind == k) for k in ("video", "image", "text")}
        log.info("Visuals: %s", counts)
        if counts["text"] == len(shots):
            raise RuntimeError("no stock visuals found for any scene (is PEXELS_API_KEY set?)")

        # 4. Audio mix with -18 dB ducking
        music = audio_mix.pick_music(opts.music, ROOT / "music_library", seed=project_id)
        mixed = audio_mix.mix(narration_wav, [(sa.start, sa.end) for sa in scene_audio], total, music,
                              audio_dir / "final_mix.wav")
        log.info("Audio: %s", f"music {music.name}" if music else "narration only (no music_library/ tracks)")

        # 5. Render (Remotion via video_compose)
        props = render.build_props(shots, captions, mixed, opts.aspect, total)
        _save(artifacts / "render_props.json", props)
        video = render.render(props, project_dir / "renders" / "final.mp4", opts.aspect)
        result.video = video
        log.info("Rendered %s", video)

        # 6. QA gate
        report = qa.check(video, SIZES[opts.aspect])
        result.qa = report
        _save(artifacts / "qa_report.json", report)
        db.update_run(run_id, video_path=str(video), video_seconds=report.get("duration"))
        if not report["passed"]:
            raise RuntimeError("QA failed: " + "; ".join(report["issues"]))
        log.info("QA passed (%s, %ss, %.1f MB)", report.get("resolution"), report.get("duration"),
                 report["bytes"] / 1e6)

        # 7. Metadata
        md = meta_mod.generate(plan.title, plan.narration_text, opts.aspect, report.get("duration") or total,
                               chat_meta, on_llm_call=llm_logger("metadata"))
        md["credits"] = [s.credit for s in shots if s.credit]
        _save(artifacts / "publish_metadata.json", md)

        # 8. Publish
        if opts.dry_run or not opts.platforms:
            result.status = "rendered"
            db.update_run(run_id, status="rendered")
            return result
        result.posts = publish_all(db, run_id, opts.platforms, video, md, opts.aspect,
                                   report.get("duration") or total, artifacts)
        result.status = finish_status(db, run_id, result.posts)
        return result
    except Exception as exc:
        db.update_run(run_id, status="failed", error=str(exc)[:2000])
        raise
    finally:
        db.close()
