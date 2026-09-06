"""Credential / configuration access.

Credentials are read from environment variables only — no secret ever appears
in the codebase.  Each protocol owns its KEY and BASE_URL pair:

    responses protocol (deepseek-v4-pro): RESPONSES_API_KEY / RESPONSES_BASE_URL
    messages  protocol (deepseek-v4-flash): MESSAGES_API_KEY / MESSAGES_BASE_URL

Conventional SDK variable names are honoured as fallbacks so existing shells
keep working (RESPONSES wins over OPENAI, MESSAGES over ANTHROPIC).  When no
base URL is configured the SDK's own default endpoint is used.
"""

from __future__ import annotations

import os
from typing import Optional

RESPONSES_KEY_ENVS = ("RESPONSES_API_KEY", "OPENAI_API_KEY")
RESPONSES_BASE_ENVS = ("RESPONSES_BASE_URL", "OPENAI_BASE_URL")

MESSAGES_KEY_ENVS = ("MESSAGES_API_KEY", "ANTHROPIC_API_KEY")
MESSAGES_BASE_ENVS = ("MESSAGES_BASE_URL", "ANTHROPIC_BASE_URL")


def _first(names: tuple[str, ...]) -> Optional[str]:
    for name in names:
        value = os.getenv(name)
        if value:  # empty value == unset
            return value
    return None


def responses_credentials() -> tuple[Optional[str], Optional[str]]:
    """(api_key, base_url) for the OpenAI Responses-API adapter."""
    return _first(RESPONSES_KEY_ENVS), _first(RESPONSES_BASE_ENVS)


def messages_credentials() -> tuple[Optional[str], Optional[str]]:
    """(api_key, base_url) for the Anthropic Messages-API adapter."""
    return _first(MESSAGES_KEY_ENVS), _first(MESSAGES_BASE_ENVS)
