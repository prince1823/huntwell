from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = ROOT / "app_settings.json"

DEFAULT_MODEL = "gpt-4o-mini"
# Practical OpenAI-compatible choices -- src/openrouter.py's get_client() wraps
# the OpenAI client directly, so any model name the configured key's account
# can access works; this list is just a sane, curated menu for the Settings
# tab rather than a free-text field most people have no reason to hand-type.
AVAILABLE_MODELS = ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1", "gpt-5-mini", "gpt-5"]
# Ashby/Workable/Rippling are deliberately left out -- see webapp/runner.py's
# RUN_PLATFORMS comment. Only platforms a web-app user can actually toggle
# belong here; the other three stay CLI-only (--platforms ashby, etc.) until
# their CAPTCHA reliability is solved.
ALL_PLATFORMS = ["linkedin", "wellfound"]


def load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        data = json.loads(SETTINGS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(patch: dict) -> dict:
    current = load_settings()
    if "api_key" in patch:
        current["api_key"] = (patch["api_key"] or "").strip()
    if "model" in patch and patch["model"]:
        current["model"] = patch["model"]
    if "platforms" in patch and isinstance(patch["platforms"], dict):
        # Always written as a full, explicit map over every known platform
        # (missing key = off) -- a partial map here would be ambiguous
        # about whether an omitted platform means "off" or "leave alone".
        current["platforms"] = {p: bool(patch["platforms"].get(p, False)) for p in ALL_PLATFORMS}
    if patch.get("max_applications_per_platform_per_day"):
        current["max_applications_per_platform_per_day"] = int(patch["max_applications_per_platform_per_day"])
    SETTINGS_PATH.write_text(json.dumps(current, indent=2))
    return current


def resolve_api_key() -> str | None:
    """A key saved via the Settings tab (BYOK) always wins over the .env
    value, so switching to your own key doesn't require touching .env or
    restarting the server."""
    key = (load_settings().get("api_key") or "").strip()
    return key or os.environ.get("ANTHROPIC_API_KEY")


def resolve_model() -> str:
    return load_settings().get("model") or DEFAULT_MODEL


def api_key_status() -> dict:
    settings = load_settings()
    own_key = (settings.get("api_key") or "").strip()
    if own_key:
        tail = own_key[-4:] if len(own_key) > 4 else own_key
        return {"source": "saved", "preview": f"…{tail}"}
    if os.environ.get("ANTHROPIC_API_KEY"):
        return {"source": "env", "preview": None}
    return {"source": "none", "preview": None}


def platform_overrides() -> dict | None:
    """None means "no override saved yet -- use search_config.yaml's own
    platforms block as-is", so a Settings tab nobody has touched changes
    nothing about existing behavior."""
    return load_settings().get("platforms")


def max_applications_override() -> int | None:
    return load_settings().get("max_applications_per_platform_per_day")
