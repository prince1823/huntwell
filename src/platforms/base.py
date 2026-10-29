from __future__ import annotations

from abc import ABC, abstractmethod

from playwright.sync_api import BrowserContext

from ..llm import Tailor
from ..models import ApplicationResult, JobPosting, Profile


class PlatformAdapter(ABC):
    """Common interface every job site adapter implements.

    A concrete adapter owns a persistent Playwright browser context (so login
    sessions survive between runs) and knows how to search for jobs and fill
    that site's specific application form.
    """

    name: str = "base"
    requires_login: bool = True

    def __init__(self, context: BrowserContext, tailor: Tailor, config: dict):
        self.context = context
        self.tailor = tailor
        self.config = config
        self.page = context.new_page()

    @abstractmethod
    def ensure_logged_in(self, email: str, password: str) -> None:
        """Log in if the stored session (context storage_state) isn't already authenticated."""

    @abstractmethod
    def search_jobs(self, profile: Profile) -> list[JobPosting]:
        """Return candidate postings for the titles/locations in config, newest first."""

    @abstractmethod
    def apply(self, job: JobPosting, profile: Profile) -> ApplicationResult:
        """Fill and submit the application for one posting."""
