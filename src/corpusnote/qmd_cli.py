"""qmd CLI adapter for corpusnote.

Restricted subprocess environment, pinned argv, template-enforced config,
and JSON hit parsing for keyword search with qmd.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml

from .errors import SearchUnavailable
from .models import Corpus, SearchHit
from .scrub import scrub_text

_FORBIDDEN_COMMANDS = {"query", "embed", "vsearch", "mcp", "collection", "ls"}
_FORBIDDEN_CONFIG_KEYS = {"update", "models", "global_context"}
_ALLOWED_ENV_VARS = {"PATH", "HOME", "LANG"}


def qmd_subprocess_env(corpus: Corpus) -> dict[str, str]:
    """Build a strictly filtered subprocess environment for qmd execution.

    Never copies arbitrary os.environ. Never exposes API keys.
    Pins QMD_CONFIG_DIR, XDG_CACHE_HOME, and XDG_CONFIG_HOME to the corpus paths.
    """
    env: dict[str, str] = {}
    for key in _ALLOWED_ENV_VARS:
        val = os.environ.get(key)
        if val is not None:
            env[key] = val

    env["QMD_CONFIG_DIR"] = str(corpus.qmd_config.resolve())
    env["XDG_CONFIG_HOME"] = str(corpus.qmd_config.resolve())
    env["XDG_CACHE_HOME"] = str(corpus.qmd_cache.resolve())
    return env


def _render_index_yaml(corpus: Corpus) -> str:
    """Render canonical index.yml with exactly one collection and no hooks."""
    config_data = {
        "collections": {
            corpus.id: {
                "path": str(corpus.root.resolve()),
                "pattern": "**/*.md",
                "ignore": [
                    "handoffs/**",
                    "inbox/**",
                ],
            }
        }
    }
    return yaml.safe_dump(config_data, sort_keys=False)


def _check_config_dict(data: Any, corpus: Corpus) -> None:
    """Validate that parsed config data strictly adheres to single-collection constraints."""
    if not isinstance(data, dict):
        raise SearchUnavailable("qmd_refused")

    # Reject forbidden keys anywhere in the config dict
    for k, v in data.items():
        if k in _FORBIDDEN_CONFIG_KEYS:
            raise SearchUnavailable("qmd_refused")

    collections = data.get("collections")
    if not isinstance(collections, dict):
        raise SearchUnavailable("qmd_refused")

    if len(collections) != 1 or corpus.id not in collections:
        raise SearchUnavailable("qmd_refused")

    coll_conf = collections[corpus.id]
    if not isinstance(coll_conf, dict):
        raise SearchUnavailable("qmd_refused")

    for k in coll_conf:
        if k in _FORBIDDEN_CONFIG_KEYS:
            raise SearchUnavailable("qmd_refused")

    # Validate path matches corpus root
    coll_path = Path(coll_conf.get("path", "")).resolve()
    if coll_path != corpus.root.resolve():
        raise SearchUnavailable("qmd_refused")


def write_and_verify_index_config(corpus: Corpus) -> Path:
    """Atomically rewrite index.yml from canonical template and verify before spawning."""
    corpus.qmd_config.mkdir(parents=True, exist_ok=True)
    target_file = corpus.qmd_config / "index.yml"
    rendered = _render_index_yaml(corpus)

    # Write temp file and atomically rename
    tmp_fd, tmp_path_str = tempfile.mkstemp(dir=corpus.qmd_config, prefix="index_", suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(rendered)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path_str, target_file)
    except Exception as e:
        if os.path.exists(tmp_path_str):
            try:
                os.unlink(tmp_path_str)
            except OSError:
                pass
        raise SearchUnavailable(f"config_write_failed: {e}") from e

    # Read back and verify
    verify_index_config(corpus)
    return target_file


def verify_index_config(corpus: Corpus) -> None:
    """Verify that existing index.yml contains only the single collection and no forbidden keys."""
    target_file = corpus.qmd_config / "index.yml"
    if not target_file.exists():
        write_and_verify_index_config(corpus)
        return

    try:
        with open(target_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        raise SearchUnavailable(f"qmd_refused: invalid_yaml: {e}") from e

    _check_config_dict(data, corpus)


def parse_qmd_search_json(raw_json: str, corpus: Corpus, *, include_handoffs: bool = False) -> list[SearchHit]:
    """Parse JSON output from qmd search.

    Accepts list of hits or a dict with hits/results/items.
    Strips qmd://{corpus_id}/ prefix, resolves path under corpus root.
    """
    try:
        data = json.loads(raw_json)
    except Exception as e:
        raise SearchUnavailable("qmd_bad_json") from e

    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        if "hits" in data and isinstance(data["hits"], list):
            items = data["hits"]
        elif "results" in data and isinstance(data["results"], list):
            items = data["results"]
        elif "items" in data and isinstance(data["items"], list):
            items = data["items"]
        else:
            items = [data]
    else:
        raise SearchUnavailable("qmd_bad_json")

    hits: list[SearchHit] = []
    root_resolved = corpus.root.resolve()

    for item in items:
        if not isinstance(item, dict):
            continue

        raw_path = item.get("file") or item.get("path") or item.get("displayPath")
        if not raw_path or not isinstance(raw_path, str):
            continue

        clean_path = raw_path.strip()
        prefix = f"qmd://{corpus.id}/"
        if clean_path.startswith(prefix):
            clean_path = clean_path[len(prefix):]
        elif clean_path.startswith("qmd://"):
            clean_path = clean_path[6:]

        # Resolve path
        p = Path(clean_path)
        resolved = p.resolve() if p.is_absolute() else (corpus.root / clean_path).resolve()
        if not resolved.is_relative_to(root_resolved):
            continue
        if not resolved.is_file():
            continue

        relpath = resolved.relative_to(root_resolved).as_posix()
        if not include_handoffs and (relpath.startswith("handoffs/") or relpath.startswith("inbox/")):
            continue

        try:
            score = float(item.get("score", 1.0))
        except (ValueError, TypeError):
            score = 1.0

        title = item.get("title") or Path(relpath).stem
        hits.append(SearchHit(relpath=relpath, title=title, score=score))

    return hits


def qmd_search(
    corpus: Corpus,
    query: str,
    *,
    limit: int = 8,
    include_handoffs: bool = False,
    extra_secrets: tuple[str, ...] = (),
    qmd_bin: str = "qmd",
    runner: Any | None = None,
) -> list[SearchHit]:
    """Execute qmd search keyword search inside the corpus config directory.

    Scrubs query, sets cwd to corpus.qmd_config, enforces 20s timeout.
    """
    scrubbed_query, _ = scrub_text(query, extra_secrets)
    if not scrubbed_query.strip():
        return []

    verify_index_config(corpus)

    argv = [qmd_bin, "search", "-n", str(limit), "--json", "--", scrubbed_query]
    cwd = corpus.qmd_config
    env = qmd_subprocess_env(corpus)

    if runner is not None:
        res = runner(argv, cwd=cwd, env=env, timeout=20.0)
    else:
        try:
            res = subprocess.run(
                argv,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=20.0,
            )
        except subprocess.TimeoutExpired as e:
            raise SearchUnavailable("timeout") from e
        except FileNotFoundError as e:
            raise SearchUnavailable("qmd_missing") from e
        except Exception as e:
            raise SearchUnavailable(f"qmd_error: {e}") from e

    if res.returncode != 0:
        raise SearchUnavailable(f"qmd_exit_{res.returncode}")

    hits = parse_qmd_search_json(res.stdout, corpus, include_handoffs=include_handoffs)
    return hits[:limit]


def qmd_update(
    corpus: Corpus,
    *,
    qmd_bin: str = "qmd",
    runner: Any | None = None,
) -> None:
    """Rewrite index.yml from template and execute qmd update."""
    write_and_verify_index_config(corpus)

    argv = [qmd_bin, "update"]
    cwd = corpus.qmd_config
    env = qmd_subprocess_env(corpus)

    if runner is not None:
        res = runner(argv, cwd=cwd, env=env, timeout=20.0)
    else:
        try:
            res = subprocess.run(
                argv,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=20.0,
            )
        except subprocess.TimeoutExpired as e:
            raise SearchUnavailable("timeout") from e
        except FileNotFoundError as e:
            raise SearchUnavailable("qmd_missing") from e
        except Exception as e:
            raise SearchUnavailable(f"qmd_error: {e}") from e

    if res.returncode != 0:
        raise SearchUnavailable(f"qmd_exit_{res.returncode}")


# Alias update_index as specified in the plan
update_index = qmd_update

