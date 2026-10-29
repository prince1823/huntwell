from __future__ import annotations

import os
from pathlib import Path

import click
import yaml
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from src.llm import Tailor
from src.models import Profile
from src.platforms.ashby import AshbyAdapter
from src.platforms.base import PlatformAdapter
from src.platforms.indeed import IndeedAdapter
from src.platforms.linkedin import LinkedInAdapter
from src.platforms.wellfound import WellfoundAdapter
from src.resume_parser import parse_resume
from src.tracker import Tracker

load_dotenv()

ADAPTERS = {
    "linkedin": LinkedInAdapter,
    "indeed": IndeedAdapter,
    "wellfound": WellfoundAdapter,
    "ashby": AshbyAdapter,
}


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def run_platform(
    platform: str,
    config: dict,
    profile: Profile,
    tailor: Tailor,
    tracker: Tracker,
    headless: bool,
    min_fit_score: float,
    state_dir: Path,
) -> None:
    adapter_cls = ADAPTERS[platform]
    state_file = state_dir / f"{platform}.json"
    daily_cap = config.get("max_applications_per_day", {}).get(
        platform, config.get("max_applications_per_platform_per_day", 20)
    )

    already_today = tracker.count_applied_today(platform)
    if already_today >= daily_cap:
        click.echo(f"[{platform}] daily cap ({daily_cap}) already reached, skipping")
        return

    if headless and not state_file.exists() and adapter_cls.requires_login:
        # Headless means no human is watching this run -- if there's no
        # saved session yet, ensure_logged_in would open an invisible
        # browser and wait up to 3 minutes for someone to complete a login
        # nobody can see, then time out. That's exactly the failure mode a
        # batch/parallel run across many accounts would hit. Fail fast with
        # a clear instruction instead.
        click.echo(
            f"[{platform}] no saved session at {state_file} -- this account needs a "
            f"one-time manual login first. Run with --no-headless once to log in, "
            f"then headless runs will work."
        )
        return

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            storage_state=str(state_file) if state_file.exists() else None
        )
        adapter: PlatformAdapter = adapter_cls(context, tailor, config)

        email = os.environ.get(f"{platform.upper()}_EMAIL", "")
        password = os.environ.get(f"{platform.upper()}_PASSWORD", "")
        try:
            adapter.ensure_logged_in(email, password)
        except RuntimeError as e:
            click.echo(f"[{platform}] LOGIN FAILED: {e}")
            browser.close()
            return
        context.storage_state(path=str(state_file))

        click.echo(f"[{platform}] searching for jobs...")
        postings = adapter.search_jobs(profile)
        click.echo(f"[{platform}] found {len(postings)} candidate postings")

        applied_count = already_today
        for job in postings:
            if applied_count >= daily_cap:
                click.echo(f"[{platform}] hit daily cap of {daily_cap}, stopping")
                break
            if tracker.already_handled(job):
                continue
            if any(kw.lower() in job.title.lower() for kw in config.get("keywords_exclude", [])):
                continue

            fit = tailor.score_fit(profile, job)
            if fit < min_fit_score:
                click.echo(f"[{platform}] SKIP (fit {fit:.2f}) {job.title} @ {job.company}")
                continue

            click.echo(f"[{platform}] applying (fit {fit:.2f}): {job.title} @ {job.company}")
            try:
                result = adapter.apply(job, profile)
            except Exception as e:  # noqa: BLE001 — one bad posting shouldn't kill the run
                from src.models import ApplicationResult

                result = ApplicationResult(job=job, status="failed", reason=str(e))

            tracker.record(result)
            click.echo(f"[{platform}]   -> {result.status} {result.reason}")
            if result.status == "applied":
                applied_count += 1

        context.storage_state(path=str(state_file))
        browser.close()


@click.command()
@click.option("--resume", required=True, type=click.Path(exists=True), help="Path to resume (.pdf/.docx/.txt)")
@click.option("--config", default="config/search_config.yaml", help="Path to search config YAML")
@click.option("--db", default=None, help="Path to the SQLite application tracker (default: applications.db, or profiles/<name>/applications.db with --profile)")
@click.option("--headless/--no-headless", default=False, help="Run browsers headless (first run per site should be --no-headless so you can clear login challenges)")
@click.option("--min-fit-score", default=0.6, help="Skip postings scored below this fit (0-1)")
@click.option("--platforms", default="", help="Comma-separated subset of platforms to run (default: all enabled in config)")
@click.option(
    "--profile",
    default=None,
    help=(
        "Account/profile name, for running multiple real accounts side by side "
        "(each with their own auth_state/cache/tracker under profiles/<name>/). "
        "Each profile still needs its own one-time --no-headless login per "
        "platform before headless/batch runs will work for it. Omit for the "
        "original single-account layout (auth_state/, applications.db, .cache/)."
    ),
)
def main(
    resume: str,
    config: str,
    db: str | None,
    headless: bool,
    min_fit_score: float,
    platforms: str,
    profile: str | None,
) -> None:
    from src.profile_paths import resolve_profile_paths

    paths = resolve_profile_paths(profile, config_arg=config, db_arg=db)
    state_dir = paths.state_dir

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise click.ClickException("Set ANTHROPIC_API_KEY in your .env file")

    cfg = load_config(str(paths.config_path))

    label = f"[{profile}] " if profile else ""
    click.echo(f"{label}Parsing resume...")
    profile_obj = parse_resume(resume, api_key, cache_dir=paths.cache_dir)
    click.echo(f"{label}Parsed profile for {profile_obj.full_name} ({profile_obj.headline})")

    tailor = Tailor(api_key, cache_dir=paths.cache_dir)
    tracker = Tracker(str(paths.db_path))

    # Search titles come from THIS resume by default, not one static list in
    # the config -- set `titles_from_resume: false` in search_config.yaml
    # (or profiles/<name>/search_config.yaml) to pin a fixed list instead.
    if cfg.get("titles_from_resume", True):
        suggested_titles = tailor.suggest_search_titles(profile_obj)
        if suggested_titles:
            click.echo(f"{label}Searching titles: {', '.join(suggested_titles)}")
            cfg["titles"] = suggested_titles

    requested = [p.strip() for p in platforms.split(",") if p.strip()] or [
        name for name, enabled in cfg.get("platforms", {}).items() if enabled
    ]

    for platform in requested:
        if platform not in ADAPTERS:
            click.echo(f"Unknown platform '{platform}', skipping")
            continue
        run_platform(platform, cfg, profile_obj, tailor, tracker, headless, min_fit_score, state_dir)

    tracker.close()


if __name__ == "__main__":
    main()
