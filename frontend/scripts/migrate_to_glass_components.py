#!/usr/bin/env python3
"""Migrate .glass / .glass-pill to <Glass> / <GlassPill> with correct closing tags."""

from __future__ import annotations

import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
SKIP = {"components/glass"}

TAGS = ("section", "div", "aside", "details", "header", "article", "li", "span", "label", "form", "p")
VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr",
})

OPEN_PATTERNS = [
    re.compile(rf"<({t})\b((?:\s[^\n>]*)?)className=\"([^\"]*)\"((?:\s[^\n>]*)?)>", re.MULTILINE)
    for t in TAGS
] + [
    re.compile(rf"<({t})\b((?:\s[^\n>]*)?)className=\{{`([^`]*)`\}}((?:\s[^\n>]*)?)>", re.MULTILINE)
    for t in TAGS
]


def strip_glass(classes: str) -> str:
    return " ".join(p for p in classes.split() if p not in {"glass", "glass-pill"})


def infer_variant(classes: str, attrs: str) -> str | None:
    blob = f"{classes} {attrs}"
    if "shadow-2xl" in blob and "fixed right-0" in blob:
        return "drawer"
    if "max-w-7xl" in blob:
        return "modal"
    if "max-h-[88vh]" in blob and "overflow-hidden" in blob:
        return "modal"
    return None


def build_open_replacement(
    tag: str, before: str, classes: str, after: str, tpl: bool,
) -> tuple[str, str] | None:
    has_pill = "glass-pill" in classes
    has_glass = re.search(r"\bglass\b", classes) is not None
    if not has_pill and not has_glass:
        return None
    cleaned = strip_glass(classes)
    if has_pill:
        comp = "GlassPill"
        close_tag = tag
        as_attr = f' as="{tag}"' if tag != "span" else ""
    else:
        comp, close_tag = "Glass", tag
        as_attr = f' as="{tag}"' if tag != "div" else ""
    variant = infer_variant(classes, f"{before}{after}")
    variant_attr = f' variant="{variant}"' if variant and not has_pill else ""
    if tpl and cleaned:
        cls = f" className={{`{cleaned}`}}"
    elif cleaned:
        cls = f' className="{cleaned}"'
    else:
        cls = ""
    open_text = f"<{comp}{as_attr}{variant_attr}{before}{cls}{after}>"
    return open_text, close_tag


def pass_openings(text: str) -> tuple[str, list[tuple[int, int, str]]]:
    """Returns new text and list of (close_start, close_len, component) to rewrite."""
    replacements: list[tuple[int, int, str, str]] = []
    for pat in OPEN_PATTERNS:
        for m in pat.finditer(text):
            built = build_open_replacement(
                m.group(1), m.group(2), m.group(3), m.group(4), "`" in pat.pattern,
            )
            if not built:
                continue
            open_text, close_tag = built
            replacements.append((m.start(), m.end(), open_text, close_tag))

    replacements.sort(key=lambda x: x[0])
    merged: list[tuple[int, int, str, str]] = []
    for r in replacements:
        if merged and r[0] < merged[-1][1]:
            continue
        merged.append(r)

    out = []
    pos = 0
    closes: list[tuple[int, int, str]] = []
    for start, end, open_text, close_tag in merged:
        out.append(text[pos:start])
        out.append(open_text)
        close_pos = find_matching_close(text, end, close_tag)
        if close_pos is not None:
            comp = "GlassPill" if open_text.startswith("<GlassPill") else "Glass"
            closes.append((close_pos, len(f"</{close_tag}>"), comp))
        pos = end
    out.append(text[pos:])
    return "".join(out), closes


def find_matching_close(text: str, start: int, close_tag_name: str) -> int | None:
    target = f"</{close_tag_name}>"
    depth = 1
    i = start
    tag_re = re.compile(r"<(/?)([A-Za-z][\w.-]*)([^>]*?)(\s*/)?>")
    while i < len(text):
        m = tag_re.search(text, i)
        if not m:
            return None
        closing, name, _, self_close = m.group(1), m.group(2), m.group(3), m.group(4)
        full = m.group(0)
        if self_close or name in VOID_TAGS:
            i = m.end()
            continue
        if closing:
            if depth == 1 and full == target:
                return m.start()
            if name not in ("Glass", "GlassPill"):
                depth -= 1
        else:
            if name not in VOID_TAGS and name not in ("Glass", "GlassPill"):
                depth += 1
        i = m.end()
    return None


def apply_closes(text: str, closes: list[tuple[int, int, str]]) -> str:
    for start, length, comp in sorted(closes, key=lambda x: x[0], reverse=True):
        text = text[:start] + f"</{comp}>" + text[start + length :]
    return text


def migrate_content(text: str) -> tuple[str, bool]:
    text2, closes = pass_openings(text)
    if not closes and text2 == text:
        return text, False
    text3 = apply_closes(text2, closes)
    needs_glass = "<Glass" in text3
    needs_pill = "<GlassPill" in text3
    if needs_glass or needs_pill:
        parts = []
        if needs_glass:
            parts.append("Glass")
        if needs_pill:
            parts.append("GlassPill")
        if 'from "@/components/glass"' not in text3:
            imp = f'import {{ {", ".join(parts)} }} from "@/components/glass";\n'
            end = 0
            for m in re.finditer(r"^import .+;\n", text3, re.MULTILINE):
                end = m.end()
            text3 = text3[:end] + imp + text3[end:]
    return text3, True


def migrate_file(path: Path) -> bool:
    rel = path.relative_to(SRC).as_posix()
    if any(rel.startswith(s) for s in SKIP):
        return False
    text = path.read_text(encoding="utf-8")
    if "glass" not in text:
        return False
    new_text, changed = migrate_content(text)
    if not changed:
        return False
    path.write_text(new_text, encoding="utf-8")
    print(f"migrated {rel}")
    return True


def main() -> int:
    n = sum(1 for p in sorted(SRC.rglob("*.tsx")) if migrate_file(p))
    print(f"done: {n} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
