from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _memory_path(profile_name: str | None) -> Path:
    # Same per-profile-dir-vs-root split as profile_overrides.py's
    # profile_overrides.yaml, so a named account's memory lives next to its
    # other account-specific files rather than being shared across accounts.
    if profile_name:
        return ROOT / "profiles" / profile_name / "memory.json"
    return ROOT / "memory.json"


def load_facts(profile_name: str | None) -> list[dict]:
    path = _memory_path(profile_name)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def _save_facts(profile_name: str | None, facts: list[dict]) -> None:
    path = _memory_path(profile_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(facts, indent=2))


def add_fact(profile_name: str | None, text: str, tag: str) -> dict:
    facts = load_facts(profile_name)
    fact = {"id": uuid.uuid4().hex[:8], "text": text.strip(), "tag": tag.strip(), "added_at": time.time()}
    facts.append(fact)
    _save_facts(profile_name, facts)
    return fact


def delete_fact(profile_name: str | None, fact_id: str) -> bool:
    facts = load_facts(profile_name)
    remaining = [f for f in facts if f.get("id") != fact_id]
    if len(remaining) == len(facts):
        return False
    _save_facts(profile_name, remaining)
    return True


def suggest_tag(text: str, api_key: str) -> str:
    """A short (1-3 word) label for a free-text memory note, for the
    flowgraph bubble on the Profile tab -- e.g. "Open to relocating for the
    right role" -> "Open to relocation". Falls back to a plain truncation
    of the text itself if the LLM call fails for any reason, so adding a
    memory note never hard-fails on a flaky API call."""
    try:
        from .app_settings import resolve_model
        from .openrouter import get_client

        client = get_client(api_key)
        prompt = f"""Summarize this candidate's note as a short label, 1-3 words, title case,
like a tag on a chip/pill UI element (e.g. "Pay", "Open to relocation", "FinTech only").
Reply with ONLY the label, nothing else.

NOTE: {text}
"""
        resp = client.chat.completions.create(
            model=resolve_model(), max_tokens=12, messages=[{"role": "user", "content": prompt}]
        )
        label = resp.choices[0].message.content.strip().strip('."')
        return label[:40] if label else text[:24]
    except Exception:  # noqa: BLE001 -- a tag is cosmetic, never block saving the note over it
        return text[:24] + ("…" if len(text) > 24 else "")


def as_prompt_block(facts: list[dict]) -> str:
    """Formats stored facts as a labeled block for injection into Tailor's
    LLM prompts -- see Tailor.__init__'s memory_text param. Empty string
    (not even a header) when there's nothing stored, so prompts without any
    memory saved read exactly as they did before this feature existed."""
    if not facts:
        return ""
    return "\n".join(f"- {f['text']}" for f in facts if f.get("text"))
