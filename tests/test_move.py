from pathlib import Path
import pytest

from corpusnote import (
    confirm_rule,
    locate_workstream_corpora,
    move_project_pages,
    move_workstream_page,
    open_corpus,
    propose_rule,
    read_page,
    search,
    write_page,
)


@pytest.fixture
def corpora(tmp_path: Path):
    c_gen = open_corpus("general", markdown_root=tmp_path / "corpora", qmd_config=tmp_path / "config-gen", qmd_cache=tmp_path / "cache-gen")
    c_pers = open_corpus("personal", markdown_root=tmp_path / "corpora", qmd_config=tmp_path / "config-pers", qmd_cache=tmp_path / "cache-pers")
    return tmp_path / "corpora", c_gen, c_pers


def test_locate_and_move_workstream_page(corpora):
    corpora_root, c_gen, c_pers = corpora

    # Write a workstream page in general
    ws_file = c_gen.root / "workstreams" / "ch-wallet.md"
    ws_file.parent.mkdir(parents=True, exist_ok=True)
    ws_file.write_text("---\nid: general/workstreams/ch-wallet\ncorpus: general\ntitle: Wallet WS\n---\nWS body", encoding="utf-8")

    # 1. locate_workstream_corpora finds general
    located = locate_workstream_corpora(corpora_root, "ch-wallet")
    assert located == ["general"]

    # 2. move_workstream_page src == dst is a no-op
    res = move_workstream_page(corpora_root, "ch-wallet", "general", "general")
    assert res == "workstreams/ch-wallet.md"
    assert ws_file.is_file()

    # 3. move_workstream_page to personal moves only that file and rewrites id and corpus
    res_move = move_workstream_page(corpora_root, "ch-wallet", "general", "personal")
    assert res_move == "workstreams/ch-wallet.md"
    assert not ws_file.is_file()

    pers_ws = c_pers.root / "workstreams" / "ch-wallet.md"
    assert pers_ws.is_file()
    page = read_page(c_pers, "workstreams/ch-wallet.md")
    assert page.frontmatter["corpus"] == "personal"
    assert page.frontmatter["id"] == "personal/workstreams/ch-wallet"

    # 4. Destination file that already exists is NOT overwritten, source stays
    ws_file.write_text("---\ntitle: Stale WS\n---\nStale", encoding="utf-8")
    blocked = move_workstream_page(corpora_root, "ch-wallet", "general", "personal")
    assert blocked is None
    assert ws_file.is_file()  # Source stays


def test_move_project_pages_intra_links_and_idempotency(corpora):
    corpora_root, c_gen, c_pers = corpora
    proj_id = "proj-quickwallet-uuid"

    # 1. Create collection-level rule in general (must STAY behind)
    propose_rule(c_gen, slug="01-global-rule", title="Global Rule", body="General rule body", project=None)
    confirm_rule(c_gen, "rules/01-global-rule.md", confirmed_by="op")

    # 2. Create project pages in general with internal wikilink
    write_page(
        c_gen,
        "projects/quickwallet/notes/sess-1.md",
        frontmatter={"title": "Note 1", "project": "quickwallet", "project_id": proj_id, "kind": "note", "status": "active"},
        body="Note 1 linking to [[general/projects/quickwallet/notes/sess-2]] and global [[general/rules/01-global-rule]].",
    )
    write_page(
        c_gen,
        "projects/quickwallet/notes/sess-2.md",
        frontmatter={"title": "Note 2", "project": "quickwallet", "project_id": proj_id, "kind": "note", "status": "active"},
        body="Note 2 content.",
    )

    # 3. Execute move_project_pages to personal
    moved = move_project_pages(corpora_root, "personal", proj_id)
    assert len(moved) == 2
    assert "projects/quickwallet/notes/sess-1.md" in moved
    assert "projects/quickwallet/notes/sess-2.md" in moved

    # 4. Global rule stays in general
    assert (c_gen.root / "rules" / "01-global-rule.md").is_file()

    # 5. Project pages are gone from general
    assert not (c_gen.root / "projects" / "quickwallet" / "notes" / "sess-1.md").exists()
    assert not (c_gen.root / "projects" / "quickwallet" / "notes" / "sess-2.md").exists()

    # 6. Project pages exist in personal with rewritten wikilink
    p1 = read_page(c_pers, "projects/quickwallet/notes/sess-1.md")
    assert p1.frontmatter["corpus"] == "personal"
    assert p1.frontmatter["id"] == "personal/projects/quickwallet/notes/sess-1"
    # Internal wikilink rewritten to personal
    assert "[[personal/projects/quickwallet/notes/sess-2]]" in p1.body
    # Global rule link was NOT in moved set, so it stays unresolved to general
    assert "[[general/rules/01-global-rule]]" in p1.body

    # 7. Search of general no longer returns moved project files
    gen_hits = search(c_gen, "linking to")
    assert gen_hits == []

    # 8. Idempotency: second call when files are already in destination
    moved_second = move_project_pages(corpora_root, "personal", proj_id)
    assert moved_second == []
