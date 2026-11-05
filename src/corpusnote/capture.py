"""Capture and ingestion workflows for corpusnote."""

from __future__ import annotations

from .layout import slugify_project
from .models import Capture, Corpus, Page
from .pages import write_page
from .scrub import scrub_text


def capture(
    corpus: Corpus,
    item: Capture,
    *,
    extra_secrets: tuple[str, ...] = (),
) -> list[Page]:
    """Capture a completed session into an indexed note and a detailed handoff.

    Deterministic: no LLM call, no qmd update spawned. Overwrites on new accept.
    - Note page: projects/{slug}/notes/{session_id}.md (under 800-token / 3200-char floor).
    - Handoff page: handoffs/{session_id}.md (capped at 16,000 characters).
    """
    scrubbed_goal, _ = scrub_text(item.goal, extra_secrets)
    scrubbed_summary, _ = scrub_text(item.output_summary, extra_secrets)

    v_line = f"Verification: exit_code={item.verification.exit_code}, passed={item.verification.passed}"
    note_header = f"Goal: {scrubbed_goal}\n{v_line}\n\nSummary:\n"

    # Floor limit is 3200 characters (~800 tokens). Leave margin for header and frontmatter.
    avail_note_chars = max(0, 3000 - len(note_header))
    note_body = note_header + scrubbed_summary[:avail_note_chars]

    proj_slug = slugify_project(item.project) if item.project else "unassigned"
    note_relpath = f"projects/{proj_slug}/notes/{item.session_id}.md"

    note_fm = {
        "title": f"Session note {item.session_id}",
        "kind": "note",
        "status": "active",
        "source": "capture",
        "confirmed": False,
        "project": item.project or "",
        "project_id": item.project_id or "",
        "workstream_id": item.workstream_id or "",
        "session_id": item.session_id,
    }

    note_page = write_page(
        corpus,
        note_relpath,
        frontmatter=note_fm,
        body=note_body,
        extra_secrets=extra_secrets,
    )

    # Handoff page: capped at 16,000 characters
    handoff_header = f"Goal: {scrubbed_goal}\n{v_line}\n\nSummary:\n"
    avail_handoff_chars = max(0, 16000 - len(handoff_header))
    handoff_body = handoff_header + scrubbed_summary[:avail_handoff_chars]

    handoff_relpath = f"handoffs/{item.session_id}.md"
    handoff_fm = {
        "title": f"Handoff {item.session_id}",
        "kind": "handoff",
        "status": "active",
        "source": "capture",
        "confirmed": False,
        "project": item.project or "",
        "project_id": item.project_id or "",
        "workstream_id": item.workstream_id or "",
        "session_id": item.session_id,
    }

    handoff_page = write_page(
        corpus,
        handoff_relpath,
        frontmatter=handoff_fm,
        body=handoff_body,
        extra_secrets=extra_secrets,
    )

    return [note_page, handoff_page]


def ingest_text(
    corpus: Corpus,
    *,
    title: str,
    text: str,
    project: str | None,
    project_id: str | None,
    extra_secrets: tuple[str, ...] = (),
) -> Page:
    """Ingest utf-8 markdown or text as an upload note in the corpus."""
    slug = slugify_project(title)
    relpath = f"uploads/{slug}.md"

    fm = {
        "title": title,
        "kind": "upload",
        "status": "active",
        "source": "upload",
        "confirmed": False,
        "project": project or "",
        "project_id": project_id or "",
    }

    return write_page(
        corpus,
        relpath,
        frontmatter=fm,
        body=text,
        extra_secrets=extra_secrets,
    )
