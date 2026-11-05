from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .cache import DEFAULT_CACHE_DIR, JSONCache
from .models import Profile


def _extract_text(resume_path: Path) -> str:
    suffix = resume_path.suffix.lower()
    if suffix == ".pdf":
        import pdfplumber

        with pdfplumber.open(resume_path) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    if suffix == ".docx":
        import docx

        doc = docx.Document(str(resume_path))
        return "\n".join(p.text for p in doc.paragraphs)
    if suffix == ".txt":
        return resume_path.read_text()
    raise ValueError(f"Unsupported resume format: {suffix}")


EXTRACTION_PROMPT = """You are extracting structured data from a resume's raw text.
Return ONLY a JSON object matching this schema, no prose, no markdown fences:

{{
  "full_name": str,
  "email": str,
  "phone": str,
  "location": str,          // the candidate's current city, inferred from any real
                             // signal in the resume -- an explicit address/location
                             // line, the city listed on their most recent job, or
                             // their most recent university's city if no work
                             // location is given. Prefer the most recent/current
                             // signal over an older one. If the resume gives no
                             // location signal of any kind, use "" -- never invent
                             // a city that isn't actually supported by the text.
  "linkedin_url": str,
  "portfolio_url": str,
  "headline": str,          // the candidate's current professional identity/target
                             // role, e.g. "Backend Engineer". Prioritize what the
                             // PROFESSIONAL SUMMARY says they're targeting/interested
                             // in over the title of an old past role (especially a
                             // short internship) -- e.g. if the summary says they're
                             // "interested in management, business operations, and
                             // strategy analyst roles" but their most recent listed
                             // role is a 2-month data science internship from 2 years
                             // ago, the headline should reflect the stated target
                             // (e.g. "Business Operations / Project Coordination"),
                             // not the stale internship title.
  "summary": str,           // 2-3 sentence professional summary
  "skills": [str],
  "experience": [
    {{"title": str, "company": str, "start_date": str, "end_date": str, "description": str}}
  ],
  "education": [
    {{"school": str, "degree": str, "field": str, "end_date": str}}
  ],
  "years_of_experience": int  // total professional experience, estimate from dates
}}

If a field is missing from the resume, use an empty string, empty list, or 0. Do not invent facts.

For dates: resolve relative phrasing ("since last year", "for the past year",
"current") against today's date ({today_date}) -- e.g. a role
described as starting "last year" with no end date is ongoing, roughly 1 year
long. Use "Present" as end_date for ongoing roles. Compute years_of_experience
as the total span of professional work experience across all roles (merge
overlapping date ranges rather than summing them), rounded DOWN to the
nearest whole completed year (floor, not nearest) -- e.g. 16 or 20 months of
combined experience is 1 year, not 2. Only round up to a full year once that
year is actually complete.

RESUME TEXT:
---
{resume_text}
---
"""


def parse_resume(
    resume_path: str, api_key: str, force_refresh: bool = False, cache_dir=DEFAULT_CACHE_DIR, model: str | None = None
) -> Profile:
    """Parse a resume file (.pdf/.docx/.txt) into a structured Profile.

    Cached by file path + mtime + size, so re-running the pipeline against
    the same unmodified resume costs zero LLM tokens after the first parse.
    Pass force_refresh=True (or edit the resume, which changes its mtime) to
    re-parse. cache_dir namespaces this per account/profile (see main.py
    --profile) so multiple people's cached resume data stays separate.
    """
    path = Path(resume_path)
    raw_text = _extract_text(path)

    cache = JSONCache("resume_profiles", base_dir=cache_dir)
    stat = path.stat()
    cache_key = JSONCache.make_key(str(path.resolve()), str(stat.st_mtime_ns), str(stat.st_size))
    if not force_refresh:
        cached = cache.get(cache_key)
        if cached is not None:
            return Profile(**cached)

    from .openrouter import MODEL, get_client

    client = get_client(api_key)
    response = client.chat.completions.create(
        model=model or MODEL,
        max_tokens=2000,
        messages=[
            {
                "role": "user",
                "content": EXTRACTION_PROMPT.format(
                    resume_text=raw_text, today_date=date.today().isoformat()
                ),
            }
        ],
    )
    text = response.choices[0].message.content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    data = json.loads(text)
    data["resume_pdf_path"] = str(path)
    data["raw_resume_text"] = raw_text
    cache.set(cache_key, data)
    return Profile(**data)
