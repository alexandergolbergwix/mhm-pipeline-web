---
name: heb-en-docx
description: >-
  Hebrew/English RTL DOCX bridge. Content in repo .md (draft) and/or .docx
  (deliverable). After export, patch .docx only.
---

# Hebrew + English DOCX (web bridge)

Canonical skill: `/Users/alexandergo/Documents/Doctorat/pipeline/.codex/skills/heb-en-docx/SKILL.md`

## Where content is saved

| File | When |
|------|------|
| `docs/*.md` | Draft / optional git mirror |
| `docs/*.docx` | **Deliverable** — send this; canonical after first export |

Scripts: `pipeline/scripts/md_to_rtl_docx.py` (once) · `rtl_docx_tool.py` (all edits)

After `.docx` exists → patch the `.docx` in repo; **never** `--force` without user explicitly requesting `--discard-word-edits`.

## Audience

See canonical skill § **Audience — recipient vs author**. Chat-only / author-only text must never land in recipient `.md` / `.docx`.
