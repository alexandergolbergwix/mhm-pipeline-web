"""Quarantine untrusted MARC / Wikidata text before the privileged planner.

The Dual-LLM pattern: raw retrieved text is data, never an instruction.
This layer converts it into typed, length-capped JSON the planner may see.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

_MAX_CHARS = 2000
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_INSTRUCTION_RE = re.compile(
    r"(ignore (previous|all) instructions|system prompt|you are now)",
    re.IGNORECASE,
)


def quarantine_text(raw: str | None, *, max_chars: int = _MAX_CHARS) -> dict[str, Any]:
    """Return a typed envelope. The privileged model must not see raw strings inline."""
    text = unicodedata.normalize("NFC", str(raw or ""))
    text = _CONTROL_RE.sub("", text)
    truncated = len(text) > max_chars
    text = text[:max_chars]
    flagged = bool(_INSTRUCTION_RE.search(text))
    if flagged:
        text = _INSTRUCTION_RE.sub("[redacted-instruction]", text)
    return {
        "type": "untrusted_text",
        "value": text,
        "chars": len(text),
        "truncated": truncated,
        "instruction_like": flagged,
    }


def quarantine_mapping(data: dict[str, Any], text_keys: tuple[str, ...] = ("value", "label", "description", "text")) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, val in data.items():
        if key in text_keys and isinstance(val, str):
            out[key] = quarantine_text(val)["value"]
        elif isinstance(val, dict):
            out[key] = quarantine_mapping(val, text_keys)
        elif isinstance(val, list):
            out[key] = [
                quarantine_mapping(item, text_keys) if isinstance(item, dict)
                else (quarantine_text(item)["value"] if isinstance(item, str) else item)
                for item in val[:80]
            ]
        else:
            out[key] = val
    return out


def output_rail(text: str, *, system_prompt: str) -> str:
    """Strip leaked system instructions from assistant text."""
    cleaned = text or ""
    if system_prompt and system_prompt[:80] in cleaned:
        cleaned = cleaned.replace(system_prompt, "[redacted]")
    for needle in ("MASTER_KEY", "tool_grant", "BotName:password", "HEROKU_TOOL_BASE_URL"):
        cleaned = cleaned.replace(needle, "[redacted]")
    return cleaned[:8000]
