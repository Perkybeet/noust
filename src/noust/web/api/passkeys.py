# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Passkeys over HTTP: ``/api/auth/passkeys``.

A thin client of :class:`noust.core.accounts.passkeys.PasskeyManager`, the same
manager ``noust passkey`` drives. What this module adds is what only the
console knows: the relying party a request implies (its ``Host``, whether it
arrived over TLS, ``web.passkeys.*`` and ``web.public_url``), the session a
ceremony is bound to, sessions and sudo mode, the lockout and the audit trail.

Mounted under ``/api/auth`` (by :mod:`noust.web.api.router`), so a central's
fleet token never reaches it (``noust.web.auth.FLEET_REFUSED_PREFIXES``): a
central cannot enrol a passkey on a node. The two routes that verify an
assertion are in the middleware's lockout paths (``AUTH_PATHS``) and count
every refused one through :func:`noust.web.auth.record_auth_failure`.

Every ceremony belongs to a browser session: the options carry a challenge
bound to it, and the answer must come back through the same one. A passkey
belongs to the person signed in, or to the master token when that is who
signed in. Registering one is sudo mode, like issuing a token, except for an
account's first second factor, which it must enrol before doing anything
else and cannot confirm with a factor it does not have yet.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from noust.core.accounts.passkeys import (
    Passkey,
    PasskeyManager,
    PasskeyOwner,
    PasskeysUnavailable,
    RelyingParty,
    require_library,
    resolve_relying_party,
)
from noust.core.accounts.webauthn import WebAuthnError
from noust.web.api.auth import (
    SIGN_IN_EXPIRED,
    ElevateResponse,
    LoginResponse,
    account_signed_in,
    login_failure,
    set_session_cookies,
)
from noust.web.api.deps import NoustErrorRoute, ensure_elevated
from noust.web.auth import (
    audit_event,
    get_client_ip,
    is_fleet,
    is_secure_request,
    record_auth_failure,
    require_auth,
)
from noust.web.permissions.enforce import ensure_notice_accepted
from noust.web.permissions.roles import GRANT_COMPAT
from noust.web.server import get_brute_force, get_token_manager

router = APIRouter(route_class=NoustErrorRoute)

LOGIN_PATH = "/api/auth/passkeys/login"
ELEVATE_PATH = "/api/auth/passkeys/elevate"


class PasskeyInfo(BaseModel):
    """
    One passkey, with no key material in it.

    Attributes:
        id: Store id, to rename or remove it.
        name: What its owner called it.
        owner: The account's username, or ``master``.
        rp_id: The name it was registered under; it signs in nowhere else.
        algorithm: ``ES256``, ``RS256`` or ``EdDSA``.
        synced: Whether the authenticator said it may be synced (a password
            manager's passkey) rather than bound to one device. A label: the
            attestation is ``none``.
        transports: How the browser said it is reached (hints only).
        created_at: When it was registered, UNIX seconds.
        created_by: Who registered it.
        last_used_at: Its last use, UNIX seconds.
        last_used_ip: Where from.
        clone_warning_at: When its signature counter went back - it may have
            been copied - if it ever did.
    """

    id: int
    name: str
    owner: str
    rp_id: str
    algorithm: str
    synced: bool
    transports: list[str] = Field(default_factory=list)
    created_at: float
    created_by: str | None = None
    last_used_at: float | None = None
    last_used_ip: str | None = None
    clone_warning_at: float | None = None

    @classmethod
    def of(cls, passkey: Passkey) -> PasskeyInfo:
        """
        Args:
            passkey: The passkey.

        Returns:
            Its public description.
        """
        data = passkey.to_dict()
        return cls(**{name: data[name] for name in _PASSKEY_FIELDS})


#: The fields of :class:`PasskeyInfo`, read from ``Passkey.to_dict()``.
_PASSKEY_FIELDS = (
    "id",
    "name",
    "owner",
    "rp_id",
    "algorithm",
    "synced",
    "transports",
    "created_at",
    "created_by",
    "last_used_at",
    "last_used_ip",
    "clone_warning_at",
)


class PasskeyAvailability(BaseModel):
    """
    Whether passkeys work for the request that asked, and if not, why.

    Attributes:
        supported: Whether a ceremony can be started from this page.
        rp_id: The name passkeys are bound to here, when supported.
        reason: When not: ``library_missing``, ``ip_address``,
            ``insecure_context``, ``invalid_host`` or ``host_mismatch``. The
            browser knows more (no WebAuthn, a certificate it does not trust)
            and says so itself.
        detail: What is wrong, in one sentence.
        hint: How to fix it.
    """

    supported: bool
    rp_id: str | None = None
    reason: str | None = None
    detail: str | None = None
    hint: str | None = None


class PasskeyListResponse(BaseModel):
    """
    The signed-in owner's passkeys, and whether passkeys work from here.

    Attributes:
        passkeys: Oldest first.
        availability: Whether a ceremony can start from this page.
        allow_synced: Whether passkeys that may be synced are accepted
            (``web.passkeys.allow_synced``).
    """

    passkeys: list[PasskeyInfo]
    availability: PasskeyAvailability
    allow_synced: bool


class CeremonyOptions(BaseModel):
    """
    The options of a WebAuthn ceremony.

    Attributes:
        public_key: The ``publicKey`` member, in WebAuthn's JSON form: pass it
            to ``PublicKeyCredential.parseCreationOptionsFromJSON()`` or
            ``parseRequestOptionsFromJSON()``.
        mediation: For a sign-in, ``conditional`` when asked for the autofill
            flavour (``navigator.credentials.get({mediation: "conditional"})``
            on a field with ``autocomplete="username webauthn"``), else
            ``optional``. None for the other ceremonies.
        expires_in: Seconds the challenge stays valid; ask again after.
    """

    public_key: dict[str, Any]
    mediation: str | None = None
    expires_in: int


class LoginOptionsRequest(BaseModel):
    """
    Asking for sign-in options.

    Attributes:
        conditional: For the autofill (conditional UI) flavour.
        challenge: The second step a right password opened
            (``second_factor.challenge``): the options then offer that
            account's passkeys only, and the sign-in they start finishes it.
    """

    conditional: bool = False
    challenge: str | None = Field(default=None, max_length=128)


class CredentialBody(BaseModel):
    """
    A browser's answer to a ceremony.

    Attributes:
        credential: ``PublicKeyCredential.toJSON()``, as the browser made it.
    """

    credential: dict[str, Any]


class PasskeyLoginRequest(CredentialBody):
    """
    Signing in with a passkey.

    Attributes:
        bearer: Also return the session token in the body, for clients
            without a cookie jar.
        challenge: The second step the options were asked for, when this
            finishes a sign-in whose password was right: only that account's
            passkey is accepted then, and the step is spent.
    """

    bearer: bool = False
    challenge: str | None = Field(default=None, max_length=128)


class PasskeyRegistrationRequest(CredentialBody):
    """
    Registering a passkey.

    Attributes:
        name: What to call it, such as "Work laptop".
    """

    name: str = Field(min_length=1, max_length=64)


class PasskeyRegistered(BaseModel):
    """
    A passkey just registered.

    Attributes:
        passkey: The passkey.
        backup_codes: The account's backup codes, in clear, when this was its
            first second factor: shown exactly once.
    """

    passkey: PasskeyInfo
    backup_codes: list[str] | None = None


class PasskeyRename(BaseModel):
    """
    Renaming a passkey.

    Attributes:
        name: The new name.
    """

    name: str = Field(min_length=1, max_length=64)


class PasskeyRemoved(BaseModel):
    """
    A passkey that was removed.

    Attributes:
        success: Always true.
        passkey: The passkey as it was.
    """

    success: bool
    passkey: PasskeyInfo


def passkeys() -> PasskeyManager:
    """
    Returns:
        The passkey manager over the console's store.
    """
    return PasskeyManager()


def relying_party(request: Request) -> RelyingParty:
    """
    Decide what this console is to a passkey, for this request.

    Args:
        request: The request, for its ``Host`` and whether it came over TLS.

    Returns:
        The relying party.

    Raises:
        PasskeysUnavailable: When passkeys cannot work from here, and why.
    """
    from noust.core.config import Config

    require_library()
    config = Config()
    origins = config.get("web.passkeys.origins") or None
    if isinstance(origins, str):
        origins = [origins]
    return resolve_relying_party(
        request.headers.get("host"),
        secure=is_secure_request(request),
        rp_id=str(config.get("web.passkeys.rp_id") or "") or None,
        origins=[str(item) for item in origins] if origins else None,
        public_url=str(config.get("web.public_url") or "") or None,
    )


def _unavailable(exc: PasskeysUnavailable) -> HTTPException:
    """
    Args:
        exc: Why passkeys cannot work here.

    Returns:
        The answer: ``passkeys_<reason>``, 503 for a missing library (the
        server's fault), 400 for the way the page was reached.
    """
    return HTTPException(
        status_code=503 if exc.reason == "library_missing" else 400,
        detail={
            "error": f"passkeys_{exc.reason}",
            "detail": exc.message,
            "hint": exc.details,
            "fields": None,
        },
    )


def _party(request: Request) -> RelyingParty:
    """
    Args:
        request: The request.

    Returns:
        The relying party.

    Raises:
        HTTPException: ``passkeys_<reason>`` when passkeys cannot work here.
    """
    try:
        return relying_party(request)
    except PasskeysUnavailable as exc:
        raise _unavailable(exc) from exc


def _owner(session: dict[str, Any]) -> PasskeyOwner:
    """
    Whose passkeys a session manages.

    Args:
        session: The authenticated payload.

    Returns:
        The account signed in, or the master token.

    Raises:
        HTTPException: 400 ``passkey_session_required`` for anything but a
            browser session: a token has no person present to touch an
            authenticator, and a ceremony is bound to a session.
    """
    if session.get("type") != "session" or is_fleet(session):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "passkey_session_required",
                "detail": "Passkeys are managed from a signed-in browser session",
                "hint": "Sign in to the console in a browser; tokens cannot hold passkeys.",
                "fields": None,
            },
        )
    account_id = session.get("account_id")
    if account_id is None:
        return PasskeyOwner(account_id=None, username="master")
    account = get_token_manager().accounts.require_id(int(account_id))
    return PasskeyOwner(
        account_id=account.id, username=account.username, display_name=account.display_name
    )


def _binding(session: dict[str, Any]) -> str:
    """
    Args:
        session: A browser session's payload.

    Returns:
        What a ceremony's challenge is bound to: the session's family, which
        survives the rotation of its id on renewal.
    """
    return f"session:{session.get('family') or session.get('sid')}"


def _needs_confirmation(owner: PasskeyOwner) -> bool:
    """
    Report whether adding a passkey asks for sudo mode.

    Args:
        owner: The owner.

    Returns:
        True for the master token, and for an account that already has a
        second factor to confirm with. False for an account enrolling its
        first, which is all it may do until it has one.
    """
    if owner.account_id is None:
        return True
    account = get_token_manager().accounts.get(owner.account_id)
    return account is None or account.has_mfa


def _rejected(exc: WebAuthnError) -> HTTPException:
    """
    Args:
        exc: Why a registration was refused.

    Returns:
        A 400 naming it: the person is signed in, so the reason is theirs to see.
    """
    return HTTPException(
        status_code=400,
        detail={
            "error": "passkey_rejected",
            "detail": str(exc),
            "hint": "Try again; if it keeps failing, use another authenticator.",
            "fields": None,
        },
    )


def _refused(exc: WebAuthnError) -> HTTPException:
    """
    The answer to an assertion that did not verify.

    Uniform, like every refused credential, except where saying more tells a
    guesser nothing and the person a lot: a passkey this server does not know
    (the classic case is another server reached at the same ``localhost``),
    a challenge that ran out, and a page on an origin this console does not
    expect (a proxy to configure).

    Args:
        exc: The refusal.

    Returns:
        A 401.
    """
    error, detail, hint = "invalid_passkey", "The passkey could not be verified.", None
    if exc.reason == "unknown_credential":
        error = "passkey_unknown"
        detail = "This passkey is not registered on this server."
        hint = (
            "It may belong to another server reached at the same address, such as localhost. "
            "Choose this server's passkey, or sign in another way."
        )
    elif exc.reason == "challenge":
        error = "passkey_expired"
        detail = "That took too long, or the request was already used."
        hint = "Try again."
    elif exc.reason == "origin":
        error = "passkey_origin"
        detail = str(exc)
        hint = "Behind a proxy, list the console's public origin in web.passkeys.origins."
    elif exc.reason == "wrong_owner":
        error = "passkey_wrong_owner"
        detail = "That passkey belongs to someone else."
        hint = "Confirm with one of your own passkeys."
    return HTTPException(
        status_code=401, detail={"error": error, "detail": detail, "hint": hint, "fields": None}
    )


def _iso(timestamp: float | None) -> str | None:
    """
    Args:
        timestamp: UNIX seconds, or None.

    Returns:
        ISO 8601 in UTC, as every session field on the wire is written.
    """
    if timestamp is None:
        return None
    return datetime.fromtimestamp(float(timestamp), tz=timezone.utc).isoformat()


def _note_clone(exc: WebAuthnError, client_ip: str, session: dict[str, Any] | None) -> None:
    """
    Put a counter that went back on record, loudly.

    Args:
        exc: The refusal.
        client_ip: Where the assertion came from.
        session: The payload, when one was signed in.
    """
    if exc.reason != "counter":
        return
    audit_event(
        "auth.passkey.clone",
        "warning",
        client_ip=client_ip,
        session=session,
        target=f"credential:{(exc.credential_id or b'').hex()[:16]}",
        detail=str(exc),
    )


# ------------------------------------------------------------------ reading


@router.get("", response_model=PasskeyListResponse)
def list_passkeys(
    request: Request, session: dict[str, Any] = Depends(require_auth)
) -> PasskeyListResponse:
    """
    List the signed-in owner's passkeys, and say whether passkeys work from here.

    Args:
        request: The request, for the availability.
        session: The authenticated payload.

    Returns:
        The passkeys and the availability.
    """
    from noust.core.config import Config

    owner = _owner(session)
    try:
        rp = relying_party(request)
        availability = PasskeyAvailability(supported=True, rp_id=rp.id)
    except PasskeysUnavailable as exc:
        availability = PasskeyAvailability(
            supported=False, reason=exc.reason, detail=exc.message, hint=exc.details
        )
    return PasskeyListResponse(
        passkeys=[PasskeyInfo.of(item) for item in passkeys().list(owner.account_id)],
        availability=availability,
        allow_synced=bool(Config().get("web.passkeys.allow_synced", True)),
    )


# ------------------------------------------------------------- registration


@router.post("/registration/options", response_model=CeremonyOptions)
def registration_options(
    request: Request, session: dict[str, Any] = Depends(require_auth)
) -> CeremonyOptions:
    """
    Start registering a passkey for the signed-in owner.

    Args:
        request: The request.
        session: The authenticated payload.

    Returns:
        The creation options.

    Raises:
        HTTPException: 403 ``elevation_required`` outside sudo mode (except an
            account's first second factor); ``passkeys_<reason>`` when
            passkeys cannot work from this page.
    """
    owner = _owner(session)
    if _needs_confirmation(owner):
        ensure_elevated(request, session)
    rp = _party(request)
    options = passkeys().registration_options(owner, rp, binding=_binding(session))
    return CeremonyOptions(public_key=options, expires_in=int(options["timeout"]) // 1000)


@router.post("/registration", response_model=PasskeyRegistered, status_code=201)
def register_passkey(
    request: Request,
    body: PasskeyRegistrationRequest,
    session: dict[str, Any] = Depends(require_auth),
) -> PasskeyRegistered:
    """
    Finish registering a passkey: verify the authenticator's answer and keep it.

    Args:
        request: The request.
        body: The credential and its name.
        session: The authenticated payload.

    Returns:
        The passkey, and the account's backup codes when this was its first
        second factor.

    Raises:
        HTTPException: 403 ``elevation_required`` as for the options; 400
            ``passkey_rejected`` when the answer does not verify.
        AccountError: When it is already registered, the owner has too many,
            or it may be synced and this server only accepts bound ones.
    """
    owner = _owner(session)
    if _needs_confirmation(owner):
        ensure_elevated(request, session)
    rp = _party(request)
    client_ip = get_client_ip(request)
    try:
        registration = passkeys().register(
            owner,
            rp,
            body.credential,
            name=body.name,
            binding=_binding(session),
            created_by=str(session.get("username") or "master"),
        )
    except WebAuthnError as exc:
        audit_event(
            "auth.passkey.register",
            "failure",
            client_ip=client_ip,
            session=session,
            target=f"account:{owner.username}",
            detail=f"refused: {exc.reason}",
        )
        raise _rejected(exc) from exc
    passkey = registration.passkey
    audit_event(
        "auth.passkey.register",
        "success",
        client_ip=client_ip,
        session=session,
        target=f"passkey:{passkey.id}",
        detail=f"'{passkey.name}' ({passkey.algorithm}, {'synced' if passkey.synced else 'device-bound'}) "
        f"for {owner.username} under {passkey.rp_id}",
    )
    return PasskeyRegistered(
        passkey=PasskeyInfo.of(passkey), backup_codes=registration.backup_codes
    )


# --------------------------------------------------------------- governance


@router.patch("/{passkey_id}", response_model=PasskeyInfo)
def rename_passkey(
    passkey_id: int,
    request: Request,
    body: PasskeyRename,
    session: dict[str, Any] = Depends(require_auth),
) -> PasskeyInfo:
    """
    Rename one of the signed-in owner's passkeys.

    Args:
        passkey_id: Its id.
        request: The request.
        body: The new name.
        session: The authenticated payload.

    Returns:
        The passkey after the change.

    Raises:
        AccountError: When the owner has no passkey with that id.
    """
    owner = _owner(session)
    passkey = passkeys().rename(passkey_id, body.name, account_id=owner.account_id)
    audit_event(
        "auth.passkey.rename",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target=f"passkey:{passkey.id}",
        detail=f"renamed to '{passkey.name}'",
    )
    return PasskeyInfo.of(passkey)


@router.delete("/{passkey_id}", response_model=PasskeyRemoved)
def remove_passkey(
    passkey_id: int, request: Request, session: dict[str, Any] = Depends(require_auth)
) -> PasskeyRemoved:
    """
    Remove one of the signed-in owner's passkeys, in sudo mode.

    Args:
        passkey_id: Its id.
        request: The request.
        session: The authenticated payload.

    Returns:
        The passkey as it was.

    Raises:
        HTTPException: 403 ``elevation_required`` outside sudo mode.
        AccountError: When the owner has no passkey with that id, or it is an
            account's only second factor.
    """
    owner = _owner(session)
    ensure_elevated(request, session)
    passkey = passkeys().remove(passkey_id, account_id=owner.account_id)
    audit_event(
        "auth.passkey.remove",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target=f"passkey:{passkey.id}",
        detail=f"'{passkey.name}' of {owner.username}",
    )
    return PasskeyRemoved(success=True, passkey=PasskeyInfo.of(passkey))


# ------------------------------------------------------------------ sign-in


@router.post("/login/options", response_model=CeremonyOptions)
def login_options(request: Request, body: LoginOptionsRequest) -> CeremonyOptions:
    """
    Start a sign-in with a passkey. Anonymous, and writes nothing.

    The options list no credential: the passkey says whose it is, so nobody
    types a name and nobody can ask which names have passkeys. The second
    step of a sign-in whose password was right is the exception: the options
    list that account's passkeys, which only the person who knows its
    password learns.

    Args:
        request: The request.
        body: Whether this is for the autofill flavour.

    Returns:
        The request options.

    Raises:
        HTTPException: ``passkeys_<reason>`` when passkeys cannot work from
            this page; 401 ``sign_in_expired`` for a second step no longer open.
    """
    rp = _party(request)
    if body.challenge is None:
        options = passkeys().authentication_options(rp, purpose="login", binding="", any_owner=True)
    else:
        account_id = _second_step_account(request, body.challenge)
        options = passkeys().authentication_options(
            rp, purpose="login", binding=_second_step_binding(body.challenge), account_id=account_id
        )
    return CeremonyOptions(
        public_key=options,
        mediation="conditional" if body.conditional else "optional",
        expires_in=int(options["timeout"]) // 1000,
    )


@router.post("/login", response_model=LoginResponse)
def login_with_passkey(
    request: Request, response: Response, body: PasskeyLoginRequest
) -> LoginResponse:
    """
    Sign in with a passkey: a complete sign-in, no password or code.

    A passkey is possession and a PIN or biometric in one, bound to this
    console's name. The account it belongs to must be able to sign in; the
    master token's own passkey signs in as the master token (break-glass, on
    record as such).

    Args:
        request: The request.
        response: Response used to set the session cookies.
        body: The assertion.

    Returns:
        The login result, as ``POST /api/auth/login`` answers it.

    Raises:
        HTTPException: 401 ``invalid_passkey``, ``passkey_unknown``,
            ``passkey_expired`` or ``passkey_origin``; ``passkeys_<reason>``
            when passkeys cannot work from this page.
    """
    rp = _party(request)
    token_manager = get_token_manager()
    client_ip = get_client_ip(request)
    manager = passkeys()
    step_account = None if body.challenge is None else _second_step_account(request, body.challenge)
    try:
        if body.challenge is None:
            passkey = manager.authenticate(
                rp,
                body.credential,
                purpose="login",
                binding="",
                client_ip=client_ip,
                any_owner=True,
            )
        else:
            passkey = manager.authenticate(
                rp,
                body.credential,
                purpose="login",
                binding=_second_step_binding(body.challenge),
                client_ip=client_ip,
                account_id=step_account,
            )
    except WebAuthnError as exc:
        _count_refusal(exc, client_ip, LOGIN_PATH, manager)
        _note_clone(exc, client_ip, None)
        audit_event(
            "auth.passkey.login",
            "failure",
            client_ip=client_ip,
            target=LOGIN_PATH,
            detail=f"passkey sign-in refused: {exc.reason}",
        )
        raise _refused(exc) from exc

    if passkey.account_id is None:
        return _master_session(request, response, body, passkey)

    accounts = token_manager.accounts
    account = accounts.get(passkey.account_id)
    if account is None or not account.can_sign_in():
        record_auth_failure(client_ip, LOGIN_PATH, "passkey")
        audit_event(
            "auth.passkey.login",
            "failure",
            client_ip=client_ip,
            target=f"account:{passkey.owner_name}",
            detail="passkey sign-in refused: the account cannot sign in "
            f"({account.effective_status() if account else 'gone'})",
        )
        raise _refused(WebAuthnError("account", "The account cannot sign in"))

    if body.challenge is not None and not token_manager.spend_login_challenge(body.challenge):
        # Another request finished this step first: one step, one session.
        raise login_failure("sign_in_expired", SIGN_IN_EXPIRED)
    return account_signed_in(
        request,
        response,
        account,
        bearer=body.bearer,
        auth_method="passkey",
        event="auth.passkey.login",
        detail=f"signed in with the passkey '{passkey.name}'"
        + (" after the password" if body.challenge is not None else ""),
        lockout_key=client_ip,
    )


def _second_step_account(request: Request, challenge: str) -> int:
    """
    Read the account a sign-in's second step belongs to.

    Args:
        request: The request presenting it.
        challenge: ``second_factor.challenge``.

    Returns:
        The account's id.

    Raises:
        HTTPException: 401 ``sign_in_expired`` when the step is spent,
            expired or from another address.
    """
    account_id = get_token_manager().login_challenge_account(challenge, get_client_ip(request))
    if account_id is None:
        raise login_failure("sign_in_expired", SIGN_IN_EXPIRED)
    return account_id


def _second_step_binding(challenge: str) -> str:
    """
    Tie a passkey ceremony to the second step it finishes.

    The ceremony's own challenge is signed with this, so options asked for one
    sign-in cannot finish another, nor an anonymous passkey sign-in.

    Args:
        challenge: ``second_factor.challenge``.

    Returns:
        The binding: a digest of the step, never the step itself.
    """
    return "sign-in:" + hashlib.sha256(challenge.encode()).hexdigest()


def _count_refusal(exc: WebAuthnError, client_ip: str, path: str, manager: PasskeyManager) -> None:
    """
    Count a refused assertion against the address, and against the account it named.

    Args:
        exc: The refusal.
        client_ip: Where it came from.
        path: The route, for the lockout's record.
        manager: The passkey manager, to find the account a known credential
            belongs to.
    """
    record_auth_failure(client_ip, path, "passkey")
    if exc.credential_id is None or exc.reason in ("unknown_credential", "challenge"):
        return
    passkey = manager.by_credential_id(exc.credential_id)
    if passkey is not None and passkey.account_id is not None:
        get_token_manager().accounts.record_failure(passkey.account_id, client_ip)


def _master_session(
    request: Request, response: Response, body: PasskeyLoginRequest, passkey: Passkey
) -> LoginResponse:
    """
    Sign in as the master token with its own passkey.

    Args:
        request: The request.
        response: Response used to set the session cookies.
        body: The login request.
        passkey: The master token's passkey that verified.

    Returns:
        The login result, with the grant the master token holds.
    """
    token_manager = get_token_manager()
    client_ip = get_client_ip(request)
    get_brute_force().record_success(client_ip)
    session = token_manager.create_session(client_ip, auth_method="passkey")
    set_session_cookies(response, session, secure=is_secure_request(request))
    grant = token_manager.master_grant()
    audit_event(
        "auth.passkey.login",
        "success",
        client_ip=client_ip,
        session={"sid": session.session_id},
        target=LOGIN_PATH,
        detail=f"the master token's passkey '{passkey.name}' signed in",
    )
    audit_event(
        "auth.break_glass",
        "warning",
        client_ip=client_ip,
        session={"sid": session.session_id},
        target=LOGIN_PATH,
        detail=(
            "the master token signed in with its passkey; no account exists yet"
            if grant == GRANT_COMPAT
            else f"the master token signed in with its passkey ({grant})"
        ),
    )
    return LoginResponse(
        success=True,
        expires_in=session.max_age,
        csrf_token=session.csrf_token,
        session_token=session.token if body.bearer else None,
        grant=grant,
    )


# -------------------------------------------------------------- sudo mode


@router.post("/elevate/options", response_model=CeremonyOptions)
def elevate_options(
    request: Request, session: dict[str, Any] = Depends(require_auth)
) -> CeremonyOptions:
    """
    Start confirming sudo mode with one of the signed-in owner's passkeys.

    The options list the owner's own passkeys, so another server's reached
    at the same address is not offered.

    Args:
        request: The request.
        session: The authenticated payload.

    Returns:
        The request options.

    Raises:
        HTTPException: ``passkeys_<reason>`` when passkeys cannot work from
            this page.
    """
    owner = _owner(session)
    rp = _party(request)
    options = passkeys().authentication_options(
        rp, purpose="elevate", binding=_binding(session), account_id=owner.account_id
    )
    return CeremonyOptions(public_key=options, expires_in=int(options["timeout"]) // 1000)


@router.post("/elevate", response_model=ElevateResponse)
def elevate_with_passkey(
    request: Request, body: CredentialBody, session: dict[str, Any] = Depends(require_auth)
) -> ElevateResponse:
    """
    Confirm it's you with a passkey, opening sudo mode for ten minutes.

    Args:
        request: The request.
        body: The assertion.
        session: The authenticated payload.

    Returns:
        The new elevation deadline.

    Raises:
        HTTPException: 401 as for a passkey sign-in, and
            ``passkey_wrong_owner`` for somebody else's passkey; 403
            ``notice_required`` before the usage notice is accepted.
    """
    ensure_notice_accepted(session)
    owner = _owner(session)
    rp = _party(request)
    client_ip = get_client_ip(request)
    manager = passkeys()
    try:
        passkey = manager.authenticate(
            rp,
            body.credential,
            purpose="elevate",
            binding=_binding(session),
            client_ip=client_ip,
            account_id=owner.account_id,
        )
    except WebAuthnError as exc:
        _count_refusal(exc, client_ip, ELEVATE_PATH, manager)
        _note_clone(exc, client_ip, session)
        audit_event(
            "auth.elevate",
            "failure",
            client_ip=client_ip,
            session=session,
            target=ELEVATE_PATH,
            detail=f"passkey confirmation refused: {exc.reason}",
        )
        raise _refused(exc) from exc
    elevated_until = get_token_manager().elevate(str(session.get("sid")))
    audit_event(
        "auth.elevate",
        "success",
        client_ip=client_ip,
        session=session,
        target=ELEVATE_PATH,
        detail=f"confirmed with the passkey '{passkey.name}'",
    )
    return ElevateResponse(elevated_until=_iso(elevated_until) or "")
