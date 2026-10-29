from __future__ import annotations

"""Run the auto-apply pipeline for multiple accounts (profiles) concurrently.

Each profile is a real person's own account(s) -- this does NOT create or
share any login. A profile only runs headless (unattended) for whichever
platforms it already has a saved session for (profiles/<name>/auth_state/
<platform>.json); see README.md for the one-time manual login step each
profile's owner must do first.

Usage:
    python run_profiles.py --concurrency 5
    python run_profiles.py --profiles alice,bob,carol

Directory convention, per profile:
    profiles/<name>/resume.(pdf|docx|txt)      required
    profiles/<name>/search_config.yaml         optional (else shared config)
    profiles/<name>/.env                       optional (e.g. a different
                                                 ANTHROPIC_API_KEY per person)
    profiles/<name>/auth_state/<platform>.json created by that profile's
                                                 one-time --no-headless login
"""

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click

PROFILES_DIR = Path("profiles")
RESUME_EXTENSIONS = (".pdf", ".docx", ".txt")


def _find_resume(profile_dir: Path) -> Path | None:
    for ext in RESUME_EXTENSIONS:
        candidate = profile_dir / f"resume{ext}"
        if candidate.exists():
            return candidate
    return None


def _discover_profiles(selected: list[str] | None) -> list[str]:
    if not PROFILES_DIR.exists():
        return []
    names = sorted(p.name for p in PROFILES_DIR.iterdir() if p.is_dir())
    if selected:
        missing = [n for n in selected if n not in names]
        if missing:
            raise click.ClickException(f"No such profile directory: {', '.join(missing)}")
        return selected
    return names


def _run_one(name: str, min_fit_score: float, extra_args: list[str]) -> tuple[str, int, Path]:
    profile_dir = PROFILES_DIR / name
    resume = _find_resume(profile_dir)
    log_path = profile_dir / "run.log"

    if resume is None:
        log_path.write_text(
            f"SKIPPED: no resume.pdf/.docx/.txt found in {profile_dir}/\n"
        )
        return name, -1, log_path

    auth_dir = profile_dir / "auth_state"
    has_any_session = auth_dir.exists() and any(auth_dir.glob("*.json"))
    if not has_any_session:
        log_path.write_text(
            f"SKIPPED: no saved session in {auth_dir}/ -- this profile needs a "
            f"one-time manual login first:\n\n"
            f"  python main.py --profile {name} --resume {resume} --no-headless\n"
        )
        return name, -1, log_path

    cmd = [
        sys.executable,
        "main.py",
        "--profile",
        name,
        "--resume",
        str(resume),
        "--headless",
        "--min-fit-score",
        str(min_fit_score),
        *extra_args,
    ]
    with open(log_path, "w") as log_file:
        proc = subprocess.run(cmd, stdout=log_file, stderr=subprocess.STDOUT)
    return name, proc.returncode, log_path


@click.command()
@click.option("--profiles", default="", help="Comma-separated profile names to run (default: every profiles/<name>/ directory)")
@click.option("--concurrency", default=5, help="Max number of profiles to run at the same time")
@click.option("--min-fit-score", default=0.6, help="Passed through to each profile's run")
@click.option("--platforms", default="", help="Passed through to each profile's run (e.g. 'linkedin,wellfound')")
def main(profiles: str, concurrency: int, min_fit_score: float, platforms: str) -> None:
    selected = [p.strip() for p in profiles.split(",") if p.strip()] or None
    names = _discover_profiles(selected)
    if not names:
        click.echo(f"No profiles found under {PROFILES_DIR}/ -- create profiles/<name>/resume.pdf for each account.")
        return

    extra_args = ["--platforms", platforms] if platforms else []

    click.echo(f"Running {len(names)} profile(s) with concurrency {concurrency}: {', '.join(names)}")

    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(_run_one, name, min_fit_score, extra_args): name for name in names}
        for future in as_completed(futures):
            name, code, log_path = future.result()
            status = "skipped" if code == -1 else ("ok" if code == 0 else f"exit {code}")
            click.echo(f"[{name}] {status} -- log: {log_path}")
            results.append((name, code))

    ran = sum(1 for _, c in results if c == 0)
    skipped = sum(1 for _, c in results if c == -1)
    failed = sum(1 for _, c in results if c not in (0, -1))
    click.echo(f"\nDone: {ran} ran, {skipped} skipped (needs login), {failed} failed.")


if __name__ == "__main__":
    main()
