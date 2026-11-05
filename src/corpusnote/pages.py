"""Page read, write, proposal, and confirmation for corpusnote."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
import yaml

from .errors import MissingPage, RejectedWrite
from .layout import resolve_page_path, validate_segment
from .models import Corpus, Page
from .scrub import scrub_text

DENYLIST_SUBSTRINGS = (
    "ignore your instructions",
    "ignore previous instructions",
    "ignore all previous instructions",
    "ignore the above",
    "disregard your instructions",
    "you are now",
    "system:",
    "developer:",
)


def contains_instruction_injection(text: str) -> bool:
    """Check if text contains any prompt injection phrase from the denylist."""
    lower = text.lower()
    for phrase in DENYLIST_SUBSTRINGS:
        if phrase in lower:
            return True
    return False


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _split_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter and body from a markdown document."""
    if not content.startswith("---"):
        return {}, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content

    raw_yaml = parts[1]
    body = parts[2].lstrip("\n")

    try:
        fm = yaml.safe_load(raw_yaml) or {}
    except Exception:
        raise RejectedWrite("bad_id")

    if not isinstance(fm, dict):
        raise RejectedWrite("bad_id")

    return fm, body


def _dump_page(frontmatter: dict, body: str) -> str:
    """Format YAML frontmatter and body."""
    raw_yaml = yaml.safe_dump(
        frontmatter,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
    return f"---\n{raw_yaml}---\n\n{body}"


def read_page(corpus: Corpus, relpath: str) -> Page:
    """Read a page from the corpus root with path jail enforcement.

    Raises:
        MissingPage: if page is absent or resolves outside root.
    """
    try:
        path = resolve_page_path(corpus, relpath)
    except RejectedWrite:
        raise MissingPage(corpus.id, relpath)

    if not path.is_file():
        raise MissingPage(corpus.id, relpath)

    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        raise MissingPage(corpus.id, relpath)

    fm, body = _split_frontmatter(content)
    return Page(relpath=relpath, frontmatter=fm, body=body)


def write_page(
    corpus: Corpus,
    relpath: str,
    *,
    frontmatter: dict,
    body: str,
    extra_secrets: tuple[str, ...] = (),
) -> Page:
    """Write a page to the corpus with secret scrubbing and permission 0600.

    Rejects kind: rule and forces confirmed: false.
    """
    path = resolve_page_path(corpus, relpath)

    # Rejection of kind: rule
    if frontmatter.get("kind") == "rule":
        raise RejectedWrite("bad_kind")

    # Force confirmed: false
    fm = dict(frontmatter)
    fm["confirmed"] = False

    # Scrub body
    scrubbed_body, count = scrub_text(body, extra_secrets=extra_secrets)

    # Permalinks and metadata
    stem = relpath[:-3] if relpath.endswith(".md") else relpath
    fm["id"] = f"{corpus.id}/{stem}"
    fm["corpus"] = corpus.id
    fm["checksum"] = hashlib.sha256(scrubbed_body.encode("utf-8")).hexdigest()
    fm["redactions"] = count
    now = _now_iso()
    if "created_at" not in fm or not fm["created_at"]:
        fm["created_at"] = now
    fm["updated_at"] = now

    rendered = _dump_page(fm, scrubbed_body)

    path.parent.mkdir(parents=True, exist_ok=True)
    # Write file mode 0600
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(rendered)
    except Exception:
        pass

    return Page(relpath=relpath, frontmatter=fm, body=scrubbed_body)


def propose_rule(
    corpus: Corpus,
    *,
    slug: str,
    title: str,
    body: str,
    project: str | None,
    project_id: str | None = None,
    extra_secrets: tuple[str, ...] = (),
) -> Page:
    """Propose a new rule.

    Forces kind: rule, source: human, confirmed: false.
    Refuses if body contains denylisted prompt injection instructions.
    """
    validate_segment(slug)
    relpath = f"rules/{slug}.md"
    path = resolve_page_path(corpus, relpath)

    if contains_instruction_injection(body):
        raise RejectedWrite("instruction_in_rule")

    scrubbed_body, count = scrub_text(body, extra_secrets=extra_secrets)
    stem = f"rules/{slug}"
    now = _now_iso()

    fm = {
        "id": f"{corpus.id}/{stem}",
        "title": title,
        "kind": "rule",
        "status": "active",
        "confirmed": False,
        "confirmed_by": "",
        "project": project or "",
        "project_id": project_id or "",
        "corpus": corpus.id,
        "created_at": now,
        "updated_at": now,
        "source": "human",
        "checksum": hashlib.sha256(scrubbed_body.encode("utf-8")).hexdigest(),
        "redactions": count,
    }

    rendered = _dump_page(fm, scrubbed_body)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(rendered)

    return Page(relpath=relpath, frontmatter=fm, body=scrubbed_body)


def confirm_rule(corpus: Corpus, relpath: str, *, confirmed_by: str) -> Page:
    """Confirm a human rule.

    Accepts only rules/{slug}.md where kind == "rule" and source == "human".
    Does not change kind.
    """
    parts = relpath.split("/")
    if len(parts) != 2 or parts[0] != "rules" or not parts[1].endswith(".md"):
        raise RejectedWrite("bad_kind")

    page = read_page(corpus, relpath)
    fm = dict(page.frontmatter)

    if fm.get("kind") != "rule" or fm.get("source") != "human":
        raise RejectedWrite("bad_kind")

    if contains_instruction_injection(page.body):
        raise RejectedWrite("instruction_in_rule")

    fm["confirmed"] = True
    fm["confirmed_by"] = confirmed_by
    fm["updated_at"] = _now_iso()

    path = resolve_page_path(corpus, relpath)
    rendered = _dump_page(fm, page.body)

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(rendered)

    return Page(relpath=relpath, frontmatter=fm, body=page.body)
