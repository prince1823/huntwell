from __future__ import annotations

import json
from pathlib import Path

from .cache import DEFAULT_CACHE_DIR, JSONCache
from .models import JobPosting, Profile
from .openrouter import MODEL, get_client

# Real, structured facts a form field can ask for, each with a getter that
# pulls the actual value straight from the parsed resume -- never generated
# text. This is what makes classify_field() safe rather than just clever:
# a field that's recognized as asking for one of these gets REAL data,
# regardless of how the question is phrased, instead of a free-text AI
# answer that an autocomplete/combobox might not be able to match to a
# real suggestion.
PROFILE_SLOTS: dict[str, "callable"] = {
    "full_name": lambda p: p.full_name,
    "email": lambda p: p.email,
    "phone": lambda p: p.phone,
    "location": lambda p: p.location,
    "linkedin_url": lambda p: p.linkedin_url,
    "portfolio_url": lambda p: p.portfolio_url,
    "school": lambda p: p.education[0].school if p.education else "",
    "degree": lambda p: p.education[0].degree if p.education else "",
    "field_of_study": lambda p: p.education[0].field if p.education else "",
    "graduation_date": lambda p: p.education[0].end_date if p.education else "",
    "current_company": lambda p: next(
        (e.company for e in p.experience if e.end_date.strip().lower() == "present"),
        p.experience[0].company if p.experience else "",
    ),
    "current_job_title": lambda p: next(
        (e.title for e in p.experience if e.end_date.strip().lower() == "present"),
        p.experience[0].title if p.experience else "",
    ),
}


class Tailor:
    """Wraps LLM calls for fit scoring, cover letters, and screening-question
    answers. Every call is cached on disk keyed by (job, profile, question),
    so re-running against the same postings -- e.g. after a crash, or while
    iterating on the apply logic -- doesn't re-spend tokens on answers
    already computed.

    cache_dir separates one account/profile's cached answers from
    another's when running multiple accounts (see main.py --profile)."""

    def __init__(self, api_key: str, cache_dir: Path | str = DEFAULT_CACHE_DIR):
        self.client = get_client(api_key)
        self.cache = JSONCache("llm_answers", base_dir=cache_dir)

    def _profile_fingerprint(self, profile: Profile) -> str:
        return JSONCache.make_key(profile.model_dump_json())

    def cover_letter(self, profile: Profile, job: JobPosting) -> str:
        key = JSONCache.make_key(
            "cover_letter", job.platform, job.job_id, self._profile_fingerprint(profile)
        )
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        prompt = f"""Write a concise (under 200 words) cover letter from this candidate for this job.
No placeholders, no markdown, plain text only, ready to paste into an application form.

This must read as written for THIS company specifically, not a template with
the name swapped in. Pull at least one concrete detail from the job
description below (what the company actually builds, a problem they're
solving, a technology they use) and connect it to a specific thing in the
candidate's background -- not just "I'm excited to apply" boilerplate.

CANDIDATE SUMMARY:
{profile.summary}

CANDIDATE SKILLS: {", ".join(profile.skills)}

CANDIDATE EXPERIENCE:
{self._format_experience(profile)}

CANDIDATE EDUCATION:
{self._format_education(profile)}

JOB TITLE: {job.title}
COMPANY: {job.company}
JOB DESCRIPTION:
{job.description[:3000]}
"""
        resp = self.client.chat.completions.create(
            model=MODEL, max_tokens=500, messages=[{"role": "user", "content": prompt}]
        )
        answer = resp.choices[0].message.content.strip()
        self.cache.set(key, answer)
        return answer

    def company_interest_answer(self, profile: Profile, job: JobPosting) -> str:
        """Short answer for "what interests you about this company?" /
        "why do you want to work here?" style fields -- distinct from
        cover_letter: no salutation/signature, just a direct 2-4 sentence
        answer grounded in what this specific company actually does."""
        key = JSONCache.make_key(
            "company_interest", job.platform, job.job_id, self._profile_fingerprint(profile)
        )
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        prompt = f"""Answer this job application question in 2-4 sentences, plain text, no
salutation or signature (it goes directly into a form field, not a letter).

QUESTION: "What interests you about working for this company?" (or similar)

Your answer must be specific to THIS company, not a generic "I'm excited to
apply" statement. Identify from the job/company info below what this company
actually builds or what problem it solves, then explain concretely how the
candidate's specific background/skills would let them contribute to that --
reference an actual project, skill, or experience from their background, not
just a restatement of their title. If the company/job info below doesn't
give you anything specific to latch onto, say so implicitly by keeping the
answer grounded in the role itself rather than inventing company details.

CANDIDATE SUMMARY: {profile.summary}
CANDIDATE SKILLS: {", ".join(profile.skills)}
CANDIDATE EXPERIENCE:
{self._format_experience(profile)}
CANDIDATE EDUCATION:
{self._format_education(profile)}

JOB TITLE: {job.title}
COMPANY: {job.company}
JOB/COMPANY INFO:
{job.description[:4000]}
"""
        resp = self.client.chat.completions.create(
            model=MODEL, max_tokens=250, messages=[{"role": "user", "content": prompt}]
        )
        answer = resp.choices[0].message.content.strip()
        self.cache.set(key, answer)
        return answer

    def answer_single_choice(
        self, profile: Profile, job: JobPosting, question: str, options: list[str], defaults: dict
    ) -> str:
        """Pick exactly one of a fixed set of options (radio buttons --
        typically Yes/No). Returns the exact option text so the caller can
        match it directly, rather than reusing the free-text
        answer_screening_question, whose answers (e.g. a bare "1"/"0" for a
        yes/no question) don't reliably match option labels like "Yes"/"No"
        and would silently fall back to the first option regardless of the
        actual answer."""
        key = JSONCache.make_key(
            "single_choice",
            job.platform,
            job.job_id,
            question,
            "|".join(options),
            self._profile_fingerprint(profile),
        )
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        prompt = f"""This is a single-choice question (radio buttons) on a job application.
Choose exactly ONE of the options below, based honestly on the candidate's
resume -- do not pick a flattering-sounding option unless the resume
actually supports it (e.g. don't answer "yes" to having led a team if
nothing in their experience shows that). Reply with ONLY the exact option
text, copied exactly as given in OPTIONS, nothing else.

CANDIDATE SUMMARY: {profile.summary}
CANDIDATE YEARS OF EXPERIENCE: {profile.years_of_experience}
CANDIDATE EXPERIENCE:
{self._format_experience(profile)}
CANDIDATE EDUCATION:
{self._format_education(profile)}

DEFAULTS (use if the resume is silent but a default covers this): {json.dumps(defaults, indent=2)}

JOB: {job.title} at {job.company}

QUESTION: {question}
OPTIONS:
{chr(10).join(options)}
"""
        resp = self.client.chat.completions.create(
            model=MODEL, max_tokens=50, messages=[{"role": "user", "content": prompt}]
        )
        text = resp.choices[0].message.content.strip()
        answer = next((opt for opt in options if opt.strip().lower() == text.strip().lower()), None)
        if answer is None:
            # Fall back to substring match rather than assuming the first
            # option is correct.
            answer = next(
                (opt for opt in options if opt.strip().lower() in text.lower() or text.lower() in opt.strip().lower()),
                options[0] if options else text,
            )
        self.cache.set(key, answer)
        return answer

    def answer_multi_select(
        self, profile: Profile, job: JobPosting, question: str, options: list[str], defaults: dict
    ) -> list[str]:
        """Pick zero or more of a fixed set of checkbox options (e.g. "Which
        of these tools are you familiar with?"). Never fabricates -- only
        selects options the resume actually evidences, since checking boxes
        for skills/tools the candidate doesn't have would misrepresent them
        to the employer."""
        key = JSONCache.make_key(
            "multi_select",
            job.platform,
            job.job_id,
            question,
            "|".join(options),
            self._profile_fingerprint(profile),
        )
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        prompt = f"""This is a multi-select checkbox question on a job application. Select ONLY
the options that are genuinely supported by the candidate's resume below --
do not select an option just because it would look good; an unchecked box is
correct when the resume gives no evidence for it. Reply with the exact option
text(s) that apply, one per line, exactly as given in OPTIONS below. If none
apply, reply with the single word NONE.

CANDIDATE SKILLS: {", ".join(profile.skills)}
CANDIDATE EXPERIENCE:
{self._format_experience(profile)}
CANDIDATE EDUCATION:
{self._format_education(profile)}

DEFAULTS (use if the resume is silent but a default covers this): {json.dumps(defaults, indent=2)}

JOB: {job.title} at {job.company}

QUESTION: {question}
OPTIONS:
{chr(10).join(options)}
"""
        resp = self.client.chat.completions.create(
            model=MODEL, max_tokens=200, messages=[{"role": "user", "content": prompt}]
        )
        text = resp.choices[0].message.content.strip()
        if text.upper() == "NONE":
            selected: list[str] = []
        else:
            lines = {line.strip() for line in text.splitlines() if line.strip()}
            selected = [opt for opt in options if opt in lines]
        self.cache.set(key, selected)
        return selected

    def answer_screening_question(
        self, profile: Profile, job: JobPosting, question: str, defaults: dict
    ) -> str:
        """Answer a single free-text or short-answer screening question for an application form."""
        key = JSONCache.make_key(
            "answer",
            job.platform,
            job.job_id,
            question,
            json.dumps(defaults, sort_keys=True),
            self._profile_fingerprint(profile),
        )
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        prompt = f"""You are filling out a job application form as this candidate. Answer the
following screening question briefly and truthfully based on the candidate's resume data below.
If the resume doesn't contain the answer, use the provided defaults. Reply with ONLY the answer
text, no preamble, no quotes.

If the question asks for YEARS OF EXPERIENCE (overall, or with a specific
skill/technology), compute it from the candidate's actual work history:
- Overall experience: use `years_of_experience`, which is the candidate's
  total professional tenure computed from their employment dates.
- Experience with a specific skill/technology: look at which `experience`
  entries mention it (in the title, description, or alongside it in
  `skills`) and estimate the span covering those roles' dates. If the skill
  only appears in a role that started this year or last year, that's 0-1
  years, not more -- never round up or invent a larger number than the
  dates support. If the skill isn't evidenced in any experience entry at
  all, answer 0 rather than guessing a plausible-sounding number.
Reply with a bare number (e.g. "1"), not a sentence, when the question
expects a count of years.

If the question asks WHEN you can start / join / be available (earliest
start date, notice period start, etc.), answer "Immediately" (or
"Immediately available" if the field expects a short phrase rather than one
word) rather than computing or guessing a calendar date -- a past job's
start date in the resume is NOT the answer to this question, and inventing
a future date isn't grounded in anything real either. If
`notice_period_days` in DEFAULT ANSWERS is explicitly set to a nonzero
value, say that instead (e.g. "After a 30-day notice period"), still never
a specific calendar date.

If the question shows a numeric example (e.g. "Example: 200000") or
otherwise clearly expects a plain number (salary/CTC, compensation, notice
period in days, etc.), you MUST reply with a bare number in that same
format -- never a sentence, never "not specified", "N/A", "I don't have
this info", or similar. The form's validation will silently reject anything
non-numeric and the whole application will get stuck. Check DEFAULT ANSWERS
first; if nothing there covers it either, give your best reasonable numeric
estimate for someone at this candidate's experience level and role rather
than declining to answer.

For every OTHER question -- anything open-ended, behavioral, or asking what
tools/skills/experience the candidate has in prose form (not a bare count of
years) -- always answer in a real sentence or two, never a bare number like
"0". If the resume doesn't directly cover it, say so honestly and pivot to
genuinely transferable experience (e.g. "I haven't directly used CRM tools,
but my experience building [X] involved similar workflows") rather than
replying with "0" or leaving it blank -- a bare "0" to a prose question looks
broken, not honest.

CANDIDATE PROFILE (JSON):
{profile.model_dump_json(indent=2)}

DEFAULT ANSWERS (use if resume has no info):
{json.dumps(defaults, indent=2)}

JOB: {job.title} at {job.company}

QUESTION: {question}
"""
        resp = self.client.chat.completions.create(
            model=MODEL, max_tokens=150, messages=[{"role": "user", "content": prompt}]
        )
        answer = resp.choices[0].message.content.strip()
        self.cache.set(key, answer)
        return answer

    def score_fit(self, profile: Profile, job: JobPosting) -> float:
        """Return a 0-1 fit score so we skip clearly irrelevant postings before spending apply effort."""
        key = JSONCache.make_key(
            "score_fit", job.platform, job.job_id, self._profile_fingerprint(profile)
        )
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        prompt = f"""Score how well this candidate fits this job posting, from 0.0 (no fit) to
1.0 (excellent fit). Reply with ONLY a number like 0.73, nothing else.

Score as a weighted combination of three factors. EXPERIENCE/SENIORITY FIT
and TITLE/ROLE-TYPE MATCH are co-primary -- equally weighted, and together
they dominate the score. SKILLS/DOMAIN RELEVANCE is the smallest factor: it
can only pull the score up within the ceiling the other two allow, never
override either of them.

1. EXPERIENCE/SENIORITY FIT (co-primary). Compare the candidate's
   {profile.years_of_experience} years against whatever the posting states
   or implies (an explicit "X+ years"/"minimum X years", or a seniority
   word in the title -- "Senior"/"Staff"/"Lead"/"Principal"/"Director" imply
   roughly 5+, 8+, 5+, 8+, 10+ years respectively absent a stated number).
   A clear mismatch (posting wants meaningfully more than the candidate
   has) caps the WHOLE score at 0.2 regardless of how well skills match --
   a skills-perfect candidate still gets auto-rejected by an experience
   gate, so don't let skill match compensate for it. An entry-level/
   junior/new-grad/intern posting, or one with no stated experience
   requirement at all, is never penalized for a candidate with limited
   experience -- that's the opposite error, and just as costly.
2. TITLE/ROLE-TYPE MATCH (co-primary, same weight as experience fit). Does
   the posting's title actually describe the kind of role the candidate is
   looking for (per CANDIDATE HEADLINE below), not just a role in the same
   general field? A substantively different role type (e.g. a QA/support/
   sales-engineering title for a candidate targeting pure software
   engineering) caps the score the same way an experience mismatch does --
   don't let strong skills overlap paper over a role-type mismatch either.
3. SKILLS/DOMAIN RELEVANCE (the smallest factor). Only matters once
   experience fit and title fit are both established; a skills-perfect
   posting that fails either of the other two stays capped per above.

The job description below may be empty or truncated (not every platform has
the full text available at this stage) -- when it is, rely on the title's
own wording for both rules 1 and 2, and don't assume a good fit just
because nothing contradicts it.

CANDIDATE HEADLINE: {profile.headline}
CANDIDATE SKILLS: {", ".join(profile.skills)}
CANDIDATE YEARS OF EXPERIENCE: {profile.years_of_experience}

JOB TITLE: {job.title}
JOB DESCRIPTION:
{job.description[:2000]}
"""
        resp = self.client.chat.completions.create(
            model=MODEL, max_tokens=10, messages=[{"role": "user", "content": prompt}]
        )
        try:
            score = float(resp.choices[0].message.content.strip())
        except ValueError:
            score = 0.5
        self.cache.set(key, score)
        return score

    def classify_field(self, question: str) -> str:
        """Classify a form field's question against PROFILE_SLOTS so it can be
        filled with real resume data, regardless of how a given company
        phrases it ("Where are you currently based?" / "Current location" /
        "City" all resolve to the same "location" slot). Returns "freeform"
        for anything that doesn't map to a known real fact (open-ended
        questions, yes/no questions, salary, years of experience, etc.) --
        those still go through answer_screening_question as before.

        Cached on the question text alone, not the job or profile -- the
        classification itself doesn't depend on either (the same phrasing
        means the same slot for every company and every candidate), so this
        also means a phrasing seen once is never re-classified again."""
        key = JSONCache.make_key("classify_field", question.strip().lower())
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        slots = ", ".join(PROFILE_SLOTS.keys())
        prompt = f"""Does this job application form question ask for one of these specific,
literal facts about a candidate: {slots}?

Reply with ONLY the single matching name from that list, or "freeform" if the
question asks for anything else -- including yes/no questions, salary/CTC,
years of experience, visa/sponsorship/relocation questions, or any
open-ended/behavioral question. Only match a slot if the question is
unambiguously and specifically asking for that exact fact.

QUESTION: {question}
"""
        resp = self.client.chat.completions.create(
            model=MODEL, max_tokens=20, messages=[{"role": "user", "content": prompt}]
        )
        answer = resp.choices[0].message.content.strip().lower().strip('."')
        result = answer if answer in PROFILE_SLOTS else "freeform"
        self.cache.set(key, result)
        return result

    def suggest_search_titles(self, profile: Profile, max_titles: int = 5) -> list[str]:
        """Return a short list of job titles to search for on LinkedIn/Wellfound/Ashby,
        derived from this specific resume instead of one hardcoded list shared by
        every account -- so a product manager's resume and a backend engineer's
        resume searching through the same run each get titles relevant to them."""
        key = JSONCache.make_key("suggest_search_titles", self._profile_fingerprint(profile))
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        prompt = f"""Suggest up to {max_titles} job titles to search for on job boards for this
candidate, ordered by relevance. Use titles as they commonly appear in real job
postings (e.g. "Backend Engineer", not "Backend Software Development
Professional"). Base this on their stated target role and their actual
skills/experience -- do not suggest titles requiring skills or seniority they
don't have. Reply with ONLY a JSON array of strings, nothing else.

CANDIDATE HEADLINE: {profile.headline}
CANDIDATE SUMMARY: {profile.summary}
CANDIDATE SKILLS: {", ".join(profile.skills)}
CANDIDATE YEARS OF EXPERIENCE: {profile.years_of_experience}
MOST RECENT ROLE: {profile.experience[0].title if profile.experience else "N/A"}
"""
        resp = self.client.chat.completions.create(
            model=MODEL, max_tokens=150, messages=[{"role": "user", "content": prompt}]
        )
        text = resp.choices[0].message.content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
        try:
            titles = json.loads(text)
            if not isinstance(titles, list) or not titles:
                raise ValueError
            titles = [str(t) for t in titles][:max_titles]
        except (json.JSONDecodeError, ValueError):
            titles = [profile.headline] if profile.headline else []
        self.cache.set(key, titles)
        return titles

    @staticmethod
    def _format_experience(profile: Profile) -> str:
        lines = []
        for exp in profile.experience[:4]:
            lines.append(f"- {exp.title} at {exp.company} ({exp.start_date}-{exp.end_date}): {exp.description}")
        return "\n".join(lines)

    @staticmethod
    def _format_education(profile: Profile) -> str:
        lines = []
        for edu in profile.education:
            lines.append(f"- {edu.degree} in {edu.field}, {edu.school} ({edu.end_date})")
        return "\n".join(lines) if lines else "(none listed)"
