from __future__ import annotations

import re

import click
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ..models import ApplicationResult, JobPosting, Profile
from .base import PlatformAdapter

# NOTE: Wellfound (formerly AngelList Talent) is a React SPA with no
# query-param-driven search (unlike LinkedIn/Indeed) -- the role filter is a
# multi-select "chip" picker with autocomplete, driven entirely by client
# state, so search_jobs drives the actual UI instead of building a URL.
# Verified against a live account on 2026-10-29.
#
# Login is intentionally NOT automated -- same reasoning as the LinkedIn
# adapter: typing credentials into a scripted browser risks tripping bot
# detection, and the Wellfound login form also offers "Continue with
# Google", which an automated script has no reasonable way to complete
# anyway. We open a real, visible browser window and wait for a human to
# log in by hand; the session is then saved to auth_state/wellfound.json.

LOGIN_WAIT_TIMEOUT_MS = 180_000  # 3 minutes to complete login by hand
JOB_ID_RE = re.compile(r"^/jobs/(\d+)-")


class WellfoundAdapter(PlatformAdapter):
    name = "wellfound"

    def ensure_logged_in(self, email: str, password: str) -> None:
        self.page.goto("https://wellfound.com/jobs", wait_until="domcontentloaded")
        if self.page.locator("a[href*='/logout']").count() > 0:
            return

        self.page.goto("https://wellfound.com/login", wait_until="domcontentloaded")
        click.echo(
            "[wellfound] No valid session found. A browser window is open -- "
            "please log in by hand (email/password or Google), then this "
            "script will continue automatically once it sees you're logged in."
        )
        try:
            self.page.wait_for_function(
                "() => !window.location.pathname.includes('login')",
                timeout=LOGIN_WAIT_TIMEOUT_MS,
            )
            self.page.wait_for_timeout(1500)
        except PlaywrightTimeoutError:
            raise RuntimeError(
                "Timed out waiting for manual Wellfound login. Re-run with "
                "--no-headless and log in within 3 minutes of the window opening."
            )

    def search_jobs(self, profile: Profile) -> list[JobPosting]:
        postings: list[JobPosting] = []
        seen_ids: set[str] = set()
        titles = self.config.get("titles", [])

        self.page.goto("https://wellfound.com/jobs", wait_until="domcontentloaded")
        self.page.wait_for_timeout(2500)

        # The role field is a react-select multi-value chip picker. Two bugs
        # used to live here, both confirmed live against the real account:
        #  1. Chips are additive and persist on the account across every
        #     past run -- without clearing them first, each new search was
        #     silently OR'd with every title ever searched before (so an
        #     "HR Coordinator" search also matched "Software Engineer" etc.
        #     from earlier testing). Clear every existing chip via
        #     react-select's own .select__multi-value__remove buttons before
        #     typing anything new.
        #  2. A fixed-timeout type->Enter loop raced react-select's own
        #     commit: if Enter hadn't finished creating a chip before the
        #     next title started typing, the two titles got concatenated
        #     into a single malformed chip (e.g. "AI EngineerBackend
        #     Engineer"). Fixed by polling the chip count to confirm each
        #     title actually committed as its own chip before moving on.
        role_field = self.page.locator("[data-test='SearchBar-RoleSelect-FocusButton']")
        if role_field.count() > 0:
            role_field.first.click()
            self.page.wait_for_timeout(400)

            remove_btns = self.page.locator(".select__multi-value__remove")
            for _ in range(60):
                if remove_btns.count() == 0:
                    break
                remove_btns.first.click()
                self.page.wait_for_timeout(150)

            chip_input = self.page.locator(".select__input input")
            chips = self.page.locator(".select__multi-value")
            for title in titles:
                before = chips.count()
                self.page.keyboard.type(title)
                self.page.keyboard.press("Enter")
                committed = False
                for _ in range(10):
                    self.page.wait_for_timeout(300)
                    if chips.count() > before:
                        committed = True
                        break
                if not committed and chip_input.count() > 0:
                    # Enter never created a chip (no matching/free-text commit) --
                    # clear the leftover text so it can't bleed into the next title.
                    chip_input.first.fill("")
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(2000)

        self._set_experience_filter(max(profile.years_of_experience + 1, 1))

        # The company/job list is virtualized like LinkedIn's -- poll
        # briefly rather than trusting a single count() read right after
        # the search updates.
        results = self.page.locator("[data-test='StartupResult']")
        for _ in range(6):
            if results.count() > 0:
                break
            self.page.wait_for_timeout(500)

        count = min(results.count(), 25)
        for i in range(count):
            card = results.nth(i)
            try:
                company_text = card.locator("a[href^='/company/']").first.inner_text()
                company = company_text.split("\n")[0].strip()
            except Exception:
                company = ""

            job_links = card.locator("a[href^='/jobs/']")
            for j in range(job_links.count()):
                link = job_links.nth(j)
                href = link.get_attribute("href") or ""
                id_match = JOB_ID_RE.match(href)
                if not id_match:
                    continue
                job_id = id_match.group(1)
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)

                try:
                    job_title = link.locator(".styles_title__xpQDw").first.inner_text().strip()
                except Exception:
                    job_title = link.inner_text().split("\n")[0].strip()
                try:
                    job_location = " / ".join(
                        t.strip()
                        for t in link.locator(".styles_location__O9Z62").all_inner_texts()
                        if t.strip()
                    )
                except Exception:
                    job_location = ""

                if len(postings) >= 25:
                    break
                postings.append(
                    JobPosting(
                        platform=self.name,
                        job_id=job_id,
                        title=job_title,
                        company=company,
                        location=job_location,
                        url=f"https://wellfound.com{href}",
                    )
                )
        return postings

    def _set_experience_filter(self, max_years: int) -> None:
        """Wellfound DOES have a native "Required experience" filter --
        it's just a custom drag-slider (rheostat component, not a native
        <input type=range>) buried inside the filter panel, invisible until
        you scroll the panel itself into that section. Sets the range to
        0..max_years and turns on "include jobs with no experience listed"
        (off by default, which otherwise hides most postings that simply
        don't state a range -- leaving it off took results from 228 to 0
        in testing)."""
        filter_btn = self.page.locator("[data-test='SearchBar-ToggleFilterControlPanelButton']")
        if filter_btn.count() == 0:
            return
        filter_btn.first.click()
        self.page.wait_for_timeout(800)

        sliders = self.page.locator("[role='slider']")
        exp_handles = [
            sliders.nth(i) for i in range(sliders.count()) if sliders.nth(i).get_attribute("aria-valuemax") == "10"
        ]
        if len(exp_handles) != 2:
            self.page.keyboard.press("Escape")
            return

        max_handle = exp_handles[1]
        max_handle.scroll_into_view_if_needed()
        self.page.wait_for_timeout(300)
        max_box = max_handle.bounding_box()
        track = max_handle.locator("xpath=ancestor::*[contains(@class,'rheostat')][1]")
        box = track.bounding_box()
        if not max_box or not box:
            self.page.keyboard.press("Escape")
            return

        slider_max = int(max_handle.get_attribute("aria-valuemax") or 10)
        target_fraction = min(max_years, slider_max) / slider_max
        target_x = box["x"] + box["width"] * target_fraction
        start_x = max_box["x"] + max_box["width"] / 2
        y = max_box["y"] + max_box["height"] / 2

        self.page.mouse.move(start_x, y)
        self.page.mouse.down()
        self.page.mouse.move((start_x + target_x) / 2, y, steps=8)
        self.page.mouse.move(target_x, y, steps=8)
        self.page.mouse.up()
        self.page.wait_for_timeout(500)

        toggle_label = self.page.locator("label[for='includeJobsWithoutExperience']")
        if toggle_label.count() > 0:
            toggle_label.first.click()
            self.page.wait_for_timeout(500)

        view_btn = self.page.locator("[data-test='SearchBar-ViewResultsButton']")
        if view_btn.count() > 0:
            view_btn.first.click()
            self.page.wait_for_timeout(1500)

    def apply(self, job: JobPosting, profile: Profile) -> ApplicationResult:
        self.page.goto(job.url, wait_until="domcontentloaded")
        self.page.wait_for_timeout(2000)

        try:
            job.description = self._extract_description()
        except Exception:
            pass

        apply_btn = self.page.get_by_role("button", name=re.compile(r"^Apply$", re.I))
        if apply_btn.count() == 0:
            return ApplicationResult(
                job=job, status="skipped", reason="No native Apply button (likely applies on company site)"
            )

        apply_btn.first.click()
        self.page.wait_for_timeout(1500)

        # The page can have more than one <form> (e.g. an empty/hidden one
        # elsewhere), so scope to the form that actually contains the Send
        # button rather than assuming it's the first <form> on the page.
        send_btn = self.page.get_by_role("button", name=re.compile("Send application", re.I))
        if send_btn.count() == 0:
            return ApplicationResult(job=job, status="needs_review", reason="No Send application button found")

        form = send_btn.first.locator("xpath=ancestor::form[1]")
        if form.count() == 0:
            return ApplicationResult(job=job, status="needs_review", reason="No application form found after Apply")

        if send_btn.first.is_disabled():
            # The Send button starts disabled when the whole form is
            # ineligible (e.g. a location/timezone/visa constraint) -- in
            # that case the fields themselves are disabled too and there is
            # nothing to fill or fix, so bail out before spending any LLM
            # tokens on answers that can never be submitted.
            reason = self._disabled_reason(form) or "Send application button stayed disabled"
            return ApplicationResult(job=job, status="skipped", reason=reason)

        self._fill_visible_fields(form, job, profile)

        if send_btn.first.is_disabled():
            reason = self._disabled_reason(form) or "Send application button became disabled after filling"
            return ApplicationResult(job=job, status="skipped", reason=reason)

        send_btn.first.click()
        self.page.wait_for_timeout(1500)
        return ApplicationResult(job=job, status="applied")

    def _extract_description(self) -> str:
        """The full page text is noisy (nav links, 'People also viewed'
        footer) and -- worse -- straight truncation was cutting off the
        "ABOUT <Company>" panel entirely on some pages (it renders after the
        job description in DOM order), which is exactly the section that
        makes a "what interests you about this company" answer specific
        rather than generic. Stop before the next, irrelevant section
        instead of truncating at a fixed length."""
        text = self.page.locator("body").inner_text()
        for stop_marker in ("\nJOBS AT ", "\nPEOPLE ALSO VIEWED"):
            idx = text.find(stop_marker)
            if idx != -1:
                text = text[:idx]
        return text[:6000]

    def _fill_visible_fields(self, form, job: JobPosting, profile: Profile) -> None:
        defaults = self.config.get("screening_defaults", {})

        for textarea in form.locator("textarea").all():
            if not textarea.is_enabled() or (textarea.input_value() or "").strip():
                continue
            question, required = self._field_info(textarea)
            if not required:
                # Optional field -- leave it blank rather than padding the
                # application with a question nobody asked us to answer.
                continue
            if "cover" in question.lower() or "interest" in question.lower() or "why" in question.lower():
                textarea.fill(self.tailor.company_interest_answer(profile, job))
            else:
                answer = self.tailor.answer_screening_question(profile, job, question, defaults)
                textarea.fill(answer)

        for input_el in form.locator("input[type='text'], input[type='number']").all():
            if not input_el.is_enabled() or (input_el.input_value() or "").strip():
                continue
            question, required = self._field_info(input_el)
            if not question or not required:
                continue
            answer = self.tailor.answer_screening_question(profile, job, question, defaults)
            input_el.fill(answer)

        for select_el in form.locator("select").all():
            current = (select_el.input_value() or "").strip()
            if not select_el.is_enabled() or current:
                continue
            question, required = self._field_info(select_el)
            if not required:
                continue
            options = [
                o.strip()
                for o in select_el.locator("option").all_inner_texts()
                if o.strip() and o.strip().lower() not in ("select an option", "select...")
            ]
            if not options:
                continue
            answer = self.tailor.answer_screening_question(profile, job, question, defaults)
            idx = self._closest_match_index(answer, options)
            select_el.select_option(label=options[idx if idx is not None else 0])

        self._fill_radio_groups(form, job, profile, defaults)
        self._fill_checkbox_groups(form, job, profile, defaults)

    def _fill_radio_groups(self, form, job: JobPosting, profile: Profile, defaults: dict) -> None:
        # Radio options are grouped by a shared `name` attribute, and each
        # option has its own real label[for=id] -- e.g. "Are you a
        # B.E./B.Tech graduate? *" with Yes/No options.
        radios = form.locator("input[type='radio']")
        seen_names: set[str] = set()
        for i in range(radios.count()):
            radio = radios.nth(i)
            name = radio.get_attribute("name") or ""
            if not name or name in seen_names:
                continue
            seen_names.add(name)

            group = form.locator(f"input[type='radio'][name='{name}']")
            if any(group.nth(j).is_checked() for j in range(group.count())):
                continue

            question, required = self._field_info(radio)
            if not required:
                continue

            option_ids = [group.nth(j).get_attribute("id") for j in range(group.count())]
            option_texts = [self._label_text_for(form, oid) for oid in option_ids]
            answer = self.tailor.answer_single_choice(profile, job, question, option_texts, defaults)
            idx = self._closest_match_index(answer, option_texts)
            if idx is None:
                continue  # couldn't confidently match an answer -- leave unanswered rather than guess
            target_id = option_ids[idx]
            if target_id:
                form.locator(f"label[for='{target_id}']").click()

    def _fill_checkbox_groups(self, form, job: JobPosting, profile: Profile, defaults: dict) -> None:
        # Unlike radios, these checkboxes share no `name` attribute -- a
        # question like "Which of these tools are you familiar with?" is a
        # multi-select, grouped only by sharing the same wrapping
        # label.block.mb-6. Skip any group a single-checkbox "I agree to
        # the policy" consent would also land in; we still answer those
        # truthfully (never fabricate agreement) via the multi-select path,
        # since the resume-grounded prompt only selects options actually
        # evidenced for skill checklists and leaves pure consent checkboxes
        # alone if nothing in the profile supports checking them.
        groups = form.locator("label.block.mb-6")
        for i in range(groups.count()):
            group = groups.nth(i)
            checkboxes = group.locator("input[type='checkbox']")
            count = checkboxes.count()
            if count == 0:
                continue
            if any(checkboxes.nth(j).is_checked() for j in range(count)):
                continue

            q_div = group.locator("div.mb-1").first
            text = q_div.inner_text() if q_div.count() > 0 else group.inner_text()
            required = "*" in text
            if not required:
                continue
            question = text.replace("*", "").strip()

            option_ids = [checkboxes.nth(j).get_attribute("id") for j in range(count)]
            option_texts = [self._label_text_for(form, oid) for oid in option_ids]
            selected = self.tailor.answer_multi_select(profile, job, question, option_texts, defaults)
            for opt_text, opt_id in zip(option_texts, option_ids):
                if opt_text in selected and opt_id:
                    form.locator(f"label[for='{opt_id}']").click()

    @staticmethod
    def _label_text_for(form, element_id: str | None) -> str:
        if not element_id:
            return ""
        try:
            label = form.locator(f"label[for='{element_id}']")
            return label.first.inner_text().strip() if label.count() > 0 else ""
        except Exception:
            return ""

    @staticmethod
    def _field_info(element) -> tuple[str, bool]:
        """Every Wellfound custom-question field shares the same wrapper:
        label.block.mb-6 > div.mb-1 > div(question text + optional "*").
        There is no native HTML `required` attribute on any of these
        inputs -- required-ness is purely the visual asterisk, enforced by
        JS validation that disables Send rather than browser validation."""
        try:
            label = element.locator("xpath=ancestor::label[1]")
            if label.count() == 0:
                return "", False
            q_div = label.locator("div.mb-1").first
            text = q_div.inner_text() if q_div.count() > 0 else label.inner_text()
            return text.replace("*", "").strip(), "*" in text
        except Exception:
            return "", False

    @staticmethod
    def _closest_match_index(answer: str, options: list[str]) -> int | None:
        answer_lower = answer.strip().lower()
        for i, opt in enumerate(options):
            opt_lower = opt.strip().lower()
            if opt_lower and (opt_lower in answer_lower or answer_lower in opt_lower):
                return i
        return None

    @staticmethod
    def _disabled_reason(form) -> str:
        try:
            text = form.inner_text()
        except Exception:
            return ""
        m = re.search(r"(not accepting applications[^\n]*)", text, re.I)
        return m.group(1).strip() if m else ""
