"""corpusnote package entry points."""

from .errors import (
    CorpusNoteError,
    MissingPage,
    RejectedWrite,
    SearchUnavailable,
)
from .layout import open_corpus, resolve_page_path, slugify_project
from .models import (
    Capture,
    Corpus,
    CorpusId,
    Pack,
    PackRequest,
    PackSection,
    Page,
    SearchHit,
    Verification,
)
from .pages import confirm_rule, propose_rule, read_page, write_page
from .pack import assemble_pack
from .qmd_cli import (
    qmd_search,
    qmd_subprocess_env,
    qmd_update,
    update_index,
    write_and_verify_index_config,
)
from .scrub import scrub_text
from .search import FakeIndex, SearchIndex, get_search_index, search, set_search_index

__all__ = [
    "CorpusNoteError",
    "MissingPage",
    "RejectedWrite",
    "SearchUnavailable",
    "Corpus",
    "CorpusId",
    "Page",
    "SearchHit",
    "Verification",
    "Capture",
    "PackRequest",
    "PackSection",
    "Pack",
    "open_corpus",
    "resolve_page_path",
    "slugify_project",
    "read_page",
    "write_page",
    "propose_rule",
    "confirm_rule",
    "scrub_text",
    "assemble_pack",
    "search",
    "SearchIndex",
    "FakeIndex",
    "set_search_index",
    "get_search_index",
    "qmd_subprocess_env",
    "qmd_search",
    "qmd_update",
    "update_index",
    "write_and_verify_index_config",
]
