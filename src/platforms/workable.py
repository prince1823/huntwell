from __future__ import annotations

import re

from ..llm import PROFILE_SLOTS
from ..models import ApplicationResult, JobPosting, Profile
from .base import PlatformAdapter

# Workable is structurally the same shape as Ashby: no cross-site account,
# each company hosts its own career page + guest-apply form at
# apply.workable.com/<company-slug>, and there's no login at all. Unlike
# Ashby, Workable doesn't need a third-party aggregator for discovery --
# apply.workable.com/api/v1/widget/accounts/<slug>?details=true is
# Workable's OWN public, keyless JSON API (the same one that backs their
# "jobs widget" embed), confirmed live 2026-10-31 against a real company
# (Callsign) -- returns every open posting with title, department,
# location and a direct application_url, no scraping needed.
#
# Fields come in two flavors, both easy to tell apart from a form field's
# own `name` attribute: `firstname`/`lastname`/`email`/`phone` (Workable's
# fixed identifiers, like Ashby's _systemfield_*), and `CA_<id>` /
# `QA_<id>` for Workable's own built-in structured questions (salary,
# notice period) and this company's custom screening questions
# respectively -- confirmed live, both get the exact same generic
# classify-then-fill treatment since the prefix alone doesn't tell you the
# widget type (CA_ fields showed up as both a plain text input and a
# select-style dropdown in testing).
WIDGET_API = "https://apply.workable.com/api/v1/widget/accounts/{company}?details=true"


def _split_name(full_name: str) -> tuple[str, str]:
    parts = (full_name or "").strip().split(None, 1)
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


class WorkableAdapter(PlatformAdapter):
    name = "workable"
    requires_login = False

    def ensure_logged_in(self, email: str, password: str, headless: bool = False) -> None:
        pass  # guest application form, no account needed

    def search_jobs(self, profile: Profile) -> list[JobPosting]:
        postings: list[JobPosting] = []
        seen_ids: set[str] = set()
        companies = self.config.get("workable_companies", [])
        titles = [t.lower() for t in self.config.get("titles", [])]

        for company in companies:
            try:
                resp = self.page.request.get(
                    WIDGET_API.format(company=company), timeout=15000
                )
                if not resp.ok:
                    continue
                payload = resp.json()
            except Exception:
                continue

            for item in payload.get("jobs", []):
                title = item.get("title", "")
                if titles and not any(t in title.lower() for t in titles):
                    continue
                shortcode = item.get("shortcode") or ""
                key = f"{company}:{shortcode}"
                if not shortcode or key in seen_ids:
                    continue
                seen_ids.add(key)

                apply_url = item.get("application_url") or f"https://apply.workable.com/{company}/j/{shortcode}/apply/"
                city = item.get("city") or ""
                country = item.get("country") or ""
                location = ", ".join(p for p in (city, country) if p)

                postings.append(
                    JobPosting(
                        platform=self.name,
                        job_id=key,
                        title=title,
                        company=item.get("name") or company,
                        location=location,
                        url=apply_url,
                    )
                )
        return postings

    def apply(self, job: JobPosting, profile: Profile) -> ApplicationResult:
        self.page.goto(job.url, wait_until="domcontentloaded")
        self.page.wait_for_timeout(2000)

        form = self.page.locator("form").first
        if form.count() == 0:
            return ApplicationResult(job=job, status="needs_review", reason="No application form found")

        try:
            job.description = self.page.locator("body").inner_text()[:4000]
        except Exception:
            pass

        self._dismiss_cookie_banner()
        self._fill_fixed_fields(profile)
        self._fill_labeled_fields(job, profile)
        self._fill_radio_groups(job, profile)
        self._fill_checkbox_groups(job, profile)

        missing = self._unanswered_required_fields()
        if missing:
            return ApplicationResult(
                job=job, status="needs_review", reason=f"Missing required field(s): {'; '.join(missing)}"
            )

        submit_btn = self.page.get_by_role("button", name=re.compile("Submit application", re.I))
        if submit_btn.count() == 0:
            return ApplicationResult(job=job, status="needs_review", reason="No submit button found")
        if submit_btn.first.is_disabled():
            return ApplicationResult(
                job=job, status="needs_review", reason="Submit button disabled -- a required field was likely missed"
            )

        submit_btn.first.click()
        self.page.wait_for_timeout(2500)
        if self._submission_confirmed(form):
            return ApplicationResult(job=job, status="applied")
        # Same bug class already fixed on LinkedIn/Ashby -- clicking Submit
        # is not proof Workable accepted the application (and this form
        # also has no account to check "did I already apply" against
        # later). Fail safe to needs_review instead of guessing. NOT yet
        # verified against a guaranteed-successful real submission.
        return ApplicationResult(
            job=job,
            status="needs_review",
            reason="Clicked Submit but could not confirm Workable accepted the application",
        )

    def _dismiss_cookie_banner(self) -> None:
        """Every Workable-hosted career page shows its own cookie-consent
        dialog on first load -- confirmed live (2026-10-31) it sits on top
        of the whole page and blocks every click on the real form fields
        underneath until dismissed. Declines non-essential cookies rather
        than accepting, per this project's own privacy-preserving default."""
        try:
            decline_btn = self.page.get_by_role("button", name=re.compile("Decline all", re.I))
            if decline_btn.count() > 0 and decline_btn.first.is_visible():
                decline_btn.first.click()
                self.page.wait_for_timeout(300)
        except Exception:
            pass

    def _fill_fixed_fields(self, profile: Profile) -> None:
        first, last = _split_name(profile.full_name)
        self._fill_text_by_name("firstname", first)
        self._fill_text_by_name("lastname", last)
        self._fill_text_by_name("email", profile.email)

        # The phone input (intl-tel-input) auto-detects a default country
        # from the visitor's own IP and expects just the national number
        # typed into it, with country picked separately via its own flag
        # dropdown -- not swapped per-candidate here, a known limitation
        # for a candidate whose profile country doesn't match wherever
        # this machine's traffic looks like it's from. Strip a leading
        # "+<country code>" from the profile value rather than typing it
        # as part of the number.
        phone = re.sub(r"^\+\d{1,4}\s*", "", profile.phone or "")
        self._fill_text_by_name("phone", phone)

        file_input = self.page.locator("input[type='file']").first
        if file_input.count() > 0 and profile.resume_pdf_path:
            file_input.set_input_files(profile.resume_pdf_path)
            self.page.wait_for_timeout(1500)

    def _fill_text_by_name(self, name: str, value: str) -> None:
        field = self.page.locator(f"input[name='{name}']").first
        if field.count() == 0 or (field.input_value() or "").strip():
            return
        field.fill(value or "")

    def _fill_labeled_fields(self, job: JobPosting, profile: Profile) -> None:
        """Every plain text/email/textarea/combobox field on a Workable form
        is wrapped by its own <label> containing both the question text and
        the input -- confirmed live across multiple companies' forms. The
        fixed name/email/phone fields are already handled by
        _fill_fixed_fields, so this skips anything with one of those
        names (plus the resume file input, which has no name at all)."""
        labels = self.page.locator("form label")
        for i in range(labels.count()):
            label = labels.nth(i)
            question = label.inner_text().strip().lstrip("*").strip()
            if not question:
                continue

            text_input = label.locator("input[type='text'], input[type='email']")
            textarea = label.locator("textarea")
            combobox = label.locator("[role='combobox']")

            if text_input.count() > 0:
                input_el = text_input.first
                name = input_el.get_attribute("name") or ""
                if name in ("firstname", "lastname", "email", "phone"):
                    continue
                if (input_el.input_value() or "").strip():
                    continue
                slot = self.tailor.classify_field(question)
                value = PROFILE_SLOTS[slot](profile) if slot != "freeform" else ""
                if not value:
                    value = self.tailor.answer_screening_question(
                        profile, job, question, self.config.get("screening_defaults", {})
                    )
                input_el.fill(value)
            elif combobox.count() > 0:
                self._fill_combobox(combobox.first, question, job, profile)
            elif textarea.count() > 0:
                ta = textarea.first
                if (ta.input_value() or "").strip():
                    continue
                if "excite" in question.lower() or "why" in question.lower() or "interest" in question.lower():
                    ta.fill(self.tailor.company_interest_answer(profile, job))
                else:
                    answer = self.tailor.answer_screening_question(
                        profile, job, question, self.config.get("screening_defaults", {})
                    )
                    ta.fill(answer)

    def _fill_combobox(self, combo, question: str, job: JobPosting, profile: Profile) -> None:
        if (combo.input_value() or "").strip():
            return
        is_select_style = combo.get_attribute("readonly") == "true"

        if is_select_style:
            # A fixed-option dropdown (e.g. "Notice period") -- click to
            # open its listbox and pick the option whose text the model
            # judges the best match, same pattern as a native <select>.
            combo.click()
            self.page.wait_for_timeout(500)
            listbox_id = combo.get_attribute("aria-owns") or ""
            if not listbox_id:
                combo.press("Escape")
                return
            options = self.page.locator(f"#{listbox_id} [role='option']")
            count = options.count()
            if count == 0:
                combo.press("Escape")
                return
            option_texts = [options.nth(i).inner_text().strip() for i in range(count)]
            answer = self.tailor.answer_single_choice(
                profile, job, question, option_texts, self.config.get("screening_defaults", {})
            )
            for i, text in enumerate(option_texts):
                if text.strip().lower() == answer.strip().lower():
                    options.nth(i).click()
                    return
            combo.press("Escape")
            return

        # A genuine free-text autocomplete (e.g. a location typeahead) --
        # classify against real profile facts first, same as Ashby's
        # equivalent, since picking the right real value matters more than
        # generated text an autocomplete suggestion won't match.
        slot = self.tailor.classify_field(question)
        value = PROFILE_SLOTS[slot](profile) if slot != "freeform" else ""
        if not value:
            value = self.tailor.answer_screening_question(
                profile, job, question, self.config.get("screening_defaults", {})
            )
        combo.fill(value)
        value_words = [w.lower() for w in re.split(r"[,\s]+", value) if len(w) >= 3]
        options = self.page.locator("[role='option']")
        for _ in range(8):
            self.page.wait_for_timeout(300)
            for i in range(options.count()):
                opt_text = options.nth(i).inner_text().lower()
                if any(w in opt_text for w in value_words):
                    options.nth(i).click()
                    return
        combo.press("Escape")

    def _click_option_input(self, input_el) -> None:
        """Workable's own radio/checkbox inputs render with
        aria-hidden="true" and a custom styled control drawn next to them
        -- confirmed live (2026-10-31): calling .check() directly on the
        real <input> raises "Clicking the checkbox did not change its
        state", because the actual click target the page's JS listens to
        is the wrapping <label>, not the hidden input itself. Click the
        label instead; fall back to a forced check if no label wraps it.

        The first click sometimes silently doesn't register right after
        other fields were just filled -- confirmed live: a second attempt
        immediately after succeeded with nothing else different, a
        transient timing issue (likely the radio group's own React event
        listener isn't attached yet right after a recent re-render) rather
        than a structural one. Retry a few times rather than trusting the
        first click."""
        label = input_el.locator("xpath=ancestor::label[1]")
        target = label.first if label.count() > 0 else None
        for _ in range(3):
            if target is not None:
                target.click()
            else:
                input_el.check(force=True)
            self.page.wait_for_timeout(400)
            if input_el.is_checked():
                return

    def _fill_radio_groups(self, job: JobPosting, profile: Profile) -> None:
        fieldsets = self.page.locator("form fieldset[role='radiogroup']")
        for i in range(fieldsets.count()):
            fieldset = fieldsets.nth(i)
            radios = fieldset.locator("input[type='radio']")
            count = radios.count()
            if count == 0 or any(radios.nth(j).is_checked() for j in range(count)):
                continue

            question = self._fieldset_question(self._fieldset_question_block(fieldset))
            option_texts = []
            for j in range(count):
                opt_label = radios.nth(j).locator("xpath=ancestor::label[1]")
                option_texts.append(opt_label.first.inner_text().strip() if opt_label.count() > 0 else "")
            if not any(option_texts):
                continue

            answer = self.tailor.answer_single_choice(
                profile, job, question, [t for t in option_texts if t], self.config.get("screening_defaults", {})
            )
            for j, text in enumerate(option_texts):
                if text.strip().lower() == answer.strip().lower():
                    self._click_option_input(radios.nth(j))
                    break

    def _fill_checkbox_groups(self, job: JobPosting, profile: Profile) -> None:
        # Checkbox-based questions didn't turn up on any sampled Workable
        # form (checked several real companies 2026-10-31) -- consent is
        # typically a privacy-policy link rather than a checkbox, and no
        # multi-select checkbox question was found either. role='group' is
        # the standard ARIA pairing for fieldset[role='radiogroup']'s
        # checkbox equivalent, kept as a best-effort for whichever
        # company's form does use one, but unverified against a real one.
        fieldsets = self.page.locator("form fieldset[role='group']")
        for i in range(fieldsets.count()):
            fieldset = fieldsets.nth(i)
            checkboxes = fieldset.locator("input[type='checkbox']")
            count = checkboxes.count()
            if count == 0:
                continue

            question = self._fieldset_question(self._fieldset_question_block(fieldset))
            if count == 1:
                cb = checkboxes.first
                if cb.is_checked():
                    continue
                if re.search(r"agree|consent|certify|acknowledg|confirm|attest", question, re.I):
                    self._click_option_input(cb)
                continue

            option_texts = []
            for j in range(count):
                opt_label = checkboxes.nth(j).locator("xpath=ancestor::label[1]")
                option_texts.append(opt_label.first.inner_text().strip() if opt_label.count() > 0 else "")
            if not any(option_texts):
                continue
            chosen = set(
                self.tailor.answer_multi_select(
                    profile, job, question, [t for t in option_texts if t], self.config.get("screening_defaults", {})
                )
            )
            for j, text in enumerate(option_texts):
                if text and text in chosen and not checkboxes.nth(j).is_checked():
                    self._click_option_input(checkboxes.nth(j))

    def _fieldset_question_block(self, fieldset) -> str:
        """Raw text of the question wrapper around a radio/checkbox
        fieldset's aria-labelledby label, including a leading "*" when
        required. Confirmed live: the "*" is NOT inside the
        aria-labelledby span itself, or on the fieldset's own immediate
        parent -- it's a sibling two ancestor levels above the label
        span, alongside the question text but before the option labels."""
        label_id = fieldset.get_attribute("aria-labelledby") or ""
        if not label_id:
            return fieldset.inner_text().strip()[:200]
        label_el = self.page.locator(f"#{label_id}")
        if label_el.count() == 0:
            return ""
        wrapper = label_el.locator("xpath=ancestor::*[2]")
        return wrapper.first.inner_text().strip() if wrapper.count() > 0 else label_el.first.inner_text().strip()

    @staticmethod
    def _fieldset_question(block: str) -> str:
        return block.lstrip("*").strip()

    def _unanswered_required_fields(self) -> list[str]:
        missing = []
        labels = self.page.locator("form label")
        for i in range(labels.count()):
            label = labels.nth(i)
            raw_text = label.inner_text().strip()
            if not raw_text.startswith("*"):
                continue
            question = raw_text.lstrip("*").strip()
            if not question:
                continue

            text_input = label.locator("input[type='text'], input[type='email']")
            textarea = label.locator("textarea")
            combobox = label.locator("[role='combobox']")
            if text_input.count() > 0:
                if not (text_input.first.input_value() or "").strip():
                    missing.append(question)
            elif combobox.count() > 0:
                if not (combobox.first.input_value() or "").strip():
                    missing.append(question)
            elif textarea.count() > 0:
                if not (textarea.first.input_value() or "").strip():
                    missing.append(question)

        fieldsets = self.page.locator("form fieldset[role='radiogroup'], form fieldset[role='group']")
        for i in range(fieldsets.count()):
            fieldset = fieldsets.nth(i)
            block = self._fieldset_question_block(fieldset)
            if not block.startswith("*"):
                continue
            boxes = fieldset.locator("input[type='radio'], input[type='checkbox']")
            if boxes.count() > 0 and not any(boxes.nth(j).is_checked() for j in range(boxes.count())):
                missing.append(self._fieldset_question(block) or "unlabeled required question")

        return missing

    def _submission_confirmed(self, form) -> bool:
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
