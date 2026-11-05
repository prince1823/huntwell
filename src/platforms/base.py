from __future__ import annotations

import random
import re
from abc import ABC, abstractmethod
from collections.abc import Callable

from playwright.sync_api import BrowserContext

from ..llm import Tailor
from ..models import ApplicationResult, JobPosting, Profile

# Widgets/iframes that mean the submit never actually went through.
_CAPTCHA_SELECTORS = (
    "iframe[src*='recaptcha']",
    "iframe[src*='hcaptcha']",
    "iframe[src*='challenges.cloudflare']",
    "iframe[src*='turnstile']",
    "iframe[title*='reCAPTCHA' i]",
    "iframe[title*='hCaptcha' i]",
    ".g-recaptcha",
    ".h-captcha",
    "div.cf-turnstile",
    "#cf-challenge-running",
    "[data-cf-challenge]",
)
_CAPTCHA_TEXT = re.compile(
    r"verify you('re| are) (a )?human|i['’]?m not a robot|complete (the |a )?(captcha|security check)"
    r"|unusual traffic|security (check|challenge)|please (complete|solve).{0,40}captcha"
    r"|hcaptcha|recaptcha",
    re.I,
)
_CHALLENGE_URL = re.compile(r"captcha|challenge|cdn-cgi|turnstile|hcaptcha|recaptcha", re.I)


class PlatformAdapter(ABC):
    """Common interface every job site adapter implements.

    A concrete adapter owns a persistent Playwright browser context (so login
    sessions survive between runs) and knows how to search for jobs and fill
    that site's specific application form.
    """

    name: str = "base"
    requires_login: bool = True

    def __init__(self, context: BrowserContext, tailor: Tailor, config: dict):
        self.context = context
        self.tailor = tailor
        self.config = config
        self.page = context.new_page()

    def _form_delay_cfg(self) -> dict:
        return self.config.get("form_fill_delay_ms") or {}

    def _human_field_pause(self) -> None:
        """Wait between fields on one form, as if reading the next question.

        This is not apply_delay_seconds (the gap between separate
        applications). Instant fill of every widget in a few hundred ms is
        a bot-shaped timing pattern even when submissions themselves are
        spaced out. 0/0 (the default) means no pause.
        """
        cfg = self._form_delay_cfg()
        lo = int(cfg.get("field_min", 0) or 0)
        hi = int(cfg.get("field_max", 0) or 0)
        if hi <= 0:
            return
        if hi < lo:
            lo = hi
        self.page.wait_for_timeout(random.randint(lo, hi))

    def _human_type(self, locator, value: str) -> None:
        """Type into a text widget with a per-keystroke delay instead of
        dumping the whole value with fill(). Falls back to fill() when
        key delay is disabled, typing fails, or the value is empty.
        Long answers are sped up so total typing stays under typing_cap_ms
        -- a cover-letter-length box shouldn't stall the run for minutes.
        """
        value = value or ""
        cfg = self._form_delay_cfg()
        lo = int(cfg.get("key_min", 0) or 0)
        hi = int(cfg.get("key_max", 0) or 0)
        if hi <= 0 or not value:
            locator.fill(value)
            return
        if hi < lo:
            lo = hi
        delay = random.randint(lo, hi)
        cap = int(cfg.get("typing_cap_ms", 8000) or 8000)
        if delay * len(value) > cap:
            delay = max(lo, cap // len(value)) or lo
        try:
            locator.click()
            locator.fill("")
            locator.press_sequentially(value, delay=delay)
        except Exception:
            locator.fill(value)

    def _url_looks_like_challenge(self, url: str) -> bool:
        return bool(_CHALLENGE_URL.search(url or ""))

    def _captcha_blocking(self) -> bool:
        """True when a CAPTCHA / bot-check is actually on screen after Submit.

        Presence of a hidden recaptcha badge script is ignored; only a
        visible widget, challenge iframe, or challenge copy counts. A
        URL that itself is a challenge page also counts.
        """
        try:
            if self._url_looks_like_challenge(self.page.url):
                return True
        except Exception:
            pass
        for sel in _CAPTCHA_SELECTORS:
            try:
                loc = self.page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    return True
            except Exception:
                continue
        try:
            text = self.page.get_by_text(_CAPTCHA_TEXT)
            if text.count() > 0 and text.first.is_visible():
                return True
        except Exception:
            pass
        return False

    def _watch_submit_outcome(self, is_confirmed: Callable[[], bool]) -> str:
        """Poll after Submit instead of a single short wait.

        Returns 'applied', 'captcha', or 'unconfirmed'. Captcha is checked
        first so a challenge-page URL change is not mistaken for success.
        How long we watch is submit_observe_ms in form_fill_delay_ms
        (default 45s) -- long enough for a confirmation screen *or* a
        delayed CAPTCHA to actually appear.
        """
        cfg = self._form_delay_cfg()
        total_ms = int(cfg.get("submit_observe_ms", 45000) or 45000)
        step_ms = 1500
        elapsed = 0
        captcha_seen = False
        while elapsed < total_ms:
            self.page.wait_for_timeout(step_ms)
            elapsed += step_ms
            if self._captcha_blocking():
                captcha_seen = True
                continue
            try:
                if is_confirmed():
                    return "applied"
            except Exception:
                continue
        if captcha_seen or self._captcha_blocking():
            return "captcha"
        return "unconfirmed"

    @abstractmethod
    def ensure_logged_in(self, email: str, password: str, headless: bool = False) -> None:
        """Log in if the stored session (context storage_state) isn't already
        authenticated. headless tells an adapter whose login requires a human
        (LinkedIn, Wellfound) whether a visible window actually exists for
        someone to log into -- if headless and the session turns out to be
        invalid, it should fail immediately with a clear message instead of
        waiting out the full manual-login timeout for a login nobody can
        perform."""

    @abstractmethod
    def search_jobs(self, profile: Profile) -> list[JobPosting]:
        """Return candidate postings for the titles/locations in config, newest first."""

    @abstractmethod
    def apply(self, job: JobPosting, profile: Profile) -> ApplicationResult:
        """Fill and submit the application for one posting."""
