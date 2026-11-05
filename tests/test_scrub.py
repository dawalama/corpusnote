from pathlib import Path
import pytest

from corpusnote import (
    open_corpus,
    read_page,
    write_page,
    scrub_text,
)


@pytest.fixture
def tmp_corpus(tmp_path: Path):
    return open_corpus(
        "scrub-corpus",
        markdown_root=tmp_path / "corpora",
        qmd_config=tmp_path / "qmd_config",
        qmd_cache=tmp_path / "qmd_cache",
    )


def test_scrub_tokens_and_vault_secrets(tmp_corpus):
    vault_token = "my_vault_secret_token_12345"
    openai_token = "sk-abcdefghijklmnopqrstuv1234567890"
    sha40 = "e742b0c1234567890abcdef1234567890abcdef1"

    body = (
        f"Connecting with {vault_token} and OpenAI key {openai_token}.\n"
        f"Git revision baseline is {sha40}."
    )

    page = write_page(
        tmp_corpus,
        "notes/scrubbed.md",
        frontmatter={"kind": "note"},
        body=body,
        extra_secrets=(vault_token,),
    )

    disk_text = (tmp_corpus.root / page.relpath).read_text(encoding="utf-8")
    assert vault_token not in disk_text
    assert openai_token not in disk_text
    assert "[REDACTED]" in disk_text
    assert sha40 in disk_text  # 40-hex SHA must stay!
    assert page.frontmatter["redactions"] >= 2


def test_confirmed_string_false_not_truthy(tmp_corpus):
    # If frontmatter has string "false", Python yaml safe_load parses "false" as str, not bool True
    # write_page always sets bool False
    page = write_page(
        tmp_corpus,
        "notes/bool-check.md",
        frontmatter={"kind": "note", "confirmed": "false"},
        body="testing boolean",
    )
    loaded = read_page(tmp_corpus, "notes/bool-check.md")
    assert loaded.frontmatter["confirmed"] is False
    assert loaded.frontmatter["confirmed"] is not True
