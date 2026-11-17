"""Stage 2: inbox manifest -> 9:16 promo reel (Remotion) -> QA -> metadata -> publish.

Templates are functions ``(ctx) -> Timeline``. Only ``story_cliffhanger`` exists so far;
the other PRD templates register here as their PromoBots are built.

story_cliffhanger (parent-directed):
  [hook card: cover art + big hook text + parent voiceover]
  [gameplay with the app's own narration, up to the choice screen]
  [end card: app name + store CTA + voiceover "Where will Bun go next?..."]
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from autopilot import metadata as meta_mod, narration, qa, render
from autopilot.db import RunLog
from autopilot.media_utils import run as run_cmd
from autopilot.pipeline import _save, finish_status, publish_all

log = logging.getLogger("autopilot.promo")

COPY_SYSTEM = (
    "You write short promo copy for a children's educational app. The audience is PARENTS, "
    "never children: address the parent ('your child', 'your kid'). Use ONLY the approved "
    "claims given; do not invent statistics, percentages, awards, prices, 'free', 'best', "
    "'#1', or outcomes like 'fluent'. Copy is read aloud by a text-to-speech voice: plain "
    "words, no emojis, no hashtags. Reply with JSON only."
)


@dataclass
class Timeline:
    cuts: list[dict[str, Any]]
    overlays: list[dict[str, Any]]
    voice: list[tuple[Path, float]]          # (wav, start seconds)
    game_audio: Optional[tuple[Path, float]]  # (clip, start seconds)
    captions: list[dict[str, Any]]
    total: float
    copy: dict[str, str] = field(default_factory=dict)


def _fmt(text: str, values: dict[str, str]) -> str:
    return text.format(**values) if "{" in text else text


def story_context(app: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    params = manifest.get("capture_params", {})
    story_id = params.get("story", "")
    data = json.loads((Path(app["path"]) / "data" / "stories" / f"{story_id}.json").read_text(encoding="utf-8"))
    who = next((l.get("who") for s in data["scenes"] for l in s.get("lines", [])
                if l.get("who") and l.get("who") != "narrator"), "")
    choice = next((s["choice"] for s in data["scenes"] if s.get("choice")), None)
    target = params.get("target", "zh")
    # Title / blurb in the audience language when the story has it (stories carry vi / en / ja / zh).
    lang_key = str(app.get("locale", "en")).split("_")[0]

    def pick(v: Any) -> str:
        text = " ".join(str(v.get(lang_key) or v.get("en", "")).split()) if isinstance(v, dict) else str(v or "")
        # zh / ja story text is word-split with spaces for the reader; join it for display.
        cjk = "\u3040-\u30ff\u3400-\u9fff"
        return re.sub(rf" +(?=[{cjk}])|(?<=[{cjk}]) +", "", text) if lang_key in ("zh", "ja") else text

    title = pick(data["title"])
    blurb = pick(data.get("blurb", ""))
    cover = Path(app["path"]) / "assets" / "ui" / f"{data['cover']}.jpg"
    language = app.get("language_names", {}).get(target, target)
    prompt = (choice or {}).get("prompt", {}).get("en", "") if choice else ""
    return {
        "story_id": story_id, "title": title, "blurb": blurb, "hero": who.title() if who else "our hero",
        "language": language, "choice_prompt": prompt,
        "cover": cover if cover.is_file() else None,
        "video_desc": f'the story "{title}" ({blurb}) being read aloud in {language}, '
                      f'stopping on a choice: "{prompt}"',
        "outro_ask": "tease the choice and name the app",
    }


def app_context(app: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    """Context for apps without story data: what the captured gameplay shows."""
    params = manifest.get("capture_params", {})
    item = params.get("song") or params.get("lesson") or params.get("type") or "demo"
    return {
        "story_id": str(item), "title": app["display_name"], "blurb": app.get("context", ""),
        "hero": app.get("hero", "Bun"), "language": "", "choice_prompt": "", "cover": None,
        "video_desc": app.get("context", "gameplay of the app"),
        "outro_ask": "invite parents to try it and name the app",
    }


def make_copy(app: dict[str, Any], ctx: dict[str, Any], chat_meta: Optional[Callable],
              on_llm_call: Optional[Callable[[dict], None]] = None) -> tuple[dict[str, str], str]:
    values = {"language": ctx["language"], "hero": ctx["hero"]}
    claims = [_fmt(c, values) for c in app.get("approved_claims", [])]
    fallback = {k: _fmt(v, values) for k, v in app.get("fallback_copy", {}).items()}
    if chat_meta is None:
        return fallback, "fallback (no LLM)"
    cfg = app.get("locale_cfg", {})
    lang_name = cfg.get("name", "English")
    user = f"""App: {app['display_name']} ({app.get('audience', 'parents')})
Approved claims (in {lang_name}):
{chr(10).join('- ' + c for c in claims)}
The video shows {ctx['video_desc']}.
Write all three lines in natural, warm {lang_name} as a native speaker would say them to a parent.
Keep the app name "{app['display_name']}" and character names exactly as written.

Return JSON:
{{"hook": "spoken opening line to parents, max 12 words",
  "hook_overlay": "on-screen hook text, max 6 words",
  "outro": "spoken closing line: {ctx['outro_ask']} ({app['display_name']}), max 16 words"}}"""
    try:
        data, meta = chat_meta([{"role": "system", "content": COPY_SYSTEM}, {"role": "user", "content": user}],
                               json_mode=True, max_tokens=400)
        if on_llm_call:
            on_llm_call(meta)
        out = {k: " ".join(str(data.get(k, "")).split()) for k in ("hook", "hook_overlay", "outro")}
        # Length: words, scaled for syllable languages (vi); characters for zh / ja.
        if cfg.get("cjk"):
            limits, size = {"hook": 30, "hook_overlay": 14, "outro": 40}, len
        else:
            k_scale = float(cfg.get("copy_scale", 1.0))
            limits = {k: int(n * k_scale) for k, n in {"hook": 14, "hook_overlay": 7, "outro": 18}.items()}
            size = lambda s: len(s.split())  # noqa: E731
        terms = [r"\d+\s*%", r"\bfree\b", r"\bbest\b", "#1", r"\bfluent"] + \
            [re.escape(t) for t in cfg.get("banned_terms", [])]
        banned = re.compile("|".join(terms), re.I)
        for k, n in limits.items():
            if not out[k] or size(out[k]) > n or banned.search(out[k]):
                raise ValueError(f"{k!r} rejected: {out[k]!r}")
        if app["display_name"].lower() not in out["outro"].lower():
            raise ValueError("outro does not name the app")
        return out, f"llm ({meta.get('provider')})"
    except Exception as exc:
        log.warning("LLM copy unusable (%s); using fallback copy", exc)
        return fallback, "fallback"


def _localized(path: Path, locale: Optional[str]) -> Path:
    """design/promo/end_card.png -> end_card_<locale>.png when that exists (text in the
    audience language); the plain file otherwise (English or text-free design)."""
    if locale:
        loc = path.with_name(f"{path.stem}_{locale}{path.suffix}")
        if loc.is_file():
            return loc
    return path


def _no_llm(*_a, **_k):
    raise RuntimeError("LLM disabled")


def _vo(text: str, out_dir: Path, voice: Optional[str], app: Optional[dict] = None) -> tuple[Path, list[dict], float]:
    """Voiceover in the app's locale voice (Piper, or ElevenLabs when allowed)."""
    if app and app.get("locale_cfg"):
        from autopilot.promo.locale import voiceover
        return voiceover(text, out_dir, app, allow_paid=bool(app.get("allow_paid_voice")), voice_override=voice)
    scene = type("VO", (), {"id": out_dir.name, "narration": text})()
    wav, parts = narration.synthesize([scene], out_dir, voice=voice)
    return wav, parts[0].words, parts[0].end


def _shift(words: list[dict], seconds: float) -> list[dict]:
    ms = int(round(seconds * 1000))
    return [dict(w, startMs=w["startMs"] + ms, endMs=w["endMs"] + ms) for w in words]


def _hook_frame(clip: str, out: Path, at: float = 1.5) -> Optional[str]:
    """A still from the gameplay clip for the hook card (apps without cover art)."""
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        run_cmd(["ffmpeg", "-y", "-v", "error", "-ss", f"{at}", "-i", clip, "-frames:v", "1", "-q:v", "2", str(out)])
        return str(out) if out.is_file() else None
    except Exception as exc:
        log.warning("hook frame extraction failed: %s", exc)
        return None


def gameplay_showcase(app, manifest, ctx, copy, audio_dir: Path, voice: Optional[str]) -> Timeline:
    """Phase-1 template for every app: hook card on a gameplay still -> gameplay -> end card.
    Template-specific effects (countdowns, speed ramps, split layouts) come with event-rich PromoBots."""
    from autopilot.promo import templates
    from autopilot.promo.effects import clip_duration

    prepared = templates.prepare(app, manifest, audio_dir.parent / "video" / "fx", copy)
    if str(prepared) != manifest["clip_path"]:
        log.info("%s effects applied -> %s", manifest["template"], prepared.name)
        manifest = {**manifest, "clip_path": str(prepared), "duration_sec": clip_duration(prepared)}
    hook_img = app.get("hook_card")
    hook_path = Path(app.get("path", ".")) / hook_img if hook_img else None
    if hook_path and hook_path.is_file():
        cover = str(hook_path)
    else:
        cover = _hook_frame(manifest["clip_path"], audio_dir.parent / "images" / "hook_frame.jpg",
                            at=float(app.get("hook_frame_at", 1.5)))
    return story_cliffhanger(app, manifest, {**ctx, "cover": cover}, copy, audio_dir, voice)


def story_cliffhanger(app, manifest, ctx, copy, audio_dir: Path, voice: Optional[str]) -> Timeline:
    hook_wav, hook_words, hook_d = _vo(copy["hook"], audio_dir / "vo_hook", voice, app)
    out_wav, out_words, out_d = _vo(copy["outro"], audio_dir / "vo_outro", voice, app)
    lead = 0.3
    intro = max(2.8, lead + hook_d + 0.5)
    clip_d = float(manifest["duration_sec"])
    outro_start = intro + clip_d
    total = outro_start + max(3.5, 0.4 + out_d + 1.2)
    choice_t = next((e["t"] for e in manifest["events"] if e["type"] == "choice"), None)
    stores = " & ".join(app.get("stores", [])) or "your app store"
    cta = app.get("cta") or f"On the {stores}"
    cover = str(ctx["cover"]) if ctx["cover"] else None
    end_card = _localized(Path(app.get("path", ".")) / app["end_card"], app.get("locale")) if app.get("end_card") else None
    end_cut: dict[str, Any] = (
        {"id": "endcard", "source": str(end_card), "animation": "zoom-out"}
        if end_card and end_card.is_file() else
        {"id": "endcard", "type": "hero_title", "source": "", "text": app["display_name"], "heroSubtitle": cta,
         **({"backgroundImage": cover, "backgroundOverlay": 0.1} if cover else {})})

    cuts = [
        ({"id": "hook", "source": cover, "in_seconds": 0, "out_seconds": round(intro, 3), "animation": "zoom-in"}
         if cover else {"id": "hook", "type": "text_card", "source": "", "text": copy["hook_overlay"],
                        "in_seconds": 0, "out_seconds": round(intro, 3)}),
        {"id": "gameplay", "source": manifest["clip_path"], "in_seconds": round(intro, 3),
         "out_seconds": round(outro_start, 3), "transition_in": "fade", "transition_out": "cut",
         "transition_duration": 0.25},
        {**end_cut, "in_seconds": round(outro_start, 3), "out_seconds": round(total, 3)},
    ]
    # No overlays: the stock hero/section titles are sized for 16:9 and wash out the art in 9:16.
    # The hook voiceover's captions carry the hook text, and the app's own choice screen
    # already asks the cliffhanger question.
    overlays: list[dict[str, Any]] = []
    captions = _shift(hook_words, lead) + _shift(out_words, outro_start + 0.4)
    return Timeline(cuts, overlays, [(hook_wav, lead), (out_wav, outro_start + 0.4)],
                    (Path(manifest["clip_path"]), intro), captions, total, copy)


# Phase 1: every PRD template renders as gameplay_showcase until its PromoBot emits the
# events its effects need (countdown, speed ramp, split layout, timelapse).
TEMPLATES = {
    "story_cliffhanger": story_cliffhanger,
    "gameplay_showcase": gameplay_showcase,
    "timed_challenge": gameplay_showcase,
    "logic_execution": gameplay_showcase,
    "brain_teaser": gameplay_showcase,
    "experiment_wow": gameplay_showcase,
    "timelapse_reveal": gameplay_showcase,
    "musical_showcase": gameplay_showcase,
}


def mix_audio(tl: Timeline, out_path: Path) -> Path:
    inputs: list[str] = []
    chains: list[str] = []
    sources = [(p, s) for p, s in tl.voice] + ([tl.game_audio] if tl.game_audio else [])
    for i, (path, start) in enumerate(sources):
        inputs += ["-i", str(path)]
        ms = int(round(start * 1000))
        chains.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,adelay={ms}|{ms}[a{i}]")
    labels = "".join(f"[a{i}]" for i in range(len(sources)))
    graph = (";".join(chains) + f";{labels}amix=inputs={len(sources)}:normalize=0:duration=longest,"
             f"apad,atrim=0:{tl.total:.3f},loudnorm=I=-14:TP=-1.5:LRA=11[out]")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run_cmd(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[out]",
             "-ar", "48000", "-c:a", "pcm_s16le", "-t", f"{tl.total:.3f}", str(out_path)])
    return out_path


def assemble(manifest_path: Path, platforms: list[str], dry_run: bool = False, voice: Optional[str] = None,
             use_llm: bool = True, locale: Optional[str] = None, allow_paid_voice: bool = False) -> dict[str, Any]:
    from autopilot.promo import app_config, load_registry
    from lib.checkpoint import init_project

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    registry = load_registry()
    locale = locale or manifest.get("locale") or "en"
    app = app_config(manifest["app_id"], registry, locale)
    app["allow_paid_voice"] = allow_paid_voice
    if platforms and not dry_run and app.get("review") != "approved":
        raise PermissionError(
            f"{app['id']} '{locale}' copy is '{app.get('review')}', not approved: review "
            f"autopilot/promo/locales/{locale}.yaml before publishing (renders work with --dry-run)")
    template = TEMPLATES.get(manifest["template"])
    if template is None:
        raise NotImplementedError(f"template {manifest['template']!r} is not built yet")
    chat_meta = None
    if use_llm:
        from lib.llm_client import chat_meta

    ctx = story_context(app, manifest) if manifest["template"] == "story_cliffhanger" else app_context(app, manifest)
    target = manifest.get("capture_params", {}).get("target")
    tag = "-".join(p for p in (ctx["story_id"] or "demo", target if manifest["template"] == "story_cliffhanger" else "", locale) if p)
    project_id = f"promo-{app['id']}-{tag}-{time.strftime('%Y%m%d-%H%M%S')}".replace("_", "-")
    pdir = init_project(project_id, title=f"{app['display_name']} promo", pipeline_type="promo", series="kawaii-promos")
    artifacts = pdir / "artifacts"
    db = RunLog()
    run_id = db.start_run(project_id, f"{app['display_name']}: {ctx['title']}", manifest["template"], "9:16",
                          manifest["duration_sec"])
    out: dict[str, Any] = {"run_id": run_id, "project_dir": str(pdir), "status": "failed"}
    try:
        copy, source = make_copy(app, ctx, chat_meta, on_llm_call=lambda m: db.llm_call(run_id, "promo_copy", m))
        log.info("Copy (%s): %s", source, copy)
        tl = template(app, manifest, ctx, copy, pdir / "assets" / "audio", voice)
        from autopilot.promo.locale import caption_style
        cap_style, cap_props = caption_style(app)
        mixed = mix_audio(tl, pdir / "assets" / "audio" / "final_mix.wav")
        props = {
            "renderer_family": "explainer-data", "themeConfig": app.get("theme", {}), "tailPaddingSeconds": 0,
            "cuts": tl.cuts, "overlays": tl.overlays, "captions": tl.captions,
            "captionStyle": cap_style,
            **({"captionProps": cap_props} if cap_props else {}),
            "audio": {"narration": {"src": str(mixed), "volume": 1.0}},
        }
        _save(artifacts / "promo_plan.json", {"manifest": manifest, "copy": copy, "copy_source": source,
                                             "context": {k: str(v) for k, v in ctx.items()}})
        _save(artifacts / "render_props.json", props)
        video = render.render(props, pdir / "renders" / "final.mp4", "9:16")
        report = qa.check(video, tuple(registry.get("size", [1080, 1920])))
        _save(artifacts / "qa_report.json", report)
        db.update_run(run_id, video_path=str(video), video_seconds=report.get("duration"))
        out.update(video=str(video), qa=report)
        if not report["passed"]:
            raise RuntimeError("QA failed: " + "; ".join(report["issues"]))

        script = f"{copy['hook']} {app['display_name']}: {ctx['title']}. {ctx['blurb']} {copy['outro']}"
        md = meta_mod.generate(f"{ctx['title']} | {app['display_name']}", script, "9:16", report["duration"],
                               chat_meta or _no_llm, on_llm_call=lambda m: db.llm_call(run_id, "metadata", m),
                               language=app.get("locale_cfg", {}).get("name", "English"))
        md["locale"], md["review"] = locale, app.get("review")
        md["youtube"]["made_for_kids"] = bool(app.get("made_for_kids", False))
        _save(artifacts / "publish_metadata.json", md)

        if dry_run or not platforms:
            out["status"] = "rendered"
            db.update_run(run_id, status="rendered")
            return out
        posts = publish_all(db, run_id, platforms, video, md, "9:16", report["duration"], artifacts)
        out["posts"] = [p.__dict__ for p in posts]
        out["status"] = finish_status(db, run_id, posts)
        return out
    except Exception as exc:
        db.update_run(run_id, status="failed", error=str(exc)[:2000])
        raise
    finally:
        db.close()
