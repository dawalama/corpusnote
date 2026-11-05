"""Command-line interface for corpusnote."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .errors import MissingPage, SearchUnavailable
from .layout import open_corpus
from .pages import read_page
from .search import search


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="corpusnote", description="Local markdown vault knowledge management")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Search command
    search_parser = subparsers.add_parser("search", help="Search pages within a corpus")
    search_parser.add_argument("--corpus", required=True, help="Corpus collection ID")
    search_parser.add_argument("--root", default=".", help="Root directory holding corpora")
    search_parser.add_argument("--limit", type=int, default=8, help="Max results to return")
    search_parser.add_argument("query", help="Keyword search query")

    # Get command
    get_parser = subparsers.add_parser("get", help="Retrieve full page content")
    get_parser.add_argument("--corpus", required=True, help="Corpus collection ID")
    get_parser.add_argument("--page", required=True, help="Relative page path (e.g. notes/sess-1.md)")
    get_parser.add_argument("--root", default=".", help="Root directory holding corpora")

    args = parser.parse_args(argv)

    root_path = Path(args.root).resolve()
    # If root_path points directly to corpora directory or contains it:
    markdown_root = root_path if root_path.name == "corpora" else (root_path / "corpora")
    qmd_config = root_path / "qmd" / args.corpus / "config"
    qmd_cache = root_path / "qmd" / args.corpus / "cache"

    try:
        corpus = open_corpus(
            args.corpus,
            markdown_root=markdown_root,
            qmd_config=qmd_config,
            qmd_cache=qmd_cache,
        )
    except Exception as e:
        sys.stderr.write(f"Error opening corpus '{args.corpus}': {e}\n")
        return 1

    if args.command == "get":
        try:
            page = read_page(corpus, args.page)
            sys.stdout.write(page.body + "\n")
            return 0
        except MissingPage:
            sys.stderr.write(f"Page '{args.page}' not found in corpus '{args.corpus}'\n")
            return 2
        except Exception as e:
            sys.stderr.write(f"Error reading page '{args.page}': {e}\n")
            return 1

    elif args.command == "search":
        try:
            hits = search(corpus, args.query, limit=args.limit)
            if not hits:
                sys.stdout.write("No matching pages found.\n")
                return 0
            for h in hits:
                sys.stdout.write(f"{h.score:.2f}\t{h.relpath}\t{h.title}\n")
            return 0
        except SearchUnavailable as e:
            sys.stderr.write(f"Search unavailable: {e}\n")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
