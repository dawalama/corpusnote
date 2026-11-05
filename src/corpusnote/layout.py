"""Layout, path jail, and path utilities for corpusnote."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from .errors import RejectedWrite
from .models import Corpus, CorpusId

_SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")


def validate_segment(segment: str) -> None:
    """Validate a single path segment."""
    if not segment or not _SEGMENT_RE.match(segment) or segment.startswith("."):
        raise RejectedWrite("bad_id")


def validate_corpus_id(corpus_id: str) -> None:
    """Validate corpus ID (matches single path segment regex)."""
    if not corpus_id or not _SEGMENT_RE.match(corpus_id) or corpus_id.startswith("."):
        raise RejectedWrite("bad_id")


def validate_relpath(relpath: str) -> list[str]:
    """Validate relative path segments, disallowing . and .. and special chars."""
    if not relpath or relpath.startswith("/") or relpath.startswith("\\"):
        raise RejectedWrite("path_escape")

    # Normalize forward slashes
    parts = Path(relpath).parts
    if not parts:
        raise RejectedWrite("bad_id")

    for part in parts:
        if part in (".", "..") or part.startswith("."):
            raise RejectedWrite("path_escape")
        # If it ends with .md, validate the stem
        stem = part[:-3] if part.endswith(".md") else part
        if not stem or not _SEGMENT_RE.match(stem) or stem.startswith("."):
            raise RejectedWrite("bad_id")

    return list(parts)


def resolve_page_path(corpus: Corpus, relpath: str, *, must_exist: bool = False) -> Path:
    """Resolve and enforce path jail under corpus.root."""
    validate_relpath(relpath)
    root = corpus.root.resolve()

    target = (corpus.root / relpath)

    # Check symlinks resolving outside root first
    if target.is_symlink():
        try:
            real_target = Path(os.path.realpath(target)).resolve()
            real_target.relative_to(root)
        except ValueError:
            raise RejectedWrite("symlink_escape")

    # Check lexical path components
    try:
        resolved = target.resolve()
    except OSError:
        raise RejectedWrite("path_escape")

    # Check that resolved stays under root
    try:
        resolved.relative_to(root)
    except ValueError:
        raise RejectedWrite("path_escape")

    return target


def slugify_project(name: str) -> str:
    """Slugify project name for directories.

    Project name lowercased, with characters outside [a-z0-9._-] replaced by -,
    collapsed, and truncated to 80 characters.
    """
    if not name:
        return "project"
    cleaned = re.sub(r"[^a-z0-9._-]+", "-", name.lower()).strip("-")
    cleaned = re.sub(r"-+", "-", cleaned)
    if not cleaned:
        cleaned = "project"
    slug = cleaned[:80]
    return slug


def open_corpus(
    corpus_id: str,
    *,
    markdown_root: Path,
    qmd_config: Path,
    qmd_cache: Path,
) -> Corpus:
    """Open or initialize a corpus.

    open_corpus creates the markdown directory only when that live path is absent.
    It does not recreate a directory that was quarantined.
    """
    validate_corpus_id(corpus_id)

    root = (markdown_root / corpus_id)
    config_dir = (qmd_config / corpus_id)
    cache_dir = (qmd_cache / corpus_id)

    if not root.exists():
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
    config_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    cache_dir.mkdir(parents=True, mode=0o700, exist_ok=True)

    return Corpus(
        id=corpus_id,
        root=root,
        qmd_config=config_dir,
        qmd_cache=cache_dir,
    )
