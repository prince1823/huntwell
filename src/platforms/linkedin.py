from __future__ import annotations

import re
import urllib.parse

import click
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ..models import ApplicationResult, JobPosting, Profile
from .base import PlatformAdapter

# NOTE: LinkedIn changes its DOM structure frequently, so the search/apply
# selectors below WILL need maintenance. Login is intentionally NOT automated:
# LinkedIn's Cloudflare bot protection serves a challenge page instead of the
# login form to plain automated browsers, before any credentials are even
# typed. So we open a real, visible browser window and wait for a human to log
# in by hand -- the same way AIHawk-style tools do it. Once that session is
# saved (auth_state/linkedin.json), subsequent runs can skip this.
#
# As of late 2025/2026 LinkedIn's job search UI renders with fully hashed,
# build-specific CSS class names (e.g. "ajrax7 ajrluf") that carry no stable
# meaning across deploys -- plain class selectors are a dead end. What *is*
# stable is the accessibility layer: each job card has a
# button[aria-label="Dismiss <title> job"], and the Easy Apply modal's
# controls are matched by their visible button text ("Next", "Review",
# "Submit application") via get_by_role. Verified against a live account on
# 2026-10-29; only the search list and the modal's first page were confirmed
# interactively -- later modal pages were not clicked through live to avoid
# submitting a real test application, so screening-question field discovery
# (_fill_visible_fields) may still need adjustment on first real run.
#
# IMPORTANT: LinkedIn is mid-rollout of this new UI -- not every account has
# it yet. Confirmed on 2026-10-29 that a second real account was still on the
# "classic" job search UI (plain semantic classnames like
# job-card-container, real <li> elements, a data-occludable-job-id attribute
# holding the job ID directly -- no click-to-reveal needed). On that UI, the
# role='button' ancestor the new-UI path relies on doesn't exist at all, so
# card extraction silently found 0 results every time despite the dismiss
# buttons themselves being present on both UIs. search_jobs tries the classic
# <li> structure first (cheaper, no clicking) and falls back to the new UI's
# click-based extraction if no such <li> elements are found.

LOGIN_WAIT_TIMEOUT_MS = 180_000  # 3 minutes to complete login/2FA by hand
DISMISS_BUTTON_RE = re.compile(r"^Dismiss (.+) job$")
STATUS_LINE_RE = re.compile(
    r"\b(ago|applicant|actively|viewed|applied|promoted|easy apply|verified job)\b", re.I
)
# Questions whose answer must come fresh from the resume every time, even if
# LinkedIn has pre-filled a stale value from a past application/profile field
# (e.g. a leftover "2" from months ago that no longer matches the resume).
EXPERIENCE_QUESTION_RE = re.compile(r"years?.*(of\s+)?(work\s+)?experience", re.I)


class LinkedInAdapter(PlatformAdapter):
    name = "linkedin"

    def ensure_logged_in(self, email: str, password: str) -> None:
        self.page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
        if "/feed" in self.page.url:
            return  # existing storage_state session is still valid

        self.page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
        click.echo(
            "[linkedin] No valid session found. A browser window is open -- "
            "please log in by hand (including any 2FA/checkpoint), then this "
            "script will continue automatically once it sees your feed."
        )
        try:
            self.page.wait_for_url("**/feed/**", timeout=LOGIN_WAIT_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            raise RuntimeError(
                "Timed out waiting for manual LinkedIn login. Re-run with "
                "--no-headless and log in within 3 minutes of the window opening."
            )

    def search_jobs(self, profile: Profile) -> list[JobPosting]:
        postings: list[JobPosting] = []
        seen_ids: set[str] = set()
        titles = self.config.get("titles", [])
        # "Remote" as a literal location string only matches postings
        # explicitly tagged remote -- it was hardcoded as the default here,
        # which silently excluded every onsite posting regardless of what a
        # profile's resume actually said. Same spirit as titles_from_resume:
        # use the resume's own stated location by default (surfaces both
        # onsite roles there and remote roles, which still show up in
        # city-based searches), falling back to an explicit config override
        # or "Remote" only if the resume has no location at all.
        if self.config.get("locations_from_resume", True) and profile.location:
            locations = [profile.location]
        else:
            locations = self.config.get("locations", ["Remote"])
        experience_level = self._experience_level_param(profile.years_of_experience)

        # LinkedIn's keyword search supports boolean OR, so all titles go in
        # ONE query instead of a separate search per title -- confirmed live
        # (a 5-title OR query returned 99+ relevant results, same as
        # searching each title separately). location has no such
        # combinator in the search URL (only one value at a time), so that
        # loop stays -- this still cuts N titles x M locations down to just
        # M searches instead of N x M.
        keywords = " OR ".join(f'"{t}"' for t in titles) if titles else ""

        for location in locations:
            params = {
                "keywords": keywords,
                "location": location,
                "f_AL": "true",  # Easy Apply filter
                "f_E": experience_level,  # LinkedIn's own seniority filter, from the resume
                "sortBy": "DD",  # most recent
            }
            url = "https://www.linkedin.com/jobs/search/?" + urllib.parse.urlencode(params)
            # Rendering this page is genuinely variable in latency
            # (noticeably slower/less consistent on newer, less-weighted
            # accounts) -- a single 15s wait isn't reliable enough to
            # conclude "no results" from. Retry the navigation once before
            # giving up on this location.
            found_results = False
            for attempt in range(2):
                self.page.goto(url, wait_until="domcontentloaded")
                try:
                    self.page.wait_for_selector("button[aria-label^='Dismiss ']", timeout=15000)
                    found_results = True
                    break
                except PlaywrightTimeoutError:
                    continue
            if not found_results:
                continue  # no results for this location

            self.page.wait_for_timeout(1500)

            classic_cards = self.page.locator("li[data-occludable-job-id]")
            for _ in range(6):
                if classic_cards.count() > 0:
                    break
                self.page.wait_for_timeout(500)

            if classic_cards.count() > 0:
                new_postings = self._extract_classic_ui_cards(classic_cards, seen_ids)
            else:
                new_postings = self._extract_new_ui_cards(seen_ids)
            postings.extend(new_postings)
        return postings

    def _extract_classic_ui_cards(self, cards, seen_ids: set[str]) -> list[JobPosting]:
        """LinkedIn's classic job search UI (still served to some accounts
        alongside the newer one) -- real <li> elements with the job ID
        available directly as a data attribute, no click needed."""
        results = []
        count = min(cards.count(), 25)
        for i in range(count):
            li = cards.nth(i)
            job_id = li.get_attribute("data-occludable-job-id") or ""
            if not job_id or job_id in seen_ids:
                continue
            seen_ids.add(job_id)

            title_link = li.locator("a.job-card-list__title--link")
            if title_link.count() == 0:
                continue
            raw_title = title_link.first.get_attribute("aria-label") or title_link.first.inner_text()
            job_title = re.sub(r"\s*with verification\s*$", "", raw_title.strip(), flags=re.I)

            company = ""
            subtitle = li.locator(".artdeco-entity-lockup__subtitle")
            if subtitle.count() > 0:
                company = subtitle.first.inner_text().strip()

            job_location = ""
            caption = li.locator(".artdeco-entity-lockup__caption")
            if caption.count() > 0:
                job_location = caption.first.inner_text().strip()

            results.append(
                JobPosting(
                    platform=self.name,
                    job_id=job_id,
                    title=job_title,
                    company=company,
                    location=job_location,
                    url=f"https://www.linkedin.com/jobs/view/{job_id}/",
                    easy_apply=True,
                )
            )
        return results

    def _extract_new_ui_cards(self, seen_ids: set[str]) -> list[JobPosting]:
        """LinkedIn's newer "AI-powered" job search UI -- hashed CSS classes,
        no direct job-ID attribute, so each card must be clicked and the
        resulting URL parsed for currentJobId."""
        results = []
        dismiss_buttons = self.page.locator("button[aria-label^='Dismiss ']")
        count = min(dismiss_buttons.count(), 25)

        for i in range(count):
            btn = dismiss_buttons.nth(i)
            aria_label = btn.get_attribute("aria-label") or ""
            m = DISMISS_BUTTON_RE.match(aria_label)
            if not m:
                continue
            job_title = m.group(1).strip()

            card = btn.locator("xpath=ancestor::*[@role='button'][1]")
            if card.count() == 0:
                continue

            company, job_location = self._extract_company_location(job_title, card.first)

            try:
                card.first.click()
                self.page.wait_for_timeout(1200)
            except Exception:
                continue

            id_match = re.search(r"currentJobId=(\d+)", self.page.url)
            if not id_match:
                continue
            job_id = id_match.group(1)
            if job_id in seen_ids:
                continue
            seen_ids.add(job_id)

            results.append(
                JobPosting(
                    platform=self.name,
                    job_id=job_id,
                    title=job_title,
                    company=company,
                    location=job_location,
                    url=f"https://www.linkedin.com/jobs/view/{job_id}/",
                    easy_apply=True,
                )
            )
        return results

    @staticmethod
    def _experience_level_param(years: int) -> str:
        """Map resume-derived years of experience to LinkedIn's f_E seniority
        filter codes (1=Internship, 2=Entry level, 3=Associate,
        4=Mid-Senior, 5=Director, 6=Executive) so the search itself targets
        postings the candidate is actually eligible for."""
        if years <= 1:
            return "1,2"
        if years <= 4:
            return "2,3"
        if years <= 8:
            return "3,4"
        return "4,5,6"

    @staticmethod
    def _extract_company_location(job_title: str, card) -> tuple[str, str]:
        lines = [l.strip() for l in card.inner_text().split("\n") if l.strip()]
        candidates = [
            l for l in lines if job_title not in l and not STATUS_LINE_RE.search(l)
        ]
        company = candidates[0] if candidates else ""
        job_location = candidates[1] if len(candidates) > 1 else ""
        return company, job_location

    def apply(self, job: JobPosting, profile: Profile) -> ApplicationResult:
        self.page.goto(job.url, wait_until="domcontentloaded")
        self.page.wait_for_timeout(2000)

        try:
            job.description = self._get_description_text()
        except Exception:
            pass

        easy_apply_btn = self.page.get_by_role("button", name=re.compile("Easy Apply", re.I))
        if easy_apply_btn.count() == 0:
            return ApplicationResult(job=job, status="skipped", reason="No Easy Apply button found")

        easy_apply_btn.first.click()
        self.page.wait_for_timeout(1500)

        # Easy Apply renders inside a native <dialog> element -- not a <form>,
        # not role="dialog" -- so every field/button query below is scoped to
        # it to avoid touching the page behind the modal (e.g. the top-nav
        # search box, which also matches input[type='text']).
        dialog = self.page.locator("dialog")

        # Multi-step modal: answer fields, click Next/Review until Submit, or
        # give up after a sane number of steps. Buttons are matched by their
        # visible text, not CSS classes, since LinkedIn's classes are hashed
        # per-build and not stable.
        for _ in range(10):
            before = self._step_fingerprint(dialog)
            self._fill_visible_fields(dialog, job, profile)

            submit_btn = dialog.get_by_role("button", name=re.compile("Submit application", re.I))
            if submit_btn.count() > 0:
                submit_btn.first.click()
                self.page.wait_for_timeout(2000)
                if self._submission_confirmed():
                    return ApplicationResult(job=job, status="applied")
                # Clicking Submit is not proof LinkedIn accepted it -- no
                # confirmation (toast/banner, or the modal actually closing)
                # means we genuinely don't know whether it went through.
                # Marking "applied" here with no verification is exactly the
                # bug a real account hit: DB said applied, LinkedIn showed
                # not applied. Fail safe to needs_review instead of guessing.
                return ApplicationResult(
                    job=job,
                    status="needs_review",
                    reason="Clicked Submit but could not confirm LinkedIn accepted the application",
                )

            unanswered = self._unanswered_required_fields(dialog)
            if unanswered:
                return ApplicationResult(
                    job=job,
                    status="needs_review",
                    reason=f"Could not confidently answer: {'; '.join(unanswered)}",
                )

            next_btn = dialog.get_by_role("button", name=re.compile(r"^(Next|Review)$", re.I))
            if next_btn.count() == 0:
                return ApplicationResult(
                    job=job, status="needs_review", reason="Unrecognized Easy Apply step"
                )

            next_btn.first.click()
            self.page.wait_for_timeout(1200)

            if self._step_fingerprint(dialog) == before:
                # Clicking Next/Review didn't change the step -- client-side
                # validation is silently blocking progress (e.g. an answer in
                # a format LinkedIn doesn't accept). Don't keep hammering it.
                return ApplicationResult(
                    job=job, status="needs_review", reason="Form did not advance after filling"
                )

        return ApplicationResult(job=job, status="failed", reason="Exceeded max Easy Apply steps")

    @staticmethod
    def _step_fingerprint(dialog) -> str:
        try:
            return dialog.inner_text()[:500]
        except Exception:
            return ""

    def _submission_confirmed(self) -> bool:
        """Checked right after clicking Submit -- clicking a button is not
        proof LinkedIn accepted the application. Two independent signals,
        either one counts as confirmed: (1) a visible confirmation message
        (LinkedIn's own post-submit screen/toast says "application was
        sent" or similar), or (2) the Easy Apply dialog itself closed,
        which it does on a real successful submit but not when a
        client-side error keeps it open. NOT verified against a real
        submission yet (avoided on purpose, to not submit a live test
        application) -- if this starts producing false needs_review on
        postings that actually did go through, the signals here need
        adjusting against a real one."""
        try:
            confirmation = self.page.get_by_text(
                re.compile(r"application (was|has been) sent|your application.{0,20}submitted", re.I)
            )
            if confirmation.count() > 0:
                return True
        except Exception:
            pass
        try:
            return self.page.locator("dialog").count() == 0
        except Exception:
            return False

    def _get_description_text(self) -> str:
        columns = self.page.locator("[data-testid='lazy-column']")
        best = ""
        for i in range(columns.count()):
            text = columns.nth(i).inner_text()
            if len(text) > len(best):
                best = text
        return best

    def _fill_visible_fields(self, dialog, job: JobPosting, profile: Profile) -> None:
        defaults = self.config.get("screening_defaults", {})

        # Short-text / numeric screening questions. The question text is the
        # input's own aria-label -- LinkedIn renders it directly, there's no
        # separate <label> element to look up. "Years of experience with X"
        # questions are always re-answered from the resume even if LinkedIn
        # pre-filled a stale value (e.g. a leftover "2" from a past profile
        # edit that no longer matches what's on the resume today).
        for input_el in dialog.locator("input[type='text'], input[type='number']").all():
            question = self._question_text(dialog, input_el)
            current = (input_el.input_value() or "").strip()
            is_experience_question = bool(EXPERIENCE_QUESTION_RE.search(question))
            if current and not is_experience_question:
                continue
            max_length = self._char_limit(input_el)
            prompt_question = question
            if max_length:
                # LinkedIn enforces this client-side via a JS character
                # counter, not a native maxlength attribute, with no error
                # shown until Next/Review is clicked -- an over-length
                # answer silently blocks the whole step, so both hint the
                # model and hard-truncate as a safety net.
                prompt_question = f"{question} (answer in {max_length} characters or fewer)"
            answer = self.tailor.answer_screening_question(profile, job, prompt_question, defaults)
            if max_length:
                answer = answer[:max_length]
            input_el.fill(answer)

        self._fill_radio_groups(dialog, job, profile, defaults)

        # Native <select> dropdowns. Unlike text inputs, these have no
        # aria-label -- the question lives in a separate label[for=id].
        for select_el in dialog.locator("select").all():
            current = (select_el.input_value() or "").strip()
            if current:
                continue
            question = self._question_text(dialog, select_el)
            options = self._select_options(select_el)
            if not options:
                continue
            answer = self.tailor.answer_screening_question(profile, job, question, defaults)
            idx = self._closest_match_index(answer, options)
            select_el.select_option(label=options[idx if idx is not None else 0])

        # Required checkboxes only (consent/terms) -- never auto-check an
        # optional opt-in like "email me similar jobs".
        for checkbox in dialog.locator("input[type='checkbox'][required]").all():
            if not checkbox.is_checked():
                checkbox.check(force=True)

        # Free-text boxes: cover letter / "why interested" get a tailored,
        # company-specific answer, anything else gets a direct
        # resume-grounded answer. Optional boxes are left blank rather than
        # padded with an answer nobody asked for.
        for textarea in dialog.locator("textarea").all():
            if (textarea.input_value() or "").strip():
                continue
            if (textarea.get_attribute("required") or textarea.get_attribute("aria-required")) is None:
                continue
            question = self._question_text(dialog, textarea)
            if "cover" in question.lower() or "why" in question.lower() or "interest" in question.lower():
                textarea.fill(self.tailor.company_interest_answer(profile, job))
            else:
                answer = self.tailor.answer_screening_question(profile, job, question, defaults)
                textarea.fill(answer)

    def _fill_radio_groups(self, dialog, job: JobPosting, profile: Profile, defaults: dict) -> None:
        # LinkedIn groups radio options by the `name` attribute rather than a
        # <fieldset>; every option in a group shares the same aria-label
        # (the question itself), and each option's visible text sits in a
        # sibling <p>, not inside a <label>.
        radios = dialog.locator("input[type='radio']")
        seen_names: set[str] = set()
        for i in range(radios.count()):
            radio = radios.nth(i)
            name = radio.get_attribute("name") or ""
            if not name or name in seen_names:
                continue
            seen_names.add(name)

            group = dialog.locator(f"input[type='radio'][name='{name}']")
            if any(group.nth(j).is_checked() for j in range(group.count())):
                continue

            question = (group.first.get_attribute("aria-label") or "").strip()
            option_texts = [self._radio_option_text(group.nth(j)) for j in range(group.count())]
            answer = self.tailor.answer_screening_question(profile, job, question, defaults)
            idx = self._closest_match_index(answer, option_texts)
            target = group.nth(idx if idx is not None else 0)
            target_id = target.get_attribute("id")
            if target_id:
                dialog.locator(f"label[for='{target_id}']").click()
            else:
                target.check(force=True)

    @staticmethod
    def _question_text(dialog, element) -> str:
        """Most Easy Apply fields carry their question as aria-label
        directly; <select> dropdowns instead use a separate label[for=id]."""
        aria = (element.get_attribute("aria-label") or "").strip()
        if aria:
            return aria
        el_id = element.get_attribute("id")
        if el_id:
            label = dialog.locator(f"label[for='{el_id}']")
            if label.count() > 0:
                try:
                    return label.first.inner_text().strip()
                except Exception:
                    pass
        return ""

    @staticmethod
    def _select_options(select_el) -> list[str]:
        """Visible option labels, excluding the disabled "Select an option"
        placeholder (value="")."""
        option_els = select_el.locator("option")
        options = []
        for i in range(option_els.count()):
            opt = option_els.nth(i)
            if (opt.get_attribute("value") or "") == "":
                continue
            text = opt.inner_text().strip()
            if text:
                options.append(text)
        return options

    def _char_limit(self, input_el) -> int | None:
        """Some text fields enforce a character cap via a JS counter (e.g.
        "12/20") linked by aria-describedby, rather than a native maxlength
        attribute -- read that counter so we don't silently exceed it."""
        described_by = (input_el.get_attribute("aria-describedby") or "").split()
        if not described_by:
            return None
        info_el = self.page.locator(f"#{described_by[0]}")
        if info_el.count() == 0:
            return None
        try:
            text = info_el.first.inner_text()
        except Exception:
            return None
        m = re.search(r"/(\d+)", text)
        return int(m.group(1)) if m else None

    @staticmethod
    def _radio_option_text(radio_locator) -> str:
        try:
            sibling = radio_locator.locator("xpath=../following-sibling::div[1]")
            if sibling.count() > 0:
                return sibling.first.inner_text().strip()
        except Exception:
            pass
        return ""

    @staticmethod
    def _closest_match_index(answer: str, options: list[str]) -> int | None:
        answer_lower = answer.strip().lower()
        for i, opt in enumerate(options):
            opt_lower = opt.strip().lower()
            if opt_lower and (opt_lower in answer_lower or answer_lower in opt_lower):
                return i
        return None

    @staticmethod
    def _unanswered_required_fields(dialog) -> list[str]:
        """Required fields that are still empty after a fill pass -- surfaced
        as needs_review instead of letting the Next click silently fail."""
        missing = []
        for input_el in dialog.locator(
            "input[type='text'][required], input[type='number'][required]"
        ).all():
            if not (input_el.input_value() or "").strip():
                missing.append(input_el.get_attribute("aria-label") or "unlabeled field")
        for select_el in dialog.locator("select[required]").all():
            if not (select_el.input_value() or "").strip():
                missing.append(select_el.get_attribute("aria-label") or "unlabeled dropdown")
        return missing
