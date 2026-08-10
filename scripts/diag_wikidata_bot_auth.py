#!/usr/bin/env python3
"""Redacted Wikidata bot-password diagnostic (never prints the secret).

Usage (paste when prompted, or pipe one line):

  cd backend && .venv/bin/python ../scripts/diag_wikidata_bot_auth.py --test
  printf '%s' 'Username@Bot:secret' | .venv/bin/python ../scripts/diag_wikidata_bot_auth.py --test

Or from env (still redacted in output):

  WIKIDATA_DIAG_TOKEN='Username@Bot:secret' .venv/bin/python scripts/diag_wikidata_bot_auth.py --test
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from converter.wikidata.auth_token import (  # noqa: E402
    normalize_wikidata_auth_token,
    wikidata_auth_token_format_ok,
)

_TEST_API = "https://test.wikidata.org/w/api.php"
_LIVE_API = "https://www.wikidata.org/w/api.php"


def _read_token(args: argparse.Namespace) -> str:
    if args.token_env:
        return os.environ.get(args.token_env, "") or ""
    env = os.environ.get("WIKIDATA_DIAG_TOKEN", "")
    if env:
        return env
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return getpass.getpass("Paste Username@BotName:password (hidden): ")


def _redacted_report(raw: str) -> dict:
    norm = normalize_wikidata_auth_token(raw)
    kind = "empty"
    user_len = 0
    bot_name = ""
    password_len = 0
    user_has_spaces = False
    user_preview = ""
    if not norm:
        kind = "empty"
    elif "|" in norm:
        kind = f"oauth_parts_{len(norm.split('|'))}"
    elif ":" in norm and "@" in norm.split(":", 1)[0]:
        kind = "bot_password"
        user, password = norm.split(":", 1)
        user_len = len(user)
        password_len = len(password)
        user_has_spaces = " " in user
        if "@" in user:
            bot_name = user.rsplit("@", 1)[-1]
        # Show only safe prefix of the login name (not the password).
        user_preview = re.sub(r"[^\w@.\- ]", "?", user)[:48]
    elif norm.count(".") == 2 and norm.startswith("eyJ"):
        kind = "jwt"
    else:
        kind = "unrecognized"

    issues: list[str] = []
    if not wikidata_auth_token_format_ok(raw):
        issues.append("format_rejected_by_uploader")
    if kind == "bot_password" and password_len == 0:
        issues.append("empty_password_after_colon")
    if kind == "bot_password" and password_len < 8:
        issues.append("password_suspiciously_short")
    if kind == "bot_password" and not bot_name:
        issues.append("missing_bot_name_after_at")
    if "\n" in (raw or "") or "\r" in (raw or ""):
        issues.append("had_newlines_normalized")
    if raw != norm and not issues:
        issues.append("normalized_ok")

    return {
        "format_ok": wikidata_auth_token_format_ok(raw),
        "kind": kind,
        "raw_len": len(raw or ""),
        "normalized_len": len(norm),
        "user_len": user_len,
        "user_preview": user_preview,
        "user_has_spaces": user_has_spaces,
        "bot_name": bot_name,
        "password_len": password_len,
        "issues": issues,
        "secret_redacted": True,
    }


def _try_login(token: str, *, is_test: bool) -> dict:
    from wikibaseintegrator import wbi_login
    from wikibaseintegrator.wbi_config import config as wbi_config

    api = _TEST_API if is_test else _LIVE_API
    wbi_config["MEDIAWIKI_API_URL"] = api
    wbi_config["USER_AGENT"] = "MHMPipeline-diag/1.0 (local; redacted)"
    norm = normalize_wikidata_auth_token(token)
    if ":" not in norm or "@" not in norm.split(":", 1)[0]:
        return {"ok": False, "error": "not_bot_password_format", "api": api}
    user, password = norm.split(":", 1)
    try:
        login = wbi_login.Login(
            user=user,
            password=password,
            mediawiki_api_url=api,
            user_agent="MHMPipeline-diag/1.0 (local; redacted)",
        )
        # Force the login handshake.
        _ = login.get_edit_token()
        return {
            "ok": True,
            "api": api,
            "login_user_len": len(user),
            "bot_name": user.rsplit("@", 1)[-1] if "@" in user else "",
        }
    except Exception as exc:  # noqa: BLE001 — diagnostic only
        msg = str(exc)
        # Never echo token fragments if the exception string embeds them.
        msg = msg.replace(password, "<redacted>") if password else msg
        msg = msg.replace(norm, "<redacted>")
        return {"ok": False, "api": api, "error_type": type(exc).__name__, "error": msg[:300]}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--test", action="store_true", help="Hit test.wikidata.org once (default)")
    p.add_argument("--live", action="store_true", help="Hit www.wikidata.org once")
    p.add_argument(
        "--token-env",
        default="",
        help="Read token from this env var name (default WIKIDATA_DIAG_TOKEN)",
    )
    p.add_argument(
        "--no-login",
        action="store_true",
        help="Only check format; do not call the Action API",
    )
    args = p.parse_args()
    raw = _read_token(args)
    report: dict = {"format": _redacted_report(raw)}
    if args.no_login:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report["format"]["format_ok"] else 1

    is_test = not args.live
    if report["format"]["format_ok"]:
        report["login_probe"] = _try_login(raw, is_test=is_test)
    else:
        report["login_probe"] = {
            "ok": False,
            "skipped": True,
            "reason": "format_invalid",
        }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    ok = report["format"]["format_ok"] and bool(report["login_probe"].get("ok"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
