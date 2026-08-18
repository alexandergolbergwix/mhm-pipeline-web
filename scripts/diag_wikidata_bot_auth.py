#!/usr/bin/env python3
"""Redacted Wikidata bot-password diagnostic (never prints the secret).

Usage (paste when prompted, or pipe one line):

  cd backend && .venv/bin/python ../scripts/diag_wikidata_bot_auth.py --test
  printf '%s' 'Username@Bot:secret' | .venv/bin/python ../scripts/diag_wikidata_bot_auth.py --test

Or from env (still redacted in output):

  WIKIDATA_DIAG_TOKEN='Username@Bot:secret' .venv/bin/python scripts/diag_wikidata_bot_auth.py --test

Add ``--write-probe`` to also check session rights and (optionally) create+delete
a throwaway item on the wiki.
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
_REQUIRED = frozenset({"edit", "createpage"})
_USEFUL = frozenset({"edit", "createpage", "item-create", "move", "bot", "writeapi"})


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


def _try_login(token: str, *, is_test: bool):
    """Return (report_dict, login_or_None)."""
    from wikibaseintegrator import wbi_login
    from wikibaseintegrator.wbi_config import config as wbi_config

    api = _TEST_API if is_test else _LIVE_API
    wbi_config["MEDIAWIKI_API_URL"] = api
    wbi_config["USER_AGENT"] = "MHMPipeline-diag/1.0 (local; redacted)"
    norm = normalize_wikidata_auth_token(token)
    if ":" not in norm or "@" not in norm.split(":", 1)[0]:
        return {"ok": False, "error": "not_bot_password_format", "api": api}, None
    user, password = norm.split(":", 1)
    try:
        login = wbi_login.Login(
            user=user,
            password=password,
            mediawiki_api_url=api,
            user_agent="MHMPipeline-diag/1.0 (local; redacted)",
        )
        _ = login.get_edit_token()
        return (
            {
                "ok": True,
                "api": api,
                "login_user_len": len(user),
                "bot_name": user.rsplit("@", 1)[-1] if "@" in user else "",
            },
            login,
        )
    except Exception as exc:  # noqa: BLE001 — diagnostic only
        msg = str(exc)
        msg = msg.replace(password, "<redacted>") if password else msg
        msg = msg.replace(norm, "<redacted>")
        return {
            "ok": False,
            "api": api,
            "error_type": type(exc).__name__,
            "error": msg[:300],
        }, None


def _rights_probe(login, *, is_test: bool) -> dict:
    api = _TEST_API if is_test else _LIVE_API
    try:
        resp = login.get_session().get(
            api,
            params={
                "action": "query",
                "meta": "userinfo",
                "uiprop": "rights|groups|blockinfo",
                "format": "json",
            },
            timeout=20,
        )
        resp.raise_for_status()
        info = resp.json().get("query", {}).get("userinfo") or {}
        rights = {str(r) for r in (info.get("rights") or [])}
        missing = sorted(_REQUIRED - rights)
        useful = sorted(rights & _USEFUL)
        return {
            "ok": not missing and not info.get("anon"),
            "name": info.get("name"),
            "id": info.get("id"),
            "anon": bool(info.get("anon")),
            "groups": info.get("groups") or [],
            "useful_rights": useful,
            "missing_required": missing,
            "blocked": bool(info.get("blockid") or info.get("blockedby")),
            "block_reason": (info.get("blockreason") or "")[:200] or None,
            "rights_count": len(rights),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error_type": type(exc).__name__, "error": str(exc)[:300]}


def _uploader_capability(token: str, *, is_test: bool) -> dict:
    from converter.wikidata.uploader import WikidataUploader

    try:
        up = WikidataUploader(token=token, is_test=is_test, mark_as_bot=False)
        up.ensure_authenticated()
        return {
            "ok": True,
            "authenticated_user": up._authenticated_user,
            "mark_as_bot": up._mark_as_bot,
        }
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        norm = normalize_wikidata_auth_token(token)
        if ":" in norm:
            msg = msg.replace(norm.split(":", 1)[1], "<redacted>")
        return {"ok": False, "error_type": type(exc).__name__, "error": msg[:400]}


def _create_delete_probe(login, *, is_test: bool) -> dict:
    """Create a minimal item then delete it (proves real write rights)."""
    import time
    import uuid

    from wikibaseintegrator import WikibaseIntegrator
    from wikibaseintegrator.wbi_config import config as wbi_config

    api = _TEST_API if is_test else _LIVE_API
    wbi_config["MEDIAWIKI_API_URL"] = api
    wbi = WikibaseIntegrator(login=login)
    # Unique label each run — Wikidata rejects CREATE when another item already
    # has the same en label+description (leftover Q247826 from earlier diag).
    stamp = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    label = f"MHMPipeline diag throwaway {stamp}"
    try:
        item = wbi.item.new()
        item.labels.set(language="en", value=label)
        item.descriptions.set(
            language="en",
            value=f"Temporary diagnostic item {stamp}; safe to ignore",
        )
        # Labels-only create — avoid P31: test.wikidata.org property datatypes
        # often diverge from production and confuse the probe.
        written = item.write(
            summary="MHMPipeline local diag: create throwaway item",
            is_bot=False,
        )
        qid = getattr(written, "id", None)
        deleted = False
        delete_error = None
        if qid:
            try:
                session = login.get_session()
                csrf = login.get_edit_token()
                del_resp = session.post(
                    api,
                    data={
                        "action": "delete",
                        "title": qid,
                        "reason": "MHMPipeline local diag cleanup",
                        "token": csrf,
                        "format": "json",
                    },
                    timeout=30,
                )
                body = del_resp.json()
                deleted = "delete" in body and "error" not in body
                if not deleted:
                    delete_error = str(body.get("error") or body)[:200]
            except Exception as exc:  # noqa: BLE001
                delete_error = f"{type(exc).__name__}: {exc}"[:200]
        return {
            "ok": bool(qid),
            "created_qid": qid,
            "deleted": deleted,
            "delete_error": delete_error,
            "wiki": "test" if is_test else "live",
        }
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        code = getattr(exc, "code", None)
        return {
            "ok": False,
            "error_type": type(exc).__name__,
            "error_code": code,
            "error": msg[:400],
        }


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
    p.add_argument(
        "--write-probe",
        action="store_true",
        help="After login, check rights + WikidataUploader.assert_write_capability",
    )
    p.add_argument(
        "--create-item",
        action="store_true",
        help="Also create+delete a throwaway item (implies --write-probe)",
    )
    args = p.parse_args()
    raw = _read_token(args)
    report: dict = {"format": _redacted_report(raw)}
    if args.no_login:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report["format"]["format_ok"] else 1

    is_test = not args.live
    login = None
    if report["format"]["format_ok"]:
        report["login_probe"], login = _try_login(raw, is_test=is_test)
    else:
        report["login_probe"] = {
            "ok": False,
            "skipped": True,
            "reason": "format_invalid",
        }

    do_write = args.write_probe or args.create_item
    if do_write and login is not None and report["login_probe"].get("ok"):
        report["rights_probe"] = _rights_probe(login, is_test=is_test)
        report["uploader_capability"] = _uploader_capability(raw, is_test=is_test)
        if args.create_item:
            report["create_probe"] = _create_delete_probe(login, is_test=is_test)

    print(json.dumps(report, indent=2, ensure_ascii=False))
    ok = report["format"]["format_ok"] and bool(report["login_probe"].get("ok"))
    if do_write:
        ok = ok and bool(report.get("rights_probe", {}).get("ok"))
        ok = ok and bool(report.get("uploader_capability", {}).get("ok"))
        if args.create_item:
            ok = ok and bool(report.get("create_probe", {}).get("ok"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
