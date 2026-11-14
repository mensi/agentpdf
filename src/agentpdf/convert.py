"""Convert a PDF into a directory of Markdown sections, images and indexes."""

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pypdf

from .document import Document, validate_pdf
from .furniture import drop_printed_toc, remove_furniture
from .headings import detect_headings, mark_outline_headings, repair_outline_levels
from .model import RegionKind
from .paragraphs import Block, page_blocks, resolve_hyphens
from .search import INDEX_FILE, build_index
from .sections import Section, build_sections
from .util import page_range, parse_pages, slugify
from .writers import (
    MANIFEST,
    MARKER,
    is_agentpdf_agents_md,
    is_agentpdf_dir,
    manifest,
    pages_markdown,
    section_markdown,
    write_agents_md,
    write_index,
)


@dataclass
class Options:
    dpi: int = 150  # resolution of rendered images
    max_tokens: int = 6000  # approximate size limit of a section file
    page_images: bool = False  # also render every full page
    split_pdf: bool = False  # also write one PDF per section
    index: bool = True  # rebuild the search index after converting
    force: bool = False  # allow existing non-empty output root


def _prepare_out_root(out_root: Path, force: bool) -> None:
    if out_root.is_symlink() or (out_root.exists() and not out_root.is_dir()):
        raise FileExistsError(f"{out_root} exists and is not a directory")
    if out_root.exists():
        has_marker = (out_root / MARKER).is_file() and not (out_root / MARKER).is_symlink()
        if not has_marker and any(out_root.iterdir()) and not force:
            raise FileExistsError(
                f"{out_root} already exists and was not created by agentpdf (use --force to use it anyway)"
            )
        agents_path = out_root / "AGENTS.md"
        if (agents_path.exists() or agents_path.is_symlink()) and not is_agentpdf_agents_md(agents_path):
            raise FileExistsError(f"{agents_path} exists and was not created by agentpdf")
        index_path = out_root / INDEX_FILE
        if index_path.is_symlink() or index_path.is_dir() or (not has_marker and index_path.exists()):
            raise FileExistsError(f"{index_path} exists and was not created by agentpdf")
    else:
        out_root.mkdir(parents=True)


def _swap_dir(work_dir: Path, final_dir: Path) -> None:
    if not final_dir.exists():
        work_dir.rename(final_dir)
        return
    backup = tempfile.mkdtemp(prefix=f".{final_dir.name}.", suffix=".backup", dir=final_dir.parent)
    backup_dir = Path(backup)
    backup_dir.rmdir()
    final_dir.rename(backup_dir)
    try:
        work_dir.rename(final_dir)
    except BaseException:
        backup_dir.rename(final_dir)
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
    shutil.rmtree(backup_dir, ignore_errors=True)


def _manifest_sha256(doc_dir: Path) -> str | None:
    try:
        info = json.loads((doc_dir / MANIFEST).read_text(encoding="utf-8"))
        return info["source"]["sha256"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _target_dir(out_root: Path, slug: str, sha256: str) -> Path:
    n = 1
    while True:
        name = slug if n == 1 else f"{slug}-{n}"
        candidate = out_root / name
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
        if not is_agentpdf_dir(candidate):
            raise FileExistsError(f"{candidate} exists and was not created by agentpdf")
        if _manifest_sha256(candidate) == sha256:
            return candidate
        n += 1


def convert(pdf_path: str | Path, out_root: str | Path, options: Options | None = None) -> Path:
    """Convert one PDF into ``out_root/<name>/`` and update the shared
    AGENTS.md and search index in ``out_root``. Returns the document's
    output directory.

    The output is built in a temporary directory and swapped in when
    complete, so an interrupted conversion leaves the previous result intact.
    """
    options = options or Options()
    source, out_root = Path(pdf_path), Path(out_root)
    validate_pdf(source)
    sha256 = _sha256(source)
    _prepare_out_root(out_root, options.force)
    final_dir = _target_dir(out_root, slugify(source.stem), sha256)

    work_dir = Path(tempfile.mkdtemp(prefix=f".{final_dir.name}.", suffix=".partial", dir=out_root))
    try:
        _convert(source, sha256, work_dir, options)
    except BaseException:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
    _swap_dir(work_dir, final_dir)
    marker = out_root / MARKER
    if not marker.exists():
        marker.write_text("agentpdf\n", encoding="utf-8")

    if options.index:
        build_index(out_root)
    write_agents_md(out_root, has_search=(out_root / INDEX_FILE).is_file())
    return final_dir


def _convert(source: Path, sha256: str, out_dir: Path, options: Options) -> None:
    (out_dir / "sections").mkdir()
    (out_dir / "images").mkdir()

    with Document(str(source)) as doc:
        pages = list(doc.pages())
        outline = doc.outline()
        remove_furniture(pages)
        toc_pages = drop_printed_toc(pages, outline)

        # Tables and figures become images; pages without text are rendered whole.
        for page in pages:
            avoid = [ln.bbox for ln in page.lines]
            for region in page.regions:
                region.image = f"images/p{page.number:04d}-{region.id}.png"
                pad = 6.0 if region.kind == RegionKind.FIGURE else 4.0
                doc.render(
                    page.number - 1,
                    str(out_dir / region.image),
                    options.dpi,
                    region.bbox,
                    pad=pad,
                    avoid=avoid,
                )
            if options.page_images or not page.has_text_layer:
                page.image = f"images/p{page.number:04d}.png"
                doc.render(page.number - 1, str(out_dir / page.image), options.dpi)

        blocks = [b for page in pages for b in page_blocks(page)]
        blocks += [
            Block(p.number, -1, f"*(This page has no text layer. See [page image](../{p.image}).)*")
            for p in pages
            if not p.has_text_layer
        ]
        if toc_pages:
            pages_text = page_range((toc_pages[0], toc_pages[-1]))
            note = f"*(Printed table of contents omitted ({pages_text}); see [INDEX.md](../INDEX.md).)*"
            blocks.append(Block(toc_pages[0], -1, note))
        blocks.sort(key=lambda b: (b.page, b.order))
        resolve_hyphens(blocks)

        if outline:
            blocks = mark_outline_headings(blocks, repair_outline_levels(outline))
            outline_source = "bookmarks"
        else:
            outline_source = "typography" if detect_headings(blocks) else "none"
        sections = build_sections(blocks, options.max_tokens * 4)

        for section in sections:
            if section.has_file:
                (out_dir / "sections" / section.filename).write_text(
                    section_markdown(section), encoding="utf-8"
                )
        if options.split_pdf:
            _split_pdf(source, sections, out_dir / "pdf")

        info = manifest(source, sha256, doc.metadata, pages, sections, outline_source)
        (out_dir / MANIFEST).write_text(
            json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        write_index(out_dir, info, sections)
        (out_dir / "PAGES.md").write_text(pages_markdown(info), encoding="utf-8")


def render_pages(
    target: str | Path,
    pages_spec: str,
    out_root: str | Path = ".",
    *,
    pdf_path: str | Path | None = None,
    dpi: int = 150,
) -> list[Path]:
    """Render selected 1-based pages (e.g. ``10,38-42``) as full-page PNGs.

    ``target`` may be the document directory name inside ``out_root``, a path
    to a converted document directory, or a path to a PDF file. When a matching
    converted document directory exists, ``images/pXXXX.png``, ``manifest.json``
    and ``PAGES.md`` are updated in place.
    """
    target_path, root = Path(target), Path(out_root)
    doc_dir: Path | None = None
    source: Path | None = Path(pdf_path) if pdf_path is not None else None
    if source is not None and not source.is_file():
        raise FileNotFoundError(f"{source} not found")

    if is_agentpdf_dir(target_path):
        doc_dir = target_path
    elif is_agentpdf_dir(root / target_path):
        doc_dir = root / target_path
    elif target_path.is_file():
        validate_pdf(target_path)
        source = source or target_path
        sha = _sha256(target_path)
        for cand in sorted(root.glob(f"*/{MANIFEST}")) if root.is_dir() else []:
            if is_agentpdf_dir(cand.parent) and _manifest_sha256(cand.parent) == sha:
                doc_dir = cand.parent
                break
    else:
        raise FileNotFoundError(f"document {str(target)!r} not found in {root}")

    if doc_dir is None:
        assert source is not None
        if root.is_symlink() or (root.exists() and not root.is_dir()):
            raise FileExistsError(f"{root} exists and is not a directory")
        root.mkdir(parents=True, exist_ok=True)
        with Document(str(source)) as doc:
            numbers = parse_pages(pages_spec, len(doc))
            written = []
            for num in numbers:
                dest = root / f"p{num:04d}.png"
                doc.render(num - 1, str(dest), dpi)
                written.append(dest)
        return written

    info = json.loads((doc_dir / MANIFEST).read_text(encoding="utf-8"))
    if source is None:
        src_info = info.get("source", {})
        candidates = []
        if src_info.get("path"):
            candidates.append(Path(src_info["path"]))
        if src_info.get("file"):
            candidates += [doc_dir.parent / src_info["file"], Path(src_info["file"])]
        source = next((c for c in candidates if c.is_file()), None)
        if source is None:
            raise FileNotFoundError(f"source PDF {src_info.get('file', '')!r} not found; pass --pdf PATH")

    images_dir = doc_dir / "images"
    if images_dir.is_symlink() or (images_dir.exists() and not images_dir.is_dir()):
        raise FileExistsError(f"{images_dir} exists and is not a directory")
    if (doc_dir / "PAGES.md").is_symlink():
        raise FileExistsError(f"{doc_dir / 'PAGES.md'} is a symlink")

    with Document(str(source)) as doc:
        numbers = parse_pages(pages_spec, len(doc))
        images_dir.mkdir(exist_ok=True)
        by_num = {p["page"]: p for p in info.get("pages", [])}
        written = []
        for num in numbers:
            rel = f"images/p{num:04d}.png"
            dest = doc_dir / rel
            doc.render(num - 1, str(dest), dpi)
            if num in by_num:
                by_num[num]["image"] = rel
            written.append(dest)

    (doc_dir / MANIFEST).write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (doc_dir / "PAGES.md").write_text(pages_markdown(info), encoding="utf-8")
    return written


def _split_pdf(source: Path, sections: list[Section], out_dir: Path) -> None:
    out_dir.mkdir()
    reader = pypdf.PdfReader(source)
    for section in sections:
        if not section.has_file or section.pages is None:
            continue
        first, last = section.pages
        writer = pypdf.PdfWriter()
        for number in range(first, last + 1):
            writer.add_page(reader.pages[number - 1])
        with open(out_dir / section.filename.replace(".md", ".pdf"), "wb") as f:
            writer.write(f)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
