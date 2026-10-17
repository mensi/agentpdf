# agentpdf

> [!WARNING]
> This is a vibe-coded tool. The code was briefly reviewed by a human but not
> directly changed / maintained manually.

Make large PDFs — specifications, reference manuals, datasheets — usable by AI
coding agents without reading them into context.

agentpdf converts a PDF once into small Markdown section files, images of its
tables and figures, a table of contents and a full-text search index. Agents
then search and read only the parts they need, with their usual tools.

```sh
uvx agentpdf convert spec.pdf -o docs/reference
uvx agentpdf search -d docs/reference "attach request timer"
```

```
docs/reference/
├── AGENTS.md          # how agents should use this directory
├── search.sqlite      # full-text index over all documents
└── spec/
    ├── INDEX.md       # table of contents with page ranges and sizes
    ├── PAGES.md       # page → section, table and figure images
    ├── sections/      # Markdown, ≤ ~6k tokens per file, with page markers
    └── images/        # one PNG per table and figure
```

Requires [uv](https://docs.astral.sh/uv/) (or `pip install agentpdf`). Runs
locally on CPU, in seconds even for documents with hundreds of pages; no
models or network access needed.

## Using it with agents

**Point the agent at the output.** Add one line to your project's `AGENTS.md`
or `CLAUDE.md`:

```markdown
Reference documents (PDF specs) are in docs/reference/; see its AGENTS.md.
```

The generated `AGENTS.md` explains the layout and the search command in a few
lines.

**Or just ask.** Agents that can run commands can do the conversion
themselves: *"Convert spec.pdf with `uvx agentpdf` (github.com/mensi/agentpdf)
and use it to implement …"*

**Or install the skill** (Claude Code), so agents convert and search PDFs on
their own:

```
/plugin install agentpdf --marketplace mensi/agentpdf
```

On older versions: `/plugin marketplace add mensi/agentpdf`, then
`/plugin install agentpdf@agentpdf`. The skill is a plain
[Agent Skill](https://agentskills.io) in
[`plugins/agentpdf/skills/agentpdf`](plugins/agentpdf/skills/agentpdf/SKILL.md),
which you can also copy into `~/.claude/skills/` or another agent's skill
directory.

## How it works

- Text is extracted with [PDFium](https://pdfium.googlesource.com/pdfium/) in
  reading order; running headers, footers and printed tables of contents are
  dropped; paragraphs are re-flowed so phrases are searchable.
- Sections follow the PDF's bookmarks, or headings inferred from typography.
  Large sections are split at subheadings to fit the size budget.
- Tables and figures are not converted to Markdown tables, which is
  unreliable. Each is rendered as an image — the source of truth — plus a
  text transcription that keeps characters at their horizontal position.
- Search uses SQLite FTS5 (BM25 ranking, stemming); hits point to file, line,
  page and heading.

Only PDFs with a text layer are supported; scanned pages are rendered as
images but not OCRed.

## Options

```
agentpdf convert PDF... -o DIR [--max-tokens 6000] [--dpi 150]
                               [--page-images] [--split-pdf] [--no-index]
                               [--force]
agentpdf search QUERY [-d DIR] [-n 10] [--doc NAME] [--any]
```

`--page-images` also renders every page, `--split-pdf` writes one PDF per
section, `--force` writes into an existing non-empty output directory.
Converting again replaces a document's output.

## Development

```sh
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

## License

MIT
