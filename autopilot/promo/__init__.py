"""Kawaii app promos: Godot gameplay capture -> assembled 9:16 reel -> publish."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml

HERE = Path(__file__).resolve().parent
REGISTRY = HERE / "apps.yaml"
LOCALES_DIR = HERE / "locales"


def load_registry(path: Path = REGISTRY, locales_dir: Path = LOCALES_DIR) -> dict[str, Any]:
    """apps.yaml plus locales/<code>.yaml: registry['locales'][code] and per-app overrides
    in registry['apps'][app]['locales'][code]."""
    reg = yaml.safe_load(path.read_text(encoding="utf-8"))
    reg.setdefault("locales", {})
    for f in sorted(locales_dir.glob("*.yaml")) if locales_dir.is_dir() else []:
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        reg["locales"][f.stem] = data.get("locale", {})
        for app_id, over in (data.get("apps") or {}).items():
            if app_id in reg.get("apps", {}):
                reg["apps"][app_id].setdefault("locales", {})[f.stem] = over or {}
    return reg


def app_config(app_id: str, registry: dict[str, Any] | None = None, locale: Optional[str] = None) -> dict[str, Any]:
    reg = registry or load_registry()
    apps = reg.get("apps", {})
    if app_id not in apps:
        raise KeyError(f"unknown app {app_id!r}; known: {', '.join(apps)}")
    app = dict(apps[app_id])
    if app.get("status") != "ready":
        raise NotImplementedError(f"{app_id}: template {app.get('template')!r} is not built yet")
    app["id"] = app_id
    app["path"] = str(Path(reg["games_dir"]) / app["repo"])
    if locale and reg.get("locales"):
        from autopilot.promo.locale import resolve
        app = resolve(app, reg, locale)
    return app
