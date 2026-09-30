"""
Authentication endpoints.

Logging in sets a ``HttpOnly`` session cookie plus a readable CSRF cookie. The
session token is only returned in the response body when the caller explicitly
asks for it (``bearer: true``), which is what the CLI and automation do; the
browser never needs it, and a token the browser cannot read is a token an XSS
bug cannot steal.

There are two ways in. A person signs in with their account: username,
password and the code of their authenticator, answered with one uniform error
whatever was wrong (ENS op.acc.6.7). The master token still signs in too: it is
the whole console while the server has no account, the break-glass credential
once it has, and only account recovery under the ENS profile; every use of it
is on record. What either may do is decided by :mod:`noust.web.permissions`,
not here; the account-management endpoints are in
:mod:`noust.web.api.auth_accounts`.
"""

from __future__ import annotations

import socket
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from noust import __version__
from noust.core import paths
from noust.core.accounts import Account, AuthenticationFailed
from noust.core.accounts.model import AccountError
from noust.core.accounts.passkeys import PasskeyManager
from noust.core.totp import provisioning_uri
from noust.web.api.auth_accounts import AccountInfo
from noust.web.api.auth_accounts import router as accounts_router
from noust.web.api.central import CentralInfo, session_central
from noust.web.api.deps import NoustErrorRoute, ensure_elevated, require_elevated
from noust.web.auth import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    IssuedSession,
    actor_label,
    audit_event,
    bearer_token,
    get_audit_logger,
    get_client_ip,
    is_fleet,
    is_secure_request,
    record_auth_failure,
    require_auth,
    verify_credential,
)
from noust.web.permissions import ALL_PERMISSIONS, DESCRIPTIONS, Permission
from noust.web.permissions.enforce import has_permission
from noust.web.permissions.roles import GRANT_COMPAT, ROLE_PERMISSIONS
from noust.web.server import get_brute_force, get_token_manager

# The error boundary every other API router already has: without it a
# SecurityError from the two-factor manager would crash the route instead of
# answering 400 with the actionable half attached.
router = APIRouter(route_class=NoustErrorRoute)

#: Historical name of the auth dependency. Kept as an alias so the rest of the
#: API keeps working while there is exactly one implementation.
get_current_session = require_auth

#: The one answer to a refused account sign-in or confirmation, whatever was
#: wrong: naming it would tell a guesser which half of the credential was right.
INVALID_CREDENTIALS = "Invalid credentials."


class LoginRequest(BaseModel):
    """
    Login request body: an account, or the master token.

    Attributes:
        username: The account's name. With it, ``password`` (and
            ``totp_code`` for an account with an authenticator) sign in as
            that person.
        password: The account's password.
        token: The master access token, for the break-glass sign-in; ignored
            when ``username`` is given.
        bearer: Whether to also return the session token in the response, for
            clients without a cookie jar.
        totp_code: Second factor - a six-digit authenticator code or a backup
            code: the account's own, or the console's for the master token.
    """

    username: str | None = Field(default=None, max_length=128)
    password: str | None = Field(default=None, max_length=1024)
    token: str | None = Field(default=None, max_length=512)
    bearer: bool = False
    totp_code: str | None = Field(default=None, max_length=64)


class LoginResponse(BaseModel):
    """
    Login response body.

    Attributes:
        success: Always true when the request succeeded.
        expires_in: Session lifetime in seconds.
        csrf_token: Token to echo in the ``X-WASM-CSRF`` header on mutations.
        session_token: Session token, only present for ``bearer`` clients.
        account: The account signed in; None for the master token.
        grant: For the master token, how it holds the console: ``compat``
            (no account exists yet), ``break_glass`` or ``recovery``.
        previous_login_at: The sign-in before this one, ISO 8601 (ENS
            op.acc.6.r5.2: shown to the person right after they sign in).
        previous_login_ip: Where it came from.
        failures_since: Refused attempts on the account since then.
        last_failure_at: The latest of them, ISO 8601.
        last_failure_ip: Where it came from.
        mfa_required: The account must enrol an authenticator before
            anything else.
        notice_pending: The account must accept the usage notice before
            anything else.
    """

    success: bool
    expires_in: int
    csrf_token: str
    session_token: str | None = None
    account: AccountInfo | None = None
    grant: str | None = None
    previous_login_at: str | None = None
    previous_login_ip: str | None = None
    failures_since: int | None = None
    last_failure_at: str | None = None
    last_failure_ip: str | None = None
    mfa_required: bool = False
    notice_pending: bool = False


class TokenInfo(BaseModel):
    """
    Session information.

    Attributes:
        valid: Whether the session is usable.
        expires_at: Expiry as a UNIX timestamp.
        session_id: Server-side session identifier.
    """

    valid: bool
    expires_at: float | None = None
    session_id: str | None = None


class SuccessResponse(BaseModel):
    """A bare confirmation, for an action with nothing else to report back."""

    success: bool
    message: str


class RevokedResponse(BaseModel):
    """Confirmation that one record - a session or an API token - was revoked."""

    success: bool
    revoked: str


class SessionEntry(BaseModel):
    """
    One live session, with no usable identifier in it.

    Attributes:
        sid_prefix: Leading characters of the session id - enough to name a
            row for revocation, useless for forging the cookie it belongs to.
        client_ip: Address the session was issued to.
        created_at: When the session was issued, as a UNIX timestamp.
        last_seen: Most recent activity, as a UNIX timestamp.
        expires_at: When the session stops being valid, as a UNIX timestamp.
        is_current: Whether this is the session the caller is using now.
        kind: ``account`` for a person's sign-in, ``master`` for the master
            token's.
        account_id: The account signed in, for an ``account`` session.
    """

    sid_prefix: str
    client_ip: str
    created_at: float
    last_seen: float
    expires_at: float
    is_current: bool
    kind: str = "master"
    account_id: int | None = None


class SessionsListResponse(BaseModel):
    """
    Every active session the caller may see.

    Attributes:
        active_sessions: Count of the sessions listed.
        current_session: The caller's own session id, unmasked - it is
            already the credential proving the request, unlike every other
            session's id, which only ever leaves as a prefix.
        sessions: One entry per live session, newest activity first.
    """

    active_sessions: int
    current_session: str | None
    sessions: list[SessionEntry]


class WebSocketTicket(BaseModel):
    """
    Single-use credential for opening a WebSocket.

    Attributes:
        ticket: The ticket value.
        expires_in: Ticket lifetime in seconds.
    """

    ticket: str
    expires_in: int


class NoticeInfo(BaseModel):
    """
    The rights-and-obligations notice an account has to accept.

    Attributes:
        text: The notice, as the operator wrote it.
        version: Its version, to send back when accepting it.
    """

    text: str
    version: str


class SessionInfo(BaseModel):
    """
    Bootstrap information the console reads before it knows anything else.

    Answered for an anonymous caller too, with ``authenticated=False``, so the
    console can decide between the sign-in screen and the shell from one
    request instead of treating a 401 as "maybe not logged in yet". An
    anonymous caller learns nothing about the server (ENS op.acc.6.7, G10):
    no hostname, no version, not whether a second factor is on - only the
    label the operator chose for the sign-in page.

    Attributes:
        authenticated: Whether a usable credential was presented.
        scope: The credential's 3.0 scope, or None when unauthenticated.
        expires_at: Session expiry, ISO 8601, or None.
        elevated_until: End of the sudo-mode confirmation window, ISO 8601,
            or None when the session is not currently elevated.
        totp_enabled: Whether this credential's sign-in asks for a second
            factor: the account's own authenticator, or the console's for the
            master token. False for an anonymous caller.
        hostname: This machine's hostname, so an operator with several panels
            open can tell them apart; the operator's sign-in label (empty by
            default) for an anonymous caller.
        version: The installed Noust version; empty for an anonymous caller.
        csrf_header: Header name a mutation must echo the CSRF cookie in.
        csrf_cookie: Name of the readable CSRF cookie.
        renamed_from_wasm: Whether this server ran WASM before Noust, so the
            console tells the operator once that the product was renamed.
        central: This server's role and whether its sealed secrets are
            locked, so the console hides what a hub does not do and offers
            the unlock form; None for an anonymous caller.
        login_label: What the operator chose to show on the sign-in page.
        account: The account signed in, or None.
        role: Its role, or the role a central forwarded.
        grant: For the master token, how it holds the console.
        permissions: Everything the credential may do.
        mfa_required: The account must enrol an authenticator first.
        notice: The usage notice to accept first, when one is pending.
        accounts_exist: Whether this server has accounts; while it has none
            the console offers to create the first.
        security_profile: ``standard`` or ``ens-medium``.
        idle_minutes: How long the session may go unused.
    """

    authenticated: bool
    scope: str | None = None
    expires_at: str | None = None
    elevated_until: str | None = None
    totp_enabled: bool
    hostname: str
    version: str
    csrf_header: str = CSRF_HEADER_NAME
    csrf_cookie: str = CSRF_COOKIE_NAME
    renamed_from_wasm: bool = False
    central: CentralInfo | None = None
    login_label: str | None = None
    account: AccountInfo | None = None
    role: str | None = None
    grant: str | None = None
    permissions: list[str] = Field(default_factory=list)
    mfa_required: bool = False
    notice: NoticeInfo | None = None
    accounts_exist: bool | None = None
    security_profile: str | None = None
    idle_minutes: int | None = None


class ElevateRequest(BaseModel):
    """
    Confirmation presented to enter sudo mode for the next ten minutes.

    Attributes:
        code: A TOTP code or a backup code: the account's own, or the
            console's for the master token when two-factor is enabled.
        password: The account's password; an account confirms with both.
        token: The master token, for a master token session without two-factor.
    """

    code: str | None = Field(default=None, max_length=64)
    password: str | None = Field(default=None, max_length=1024)
    token: str | None = Field(default=None, max_length=512)


class ElevateResponse(BaseModel):
    """
    Result of a successful elevation.

    Attributes:
        elevated_until: End of the confirmation window, ISO 8601.
    """

    elevated_until: str


def _iso(timestamp: float | None) -> str | None:
    """
    Render a UNIX timestamp the way every session field on the wire does.

    Args:
        timestamp: A UNIX timestamp, or None.

    Returns:
        The ISO 8601 form in UTC, or None.
    """
    if timestamp is None:
        return None
    return datetime.fromtimestamp(float(timestamp), tz=timezone.utc).isoformat()


def _login_failure(error: str, detail: str) -> HTTPException:
    """
    Build a 401 whose reason a client can branch on without parsing English.

    For the master token, ``error`` distinguishes a wrong token from a
    missing or wrong second factor: the token is a 256-bit secret, and
    saying it was right tells whoever holds it nothing new, while the
    console's two-step sign-in depends on it. For an account every refusal is
    ``invalid_credentials``. No answer counts down the attempts left: that
    is information for a guesser, not for the person (G10).
    :func:`~noust.web.api.deps.handle_http_exception` passes a ``detail``
    dict carrying ``error`` through unchanged.

    Args:
        error: Machine-readable reason - ``invalid_token``, ``totp_required``,
            ``passkey_required``, ``second_factor_required``, ``invalid_totp``
            or ``invalid_credentials``.
        detail: Human-readable message.

    Returns:
        The exception to raise.
    """
    return HTTPException(
        status_code=401, detail={"error": error, "detail": detail, "hint": None, "fields": None}
    )


def master_has_passkeys() -> bool:
    """
    Report whether the master token has passkeys of its own.

    A passkey is the master token's second factor, as the console's TOTP is:
    once one exists, the token alone no longer signs in or confirms sudo mode.

    Returns:
        True when at least one passkey belongs to the master token.
    """
    return PasskeyManager().count(None) > 0


def _second_factor_required(
    totp_on: bool, passkeys_on: bool, field: str, *, elevate: bool = False
) -> tuple[str, str]:
    """
    Say which second factor the master token has to add.

    Args:
        totp_on: Whether the console's TOTP is enabled.
        passkeys_on: Whether the master token has passkeys.
        field: The body field a TOTP or backup code goes in.
        elevate: Whether this is a sudo-mode confirmation.

    Returns:
        The error code - ``totp_required`` as before when TOTP is the only
        factor (clients branch on it), ``passkey_required`` when a passkey is,
        ``second_factor_required`` when either will do - and the sentence.
    """
    where = "/api/auth/passkeys/elevate" if elevate else "/api/auth/passkeys/login"
    if totp_on and not passkeys_on:
        return "totp_required", f"Two-factor authentication is enabled. Include {field}."
    if passkeys_on and not totp_on:
        return "passkey_required", f"This sign-in has a passkey now. Use it: POST {where}."
    return (
        "second_factor_required",
        f"Two-factor authentication is enabled. Include {field}, or use a passkey: POST {where}.",
    )


def _printable(value: str | None, limit: int = 64) -> str:
    """
    Reduce text a stranger typed to something safe to put in the audit log.

    Args:
        value: The text.
        limit: Longest result.

    Returns:
        The text without control characters, cut at ``limit``.
    """
    return "".join(char for char in (value or "") if char.isprintable())[:limit]


def set_session_cookies(response: Response, session: IssuedSession, secure: bool) -> None:
    """
    Attach the session and CSRF cookies to a response.

    Args:
        response: The response being returned to the browser.
        session: The issued session.
        secure: Whether to mark the cookies ``Secure``.
    """
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session.token,
        max_age=session.max_age,
        httponly=True,
        samesite="strict",
        secure=secure,
        path="/",
    )
    # Readable on purpose: the SPA has to copy it into the CSRF header. Its
    # value is useless without the HttpOnly session cookie.
    response.set_cookie(
        CSRF_COOKIE_NAME,
        session.csrf_token,
        max_age=session.max_age,
        httponly=False,
        samesite="strict",
        secure=secure,
        path="/",
    )


def _account_of(session: dict[str, Any]) -> Account | None:
    """
    Read the account a payload signed in as.

    Args:
        session: The authenticated payload.

    Returns:
        The account, or None for any credential that is not an account's
        session.
    """
    account_id = session.get("account_id")
    if account_id is None:
        return None
    return get_token_manager().accounts.get(int(account_id))


@router.post("/login", response_model=LoginResponse)
def login(request: Request, response: Response, body: LoginRequest) -> LoginResponse:
    """
    Exchange an account's credentials, or the master token, for a session.

    Args:
        request: The incoming request.
        response: Response used to set the session cookies.
        body: The login payload.

    Returns:
        The login result, with what happened since the account's last sign-in.

    Raises:
        HTTPException: 401. For an account, ``invalid_credentials`` whatever
            was wrong. For the master token, ``invalid_token`` when it is
            wrong, ``totp_required``, ``passkey_required`` or
            ``second_factor_required`` when a required second factor is
            missing, ``invalid_totp`` when it is wrong. A client locked out
            by too many attempts never reaches this handler:
            ``SecurityMiddleware`` answers 429 with ``locked_out`` first.
    """
    if body.username is not None:
        return _account_login(request, response, body)
    return _master_login(request, response, body)


def _account_login(request: Request, response: Response, body: LoginRequest) -> LoginResponse:
    """
    Sign a person in with their account.

    Args:
        request: The incoming request.
        response: Response used to set the session cookies.
        body: The login payload, with ``username``.

    Returns:
        The login result.

    Raises:
        HTTPException: 401 ``invalid_credentials`` for any refusal.
    """
    token_manager = get_token_manager()
    client_ip = get_client_ip(request)
    name = _printable(body.username)
    try:
        account = token_manager.accounts.authenticate(
            body.username or "", body.password or "", body.totp_code, client_ip=client_ip
        )
    except AuthenticationFailed as exc:
        # The address is counted like any wrong credential, before the account:
        # an attacker spraying names runs out at the address first, and a
        # person's account is not locked by somebody else's guesses as fast.
        get_brute_force().record_failure(client_ip)
        audit_event(
            "auth.login",
            "failure",
            client_ip=client_ip,
            target=f"account:{name}",
            detail=f"account sign-in refused: {exc.reason}",
        )
        if exc.locked_now:
            policy = token_manager.policy()
            audit_event(
                "auth.lockout",
                "warning",
                client_ip=client_ip,
                target=f"account:{name}",
                detail=(
                    f"account locked for {policy.lockout_minutes} minutes after "
                    f"{policy.lockout_threshold} refused sign-ins"
                ),
            )
        raise _login_failure("invalid_credentials", INVALID_CREDENTIALS) from exc

    record = token_manager.accounts.record_login(account.id, client_ip)
    get_brute_force().record_success(client_ip)
    session = token_manager.create_session(client_ip, account_id=account.id, auth_method="password")
    set_session_cookies(response, session, secure=is_secure_request(request))

    fields = token_manager.account_fields(record.account)
    audit_event(
        "auth.login",
        "success",
        client_ip=client_ip,
        session=fields,
        target=f"account:{account.username}",
        detail="signed in with a password" + (" and a second factor" if account.has_mfa else ""),
    )
    return LoginResponse(
        success=True,
        expires_in=session.max_age,
        csrf_token=session.csrf_token,
        session_token=session.token if body.bearer else None,
        account=AccountInfo.of(record.account),
        previous_login_at=_iso(record.previous_login_at),
        previous_login_ip=record.previous_login_ip,
        failures_since=record.failures_since,
        last_failure_at=_iso(record.last_failed_at),
        last_failure_ip=record.last_failed_ip,
        mfa_required=bool(fields["mfa_pending"]),
        notice_pending=bool(fields["notice_pending"]),
    )


def _master_login(request: Request, response: Response, body: LoginRequest) -> LoginResponse:
    """
    Sign in with the master token: the whole console, break-glass or recovery.

    Args:
        request: The incoming request.
        response: Response used to set the session cookies.
        body: The login payload, with ``token``.

    Returns:
        The login result.

    Raises:
        HTTPException: 401 ``invalid_token``, ``totp_required`` or
            ``invalid_totp``.
    """
    token_manager = get_token_manager()
    brute_force = get_brute_force()
    audit = get_audit_logger()
    client_ip = get_client_ip(request)

    if not token_manager.verify_master_token(body.token or ""):
        brute_force.record_failure(client_ip)
        if audit:
            audit.record(
                action="auth.login",
                result="failure",
                client_ip=client_ip,
                resource="/api/auth/login",
                detail="invalid master token",
            )
        raise _login_failure("invalid_token", "Invalid token.")

    totp_on = token_manager.totp_enabled()
    passkeys_on = master_has_passkeys()
    if totp_on or passkeys_on:
        code = (body.totp_code or "").strip()
        if not code or not totp_on:
            # Not counted by the lockout: an absent code is a client that does
            # not know the second factor exists, not a guess at it. With only
            # a passkey, the token alone no longer signs in (spec §2.4): the
            # passkey does, on its own, at /api/auth/passkeys/login.
            if audit:
                audit.record(
                    action="auth.login",
                    result="failure",
                    client_ip=client_ip,
                    resource="/api/auth/login",
                    detail="second factor required but not presented",
                )
            raise _login_failure(*_second_factor_required(totp_on, passkeys_on, "totp_code"))
        if not token_manager.verify_second_factor(code, purpose="login"):
            # The same chokepoint that counts a bad master token: a wrong
            # second factor is a credential guess, and it must not have its
            # own, softer counter.
            record_auth_failure(client_ip, "/api/auth/login", "totp")
            raise _login_failure("invalid_totp", "Invalid two-factor code.")

    brute_force.record_success(client_ip)
    session = token_manager.create_session(client_ip)
    set_session_cookies(response, session, secure=is_secure_request(request))
    grant = token_manager.master_grant()

    if audit:
        audit.record(
            action="auth.login",
            result="success",
            client_ip=client_ip,
            actor=actor_label({"sid": session.session_id}),
            resource="/api/auth/login",
        )
    # Every use of the master token is on record as a warning (spec §2.2):
    # it is the credential of whoever holds root here, not of a person.
    audit_event(
        "auth.break_glass",
        "warning",
        client_ip=client_ip,
        session={"sid": session.session_id},
        target="/api/auth/login",
        detail=(
            "the master token signed in; no account exists yet - create one with "
            "'noust user create NAME --role admin'"
            if grant == GRANT_COMPAT
            else f"the master token signed in ({grant}); people should use their accounts"
        ),
    )

    return LoginResponse(
        success=True,
        expires_in=session.max_age,
        csrf_token=session.csrf_token,
        session_token=session.token if body.bearer else None,
        grant=grant,
    )


@router.get("/session", response_model=SessionInfo)
def get_session_info(request: Request) -> SessionInfo:
    """
    Report whether the caller is signed in, without demanding that they are.

    Every other endpoint under ``/api`` requires ``require_auth`` and answers
    401 to an anonymous caller; this one exists so the console has something
    to call before it knows which of those two things it is. A caller that
    presents nothing is not guessing anything and is not counted. A caller
    that presents a credential is checked exactly as ``require_auth`` checks
    one, through :func:`~noust.web.auth.verify_credential`, and a wrong one is
    counted towards the lockout: the answer here says whether the value was
    the master token, so without counting this was a guessing oracle with no
    limit. A session cookie this server signed but that has since expired is
    not a guess and is not counted.

    Args:
        request: The incoming request.

    Returns:
        The session's bootstrap information; for no credential, or an expired
        or revoked one, ``authenticated=False`` and the sign-in label only.
    """
    token_manager = get_token_manager()
    policy = token_manager.policy()
    client_ip = get_client_ip(request)
    bearer = bearer_token(request)
    credential = bearer or request.cookies.get(SESSION_COOKIE_NAME)
    session = (
        verify_credential(
            credential,
            client_ip,
            resource="/api/auth/session",
            source="bearer" if bearer else "cookie",
            connection=request,
        )
        if credential
        else None
    )

    if session is None:
        return SessionInfo(
            authenticated=False,
            totp_enabled=False,
            hostname=policy.login_label,
            version="",
            login_label=policy.login_label or None,
        )

    account = _account_of(session)
    notice_version = policy.notice_version
    notice = (
        NoticeInfo(text=policy.notice_text.strip(), version=notice_version)
        if session.get("notice_pending") and notice_version
        else None
    )
    return SessionInfo(
        authenticated=True,
        scope=session.get("scope"),
        expires_at=_iso(session.get("expires_at") or session.get("exp")),
        elevated_until=_iso(session.get("elevated_until")),
        totp_enabled=account.has_mfa if account is not None else token_manager.totp_enabled(),
        hostname=socket.gethostname(),
        version=__version__,
        renamed_from_wasm=paths.came_from_wasm(),
        central=session_central(),
        login_label=policy.login_label or None,
        account=AccountInfo.of(account) if account is not None else None,
        role=session.get("role"),
        grant=session.get("grant"),
        permissions=sorted(session.get("permissions") or ()),
        mfa_required=bool(session.get("mfa_pending")),
        notice=notice,
        accounts_exist=token_manager.accounts_exist(),
        security_profile=policy.profile,
        idle_minutes=policy.idle_minutes,
    )


@router.post("/elevate", response_model=ElevateResponse)
def elevate(
    request: Request, body: ElevateRequest, session: dict[str, Any] = Depends(require_auth)
) -> ElevateResponse:
    """
    Confirm the caller's identity again, opening sudo mode for ten minutes.

    D5: deleting an application, a database, a service or a site, writing raw
    configuration or a unit file, running a write against a database console,
    revealing a ``.env`` in clear, issuing an API token and turning
    two-factor authentication off all require a cookie session to have
    called this recently; see :func:`noust.web.api.deps.require_elevated`.

    An account confirms with its password and a code from its authenticator;
    an account without one cannot enter sudo mode. The master token confirms
    as it signs in: the console's two-factor code when that is enabled, the
    master token otherwise. A wrong factor is counted by the same lockout a
    login failure is, through the same chokepoint.

    Args:
        request: The incoming request.
        body: The factors.
        session: The authenticated session being elevated.

    Returns:
        The new elevation deadline.

    Raises:
        HTTPException: 401 with ``error`` ``invalid_credentials`` for an
            account, ``totp_required``, ``invalid_totp`` or ``invalid_token``
            for the master token, when the factors do not verify.
    """
    token_manager = get_token_manager()
    client_ip = get_client_ip(request)

    account_id = session.get("account_id")
    if account_id is not None:
        accounts = token_manager.accounts
        account = accounts.require_id(int(account_id))
        if not account.has_mfa:
            raise AccountError(
                "Sudo mode needs a second factor, and this account has none",
                details="Enrol an authenticator first: POST /api/auth/2fa/enroll.",
            )
        password_ok = accounts.verify_password(account.id, body.password or "", client_ip=client_ip)
        code_ok = password_ok and accounts.verify_second_factor(
            account.id, body.code or "", purpose="elevate"
        )
        if not code_ok:
            if password_ok:
                accounts.record_failure(account.id, client_ip)
            record_auth_failure(client_ip, "/api/auth/elevate", "password")
            raise _login_failure("invalid_credentials", INVALID_CREDENTIALS)
    elif token_manager.totp_enabled():
        code = (body.code or "").strip()
        if not code:
            raise _login_failure(
                *_second_factor_required(True, master_has_passkeys(), "code", elevate=True)
            )
        if not token_manager.verify_second_factor(code, purpose="elevate"):
            record_auth_failure(client_ip, "/api/auth/elevate", "totp")
            raise _login_failure("invalid_totp", "Invalid two-factor code.")
    elif master_has_passkeys():
        # The token is accepted only while no second factor exists, as with
        # TOTP: a passkey confirms at /api/auth/passkeys/elevate.
        raise _login_failure(*_second_factor_required(False, True, "code", elevate=True))
    elif not token_manager.verify_master_token(body.token or ""):
        record_auth_failure(client_ip, "/api/auth/elevate", "master_token")
        raise _login_failure("invalid_token", "Invalid token.")

    elevated_until = token_manager.elevate(str(session.get("sid")))

    audit_event(
        "auth.elevate", "success", client_ip=client_ip, session=session, target="/api/auth/elevate"
    )
    return ElevateResponse(elevated_until=_iso(elevated_until) or "")


@router.post("/logout", response_model=SuccessResponse)
def logout(
    request: Request, response: Response, session: dict[str, Any] = Depends(require_auth)
) -> SuccessResponse:
    """
    Revoke the current session and clear its cookies.

    Args:
        request: The incoming request.
        response: Response used to clear the cookies.
        session: The authenticated session.

    Returns:
        A confirmation payload.
    """
    token_manager = get_token_manager()
    session_id = session.get("sid")

    if session_id and session.get("type") == "session":
        token_manager.revoke_session(session_id)

    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")

    audit_event(
        "auth.logout",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target="/api/auth/logout",
    )
    return SuccessResponse(success=True, message="Logged out successfully")


@router.get("/verify", response_model=TokenInfo)
async def verify_token(session: dict[str, Any] = Depends(require_auth)) -> TokenInfo:
    """
    Report whether the presented credential is still valid.

    Args:
        session: The authenticated session.

    Returns:
        Session information.
    """
    return TokenInfo(
        valid=True,
        expires_at=session.get("expires_at") or session.get("exp"),
        session_id=session.get("sid"),
    )


@router.post("/ws-ticket", response_model=WebSocketTicket)
def create_ws_ticket(
    request: Request, session: dict[str, Any] = Depends(require_auth)
) -> WebSocketTicket:
    """
    Issue a single-use ticket for opening a WebSocket.

    Browsers cannot set headers on a WebSocket handshake, so clients that
    cannot rely on cookies use this instead of putting a long-lived token in a
    query string that proxies and access logs record.

    Any credential may ask: a session, the master token or an API token. The
    ticket redeems as that same credential, with its permissions, and only
    while it is still valid - see
    :meth:`noust.web.auth.TokenManager.consume_ws_ticket`.

    Args:
        request: The incoming request.
        session: The authenticated session.

    Returns:
        The ticket and its lifetime.
    """
    token_manager = get_token_manager()
    client_ip = get_client_ip(request)
    ticket, expires_in = token_manager.issue_ws_ticket(str(session.get("sid")), client_ip)

    audit_event(
        "auth.ws_ticket",
        "success",
        client_ip=client_ip,
        session=session,
        target="/api/auth/ws-ticket",
    )
    return WebSocketTicket(ticket=ticket, expires_in=expires_in)


def _sessions_scope(session: dict[str, Any]) -> int | None:
    """
    Whose sessions a caller manages from the session endpoints.

    Args:
        session: The authenticated payload.

    Returns:
        The caller's own account id for a person without ``accounts.manage``
        (their own sessions only); None - every session, as in 3.0 - for the
        master token and for whoever governs accounts.
    """
    account_id = session.get("account_id")
    if account_id is None or has_permission(session, Permission.ACCOUNTS_MANAGE):
        return None
    return int(account_id)


@router.get("/sessions", response_model=SessionsListResponse)
def get_sessions(session: dict[str, Any] = Depends(require_auth)) -> SessionsListResponse:
    """
    Report the active sessions: a person's own, or all of them for whoever
    governs accounts and for the master token.

    Only a truncated prefix of each session id is included: enough to name a
    row for ``DELETE /api/auth/sessions/{sid_prefix}``, useless for forging
    the cookie it belongs to.

    Args:
        session: The authenticated session.

    Returns:
        The count, the caller's session id, and one entry per live session
        with its address, birth, last activity and expiry.
    """
    token_manager = get_token_manager()
    current = session.get("sid") if session.get("type") == "session" else None
    entries = token_manager.list_sessions(current, _sessions_scope(session))
    return SessionsListResponse(
        active_sessions=len(entries),
        current_session=session.get("sid"),
        sessions=[SessionEntry(**entry) for entry in entries],
    )


# Synchronous so the settings screen's "Sign out everywhere" adapter can call
# it directly; FastAPI runs it in a threadpool either way. Declared before the
# parametrised sibling, the way every router here orders its routes.
@router.post("/sessions/revoke-all", response_model=SuccessResponse)
def revoke_all_sessions(
    request: Request, response: Response, session: dict[str, Any] = Depends(require_auth)
) -> SuccessResponse:
    """
    Revoke every session, including the caller's: a person's own sessions,
    every session for the master token.

    Args:
        request: The incoming request.
        response: Response used to clear the caller's cookies.
        session: The authenticated session.

    Returns:
        A confirmation payload.
    """
    token_manager = get_token_manager()
    account_id = session.get("account_id")
    if account_id is None:
        token_manager.revoke_all_sessions()
    else:
        token_manager.sessions.revoke_account(int(account_id))

    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")

    audit_event(
        "auth.revoke_all",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target="/api/auth/sessions/revoke-all",
        detail="every session" if account_id is None else "every session of the account",
    )
    return SuccessResponse(success=True, message="All sessions revoked")


# Declared before the parametrised sibling, like revoke-all above it.
@router.post("/sessions/revoke-others", response_model=SuccessResponse)
def revoke_other_sessions(
    request: Request, session: dict[str, Any] = Depends(require_auth)
) -> SuccessResponse:
    """
    Revoke every session except the caller's, leaving it signed in.

    The counterpart to "Sign out everywhere": an operator who notices an
    unrecognised session in the list wants every other session gone without
    also being signed out of the tab they are looking at the list from. A
    person's own other sessions; every other one for the master token.

    Args:
        request: The incoming request.
        session: The authenticated session.

    Returns:
        A confirmation payload naming how many sessions were revoked.

    Raises:
        HTTPException: 400 when the caller's own credential is not a session
            (a Bearer token or the master token) - there is no "other
            session" concept for a credential that never had a browser tab
            of its own.
    """
    current = session.get("sid") if session.get("type") == "session" else None
    if current is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "This credential is not a browser session; there is no other "
                "session to leave signed in."
            ),
        )

    token_manager = get_token_manager()
    account_id = session.get("account_id")
    revoked = token_manager.revoke_other_sessions(
        current, int(account_id) if account_id is not None else None
    )

    audit_event(
        "auth.revoke_others",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target="/api/auth/sessions/revoke-others",
        detail=f"revoked {revoked} session(s)",
    )
    return SuccessResponse(success=True, message=f"Revoked {revoked} other session(s)")


@router.delete("/sessions/{sid_prefix}", response_model=RevokedResponse)
def revoke_one_session(
    sid_prefix: str, request: Request, session: dict[str, Any] = Depends(require_auth)
) -> RevokedResponse:
    """
    Revoke exactly one session, named by a unique prefix of its id.

    A person revokes their own sessions; whoever governs accounts, and the
    master token, any session. Synchronous on purpose, like the two-factor
    handlers: the settings screen calls this function directly, so there is
    one implementation of "revoke a session" with one audit trail.

    Args:
        sid_prefix: Leading characters of the session id, as listed by
            ``GET /api/auth/sessions``.
        request: The incoming request.
        session: The authenticated session.

    Returns:
        A confirmation payload naming the revoked prefix.

    Raises:
        HTTPException: 404 when nothing the caller may revoke matches the prefix.
        SecurityError: When the prefix is malformed or ambiguous, or names the
            caller's own session - ending the session you are inside is
            sign-out, which also clears the browser's cookies.
    """
    token_manager = get_token_manager()
    protect = session.get("sid") if session.get("type") == "session" else None
    revoked = token_manager.revoke_session_by_prefix(
        sid_prefix, protect_sid=protect, account_id=_sessions_scope(session)
    )

    if revoked is None:
        raise HTTPException(
            status_code=404, detail="No active session matches that prefix. It may have expired."
        )

    audit_event(
        "auth.session.revoke",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target=f"/api/auth/sessions/{revoked}",
        detail=f"revoked session {revoked}",
    )
    return RevokedResponse(success=True, revoked=revoked)


class TwoFactorStatus(BaseModel):
    """
    Two-factor state, with no secret in it.

    Attributes:
        enabled: Whether logins require a second factor.
        pending: Whether an enrolment has been begun but not confirmed.
        backup_codes_remaining: Unused single-use backup codes left.
    """

    enabled: bool
    pending: bool
    backup_codes_remaining: int


class TwoFactorEnrollment(BaseModel):
    """
    A begun enrolment. This is the only response that ever carries the secret.

    Attributes:
        secret: The base32 secret, to type into an authenticator app by hand.
        uri: The ``otpauth://`` URI the QR code encodes.
    """

    secret: str
    uri: str


class TwoFactorCode(BaseModel):
    """
    A second-factor code presented to confirm or disable.

    Attributes:
        code: A six-digit authenticator code, or a backup code for disable.
    """

    code: str = Field(max_length=64)


class TwoFactorConfirmed(BaseModel):
    """
    The result of activating the second factor.

    Attributes:
        success: Always true when the request succeeded.
        backup_codes: Single-use recovery codes, shown exactly once. Only
            salted hashes are stored, so they cannot be shown again.
    """

    success: bool
    backup_codes: list[str]


def enrollment_uri(secret: str, account: str | None = None) -> str:
    """
    Build the provisioning URI this panel enrols with.

    One implementation, used by the JSON response and by the settings
    fragment: the issuer and the account must agree wherever the QR is drawn.

    Args:
        secret: The base32 secret being enrolled.
        account: The username the authenticator is for, when it is a
            person's; the console's own factor names only the machine.

    Returns:
        The ``otpauth://`` URI, naming this machine so an operator with
        several panels can tell them apart in the app.
    """
    host = socket.gethostname()
    return provisioning_uri(
        secret, issuer="Noust", account=f"{account}@{host}" if account else host
    )


# The 2FA handlers are synchronous on purpose, and it buys two things: FastAPI
# runs them in a threadpool, and the panel's settings screen can call them
# directly as functions - same auditing, same lockout accounting - instead of
# growing a second implementation of each mutation. For an account they act
# on its own authenticator; for the master token, on the console's.


@router.get("/2fa", response_model=TwoFactorStatus)
def two_factor_status(session: dict[str, Any] = Depends(require_auth)) -> TwoFactorStatus:
    """
    Report the two-factor state of the caller's own sign-in.

    Args:
        session: The authenticated session.

    Returns:
        The state, never including any secret.
    """
    account = _account_of(session)
    if account is not None:
        return TwoFactorStatus(
            enabled=account.totp_enabled,
            pending=account.totp_pending,
            backup_codes_remaining=account.backup_codes_remaining,
        )
    return TwoFactorStatus(**get_token_manager().totp_status())


@router.post("/2fa/enroll", response_model=TwoFactorEnrollment)
def two_factor_enroll(
    request: Request, session: dict[str, Any] = Depends(require_auth)
) -> TwoFactorEnrollment:
    """
    Begin enrolment: generate a pending secret. Nothing is enforced yet.

    An account enrols its first authenticator right after signing in with
    its password - it can do nothing else until it has - so this asks for no
    sudo mode; replacing one takes a security officer (``reset-mfa``). The
    console's own factor, for the master token, asks for sudo mode confirmed
    with the master token: the factor enrolled here is the one every later
    confirmation asks for, so a session nobody re-confirmed must not be able
    to bind its own authenticator.

    Args:
        request: The incoming request.
        session: The authenticated session.

    Returns:
        The secret and its provisioning URI, shown to the operator once.

    Raises:
        HTTPException: 403 with ``error: "elevation_required"`` for the master
            token outside sudo mode.
        AccountError: When the account already has an authenticator.
    """
    token_manager = get_token_manager()
    account = _account_of(session)
    if account is not None:
        secret = token_manager.accounts.begin_totp(account.id)
        uri = enrollment_uri(secret, account.username)
    else:
        ensure_elevated(request, session)
        secret = token_manager.begin_totp_enrollment()
        uri = enrollment_uri(secret)

    audit_event(
        "auth.2fa.enroll",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target="/api/auth/2fa/enroll",
    )
    return TwoFactorEnrollment(secret=secret, uri=uri)


@router.post("/2fa/confirm", response_model=TwoFactorConfirmed)
def two_factor_confirm(
    request: Request, body: TwoFactorCode, session: dict[str, Any] = Depends(require_auth)
) -> TwoFactorConfirmed:
    """
    Verify a code from the authenticator and activate the second factor.

    Sudo mode for the master token, like enrolling: this is the step that
    switches the console's second factor on and hands out the backup codes.

    Args:
        request: The incoming request.
        body: The code the app shows for the pending secret.
        session: The authenticated session.

    Returns:
        The backup codes, in clear, exactly once.

    Raises:
        HTTPException: 403 with ``error: "elevation_required"`` for the
            master token outside sudo mode. 400 when the code does not
            verify. Not counted by the lockout: the pending secret is on the
            operator's own screen, so a wrong code here proves a typo, not a
            guess at a credential.
    """
    token_manager = get_token_manager()
    client_ip = get_client_ip(request)
    account = _account_of(session)
    if account is not None:
        codes = token_manager.accounts.confirm_totp(account.id, body.code)
    else:
        ensure_elevated(request, session)
        codes = token_manager.confirm_totp_enrollment(body.code)

    if codes is None:
        audit_event(
            "auth.2fa.confirm",
            "failure",
            client_ip=client_ip,
            session=session,
            target="/api/auth/2fa/confirm",
            detail="code did not match the pending secret",
        )
        raise HTTPException(
            status_code=400,
            detail="That code was not accepted. Scan the QR again and enter a fresh code.",
        )

    audit_event(
        "auth.2fa.confirm",
        "success",
        client_ip=client_ip,
        session=session,
        target="/api/auth/2fa/confirm",
    )
    return TwoFactorConfirmed(success=True, backup_codes=codes)


@router.post("/2fa/disable", response_model=SuccessResponse)
def two_factor_disable(
    request: Request, body: TwoFactorCode, session: dict[str, Any] = Depends(require_elevated)
) -> SuccessResponse:
    """
    Turn the console's second factor off, on presentation of a current code.

    An account cannot turn its own off: every account keeps a second factor
    (ENS op.acc.6), and replacing one is a security officer's ``reset-mfa``.

    Args:
        request: The incoming request.
        body: A TOTP code or an unused backup code.
        session: The authenticated session.

    Returns:
        A confirmation payload.

    Raises:
        HTTPException: 403 with ``error: "elevation_required"`` when a cookie
            session has not called ``POST /api/auth/elevate`` recently (D5).
            400 when the code does not verify. Counted by the same lockout as
            a failed login: this endpoint guards the switch that turns the
            second factor off, so a wrong code here is a credential guess by
            whoever holds the session.
        AccountError: For an account's session.
    """
    if session.get("account_id") is not None:
        raise AccountError(
            "An account's second factor cannot be turned off",
            details="Every account keeps one. To replace it, ask a security officer to reset it.",
        )
    token_manager = get_token_manager()
    client_ip = get_client_ip(request)

    if not token_manager.disable_totp(body.code):
        record_auth_failure(client_ip, "/api/auth/2fa/disable", "totp")
        raise HTTPException(
            status_code=400,
            detail="That code was not accepted. Two-factor authentication stays on.",
        )

    audit_event(
        "auth.2fa.disable",
        "success",
        client_ip=client_ip,
        session=session,
        target="/api/auth/2fa/disable",
    )
    return SuccessResponse(success=True, message="Two-factor authentication disabled")


@router.post("/2fa/backup-codes", response_model=TwoFactorConfirmed)
def regenerate_backup_codes(
    request: Request, session: dict[str, Any] = Depends(require_elevated)
) -> TwoFactorConfirmed:
    """
    Replace the backup codes with a fresh set, shown exactly once.

    Every code issued before this call stops working: a set an operator can
    no longer account for - lost, or shown on a screen they no longer trust -
    is worthless as a recovery path if the old ones stay live alongside it.

    Args:
        request: The incoming request.
        session: The authenticated session, elevated (D5): a cookie session
            must have called ``POST /api/auth/elevate`` recently.

    Returns:
        The new backup codes, in clear.

    Raises:
        HTTPException: 403 with ``error: "elevation_required"`` per
            :func:`noust.web.api.deps.require_elevated`.
        SecurityError: 400 when two-factor authentication is not enabled.
    """
    token_manager = get_token_manager()
    account = _account_of(session)
    if account is not None:
        codes = token_manager.accounts.regenerate_backup_codes(account.id)
    else:
        codes = token_manager.regenerate_backup_codes()

    audit_event(
        "auth.2fa.backup_codes",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target="/api/auth/2fa/backup-codes",
    )
    return TwoFactorConfirmed(success=True, backup_codes=codes)


class PasswordChange(BaseModel):
    """
    An account holder changing their own password.

    Attributes:
        current_password: The password in force.
        new_password: The new one; checked against the policy.
    """

    current_password: str = Field(max_length=1024)
    new_password: str = Field(max_length=1024)


@router.post("/password", response_model=SuccessResponse)
def change_password(
    request: Request, body: PasswordChange, session: dict[str, Any] = Depends(require_auth)
) -> SuccessResponse:
    """
    Change the signed-in account's own password.

    Every other session of the account is signed out: whoever else held one
    held it with the old password.

    Args:
        request: The incoming request.
        body: The current and the new password.
        session: The authenticated session.

    Returns:
        A confirmation payload.

    Raises:
        HTTPException: 400 for a credential that is not an account's
            session; 401 ``invalid_credentials`` when the current password is
            wrong.
        ValidationError: When the new password is refused.
    """
    account_id = session.get("account_id")
    if account_id is None:
        raise HTTPException(
            status_code=400, detail="Only an account has a password; this credential is not one."
        )
    token_manager = get_token_manager()
    client_ip = get_client_ip(request)
    try:
        token_manager.accounts.change_password(
            int(account_id), body.current_password, body.new_password, client_ip=client_ip
        )
    except AuthenticationFailed as exc:
        record_auth_failure(client_ip, "/api/auth/password", "password")
        raise _login_failure("invalid_credentials", INVALID_CREDENTIALS) from exc
    if session.get("type") == "session":
        token_manager.revoke_other_sessions(str(session["sid"]), int(account_id))
    audit_event(
        "auth.password.change",
        "success",
        client_ip=client_ip,
        session=session,
        target=f"account:{session.get('username')}",
    )
    return SuccessResponse(success=True, message="Password changed")


class NoticeAcceptance(BaseModel):
    """
    Acceptance of the usage notice.

    Attributes:
        version: The version of the notice that was shown.
    """

    version: str = Field(max_length=64)


@router.post("/notice/accept", response_model=SuccessResponse)
def accept_notice(
    request: Request, body: NoticeAcceptance, session: dict[str, Any] = Depends(require_auth)
) -> SuccessResponse:
    """
    Record that the signed-in person accepted the rights-and-obligations notice.

    Until they do, every request but their own session's is answered 403
    ``notice_required`` (ENS op.acc.6.9, mp.per.2).

    Args:
        request: The incoming request.
        body: The version shown.
        session: The authenticated session.

    Returns:
        A confirmation payload.

    Raises:
        HTTPException: 400 for a credential that is not an account's session.
        ValidationError: When the version is not the one in force.
    """
    account_id = session.get("account_id")
    if account_id is None:
        raise HTTPException(
            status_code=400, detail="Only a person accepts the notice; this credential is not one."
        )
    get_token_manager().accounts.accept_notice(int(account_id), body.version)
    audit_event(
        "auth.notice.accept",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target=f"account:{session.get('username')}",
        detail=f"notice version {body.version}",
    )
    return SuccessResponse(success=True, message="Notice accepted")


class RolesResponse(BaseModel):
    """
    What each role may do, for the console's account screens.

    Attributes:
        roles: Role name to its permissions.
        permissions: Every permission, with what it allows.
    """

    roles: dict[str, list[str]]
    permissions: dict[str, str]


@router.get("/roles", response_model=RolesResponse)
def list_roles(session: dict[str, Any] = Depends(require_auth)) -> RolesResponse:
    """
    Describe the roles and the permissions they hold.

    Args:
        session: The authenticated session.

    Returns:
        The role table and every permission's description.
    """
    return RolesResponse(
        roles={role: sorted(held) for role, held in ROLE_PERMISSIONS.items()},
        permissions={name: DESCRIPTIONS.get(name, "") for name in sorted(ALL_PERMISSIONS)},
    )


class ApiTokenRequest(BaseModel):
    """
    Request to issue an API token.

    Attributes:
        name: Human-chosen name, unique across all tokens ever issued.
        scope: ``read``, ``deploy`` or ``admin``; narrowed to what the
            issuing account holds.
        expires_hours: Lifetime in hours; omit for a token that only dies by
            revocation (under the ENS profile, the longest allowed).
        permissions: Permissions to narrow it to instead of its scope's; all
            must be held by the issuing account.
        allowed_cidrs: Networks it is accepted from; any when omitted.
        allow_elevated: Let it act where sudo mode is asked. Off by default,
            refused under the ENS profile.
    """

    name: str = Field(min_length=1, max_length=64)
    scope: str
    expires_hours: int | None = Field(default=None, ge=1, le=24 * 3650)
    permissions: list[str] | None = None
    allowed_cidrs: list[str] | None = None
    allow_elevated: bool = False


class ApiTokenCreated(BaseModel):
    """
    A freshly issued API token. The only response that ever carries the token.

    Attributes:
        id: Record id, used to revoke it.
        name: The token's name.
        scope: The token's scope.
        token: The credential, shown exactly once - only its salted hash is
            stored, so it cannot be shown again.
        created_at: Creation time as a UNIX timestamp.
        expires_at: Expiry as a UNIX timestamp, or None for no expiry.
        owner_account_id: The account it acts for, or None.
        permissions: What it may do at most, or None for its scope's.
        allowed_cidrs: Networks it is accepted from, or None.
        allow_elevated: Whether it may act where sudo mode is asked.
    """

    id: int
    name: str
    scope: str
    token: str
    created_at: float
    expires_at: float | None = None
    owner_account_id: int | None = None
    permissions: list[str] | None = None
    allowed_cidrs: list[str] | None = None
    allow_elevated: bool | None = None


class ApiTokenInfo(BaseModel):
    """
    One API token record, with no credential in it.

    Attributes:
        id: Record id.
        name: The token's name.
        scope: The token's scope.
        created_at: Creation time as a UNIX timestamp.
        expires_at: Expiry as a UNIX timestamp, or None for no expiry.
        last_used_at: When it last authenticated a request, or None.
        revoked_at: When it was revoked, or None while it is live.
        owner_account_id: The account it acts for; None for a token issued
            before accounts existed, until the first admin adopts it.
        permissions: What it may do at most, or None for its scope's.
        allowed_cidrs: Networks it is accepted from, or None.
        allow_elevated: Whether it may act where sudo mode is asked; None for
            a token issued before 3.1.
        created_by: Who issued it.
        last_used_ip: Where it was last used from.
    """

    id: int
    name: str
    scope: str
    created_at: float
    expires_at: float | None = None
    last_used_at: float | None = None
    revoked_at: float | None = None
    owner_account_id: int | None = None
    permissions: list[str] | None = None
    allowed_cidrs: list[str] | None = None
    allow_elevated: bool | None = None
    created_by: str | None = None
    last_used_ip: str | None = None


class ApiTokenListResponse(BaseModel):
    """
    Every API token record the caller may see.

    Attributes:
        tokens: The records, newest first.
    """

    tokens: list[ApiTokenInfo]


def _token_scope(session: dict[str, Any]) -> tuple[bool, int | None]:
    """
    Whose API tokens a caller manages.

    Args:
        session: The authenticated payload.

    Returns:
        ``(True, None)`` for every token - whoever governs accounts, the
        master token, an ``admin`` token of 3.0 - or ``(True, account_id)``
        for a person's (or their token's) own; ``(False, None)`` for a
        credential that manages none, such as a 3.0 ``read`` token.
    """
    if has_permission(session, Permission.ACCOUNTS_MANAGE):
        return True, None
    owner = session.get("account_id") or session.get("owner_account_id")
    if owner is not None:
        return True, int(owner)
    return False, None


# Synchronous like the two-factor handlers, and for the same reason: the
# settings screen calls these functions directly.


@router.get("/tokens", response_model=ApiTokenListResponse)
def list_api_tokens(session: dict[str, Any] = Depends(require_auth)) -> ApiTokenListResponse:
    """
    List the API tokens the caller may see, live and revoked alike: a
    person's own, or every token for whoever governs accounts.

    Args:
        session: The authenticated session.

    Returns:
        The records. No response from this endpoint carries a token.

    Raises:
        HTTPException: 403 for a credential that manages no tokens.
    """
    allowed, owner = _token_scope(session)
    if not allowed:
        raise HTTPException(status_code=403, detail="This credential cannot list API tokens.")
    records = get_token_manager().list_api_tokens(owner)
    return ApiTokenListResponse(tokens=[ApiTokenInfo(**record) for record in records])


@router.post("/tokens", response_model=ApiTokenCreated, status_code=201)
def create_api_token(
    request: Request,
    body: ApiTokenRequest,
    session: dict[str, Any] = Depends(require_elevated),
) -> ApiTokenCreated:
    """
    Issue a named, scoped API token, returned in clear exactly once.

    A person's token belongs to them and acts with at most their role, checked
    on every use. The master token's belongs to nobody until the first admin
    account adopts it.

    Args:
        request: The incoming request.
        body: Name, scope, optional expiry, permissions and networks.
        session: The authenticated session, elevated (D5): issuing a token is
            a standing credential, the same category of action as deleting
            something.

    Returns:
        The record, including the one and only clear copy of the token.

    Raises:
        SecurityError: When the name is taken, the scope is not a scope, or a
            permission, network or expiry is refused. The audit record names
            the token; the token itself never reaches the audit log.
        HTTPException: 403 with ``error: "elevation_required"`` per
            :func:`noust.web.api.deps.require_elevated`.
    """
    token_manager = get_token_manager()
    if session.get("type") == "api_token" and not has_permission(
        session, Permission.ACCOUNTS_MANAGE
    ):
        # A token minting tokens is a standing credential multiplying itself
        # out of anybody's sight; only a 3.0 admin token ever could.
        raise HTTPException(status_code=403, detail="An API token cannot issue API tokens.")
    owner = _account_of(session)
    issued = token_manager.create_api_token(
        body.name,
        body.scope,
        body.expires_hours,
        owner=owner,
        permissions=body.permissions,
        allowed_cidrs=body.allowed_cidrs,
        allow_elevated=body.allow_elevated,
        created_by=actor_label(session),
    )

    audit_event(
        "auth.token.create",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target="/api/auth/tokens",
        detail=f"issued token '{issued['name']}' with scope '{issued['scope']}'",
    )
    return ApiTokenCreated(**issued)


@router.delete("/tokens/{token_id}", response_model=RevokedResponse)
def revoke_api_token(
    token_id: int, request: Request, session: dict[str, Any] = Depends(require_auth)
) -> RevokedResponse:
    """
    Revoke one API token. Requests presenting it stop authenticating at once.

    A person revokes their own tokens; whoever governs accounts, any.

    Args:
        token_id: The record's id, as listed by ``GET /api/auth/tokens``.
        request: The incoming request.
        session: The authenticated session.

    Returns:
        A confirmation payload naming the revoked token.

    Raises:
        HTTPException: 404 when the caller has no token with that id.
    """
    token_manager = get_token_manager()
    allowed, owner = _token_scope(session)
    record = token_manager.get_api_token(token_id) if allowed else None
    if record is None or (owner is not None and record.get("owner_account_id") != owner):
        raise HTTPException(status_code=404, detail=f"No API token with id {token_id}")
    name = token_manager.revoke_api_token(token_id)
    if name is None:
        raise HTTPException(status_code=404, detail=f"No API token with id {token_id}")

    audit_event(
        "auth.token.revoke",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target=f"/api/auth/tokens/{token_id}",
        detail=f"revoked token '{name}'",
    )
    return RevokedResponse(success=True, revoked=name)


@router.post("/fleet/revoke", response_model=RevokedResponse)
def revoke_own_fleet_token(
    request: Request, session: dict[str, Any] = Depends(require_auth)
) -> RevokedResponse:
    """
    Let a central's fleet token revoke itself, when the central removes the node.

    The only credential endpoint a fleet token may reach (see
    :func:`noust.web.auth.fleet_refusal`), and it can only ever end the token
    that calls it: removing a node from a central should not leave a working
    root-equivalent credential behind on the node.

    Args:
        request: The incoming request.
        session: The authenticated payload; it must be a fleet token.

    Returns:
        A confirmation payload naming the revoked token.

    Raises:
        HTTPException: 403 for any credential that is not a fleet token.
    """
    if not is_fleet(session) or session.get("token_id") is None:
        raise HTTPException(
            status_code=403,
            detail="Only a central's fleet token revokes itself here; revoke others in Settings.",
        )
    name = get_token_manager().revoke_api_token(int(session["token_id"]))

    audit_event(
        "auth.token.revoke",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target="/api/auth/fleet/revoke",
        detail=f"fleet token '{name or session.get('token_name')}' revoked itself",
    )
    return RevokedResponse(success=True, revoked=str(name or session.get("token_name") or ""))


# Accounts, invitations and separation-of-duties exceptions, under /api/auth
# so that everything a fleet token may never reach stays under one prefix.
router.include_router(accounts_router)
