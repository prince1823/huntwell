from __future__ import annotations

import re
import urllib.parse

from ..models import ApplicationResult, JobPosting, Profile
from .base import PlatformAdapter

# Ashby is structurally different from LinkedIn/Wellfound/Indeed: it's not a
# job board with its own account -- each company hosts its own career page
# and application form at jobs.ashbyhq.com/<company-slug>, and applying is a
# guest form, no login or account needed at all. That sidesteps the
# bot-detection problems we hit on the other platforms entirely (verified
# live on 2026-10-29: loads cleanly headless, no Cloudflare challenge).
#
# Because there's no cross-company search, search_jobs iterates a
# configurable list of company slugs (config["ashby_companies"]) rather than
# a keyword search against one site.
#
# The application form always has a clearly-marked, separate "Diversity
# Survey" section (age/gender/ethnicity/disability) that's explicitly
# voluntary and doesn't affect the application. That section is NOT part of
# the standard ashby-application-form-field-entry pattern this adapter
# iterates, so it's skipped structurally; _is_diversity_section is a second,
# explicit guard in case another company's form nests it differently. These
# are self-identification questions, not something to infer or guess from a
# resume -- this adapter never answers them.

JOB_ID_RE = re.compile(r"/([a-f0-9-]{36})$")


class AshbyAdapter(PlatformAdapter):
    name = "ashby"
    requires_login = False

    def ensure_logged_in(self, email: str, password: str) -> None:
        pass  # guest application form, no account needed

    def search_jobs(self, profile: Profile) -> list[JobPosting]:
        postings: list[JobPosting] = []
        companies = self.config.get("ashby_companies", [])
        titles = [t.lower() for t in self.config.get("titles", [])]

        for company in companies:
            self.page.goto(f"https://jobs.ashbyhq.com/{company}", wait_until="domcontentloaded")
            self.page.wait_for_timeout(1500)

            links = self.page.locator(f"a[href^='/{company}/']")
            seen_ids: set[str] = set()
            count = min(links.count(), 50)
            for i in range(count):
                link = links.nth(i)
                href = link.get_attribute("href") or ""
                m = JOB_ID_RE.search(href)
                if not m:
                    continue
                job_id = m.group(1)
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)

                title_el = link.locator("h3.ashby-job-posting-brief-title")
                job_title = (
                    title_el.first.inner_text().strip() if title_el.count() > 0 else link.inner_text().split("\n")[0].strip()
                )
                if titles and not any(t in job_title.lower() for t in titles):
                    continue

                job_location = ""
                details = link.locator("div.ashby-job-posting-brief-details p").first
                if details.count() > 0:
                    job_location = details.inner_text().strip()

                postings.append(
                    JobPosting(
                        platform=self.name,
                        job_id=f"{company}:{job_id}",
                        title=job_title,
                        company=company,
                        location=job_location,
                        url=f"https://jobs.ashbyhq.com/{company}/{job_id}/application",
                    )
                )
        return postings

    def apply(self, job: JobPosting, profile: Profile) -> ApplicationResult:
        self.page.goto(job.url, wait_until="domcontentloaded")
        self.page.wait_for_timeout(2000)

        try:
            job.description = self.page.locator("body").inner_text()[:4000]
        except Exception:
            pass

        entries = self.page.locator("div.ashby-application-form-field-entry")
        if entries.count() == 0:
            return ApplicationResult(job=job, status="needs_review", reason="No application form found")

        self._fill_entries(entries, job, profile)

        # The Submit button's disabled state isn't reliably tied to
        # client-side validation on this form (confirmed live: it stayed
        # enabled with a required field still empty) -- check required
        # fields explicitly rather than trusting is_disabled().
        missing = self._unanswered_required_entries(entries)
        if missing:
            return ApplicationResult(
                job=job, status="needs_review", reason=f"Missing required field(s): {'; '.join(missing)}"
            )

        submit_btn = self.page.get_by_role("button", name=re.compile("Submit Application", re.I))
        if submit_btn.count() == 0:
            return ApplicationResult(job=job, status="needs_review", reason="No submit button found")

        if submit_btn.first.is_disabled():
            return ApplicationResult(
                job=job, status="needs_review", reason="Submit button disabled -- a required field was likely missed"
            )

        submit_btn.first.click()
        self.page.wait_for_timeout(2000)
        return ApplicationResult(job=job, status="applied")

    def _fill_entries(self, entries, job: JobPosting, profile: Profile) -> None:
        defaults = self.config.get("screening_defaults", {})
        count = entries.count()

        for i in range(count):
            entry = entries.nth(i)
            if self._is_diversity_section(entry):
                continue

            field_path = entry.get_attribute("data-field-path") or ""
            label = entry.locator("label.ashby-application-form-question-title")
            question = label.first.inner_text().strip() if label.count() > 0 else ""

            if field_path == "_systemfield_name":
                self._fill_text(entry, profile.full_name)
            elif field_path == "_systemfield_email":
                self._fill_text(entry, profile.email)
            elif field_path == "_systemfield_resume":
                self._upload_resume(entry, profile)
            elif field_path == "_systemfield_location":
                if profile.location:
                    self._fill_autocomplete(entry, profile.location)
                # else: leave blank rather than feed a non-location string
                # (e.g. "Remote") into a geo-autocomplete -- it can fuzzy-match
                # to an unrelated real place. A disabled Submit button from a
                # missed required field becomes needs_review, which is safer
                # than silently submitting a wrong location.
            elif entry.locator("input[type='file']").count() > 0:
                continue  # optional attachment (e.g. cover letter upload) -- leave blank
            elif entry.locator("[class*='-input-yesno']").count() > 0:
                self._fill_yesno(entry, job, profile, question, defaults)
            elif entry.locator("[role='combobox']").count() > 0:
                answer = self.tailor.answer_screening_question(profile, job, question, defaults)
                self._fill_autocomplete(entry, answer)
            elif entry.locator("textarea").count() > 0:
                self._fill_textarea(entry, job, profile, question, defaults)
            elif entry.locator("input[type='text'], input[type='email']").count() > 0:
                answer = self.tailor.answer_screening_question(profile, job, question, defaults)
                self._fill_text(entry, answer)

    def _fill_text(self, entry, value: str) -> None:
        input_el = entry.locator("input[type='text'], input[type='email']").first
        if (input_el.input_value() or "").strip():
            return
        input_el.fill(value or "")

    def _fill_textarea(self, entry, job: JobPosting, profile: Profile, question: str, defaults: dict) -> None:
        textarea = entry.locator("textarea").first
        if (textarea.input_value() or "").strip():
            return
        if "excite" in question.lower() or "why" in question.lower() or "interest" in question.lower():
            textarea.fill(self.tailor.company_interest_answer(profile, job))
        else:
            answer = self.tailor.answer_screening_question(profile, job, question, defaults)
            textarea.fill(answer)

    def _fill_yesno(self, entry, job: JobPosting, profile: Profile, question: str, defaults: dict) -> None:
        buttons = entry.locator("[class*='-input-yesno-option']")
        if buttons.count() == 0:
            return
        already = any(
            buttons.nth(j).get_attribute("aria-pressed") == "true" for j in range(buttons.count())
        )
        if already:
            return
        answer = self.tailor.answer_single_choice(profile, job, question, ["Yes", "No"], defaults)
        target_option = "yes" if answer.strip().lower() == "yes" else "no"
        for j in range(buttons.count()):
            btn = buttons.nth(j)
            if btn.get_attribute("data-option") == target_option:
                btn.click()
                return

    def _fill_autocomplete(self, entry, value: str) -> None:
        input_el = entry.locator("input[role='combobox'], input._input_d7ago_28").first
        if input_el.count() == 0 or (input_el.input_value() or "").strip():
            return
        input_el.fill(value)
        self.page.wait_for_timeout(800)
        options = self.page.locator("[role='option']")
        # Picking the first suggestion blindly is wrong -- a typeahead can
        # fuzzy-match to an unrelated result (e.g. "Remote" once matched a
        # town called "Remte, Latvia"). Only select an option that actually
        # shares a meaningful word with what we typed; otherwise leave the
        # field as typed-but-unselected rather than submit a wrong match.
        value_words = [w.lower() for w in re.split(r"[,\s]+", value) if len(w) >= 3]
        for i in range(options.count()):
            opt_text = options.nth(i).inner_text().lower()
            if any(w in opt_text for w in value_words):
                options.nth(i).click()
                return
        input_el.press("Escape")

    def _upload_resume(self, entry, profile: Profile) -> None:
        file_input = entry.locator("input[type='file']").first
        if profile.resume_pdf_path:
            file_input.set_input_files(profile.resume_pdf_path)
            self.page.wait_for_timeout(1500)

    def _unanswered_required_entries(self, entries) -> list[str]:
        missing = []
        for i in range(entries.count()):
            entry = entries.nth(i)
            if self._is_diversity_section(entry):
                continue
            label = entry.locator("label.ashby-application-form-question-title")
            if label.count() == 0:
                continue
            cls = label.first.get_attribute("class") or ""
            if "_required_" not in cls:
                continue
            question = label.first.inner_text().strip()

            if entry.locator("input[type='text'], input[type='email']").count() > 0:
                val = entry.locator("input[type='text'], input[type='email']").first.input_value()
                if not (val or "").strip():
                    missing.append(question)
            elif entry.locator("textarea").count() > 0:
                val = entry.locator("textarea").first.input_value()
                if not (val or "").strip():
                    missing.append(question)
            elif entry.locator("input[type='file']").count() > 0:
                continue  # can't read a file input's value; _upload_resume already handled it
            elif entry.locator("[class*='-input-yesno-option']").count() > 0:
                buttons = entry.locator("[class*='-input-yesno-option']")
                if not any(
                    buttons.nth(j).get_attribute("aria-pressed") == "true" for j in range(buttons.count())
                ):
                    missing.append(question)
            elif entry.locator("input[role='combobox']").count() > 0:
                val = entry.locator("input[role='combobox']").first.input_value()
                if not (val or "").strip():
                    missing.append(question)
        return missing

    @staticmethod
    def _is_diversity_section(entry) -> bool:
        try:
            section = entry.locator("xpath=ancestor::div[contains(@class,'ashby-application-form-section-c')][1]")
            if section.count() == 0:
                return False
            heading = section.locator("h2").first
            if heading.count() == 0:
                return False
            return "diversity" in heading.inner_text().lower()
        except Exception:
            return False
