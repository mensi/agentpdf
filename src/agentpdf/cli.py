"""Command-line interface."""

import argparse
import sys
import time
from pathlib import Path

from . import __version__
from .convert import Options, convert
from .search import INDEX_FILE, build_index, format_hits, search
from .writers import write_agents_md


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentpdf",
        description="Convert PDF documents into small, searchable files for AI coding agents.",
    )
    parser.add_argument("--version", action="version", version=f"agentpdf {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    conv = sub.add_parser("convert", help="convert one or more PDF files")
    conv.add_argument("pdf", nargs="+", help="PDF files to convert")
    conv.add_argument("-o", "--output", required=True, help="output directory (one subdirectory per PDF)")
    conv.add_argument("--dpi", type=int, default=150, help="resolution of rendered images (default: 150)")
    conv.add_argument(
        "--max-tokens",
        type=int,
        default=6000,
        help="approximate size limit of a section file in tokens; larger sections are split at "
        "subheadings, or into parts (default: 6000)",
    )
    conv.add_argument("--page-images", action="store_true", help="also render every full page as PNG")
    conv.add_argument("--split-pdf", action="store_true", help="also write one PDF per section")
    conv.add_argument("--no-index", action="store_true", help="do not build the search index")
    conv.add_argument(
        "--force", action="store_true", help="write into an existing non-empty output directory"
    )

    find = sub.add_parser("search", help="search converted documents")
    find.add_argument("query", help='search terms; "quoted phrase", prefix*')
    find.add_argument(
        "-d", "--dir", default=".", help="output directory of `convert` (default: current directory)"
    )
    find.add_argument("-n", "--limit", type=int, default=10, help="maximum number of results (default: 10)")
    find.add_argument("--doc", help="restrict to one document (its directory name)")
    find.add_argument("--any", action="store_true", help="match any term instead of all terms")

    args = parser.parse_args(argv)
    if args.command == "search":
        return _search(args)
    return _convert(args)


def _convert(args: argparse.Namespace) -> int:
    options = Options(
        dpi=args.dpi,
        max_tokens=args.max_tokens,
        page_images=args.page_images,
        split_pdf=args.split_pdf,
        index=False,  # built once below
        force=args.force,
    )
    status = 0
    converted = 0
    for path in args.pdf:
        start = time.monotonic()
        try:
            out_dir = convert(path, args.output, options)
        except Exception as exc:
            print(f"error: {path}: {exc}", file=sys.stderr)
            status = 1
            continue
        converted += 1
        print(f"{path} -> {out_dir} ({time.monotonic() - start:.1f}s)")
    if converted and not args.no_index:
        build_index(Path(args.output))
        write_agents_md(Path(args.output), has_search=(Path(args.output) / INDEX_FILE).is_file())
    return status


def _search(args: argparse.Namespace) -> int:
    try:
        hits, relaxed = search(Path(args.dir), args.query, args.limit, args.doc, args.any)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not hits:
        print("no matches", file=sys.stderr)
        return 1
    if relaxed:
        print("(no entry contains all terms; showing entries matching any term)")
    print(format_hits(hits))
    return 0


if __name__ == "__main__":
    sys.exit(main())
