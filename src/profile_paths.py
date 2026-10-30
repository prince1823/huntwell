from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .cache import DEFAULT_CACHE_DIR

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config" / "search_config.yaml"


@dataclass
class ProfilePaths:
    profile_name: str | None
    state_dir: Path
    cache_dir: Path
    db_path: Path
    config_path: Path


def resolve_profile_paths(
    profile_name: str | None,
    config_arg: str | Path = DEFAULT_CONFIG_PATH,
    db_arg: str | Path | None = None,
) -> ProfilePaths:
    """Single source of truth for where a profile's state lives, shared by
    the CLI (main.py) and the webapp (webapp/runner.py) so they can never
    drift out of sync with each other.

    profile_name=None -> the original single-account layout (auth_state/,
    applications.db, .cache/ at the project root). A real name -> fully
    isolated per-account state under profiles/<name>/, same as the CLI's
    --profile flag, including an optional profile-specific .env (e.g. a
    different person's own ANTHROPIC_API_KEY) and search_config.yaml
    override.
    """
    config_arg = Path(config_arg)

    if profile_name:
        profile_dir = ROOT / "profiles" / profile_name
        profile_dir.mkdir(parents=True, exist_ok=True)
        state_dir = profile_dir / "auth_state"
        state_dir.mkdir(exist_ok=True)
        cache_dir = profile_dir / "cache"
        db_path = Path(db_arg) if db_arg else profile_dir / "applications.db"

        profile_env = profile_dir / ".env"
        if profile_env.exists():
            load_dotenv(profile_env, override=True)

        profile_config = profile_dir / "search_config.yaml"
        config_path = profile_config if (config_arg == DEFAULT_CONFIG_PATH and profile_config.exists()) else config_arg
    else:
        state_dir = ROOT / "auth_state"
        state_dir.mkdir(exist_ok=True)
        cache_dir = DEFAULT_CACHE_DIR
        db_path = Path(db_arg) if db_arg else ROOT / "applications.db"

        # Same override pattern as a named profile's search_config.yaml, but
        # at the project root since there's no profiles/<name>/ directory
        # for the root account -- lets the root account pin its own search
        # locations etc. without editing the shared default config.
        root_config = ROOT / "search_config.yaml"
        config_path = root_config if (config_arg == DEFAULT_CONFIG_PATH and root_config.exists()) else config_arg

    return ProfilePaths(
        profile_name=profile_name,
        state_dir=state_dir,
        cache_dir=cache_dir,
        db_path=db_path,
        config_path=config_path,
    )
