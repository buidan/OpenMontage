#!/usr/bin/env python3
"""Kawaii app promos (docs/kawaii_ai_video_pipeline_prd.md).

  python run_promo.py capture  --app stories [--locale vi] [--story ch01_morning_walk] [--target zh]
  python run_promo.py assemble --manifest projects/promo_inbox/<clip>.json [--platforms youtube,meta,tiktok] [--dry-run]
  python run_promo.py run      --app stories --locale vi --target ja [--platforms ...] [--dry-run]   # capture + assemble
  python run_promo.py batch    --locale vi [--apps stories,math] [--dry-run]   # every app; Stories x every target
  python run_promo.py apps                                                                   # registry + locale status

Languages: --locale is the parent's language (voiceover, captions, stickers, metadata) and
sets the app UI language for the capture; --target is what the child learns (Stories).
Copy per locale: autopilot/promo/locales/<locale>.yaml. Drafts render, but only
`review: approved` locales publish. zh / ja voiceovers use ElevenLabs (paid): pass
--allow-paid-voice to spend credits.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import tools.base_tool  # noqa: E402,F401  (loads .env)

INBOX = ROOT / "projects" / "promo_inbox"


def _run_one(app_id: str, locale: str, params: dict, args) -> dict:
    from autopilot.promo import app_config, load_registry
    from autopilot.promo.assemble import assemble
    from autopilot.promo.capture import capture
    from autopilot.publishers import parse_platforms

    registry = load_registry()
    app = app_config(app_id, registry, locale)
    manifest = capture(app, registry, INBOX, params)
    logging.info("Manifest: %s", manifest)
    return assemble(Path(manifest), parse_platforms(args.platforms), dry_run=args.dry_run, voice=args.voice,
                    use_llm=not args.no_llm, locale=locale, allow_paid_voice=args.allow_paid_voice)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("capture", "run", "batch"):
        p = sub.add_parser(name)
        if name != "batch":
            p.add_argument("--app", required=True)
        else:
            p.add_argument("--apps", help="comma list (default: every ready app)")
        p.add_argument("--locale", default="en", help="audience language: en | vi | zh | ja")
        p.add_argument("--story")
        p.add_argument("--target", help="learning language for Stories (zh|en|ja)")
        p.add_argument("--ui", help="override the app interface language (default: the locale's)")
        p.add_argument("--hold", type=float, help="seconds to hold on the cliffhanger screen")
        p.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                       help="override a capture default from apps.yaml (e.g. --param song=twinkle)")
    asm = sub.add_parser("assemble")
    asm.add_argument("--manifest", required=True, type=Path)
    asm.add_argument("--locale", help="default: the manifest's locale")
    for p in (sub.choices["run"], sub.choices["batch"], asm):
        p.add_argument("--platforms", default="")
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--voice", help="override the voiceover voice (Piper voice id / ElevenLabs voice id)")
        p.add_argument("--no-llm", action="store_true", help="use the locale's fallback copy")
        p.add_argument("--allow-paid-voice", action="store_true", help="allow ElevenLabs voiceovers (zh / ja)")
    sub.add_parser("apps")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    from autopilot.promo import app_config, load_registry

    if args.cmd == "apps":
        reg = load_registry()
        locales = sorted(reg.get("locales", {}))
        print(f"{'app':<8} {'status':<6} {'template':<18} " + " ".join(f"{l:<9}" for l in locales))
        for app_id, a in reg["apps"].items():
            cells = []
            for l in locales:
                over = a.get("locales", {}).get(l)
                cells.append("approved" if l == "en" else (over.get("review", "draft") if over else "-"))
            print(f"{app_id:<8} {a.get('status', '?'):<6} {a['template']:<18} " + " ".join(f"{c:<9}" for c in cells))
        return 0

    if args.cmd == "assemble":
        from autopilot.promo.assemble import assemble
        from autopilot.publishers import parse_platforms
        try:
            result = assemble(args.manifest, parse_platforms(args.platforms), dry_run=args.dry_run, voice=args.voice,
                              use_llm=not args.no_llm, locale=args.locale, allow_paid_voice=args.allow_paid_voice)
        except Exception as exc:
            logging.error("Promo failed: %s", exc)
            return 1
        print(json.dumps({k: v for k, v in result.items() if k != "qa"}, indent=2, default=str))
        return 0 if result["status"] in {"published", "rendered"} else 2

    params = {"story": args.story, "target": args.target, "ui": args.ui, "hold": args.hold}
    params.update(dict(p.split("=", 1) for p in args.param))

    if args.cmd == "capture":
        from autopilot.promo.capture import capture
        try:
            registry = load_registry()
            print(f"Manifest: {capture(app_config(args.app, registry, args.locale), registry, INBOX, params)}")
        except Exception as exc:
            logging.error("Capture failed: %s", exc)
            return 1
        return 0

    if args.cmd == "run":
        try:
            result = _run_one(args.app, args.locale, params, args)
        except Exception as exc:
            logging.error("Promo failed: %s", exc)
            return 1
        print(json.dumps({k: v for k, v in result.items() if k != "qa"}, indent=2, default=str))
        return 0 if result["status"] in {"published", "rendered"} else 2

    # batch: every ready app for one locale; Stories once per learning language
    reg = load_registry()
    wanted = [a.strip() for a in args.apps.split(",")] if args.apps else \
        [a for a, cfg in reg["apps"].items() if cfg.get("status") == "ready"]
    jobs = []
    for app_id in wanted:
        targets = reg["apps"][app_id].get("targets") if app_id == "stories" and not args.target else None
        for t in targets or [args.target]:
            jobs.append((app_id, {**params, "target": t} if t else params))
    summary, failed = [], 0
    for app_id, p in jobs:
        label = f"{app_id}{'/' + p['target'] if p.get('target') else ''} [{args.locale}]"
        try:
            result = _run_one(app_id, args.locale, p, args)
            summary.append(f"{label:<22} {result['status']:<9} {result.get('video', '')}")
        except Exception as exc:
            failed += 1
            summary.append(f"{label:<22} FAILED    {exc}")
            logging.error("%s failed: %s", label, exc)
    print("\n".join(summary))
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
