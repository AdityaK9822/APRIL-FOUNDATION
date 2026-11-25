"""The persona permission layer: two machine gates plus attested rules.

The two gates are deterministic and shared with the post-selection checklist:
an explicit public action needs an approval marker in the same command; a
credential-shaped string is refused outright. Every other protocol is recorded
as attested, never pretended to be machine-verified.
"""

from __future__ import annotations

import re

from .kernel import PROTOCOL_OVERRIDE_LINE

MACHINE_RULES: tuple[str, ...] = (
    "no-unapproved-public-action",
    "no-secrets-stored-echoed-logged",
)

PUBLIC_ACTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bgit\s+push\b", re.I),
    re.compile(r"\bgh\s+(?:release|pr)\s+create\b", re.I),
    re.compile(r"\bnpm\s+publish\b", re.I),
    re.compile(r"\bdocker\s+push\b", re.I),
    re.compile(r"\bkubectl\s+(?:apply|rollout)\b", re.I),
    re.compile(r"\bterraform\s+apply\b", re.I),
    re.compile(r"\bcurl\b[^\n]*\b(?:-X|--request)\s*(?:POST|PUT|PATCH|DELETE)\b", re.I),
    re.compile(r"\b(?:post|publish|send)\b[^\n]{0,30}\b(?:publicly|to\s+production)\b", re.I),
)

APPROVAL_PATTERN = re.compile(
    r"\b(?:user|operator)\s+approved\b|\bapproved\s+by\b|\bAPPROVED\b|"
    r"\b(?:got|has)\s+(?:the\s+)?go-ahead\b",
    re.I,
)

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("bearer-token", re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}\b", re.I)),
    (
        "credential-assignment",
        re.compile(
            r"(?i)\b(?:password|passwd|api[_-]?key|apikey|secret|access[_-]?token|"
            r"refresh[_-]?token)\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-./+]{8,}"
        ),
    ),
)


def public_action_hits(text: str) -> bool:
    return any(pattern.search(str(text or "")) for pattern in PUBLIC_ACTION_PATTERNS)


def approved_marker(text: str) -> bool:
    return bool(APPROVAL_PATTERN.search(str(text or "")))


def secret_hits(text: str) -> list[str]:
    return [label for label, pattern in SECRET_PATTERNS if pattern.search(str(text or ""))]


class PersonaGuard:
    """A runtime permission layer over a persona's c2 text."""

    def __init__(self, guardrails: str = "", temperature: float | None = None):
        self.guardrails = str(guardrails or "")
        self.temperature = temperature

    def check(self, command: str) -> tuple[bool, str]:
        text = str(command or "")
        secrets = secret_hits(text)
        if secrets:
            return (
                False,
                "secrets hygiene: never store, echo, or log secrets — "
                f"command carries credential-shaped text ({', '.join(secrets)})",
            )
        if public_action_hits(text) and not approved_marker(text):
            return (
                False,
                "public actions need a go-ahead: show the user first and get an "
                "explicit approval, then rerun with the marker in the command "
                "(e.g. `user approved the release`)",
            )
        return True, ""

    def attested_rules(self) -> list[str]:
        rules: list[str] = []
        in_block = False
        for raw in self.guardrails.splitlines():
            line = raw.strip()
            if line == "## Protocols (non-negotiable)":
                in_block = True
                continue
            if line.startswith("## "):
                in_block = False
            if in_block and line and line != PROTOCOL_OVERRIDE_LINE:
                rules.append(line.lstrip("0123456789. ").strip())
        return [rule for rule in rules if rule]
