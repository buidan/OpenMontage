#!/usr/bin/env python3
"""Daily Kawaii promo run (launchd calls this every night; see docs/promo_pipeline.md).

  python scripts/promo_daily.py                 # today's jobs from autopilot/promo/schedule.yaml
  python scripts/promo_daily.py --day sat       # run another weekday's jobs now
  python scripts/promo_daily.py --plan          # print what would run (no capture / render)
  python scripts/promo_daily.py --dry-run       # never publish, even if schedule says publish

For each job: next content item from the app's rotation -> capture -> render -> QA ->
publish (only when schedule `publish: true` and the locale copy is `review: approved`).
A run summary goes to projects/promo_daily/<date>.log and a macOS notification.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import tools.base_tool  # noqa: E402,F401  (loads .env)
import yaml  # noqa: E402

SCHEDULE = ROOT / "autopilot" / "promo" / "schedule.yaml"
STATE = ROOT / "projects" / "promo_state.json"
LOG_DIR = ROOT / "projects" / "promo_daily"
INBOX = ROOT / "projects" / "promo_inbox"
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

log = logging.getLogger("promo_daily")


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE)


def next_params(app_id: str, rotation: dict, state: dict, job: dict) -> tuple[dict, str]:
    """Pick this run's content: item n of every rotating list (n = runs so far for the app)."""
    n = int(state.get(app_id, 0))
    params: dict = {}
    for key, items in (rotation.get(app_id) or {}).items():
        if items:
            params[key] = items[n % len(items)]
    for key in ("target", "story", "song", "lesson", "type"):  # a job may pin a value
        if job.get(key):
            params[key] = job[key]
    key = "|".join(f"{k}={v}" for k, v in sorted(params.items()))
    return params, key


def notify(title: str, text: str) -> None:
    if sys.platform == "darwin":
        script = f'display notification {json.dumps(text[:200])} with title {json.dumps(title)}'
        subprocess.run(["osascript", "-e", script], capture_output=True)


def router_ok() -> bool:
    import httpx
    base = os.environ.get("LLM_ROUTER_BASE_URL", "http://127.0.0.1:4000/v1").rsplit("/v1", 1)[0]
    try:
        return httpx.get(f"{base}/health", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--day", choices=DAYS, help="run this weekday's jobs (default: today)")
    ap.add_argument("--plan", action="store_true", help="only print the plan")
    ap.add_argument("--dry-run", action="store_true", help="render only, never publish")
    args = ap.parse_args(argv)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    today = dt.date.today()
    log_file = LOG_DIR / f"{today.isoformat()}.log"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S",
                        handlers=[logging.StreamHandler(), logging.FileHandler(log_file, encoding="utf-8")])
    logging.getLogger("httpx").setLevel(logging.WARNING)

    sched = yaml.safe_load(SCHEDULE.read_text(encoding="utf-8"))
    day = args.day or DAYS[today.weekday()]
    jobs = sched.get("days", {}).get(day) or []
    publish = bool(sched.get("publish")) and not args.dry_run
    state = load_state()

    plan = []
    preview = dict(state)
    for job in jobs:
        params, key = next_params(job["app"], sched.get("rotation", {}), preview, job)
        preview[job["app"]] = int(preview.get(job["app"], 0)) + 1
        plan.append((job, params, key))
    log.info("%s (%s): %d job(s), publish=%s", today, day, len(plan), publish)
    for job, params, key in plan:
        log.info("  %-8s %-3s %s", job["app"], job.get("locale", "en"), key or "(defaults)")
    if args.plan or not plan:
        return 0

    # One run at a time: a slow render must never overlap the next night's run.
    lock = open(LOG_DIR / ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.error("another promo run is still going; skipping")
        return 1

    use_llm = bool(sched.get("use_llm"))
    if use_llm and not router_ok():
        log.warning("LLM router not reachable; using the reviewed fallback copy")
        use_llm = False

    from autopilot.promo import app_config, load_registry
    from autopilot.promo.assemble import assemble
    from autopilot.promo.capture import capture
    from autopilot.publishers import parse_platforms

    platforms = parse_platforms(",".join(sched.get("platforms", []))) if publish else []
    lines, failures = [], 0
    for job, params, key in plan:
        app_id, locale = job["app"], job.get("locale", "en")
        started = time.time()
        label = f"{app_id}[{locale}] {key}"
        try:
            registry = load_registry()
            app = app_config(app_id, registry, locale)
            if publish and app.get("review") != "approved":
                log.warning("%s: '%s' copy is %s; rendering without publishing", app_id, locale, app.get("review"))
            job_platforms = platforms if app.get("review") == "approved" else []
            manifest = capture(app, registry, INBOX, params)
            result = assemble(manifest, job_platforms, dry_run=not job_platforms, use_llm=use_llm, locale=locale,
                              allow_paid_voice=bool(sched.get("allow_paid_voice")))
            state[app_id] = int(state.get(app_id, 0)) + 1     # advance only after a good render
            save_state(state)
            posts = ", ".join(f"{p['platform']}:{p['status']}" for p in result.get("posts", [])) or "not published"
            lines.append(f"OK    {label}  {int(time.time() - started)}s  {posts}\n      {result.get('video')}")
            if result.get("status") in {"partial", "failed"}:
                failures += 1
        except Exception as exc:
            failures += 1
            lines.append(f"FAIL  {label}  {exc}")
            log.exception("%s failed", label)
    summary = "\n".join(lines)
    log.info("Summary:\n%s", summary)
    notify("Kawaii promos", f"{len(plan) - failures}/{len(plan)} ok" + (" - see projects/promo_daily" if failures else ""))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
