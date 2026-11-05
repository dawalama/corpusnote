from pathlib import Path
import pytest

from corpusnote import (
    PackRequest,
    assemble_pack,
    confirm_rule,
    open_corpus,
    propose_rule,
    write_page,
)


@pytest.fixture
def corpus(tmp_path: Path):
    return open_corpus("budget-corpus", markdown_root=tmp_path / "corpora", qmd_config=tmp_path / "config", qmd_cache=tmp_path / "cache")


def test_hard_budget_cap_and_rendered_count(corpus):
    # Propose multiple large rules
    for i in range(5):
        slug = f"{i:02d}-rule"
        # 1500 chars each = ~375 tokens each
        propose_rule(corpus, slug=slug, title=f"Rule {i}", body=f"Rule body {i} " + ("x" * 1200), project=None)
        confirm_rule(corpus, f"rules/{slug}.md", confirmed_by="op")

    # Add an upload of 5000 chars
    unique_sentence = "The zebra danced on the moon under neon lights."
    write_page(
        corpus,
        "uploads/large-doc.md",
        frontmatter={"title": "Large Doc", "kind": "upload", "status": "active"},
        body=f"Intro text.\n{unique_sentence}\n" + ("data padding " * 400),
    )

    req = PackRequest(goal="zebra danced on the moon", project=None, workstream_id=None, parent_session_id=None)
    pack = assemble_pack(corpus, req)

    # 1. Hard cap: rendered pack <= 2000 tokens
    rendered_tokens = len(pack.text) // 4
    assert rendered_tokens <= 2000
    assert pack.tokens == rendered_tokens

    # 2. Rules dropped whole by relpath sort rather than sliced
    # 00-rule and 01-rule should be present, tail rules dropped whole
    assert "Rule 0" in pack.text
    assert any("budget_dropped:rule:" in item for item in pack.omitted)

    # 3. Long upload contributes an excerpt containing the unique sentence
    assert unique_sentence in pack.text
    assert any("budget_excerpt:uploads/large-doc.md" in item for item in pack.omitted)


def test_rules_dropped_whole_not_sliced(corpus):
    # Rule body with an instruction that would be inverted if sliced
    body = "Do not deploy on Friday without explicit staff approval under any circumstance."
    propose_rule(corpus, slug="00-friday-rule", title="Friday Deployment", body=body, project=None)
    confirm_rule(corpus, "rules/00-friday-rule.md", confirmed_by="op")

    # Add a huge previous rule that consumes the budget
    propose_rule(corpus, slug="01-big-rule", title="Big Rule", body="Standard rule " + ("y" * 8000), project=None)
    confirm_rule(corpus, "rules/01-big-rule.md", confirmed_by="op")

    req = PackRequest(goal="test", project=None, workstream_id=None, parent_session_id=None)
    pack = assemble_pack(corpus, req)

    # Big rule must be dropped whole, never sliced
    assert "budget_dropped:rule:rules/01-big-rule.md" in pack.omitted
    # Friday rule must be intact
    assert "Do not deploy on Friday" in pack.text
    assert len(pack.text) // 4 <= 2000
