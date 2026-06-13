# MHM Pipeline Web - Agent Instructions

This repository inherits its shared agent rules and reusable workflows from:

- `/Users/alexandergo/Documents/Doctorat/pipeline/AGENTS.md`
- `/Users/alexandergo/Documents/Doctorat/pipeline/CLAUDE.md`
- `/Users/alexandergo/Documents/Doctorat/pipeline/.claude/commands/`
- `/Users/alexandergo/Documents/Doctorat/pipeline/.codex/commands/`
- `/Users/alexandergo/Documents/Doctorat/pipeline/.codex/skills/`

Use the pipeline repo as the upstream source of truth for shared rules,
commands, and skills. This web repo adds only web-specific overrides and
bridge notes.

## Local rule

When a pipeline rule and a local web rule conflict, follow the local web rule
only if it explicitly applies to `mhm-pipeline-web`; otherwise inherit the
pipeline rule unchanged.
