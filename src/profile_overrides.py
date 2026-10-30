from __future__ import annotations

from pathlib import Path

import yaml

from .models import Profile

ROOT = Path(__file__).resolve().parent.parent


def apply_profile_overrides(profile_name: str | None, profile: Profile) -> Profile:
    """Fill in resume fields a resume PDF doesn't state but the account owner has
    given us directly (e.g. a LinkedIn URL, a preferred work location) from
    profiles/<name>/profile_overrides.yaml, instead of guessing or leaving
    application forms blocked. Only overrides fields the file explicitly sets;
    everything else stays exactly as the resume parsed.

    profile_name=None (the root/default account) reads profile_overrides.yaml
    at the project root instead -- same mechanism, same file format, just not
    nested under profiles/ since there's no named-profile directory for it."""
    overrides_path = (
        ROOT / "profile_overrides.yaml" if not profile_name
        else ROOT / "profiles" / profile_name / "profile_overrides.yaml"
    )
    if not overrides_path.exists():
        return profile

    with open(overrides_path) as f:
        overrides = yaml.safe_load(f) or {}

    for field in ("linkedin_url", "location", "portfolio_url", "phone", "email"):
        value = overrides.get(field)
        if value:
            setattr(profile, field, value)

    return profile
