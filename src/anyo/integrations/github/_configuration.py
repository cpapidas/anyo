"""GitHub hook configuration and repository identifier validation."""

from __future__ import annotations

import re
from collections.abc import Mapping

_OWNER = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}")


def validate_configuration(action: str, configuration: Mapping[str, object]) -> Mapping[str, str]:
    """Validate GitHub options and return a new normalized configuration."""
    if action != "pr_created":
        raise ValueError("GitHub supports only the 'pr_created' action")
    if set(configuration) != {"organization", "repository"}:
        raise ValueError("GitHub requires exactly organization and repository")
    return {
        "organization": normalize_name(configuration["organization"], "organization"),
        "repository": normalize_name(configuration["repository"], "repository"),
    }


def normalize_name(value: object, field: str) -> str:
    """Apply the same name rules to configuration and webhook targets."""
    pattern = _OWNER if field == "organization" else _REPOSITORY
    if not isinstance(value, str) or not pattern.fullmatch(value) or value in {".", ".."}:
        raise ValueError(f"GitHub {field} must be a plain, nonempty name")
    return value.lower()
