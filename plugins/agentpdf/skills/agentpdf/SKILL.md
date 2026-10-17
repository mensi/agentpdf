---
name: agentpdf
description: Use when a task involves a large PDF such as a specification, reference manual, standard or datasheet. Converts the PDF once with agentpdf into searchable Markdown sections plus images of tables and figures, so you can search it and read only the relevant parts instead of the whole document. Also use when a directory contains an AGENTS.md written by agentpdf.
license: MIT
---

# PDF reference documents with agentpdf

Never read a large PDF in full. Convert it once, then search and read sections.

## Convert

Run (requires [uv](https://docs.astral.sh/uv/); takes seconds to a minute):

```sh
uvx agentpdf convert path/to/document.pdf -o docs/reference
```

- Use the project's existing reference directory if there is one (look for an
  `AGENTS.md` mentioning agentpdf); otherwise ask the user or use
  `docs/reference`.
- Several PDFs can share one output directory; each gets its own
  subdirectory, and `AGENTS.md` and the search index cover all of them.
- Converting again replaces that document's output.
- Suggest adding a line to the project's `AGENTS.md` or `CLAUDE.md` so future
  sessions find the documents, e.g.
  `Reference documents (PDF specs) are in docs/reference/; see its AGENTS.md.`
- Ask before committing the output; it may contain copyrighted documents.

## Look things up

Read `<output>/AGENTS.md` first, then:

```sh
uvx agentpdf search -d docs/reference "few precise keywords"
```

Each hit gives `file:line (page) — heading` relative to the output directory;
read that section file (or the lines around the hit). Browse
`<output>/<document>/INDEX.md` for the table of contents.
Tables and figures are linked PNG crops with a text transcription; view the
image when exact values matter.
