import os
from pathlib import Path
import pytest

from corpusnote.layout import open_corpus
from corpusnote.qmd_cli import qmd_subprocess_env


@pytest.fixture
def corpora(tmp_path: Path):
    cA = open_corpus("corpus-a", markdown_root=tmp_path / "corpora", qmd_config=tmp_path / "qmd-a-config", qmd_cache=tmp_path / "qmd-a-cache")
    cB = open_corpus("corpus-b", markdown_root=tmp_path / "corpora", qmd_config=tmp_path / "qmd-b-config", qmd_cache=tmp_path / "qmd-b-cache")
    return cA, cB


def test_qmd_subprocess_env_allowlist_and_isolation(corpora, monkeypatch):
    cA, cB = corpora

    # Plant sensitive environment variables in os.environ
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-key")
    monkeypatch.setenv("CORPUS_B_INTERNAL_PATH", str(cB.root))
    monkeypatch.setenv("RANDOM_VARIABLE", "random_value")

    # Ensure baseline allowed variables are set
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOME", "/Users/fakehome")
    monkeypatch.setenv("LANG", "en_US.UTF-8")

    env_a = qmd_subprocess_env(cA)

    # 1. Allow-listed vars present
    assert env_a["PATH"] == "/usr/bin:/bin"
    assert env_a["HOME"] == "/Users/fakehome"
    assert env_a["LANG"] == "en_US.UTF-8"

    # 2. Sensitive vars stripped
    assert "OPENAI_API_KEY" not in env_a
    assert "ANTHROPIC_API_KEY" not in env_a
    assert "CORPUS_B_INTERNAL_PATH" not in env_a
    assert "RANDOM_VARIABLE" not in env_a

    # 3. Corpus A config & cache set
    assert env_a["QMD_CONFIG_DIR"] == str(cA.qmd_config.resolve())
    assert env_a["XDG_CACHE_HOME"] == str(cA.qmd_cache.resolve())

    # 4. Corpus A env does not contain Corpus B's path
    assert str(cB.root) not in str(env_a)
    assert str(cB.qmd_config) not in str(env_a)
    assert str(cB.qmd_cache) not in str(env_a)
