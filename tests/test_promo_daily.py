"""Daily runner: schedule sanity and content rotation."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("promo_daily", ROOT / "scripts" / "promo_daily.py")
promo_daily = importlib.util.module_from_spec(spec)
spec.loader.exec_module(promo_daily)

from autopilot.promo import load_registry  # noqa: E402


def test_rotation_advances_and_wraps():
    rot = {"stories": {"story": ["a", "b"], "target": ["zh", "en", "ja"]}}
    seen = [promo_daily.next_params("stories", rot, {"stories": n}, {})[0] for n in range(4)]
    assert [s["story"] for s in seen] == ["a", "b", "a", "b"]
    assert [s["target"] for s in seen] == ["zh", "en", "ja", "zh"]


def test_job_can_pin_a_value():
    params, key = promo_daily.next_params("stories", {"stories": {"target": ["zh", "en"]}}, {}, {"target": "ja"})
    assert params["target"] == "ja" and key == "target=ja"


def test_schedule_is_valid_against_registry():
    sched = yaml.safe_load((ROOT / "autopilot" / "promo" / "schedule.yaml").read_text(encoding="utf-8"))
    reg = load_registry()
    assert sched["publish"] is False, "keep publishing off until reviews are approved"
    for day, jobs in sched["days"].items():
        assert day in promo_daily.DAYS
        for job in jobs:
            app = reg["apps"][job["app"]]
            loc = job.get("locale", "en")
            assert loc in reg["locales"]
            assert loc == "en" or loc in app.get("locales", {}), f"{job['app']} has no {loc} copy"
    pd = set(reg["apps"]["keys"].get("capture", {}).get("defaults", {}))
    assert "song" in pd
