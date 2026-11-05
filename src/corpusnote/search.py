"""Search protocol and FakeIndex for testing and offline retrieval."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

from .errors import SearchUnavailable
from .layout import resolve_page_path
from .models import Corpus, SearchHit
from .pages import read_page
from .scrub import scrub_text


class SearchIndex(Protocol):
    def search(
        self,
        corpus: Corpus,
        query: str,
        *,
        limit: int = 8,
        include_handoffs: bool = False,
        extra_secrets: tuple[str, ...] = (),
    ) -> list[SearchHit]: ...


class FakeIndex:
    """In-memory or filesystem keyword index for testing."""

    def __init__(self, hits_by_corpus: dict[str, list[SearchHit]] | None = None):
        self.hits_by_corpus = hits_by_corpus or {}

    def search(
        self,
        corpus: Corpus,
        query: str,
        *,
        limit: int = 8,
        include_handoffs: bool = False,
        extra_secrets: tuple[str, ...] = (),
    ) -> list[SearchHit]:
        scrubbed_query, _ = scrub_text(query, extra_secrets)
        if not scrubbed_query.strip():
            return []

        # If explicit hits provided for this corpus, filter by query
        if corpus.id in self.hits_by_corpus:
            results = []
            for hit in self.hits_by_corpus[corpus.id]:
                if not include_handoffs and (hit.relpath.startswith("handoffs/") or hit.relpath.startswith("inbox/")):
                    continue
                results.append(hit)
            return results[:limit]

        # Otherwise do a filesystem keyword scan inside corpus.root only
        if not corpus.root.is_dir():
            return []

        q_terms = [t.lower() for t in scrubbed_query.split() if len(t) >= 2]
        if not q_terms:
            q_terms = [scrubbed_query.lower()]

        hits: list[tuple[float, SearchHit]] = []
        for path in corpus.root.rglob("*.md"):
            try:
                rel = path.relative_to(corpus.root).as_posix()
            except ValueError:
                continue

            if not include_handoffs and (rel.startswith("handoffs/") or rel.startswith("inbox/")):
                continue

            try:
                page = read_page(corpus, rel)
            except Exception:
                continue

            # Skip rejected, superseded, draft, private
            fm = page.frontmatter
            if fm.get("rejected") is True or fm.get("private") is True:
                continue
            if fm.get("status") in ("superseded", "draft"):
                continue

            text_to_search = f"{fm.get('title', '')} {page.body}".lower()
            score = 0.0
            for term in q_terms:
                if term in text_to_search:
                    score += 1.0

            if score > 0:
                hits.append((score, SearchHit(relpath=rel, title=fm.get("title", path.stem), score=score)))

        hits.sort(key=lambda x: x[0], reverse=True)
        return [h[1] for h in hits[:limit]]


_DEFAULT_SEARCH_INDEX: SearchIndex = FakeIndex()


def set_search_index(index: SearchIndex) -> None:
    global _DEFAULT_SEARCH_INDEX
    _DEFAULT_SEARCH_INDEX = index


def get_search_index() -> SearchIndex:
    return _DEFAULT_SEARCH_INDEX


def search(
    corpus: Corpus,
    query: str,
    *,
    limit: int = 8,
    include_handoffs: bool = False,
    extra_secrets: tuple[str, ...] = (),
) -> list[SearchHit]:
    """Search the corpus using the configured search index."""
    scrubbed_query, _ = scrub_text(query, extra_secrets)
    return get_search_index().search(
        corpus,
        scrubbed_query,
        limit=limit,
        include_handoffs=include_handoffs,
        extra_secrets=extra_secrets,
    )
