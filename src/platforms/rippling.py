from __future__ import annotations

import re

from ..llm import PROFILE_SLOTS
from ..models import ApplicationResult, JobPosting, Profile
from .base import PlatformAdapter

# Same guest-apply shape as Ashby/Workable: no cross-site account, each
# company hosts its own board at ats.rippling.com/<company-slug>/jobs, and
# the board itself is a public, keyless JSON API --
# api.rippling.com/platform/api/ats/v1/board/<token>/jobs -- confirmed live
# 2026-11-01 (from danielhagever/job-board-apis, cross-checked against
# Rippling's own board and a handful of real customer boards). It returns
# one entry per (job, location) pair -- a job open in 5 cities is 5 entries,
# each with its own uuid -- not one row per job, so dedupe happens
# naturally through the tracker's dedupe_key (platform:job_id) the same way
# a repost would.
#
# There is no free cross-company aggregator for Rippling the way Ashby has
# one -- same situation as Workable, so discovery is a pinned company list
# in config["rippling_companies"], each verified live to actually resolve
# (2026-11-01): rippling, carbon-health, vulcanforms, princesspolly,
# beehive-industries, aalo-atomics, contour-education, perle.
BOARD_API = "https://api.rippling.com/platform/api/ats/v1/board/{company}/jobs"

# Confirmed live (2026-11-01) against a real Rippling application form
# (ats.rippling.com/rippling/...): every field sits in its own
# [data-testid="field"] wrapper, and the standard fields (not custom
# per-company screening questions) carry a STABLE data-testid on their
# actual input -- "input-first_name", "input-last_name", etc. -- unlike
# Ashby's field_path or Workable's name attribute, this doesn't vary by
# company, so these are matched directly against the whole form rather
# than through a per-wrapper walk. Two of these don't even sit in their
# own top-level [data-testid="field"] row: "Résumé"'s own label text is
# actually "Résumé" (not "CV"), which broke a label-keyword match outright,
# and the phone digits input ("input-phone_number") sits NESTED inside the
# "Phone number" group's wrapper alongside the separate country-code
# combobox, not as its own row -- a plain per-wrapper ".first" widget pick
# grabs the country combobox instead, as confirmed live by dumping the
# actual DOM. Matching these by their own fixed data-testid, independent
# of wrapper structure, sidesteps both problems. The country-code
# combobox itself is left on its IP-detected default, same known
# limitation already documented on Workable's phone field.
FIXED_FIELD_TESTIDS = {
    "input-resume",
    "input-first_name",
    "input-last_name",
    "input-email",
    "input-current_company",
    "input-linkedin_link",
    "input-phone_number",
}
# Left blank on purpose: cover letter is an optional second upload, and the
# two hidden fields are Rippling's own place-ID/AI-opt-out plumbing, not
# something a candidate answers.
SKIP_FIELD_TESTIDS = {"input-cover_letter", "input-externalPlaceId", "input-aiOptOut"}


def _split_name(full_name: str) -> tuple[str, str]:
    parts = (full_name or "").strip().split(None, 1)
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


class RipplingAdapter(PlatformAdapter):
    name = "rippling"
    requires_login = False

    def ensure_logged_in(self, email: str, password: str, headless: bool = False) -> None:
        pass  # guest application form, no account needed

    def search_jobs(self, profile: Profile) -> list[JobPosting]:
        postings: list[JobPosting] = []
        seen_ids: set[str] = set()
        companies = self.config.get("rippling_companies", [])
        titles = [t.lower() for t in self.config.get("titles", [])]

        for company in companies:
            try:
                resp = self.page.request.get(BOARD_API.format(company=company), timeout=15000)
                if not resp.ok:
                    continue
                entries = resp.json()
            except Exception:
                continue
            if not isinstance(entries, list):
                continue

            for item in entries:
                title = (item.get("name") or "").strip()
                if titles and not any(t in title.lower() for t in titles):
                    continue
                uuid = item.get("uuid") or ""
                key = f"{company}:{uuid}"
                if not uuid or key in seen_ids:
                    continue
                seen_ids.add(key)

                work_location = item.get("workLocation") or {}
                department = item.get("department") or {}

                postings.append(
                    JobPosting(
                        platform=self.name,
                        job_id=key,
                        title=title,
                        company=department.get("label") or company,
                        location=work_location.get("label") or "",
                        url=item.get("url") or f"https://ats.rippling.com/{company}/jobs/{uuid}",
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

        # search_jobs never populates job.description, so fit-scoring has
        # no JD text to weigh seniority against -- same gap confirmed live
        # on Wellfound/LinkedIn (a title with no seniority keyword hid an
        # explicit "2+ years" requirement from a 1-year candidate). Catch
        # it here too, before spending an actual application on it.
        mismatch = self._jd_experience_mismatch(job.description, profile.years_of_experience)
        if mismatch:
            return ApplicationResult(job=job, status="skipped", reason=mismatch)

        apply_btn = self.page.get_by_role("button", name=re.compile(r"^Apply now$", re.I))
        if apply_btn.count() == 0:
            return ApplicationResult(job=job, status="needs_review", reason="No Apply button found")
        apply_btn.first.click()
        self.page.wait_for_timeout(1500)

        form = self.page.locator("form").first
        if form.count() == 0:
            return ApplicationResult(job=job, status="needs_review", reason="No application form found")

        self._fill_entries(form, job, profile)
        self._human_field_pause()

        missing = self._unanswered_required_fields(form)
        if missing:
            return ApplicationResult(
                job=job, status="needs_review", reason=f"Missing required field(s): {'; '.join(missing)}"
            )

        submit_btn = form.get_by_role("button", name=re.compile(r"^Apply$", re.I))
        if submit_btn.count() == 0:
            submit_btn = form.locator("button[type='submit']")
        if submit_btn.count() == 0:
            return ApplicationResult(job=job, status="needs_review", reason="No submit button found")
        if submit_btn.first.is_disabled():
            return ApplicationResult(
                job=job, status="needs_review", reason="Submit button disabled -- a required field was likely missed"
            )

        if self.hitl:
            return self._queue_for_review(job)

        pre_submit_url = self.page.url
        self._human_field_pause()
        submit_btn.first.click()
        # Same bug class already fixed on Ashby/Workable/LinkedIn -- clicking
        # Apply is not proof Rippling accepted it, and this is a guest form
        # with no account to check "did I already apply" against later.
        # NOT yet verified against a guaranteed-successful real submission
        # (this project avoids deliberately submitting a real test
        # application) -- the three signals below are the same ones already
        # confirmed live on Ashby/Workable's own success pages.
        outcome = self._watch_submit_outcome(lambda: self._submission_confirmed(form, pre_submit_url))
        if outcome == "applied":
            return ApplicationResult(job=job, status="applied")
        if outcome == "captcha":
            return ApplicationResult(
                job=job,
                status="needs_review",
                reason="Submit blocked by CAPTCHA -- no success message (application not confirmed)",
            )
        return ApplicationResult(
            job=job,
            status="needs_review",
            reason="Clicked Apply but no success message appeared (no CAPTCHA detected either)",
        )

    def _field_wrappers(self, form):
        return form.locator("[data-testid='field']")

    def _field_label(self, wrapper) -> str:
        label = wrapper.locator("span[id$='-label']").first
        if label.count() > 0:
            return label.inner_text().strip()
        # A per-company custom screening question (e.g. sponsorship) has no
        # label span inside its own [data-testid="field"] wrapper at all --
        # confirmed live, the question text sits as a plain <p> in a sibling
        # div one level up. Only used as a fallback, and only when that
        # sibling isn't itself another field row (ordinary adjacent fields
        # would otherwise bleed into each other's "label").
        try:
            prev = wrapper.locator("xpath=preceding-sibling::*[1]")
            if prev.count() > 0 and (prev.first.get_attribute("data-testid") or "") != "field":
                text = prev.first.inner_text().strip()
                if text:
                    return text
        except Exception:
            pass
        return ""

    def _field_contains_handled(self, wrapper) -> bool:
        selector = ",".join(f"[data-testid='{t}']" for t in FIXED_FIELD_TESTIDS | SKIP_FIELD_TESTIDS)
        try:
            return wrapper.locator(selector).count() > 0
        except Exception:
            return False

    def _is_required(self, wrapper) -> bool:
        try:
            widget = wrapper.locator("input, textarea, [role='combobox']").first
            if widget.count() > 0 and (widget.get_attribute("aria-required") == "true" or widget.get_attribute("required") is not None):
                return True
        except Exception:
            pass
        return wrapper.locator("span.css-1av554z").count() > 0

    @staticmethod
    def _is_diversity_section(wrapper) -> bool:
        """Rippling's own U.S. EEO block (gender/race/ethnicity/veteran
        status), explicitly marked voluntary -- same policy as Ashby/
        Workable: these are self-identification questions, never inferred
        or answered from a resume."""
        try:
            section = wrapper.locator(
                "xpath=preceding::*[contains(., 'Equal Opportunity Employment')][1]"
            )
            return section.count() > 0
        except Exception:
            return False

    def _fill_entries(self, form, job: JobPosting, profile: Profile) -> None:
        defaults = self.config.get("screening_defaults", {})

        # Pass 1: fixed semantic fields, matched directly by their own
        # stable data-testid against the whole form -- see FIXED_FIELD_TESTIDS
        # for why this can't go through the per-wrapper walk below.
        first, last = _split_name(profile.full_name)
        phone_digits = re.sub(r"^\+\d{1,4}\s*", "", profile.phone or "")
        fixed_plan = {
            "input-resume": None,  # handled separately: file input, not fillable via .fill()
            "input-first_name": first,
            "input-last_name": last,
            "input-email": profile.email,
            "input-phone_number": phone_digits,
        }
        resume_input = form.locator("[data-testid='input-resume']").first
        if resume_input.count() > 0:
            self._human_field_pause()
            self._upload_resume(resume_input, profile)
        for testid, value in fixed_plan.items():
            if testid == "input-resume":
                continue
            el = form.locator(f"[data-testid='{testid}']").first
            if el.count() == 0:
                continue
            self._human_field_pause()
            self._fill_text(el, value)
        for testid in ("input-current_company", "input-linkedin_link"):
            el = form.locator(f"[data-testid='{testid}']").first
            if el.count() == 0:
                continue
            wrapper = el.locator("xpath=ancestor::*[@data-testid='field'][1]")
            question = self._field_label(wrapper) if wrapper.count() > 0 else ""
            self._human_field_pause()
            self._fill_known_or_freeform(el, question, job, profile, defaults)

        # Pass 2: everything else, walked wrapper by wrapper -- Location,
        # the diversity/EEO block (skipped, never answered), Pronouns
        # (skipped, optional self-identification), and any per-company
        # custom screening question.
        wrappers = self._field_wrappers(form)
        count = wrappers.count()
        diversity_from = None

        for i in range(count):
            wrapper = wrappers.nth(i)
            if diversity_from is not None and i >= diversity_from:
                continue
            if diversity_from is None and self._is_diversity_section(wrapper):
                diversity_from = i
                continue
            if self._field_contains_handled(wrapper):
                continue  # already filled in pass 1, or an optional/hidden field to leave alone

            if wrapper.locator("input[type='file']").count() > 0:
                continue  # an optional upload pass 1 didn't already claim

            widget = wrapper.locator("input, textarea, [role='combobox']").first
            if widget.count() == 0:
                continue
            question = self._field_label(wrapper)

            if "location" in question.lower():
                self._human_field_pause()
                self._fill_autocomplete(widget, profile.location or "")
            elif widget.get_attribute("role") == "combobox":
                if not question:
                    continue  # the bare phone country-code selector -- leave on its default
                if question.strip().lower() == "pronouns":
                    continue  # optional self-identification, same policy as diversity fields
                self._human_field_pause()
                self._fill_select_combobox(wrapper, widget, question, job, profile, defaults)
            elif widget.evaluate("el => el.tagName") in ("INPUT", "TEXTAREA"):
                # A per-company custom screening question rendered as a
                # plain text/textarea input (e.g. "Legal Name", "Preferred
                # name", a referral question) -- confirmed live (2026-11-01,
                # Beehive Industries board) this has NO other branch to
                # catch it: it isn't one of the fixed testids, isn't a
                # combobox, and isn't Location, so it silently went unfilled
                # and blocked a real submission. data-testid for these
                # starts with "customQuestions." (unlike the fixed semantic
                # fields' plain names), but matching by exclusion here is
                # simpler than enumerating that prefix.
                if not question:
                    continue
                self._human_field_pause()
                self._fill_known_or_freeform(widget, question, job, profile, defaults)

        self._fill_radio_groups(job, profile, defaults)

    def _fill_radio_groups(self, job: JobPosting, profile: Profile, defaults: dict) -> None:
        """Confirmed live (2026-11-01): the SMS-consent radio group (e.g.
        sms_opt_in) carries no visible asterisk and no aria-required
        anywhere in its DOM, yet the Apply button stays disabled until one
        option is picked -- the required-ness here is real but invisible to
        every signal _is_required checks. Verified empirically by leaving
        every other field filled and toggling only this group: Apply was
        disabled before, enabled after. Any consent/SMS/text-message
        question defaults to its decline option, same privacy-preserving
        policy already used for Workable's cookie banner; anything else
        required goes through answer_single_choice like Ashby/Workable's
        other radio groups."""
        groups = self.page.locator("[role='radiogroup']")
        for i in range(groups.count()):
            group = groups.nth(i)
            options = group.locator("[role='radio']")
            count = options.count()
            if count == 0:
                continue
            if any(options.nth(j).get_attribute("aria-checked") == "true" for j in range(count)):
                continue
            option_texts = [options.nth(j).inner_text().strip() for j in range(count)]
            group_text = " ".join(option_texts).lower()
            self._human_field_pause()
            if "consent" in group_text or "text message" in group_text or re.search(r"\bsms\b", group_text):
                decline = next((j for j, t in enumerate(option_texts) if re.match(r"^no\b", t.strip(), re.I)), None)
                options.nth(decline if decline is not None else count - 1).click()
                continue
            answer = self.tailor.answer_single_choice(profile, job, group_text[:120], option_texts, defaults)
            for j, text in enumerate(option_texts):
                if text.strip().lower() == answer.strip().lower():
                    options.nth(j).click()
                    break

    def _fill_known_or_freeform(self, widget, question: str, job: JobPosting, profile: Profile, defaults: dict) -> None:
        slot = self.tailor.classify_field(question)
        value = PROFILE_SLOTS[slot](profile) if slot != "freeform" else ""
        if slot == "freeform" or not value:
            value = self.tailor.answer_screening_question(profile, job, question, defaults)
        self._fill_text(widget, value)

    def _fill_text(self, widget, value: str) -> None:
        if (widget.input_value() or "").strip():
            return
        self._human_type(widget, value or "")

    def _upload_resume(self, file_input, profile: Profile) -> None:
        if profile.resume_pdf_path:
            file_input.set_input_files(profile.resume_pdf_path)
            self.page.wait_for_timeout(1500)

    def _fill_autocomplete(self, widget, value: str) -> None:
        if not value or (widget.input_value() or "").strip():
            return
        self._human_type(widget, value)
        value_words = [w.lower() for w in re.split(r"[,\s]+", value) if len(w) >= 3]
        options = self.page.locator("[role='option']")
        for _ in range(8):  # up to ~2.4s, same polling window as Ashby/Workable
            self.page.wait_for_timeout(300)
            for i in range(options.count()):
                opt_text = options.nth(i).inner_text().lower()
                if any(w in opt_text for w in value_words):
                    options.nth(i).click()
                    return
        widget.press("Escape")

    def _fill_select_combobox(self, wrapper, widget, question: str, job: JobPosting, profile: Profile, defaults: dict) -> None:
        """A fixed-option dropdown (e.g. a custom sponsorship/screening
        question) -- click to open its listbox and pick the option the
        model judges the best match, same pattern as Workable's
        select-style combobox."""
        widget.click()
        self.page.wait_for_timeout(400)
        options = self.page.locator("[role='option'], [role='listbox'] li")
        count = options.count()
        if count == 0:
            widget.press("Escape")
            return
        option_texts = [options.nth(i).inner_text().strip() for i in range(count)]
        answer = self.tailor.answer_single_choice(profile, job, question, option_texts, defaults)
        for i, text in enumerate(option_texts):
            if text.strip().lower() == answer.strip().lower():
                options.nth(i).click()
                return
        widget.press("Escape")

    def _unanswered_required_fields(self, form) -> list[str]:
        missing = []

        # Fixed fields -- checked directly by the same testids pass 1 fills,
        # since several of them (phone digits, resume) don't surface as
        # their own row in the wrapper walk at all (see FIXED_FIELD_TESTIDS).
        for testid, label in (
            ("input-first_name", "First name"),
            ("input-last_name", "Last name"),
            ("input-email", "Email"),
            ("input-current_company", "Current company"),
            ("input-linkedin_link", "LinkedIn Link"),
            ("input-phone_number", "Phone number"),
        ):
            el = form.locator(f"[data-testid='{testid}']").first
            if el.count() == 0:
                continue
            required = el.get_attribute("aria-required") == "true" or el.get_attribute("required") is not None
            if required and not (el.input_value() or "").strip():
                missing.append(label)
        # Resume is a file input -- its value can't be read back the normal
        # way; _upload_resume already handled it, so this just checks
        # whether it was actually required and we had nothing to attach.
        resume_input = form.locator("[data-testid='input-resume']").first
        if resume_input.count() > 0:
            required = resume_input.get_attribute("aria-required") == "true" or resume_input.get_attribute("required") is not None
            counter = form.locator("[data-testid='screen-reader-only']").filter(has_text=re.compile(r"^Total \d+ file selected$"))
            attached = False
            for i in range(counter.count()):
                if not counter.nth(i).inner_text().strip().startswith("Total 0"):
                    attached = True
                    break
            if required and not attached:
                missing.append("Résumé")

        # Everything else -- Location, and any per-company custom question.
        wrappers = self._field_wrappers(form)
        count = wrappers.count()
        diversity_from = None

        for i in range(count):
            wrapper = wrappers.nth(i)
            if diversity_from is not None and i >= diversity_from:
                continue
            if diversity_from is None and self._is_diversity_section(wrapper):
                diversity_from = i
                continue
            if self._field_contains_handled(wrapper):
                continue
            if not self._is_required(wrapper):
                continue
            question = self._field_label(wrapper) or "unlabeled required question"

            widget = wrapper.locator("input, textarea, [role='combobox']").first
            if widget.count() == 0:
                continue
            if widget.get_attribute("role") == "combobox" and widget.evaluate("el => el.tagName") == "DIV":
                # A custom-select combobox renders its chosen value as text
                # content, not a form value -- empty/placeholder text means
                # nothing was picked.
                text = (widget.inner_text() or "").strip()
                if not text or text.lower() in ("select", "select..."):
                    missing.append(question)
                continue
            val = widget.input_value()
            if not (val or "").strip():
                missing.append(question)

        # Radio groups (e.g. the SMS-consent group) -- _fill_radio_groups
        # already answers every one of these, so this is only a safety net
        # for a group it didn't recognize and so left untouched.
        groups = self.page.locator("[role='radiogroup']")
        for i in range(groups.count()):
            options = groups.nth(i).locator("[role='radio']")
            count = options.count()
            if count > 0 and not any(options.nth(j).get_attribute("aria-checked") == "true" for j in range(count)):
                missing.append("unanswered radio question")
        return missing

    def _submission_confirmed(self, form, pre_submit_url: str) -> bool:
        try:
            if self.page.url != pre_submit_url and not self._url_looks_like_challenge(self.page.url):
                return True
        except Exception:
            pass
        try:
            confirmation = self.page.get_by_text(
                re.compile(r"application (was |has been )?(submitted|received)|thank you for applying", re.I)
            )
            if confirmation.count() > 0:
                return True
        except Exception:
            pass
        try:
            return form.count() == 0 or not form.is_visible()
        except Exception:
            return False
