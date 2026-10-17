"""Write the Markdown, JSON and index files of a converted document."""

import json
import re
from pathlib import Path

from . import __version__
from .model import Page, RegionKind
from .paragraphs import Block
from .sections import Section
from .util import page_range, slugify, token_estimate

MANIFEST = "manifest.json"
MARKER = ".agentpdf"
FORMAT_VERSION = 1

# INDEX.md lists at most this many entries; deeper levels go to contents/.
_INDEX_MAX_ENTRIES = 150

_MARKDOWN_LINE_START = re.compile(r"^(#|<!--|```)", re.MULTILINE)


# Section files


def section_markdown(section: Section) -> str:
    """Render a section: page markers, headings relative to the section's own
    heading, paragraphs, and tables/figures with their transcription."""
    out: list[str] = []
    page = None
    for block in section.blocks:
        if block.page != page:
            page = block.page
            out += [f"<!-- page {page} -->", ""]
        if block.region is not None:
            out += _region_markdown(block)
        elif block.heading_level is not None:
            level = min(6, max(1, block.heading_level - section.base_level + 1))
            out.append("#" * level + " " + block.text)
        else:
            # Body lines must not read as headings, page markers or fences,
            # which navigation and the search index rely on.
            out.append(_MARKDOWN_LINE_START.sub(r"\\\1", block.text))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _region_markdown(block: Block) -> list[str]:
    region = block.region
    label = "Table" if region.kind == RegionKind.TABLE else "Figure"
    out = [f"**[{label} on page {block.page}](../{region.image})**"]
    if not region.text.strip():
        return [*out, "*(no text; see image)*"]
    fence = "```"
    while fence in region.text:
        fence += "`"
    return [*out, f"{fence}text", region.text, fence]


# manifest.json


def manifest(
    source: Path,
    sha256: str,
    metadata: dict[str, str],
    pages: list[Page],
    sections: list[Section],
    outline_source: str,
) -> dict:
    by_page = _sections_by_page(sections)
    return {
        "format_version": FORMAT_VERSION,
        "generator": f"agentpdf {__version__}",
        "source": {"file": source.name, "sha256": sha256, "pages": len(pages)},
        "title": metadata.get("Title") or source.stem,
        "metadata": metadata,
        "outline_source": outline_source,
        "outline": [
            {"title": b.text, "level": b.heading_level, "page": b.page, "file": _file(s)}
            for s in sections
            for b in s.blocks
            if b.heading_level is not None
        ],
        "sections": [
            {
                "title": s.display_title,
                "level": s.level,
                "file": _file(s),
                "pages": list(s.pages) if s.pages else None,
                "chars": s.chars,
            }
            for s in sections
        ],
        "pages": [
            {
                "page": p.number,
                "sections": [_file(s) for s in by_page.get(p.number, [])],
                "flags": p.flags,
                "image": p.image or None,
                "regions": [
                    {"kind": r.kind.value, "image": r.image, "bbox": [round(v, 1) for v in r.bbox]}
                    for r in p.regions
                ],
            }
            for p in pages
        ],
    }


def _file(section: Section) -> str | None:
    return f"sections/{section.filename}" if section.has_file else None


def _sections_by_page(sections: list[Section]) -> dict[int, list[Section]]:
    result: dict[int, list[Section]] = {}
    for section in sections:
        if section.has_file:
            for page in sorted({b.page for b in section.blocks}):
                result.setdefault(page, []).append(section)
    return result


# INDEX.md and contents/


def write_index(out_dir: Path, info: dict, sections: list[Section]) -> None:
    """Write INDEX.md. For deep outlines, the lower levels are moved to
    contents/*.md files linked from INDEX.md, to keep it small."""
    regions = [r["kind"] for p in info["pages"] for r in p["regions"]]
    out = [
        f"# {info['title']}",
        "",
        f"- Source: `{info['source']['file']}`, {info['source']['pages']} pages",
        f"- Tables: {regions.count('table')}, figures: {regions.count('figure')} "
        "(rendered as images, see [PAGES.md](PAGES.md))",
        f"- Sections from: {info['outline_source']}",
    ]

    depth = max((s.level for s in sections), default=0)
    while depth > 0 and sum(s.level <= depth for s in sections) > _INDEX_MAX_ENTRIES:
        depth -= 1
    if any(s.level > depth for s in sections):
        out.append("- Deeper sections are listed in the linked `contents/` files.")
        (out_dir / "contents").mkdir(exist_ok=True)
    out += ["", "## Contents", ""]

    for i, s in enumerate(sections):
        if s.level > depth:
            continue
        line = _index_entry(s, "", s.level)
        below = _subtree(sections, i)
        if s.level == depth and below:
            name = f"contents/{i + 1:04d}-{slugify(s.title)}.md"
            line += f" · [subsections]({name}): {_summary(below)}"
            lines = [f"# {s.title}", "", _index_entry(s, "../", 0)]
            lines += [_index_entry(sub, "../", sub.level - s.level) for sub in below]
            (out_dir / name).write_text("\n".join(lines) + "\n", encoding="utf-8")
        out.append(line)
    (out_dir / "INDEX.md").write_text("\n".join(out) + "\n", encoding="utf-8")


def _subtree(sections: list[Section], i: int) -> list[Section]:
    """The sections nested below ``sections[i]``."""
    result = []
    for s in sections[i + 1 :]:
        if s.level <= sections[i].level:
            break
        result.append(s)
    return result


def _index_entry(s: Section, prefix: str, indent: int) -> str:
    pad = "  " * indent
    if not s.has_file:
        return f"{pad}- {s.title}"
    link = f"[{s.display_title}]({prefix}sections/{s.filename})"
    return f"{pad}- {link} — {page_range(s.pages)}, {token_estimate(s.chars)}"


def _summary(group: list[Section]) -> str:
    files = [s for s in group if s.has_file]
    pages = [p for s in files if s.pages for p in s.pages]
    span = page_range((min(pages), max(pages))) if pages else "no pages"
    noun = "file" if len(files) == 1 else "files"
    return f"{len(files)} {noun}, {span}, {token_estimate(sum(s.chars for s in files))}"


# PAGES.md


def pages_markdown(pages: list[Page], sections: list[Section]) -> str:
    by_page = _sections_by_page(sections)
    out = ["# Pages", "", "| Page | Section | Tables and figures |", "| --- | --- | --- |"]
    for p in pages:
        secs = ", ".join(
            f"[{s.display_title.replace('|', r'\|')}](sections/{s.filename})"
            for s in by_page.get(p.number, [])
        )
        items = [f"[{r.kind.value} {r.index}]({r.image})" for r in p.regions]
        if p.image:
            items.insert(0, f"[full page]({p.image})")
        if not p.has_text_layer:
            items.insert(0, "no text layer")
        out.append(f"| {p.number} | {secs} | {', '.join(items)} |")
    return "\n".join(out) + "\n"


# AGENTS.md

_AGENTS_MD = """\
# Reference documents

PDFs converted by [agentpdf](https://github.com/mensi/agentpdf). Do not read
whole documents: search or browse, then read only the sections you need.

## Documents

{documents}

## Navigating

- `<document>/INDEX.md`: table of contents; each entry links a section file
  and gives its pages and size.
{search}- Exact text: `rg -n -i "some phrase" */sections/`. Headings:
  `rg -n -i "^#+ .*some title" */sections/`.
- Section files mark pages with `<!-- page N -->`. `PAGES.md` maps pages
  to sections and images.

Tables and figures appear as `**[Table on page N](../images/….png)**`,
followed by a transcription that keeps characters at their horizontal
position. The image is the source of truth: view it when exact values
matter. `�` marks characters that could not be decoded.
"""

_SEARCH_MD = """\
- Search (ranked, stemmed; use a few keywords, not sentences):
  `agentpdf search -d <this directory> "some terms"` (or `uvx agentpdf …`).
  Hits show `file:line (page) — heading`. Options: `--doc <document>`,
  `-n <count>`, `"exact phrase"`, `prefix*`. The index is an SQLite FTS5
  table `chunks(text, heading, doc, file, line, page)` in `search.sqlite`.
"""


def is_agentpdf_dir(path: Path) -> bool:
    """Whether ``path`` is a document directory created by agentpdf."""
    manifest_path = path / MANIFEST
    if not path.is_dir() or path.is_symlink() or not manifest_path.is_file() or manifest_path.is_symlink():
        return False
    try:
        info = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(info, dict) and str(info.get("generator", "")).startswith("agentpdf ")


def is_agentpdf_agents_md(path: Path) -> bool:
    """Whether ``path`` is an AGENTS.md file generated by agentpdf."""
    if not path.is_file() or path.is_symlink():
        return False
    try:
        return "PDFs converted by [agentpdf]" in path.read_text(encoding="utf-8")
    except OSError:
        return False


def write_agents_md(out_root: Path, has_search: bool) -> None:
    """(Re)write the top-level AGENTS.md listing all converted documents."""
    entries = []
    for path in sorted(out_root.glob(f"*/{MANIFEST}")):
        if not is_agentpdf_dir(path.parent):
            continue
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError):
            continue
        name = path.parent.name
        entries.append(
            f"- [{info['title']}]({name}/INDEX.md): `{info['source']['file']}`, "
            f"{info['source']['pages']} pages"
        )
    text = _AGENTS_MD.format(
        documents="\n".join(entries) or "(no documents)",
        search=_SEARCH_MD if has_search else "",
    )
    agents_path = out_root / "AGENTS.md"
    if (agents_path.exists() or agents_path.is_symlink()) and not is_agentpdf_agents_md(agents_path):
        raise FileExistsError(f"{agents_path} exists and was not created by agentpdf")
    agents_path.write_text(text, encoding="utf-8")
