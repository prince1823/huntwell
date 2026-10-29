from __future__ import annotations

from pydantic import BaseModel, Field


class Experience(BaseModel):
    title: str
    company: str
    start_date: str = ""
    end_date: str = ""
    description: str = ""


class Education(BaseModel):
    school: str
    degree: str = ""
    field: str = ""
    end_date: str = ""


class Profile(BaseModel):
    """Structured version of the user's resume, used to fill forms and tailor materials."""

    full_name: str
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin_url: str = ""
    portfolio_url: str = ""
    headline: str = ""
    summary: str = ""
    skills: list[str] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    years_of_experience: int = 0
    resume_pdf_path: str = ""
    raw_resume_text: str = ""


class JobPosting(BaseModel):
    platform: str
    job_id: str
    title: str
    company: str
    location: str = ""
    url: str
    description: str = ""
    easy_apply: bool = False

    @property
    def dedupe_key(self) -> str:
        return f"{self.platform}:{self.job_id}"


class ApplicationResult(BaseModel):
    job: JobPosting
    status: str  # "applied", "skipped", "failed", "needs_review"
    reason: str = ""
