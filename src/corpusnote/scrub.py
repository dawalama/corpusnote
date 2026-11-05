"""Secret scrubbing for corpusnote."""

from __future__ import annotations

import re

_PEM_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")
_URL_RE = re.compile(r"\b(?:postgres|postgresql|mysql|mongodb|redis)://[^\s:]+:[^\s@]+@[^\s/]+/[^\s]*\b")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+([A-Za-z0-9._~+/-]{16,}=*)")
_ASSIGN_RE = re.compile(
    r"(?i)\b(?:password|api[_-]?key|secret|token|client_secret)\s*[:=]\s*['\"]?([^\s'\"]{8,})['\"]?"
)
_KEY_PREFIXES_RE = re.compile(
    r"\b(?:"
    r"sk-[a-zA-Z0-9_-]{20,}|"
    r"sk-ant-[a-zA-Z0-9_-]{20,}|"
    r"(?:ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{36,}|"
    r"github_pat_[a-zA-Z0-9_]{36,}|"
    r"AKIA[0-9A-Z]{16}|"
    r"AIza[0-9A-Za-z-_]{35}|"
    r"xox[baprs]-[0-9A-Za-z]{10,}|"
    r"[0-9]{8,10}:[a-zA-Z0-9_-]{35}"
    r")\b"
)


def scrub_text(text: str, extra_secrets: tuple[str, ...] = ()) -> tuple[str, int]:
    """Scrub secrets from text before storing to disk or passing to subprocess argv.

    Returns:
        (scrubbed_text, count_of_redactions)
    """
    if not text:
        return "", 0

    redactions = 0
    result = text

    # 1. Exact string matches from extra_secrets (longest first)
    valid_secrets = sorted(
        {s for s in extra_secrets if isinstance(s, str) and len(s) >= 8},
        key=len,
        reverse=True,
    )
    for secret in valid_secrets:
        if secret in result:
            count = result.count(secret)
            redactions += count
            result = result.replace(secret, "[REDACTED]")

    # 2. PEM private key banners
    def _sub_pem(match: re.Match) -> str:
        nonlocal redactions
        redactions += 1
        return "[REDACTED]"

    result = _PEM_RE.sub(_sub_pem, result)

    # 3. Connection URLs
    def _sub_url(match: re.Match) -> str:
        nonlocal redactions
        redactions += 1
        return "[REDACTED]"

    result = _URL_RE.sub(_sub_url, result)

    # 4. Bearer tokens
    def _sub_bearer(match: re.Match) -> str:
        nonlocal redactions
        redactions += 1
        return "Bearer [REDACTED]"

    result = _BEARER_RE.sub(_sub_bearer, result)

    # 5. Known high-confidence token prefix patterns (sk-, sk-ant-, ghp_, AKIA, etc.)
    def _sub_token(match: re.Match) -> str:
        nonlocal redactions
        redactions += 1
        return "[REDACTED]"

    result = _KEY_PREFIXES_RE.sub(_sub_token, result)

    # 6. Assignment style secrets
    def _sub_assign(match: re.Match) -> str:
        nonlocal redactions
        val = match.group(1)
        if val == "[REDACTED]":
            return match.group(0)
        redactions += 1
        full = match.group(0)
        return full.replace(val, "[REDACTED]")

    result = _ASSIGN_RE.sub(_sub_assign, result)

    return result, redactions
