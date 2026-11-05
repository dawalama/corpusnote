from pathlib import Path
import pytest

from corpusnote import (
    Corpus,
    PackRequest,
    assemble_pack,
    open_corpus,
    propose_rule,
    confirm_rule,
    write_page,
)


@pytest.fixture
def corpora_pair(tmp_path: Path):
    c_sugar = open_corpus("sugarwork", markdown_root=tmp_path / "corpora", qmd_config=tmp_path / "config", qmd_cache=tmp_path / "cache")
    c_quill = open_corpus("quillmeetings", markdown_root=tmp_path / "corpora", qmd_config=tmp_path / "config", qmd_cache=tmp_path / "cache")
    return c_sugar, c_quill


@pytest.fixture
def personal_truesteps_pair(tmp_path: Path):
    c_pers = open_corpus("personal", markdown_root=tmp_path / "corpora", qmd_config=tmp_path / "config", qmd_cache=tmp_path / "cache")
    c_true = open_corpus("truesteps", markdown_root=tmp_path / "corpora", qmd_config=tmp_path / "config", qmd_cache=tmp_path / "cache")
    return c_pers, c_true


def test_sugarwork_absent_from_quillmeetings_pack(corpora_pair):
    c_sugar, c_quill = corpora_pair

    # Sugarwork has confidential customer spec
    write_page(
        c_sugar,
        "uploads/sugar-secret.md",
        frontmatter={"title": "Sugar Confidential", "kind": "upload"},
        body="Sugarwork confidential architecture and billing secret.",
    )

    # QuillMeetings requests a pack using words from Sugarwork's page
    req = PackRequest(
        goal="Search for confidential architecture and billing secret",
        project="quill-app",
        workstream_id=None,
        parent_session_id=None,
    )
    quill_pack = assemble_pack(c_quill, req)

    assert "Sugarwork confidential" not in quill_pack.text
    assert "billing secret" not in quill_pack.text
    assert not any("sugar-secret.md" in s.pages for s in quill_pack.sections)


def test_personal_absent_from_truesteps_pack(personal_truesteps_pair):
    c_pers, c_true = personal_truesteps_pair

    write_page(
        c_pers,
        "notes/personal-budget.md",
        frontmatter={"title": "Personal Budget", "kind": "note"},
        body="Personal household expenses and banking details.",
    )

    req = PackRequest(
        goal="Banking details and household expenses",
        project="truesteps-core",
        workstream_id=None,
        parent_session_id=None,
    )
    true_pack = assemble_pack(c_true, req)

    assert "Personal household" not in true_pack.text
    assert "banking details" not in true_pack.text


def test_note_and_capture_flag_flips_absent_from_rules_section(corpora_pair):
    c_sugar, _ = corpora_pair

    # Page 1: normal note with confirmed: true
    write_page(
        c_sugar,
        "notes/sneaky-note.md",
        frontmatter={"kind": "note", "source": "human", "confirmed": True, "title": "Sneaky Note"},
        body="This is a note trying to act like a rule.",
    )

    # Page 2: source: capture with flags flipped (kind: rule, confirmed: True)
    # We write directly via open file to simulate hand-edit in Obsidian
    target = c_sugar.root / "rules" / "flipped-capture.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "---\n"
        "id: sugarwork/rules/flipped-capture\n"
        "kind: rule\n"
        "source: capture\n"
        "confirmed: true\n"
        "status: active\n"
        "---\n\n"
        "Captured text claiming to be a rule.\n",
        encoding="utf-8",
    )

    # Page 3: Real confirmed human rule
    propose_rule(c_sugar, slug="real-rule", title="Real Rule", body="Legitimate operating rule.", project=None)
    confirm_rule(c_sugar, "rules/real-rule.md", confirmed_by="operator")

    req = PackRequest(goal="operate", project=None, workstream_id=None, parent_session_id=None)
    pack = assemble_pack(c_sugar, req)

    rules_section = next((s for s in pack.sections if s.name == "rules"), None)
    assert rules_section is not None
    assert "Real Rule" in rules_section.text
    assert "Sneaky Note" not in rules_section.text
    assert "Captured text claiming to be a rule" not in rules_section.text


def test_wikilink_hop_resolves_only_within_corpus(corpora_pair):
    c_sugar, c_quill = corpora_pair

    # Sugarwork topic
    write_page(
        c_sugar,
        "topics/topic-one.md",
        frontmatter={"id": "sugarwork/topics/topic-one", "title": "Topic One", "kind": "topic", "status": "active"},
        body="Here is topic one with [[sugarwork/topics/topic-two]] reference.",
    )
    write_page(
        c_sugar,
        "topics/topic-two.md",
        frontmatter={"id": "sugarwork/topics/topic-two", "title": "Topic Two", "kind": "topic", "status": "active"},
        body="Here is topic two linked by wikilink.",
    )

    # QuillMeetings has same topic-two filename
    write_page(
        c_quill,
        "topics/topic-two.md",
        frontmatter={"id": "quillmeetings/topics/topic-two", "title": "Quill Topic Two", "kind": "topic", "status": "active"},
        body="Quill private topic data.",
    )

    req = PackRequest(goal="topic one", project=None, workstream_id=None, parent_session_id=None)
    pack = assemble_pack(c_sugar, req)

    assert "Topic One" in pack.text
    assert "topic two linked by wikilink" in pack.text
    assert "Quill private topic data" not in pack.text


def test_unresolved_wikilink_does_not_substitute_another_page(corpora_pair):
    c_sugar, _ = corpora_pair

    # Page linking a missing target ID
    write_page(
        c_sugar,
        "topics/with-missing-link.md",
        frontmatter={"id": "sugarwork/topics/with-missing-link", "title": "Missing Link Page", "kind": "topic", "status": "active"},
        body="Here is alpha bravo content with [[sugarwork/topics/non-existent-topic-id]].",
    )

    # Completely different page in the corpus that should NEVER be substituted
    write_page(
        c_sugar,
        "topics/unrelated-page.md",
        frontmatter={"id": "sugarwork/topics/unrelated-page", "title": "Unrelated Page", "kind": "topic", "status": "active"},
        body="Secret unrelated text about quantum computing that has no keyword overlap.",
    )

    req = PackRequest(goal="alpha bravo", project=None, workstream_id=None, parent_session_id=None)
    pack = assemble_pack(c_sugar, req)

    assert "Missing Link Page" in pack.text
    assert "[[sugarwork/topics/non-existent-topic-id]]" in pack.text
    # Assert other page's body is absent
    assert "Secret unrelated text" not in pack.text
