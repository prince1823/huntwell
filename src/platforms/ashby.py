from __future__ import annotations

import re

from ..llm import PROFILE_SLOTS
from ..models import ApplicationResult, JobPosting, Profile
from .base import PlatformAdapter

# Ashby is structurally different from LinkedIn/Wellfound/Indeed: it's not a
# job board with its own account -- each company hosts its own career page
# and application form at jobs.ashbyhq.com/<company-slug>, and applying is a
# guest form, no login or account needed at all. That sidesteps the
# bot-detection problems we hit on the other platforms entirely (verified
# live on 2026-10-29: loads cleanly headless, no Cloudflare challenge).
#
# Ashby itself has no cross-company search -- not even Ashby's own site
# offers "search all companies using Ashby" (confirmed: neither Ashby nor
# DuckDuckGo/Google index a global directory). So search_jobs discovers
# postings two ways:
#   1. (primary) a free, public, keyless aggregator API --
#      jobs.whatrunsindia.com/api/jobs -- built on Common-Crawl-based
#      discovery of Ashby company boards, verified live 2026-10-29 (returns
#      real, current postings with working apply URLs). This is a
#      community-run third-party service, not something Ashby or we
#      operate, so failures there degrade silently to source #2 rather than
#      breaking the run.
#   2. (fallback/supplement) any company slugs pinned explicitly in
#      config["ashby_companies"], scraped directly off their own career page
#      -- useful for a company the aggregator hasn't indexed yet.
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
AGGREGATOR_API = "https://jobs.whatrunsindia.com/api/jobs"

MONTH_NAME_TO_NUM = {
    "january": "1", "february": "2", "march": "3", "april": "4", "may": "5", "june": "6",
    "july": "7", "august": "8", "september": "9", "october": "10", "november": "11", "december": "12",
}


def _parse_month_year(date_str: str) -> tuple[str | None, str | None]:
    """Parse a free-text date like "May 2025" into (month_num, year) for
    Ashby's Month/Year select dropdowns. Returns (None, None) if it can't
    be confidently parsed -- leaving the field blank is safer than
    guessing a month that isn't actually supported by the resume."""
    if not date_str:
        return None, None
    m = re.search(r"([A-Za-z]+)\s+(\d{4})", date_str)
    if m:
        month_num = MONTH_NAME_TO_NUM.get(m.group(1).lower())
        if month_num:
            return month_num, m.group(2)
    y = re.search(r"\b(19|20)\d{2}\b", date_str)
    if y:
        return None, y.group(0)
    return None, None


# The aggregator's own known tag vocabulary (confirmed from source:
# src/config/rules.json "tags" list) -- only these exact values are
# meaningful to its match filter, so resume skills get matched against
# this list rather than sent as-is.
KNOWN_TAGS = [
    "golang", "rust", "java", "kotlin", "scala", "ruby", "rails", "php", "elixir",
    "next.js", "vue", "angular", "svelte", "django", "fastapi", "spring boot", "graphql",
    "aws", "gcp", "azure", "kubernetes", "terraform", "docker",
    "postgres", "kafka", "spark", "snowflake", "airflow",
    "pytorch", "tensorflow", "llm", "solidity",
]


def _skill_match_param(skills: list[str]) -> str:
    """Match the resume's own skills against the aggregator's known tag
    vocabulary (case-insensitive) so the search narrows to postings
    mentioning the candidate's actual tech stack, not just title text.
    Uses the "match" query param, not "tags" -- confirmed live those are
    NOT equivalent: "tags" requires every listed tag to co-occur on the
    same posting (AND), which collapses to ~0 results once combined with
    location/exp/minScore; "match" is the loose/OR-style equivalent (e.g.
    4 skills under "tags" gave 1 result combined with other filters,
    the same 4 under "match" gave 20)."""
    skills_lower = {s.lower() for s in skills}
    matched = [tag for tag in KNOWN_TAGS if tag in skills_lower]
    return ",".join(matched)


def _experience_param(years: int) -> str | None:
    """Map resume years_of_experience to the aggregator's own exp filter
    (confirmed from source: "1"=jobs wanting <=1yr, "2"=<=3yr, "5"=<=6yr --
    it has no bucket for more senior profiles, so those get no filter here
    and rely on keywords_exclude/fit-scoring instead)."""
    if years <= 1:
        return "1"
    if years <= 3:
        return "2"
    if years <= 6:
        return "5"
    return None


class AshbyAdapter(PlatformAdapter):
    name = "ashby"
    requires_login = False

    def ensure_logged_in(self, email: str, password: str, headless: bool = False) -> None:
        pass  # guest application form, no account needed

    def search_jobs(self, profile: Profile) -> list[JobPosting]:
        postings: list[JobPosting] = []
        seen_ids: set[str] = set()
        titles = self.config.get("titles", [])

        for title in titles:
            postings.extend(self._search_aggregator(title, seen_ids, profile))

        companies = self.config.get("ashby_companies", [])
        lowered_titles = [t.lower() for t in titles]
        for company in companies:
            postings.extend(self._search_company_page(company, lowered_titles, seen_ids))

        return postings

    def _search_aggregator(self, title: str, seen_ids: set[str], profile: Profile) -> list[JobPosting]:
        postings: list[JobPosting] = []
        # The aggregator's query param is "search", not "q" -- confirmed
        # live against the actual route source (web/app/api/jobs/route.ts
        # only reads sp.get('search')). Sending "q" was silently ignored,
        # which meant every title search was returning the exact same
        # generic top-scored feed regardless of what was actually searched
        # for -- confirmed live: "AI Engineer" and "HR Coordinator" with "q"
        # returned identical results; with "search" they're genuinely
        # different (17,272 vs 361 matches, real on-topic titles).
        #
        # MAX_LIMIT is a hard server-side cap (confirmed from source: 100),
        # requesting more does nothing -- pagination (the "page" param) is
        # how to get past it, so this pulls up to max_pages of 100 each.
        #
        # exp/minScore/department are free, server-side relevance filters
        # (confirmed against the aggregator's own source: web/lib/scoring.ts,
        # src/config/rules.json) -- they narrow the pool before we even
        # download or fit-score anything, instead of spending LLM calls on
        # postings that are senior-level or low-relevance by the
        # aggregator's own keyword scoring. Verified live: "exp=1" alone cut
        # a 3,975-match "Backend Engineer" search to 811, and adding
        # minScore+department brought it to 574, with entry-level/new-grad
        # titles actually surfacing at the top instead of senior ones.
        params = {"search": title, "source": "ashby", "limit": "100"}
        exp = _experience_param(profile.years_of_experience)
        if exp and self.config.get("ashby_exp_filter", True):
            params["exp"] = exp
        min_score = self.config.get("ashby_min_score")
        if min_score:
            params["minScore"] = str(min_score)
        department = self.config.get("ashby_department")
        if department:
            params["department"] = department
        location = self.config.get("ashby_location")
        if location:
            params["location"] = location
        # Off by default -- stacked with location+exp+minScore, skill
        # matching narrows the pool to near-zero (verified live: 11 results
        # for one title with just location+exp+minScore, down to 2 once
        # skill-match was added on top). Available for anyone who wants
        # tighter-but-much-smaller-volume matching.
        if self.config.get("ashby_tags_filter", False):
            match_param = _skill_match_param(profile.skills)
            if match_param:
                params["match"] = match_param

        max_pages = self.config.get("ashby_aggregator_max_pages", 2)
        for page_num in range(1, max_pages + 1):
            try:
                resp = self.page.request.get(
                    AGGREGATOR_API,
                    params={**params, "page": str(page_num)},
                    timeout=15000,
                )
                if not resp.ok:
                    break
                payload = resp.json()
                data = payload.get("data", [])
            except Exception:
                break
            if not data:
                break
            postings.extend(self._parse_aggregator_items(data, seen_ids))
            if page_num >= payload.get("totalPages", 1):
                break
        return postings

    def _parse_aggregator_items(self, data: list[dict], seen_ids: set[str]) -> list[JobPosting]:
        postings: list[JobPosting] = []

        for item in data:
            job_id = item.get("jobId") or ""
            company = item.get("company") or ""
            apply_url = item.get("applyUrl") or item.get("jobUrl") or ""
            key = f"{company}:{job_id}"
            if not job_id or not apply_url or key in seen_ids:
                continue
            seen_ids.add(key)
            postings.append(
                JobPosting(
                    platform=self.name,
                    job_id=key,
                    title=item.get("title", ""),
                    company=company,
                    location=item.get("location") or "",
                    url=apply_url,
                )
            )
        return postings

    def _search_company_page(
        self, company: str, lowered_titles: list[str], seen_ids: set[str]
    ) -> list[JobPosting]:
        postings: list[JobPosting] = []
        self.page.goto(f"https://jobs.ashbyhq.com/{company}", wait_until="domcontentloaded")
        self.page.wait_for_timeout(1500)

        links = self.page.locator(f"a[href^='/{company}/']")
        count = min(links.count(), 50)
        for i in range(count):
            link = links.nth(i)
            href = link.get_attribute("href") or ""
            m = JOB_ID_RE.search(href)
            if not m:
                continue
            job_id = m.group(1)
            key = f"{company}:{job_id}"
            if key in seen_ids:
                continue

            title_el = link.locator("h3.ashby-job-posting-brief-title")
            job_title = (
                title_el.first.inner_text().strip() if title_el.count() > 0 else link.inner_text().split("\n")[0].strip()
            )
            if lowered_titles and not any(t in job_title.lower() for t in lowered_titles):
                continue
            seen_ids.add(key)

            job_location = ""
            details = link.locator("div.ashby-job-posting-brief-details p").first
            if details.count() > 0:
                job_location = details.inner_text().strip()

            postings.append(
                JobPosting(
                    platform=self.name,
                    job_id=key,
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

            # Ashby's own fixed identifiers for name/email/resume -- a 1:1
            # mapping, not a phrased "question" that varies by company, so
            # these stay a direct lookup rather than going through
            # classification.
            if field_path == "_systemfield_name":
                self._fill_text(entry, profile.full_name)
            elif field_path == "_systemfield_email":
                self._fill_text(entry, profile.email)
            elif field_path == "_systemfield_resume":
                self._upload_resume(entry, profile)
            elif entry.locator("input[type='file']").count() > 0:
                continue  # optional attachment (e.g. cover letter upload) -- leave blank
            elif entry.locator("[class*='-input-yesno']").count() > 0:
                self._fill_yesno(entry, job, profile, question, defaults)
            elif self._is_structured_group(entry, field_path):
                # Any repeatable/structured field group -- Education History
                # today, possibly a "Work History" or similar in the future
                # on some other company's form -- detected generically by
                # having its own nested sub-fields (each with their own
                # label), not hardcoded to one specific field_path. Each
                # sub-field is classified and filled the same way a
                # top-level field would be.
                self._fill_structured_group(entry, job, profile, defaults)
            elif entry.locator("[role='combobox']").count() > 0:
                self._fill_known_or_freeform(entry, question, job, profile, defaults, widget="combobox")
            elif entry.locator("textarea").count() > 0:
                self._fill_textarea(entry, job, profile, question, defaults)
            elif entry.locator("input[type='text'], input[type='email'], input[type='tel']").count() > 0:
                self._fill_known_or_freeform(entry, question, job, profile, defaults, widget="text")

    def _fill_known_or_freeform(
        self, entry, question: str, job: JobPosting, profile: Profile, defaults: dict, widget: str
    ) -> None:
        """Classify the question against real profile facts (PROFILE_SLOTS)
        regardless of how it's phrased -- "Where are you currently based?",
        "Current location", "City" all resolve to the same "location" slot
        and get the SAME real value, instead of each needing its own
        hardcoded pattern. Falls back to a generated free-text answer only
        when the question genuinely isn't asking for one of those exact
        facts. This matters most for combobox/autocomplete fields, where a
        generated answer that doesn't exactly match a real suggestion
        silently fails to select anything."""
        slot = self.tailor.classify_field(question)
        value = PROFILE_SLOTS[slot](profile) if slot != "freeform" else ""
        if slot == "freeform" or not value:
            value = self.tailor.answer_screening_question(profile, job, question, defaults)
        if widget == "combobox":
            self._fill_autocomplete(entry, value)
        else:
            self._fill_text(entry, value)

    def _fill_text(self, entry, value: str) -> None:
        input_el = entry.locator("input[type='text'], input[type='email'], input[type='tel']").first
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

    def _is_structured_group(self, entry, field_path: str) -> bool:
        """A field entry is a repeatable/structured group (Education History
        today, possibly a "Work History" or similar on some other
        company's form tomorrow) if it contains more than one of its OWN
        nested sub-field labels, not just the group's own top-level
        heading label -- detected structurally, not by hardcoding this
        one field_path."""
        sub_labels = entry.locator("label.ashby-application-form-question-title")
        count = sub_labels.count()
        if count <= 1:
            return False
        own_heading = 1 if field_path and entry.locator(f"label[for='{field_path}']").count() > 0 else 0
        return (count - own_heading) > 1

    def _fill_structured_group(self, entry, job: JobPosting, profile: Profile, defaults: dict) -> None:
        """Generic handler for any structured field group: reads its own
        nested sub-fields by their own labels/ids instead of hardcoding
        one widget's shape (e.g. Education History's School/Degree/Field/
        Dates), classifies each sub-question against PROFILE_SLOTS the
        same way a top-level field would be, and fills it with the real
        value using whatever widget that sub-field turns out to be (text,
        autocomplete, or a month+year date-select pair). A sub-field that
        doesn't map to any real profile data (e.g. a Start Date the
        resume doesn't state) is left blank rather than guessed."""
        field_path = entry.get_attribute("data-field-path") or ""
        sub_labels = entry.locator("label.ashby-application-form-question-title")
        group_label = entry.locator(f"label[for='{field_path}']") if field_path else None
        group_heading = group_label.first.inner_text().strip() if group_label and group_label.count() > 0 else ""

        for i in range(sub_labels.count()):
            label_el = sub_labels.nth(i)
            for_attr = label_el.get_attribute("for") or ""
            if not for_attr or for_attr == field_path:
                continue  # the group's own heading label, not a real sub-field
            sub_question = label_el.inner_text().strip()
            # A bare sub-label like "End Date" is ambiguous on its own (end
            # of what?) -- give the classifier the group's own heading for
            # context, same as a human reading the form would have.
            classify_question = f"{group_heading}: {sub_question}" if group_heading else sub_question
            slot = self.tailor.classify_field(classify_question)
            value = PROFILE_SLOTS[slot](profile) if slot != "freeform" else ""

            # Date sub-fields render as a pair of <select> under a
            # container with this id, not a single input.
            date_selects = entry.locator(f"#{for_attr} select")
            if date_selects.count() >= 2:
                month, year = _parse_month_year(value)
                if year:
                    try:
                        if month:
                            date_selects.nth(0).select_option(value=month)
                        date_selects.nth(1).select_option(value=year)
                    except Exception:
                        pass  # year outside the dropdown's range -- leave unfilled
                continue

            text_input = entry.locator(f"input#{for_attr}")
            if text_input.count() > 0:
                if value and not (text_input.first.input_value() or "").strip():
                    text_input.first.fill(value)
                continue

            # No id'd input matched -- an autocomplete/combobox sub-field
            # (e.g. School) that doesn't expose a plain id the same way.
            # _fill_autocomplete scoped to the whole group entry finds the
            # one nested combobox, same as it would at the top level.
            if value:
                self._fill_autocomplete(entry, value)

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

            if entry.locator("input[type='text'], input[type='email'], input[type='tel']").count() > 0:
                val = entry.locator("input[type='text'], input[type='email'], input[type='tel']").first.input_value()
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
