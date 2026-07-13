#!/usr/bin/env python3
"""Compress verbose CLI output before feeding it to an LLM.

Tokf-inspired, repo-local filter: strip ANSI, drop noise lines, preserve failure
blocks, emit a short summary header. Stdlib only (Python 3.12+).
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_DIR = ROOT / "configs"

ANSI_RE = re.compile(r"\x1B\[[0-9;]*[ -/]*[@-~]")
PYTEST_FAIL_HEAD = re.compile(r"^(FAILED|ERROR)\s+")
PYTEST_TRACE = re.compile(r"^(E\s+|>)")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def load_config(*, preset: str | None, config_path: Path | None) -> dict:
    path = config_path
    if path is None:
        if not preset:
            preset = "generic"
        path = CONFIG_DIR / f"{preset}.toml"
    if not path.is_file():
        raise FileNotFoundError(f"config not found: {path}")
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return data.get("filter", data)


def _compile_patterns(patterns: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(p) for p in patterns]


def line_kept(line: str, *, skip: list[re.Pattern[str]], keep: list[re.Pattern[str]]) -> bool:
    if any(p.search(line) for p in skip):
        return False
    if keep and not any(p.search(line) for p in keep):
        return False
    return True


def extract_pytest_blocks(lines: list[str]) -> list[str]:
    """Keep failure headers, tracebacks, and the short test summary."""
    out: list[str] = []
    in_block = False
    for line in lines:
        if PYTEST_FAIL_HEAD.match(line) or line.startswith("=") and "FAIL" in line:
            in_block = True
            out.append(line)
            continue
        if in_block:
            if line.strip() == "" and out and not out[-1].strip():
                in_block = False
                continue
            if PYTEST_TRACE.match(line) or line.startswith(" ") or line.startswith("\t"):
                out.append(line)
                continue
            if line.startswith("="):
                out.append(line)
                in_block = False
                continue
            in_block = False
        if "short test summary" in line.lower() or line.startswith("FAILED ") or line.startswith("ERROR "):
            out.append(line)
    return out


def pytest_summary(lines: list[str]) -> str:
    passed = failed = error = skipped = 0
    for line in lines:
        m = re.search(r"(\d+)\s+passed", line)
        if m:
            passed = int(m.group(1))
        m = re.search(r"(\d+)\s+failed", line)
        if m:
            failed = int(m.group(1))
        m = re.search(r"(\d+)\s+error", line)
        if m:
            error = int(m.group(1))
        m = re.search(r"(\d+)\s+skipped", line)
        if m:
            skipped = int(m.group(1))
    parts = []
    if passed:
        parts.append(f"{passed} passed")
    if failed:
        parts.append(f"{failed} failed")
    if error:
        parts.append(f"{error} errors")
    if skipped:
        parts.append(f"{skipped} skipped")
    return ", ".join(parts) if parts else "no pytest summary line found"


def filter_output(
    raw: str,
    cfg: dict,
) -> tuple[str, dict[str, int]]:
    strip = bool(cfg.get("strip_ansi", True))
    text = strip_ansi(raw) if strip else raw
    lines = text.splitlines()

    mode = str(cfg.get("mode", "line"))
    skip = _compile_patterns(list(cfg.get("skip_patterns", [])))
    keep = _compile_patterns(list(cfg.get("keep_patterns", [])))
    max_lines = int(cfg.get("max_output_lines", 0) or 0)

    stats = {"input_lines": len(lines), "output_lines": 0, "dropped_lines": 0}

    if mode == "pytest":
        filtered = extract_pytest_blocks(lines)
        if not filtered:
            filtered = [ln for ln in lines if line_kept(ln, skip=skip, keep=keep)]
        summary = pytest_summary(lines)
        header = f"[agent_output_filter] pytest summary: {summary}"
        body = "\n".join(filtered)
        if max_lines and len(filtered) > max_lines:
            body = "\n".join(filtered[:max_lines]) + f"\n… truncated {len(filtered) - max_lines} lines"
        stats["output_lines"] = body.count("\n") + (1 if body else 0)
        stats["dropped_lines"] = stats["input_lines"] - stats["output_lines"]
        return f"{header}\n{body}".rstrip() + "\n", stats

    filtered = [ln for ln in lines if line_kept(ln, skip=skip, keep=keep)]

    if max_lines and len(filtered) > max_lines:
        truncated = len(filtered) - max_lines
        filtered = filtered[:max_lines]
        filtered.append(f"… truncated {truncated} additional lines")

    stats["output_lines"] = len(filtered)
    stats["dropped_lines"] = stats["input_lines"] - stats["output_lines"]
    ratio = 0.0
    if stats["input_lines"]:
        ratio = 100.0 * stats["dropped_lines"] / stats["input_lines"]
    header = (
        f"[agent_output_filter] kept {stats['output_lines']}/{stats['input_lines']} lines "
        f"({ratio:.0f}% dropped)"
    )
    body = "\n".join(filtered)
    return f"{header}\n{body}".rstrip() + ("\n" if body else ""), stats


def read_input(path: str | None) -> str:
    if path and path != "-":
        return Path(path).read_text(encoding="utf-8", errors="replace")
    return sys.stdin.read()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compress CLI output for LLM context (strip ANSI, drop noise, keep failures).",
    )
    parser.add_argument("input", nargs="?", default="-", help="Input file or '-' for stdin")
    parser.add_argument("--preset", choices=["pytest", "heroku", "eval", "yarn", "generic"])
    parser.add_argument("--config", type=Path, help="TOML config path (overrides --preset)")
    parser.add_argument("--exit-code", type=int, help="Prepend captured exit code to output")
    parser.add_argument("--max-lines", type=int, help="Override config max_output_lines")
    parser.add_argument("--stats-only", action="store_true", help="Print compression stats to stderr")
    args = parser.parse_args(argv)

    cfg = load_config(preset=args.preset, config_path=args.config)
    if args.max_lines:
        cfg["max_output_lines"] = args.max_lines

    raw = read_input(args.input)
    if not raw.strip():
        if args.exit_code is not None:
            print(f"[exit_code={args.exit_code}] (empty output)")
        return 0

    out, stats = filter_output(raw, cfg)
    if args.exit_code is not None:
        out = f"[exit_code={args.exit_code}]\n{out}"

    sys.stdout.write(out)
    if args.stats_only:
        print(stats, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
