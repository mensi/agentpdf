"""Full-text search index over converted documents (SQLite FTS5).

The index is derived from the generated section files only, so it can be
rebuilt at any time. Entries are paragraphs (short ones merged) and whole
tables or figures, stored with their file, line, page and heading path.
"""

import json
import os
import re
import sqlite3
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .writers import MANIFEST, is_agentpdf_dir

INDEX_FILE = "search.sqlite"

_PAGE_MARKER = re.compile(r"^<!-- page (\d+) -->$")
_HEADING = re.compile(r"^(#{1,6}) (.*)$")
_FENCE = re.compile(r"^(`{3,})")

# Paragraphs are merged into chunks of at least this many characters.
_MIN_CHUNK = 300
# Results shown per section file.
_HITS_PER_FILE = 2
# Ranking weights for the indexed columns: text, heading.
_WEIGHTS = (1.0, 4.0)


@dataclass
class Chunk:
    doc: str
    file: str  # relative to the output root
    line: int  # 1-based line of the chunk in the file
    page: int | None
    heading: str
    text: str


@dataclass
class Hit:
    doc: str
    file: str
    line: int
    page: int | None
    heading: str
    snippet: str


def iter_chunks(out_root: Path, doc_dir: Path) -> Iterator[Chunk]:
    """Split a document's section files into searchable chunks."""
    manifest = json.loads((doc_dir / MANIFEST).read_text(encoding="utf-8"))
    titles = {s["file"]: s["title"] for s in manifest["sections"] if s["file"]}
    for path in sorted((doc_dir / "sections").glob("*.md")):
        yield from _file_chunks(
            doc_dir.name,
            path.relative_to(out_root).as_posix(),
            path.read_text(encoding="utf-8"),
            titles.get(path.relative_to(doc_dir).as_posix(), ""),
        )


def _file_chunks(doc: str, rel: str, content: str, title: str) -> Iterator[Chunk]:
    """Chunk one section file: paragraphs (and tables/figures) under the same
    heading are merged until a chunk has at least ``_MIN_CHUNK`` characters,
    so tiny fragments do not dominate the ranking and hits carry context."""
    page: int | None = None
    # Files continuing a split section start without a heading of their own.
    headings: list[str] = [title]
    buf: list[str] = []
    start, start_page = 0, None
    fence: str | None = None

    def flush(force: bool) -> Iterator[Chunk]:
        text = "\n".join(buf).strip()
        if not text or (not force and len(text) < _MIN_CHUNK):
            return
        yield Chunk(doc, rel, start, start_page, " > ".join(h for h in headings if h), text)
        buf.clear()

    for number, line in enumerate(content.splitlines(), 1):
        if fence is not None:
            buf.append(line)
            if line.strip() == fence:
                fence = None
            continue
        if match := _PAGE_MARKER.match(line):
            page = int(match.group(1))
            yield from flush(force=False)
            continue
        if match := _HEADING.match(line):
            yield from flush(force=True)
            headings[len(match.group(1)) - 1 :] = [match.group(2)]
            continue
        if not line.strip():
            yield from flush(force=False)
            if buf:
                buf.append("")
            continue
        if not buf:
            start, start_page = number, page
        buf.append(line)
        if match := _FENCE.match(line):
            fence = match.group(1)
    yield from flush(force=True)


def build_index(out_root: Path) -> Path:
    """(Re)build the search index for all converted documents in ``out_root``."""
    target = out_root / INDEX_FILE
    if target.is_symlink() or target.is_dir():
        raise FileExistsError(f"{target} cannot be overwritten")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{INDEX_FILE}.", suffix=".partial", dir=out_root)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        db = sqlite3.connect(tmp)
        try:
            db.execute(
                "CREATE VIRTUAL TABLE chunks USING fts5("
                "text, heading, doc UNINDEXED, file UNINDEXED, line UNINDEXED, page UNINDEXED, "
                "tokenize = 'porter unicode61 remove_diacritics 2')"
            )
            for manifest in sorted(out_root.glob(f"*/{MANIFEST}")):
                if not is_agentpdf_dir(manifest.parent):
                    continue
                db.executemany(
                    "INSERT INTO chunks (text, heading, doc, file, line, page) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        (c.text, c.heading, c.doc, c.file, c.line, c.page)
                        for c in iter_chunks(out_root, manifest.parent)
                    ),
                )
            db.execute("INSERT INTO chunks (chunks) VALUES ('optimize')")
            db.commit()
        finally:
            db.close()
        tmp.replace(target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target


def to_fts_query(query: str, any_term: bool = False) -> str:
    """Translate a plain search string into an FTS5 query.

    Words are matched as terms (stemmed), "quoted text" as a phrase, and a
    trailing ``*`` as a prefix. All terms must match unless ``any_term``.
    Punctuation inside words is treated as a separator, as in the index
    ("x.509" finds "X.509").
    """
    return (" OR " if any_term else " ").join(_terms(query))


def _terms(query: str) -> list[str]:
    terms = []
    for phrase, word in re.findall(r'"([^"]*)"|(\S+)', query):
        tokens = re.findall(r"\w+", phrase or word)
        if tokens:
            prefix = "*" if word.endswith("*") else ""
            terms.append('"' + " ".join(tokens) + '"' + prefix)
    return terms


def search(
    out_root: Path,
    query: str,
    limit: int = 10,
    doc: str | None = None,
    any_term: bool = False,
) -> tuple[list[Hit], bool]:
    """Search the index. Returns the hits and whether the query was relaxed
    to match any term because no entry contained all of them."""
    index = out_root / INDEX_FILE
    if not index.is_file():
        raise FileNotFoundError(f"no search index in {out_root}; convert a document first")
    terms = _terms(query)
    if not terms:
        return [], False
    hits = _query(index, to_fts_query(query, any_term), limit, doc)
    if not hits and not any_term and len(terms) > 1:
        # Nothing contains all terms: rank by any term instead.
        return _query(index, to_fts_query(query, any_term=True), limit, doc), True
    return hits, False


def _query(index: Path, fts: str, limit: int, doc: str | None) -> list[Hit]:
    sql = (
        "SELECT doc, file, line, page, heading, "
        "snippet(chunks, 0, '[', ']', ' … ', 24) "
        "FROM chunks WHERE chunks MATCH ?"
    )
    params: list[object] = [fts]
    if doc:
        sql += " AND doc = ?"
        params.append(doc)
    sql += f" ORDER BY bm25(chunks, {_WEIGHTS[0]}, {_WEIGHTS[1]}) LIMIT ?"
    params.append(limit * 5)
    db = sqlite3.connect(f"{index.resolve().as_uri()}?mode=ro", uri=True)
    try:
        rows = db.execute(sql, params).fetchall()
    finally:
        db.close()
    # Show at most a few hits per file so one section does not crowd out
    # the others; the file itself can be read for more.
    hits: list[Hit] = []
    per_file: dict[str, int] = {}
    for row in rows:
        per_file[row[1]] = per_file.get(row[1], 0) + 1
        if per_file[row[1]] <= _HITS_PER_FILE:
            hits.append(Hit(*row[:5], snippet=" ".join(row[5].split())))
    return hits[:limit]


def format_hits(hits: list[Hit]) -> str:
    lines = []
    for hit in hits:
        page = f" (p. {hit.page})" if hit.page is not None else ""
        heading = f" — {hit.heading}" if hit.heading else ""
        lines.append(f"{hit.file}:{hit.line}{page}{heading}")
        lines.append(f"    {hit.snippet}")
    return "\n".join(lines)
