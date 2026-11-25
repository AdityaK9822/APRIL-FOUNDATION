"""One YAML frontmatter parser for skills, plugins and subagent profiles."""

from __future__ import annotations

import yaml


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return ``(frontmatter, body)``; a missing or broken block yields ``({}, text)``."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    raw = text[3:end]
    body = text[end + 4 :].lstrip("\r\n")
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        data = {}
    return (data if isinstance(data, dict) else {}), body
