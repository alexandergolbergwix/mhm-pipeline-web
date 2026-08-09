"""Normalize / validate Wikidata Settings auth tokens."""

from __future__ import annotations


def normalize_wikidata_auth_token(raw: str) -> str:
    """Strip noise and join a common two-line BotPasswords paste.

    Special:BotPasswords shows the login name and the password on separate
    lines. Curators often paste both; join as ``Username@BotName:password``.
    """
    text = (raw or "").strip().replace("\r\n", "\n").replace("\r", "\n")
    if "\n" not in text:
        return text
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if (
        len(lines) == 2
        and "@" in lines[0]
        and ":" not in lines[0]
        and "|" not in lines[0]
        and not lines[1].startswith("eyJ")
        and "|" not in lines[1]
    ):
        return f"{lines[0]}:{lines[1]}"
    return " ".join(lines).strip()


def wikidata_auth_token_format_ok(token: str) -> bool:
    """Return True when *token* matches a format ``WikidataUploader`` accepts."""
    t = normalize_wikidata_auth_token(token)
    if not t:
        return False
    if "|" in t:
        n = len(t.split("|"))
        return n == 2 or n >= 4
    if ":" in t and "@" in t.split(":", 1)[0]:
        user, password = t.split(":", 1)
        return bool(user.strip()) and bool(password.strip())
    return (
        ":" not in t
        and "|" not in t
        and t.count(".") == 2
        and t.startswith("eyJ")
    )


WIKIDATA_AUTH_FORMAT_HINT = (
    "Use one line: Username@BotName:password "
    "(from Special:BotPasswords), e.g. "
    "Alexander Goldberg IL@MHMPipelineTest:theGeneratedPassword. "
    "Or OAuth: key|secret  /  key|secret|token|secret  /  JWT eyJ…"
)
