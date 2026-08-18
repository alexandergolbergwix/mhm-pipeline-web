"""Upload WikidataItem objects to Wikidata via WikibaseIntegrator.

Handles live upload with rate limiting, retry logic, and per-entity
error handling. WikibaseIntegrator is imported lazily so the module
can be loaded even when the library is not installed (dry-run mode
does not require it).

Supports both production wikidata.org and test.wikidata.org.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from converter.wikidata.item_builder import WikidataItem, WikidataStatement

logger = logging.getLogger(__name__)

_WIKIDATA_API = "https://www.wikidata.org/w/api.php"
_WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
_WIKIDATA_URL = "https://www.wikidata.org"

_TEST_API = "https://test.wikidata.org/w/api.php"
# Bug fix 2026-04-16 (deeper audit Fix #7): the previous value here was the
# MediaWiki API URL, not a SPARQL endpoint — every SPARQL query in test
# mode silently failed (got HTML back). test.wikidata.org has its own SPARQL
# endpoint at /sparql.
_TEST_SPARQL = "https://test.wikidata.org/sparql"
_TEST_URL = "https://test.wikidata.org"

_MAX_RETRIES = 3
_RETRY_DELAY_SECONDS = 5.0
_EDIT_DELAY_SECONDS = 1.5  # ~40 edits/minute (safe for OAuth with 5000 req/hr)


@dataclass
class UploadResult:
    """Result of uploading a single item."""

    local_id: str
    qid: str | None = None
    status: str = "pending"  # "success" | "updated" | "exists" | "failed" | "skipped"
    message: str = ""
    added_properties: list[str] = field(default_factory=list)


class UnauthorisedModificationError(RuntimeError):
    """Raised when the uploader is asked to modify a Wikidata item whose
    first revision was NOT authored by the authenticated user.

    This is the defense-in-depth tripwire for CLAUDE.md rule 38: the
    pipeline is only ever allowed to CREATE new entities and to MODIFY
    entities it created itself. Any attempt to modify another user's
    item must raise this exception, not merely be skipped silently.

    The *stage* field records WHICH guard caught the violation so the
    audit log makes it clear that the defense chain worked (entry /
    build / write / identity-check).
    """

    def __init__(self, *, qid: str, stage: str) -> None:
        super().__init__(
            f"SAFETY: refusing to modify {qid!r} — not authored by the "
            f"authenticated user (caught at stage {stage!r})."
        )
        self.qid = qid
        self.stage = stage


class WikidataUploader:
    """Upload WikidataItem objects to Wikidata.

    Requires ``wikibaseintegrator`` package for live uploads.
    Install with: ``pip install wikibaseintegrator``

    Usage::

        uploader = WikidataUploader(auth="User@BotName:bot-password")
        results = uploader.upload_all(items)
    """

    def __init__(
        self,
        token: str,
        is_test: bool = False,
        batch_mode: bool = False,
        *,
        allow_live: bool = False,
        mark_as_bot: bool | None = None,
    ) -> None:
        """Initialize the uploader.

        Args:
            token: OAuth bearer token or bot password for Wikidata API.
            is_test: If True, use test.wikidata.org instead of production.
            batch_mode: If True, pause 60s every 45 items to stay under rate limits.
            allow_live: If True, curator explicitly chose live wikidata.org
                (UI upload target). Same effect as ``MORATORIUM_LIFTED=true``.
            mark_as_bot: Pass ``is_bot=True`` on writes. Default False — MediaWiki
                hard-fails when the account lacks the ``bot`` right (export-40).
                Set True / ``WIKIDATA_MARK_AS_BOT=true`` only after the account
                has been granted bot rights.

        Raises:
            RuntimeError: If the Wikidata moratorium (CLAUDE.md rule 25) is in
                effect and neither ``allow_live`` nor ``MORATORIUM_LIFTED=true``
                is set. Test mode (``is_test=True``) bypasses the check.
        """
        import os  # noqa: PLC0415

        from converter.wikidata.auth_token import (  # noqa: PLC0415
            normalize_wikidata_auth_token as _normalize_auth,
        )

        self._token = _normalize_auth(token)
        self._is_test = is_test
        self._batch_mode = batch_mode
        self._allow_live = allow_live
        if mark_as_bot is None:
            mark_as_bot = os.environ.get("WIKIDATA_MARK_AS_BOT", "").lower() in {
                "1", "true", "yes",
            }
        self._mark_as_bot = bool(mark_as_bot)
        self._wbi = None
        self._login = None
        self._last_edit_time: float = 0.0
        self._authenticated_user: str | None = None  # Set after first auth
        self._creator_cache: dict[str, str] = {}  # qid → first revision author
        self._is_our_item_cache: dict[str, bool] = {}  # qid → creator-check result
        self._test_property_datatypes: dict[str, str | None] = {}
        self._test_entity_exists: dict[str, bool] = {}
        self._test_pid_map: dict[str, str] = {}
        self._test_qid_map: dict[str, str] = {}
        self._test_stubs_we_created: set[str] = set()
        self._foreign_accept_qids: set[str] = set()
        self._test_can_create_properties: bool | None = None
        self._enforce_moratorium()

    @staticmethod
    def _enforce_moratorium() -> None:
        """Refuse to run against production Wikidata while the moratorium is on.

        Lifted only when ``MORATORIUM_LIFTED=true`` is set in the environment.
        See CLAUDE.md rule 25 for the conditions that must hold before
        lifting the moratorium.

        ``is_test=True`` bypasses this check (test.wikidata.org is fine for
        development and CI).
        """
        import os  # noqa: PLC0415

        if os.environ.get("MORATORIUM_LIFTED", "").lower() == "true":
            return
        # Inspect the caller's `is_test` arg from the bound instance after init.
        # We do that in upload_item / upload_all instead of here so test-mode
        # uploaders still construct cleanly. See _check_moratorium_for_live().

    def _check_moratorium_for_live(self) -> None:
        """Block any live Wikidata write while the moratorium is on."""
        import os  # noqa: PLC0415

        if self._is_test:
            return
        if self._allow_live:
            return
        if os.environ.get("MORATORIUM_LIFTED", "").lower() == "true":
            return
        raise RuntimeError(
            "WIKIDATA MORATORIUM IN EFFECT (CLAUDE.md rule 25). "
            "All bulk Wikidata operations are blocked until pipeline bugs "
            "#1-#4 are fixed and verified, 20+ manual edits have been made, "
            "and a notice has been posted on Wikidata:Project chat. "
            "To override, choose live upload in Wikidata Studio or set "
            "MORATORIUM_LIFTED=true in the environment. "
            "See User talk:Alexander Goldberg IL § 'Please stop your edits' "
            "(Geagea, 2026-04-14)."
        )

    def _init_wbi(self) -> object:
        """Lazily initialize WikibaseIntegrator.

        Returns:
            A configured WikibaseIntegrator instance.

        Raises:
            ImportError: If wikibaseintegrator is not installed.
        """
        if self._wbi is not None:
            return self._wbi

        try:
            from wikibaseintegrator import (
                WikibaseIntegrator,  # noqa: PLC0415
                wbi_login,  # noqa: PLC0415
            )
            from wikibaseintegrator.wbi_config import config as wbi_config  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "wikibaseintegrator is required for live Wikidata upload. "
                "Install it with: pip install wikibaseintegrator\n"
                "Or use dry-run mode to export QuickStatements instead."
            ) from exc

        if self._is_test:
            wbi_config["MEDIAWIKI_API_URL"] = _TEST_API
            wbi_config["SPARQL_ENDPOINT_URL"] = _TEST_SPARQL
            wbi_config["WIKIBASE_URL"] = _TEST_URL
        else:
            wbi_config["MEDIAWIKI_API_URL"] = _WIKIDATA_API
            wbi_config["SPARQL_ENDPOINT_URL"] = _WIKIDATA_SPARQL
            wbi_config["WIKIBASE_URL"] = _WIKIDATA_URL

        # Limit retries to avoid infinite waits during server load
        # Fix 2026-04-15 third audit Fix #16: maxlag=5 was too aggressive —
        # edits fail frequently during server load and burn retries. Best
        # practice for bots is maxlag >= 10s (Wikidata:Bots).
        wbi_config["MAXLAG"] = 10
        wbi_config["BACKOFF_MAX_TRIES"] = 3  # Max 3 retries (default 5)
        wbi_config["BACKOFF_MAX_VALUE"] = 30  # Max 30s backoff (default 3600!)

        # Support four authentication methods:
        # 1. Bot password: "Username@BotName:password"
        # 2. OAuth 2.0 owner-only: "consumer_key|consumer_secret"
        # 3. OAuth 1.0a: "consumer_key|consumer_secret|access_token|access_secret"
        # 4. OAuth 2.0 pre-issued JWT bearer token:
        #    "eyJ0eXAiOiJKV1QiLCJhbGci...<dotted JWT>..."
        #    (Issued directly by meta.wikimedia.org when the consumer
        #    registration is owner-only + "Client is confidential". No
        #    consumer-secret exchange needed — we wrap a requests.Session
        #    with the Authorization: Bearer header and hand it straight
        #    to wbi_login._Login.)
        api_url = wbi_config["MEDIAWIKI_API_URL"]
        user_agent = "MHMPipeline/1.0 (shvedbook@gmail.com)"

        # JWT bearer detection: 3 dot-separated base64url parts, starts
        # with "eyJ" (the typical base64url prefix of a JSON header like
        # {"typ":"JWT", ...}). No `:` (not a bot password) and no `|`
        # (not a consumer-key/consumer-secret pair).
        is_jwt_bearer = (
            ":" not in self._token
            and "|" not in self._token
            and self._token.count(".") == 2
            and self._token.startswith("eyJ")
        )
        if is_jwt_bearer:
            import requests  # noqa: PLC0415

            session = requests.Session()
            session.headers.update({
                "Authorization": f"Bearer {self._token}",
                "User-Agent": user_agent,
            })
            login = wbi_login._Login(  # noqa: SLF001 — upstream exposes no public alternative
                session=session,
                mediawiki_api_url=api_url,
                user_agent=user_agent,
            )
        elif "|" in self._token:
            parts = self._token.split("|")
            if len(parts) == 2:
                # OAuth 2.0: consumer_key|consumer_secret
                login = wbi_login.OAuth2(
                    consumer_token=parts[0].strip(),
                    consumer_secret=parts[1].strip(),
                    mediawiki_api_url=api_url,
                    user_agent=user_agent,
                )
            elif len(parts) >= 4:
                # OAuth 1.0a: consumer_key|consumer_secret|access_token|access_secret
                login = wbi_login.OAuth1(
                    consumer_token=parts[0].strip(),
                    consumer_secret=parts[1].strip(),
                    access_token=parts[2].strip(),
                    access_secret=parts[3].strip(),
                    mediawiki_api_url=api_url,
                    user_agent=user_agent,
                )
            else:
                raise ValueError(
                    "Invalid OAuth token format. Use:\n"
                    "  OAuth 2.0: consumer_key|consumer_secret\n"
                    "  OAuth 1.0a: consumer_key|consumer_secret|access_token|access_secret"
                )
        elif ":" in self._token and "@" in self._token.split(":")[0]:
            # Bot password: "Username@BotName:password"
            parts = self._token.split(":", 1)
            login = wbi_login.Login(
                user=parts[0],
                password=parts[1],
                mediawiki_api_url=api_url,
                user_agent=user_agent,
            )
        else:
            raise ValueError(
                "Invalid authentication format. Use one of:\n"
                "  Bot password:  Username@BotName:password\n"
                "  OAuth 2.0:     consumer_key|consumer_secret\n"
                "  OAuth 1.0a:    consumer_key|consumer_secret|access_token|access_secret\n"
                "  JWT bearer:    eyJ…  (paste the owner-only access token "
                "from Special:OAuthConsumerRegistration)"
            )

        self._wbi = WikibaseIntegrator(login=login)
        self._login = login
        return self._wbi

    # MediaWiki rights required to CREATE Wikibase items. Bot passwords that
    # only grant "Edit existing pages" authenticate fine but fail every write
    # with permissiondenied (export-41 / 2026-08-11).
    _REQUIRED_WRITE_RIGHTS = frozenset({"edit", "createpage"})

    def ensure_authenticated(self) -> None:
        """Force a single MediaWiki login now (Rule W-179).

        Upload jobs must call this once and reuse the same uploader; logging
        in per item burns MediaWiki's login rate limit. Also verifies the
        session can create/edit items so missing bot-password grants abort
        the job once instead of failing every row three times.
        """
        self._init_wbi()
        self.assert_write_capability()

    def assert_write_capability(self) -> None:
        """Abort early when the session cannot create Wikidata items.

        Raises:
            RuntimeError: anonymous session or missing ``edit``/``createpage``.
        """
        info = self._query_userinfo_rights()
        if not info:
            logger.warning("Could not verify write rights via userinfo; continuing")
            return
        name = str(info.get("name") or "")
        if info.get("anon") or not name or name == "127.0.0.1":
            wiki = "test.wikidata.org" if self._is_test else "www.wikidata.org"
            raise RuntimeError(
                f"Wikidata session is anonymous on {wiki}. "
                "Check Settings → Wikidata (test/live) bot password."
            )
        self._authenticated_user = name
        rights = {str(r) for r in (info.get("rights") or [])}
        missing = sorted(self._REQUIRED_WRITE_RIGHTS - rights)
        if missing:
            wiki = "test.wikidata.org" if self._is_test else "www.wikidata.org"
            raise RuntimeError(
                f"Authenticated as {name!r} on {wiki} but missing MediaWiki "
                f"rights {missing}. On {wiki}/wiki/Special:BotPasswords enable "
                "at least: High-volume editing; Edit existing pages; "
                "Create, edit, and move pages. Then save a new password into "
                "Settings and retry."
            )
        logger.info(
            "Wikidata write session ok user=%s rights_has_createpage=%s",
            name,
            "createpage" in rights,
        )

    def _query_userinfo_rights(self) -> dict[str, Any] | None:
        """Return ``userinfo`` (name/rights/anon) via the logged-in WBI session."""
        try:
            login = self._login
            if login is None:
                return None
            session = login.get_session()
            api_url = _TEST_API if self._is_test else _WIKIDATA_API
            resp = session.get(
                api_url,
                params={
                    "action": "query",
                    "meta": "userinfo",
                    "uiprop": "rights|groups",
                    "format": "json",
                },
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json().get("query", {}).get("userinfo")
        except Exception as exc:  # noqa: BLE001
            logger.warning("userinfo rights probe failed: %s", exc)
            return None

    def _rate_limit(self) -> None:
        """Enforce edit rate limiting."""
        elapsed = time.time() - self._last_edit_time
        if elapsed < _EDIT_DELAY_SECONDS:
            time.sleep(_EDIT_DELAY_SECONDS - elapsed)
        self._last_edit_time = time.time()

    # Identity properties: if the existing item already has a DIFFERENT value
    # on one of these, adding our value would create the multi-value conflict
    # pattern that Kolja21 flagged (e.g., two birth dates, two GNDs). We MUST
    # skip our value in that case — the items are different real-world entities.
    _IDENTITY_PROPS = frozenset(
        {"P569", "P570", "P19", "P20", "P227", "P214", "P8189", "P213", "P244", "P21"}
    )

    # Rule 42 (Phase 1 HMO fidelity, 2026-05-17): P31 moves OUT of the strict
    # single-value bucket. HMO models manuscripts as intersections of multiple
    # classes (illuminated + composite + palimpsest + codex) and WikiProject
    # Manuscripts endorses multi-P31 when no pair is in a subclass relation.
    # The other ten identity properties keep their Rule 23 semantics intact.
    _MULTI_VALUE_IDENTITY_PROPS = frozenset({"P31"})

    # Defense-in-depth (audit response 2026-05-17, Geagea/Kolja21/Epìdosis
    # talk threads): the multi-value P31 relaxation is bounded to the
    # manuscript class hierarchy. Adding P31=Q5 (human), P31=Q43229
    # (organization), P31=Q215380 (band), etc. is STILL refused even
    # though P31 is multi-value. This is the structural guard against
    # the wrong-P31 incidents — manuscripts can be both Q87167 and
    # Q48498, but never Q87167 and Q5.
    _MANUSCRIPT_P31_VALUES = frozenset(
        {
            "Q87167",      # manuscript
            "Q213924",     # codex
            "Q48498",      # illuminated manuscript
            "Q33308141",   # composite manuscript
            "Q179808",     # palimpsest
            "Q3884",       # codex (alternate)
            "Q571",        # book (only when manuscript is also being typed as a book)
        }
    )

    def _would_create_identity_conflict(self, wbi_item: object, stmt: WikidataStatement) -> bool:
        """Return True if adding this statement to the existing item would create
        a multi-value conflict on an identity property."""
        if stmt.property_id in self._MULTI_VALUE_IDENTITY_PROPS:
            # Multi-value allowed, but bounded to a coherent class hierarchy
            # to avoid the wrong-P31 incidents (e.g., manuscript-class on a
            # person item). Audit response 2026-05-17.
            if stmt.property_id == "P31":
                new_value = str(stmt.value)
                if new_value not in self._MANUSCRIPT_P31_VALUES:
                    # Refuse to add a non-manuscript P31 via the pipeline.
                    # The pipeline only emits manuscript P31s; if a caller
                    # reaches here with something else, it's a bug.
                    return True
                # If the existing item already carries a non-manuscript P31
                # (e.g., Q5 human), refuse to add a manuscript-class one
                # alongside it.
                try:
                    existing_claims = wbi_item.claims.get("P31") or []
                    for existing in existing_claims:
                        existing_value = self._extract_claim_value(existing)
                        if (
                            existing_value
                            and existing_value not in self._MANUSCRIPT_P31_VALUES
                        ):
                            return True
                except Exception:
                    pass
            return False
        if stmt.property_id not in self._IDENTITY_PROPS:
            return False
        try:
            existing_claims = wbi_item.claims.get(stmt.property_id) or []
        except Exception:
            return False
        if not existing_claims:
            return False
        new_value = str(stmt.value)
        for existing in existing_claims:
            existing_value = self._extract_claim_value(existing)
            if existing_value == new_value:
                return False  # same value — safe to add (WBI will dedup)
            # date precision: compare just the date prefix for P569/P570
            if stmt.property_id in ("P569", "P570") and existing_value[:11] == new_value[:11]:
                return False
        return True  # existing has different value(s) — would conflict

    def _build_wbi_item(self, item: WikidataItem) -> tuple[object, int, list[str]]:
        """Convert a WikidataItem to a WikibaseIntegrator item object.

        For existing items, performs claim diffing to avoid duplicates AND
        refuses to write identity-property values that conflict with existing ones.

        Returns:
            Tuple of (wbi_item, new_claims_count, added_properties).
        """

        wbi = self._init_wbi()

        # DEFENSE-IN-DEPTH #2 (rule 38): Even though upload_item already
        # checks _is_our_item at entry, _build_wbi_item is called from
        # other code paths too (tests, scripts). Re-assert here so no
        # mutation can happen on someone else's item.
        self._assert_modifiable(item.existing_qid or "", stage="_build_wbi_item")

        if item.existing_qid:
            wbi_item = wbi.item.get(item.existing_qid)
        else:
            wbi_item = wbi.item.new()

        # Labels — never overwrite an existing label on an item we did not create.
        # The creator-author check upstream already guarantees we only modify our
        # own items here, but be defensive: only set a label if the language slot
        # is empty.
        for lang, label in item.labels.items():
            if item.existing_qid:
                try:
                    current = wbi_item.labels.get(lang)
                    current_val = (
                        current.value if current and getattr(current, "value", None) else ""
                    )
                except Exception:
                    current_val = ""
                if current_val:
                    continue
            wbi_item.labels.set(lang, label)

        # Descriptions — same protection as labels above. Bug fix
        # 2026-04-16 (deeper audit Fix #17): previously overwrote
        # community-improved descriptions on existing items.
        for lang, desc in item.descriptions.items():
            if item.existing_qid:
                try:
                    current = wbi_item.descriptions.get(lang)
                    current_val = (
                        current.value if current and getattr(current, "value", None) else ""
                    )
                except Exception:
                    current_val = ""
                if current_val:
                    continue
            wbi_item.descriptions.set(lang, desc)

        # Aliases
        for lang, alias_list in item.aliases.items():
            for alias in alias_list:
                wbi_item.aliases.set(lang, alias)

        # Statements — use WBI's built-in dedup for existing items
        from wikibaseintegrator.wbi_enums import ActionIfExists  # noqa: PLC0415

        action = (
            ActionIfExists.MERGE_REFS_OR_APPEND
            if item.existing_qid
            else ActionIfExists.FORCE_APPEND
        )

        added_properties: list[str] = []
        for stmt in item.statements:
            # SAFETY: never add an identity-property value that conflicts with
            # an existing one on a pre-existing item.
            if item.existing_qid and self._would_create_identity_conflict(wbi_item, stmt):
                logger.warning(
                    "SAFETY: Refusing to add %s=%s to %s (existing item has different value — would create conflict)",
                    stmt.property_id,
                    stmt.value,
                    item.existing_qid,
                )
                continue
            claim = self._build_claim(stmt)
            if claim:
                count_before = len(wbi_item.claims)
                wbi_item.claims.add(claim, action_if_exists=action)
                if len(wbi_item.claims) > count_before:
                    added_properties.append(stmt.property_id)

        new_claims = len(added_properties) if item.existing_qid else len(item.statements)
        return wbi_item, new_claims, added_properties

    def _claim_exists(self, wbi_item: object, stmt: WikidataStatement) -> bool:
        """Check if a claim with the same property+value already exists on the item."""
        try:
            existing_claims = wbi_item.claims.get(stmt.property_id)
            if not existing_claims:
                return False

            new_value = str(stmt.value)

            for existing in existing_claims:
                existing_value = self._extract_claim_value(existing)
                if existing_value == new_value:
                    return True
        except Exception:
            pass  # If comparison fails, assume claim doesn't exist → add it
        return False

    @staticmethod
    def _extract_claim_value(wbi_claim: object) -> str:
        """Extract a comparable string value from a WBI claim."""
        try:
            snak = wbi_claim.mainsnak
            dv = snak.datavalue
            if not dv:
                return ""
            val = dv.get("value", dv) if isinstance(dv, dict) else dv
            if isinstance(val, dict):
                if "id" in val:
                    return val["id"]  # Q12345
                if "time" in val:
                    return val["time"]  # +1650-00-00T00:00:00Z
                if "text" in val:
                    return val["text"]  # monolingual text
                if "amount" in val:
                    return val["amount"]
                return str(val)
            return str(val)
        except Exception:
            return ""

    def _build_claim(self, stmt: WikidataStatement) -> object | None:
        """Convert a WikidataStatement to a WikibaseIntegrator claim.

        Args:
            stmt: The statement to convert.

        Returns:
            A datatypes claim object, or None if conversion fails.
        """
        from wikibaseintegrator import datatypes  # noqa: PLC0415
        from wikibaseintegrator.models import Reference, References  # noqa: PLC0415
        from wikibaseintegrator.wbi_enums import WikibaseRank  # noqa: PLC0415

        # Build references
        refs = References()
        if stmt.references:
            ref = Reference()
            for ref_snak in stmt.references:
                ref_claim = self._build_reference_snak(ref_snak)
                if ref_claim:
                    ref.add(ref_claim)
            refs.add(ref)

        value = stmt.value
        # Skip local references (unresolved persons)
        if isinstance(value, str) and value.startswith("__LOCAL:"):
            return None

        # Build qualifiers
        from wikibaseintegrator.models import Qualifiers  # noqa: PLC0415

        qualifiers = Qualifiers()
        for qual in stmt.qualifiers or []:
            qual_claim = self._build_reference_snak(qual)
            if qual_claim:
                qualifiers.add(qual_claim)

        # Rule 42: resolve rank for the claim. WikibaseIntegrator's
        # WikibaseRank enum is the canonical surface; we fail closed by
        # defaulting to NORMAL if the value is unknown.
        rank_map = {
            "preferred": WikibaseRank.PREFERRED,
            "normal": WikibaseRank.NORMAL,
            "deprecated": WikibaseRank.DEPRECATED,
        }
        rank_enum = rank_map.get(stmt.rank, WikibaseRank.NORMAL)

        try:
            # Rule 42: somevalue/novalue map to WBI's snaktype field rather
            # than a concrete datavalue. We build a stub Item claim and
            # override its mainsnak.
            if stmt.value_type in ("somevalue", "novalue"):
                stub = datatypes.Item(
                    prop_nr=stmt.property_id,
                    references=refs,
                    qualifiers=qualifiers,
                    rank=rank_enum,
                )
                stub.mainsnak.snaktype = stmt.value_type
                stub.mainsnak.datavalue = {}
                return stub
            if stmt.value_type == "item":
                return datatypes.Item(
                    prop_nr=stmt.property_id,
                    value=str(value),
                    references=refs,
                    qualifiers=qualifiers,
                    rank=rank_enum,
                )
            if stmt.value_type == "string":
                return datatypes.String(
                    prop_nr=stmt.property_id,
                    value=str(value),
                    references=refs,
                    qualifiers=qualifiers,
                    rank=rank_enum,
                )
            if stmt.value_type == "external-id":
                return datatypes.ExternalID(
                    prop_nr=stmt.property_id,
                    value=str(value),
                    references=refs,
                    qualifiers=qualifiers,
                    rank=rank_enum,
                )
            if stmt.value_type == "time":
                return datatypes.Time(
                    prop_nr=stmt.property_id,
                    time=str(value),
                    precision=stmt.precision,
                    references=refs,
                    qualifiers=qualifiers,
                    rank=rank_enum,
                )
            if stmt.value_type == "quantity":
                # Map unit strings to Wikidata entity URLs
                unit_url_map = {
                    "mm": "http://www.wikidata.org/entity/Q174789",
                    "cm": "http://www.wikidata.org/entity/Q174728",
                    "m": "http://www.wikidata.org/entity/Q11573",
                }
                unit_val = unit_url_map.get(stmt.unit, "1") if stmt.unit else "1"
                return datatypes.Quantity(
                    prop_nr=stmt.property_id,
                    amount=value,
                    unit=unit_val,
                    references=refs,
                    qualifiers=qualifiers,
                    rank=rank_enum,
                )
            if stmt.value_type == "url":
                return datatypes.URL(
                    prop_nr=stmt.property_id,
                    value=str(value),
                    references=refs,
                    qualifiers=qualifiers,
                    rank=rank_enum,
                )
            if stmt.value_type == "monolingualtext":
                return datatypes.MonolingualText(
                    prop_nr=stmt.property_id,
                    text=str(value),
                    language=stmt.language,
                    references=refs,
                    qualifiers=qualifiers,
                    rank=rank_enum,
                )
        except Exception as exc:
            logger.warning(
                "Failed to build claim for %s=%s: %s",
                stmt.property_id,
                value,
                exc,
            )
            return None

        return None

    def _build_reference_snak(self, ref_snak: dict[str, str]) -> object | None:
        """Build a reference snak for WikibaseIntegrator."""
        from wikibaseintegrator import datatypes  # noqa: PLC0415

        prop = ref_snak.get("property", "")
        value = ref_snak.get("value", "")
        snak_type = ref_snak.get("type", "string")

        try:
            if snak_type == "item":
                return datatypes.Item(prop_nr=prop, value=value)
            if snak_type == "url":
                return datatypes.URL(prop_nr=prop, value=value)
            if snak_type == "time":
                precision = ref_snak.get("precision", 11)
                return datatypes.Time(prop_nr=prop, time=str(value), precision=int(precision))
            return datatypes.String(prop_nr=prop, value=value)
        except Exception as exc:
            logger.warning("Failed to build reference snak %s: %s", prop, exc)
            return None

    def _get_authenticated_user(self) -> str | None:
        """Get the username of the authenticated session via API."""
        if self._authenticated_user is not None:
            return self._authenticated_user
        try:
            import requests  # noqa: PLC0415

            api_url = _TEST_API if self._is_test else _WIKIDATA_API
            headers = {"User-Agent": "MHMPipeline/1.0 (shvedbook@gmail.com)"}

            # OAuth 2.0 bearer token format (single token, no |)
            if "|" not in self._token and ":" not in self._token:
                headers["Authorization"] = f"Bearer {self._token}"
            elif "|" in self._token:
                # OAuth: extract bearer if WBI provided one
                parts = self._token.split("|")
                if len(parts) == 2:
                    # Owner-only consumer — use as bearer
                    headers["Authorization"] = f"Bearer {self._token}"

            resp = requests.get(
                api_url,
                params={"action": "query", "meta": "userinfo", "format": "json"},
                headers=headers,
                timeout=10,
            )
            user = resp.json().get("query", {}).get("userinfo", {}).get("name")
            if user and user != "127.0.0.1":  # not anonymous
                self._authenticated_user = user
                logger.info("Authenticated as Wikidata user: %s", user)
                return user
        except Exception as exc:
            logger.warning("Could not determine authenticated user: %s", exc)
        return None

    def _get_first_revision_author(self, qid: str) -> str | None:
        """Get the username of the FIRST revision (creator) of an item via API."""
        if qid in self._creator_cache:
            return self._creator_cache[qid]
        try:
            import requests  # noqa: PLC0415

            api_url = _TEST_API if self._is_test else _WIKIDATA_API
            resp = requests.get(
                api_url,
                params={
                    "action": "query",
                    "prop": "revisions",
                    "titles": qid,
                    "rvprop": "user",
                    "rvdir": "newer",
                    "rvlimit": "1",
                    "format": "json",
                },
                headers={"User-Agent": "MHMPipeline/1.0 (shvedbook@gmail.com)"},
                timeout=10,
            )
            pages = resp.json().get("query", {}).get("pages", {})
            for _pid, page in pages.items():
                revs = page.get("revisions", [])
                if revs:
                    author = revs[0].get("user")
                    if author:
                        self._creator_cache[qid] = author
                        return author
        except Exception as exc:
            logger.warning("Could not get first revision author for %s: %s", qid, exc)
        return None

    def _user_created_via_contribs(self, qid: str, user: str) -> bool | None:
        """Independent cross-check via ``list=usercontribs`` API endpoint.

        Returns:
            True  — *user* has a ``new``-type contribution on *qid* (i.e.
                    they created the page).
            False — *user* has NO ``new``-type contribution on *qid*.
            None  — the API call failed; caller must fall back.

        This is the second of the two independent creator-verification
        channels required by rule 38. If this disagrees with the
        ``prop=revisions`` answer, the item is NOT confirmed as ours.
        """
        try:
            import requests  # noqa: PLC0415

            api_url = _TEST_API if self._is_test else _WIKIDATA_API
            resp = requests.get(
                api_url,
                params={
                    "action": "query",
                    "list": "usercontribs",
                    "ucuser": user,
                    "uctitle": qid,
                    "uctype": "new",
                    "uclimit": "1",
                    "format": "json",
                },
                headers={"User-Agent": "MHMPipeline/1.0 (shvedbook@gmail.com)"},
                timeout=10,
            )
            contribs = resp.json().get("query", {}).get("usercontribs", [])
            return bool(contribs)
        except Exception as exc:
            logger.warning("usercontribs check failed for %s: %s", qid, exc)
            return None

    def _item_exists_on_wikidata_sparql(self, qid: str) -> bool | None:
        """SPARQL existence check — ``ASK WHERE { wd:<qid> ?p ?o }``.

        Returns:
            True  — item exists and has at least one triple.
            False — item has no triples (deleted / redirected / not found).
            None  — SPARQL endpoint failure; caller must fall back.

        A False result means the QID has been deleted or merged into
        another item since we reconciled it; under rule 38 we refuse to
        modify such items even if the creator check passes, because the
        target of any modification would be ambiguous.
        """
        try:
            import requests  # noqa: PLC0415

            endpoint = _TEST_SPARQL if self._is_test else _WIKIDATA_SPARQL
            resp = requests.get(
                endpoint,
                params={
                    "query": f"ASK WHERE {{ wd:{qid} ?p ?o . }}",
                    "format": "json",
                },
                headers={
                    "User-Agent": "MHMPipeline/1.0 (shvedbook@gmail.com)",
                    "Accept": "application/sparql-results+json",
                },
                timeout=15,
            )
            return bool(resp.json().get("boolean"))
        except Exception as exc:
            logger.warning("SPARQL existence check failed for %s: %s", qid, exc)
            return None

    def _bot_excluded(self, qid: str) -> bool:
        """Check the item's talk page for a {{bots|deny=…}} exclusion.

        Returns True if the bot should NOT edit this item. Caches results
        per QID for the lifetime of the uploader instance. Bug fix
        2026-04-16 (deeper audit Fix #9): respects the community convention
        documented at https://www.wikidata.org/wiki/Wikidata:Bot_policy

        Network failures or missing talk pages return False (allow edit) —
        we only block when an explicit exclusion is found.
        """
        if not hasattr(self, "_bot_exclusion_cache"):
            self._bot_exclusion_cache: dict[str, bool] = {}
        if qid in self._bot_exclusion_cache:
            return self._bot_exclusion_cache[qid]
        try:
            import re  # noqa: PLC0415

            import requests  # noqa: PLC0415

            api_url = _TEST_API if self._is_test else _WIKIDATA_API
            resp = requests.get(
                api_url,
                params={
                    "action": "parse",
                    "page": f"Talk:{qid}",
                    "prop": "wikitext",
                    "format": "json",
                    "redirects": "1",
                },
                headers={"User-Agent": "MHMPipeline/1.0 (shvedbook@gmail.com)"},
                timeout=10,
            )
            data = resp.json()
            if "error" in data:
                self._bot_exclusion_cache[qid] = False
                return False
            wikitext = (data.get("parse", {}).get("wikitext", {}).get("*", "") or "").lower()
            # Match {{bots|deny=…}} where … contains "all" or our bot name.
            auth_user = (self._get_authenticated_user() or "").lower()
            patterns = [r"\{\{bots\s*\|\s*deny\s*=\s*all"]
            if auth_user:
                # Tolerate a few common bot-name variants.
                for name in {auth_user, auth_user.replace(" ", "_"), "mhmpipeline"}:
                    patterns.append(rf"\{{\{{bots\s*\|\s*deny\s*=[^}}]*\b{re.escape(name)}\b")
            excluded = any(re.search(p, wikitext) for p in patterns)
            self._bot_exclusion_cache[qid] = excluded
            return excluded
        except Exception as exc:
            logger.warning("Could not check bot exclusion for %s: %s", qid, exc)
            self._bot_exclusion_cache[qid] = False
            return False

    def register_foreign_accept(self, qid: str) -> None:
        """Record curator accept for one foreign QID (live wikidata.org only).

        Test uploads MUST NOT call this — they never UPDATE foreign items.
        """
        clean = str(qid or "").strip()
        if not clean:
            return
        if self._is_test:
            logger.warning(
                "Ignoring accept_foreign_modify for %s on test.wikidata.org — "
                "test uploads must not UPDATE foreign items",
                clean,
            )
            return
        logger.warning("FOREIGN_MODIFY_ACCEPTED qid=%s (live upload only)", clean)
        self._foreign_accept_qids.add(clean)

    def _item_usable_as_test_reference(self, qid: str) -> bool:
        """True when a test Q-id may be used as a claim value (not an UPDATE target)."""
        clean = str(qid or "").strip()
        if not clean:
            return False
        if clean in self._test_stubs_we_created:
            return True
        return self._is_our_item(clean)

    def _is_our_item(self, qid: str) -> bool:
        """Return True iff the authenticated user is the first-revision author of *qid*.

        TRIPLE-VERIFICATION contract (re-hardened 2026-04-24 per user
        directive "ensure 100 times that we will not modify entities not
        created by me — check using wikidata api and sparkql queries"):

        ========== ========== ========== ========== =========
        auth_user  rev.user   contribs   sparql     returns
        ========== ========== ========== ========== =========
        unknown    *          *          *          False
        known      unknown    *          *          False
        known      other      *          *          False
        known      self       False      *          False   ← cross-check disagrees
        known      self       None       ok         True    (contribs API down — rev call agreed)
        known      self       True       False      False   ← SPARQL says QID deleted
        known      self       True       ok         True
        ========== ========== ========== ========== =========

        Three INDEPENDENT verification channels:

            1. ``action=query&prop=revisions&rvdir=newer&rvlimit=1``
               — asks Wikidata's MediaWiki API who wrote the first
               revision of the item. Authoritative for creator.

            2. ``action=query&list=usercontribs&ucuser=<me>&uctitle=<qid>&uctype=new``
               — asks the same API whether the authenticated user has
               a "page creation" entry for this QID. Independent of
               (1): the two endpoints go through different internal
               paths, so a corruption on one side would not affect
               the other.

            3. ``ASK WHERE { wd:<qid> ?p ?o }`` on the Wikidata SPARQL
               endpoint — confirms the item still exists and has not
               been deleted, redirected, or blanked since we reconciled
               it. If the item no longer exists, the *target* of any
               modification is ambiguous and we must refuse.

        The P1343=Ktiv marker fallback is REMOVED. A community-created
        item could legitimately cite Ktiv as a source, which made that
        fallback dangerous: an API hiccup that blanked the
        revision-author lookup would silently allow modification of the
        community's item. Ktiv is a bibliographic source, not a creator
        fingerprint.

        The uploader caches every decision so repeated calls during a
        single upload are O(1) and cannot race with a concurrent edit
        that might change the first revision.
        """
        cached = self._is_our_item_cache.get(qid)
        if cached is not None:
            return cached

        if qid in self._foreign_accept_qids:
            if self._is_test:
                logger.error(
                    "SAFETY: foreign accept registered for %s on test wiki — refusing",
                    qid,
                )
                self._is_our_item_cache[qid] = False
                return False
            logger.warning(
                "FOREIGN_MODIFY_ACCEPTED: skipping Rule-38 creator check for %s",
                qid,
            )
            self._is_our_item_cache[qid] = True
            return True

        # ── Channel #1: who is the authenticated user? ─────────────────
        auth_user = self._get_authenticated_user()
        if not auth_user:
            logger.warning(
                "SAFETY: Could not determine authenticated user — refusing "
                "any modification of existing item %s (fail-closed).",
                qid,
            )
            self._is_our_item_cache[qid] = False
            return False

        # ── Channel #2: who authored the first revision of the QID? ────
        creator = self._get_first_revision_author(qid)
        if not creator:
            logger.warning(
                "SAFETY: Could not determine first-revision author of %s — "
                "refusing modification (fail-closed).",
                qid,
            )
            self._is_our_item_cache[qid] = False
            return False

        if creator != auth_user:
            logger.warning(
                "SAFETY: Item %s was created by '%s', not '%s' — REFUSING to modify",
                qid, creator, auth_user,
            )
            self._is_our_item_cache[qid] = False
            return False

        # ── Channel #3: cross-check via list=usercontribs ──────────────
        #
        # Independent API endpoint confirming the user has a "new" (page
        # creation) contribution on the QID. If it disagrees with
        # channel #2, refuse — the two cannot disagree on a well-formed
        # page. ``None`` means the contribs call failed (network blip):
        # we accept that only because channel #2 already agreed.
        contribs_ok = self._user_created_via_contribs(qid, auth_user)
        if contribs_ok is False:
            logger.warning(
                "SAFETY: Cross-check disagreement on %s — revisions API "
                "reports '%s' as creator but list=usercontribs shows no "
                "page-creation edit for that user. Refusing (fail-closed).",
                qid, auth_user,
            )
            self._is_our_item_cache[qid] = False
            return False

        # ── Channel #4: SPARQL existence check ─────────────────────────
        #
        # Confirms the QID is a live, non-redirected, non-blanked entity.
        # If SPARQL says the item has zero triples, it has been deleted
        # or merged away — we must not attempt to modify a vanished QID.
        exists = self._item_exists_on_wikidata_sparql(qid)
        if exists is False and self._is_test:
            # test.wikidata.org SPARQL is sparse and often reports no triples
            # for items that Action API still serves. Do not refuse our own
            # UPDATEs (or stub reuse) on a false SPARQL miss — confirm via
            # wbgetentities, and treat a lookup failure as inconclusive.
            fetched = self._wbgetentities([qid], props="info")
            if qid not in fetched:
                exists = None
            else:
                ent = fetched.get(qid)
                exists = bool(isinstance(ent, dict) and "missing" not in ent)
        if exists is False:
            logger.warning(
                "SAFETY: SPARQL reports %s has no triples (deleted / "
                "redirected / blanked) — refusing modification.",
                qid,
            )
            self._is_our_item_cache[qid] = False
            return False

        self._is_our_item_cache[qid] = True
        return True

    def _assert_modifiable(self, qid: str, stage: str) -> None:
        """Defense-in-depth guard: raise if *qid* must not be modified.

        Called from EVERY stage of the modification pipeline so a single
        missed check at the entry point cannot let an unauthorised edit
        slip through. Raises :class:`UnauthorisedModificationError` —
        the uploader catches it and converts to a ``skipped`` result
        rather than a crash.
        """
        if not qid:
            return  # new-item creation path; no existing item at risk
        if not self._is_our_item(qid):
            raise UnauthorisedModificationError(qid=qid, stage=stage)

    def _wbgetentities(self, ids: list[str], *, props: str) -> dict[str, dict]:
        """Fetch entities from the current wiki. Empty dict on network failure."""
        import requests  # noqa: PLC0415

        out: dict[str, dict] = {}
        if not ids:
            return out
        api_url = _TEST_API if self._is_test else _WIKIDATA_API
        headers = {"User-Agent": "MHMPipeline/1.0 (shvedbook@gmail.com)"}
        try:
            for i in range(0, len(ids), 50):
                chunk = ids[i : i + 50]
                resp = requests.get(
                    api_url,
                    params={
                        "action": "wbgetentities",
                        "ids": "|".join(chunk),
                        "props": props,
                        "format": "json",
                    },
                    headers=headers,
                    timeout=30,
                )
                resp.raise_for_status()
                entities = resp.json().get("entities") or {}
                if isinstance(entities, dict):
                    out.update(entities)
        except Exception as exc:  # noqa: BLE001
            logger.warning("wbgetentities failed (%s): %s", props, exc)
        return out

    def _wbsearchentities(
        self,
        search: str,
        *,
        entity_type: str,
        limit: int = 8,
    ) -> list[dict[str, str]]:
        """Search test wiki entities by label. Returns id/label/datatype dicts."""
        login = self._login
        if login is None:
            return []
        api_url = _TEST_API if self._is_test else _WIKIDATA_API
        try:
            session = login.get_session()
            resp = session.get(
                api_url,
                params={
                    "action": "wbsearchentities",
                    "search": search,
                    "language": "en",
                    "type": entity_type,
                    "limit": limit,
                    "format": "json",
                },
                timeout=30,
            )
            resp.raise_for_status()
            hits = resp.json().get("search") or []
            out: list[dict[str, str]] = []
            for hit in hits:
                if not isinstance(hit, dict):
                    continue
                out.append({
                    "id": str(hit.get("id") or ""),
                    "label": str(hit.get("label") or ""),
                    "datatype": str(hit.get("datatype") or ""),
                })
            return out
        except Exception as exc:  # noqa: BLE001
            logger.warning("wbsearchentities failed (%r): %s", search, exc)
            return []

    def _get_csrf_token(self) -> str | None:
        login = self._login
        if login is None:
            return None
        api_url = _TEST_API if self._is_test else _WIKIDATA_API
        try:
            session = login.get_session()
            resp = session.get(
                api_url,
                params={
                    "action": "query",
                    "meta": "tokens",
                    "type": "csrf",
                    "format": "json",
                },
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json().get("query", {}).get("tokens", {}).get("csrftoken")
        except Exception as exc:  # noqa: BLE001
            logger.warning("csrf token fetch failed: %s", exc)
            return None

    def _can_create_test_properties(self) -> bool:
        if self._test_can_create_properties is not None:
            return self._test_can_create_properties
        info = self._query_userinfo_rights()
        rights = {str(r) for r in (info.get("rights") or [])} if info else set()
        self._test_can_create_properties = "property-create" in rights
        return self._test_can_create_properties

    def _wbeditentity_new(self, *, new: str, data: dict[str, object]) -> str | None:
        """Create a property or item on the current wiki. Returns the new id."""
        login = self._login
        if login is None:
            return None
        csrf = self._get_csrf_token()
        if not csrf:
            return None
        api_url = _TEST_API if self._is_test else _WIKIDATA_API
        try:
            self._rate_limit()
            session = login.get_session()
            resp = session.post(
                api_url,
                data={
                    "action": "wbeditentity",
                    "new": new,
                    "data": json.dumps(data, ensure_ascii=False),
                    "token": csrf,
                    "format": "json",
                    "summary": "MHM Pipeline: test.wikidata.org remap stub",
                },
                timeout=60,
            )
            resp.raise_for_status()
            body = resp.json()
            entity = body.get("entity") or {}
            entity_id = str(entity.get("id") or "").strip()
            if entity_id:
                return entity_id
            logger.warning("wbeditentity new=%s failed: %s", new, body)
        except Exception as exc:  # noqa: BLE001
            logger.warning("wbeditentity new=%s error: %s", new, exc)
        return None

    def _create_test_property(self, label: str, datatype: str) -> str | None:
        if not self._can_create_test_properties():
            return None
        data = {
            "labels": {"en": {"language": "en", "value": label}},
            "datatype": datatype,
        }
        return self._wbeditentity_new(new="property", data=data)

    def _create_test_item_stub(self, label: str, live_qid: str) -> str | None:
        data = {
            "labels": {"en": {"language": "en", "value": label}},
            "descriptions": {
                "en": {
                    "language": "en",
                    "value": f"MHM test stub for live {live_qid}",
                },
            },
        }
        return self._wbeditentity_new(new="item", data=data)

    def _ensure_test_property_datatypes(self, pids: list[str]) -> None:
        missing = [p for p in pids if p not in self._test_property_datatypes]
        if not missing:
            return
        fetched = self._wbgetentities(missing, props="datatype")
        for pid in missing:
            ent = fetched.get(pid) if isinstance(fetched.get(pid), dict) else None
            if not ent or "missing" in ent:
                self._test_property_datatypes[pid] = None
            else:
                dt = ent.get("datatype")
                self._test_property_datatypes[pid] = str(dt) if dt else None

    def _ensure_test_entity_exists_flags(self, qids: list[str]) -> None:
        missing = [q for q in qids if q not in self._test_entity_exists]
        if not missing:
            return
        fetched = self._wbgetentities(missing, props="info")
        for qid in missing:
            ent = fetched.get(qid) if isinstance(fetched.get(qid), dict) else None
            self._test_entity_exists[qid] = bool(ent) and "missing" not in ent

    def _ensure_test_maps_for_item(self, item: WikidataItem) -> object:
        """Resolve live P/Q ids to test wiki ids (search, then stub CREATE)."""
        from converter.wikidata.property_labels import (  # noqa: PLC0415
            PROPERTY_LABELS,
            QID_LABELS,
        )
        from converter.wikidata.test_wiki_compat import (  # noqa: PLC0415
            WikiTestAdaptResult,
            WikiTestAdaptStats,
            choose_test_item,
            choose_test_property,
            collect_live_pids_with_types,
            collect_live_qids,
            expected_wikibase_datatype,
        )

        stats = WikiTestAdaptStats()
        live_pids = collect_live_pids_with_types(item)
        live_qids = collect_live_qids(item)
        self._ensure_test_property_datatypes([p for p, _ in live_pids])

        for live_pid, value_type in live_pids:
            if live_pid in self._test_pid_map:
                continue
            label = PROPERTY_LABELS.get(live_pid, "")
            hits = self._wbsearchentities(label, entity_type="property") if label else []
            test_pid = choose_test_property(
                live_pid,
                value_type,
                property_label=label,
                property_datatypes=self._test_property_datatypes,
                pid_map=self._test_pid_map,
                search_hits=hits,
            )
            if test_pid:
                self._test_pid_map[live_pid] = test_pid
                if test_pid != live_pid:
                    stats.properties_remapped += 1
                self._ensure_test_property_datatypes([test_pid])
                continue
            expected = expected_wikibase_datatype(value_type)
            if label and expected:
                created = self._create_test_property(label, expected)
                if created:
                    self._test_pid_map[live_pid] = created
                    self._test_property_datatypes[created] = expected
                    stats.properties_created += 1

        for live_qid in live_qids:
            if live_qid in self._test_qid_map:
                continue
            gloss = QID_LABELS.get(live_qid, "")
            if not gloss:
                continue
            hits = self._wbsearchentities(gloss, entity_type="item")
            test_qid = choose_test_item(
                live_qid,
                item_label=gloss,
                qid_map=self._test_qid_map,
                search_hits=hits,
            )
            if test_qid:
                if not self._item_usable_as_test_reference(test_qid):
                    test_qid = None
            if test_qid:
                self._test_qid_map[live_qid] = test_qid
                stats.classes_remapped += 1
                self._test_entity_exists[test_qid] = True
                continue
            created = self._create_test_item_stub(gloss, live_qid)
            if created:
                self._test_qid_map[live_qid] = created
                self._test_stubs_we_created.add(created)
                stats.classes_created += 1
                self._test_entity_exists[created] = True

        return stats

    def _adapt_item_for_test_wiki(
        self, item: WikidataItem,
    ) -> tuple[WikidataItem, object]:
        """Remap then strip claims test.wikidata.org cannot accept (W-182/W-183)."""
        from converter.wikidata.test_wiki_compat import (  # noqa: PLC0415
            WikiTestAdaptResult,
            collect_test_wiki_ids,
            filter_item_for_test_wiki,
            rewrite_item_with_maps,
        )

        stats = self._ensure_test_maps_for_item(item)
        rewritten = rewrite_item_with_maps(
            item,
            self._test_pid_map,
            self._test_qid_map,
        )
        test_pids, test_qids = collect_test_wiki_ids(rewritten)
        self._ensure_test_property_datatypes(test_pids)
        self._ensure_test_entity_exists_flags(test_qids)
        existing = {qid for qid, ok in self._test_entity_exists.items() if ok}
        from converter.wikidata.property_labels import QID_LABELS  # noqa: PLC0415

        allowed = set(self._test_qid_map.values()) | set(self._test_stubs_we_created)
        filtered, skipped = filter_item_for_test_wiki(
            rewritten,
            property_datatypes=self._test_property_datatypes,
            existing_item_ids=existing,
            live_static_qids=set(QID_LABELS),
            allowed_item_ids=allowed,
        )
        if skipped:
            logger.info(
                "test.wikidata.org adapt for %s dropped %d snaks: %s",
                item.local_id,
                len(skipped),
                "; ".join(skipped[:12]),
            )
        return filtered, WikiTestAdaptResult(stats=stats, skipped=skipped)

    def upload_item(self, item: WikidataItem) -> UploadResult:
        """Upload a single item to Wikidata with retry logic and smart diffing.

        For existing items: fetches current claims, compares with new claims,
        and only writes if there are actual changes (avoids duplicates).

        SAFETY: Only modifies existing items that were created by the MHM Pipeline
        (verified by checking for P1343=Q118384267 marker). If an existing item
        was NOT created by us, we skip it entirely to avoid modifying community items.

        Returns:
            UploadResult with QID and status.
        """
        self._check_moratorium_for_live()
        self._init_wbi()

        skipped_test_claims: list[str] = []
        test_adapt_result = None

        # Ownership BEFORE test adapt: never stub-CREATE properties/classes
        # for an item we are about to skip (Rule W-184).
        if item.existing_qid and not self._is_our_item(item.existing_qid):
            logger.warning(
                "SAFETY: Skipping %s — existing item %s was NOT authored by the "
                "authenticated user",
                item.local_id,
                item.existing_qid,
            )
            return UploadResult(
                local_id=item.local_id,
                qid=item.existing_qid,
                status="skipped",
                message=(
                    f"Skipped {item.existing_qid} — not authored by the authenticated "
                    "user (Rule-38; no UPDATE of foreign items on "
                    f"{'test.wikidata.org' if self._is_test else 'wikidata.org'})"
                ),
            )

        if self._is_test:
            item, test_adapt_result = self._adapt_item_for_test_wiki(item)
            if test_adapt_result is not None:
                skipped_test_claims = test_adapt_result.skipped

        # Bug fix 2026-04-16 (deeper audit Fix #9): respect the community
        # convention {{bots|deny=…}} on the item's talk page. If the talk
        # page denies our bot (or all bots), skip the write entirely. This
        # is community norm rather than technically enforced by MediaWiki,
        # but ignoring it has historically led to bot-blocks at WD:AN.
        if item.existing_qid and self._bot_excluded(item.existing_qid):
            logger.warning(
                "Skipping %s — talk page has {{bots|deny=…}} excluding this bot",
                item.existing_qid,
            )
            return UploadResult(
                local_id=item.local_id,
                qid=item.existing_qid,
                status="skipped",
                message=f"Skipped {item.existing_qid} — bot exclusion template on talk page",
            )

        last_error = ""
        for attempt in range(1, _MAX_RETRIES + 1):
            self._rate_limit()
            try:
                wbi_item, new_claims, added_props = self._build_wbi_item(item)

                # Skip write if existing item has no new claims
                if item.existing_qid and new_claims == 0:
                    return UploadResult(
                        local_id=item.local_id,
                        qid=item.existing_qid,
                        status="exists",
                        message=f"No changes needed for {item.existing_qid}",
                    )

                # Bot policy compliance (Wikidata:Bots): every write needs
                # a descriptive edit summary so reviewers can understand
                # the change at a glance.
                action = "Update" if item.existing_qid else "Create"
                edit_summary = (
                    f"MHM Pipeline: {action} {item.entity_type} from NLI "
                    f"Hebrew Manuscripts catalog (Ktiv); +{new_claims} claims; "
                    f"local_id={item.local_id}"
                )
                # Fix 2026-04-15 third audit Fix #17: Wikidata API rejects
                # edit summaries longer than 500 characters. Truncate to 497
                # to leave room for the "..." suffix.
                if len(edit_summary) > 497:
                    edit_summary = edit_summary[:497] + "..."
                # DEFENSE-IN-DEPTH #4 (rule 38): immediately before the
                # only .write() call in the codebase, re-assert that the
                # target QID is ours. This is the last gate — if someone
                # introduces a new upload path or bypasses the earlier
                # guards, this one fires. FAIL CLOSED.
                self._assert_modifiable(
                    item.existing_qid or "", stage="pre_write",
                )
                # WikibaseIntegrator ≥0.12 takes ``is_bot`` (never bare ``bot=``;
                # Rule W-180). Default is_bot=False — accounts without the
                # MediaWiki bot right hard-fail (Rule W-181 / export-40).
                result = wbi_item.write(
                    summary=edit_summary,
                    is_bot=self._mark_as_bot,
                )
                qid = result.id if result else None

                from converter.wikidata.test_wiki_compat import (  # noqa: PLC0415
                    format_test_wiki_outcome_note,
                )

                skip_note = format_test_wiki_outcome_note(test_adapt_result)
                if item.existing_qid:
                    from collections import Counter  # noqa: PLC0415

                    prop_summary = ", ".join(
                        f"{pid}x{cnt}" for pid, cnt in Counter(added_props).most_common(5)
                    )
                    return UploadResult(
                        local_id=item.local_id,
                        qid=qid,
                        status="updated",
                        message=(
                            f"Updated {qid}: +{new_claims} claims ({prop_summary})"
                            + skip_note
                        ),
                        added_properties=added_props,
                    )
                return UploadResult(
                    local_id=item.local_id,
                    qid=qid,
                    status="success",
                    message=f"Created {qid} ({new_claims} claims){skip_note}",
                    added_properties=added_props,
                )
            except UnauthorisedModificationError as exc:
                # Rule 38 tripwire — any defense-in-depth guard fired.
                # This is not retryable: the item is not ours, period.
                # Convert to a clean "skipped" result instead of bubbling
                # up and failing the whole batch.
                logger.error(
                    "SAFETY tripwire (%s) for %s → %s: %s",
                    exc.stage, item.local_id, exc.qid, exc,
                )
                return UploadResult(
                    local_id=item.local_id,
                    qid=exc.qid,
                    status="skipped",
                    message=(
                        f"Blocked by rule-38 guard at stage {exc.stage!r}: "
                        f"{exc.qid} was not authored by the authenticated user."
                    ),
                )
            except Exception as exc:
                last_error = str(exc)
                # Bug fix 2026-04-16 (deeper audit Fix #8): inspect the
                # error to decide retry strategy. editconflict means
                # someone edited the item between our get() and write();
                # the fix is to re-fetch and re-build the WBI item from
                # scratch (which the next loop iteration does, since
                # _build_wbi_item is called inside the try block). badtoken
                # means the CSRF token expired; same re-build path applies.
                err_lower = last_error.lower()
                is_conflict = "editconflict" in err_lower
                is_badtoken = "badtoken" in err_lower
                # "bot" right missing is configuration, not transient —
                # three retries only burn wall clock (export-40).
                is_bot_right = (
                    '"bot" right' in err_lower
                    or "do not have the \"bot\" right" in err_lower
                    or "do not have the 'bot' right" in err_lower
                )
                # Generic MediaWiki permissiondenied (export-41): almost always
                # missing bot-password grants (createpage / edit), never fixed
                # by retrying the same write three times.
                is_permission = (
                    "permissions needed" in err_lower
                    or "permissiondenied" in err_lower
                )
                is_bad_value_type = "bad value type" in err_lower
                mw_code = ""
                if hasattr(exc, "code"):
                    mw_code = str(getattr(exc, "code") or "")
                    if mw_code.lower() == "permissiondenied":
                        is_permission = True
                logger.warning(
                    "Upload attempt %d/%d for %s failed (%s%s): %s",
                    attempt,
                    _MAX_RETRIES,
                    item.local_id,
                    "editconflict" if is_conflict else "error",
                    " badtoken" if is_badtoken else "",
                    exc,
                )
                if is_bot_right:
                    return UploadResult(
                        local_id=item.local_id,
                        status="failed",
                        message=(
                            "Account lacks the MediaWiki 'bot' right but writes "
                            "requested is_bot=True. Unset WIKIDATA_MARK_AS_BOT "
                            f"(or pass mark_as_bot=False). Detail: {last_error[:160]}"
                        ),
                    )
                if is_permission:
                    wiki = "test.wikidata.org" if self._is_test else "www.wikidata.org"
                    return UploadResult(
                        local_id=item.local_id,
                        status="failed",
                        message=(
                            f"MediaWiki permissiondenied on {wiki}"
                            + (f" (code={mw_code})" if mw_code else "")
                            + ". Bot password needs: High-volume editing; "
                            "Edit existing pages; Create, edit, and move pages. "
                            f"Detail: {last_error[:120]}"
                        ),
                    )
                if is_bad_value_type:
                    wiki = "test.wikidata.org" if self._is_test else "www.wikidata.org"
                    return UploadResult(
                        local_id=item.local_id,
                        status="failed",
                        message=(
                            f"Bad claim datatype on {wiki} (not retried). "
                            "On test.wikidata.org, P-ids are not the public "
                            "Wikidata properties — remaining mismatches should "
                            "have been stripped (Rules W-182/W-183). "
                            f"Detail: {last_error[:160]}"
                        ),
                    )
                if attempt < _MAX_RETRIES:
                    # Shorter backoff for conflicts (someone is actively
                    # editing this item, so a quick retry is more likely
                    # to succeed); longer for unknown failures.
                    delay = (_RETRY_DELAY_SECONDS * attempt) if not is_conflict else 1.0
                    time.sleep(delay)
        return UploadResult(
            local_id=item.local_id,
            status="failed",
            message=f"Failed after {_MAX_RETRIES} attempts: {last_error[:200]}",
        )

    def upload_all(
        self,
        items: list[WikidataItem],
        progress_cb: Callable[[int, int, str], None] | None = None,
        entity_cb: Callable[[str, str, str | None, str | None], None] | None = None,
    ) -> list[UploadResult]:
        """Upload all items with progress tracking.

        Args:
            items: List of WikidataItem instances.
            progress_cb: Called with (completed, total, message).
            entity_cb: Called with (local_id, status, qid, message) per entity.

        Returns:
            List of UploadResult instances.
        """
        results: list[UploadResult] = []
        total = len(items)

        # Track created QIDs so manuscripts can reference freshly created persons
        created_qids: dict[str, str] = {}

        # Batch tracking: pause between batches (only when batch_mode enabled)
        batch_size = 45 if self._batch_mode else 0
        batch_count = 0

        for idx, item in enumerate(items):
            if entity_cb:
                entity_cb(item.local_id, "uploading", None, f"Uploading {item.entity_type}...")

            # Resolve __LOCAL: references to QIDs of previously uploaded items
            for stmt in item.statements:
                if isinstance(stmt.value, str) and stmt.value.startswith("__LOCAL:"):
                    local_ref = stmt.value[len("__LOCAL:") :]
                    resolved_qid = created_qids.get(local_ref)
                    if resolved_qid:
                        stmt.value = resolved_qid

            result = self.upload_item(item)
            results.append(result)
            batch_count += 1

            # Remember the QID for future __LOCAL: resolution (new and existing
            # items we wrote). On test, never wire later claims to a skipped
            # foreign QID — that number is a different entity there (W-183/W-184).
            # Live still resolves skipped-foreign so manuscripts can *link* to
            # community persons without UPDATING them.
            if result.qid and result.status in ("success", "exists", "updated"):
                created_qids[item.local_id] = result.qid
            elif (
                result.qid
                and result.status == "skipped"
                and not self._is_test
            ):
                created_qids[item.local_id] = result.qid

            if entity_cb:
                entity_cb(item.local_id, result.status, result.qid, result.message)

            if progress_cb:
                progress_cb(idx + 1, total, result.message)

            # Pause between batches to avoid rate limiting (only in batch mode)
            if batch_size > 0 and batch_count >= batch_size and idx + 1 < total:
                batch_num = (idx + 1) // batch_size
                total_batches = (total + batch_size - 1) // batch_size
                msg = f"Batch {batch_num}/{total_batches} complete. Pausing 30s..."
                logger.info(msg)
                if progress_cb:
                    progress_cb(idx + 1, total, msg)
                time.sleep(30)
                batch_count = 0

        success = sum(1 for r in results if r.status in ("success", "exists"))
        failed = sum(1 for r in results if r.status == "failed")
        logger.info(
            "Upload complete: %d/%d succeeded, %d failed",
            success,
            total,
            failed,
        )
        return results

    @staticmethod
    def write_results(results: list[UploadResult], output_path: Path) -> Path:
        """Write upload results to a JSON file.

        Args:
            results: List of UploadResult instances.
            output_path: Destination file path.

        Returns:
            The output path written to.
        """
        data = [
            {
                "local_id": r.local_id,
                "qid": r.qid,
                "status": r.status,
                "message": r.message,
            }
            for r in results
        ]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return output_path
