from __future__ import annotations

import urllib.parse

import click
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ..models import ApplicationResult, JobPosting, Profile
from .base import PlatformAdapter

# NOTE: Indeed shows frequent CAPTCHAs (Cloudflare/hCaptcha) on login and on
# repeated navigation. This adapter only handles "Indeed Apply" quick-apply
# flows; postings that redirect to an external ATS are marked needs_review.
#
# Login is intentionally NOT automated -- same reasoning as LinkedIn and
# Wellfound: typing credentials into a scripted browser risks tripping bot
# detection, and this account signed in via Google rather than a password
# anyway. We open a real, visible browser window and wait for a human to log
# in by hand; the session is then saved to auth_state/indeed.json.
#
# UNVERIFIED: unlike the LinkedIn and Wellfound adapters, nothing below this
# comment has been confirmed against a live account yet -- selectors are the
# original first-draft guesses and will very likely need fixing the same way
# LinkedIn's and Wellfound's did.

LOGIN_WAIT_TIMEOUT_MS = 180_000  # 3 minutes to complete login by hand


class IndeedAdapter(PlatformAdapter):
    name = "indeed"

    def ensure_logged_in(self, email: str, password: str) -> None:
        self.page.goto("https://www.indeed.com/", wait_until="domcontentloaded")
        if self.page.locator("a[href*='/account/logout']").count() > 0:
            return

        self.page.goto(
            "https://secure.indeed.com/auth?hl=en_US&co=US", wait_until="domcontentloaded"
        )
        click.echo(
            "[indeed] No valid session found. A browser window is open -- "
            "please log in by hand (email/password or Google), then this "
            "script will continue automatically once it sees you're logged in."
        )
        try:
            self.page.wait_for_selector("a[href*='/account/logout']", timeout=LOGIN_WAIT_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            raise RuntimeError(
                "Timed out waiting for manual Indeed login. Re-run with "
                "--no-headless and log in within 3 minutes of the window opening."
            )

    def search_jobs(self, profile: Profile) -> list[JobPosting]:
        postings: list[JobPosting] = []
        titles = self.config.get("titles", [])
        locations = self.config.get("locations", ["Remote"])

        for title in titles:
            for location in locations:
                params = {"q": title, "l": location, "sort": "date"}
                url = "https://www.indeed.com/jobs?" + urllib.parse.urlencode(params)
                self.page.goto(url, wait_until="domcontentloaded")
                self.page.wait_for_timeout(2000)

                cards = self.page.locator("div.job_seen_beacon, div.cardOutline").all()
                for card in cards[:25]:
                    try:
                        link = card.locator("a.jcs-JobTitle, h2.jobTitle a")
                        href = link.first.get_attribute("href") or ""
                        job_id = href.split("jk=")[-1].split("&")[0] if "jk=" in href else href
                        job_title = link.first.inner_text().strip()
                        company = card.locator("span.companyName").first.inner_text().strip()
                        job_location = card.locator("div.companyLocation").first.inner_text().strip()
                    except Exception:
                        continue

                    full_url = (
                        href if href.startswith("http") else f"https://www.indeed.com{href}"
                    )
                    postings.append(
                        JobPosting(
                            platform=self.name,
                            job_id=job_id,
                            title=job_title,
                            company=company,
                            location=job_location,
                            url=full_url,
                        )
                    )
        return postings

    def apply(self, job: JobPosting, profile: Profile) -> ApplicationResult:
        self.page.goto(job.url, wait_until="domcontentloaded")
        self.page.wait_for_timeout(1500)

        try:
            job.description = self.page.locator("#jobDescriptionText").first.inner_text()
        except Exception:
            pass

        apply_btn = self.page.locator("button:has-text('Apply now'), #indeedApplyButton")
        if apply_btn.count() == 0:
            return ApplicationResult(
                job=job, status="skipped", reason="No Indeed Apply button (external ATS)"
            )

        apply_btn.first.click()
        self.page.wait_for_timeout(1500)

        # Indeed Apply opens an iframe-based multi-step flow.
        frame = None
        for f in self.page.frames:
            if "smartapply" in f.url or "indeedapply" in f.url:
                frame = f
                break
        if frame is None:
            return ApplicationResult(
                job=job, status="needs_review", reason="Could not locate Indeed Apply iframe"
            )

        for _ in range(10):
            self._fill_visible_fields(frame, job, profile)

            submit_btn = frame.locator("button:has-text('Submit your application')")
            if submit_btn.count() > 0:
                submit_btn.first.click()
                self.page.wait_for_timeout(1500)
                return ApplicationResult(job=job, status="applied")

            continue_btn = frame.locator("button:has-text('Continue')")
            if continue_btn.count() > 0:
                continue_btn.first.click()
                self.page.wait_for_timeout(1000)
                continue

            return ApplicationResult(
                job=job, status="needs_review", reason="Unrecognized Indeed Apply step"
            )

        return ApplicationResult(job=job, status="failed", reason="Exceeded max Indeed Apply steps")

    def _fill_visible_fields(self, frame, job: JobPosting, profile: Profile) -> None:
        for input_el in frame.locator("input[type='text'], input[type='number']").all():
            if (input_el.input_value() or "").strip():
                continue
            label = self._label_for(frame, input_el)
            answer = self.tailor.answer_screening_question(
                profile, job, label, self.config.get("screening_defaults", {})
            )
            input_el.fill(answer)

        for textarea in frame.locator("textarea").all():
            if (textarea.input_value() or "").strip():
                continue
            label = self._label_for(frame, textarea)
            if "cover" in label.lower():
                textarea.fill(self.tailor.cover_letter(profile, job))

    def _label_for(self, frame, element) -> str:
        try:
            label_id = element.get_attribute("id")
            if label_id:
                label = frame.locator(f"label[for='{label_id}']")
                if label.count() > 0:
                    return label.first.inner_text().strip()
        except Exception:
            pass
        return ""
