"""Data structures for corpusnote."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

CorpusId = str


@dataclass(frozen=True)
class Corpus:
    id: CorpusId
    root: Path          # markdown directory, corpora/{id}
    qmd_config: Path    # per-corpus config dir, qmd/{id}/config
    qmd_cache: Path     # per-corpus cache dir, qmd/{id}/cache


@dataclass
class Page:
    relpath: str
    frontmatter: dict
    body: str


@dataclass
class SearchHit:
    relpath: str
    title: str
    score: float


@dataclass
class Verification:
    exit_code: int | None
    passed: bool | None     # True only when exit_code == 0; None when no row


@dataclass
class Capture:
    session_id: str
    project: str
    workstream_id: str | None
    goal: str
    output_summary: str
    verification: Verification
    project_id: str | None = None


@dataclass
class PackRequest:
    goal: str                      # scrubbed before this object is built; scrubbed again before qmd
    project: str | None
    workstream_id: str | None
    parent_session_id: str | None
    budget_tokens: int = 2000
    include_hits: bool = True      # False for is_local; the hit floor is not reserved then
    extra_secrets: tuple[str, ...] = ()  # in memory for this call; never copied onto Pack


@dataclass
class PackSection:
    name: str               # citations | rules | handoff | workstream | hits | hop
    text: str
    tokens: int
    pages: list[str] = field(default_factory=list)


@dataclass
class Pack:
    sections: list[PackSection]
    text: str
    tokens: int
    omitted: list[str] = field(default_factory=list)      # stable codes
    warnings: list[str] = field(default_factory=list)
