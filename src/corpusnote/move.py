"""Cross-corpus file movement and link rewriting for corpusnote."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import yaml

from .pages import _dump_page, read_page

_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:\|([^\]]+))?\]\]")


def locate_workstream_corpora(corpora_root: Path, channel_id: str) -> list[str]:
    """Find all corpus IDs under corpora_root that contain workstreams/{channel_id}.md."""
    if not corpora_root.is_dir():
        return []

    found: list[str] = []
    rel = f"workstreams/{channel_id}.md"

    for entry in sorted(corpora_root.iterdir()):
        if entry.is_dir():
            if (entry / rel).is_file():
                found.append(entry.name)

    return found


def move_workstream_page(
    corpora_root: Path,
    channel_id: str,
    src_corpus_id: str,
    dst_corpus_id: str,
) -> str | None:
    """Move workstreams/{channel_id}.md from src_corpus_id to dst_corpus_id.

    - If src == dst, returns relpath as no-op.
    - If destination file already exists, does NOT overwrite and returns None.
    - Updates frontmatter corpus: and id:.
    """
    relpath = f"workstreams/{channel_id}.md"
    src_file = corpora_root / src_corpus_id / relpath
    dst_file = corpora_root / dst_corpus_id / relpath

    if src_corpus_id == dst_corpus_id:
        return relpath if src_file.is_file() else None

    if not src_file.is_file():
        return None

    if dst_file.is_file():
        # Destination file already exists: do not overwrite!
        return None

    # Read source file
    text = src_file.read_text(encoding="utf-8")
    fm = {}
    body = text
    if text.startswith("---\n"):
        parts = text.split("---\n", 2)
        if len(parts) >= 3:
            fm = yaml.safe_load(parts[1]) or {}
            body = parts[2]

    # Update permalink and corpus
    fm["corpus"] = dst_corpus_id
    fm["id"] = f"{dst_corpus_id}/workstreams/{channel_id}"

    dst_file.parent.mkdir(parents=True, exist_ok=True)
    rendered = _dump_page(fm, body)

    fd = os.open(dst_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(rendered)

    # Remove source file
    src_file.unlink()
    return relpath


def move_project_pages(
    corpora_root: Path,
    dst_corpus_id: str,
    project_id: str,
) -> list[str]:
    """Scan all sibling corpora under corpora_root and move pages matching project_id to dst_corpus_id.

    - Does not move workstreams/.
    - Does not overwrite existing destination files.
    - Rewrites corpus: and id: (permalink).
    - Rewrites intra-set wikilinks among the moved pages.
    - Returns sorted list of moved relative paths.
    """
    if not corpora_root.is_dir() or not project_id:
        return []

    dst_root = corpora_root / dst_corpus_id
    dst_root.mkdir(parents=True, exist_ok=True)

    moved_paths: list[str] = []
    # Map old_id -> new_id for intra-set wikilink rewrite
    id_rewrite_map: dict[str, str] = {}
    pages_to_relocate: list[tuple[str, Path, str, dict, str]] = []

    for corpus_dir in sorted(corpora_root.iterdir()):
        if not corpus_dir.is_dir():
            continue

        src_id = corpus_dir.name
        if src_id == dst_corpus_id:
            # Files already at destination don't need relocation, but could participate in link maps
            continue

        for file_path in sorted(corpus_dir.rglob("*.md")):
            rel = file_path.relative_to(corpus_dir).as_posix()
            # Never move workstreams/ from this function
            if rel.startswith("workstreams/"):
                continue

            try:
                content = file_path.read_text(encoding="utf-8")
                if not content.startswith("---\n"):
                    continue
                parts = content.split("---\n", 2)
                if len(parts) < 3:
                    continue
                fm = yaml.safe_load(parts[1]) or {}
                body = parts[2]
            except Exception:
                continue

            if fm.get("project_id") != project_id:
                continue

            # Check if destination file already exists
            dst_target = dst_root / rel
            if dst_target.exists():
                # Do not overwrite existing destination file; leave source in place
                continue

            stem = rel[:-3] if rel.endswith(".md") else rel
            old_id = fm.get("id") or f"{src_id}/{stem}"
            new_id = f"{dst_corpus_id}/{stem}"

            id_rewrite_map[old_id] = new_id
            pages_to_relocate.append((src_id, file_path, rel, fm, body))

    # Move each page and write to destination
    written_pages: list[tuple[Path, dict, str]] = []
    for src_id, file_path, rel, fm, body in pages_to_relocate:
        dst_target = dst_root / rel
        dst_target.parent.mkdir(parents=True, exist_ok=True)

        stem = rel[:-3] if rel.endswith(".md") else rel
        fm["corpus"] = dst_corpus_id
        fm["id"] = f"{dst_corpus_id}/{stem}"

        fd = os.open(dst_target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(_dump_page(fm, body))

        # Unlink source file
        try:
            file_path.unlink()
        except OSError:
            pass

        moved_paths.append(rel)
        written_pages.append((dst_target, fm, body))

    # Intra-set wikilink rewrite
    if id_rewrite_map:
        for dst_target, fm, body in written_pages:
            changed = False

            def _replace_link(match: re.Match) -> str:
                nonlocal changed
                target_id = match.group(1).strip()
                label = match.group(2)
                if target_id in id_rewrite_map:
                    changed = True
                    new_target = id_rewrite_map[target_id]
                    return f"[[{new_target}|{label}]]" if label else f"[[{new_target}]]"
                return match.group(0)

            new_body = _WIKILINK_RE.sub(_replace_link, body)
            if changed:
                fm["checksum"] = hashlib.sha256(new_body.encode("utf-8")).hexdigest()
                dst_target.write_text(_dump_page(fm, new_body), encoding="utf-8")

    return sorted(moved_paths)
