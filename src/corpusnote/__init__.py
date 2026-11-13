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
    CompileResult,
    CompiledClaim,
    Corpus,
    CorpusId,
    KnowledgeDigest,
    Pack,
    PackRequest,
    PackSection,
    Page,
    SearchHit,
    TopicClash,
    Verification,
)
from .capture import capture, ingest_text
from .move import locate_workstream_corpora, move_project_pages, move_workstream_page
from .pages import confirm_rule, propose_rule, read_page, write_page
from .pack import assemble_pack
from .state import (
    compile_capture,
    compile_claim,
    deidentify_general_statement,
    digest_compiled_state,
    get_compiled_view,
    infer_topic,
    read_compiled_claims,
    render_compiled_markdown,
)
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
    "CompiledClaim",
    "CompileResult",
    "TopicClash",
    "KnowledgeDigest",
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
    "compile_claim",
    "compile_capture",
    "read_compiled_claims",
    "render_compiled_markdown",
    "get_compiled_view",
    "digest_compiled_state",
    "infer_topic",
    "deidentify_general_statement",
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
    "capture",
    "ingest_text",
    "move_project_pages",
    "locate_workstream_corpora",
    "move_workstream_page",
]
