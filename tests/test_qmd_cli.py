import json
from pathlib import Path
import shutil
import pytest

from corpusnote.errors import SearchUnavailable
from corpusnote.layout import open_corpus
from corpusnote.qmd_cli import (
    parse_qmd_search_json,
    qmd_search,
    qmd_update,
    write_and_verify_index_config,
)


class SubprocessFake:
    """Mock runner to capture argv, cwd, env, and return canned responses."""

    def __init__(self, stdout: str = "[]", returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode
        self.calls: list[dict] = []

    def __call__(self, argv, cwd, env, timeout):
        self.calls.append({
            "argv": argv,
            "cwd": cwd,
            "env": env,
            "timeout": timeout,
        })

        class Result:
            pass

        r = Result()
        r.stdout = self.stdout
        r.returncode = self.returncode
        return r


@pytest.fixture
def corpus(tmp_path: Path):
    return open_corpus(
        "sugarwork",
        markdown_root=tmp_path / "corpora",
        qmd_config=tmp_path / "config",
        qmd_cache=tmp_path / "cache",
    )


def test_qmd_search_argv_cwd_and_scrubbing(corpus):
    vault_secret = "vault-super-secret-xyz987"
    git_sha = "0123456789abcdef0123456789abcdef01234567"
    raw_query = f"find bug near {git_sha} with secret {vault_secret}"

    fake = SubprocessFake(stdout="[]")
    hits = qmd_search(
        corpus,
        raw_query,
        limit=5,
        extra_secrets=(vault_secret,),
        runner=fake,
    )
    assert hits == []
    assert len(fake.calls) == 1
    call = fake.calls[0]

    # Verify cwd is the per-corpus config directory
    assert call["cwd"] == corpus.qmd_config

    argv = call["argv"]
    # Pinned argv: qmd search -n <limit> --json -- <scrubbed_query>
    assert argv[0] == "qmd"
    assert argv[1] == "search"
    assert argv[2] == "-n"
    assert argv[3] == "5"
    assert argv[4] == "--json"
    assert argv[5] == "--"

    scrubbed_arg = argv[6]
    # Secret is redacted in spawned argv
    assert vault_secret not in scrubbed_arg
    assert "[REDACTED]" in scrubbed_arg
    # 40-hex SHA is not redacted
    assert git_sha in scrubbed_arg
    # Raw goal is not an argv element
    assert raw_query not in argv


def test_index_template_rewrite_and_refusal(corpus):
    config_file = corpus.qmd_config / "index.yml"
    corpus.qmd_config.mkdir(parents=True, exist_ok=True)

    # 1. Plant forbidden 'update:' key
    malicious_yaml = f"""collections:
  sugarwork:
    path: {corpus.root.resolve()}
    pattern: "**/*.md"
    update: "echo pwned"
"""
    config_file.write_text(malicious_yaml, encoding="utf-8")

    fake = SubprocessFake(stdout="updated", returncode=0)
    # qmd_update rewrites the template before spawn
    qmd_update(corpus, runner=fake)

    # The planted key must be gone
    new_content = config_file.read_text(encoding="utf-8")
    assert "echo pwned" not in new_content
    assert "update:" not in new_content

    # 2. If the malicious config did not stick / was refused (e.g. read back has forbidden key)
    # Plant forbidden 'models' key and make write_and_verify fail to clear it
    config_file.write_text(malicious_yaml, encoding="utf-8")
    # If config has models or update and verify is called:
    with pytest.raises(SearchUnavailable) as exc:
        # Simulate search where file is tampered
        qmd_search(corpus, "query", runner=fake)
    assert "qmd_refused" in str(exc.value)

    # 3. Plant second collection: refused
    multi_coll_yaml = f"""collections:
  sugarwork:
    path: {corpus.root.resolve()}
  quillmeetings:
    path: {corpus.root.resolve()}
"""
    config_file.write_text(multi_coll_yaml, encoding="utf-8")
    with pytest.raises(SearchUnavailable) as exc:
        qmd_search(corpus, "query", runner=fake)
    assert "qmd_refused" in str(exc.value)


def test_json_hit_parsing(corpus):
    # Create test pages in corpus
    note_file = corpus.root / "projects" / "p1" / "notes" / "sess-1.md"
    note_file.parent.mkdir(parents=True, exist_ok=True)
    note_file.write_text("---\ntitle: Note One\n---\nBody", encoding="utf-8")

    handoff_file = corpus.root / "handoffs" / "sess-1.md"
    handoff_file.parent.mkdir(parents=True, exist_ok=True)
    handoff_file.write_text("---\ntitle: Handoff One\n---\nBody", encoding="utf-8")

    raw_json = json.dumps([
        {"file": f"qmd://sugarwork/projects/p1/notes/sess-1.md", "score": 2.5, "title": "Note One"},
        {"path": "handoffs/sess-1.md", "score": 1.8},
        {"displayPath": str(corpus.root.parent / "secret.md"), "score": 3.0},  # outside path
    ])

    # Case 1: include_handoffs=False drops handoff
    hits = parse_qmd_search_json(raw_json, corpus, include_handoffs=False)
    assert len(hits) == 1
    assert hits[0].relpath == "projects/p1/notes/sess-1.md"
    assert hits[0].score == 2.5
    assert hits[0].title == "Note One"

    # Case 2: include_handoffs=True includes handoff
    hits_all = parse_qmd_search_json(raw_json, corpus, include_handoffs=True)
    assert len(hits_all) == 2
    assert {h.relpath for h in hits_all} == {"projects/p1/notes/sess-1.md", "handoffs/sess-1.md"}

    # Bad json raises SearchUnavailable("qmd_bad_json")
    with pytest.raises(SearchUnavailable) as exc:
        parse_qmd_search_json("not json", corpus)
    assert "qmd_bad_json" in str(exc.value)


@pytest.mark.skipif(shutil.which("qmd") is None, reason="qmd binary not installed on PATH")
def test_real_qmd_integration_if_installed(tmp_path: Path):
    cA = open_corpus("corp-a", markdown_root=tmp_path / "corpora", qmd_config=tmp_path / "config-a", qmd_cache=tmp_path / "cache-a")
    docA = cA.root / "doc.md"
    docA.write_text("# Corp A Secret Information\nUniqueKeywordAlpha", encoding="utf-8")

    cB = open_corpus("corp-b", markdown_root=tmp_path / "corpora", qmd_config=tmp_path / "config-b", qmd_cache=tmp_path / "cache-b")
    docB = cB.root / "doc.md"
    docB.write_text("# Corp B Secret Information\nUniqueKeywordBeta", encoding="utf-8")

    qmd_update(cA)
    qmd_update(cB)

    hits_a = qmd_search(cA, "UniqueKeywordAlpha")
    assert any("doc.md" in h.relpath for h in hits_a)
    hits_a_cross = qmd_search(cA, "UniqueKeywordBeta")
    assert hits_a_cross == []
