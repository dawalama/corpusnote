# corpusnote

Local markdown vaults with keyword search, one directory per customer, and token-capped prompt packs.

Each corpus is a folder of markdown pages. Writes stay inside that folder, secrets are scrubbed before they hit disk, and a pack builder assembles a bounded prompt from confirmed rules, handoffs, and search hits. There is no network call and no model call in the library.

Requires Python 3.11+. Runtime dependency is PyYAML.

## Install

```bash
pip install -e .
```

The console script is `corpusnote`.

```bash
pip install -e ".[test]"
pytest
```

## Layout

`open_corpus` creates these three directories when they are absent. New directories are mode `0700`:

```text
markdown_root/{corpus_id}/     pages
qmd_config/{corpus_id}/        qmd config
qmd_cache/{corpus_id}/         qmd cache
```

Page files are written mode `0600`. A corpus id and each path segment must match `^[a-z0-9][a-z0-9._-]{0,80}$` and must not start with a dot. Absolute paths, `..`, and symlinks that resolve outside the corpus root are rejected.

Pages the library writes:

| Path | Kind |
| --- | --- |
| `projects/{slug}/notes/{session_id}.md` | session note, body capped near 3,000 characters |
| `handoffs/{session_id}.md` | full handoff (16,000 characters) |
| `uploads/{slug}.md` | ingested text |
| `rules/{slug}.md` | human rule, unconfirmed until `confirm_rule` |
| `workstreams/{channel_id}.md` | workstream page, moved between corpora |

## Command line

`--root` defaults to the current directory. Markdown lives in `{root}/corpora` unless `--root` itself is named `corpora`.

```bash
corpusnote search --corpus acme "deploy notes"
corpusnote search --corpus acme --root /path/to/vault --limit 8 "deploy notes"
corpusnote get --corpus acme --page projects/acme/notes/sess-1.md
```

`search` prints `score`, relative path, and title. `get` prints the page body. Exit status is `0` on success, `2` when the page is missing, and `1` on other errors.

The CLI search walks markdown in that corpus. It skips `handoffs/` and `inbox/` unless you use the library with `include_handoffs=True`. It also skips pages marked rejected, private, superseded, or draft.

## Library

```python
from pathlib import Path

from corpusnote import (
    Capture,
    Verification,
    assemble_pack,
    capture,
    confirm_rule,
    open_corpus,
    propose_rule,
    read_page,
    search,
    PackRequest,
)

corpus = open_corpus(
    "acme",
    markdown_root=Path("data/corpora"),
    qmd_config=Path("data/qmd-config"),
    qmd_cache=Path("data/qmd-cache"),
)

pages = capture(
    corpus,
    Capture(
        session_id="sess-1",
        project="Acme",
        workstream_id=None,
        goal="Ship the export",
        output_summary="Export writes a zip.",
        verification=Verification(exit_code=0, passed=True),
    ),
)

propose_rule(corpus, slug="export", title="Export", body="Write a zip.", project="Acme")
confirm_rule(corpus, "rules/export.md", confirmed_by="ada")

hits = search(corpus, "export", limit=8)
page = read_page(corpus, hits[0].relpath)

pack = assemble_pack(
    corpus,
    PackRequest(goal="Ship the export", project="Acme", workstream_id=None, parent_session_id="sess-1"),
)
```

`write_page` refuses `kind: rule` and always stores `confirmed: false`. Rules go through `propose_rule`, then `confirm_rule`. A rule body that contains a denylisted instruction phrase is rejected. A pack copies a rule only when it is confirmed, active, human-sourced, and its `project` is empty or matches the request.

`assemble_pack` caps the pack at `budget_tokens` (default 2000, counted as 4 characters per token). It reserves an 800-token floor for search hits when `include_hits` is true. It does not raise when the vault cannot be read; the pack comes back empty with a warning. Dropped sections are listed on `pack.omitted`.

`scrub_text` redacts private keys, database URLs with passwords, bearer tokens, common key prefixes, assignment-style secrets, and any extra strings you pass. Capture, page writes, search queries, and qmd arguments are scrubbed before use. `extra_secrets` stays in memory for that call and is not stored on the pack.

## Search index

The default index is an in-process keyword scan of `*.md` under the corpus root. `set_search_index` replaces it for the process.

`qmd` is optional and is not what the CLI calls. `qmd_update` rewrites that corpus's `index.yml` and runs `qmd update`. `qmd_search` runs `qmd search` with a stripped environment (`PATH`, `HOME`, `LANG`, plus the corpus config and cache dirs), a 20 second timeout, and the corpus config directory as the working directory. A missing binary, timeout, bad JSON, or non-zero exit raises `SearchUnavailable`.

## Errors

| Exception | When |
| --- | --- |
| `RejectedWrite` | Bad id, path or symlink escape, rule written through `write_page`, injection in a rule, or confirming a page that is not a human rule |
| `MissingPage` | Page absent, or the path escapes the corpus root |
| `SearchUnavailable` | qmd cannot run or its output cannot be used |

## License

MIT. See [LICENSE](LICENSE).
