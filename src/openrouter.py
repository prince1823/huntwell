from __future__ import annotations

from openai import OpenAI

# Despite the filename (kept to avoid churning imports across the codebase),
# this now calls OpenAI directly rather than OpenRouter -- the user's
# OpenRouter key ran out of credits twice, so we switched providers.
MODEL = "gpt-4o-mini"


def get_client(api_key: str) -> OpenAI:
    return OpenAI(api_key=api_key)
