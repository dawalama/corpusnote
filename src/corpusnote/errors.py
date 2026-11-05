"""Custom exception types for corpusnote."""

from __future__ import annotations


class CorpusNoteError(Exception):
    """Base exception for all corpusnote errors."""
    pass


class MissingPage(CorpusNoteError):
    """Raised when a requested page does not exist or escapes root."""

    def __init__(self, corpus_id: str, relpath: str):
        super().__init__(f"missing_page:{corpus_id}:{relpath}")
        self.corpus_id = corpus_id
        self.relpath = relpath


class SearchUnavailable(CorpusNoteError):
    """Raised when search cannot run.

    Reasons:
    qmd_missing | qmd_timeout | qmd_failed | qmd_bad_json | qmd_refused
    """

    def __init__(self, reason: str):
        super().__init__(f"search_unavailable:{reason}")
        self.reason = reason


class RejectedWrite(CorpusNoteError):
    """Raised when a write is rejected.

    Codes:
    path_escape | symlink_escape | bad_id | instruction_in_rule | not_confirmed | bad_kind
    """

    def __init__(self, code: str):
        super().__init__(f"rejected_write:{code}")
        self.code = code
