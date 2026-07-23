"""Read-only Wikibase Cloud connection helper for the HMO Wikibase tab.

The :class:`WikibaseCloudClient` is read-only and used by the HMO Wikibase
preview panel for siteinfo checks.

The :class:`WikibaseCloudWriter` is the authenticated companion that writes
IIIF manifest JSON pages and Wikibase entities on ``mhm-hmo.wikibase.cloud``.
Production auth uses server-held OAuth 2.0 (Heroku config vars); the legacy
:class:`WikibaseBotCredentials` bot-password path remains for unit tests.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

import requests

from converter.wikibase.label_sanitize import sanitize_monolingual_map

logger = logging.getLogger(__name__)

_DEFAULT_HMO_WIKIBASE_URL = "https://mhm-hmo.wikibase.cloud"
DEFAULT_WIKIBASE_WRITE_USER = "mhm-pipeline-web"


def wikibase_edit_summary(detail: str, *, write_user: str = DEFAULT_WIKIBASE_WRITE_USER) -> str:
    """Standard edit-summary prefix so wiki history shows mhm-pipeline-web."""
    return f"{write_user}: {detail}"


@dataclass(frozen=True)
class WikibaseEndpointConfig:
    """Configuration for a Wikibase Cloud endpoint."""

    base_url: str
    display_name: str | None = None

    @property
    def api_url(self) -> str:
        """Return the MediaWiki API URL for the configured Wikibase base URL."""
        normalized = self.base_url.rstrip("/")
        if normalized.endswith("/w/api.php"):
            return normalized
        return f"{normalized}/w/api.php"


@dataclass(frozen=True)
class WikibaseConnectionResult:
    """Outcome of a read-only Wikibase Cloud siteinfo connection test."""

    ok: bool
    site_name: str
    generator: str
    api_url: str
    message: str


class WikibaseCloudClient:
    """Small read-only client for checking a Wikibase Cloud endpoint."""

    def __init__(
        self,
        config: WikibaseEndpointConfig,
        *,
        session: requests.Session | None = None,
        timeout: float = 20.0,
    ) -> None:
        self._config = config
        self._session = session or requests.Session()
        self._timeout = timeout

    @classmethod
    def config_for_mhm_hmo_cloud(cls) -> WikibaseEndpointConfig:
        """Return the default endpoint configuration for the MHM HMO Wikibase."""
        return WikibaseEndpointConfig(
            base_url=_DEFAULT_HMO_WIKIBASE_URL,
            display_name="MHM HMO Wikibase",
        )

    @classmethod
    def for_mhm_hmo_cloud(cls, *, timeout: float = 20.0) -> WikibaseCloudClient:
        """Create a read-only client for the MHM HMO Wikibase Cloud instance."""
        return cls(cls.config_for_mhm_hmo_cloud(), timeout=timeout)

    def test_connection(self) -> WikibaseConnectionResult:
        """Fetch read-only siteinfo and return a graceful connection result."""
        api_url = self._config.api_url
        params: dict[str, str] = {
            "action": "query",
            "meta": "siteinfo",
            "siprop": "general",
            "format": "json",
        }
        try:
            response = self._session.get(api_url, params=params, timeout=self._timeout)
            response.raise_for_status()
            payload = cast(object, response.json())
        except requests.RequestException as exc:
            return self._failure(api_url, f"Network error: {exc}")
        except ValueError as exc:
            return self._failure(api_url, f"Invalid JSON response: {exc}")

        if not isinstance(payload, Mapping):
            return self._failure(api_url, "Unexpected API response: root is not an object")

        error_message = _api_error_message(payload)
        if error_message is not None:
            return self._failure(api_url, error_message)

        general = _nested_mapping(payload, "query", "general")
        if general is None:
            return self._failure(api_url, "Unexpected API response: missing query.general")

        site_name = _string_value(general, "sitename") or self._config.display_name or ""
        generator = _string_value(general, "generator") or ""
        if site_name == "" and generator == "":
            return self._failure(api_url, "Unexpected API response: missing site metadata")

        return WikibaseConnectionResult(
            ok=True,
            site_name=site_name,
            generator=generator,
            api_url=api_url,
            message="Connection successful",
        )

    def _failure(self, api_url: str, message: str) -> WikibaseConnectionResult:
        """Build a consistent failed connection result."""
        return WikibaseConnectionResult(
            ok=False,
            site_name=self._config.display_name or "",
            generator="",
            api_url=api_url,
            message=message,
        )


def _nested_mapping(
    mapping: Mapping[object, object],
    first_key: str,
    second_key: str,
) -> Mapping[object, object] | None:
    first_value = mapping.get(first_key)
    if not isinstance(first_value, Mapping):
        return None
    second_value = first_value.get(second_key)
    if not isinstance(second_value, Mapping):
        return None
    return second_value


def _string_value(mapping: Mapping[object, object], key: str) -> str | None:
    value = mapping.get(key)
    if isinstance(value, str):
        return value
    return None


def _api_error_message(payload: Mapping[object, object]) -> str | None:
    error = payload.get("error")
    if not isinstance(error, Mapping):
        return None

    code = _string_value(error, "code")
    info = _string_value(error, "info")
    if code is not None and info is not None:
        return f"API error {code}: {info}"
    if info is not None:
        return f"API error: {info}"
    if code is not None:
        return f"API error {code}"
    return "API error"


def format_wbi_exception(exc: BaseException) -> str:
    """Turn a wikibaseintegrator failure into a curator-visible message."""
    try:
        from wikibaseintegrator.wbi_exceptions import MWApiError  # noqa: PLC0415
    except ImportError:
        return str(exc)

    if not isinstance(exc, MWApiError):
        return str(exc)

    parts: list[str] = []
    code = getattr(exc, "code", None)
    info = getattr(exc, "info", None)
    if code:
        parts.append(f"code={code}")
    if info and info not in parts:
        parts.append(f"info={info}")
    messages = getattr(exc, "messages", None)
    if messages:
        parts.append(f"messages={messages}")
    try:
        conflicts = exc.get_conflicting_entity_ids()
    except Exception:  # noqa: BLE001
        conflicts = None
    if conflicts:
        parts.append(f"conflicts={conflicts}")
    langs = getattr(exc, "get_languages", None)
    if callable(langs):
        try:
            bad_langs = langs()
            if bad_langs:
                parts.append(f"languages={bad_langs}")
        except Exception:  # noqa: BLE001
            pass
    if parts:
        return "; ".join(parts)
    return str(exc)


# ─────────────────────────────────────────────────────────────────────
# Rule 45 (Phase 3, 2026-05-17): Wikibase Cloud authenticated writer
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WikibaseCloudAuth:
    """Server-held OAuth 2.0 credentials for Wikibase Cloud writes."""

    mode: Literal["oauth2"]
    client_id: str
    client_secret: str
    access_token: str | None = None

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"WikibaseCloudAuth(mode={self.mode!r}, "
            f"client_id={self.client_id!r}, access_token={'<set>' if self.access_token else None})"
        )


@dataclass(frozen=True)
class WikibaseBotCredentials:
    """Bot password tuple issued by ``Special:BotPasswords``.

    Login name format: ``"<username>@<bot_name>"``.
    """

    username: str
    bot_name: str
    password: str

    @property
    def login_name(self) -> str:
        """Build the canonical login name used by MediaWiki API ``action=login``."""
        return f"{self.username}@{self.bot_name}"

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"WikibaseBotCredentials(username={self.username!r}, "
            f"bot_name={self.bot_name!r}, password='<REDACTED>')"
        )


@dataclass(frozen=True)
class EditOutcome:
    """Result of a single ``WikibaseCloudWriter.edit_page`` call."""

    page_url: str
    status: str  # "created" | "updated" | "unchanged" | "failed"
    message: str
    edit_id: int | None  # pageid from the API
    new_revid: int | None  # revid for the new revision (for permalinks)


@dataclass(frozen=True)
class CreateAccountOutcome:
    """Result of a MediaWiki ``action=createaccount`` call."""

    ok: bool
    username: str
    status: str  # "created" | "exists" | "failed"
    message: str


@dataclass(frozen=True)
class EntityEditOutcome:
    """Result of a WikibaseIntegrator item/property/claim write.

    Sibling to :class:`EditOutcome`, for the Wikibase *entity* API
    (items/properties/claims) rather than plain MediaWiki pages.
    """

    entity_id: str | None  # QID or PID, None on failure
    status: str  # "created" | "updated" | "failed"
    message: str
    page_url: str | None


class WikibaseCloudWriter:
    """Authenticated MediaWiki API writer for a Wikibase Cloud instance."""

    _MAX_RETRIES = 6
    _BASE_BACKOFF_SECONDS = 1.0
    _MAX_BACKOFF_SECONDS = 30.0

    def __init__(
        self,
        config: WikibaseEndpointConfig,
        auth: WikibaseCloudAuth | WikibaseBotCredentials,
        *,
        session: requests.Session | None = None,
        timeout: float = 30.0,
        min_write_interval_seconds: float = 0.0,
        user_agent: str = "MHMPipeline/1.0 (https://github.com/alexandergolbergwix/pipeline)",
    ) -> None:
        self._config = config
        self._auth = auth
        self._session = session or requests.Session()
        self._session.headers["User-Agent"] = user_agent
        self._timeout = timeout
        if min_write_interval_seconds < 0:
            raise ValueError("min_write_interval_seconds must be non-negative")
        self._min_write_interval_seconds = min_write_interval_seconds
        self._write_throttle_lock = threading.Lock()
        self._last_entity_write_at = 0.0
        self._user_agent = user_agent
        self._csrf_token: str | None = None
        self._logged_in = False
        self._wbi: Any | None = None

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"WikibaseCloudWriter(base_url={self._config.base_url!r}, auth={self._auth!r})"

    @property
    def uses_oauth(self) -> bool:
        return isinstance(self._auth, WikibaseCloudAuth)

    @classmethod
    def for_mhm_hmo_cloud(
        cls,
        auth: WikibaseCloudAuth | WikibaseBotCredentials,
        *,
        timeout: float = 30.0,
    ) -> WikibaseCloudWriter:
        """Build a writer pointed at the default MHM HMO Wikibase Cloud."""
        return cls(
            WikibaseCloudClient.config_for_mhm_hmo_cloud(),
            auth,
            timeout=timeout,
        )

    # ── URL builders ─────────────────────────────────────────────────

    def page_url(self, title: str) -> str:
        """Build the human-readable page URL for a given title."""
        normalized = self._config.base_url.rstrip("/")
        return f"{normalized}/wiki/{title}"

    def raw_url(self, title: str) -> str:
        """Build the raw-content URL (IIIF consumers expect JSON)."""
        return f"{self.page_url(title)}?action=raw&ctype=application/json"

    # ── Auth ─────────────────────────────────────────────────────────

    def ensure_authenticated(self) -> None:
        """Establish an authenticated session (OAuth bearer or bot login)."""
        if self._logged_in:
            return
        if isinstance(self._auth, WikibaseCloudAuth):
            if self._auth.access_token:
                self._session.headers["Authorization"] = f"Bearer {self._auth.access_token}"
                self._logged_in = True
                return
            self._init_wbi()
            self._logged_in = True
            return
        self.login()

    def current_api_user(self) -> str:
        """Return the MediaWiki username for the active OAuth/bot session."""
        self.ensure_authenticated()
        payload = self._post_with_retry({
            "action": "query",
            "meta": "userinfo",
            "uiprop": "name",
            "format": "json",
        })
        userinfo = _nested_mapping(payload, "query", "userinfo")
        name = _string_value(userinfo or {}, "name")
        if not name:
            raise RuntimeError(f"userinfo missing name in {payload!r}")
        return name

    def wiki_user_exists(self, username: str) -> bool:
        """Return whether a local wiki account with ``username`` already exists."""
        payload = self._post_with_retry({
            "action": "query",
            "list": "users",
            "ususers": username,
            "usprop": "",
            "format": "json",
        })
        users_block = _nested_mapping(payload, "query", "users")
        if users_block is None:
            return False
        users = users_block.get("users")
        if not isinstance(users, list) or not users:
            return False
        first = users[0]
        if not isinstance(first, Mapping):
            return False
        return _string_value(first, "missing") is None

    def create_local_account(
        self,
        username: str,
        password: str,
        *,
        email: str,
    ) -> CreateAccountOutcome:
        """Create a local MediaWiki account using the active OAuth/bot session."""
        self.ensure_authenticated()
        if self.wiki_user_exists(username):
            return CreateAccountOutcome(
                ok=True,
                username=username,
                status="exists",
                message="account already exists",
            )

        token_payload = self._post_with_retry({
            "action": "query",
            "meta": "tokens",
            "type": "createaccount",
            "format": "json",
        })
        tokens = _nested_mapping(token_payload, "query", "tokens")
        create_token = _string_value(tokens or {}, "createaccounttoken")
        if not create_token:
            return CreateAccountOutcome(
                ok=False,
                username=username,
                status="failed",
                message=f"createaccount token missing in {token_payload!r}",
            )

        result = self._post_with_retry({
            "action": "createaccount",
            "username": username,
            "password": password,
            "retype": password,
            "email": email,
            "createreturnurl": self._config.base_url.rstrip("/"),
            "createtoken": create_token,
            "format": "json",
        })
        create_block = result.get("createaccount") if isinstance(result, Mapping) else None
        if isinstance(create_block, Mapping):
            outcome = _string_value(create_block, "status")
            if outcome == "PASS":
                return CreateAccountOutcome(
                    ok=True,
                    username=username,
                    status="created",
                    message="ok",
                )
            message = _string_value(create_block, "message") or outcome or "createaccount failed"
            return CreateAccountOutcome(
                ok=False,
                username=username,
                status="failed",
                message=message,
            )

        error_message = _api_error_message(result) or f"unexpected response: {result!r}"
        return CreateAccountOutcome(
            ok=False,
            username=username,
            status="failed",
            message=error_message,
        )

    def login(self) -> None:
        """Perform bot-password MediaWiki login (legacy / tests only)."""
        if not isinstance(self._auth, WikibaseBotCredentials):
            self.ensure_authenticated()
            return

        login_token_payload = self._post_with_retry(
            {
                "action": "query",
                "meta": "tokens",
                "type": "login",
                "format": "json",
            }
        )
        tokens = _nested_mapping(login_token_payload, "query", "tokens")
        login_token = _string_value(tokens or {}, "logintoken")
        if not login_token:
            raise RuntimeError("Failed to obtain login token from MediaWiki API")

        result = self._post_with_retry(
            {
                "action": "login",
                "lgname": self._auth.login_name,
                "lgpassword": self._auth.password,
                "lgtoken": login_token,
                "format": "json",
            }
        )
        login_block = result.get("login") if isinstance(result, Mapping) else None
        if not isinstance(login_block, Mapping):
            raise RuntimeError(f"Unexpected login response: {result!r}")
        outcome = _string_value(login_block, "result")
        if outcome != "Success":
            reason = _string_value(login_block, "reason") or outcome or "unknown"
            raise RuntimeError(f"Login failed ({reason})")
        self._logged_in = True

    def _get_csrf_token(self) -> str:
        """Return a cached CSRF token, fetching/refreshing if needed."""
        if self._csrf_token is not None:
            return self._csrf_token
        self.ensure_authenticated()
        payload = self._post_with_retry(
            {
                "action": "query",
                "meta": "tokens",
                "type": "csrf",
                "format": "json",
            }
        )
        tokens = _nested_mapping(payload, "query", "tokens")
        token = _string_value(tokens or {}, "csrftoken")
        if not token:
            raise RuntimeError(f"Failed to obtain CSRF token: {payload!r}")
        self._csrf_token = token
        return token

    # ── Read ─────────────────────────────────────────────────────────

    def read_page(self, title: str) -> str | None:
        """Read existing wikitext for the page, or ``None`` if it does not exist."""
        payload = self._post_with_retry(
            {
                "action": "parse",
                "page": title,
                "prop": "wikitext",
                "format": "json",
            }
        )
        # When the page is missing, MediaWiki returns an error block
        # rather than empty wikitext.
        if "error" in payload:
            error_block = payload["error"]
            if isinstance(error_block, Mapping):
                if _string_value(error_block, "code") == "missingtitle":
                    return None
            return None
        parse = payload.get("parse") if isinstance(payload, Mapping) else None
        if not isinstance(parse, Mapping):
            return None
        wikitext = parse.get("wikitext")
        if isinstance(wikitext, Mapping):
            text = wikitext.get("*")
            if isinstance(text, str):
                return text
        if isinstance(wikitext, str):
            return wikitext
        return None

    # ── Write ────────────────────────────────────────────────────────

    def edit_page(
        self,
        title: str,
        body: str,
        summary: str,
        *,
        content_model: str = "json",
    ) -> EditOutcome:
        """Create or update a page idempotently.

        If the existing wikitext matches the new body byte-for-byte
        (after stripping surrounding whitespace), no API write is sent
        and ``status="unchanged"`` is returned.
        """
        existing = self.read_page(title)
        if existing is not None and _content_hash(existing) == _content_hash(body):
            return EditOutcome(
                page_url=self.page_url(title),
                status="unchanged",
                message="content identical; edit skipped",
                edit_id=None,
                new_revid=None,
            )

        token = self._get_csrf_token()
        params: dict[str, str] = {
            "action": "edit",
            "title": title,
            "text": body,
            "summary": summary,
            "token": token,
            "contentmodel": content_model,
            "format": "json",
        }
        if isinstance(self._auth, WikibaseBotCredentials):
            params["bot"] = "1"
            params["assert"] = "bot"
        result = self._post_with_retry(params)

        # Stale CSRF? refresh once and retry.
        error = result.get("error") if isinstance(result, Mapping) else None
        if isinstance(error, Mapping):
            code = _string_value(error, "code")
            if code in ("badtoken", "notoken"):
                self._csrf_token = None
                params["token"] = self._get_csrf_token()
                result = self._post_with_retry(params)
                error = result.get("error") if isinstance(result, Mapping) else None

        if isinstance(error, Mapping):
            msg = _api_error_message(result) or str(error)
            return EditOutcome(
                page_url=self.page_url(title),
                status="failed",
                message=msg,
                edit_id=None,
                new_revid=None,
            )

        edit = result.get("edit") if isinstance(result, Mapping) else None
        if not isinstance(edit, Mapping):
            return EditOutcome(
                page_url=self.page_url(title),
                status="failed",
                message=f"Unexpected response: {result!r}",
                edit_id=None,
                new_revid=None,
            )
        outcome = _string_value(edit, "result")
        if outcome != "Success":
            return EditOutcome(
                page_url=self.page_url(title),
                status="failed",
                message=f"edit result={outcome!r}",
                edit_id=None,
                new_revid=None,
            )
        status = "updated" if edit.get("oldrevid") else "created"
        pageid_val = edit.get("pageid")
        revid_val = edit.get("newrevid")
        return EditOutcome(
            page_url=self.page_url(title),
            status=status,
            message="ok",
            edit_id=int(pageid_val) if isinstance(pageid_val, int) else None,
            new_revid=int(revid_val) if isinstance(revid_val, int) else None,
        )

    # ── Entity write (items / properties / claims) ──────────────────────
    #
    # Unlike ``edit_page`` (plain MediaWiki page edits for IIIF manifest
    # JSON, above), item/property/claim writes go through
    # ``wikibaseintegrator`` — the same library ``converter/wikidata/
    # uploader.py`` already uses for wikidata.org — rather than hand-rolled
    # ``wbeditentity``/``wbcreateclaim`` calls, so auth/CSRF/retry/MAXLAG
    # handling stays in one place across both Wikibase targets.

    def _init_wbi(self) -> Any:
        """Lazily build a WikibaseIntegrator instance pointed at this config."""
        if self._wbi is not None:
            return self._wbi

        from wikibaseintegrator import WikibaseIntegrator, wbi_login  # noqa: PLC0415
        from wikibaseintegrator.wbi_config import config as wbi_config  # noqa: PLC0415

        wbi_config["MEDIAWIKI_API_URL"] = self._config.api_url
        wbi_config["WIKIBASE_URL"] = self._config.base_url.rstrip("/")
        wbi_config["USER_AGENT"] = self._user_agent
        wbi_config["MAXLAG"] = 10
        wbi_config["BACKOFF_MAX_TRIES"] = self._MAX_RETRIES
        wbi_config["BACKOFF_MAX_VALUE"] = int(self._MAX_BACKOFF_SECONDS)

        if isinstance(self._auth, WikibaseCloudAuth):
            if self._auth.access_token:
                oauth_session = requests.Session()
                oauth_session.headers.update({
                    "Authorization": f"Bearer {self._auth.access_token}",
                    "User-Agent": self._user_agent,
                })
                login = wbi_login._Login(  # noqa: SLF001
                    session=oauth_session,
                    mediawiki_api_url=self._config.api_url,
                    user_agent=self._user_agent,
                )
            else:
                login = wbi_login.OAuth2(
                    consumer_token=self._auth.client_id,
                    consumer_secret=self._auth.client_secret,
                    mediawiki_api_url=self._config.api_url,
                    user_agent=self._user_agent,
                )
        else:
            login = wbi_login.Login(
                user=self._auth.login_name,
                password=self._auth.password,
                mediawiki_api_url=self._config.api_url,
                user_agent=self._user_agent,
            )
        self._wbi = WikibaseIntegrator(login=login)
        return self._wbi

    def create_item(
        self,
        labels: Mapping[str, str],
        descriptions: Mapping[str, str],
        *,
        claims: Sequence[Any] | None = None,
        aliases: Mapping[str, Sequence[str]] | None = None,
        summary: str = "",
    ) -> EntityEditOutcome:
        """Create a new Wikibase Item.

        ``claims`` is a sequence of already-built
        ``wikibaseintegrator.datatypes`` claim objects (e.g.
        ``datatypes.Item(prop_nr="P1", value="Q2")``) — callers build the
        claim with the right datatype, this method only adds and writes it.
        """
        wbi = self._init_wbi()
        entity = wbi.item.new()
        _apply_labels_descriptions_aliases(entity, labels, descriptions, aliases)
        return self._write_entity(entity, claims, summary)

    def update_item(
        self,
        entity_id: str,
        labels: Mapping[str, str],
        descriptions: Mapping[str, str],
        *,
        claims: Sequence[Any] | None = None,
        aliases: Mapping[str, Sequence[str]] | None = None,
        summary: str = "",
    ) -> EntityEditOutcome:
        """Refresh labels/descriptions and merge claims onto an already
        existing item.

        Claims use ``ActionIfExists.APPEND_OR_REPLACE``: a claim whose
        property+value already matches is left alone (no duplicate is
        added), a claim for a new value is appended, and any statement
        a curator added by hand directly on the wiki (not present in
        ``claims``) is left untouched — this is a merge, not a
        wholesale replace of the item's claims.
        """
        try:
            entity = self._get_wbi_entity(entity_id)
        except Exception as exc:  # noqa: BLE001 - report, never raise into the caller
            msg = format_wbi_exception(exc)
            logger.warning("Wikibase entity fetch failed for update %s: %s", entity_id, msg)
            return EntityEditOutcome(
                entity_id=entity_id, status="failed", message=msg,
                page_url=self.page_url(_entity_page_title(entity_id)),
            )

        _apply_labels_descriptions_aliases(entity, labels, descriptions, aliases)
        if claims:
            from wikibaseintegrator.wbi_enums import ActionIfExists  # noqa: PLC0415

            entity.claims.add(claims, action_if_exists=ActionIfExists.APPEND_OR_REPLACE)

        try:
            write_kwargs: dict[str, Any] = {
                "summary": summary or wikibase_edit_summary("entity update"),
            }
            if isinstance(self._auth, WikibaseBotCredentials):
                write_kwargs["bot"] = True
            written = entity.write(**write_kwargs)
        except Exception as exc:  # noqa: BLE001 - report, never raise into the caller
            msg = format_wbi_exception(exc)
            logger.warning("Wikibase entity update failed for %s: %s", entity_id, msg)
            return EntityEditOutcome(
                entity_id=entity_id, status="failed", message=msg,
                page_url=self.page_url(_entity_page_title(entity_id)),
            )
        return EntityEditOutcome(
            entity_id=written.id,
            status="updated",
            message="ok",
            page_url=self.page_url(_entity_page_title(written.id)),
        )

    def create_property(
        self,
        labels: Mapping[str, str],
        descriptions: Mapping[str, str],
        datatype: str,
        *,
        claims: Sequence[Any] | None = None,
        aliases: Mapping[str, Sequence[str]] | None = None,
        summary: str = "",
    ) -> EntityEditOutcome:
        """Create a new Wikibase Property with the given datatype."""
        wbi = self._init_wbi()
        entity = wbi.property.new(datatype=datatype)
        _apply_labels_descriptions_aliases(entity, labels, descriptions, aliases)
        return self._write_entity(entity, claims, summary)

    def _write_entity(
        self, entity: Any, claims: Sequence[Any] | None, summary: str
    ) -> EntityEditOutcome:
        self._wait_for_entity_write_slot()
        if claims:
            for claim in claims:
                entity.claims.add(claim)
        try:
            write_kwargs: dict[str, Any] = {
                "summary": summary or wikibase_edit_summary("entity write"),
            }
            if isinstance(self._auth, WikibaseBotCredentials):
                write_kwargs["bot"] = True
            written = entity.write(**write_kwargs)
        except Exception as exc:  # noqa: BLE001 - report, never raise into the caller
            msg = format_wbi_exception(exc)
            logger.warning("Wikibase entity write failed: %s", msg)
            return EntityEditOutcome(
                entity_id=None, status="failed", message=msg, page_url=None
            )
        entity_id = written.id
        return EntityEditOutcome(
            entity_id=entity_id,
            status="created",
            message="ok",
            page_url=self.page_url(_entity_page_title(entity_id)),
        )

    def _get_wbi_entity(self, entity_id: str) -> Any:
        """Fetch a live item or property by id, dispatching on the Q/P prefix."""
        wbi = self._init_wbi()
        return wbi.item.get(entity_id) if entity_id.startswith("Q") else wbi.property.get(entity_id)

    def add_claim(
        self, entity_id: str, claim: Any, *, summary: str = ""
    ) -> EntityEditOutcome:
        """Add one already-built claim to an existing item or property."""
        try:
            self._wait_for_entity_write_slot()
            entity = self._get_wbi_entity(entity_id)
            entity.claims.add(claim)
            write_kwargs: dict[str, Any] = {
                "summary": summary or wikibase_edit_summary("add claim"),
            }
            if isinstance(self._auth, WikibaseBotCredentials):
                write_kwargs["bot"] = True
            written = entity.write(**write_kwargs)
        except Exception as exc:  # noqa: BLE001 - report, never raise into the caller
            msg = format_wbi_exception(exc)
            logger.warning("Wikibase claim write failed on %s: %s", entity_id, msg)
            return EntityEditOutcome(
                entity_id=entity_id,
                status="failed",
                message=msg,
                page_url=self.page_url(_entity_page_title(entity_id)),
            )
        return EntityEditOutcome(
            entity_id=written.id,
            status="updated",
            message="ok",
            page_url=self.page_url(_entity_page_title(written.id)),
        )

    def _wait_for_entity_write_slot(self) -> None:
        """Throttle entity writes without delaying read-only API calls."""
        with self._write_throttle_lock:
            now = time.monotonic()
            wait = self._min_write_interval_seconds - (now - self._last_entity_write_at)
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._last_entity_write_at = now

    def get_entity(self, entity_id: str) -> Mapping[str, Any] | None:
        """Fetch an entity by QID/PID. Verification only, never used for writes."""
        try:
            entity = self._get_wbi_entity(entity_id)
        except Exception:  # noqa: BLE001 - "not found"/network errors both mean "no entity"
            return None
        return cast(Mapping[str, Any], entity.get_json())

    # ── HTTP plumbing ────────────────────────────────────────────────

    def _post_with_retry(self, params: dict[str, str]) -> Mapping[object, object]:
        """POST to the MediaWiki API with exponential-backoff retry.

        Retries on connection / timeout / 5xx responses. Treats 4xx
        responses (other than 429) as terminal and returns the JSON
        body for the caller to inspect (it will contain the API error
        block in the standard MediaWiki shape).
        """
        api_url = self._config.api_url
        last_exc: Exception | None = None
        for attempt in range(self._MAX_RETRIES):
            try:
                response = self._session.post(
                    api_url, data=params, timeout=self._timeout
                )
                if response.status_code == 429 or response.status_code >= 500:
                    self._sleep_for_backoff(attempt)
                    continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, Mapping):
                    raise RuntimeError(f"API returned non-object payload: {payload!r}")
                return payload
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                last_exc = exc
                if attempt == self._MAX_RETRIES - 1:
                    raise
                self._sleep_for_backoff(attempt)
        # Defensive: loop should always either return or raise above.
        raise RuntimeError(  # pragma: no cover - defensive
            f"All retries exhausted: {last_exc!r}"
        )

    def _sleep_for_backoff(self, attempt: int) -> None:
        """Sleep for ``min(2**attempt, MAX_BACKOFF)`` seconds."""
        delay = min(
            self._BASE_BACKOFF_SECONDS * (2**attempt), self._MAX_BACKOFF_SECONDS
        )
        time.sleep(delay)


def _content_hash(text: str) -> str:
    """Stable SHA-256 of the stripped page body for idempotency checks."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def _entity_page_title(entity_id: str) -> str:
    """Build the ``Item:Q123`` / ``Property:P123`` page title for an entity id."""
    if entity_id.startswith("Q"):
        return f"Item:{entity_id}"
    if entity_id.startswith("P"):
        return f"Property:{entity_id}"
    return entity_id


def _apply_labels_descriptions_aliases(
    entity: Any,
    labels: Mapping[str, str],
    descriptions: Mapping[str, str],
    aliases: Mapping[str, Sequence[str]] | None,
) -> None:
    """Set labels/descriptions/aliases on a freshly-built WBI item/property."""
    for lang, value in sanitize_monolingual_map(labels).items():
        entity.labels.set(lang, value)
    for lang, value in sanitize_monolingual_map(descriptions).items():
        entity.descriptions.set(lang, value)
    if aliases:
        for lang, values in aliases.items():
            code = str(lang or "").strip().lower()
            if code in {"", "und"}:
                code = "en"
            for value in values:
                entity.aliases.set(code, value)
