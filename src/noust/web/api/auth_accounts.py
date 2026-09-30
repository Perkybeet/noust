"""
Accounts, invitations and separation-of-duties exceptions, over HTTP.

A thin client of :class:`noust.core.accounts.AccountManager`, the same manager
``noust user`` drives: every rule - the password policy, the separation of
duties, single-use invitations - is enforced there, not here. What this module
adds is what only the console knows: that disabling or removing an account
also ends its sessions and tokens, that the first ``admin`` account adopts the
tokens issued before accounts existed, and who did it, for the audit log.

Mounted under ``/api/auth`` by :mod:`noust.web.api.auth`, so a central's fleet
token never reaches it (``noust.web.auth.FLEET_REFUSED_PREFIXES``). Managing
accounts needs ``accounts.manage`` (the ``security`` role, and the master
token), every change in sudo mode; reading them ``accounts.read``. Accepting
an invitation is public: the invitation code is the credential, and a wrong
one is counted by the same lockout as a wrong password.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from noust.core.accounts import Account, AuthenticationFailed, SodException
from noust.core.accounts.model import ROLE_ADMIN, AccountError
from noust.web.api.deps import NoustErrorRoute, require_elevated
from noust.web.auth import (
    actor_label,
    audit_event,
    get_client_ip,
    record_auth_failure,
    require_auth,
)
from noust.web.server import get_token_manager

router = APIRouter(route_class=NoustErrorRoute)


class AccountInfo(BaseModel):
    """
    One account, with no secret in it.

    Attributes:
        id: Store id.
        username: Sign-in name.
        display_name: How the console greets them.
        role: ``viewer``, ``operator``, ``admin``, ``security`` or ``auditor``.
        status: ``active``, ``disabled``, ``locked`` or ``invited``.
        person_ref: Who the account belongs to, for separation of duties.
        mfa_enabled: Whether a second factor is enrolled: an authenticator
            or a passkey.
        passkeys: How many passkeys it has.
        backup_codes_remaining: Unused recovery codes.
        last_login_at: Last successful sign-in, UNIX seconds.
        last_login_ip: Where it came from.
        failures_since_login: Refused attempts since then.
        locked_until: End of a lockout in force, UNIX seconds.
        notice_accepted_at: When the usage notice was last accepted.
        password_changed_at: When the password was last set.
        created_at: When the account was created.
        created_by: Who created it.
        disabled_at: When it was disabled.
        disabled_reason: Why.
    """

    id: int
    username: str
    display_name: str
    role: str
    status: str
    person_ref: str | None = None
    mfa_enabled: bool
    passkeys: int = 0
    backup_codes_remaining: int
    last_login_at: float | None = None
    last_login_ip: str | None = None
    failures_since_login: int = 0
    locked_until: float | None = None
    notice_accepted_at: float | None = None
    password_changed_at: float | None = None
    created_at: float
    created_by: str | None = None
    disabled_at: float | None = None
    disabled_reason: str | None = None

    @classmethod
    def of(cls, account: Account) -> AccountInfo:
        """
        Args:
            account: The account.

        Returns:
            Its public description.
        """
        public = account.to_dict()
        return cls(**{name: public[name] for name in _ACCOUNT_FIELDS})


#: The fields of :class:`AccountInfo`, read from ``Account.to_dict()``;
#: spelled out because the model's own field list is named differently in
#: pydantic 1 and 2.
_ACCOUNT_FIELDS = (
    "id",
    "username",
    "display_name",
    "role",
    "status",
    "person_ref",
    "mfa_enabled",
    "passkeys",
    "backup_codes_remaining",
    "last_login_at",
    "last_login_ip",
    "failures_since_login",
    "locked_until",
    "notice_accepted_at",
    "password_changed_at",
    "created_at",
    "created_by",
    "disabled_at",
    "disabled_reason",
)


class SeparationConflict(BaseModel):
    """
    A person holding roles that should not go together.

    Attributes:
        person_ref: The person.
        accounts: Their accounts, with the role of each.
        exception: Whether a documented exception covers them.
    """

    person_ref: str
    accounts: list[dict[str, str]]
    exception: bool


class AccountsResponse(BaseModel):
    """
    Every account, and any person holding incompatible roles.

    Attributes:
        accounts: The accounts, by username.
        conflicts: People with incompatible roles, exception or not.
    """

    accounts: list[AccountInfo]
    conflicts: list[SeparationConflict]


class AccountCreate(BaseModel):
    """
    A new account with a password.

    Attributes:
        username: Sign-in name.
        role: Its role.
        password: Its first password, checked against the policy.
        display_name: How the console greets them.
        person_ref: Who it belongs to, such as an e-mail address.
    """

    username: str = Field(min_length=2, max_length=64)
    role: str
    password: str = Field(max_length=1024)
    display_name: str | None = Field(default=None, max_length=128)
    person_ref: str | None = Field(default=None, max_length=254)


class AccountUpdate(BaseModel):
    """
    Changes to an account; a field left out is kept.

    Attributes:
        role: A new role.
        display_name: A new display name.
        person_ref: A new person reference; empty clears it.
    """

    role: str | None = None
    display_name: str | None = Field(default=None, max_length=128)
    person_ref: str | None = Field(default=None, max_length=254)


class AccountDisable(BaseModel):
    """
    Why an account is being disabled.

    Attributes:
        reason: One sentence, for the record.
    """

    reason: str | None = Field(default=None, max_length=500)


class InvitationCreate(BaseModel):
    """
    An invitation: a new account, or a recovery for an existing one.

    Attributes:
        username: The account's name.
        role: The role of a new account; ignored for an existing one.
        display_name: Display name of a new account.
        person_ref: Person reference of a new account.
        expires_hours: How long the code is valid.
    """

    username: str = Field(min_length=2, max_length=64)
    role: str | None = None
    display_name: str | None = Field(default=None, max_length=128)
    person_ref: str | None = Field(default=None, max_length=254)
    expires_hours: int = Field(default=24, ge=1, le=24 * 14)


class InvitationIssued(BaseModel):
    """
    An invitation code, shown exactly once.

    Attributes:
        account: The account it is for.
        code: The code; only its digest is stored.
        expires_in: Seconds it is valid for.
    """

    account: AccountInfo
    code: str
    expires_in: int


class InvitationCode(BaseModel):
    """
    An invitation code presented to open it.

    Attributes:
        code: The code, as received.
    """

    code: str = Field(max_length=128)


class InvitationNotice(BaseModel):
    """
    The usage notice an invited person accepts with their invitation.

    Attributes:
        text: The notice.
        version: Its version, to send back.
    """

    text: str
    version: str


class InvitationOpened(BaseModel):
    """
    What an invited person needs to finish: who they are, and their authenticator.

    Attributes:
        username: The account's name.
        display_name: Its display name.
        role: Its role.
        totp_secret: The authenticator secret to enrol, shown here only.
        totp_uri: The ``otpauth://`` URI for the QR code.
        password_min_length: The shortest password accepted.
        notice: The usage notice to accept, when one is configured.
    """

    username: str
    display_name: str
    role: str
    totp_secret: str
    totp_uri: str
    password_min_length: int
    notice: InvitationNotice | None = None


class InvitationAccept(BaseModel):
    """
    Finishing an invitation.

    Attributes:
        code: The invitation code.
        password: The chosen password.
        totp_code: A current code from the enrolled authenticator.
        notice_version: The version of the usage notice accepted, when one
            is configured.
    """

    code: str = Field(max_length=128)
    password: str = Field(max_length=1024)
    totp_code: str = Field(max_length=64)
    notice_version: str | None = Field(default=None, max_length=64)


class InvitationAccepted(BaseModel):
    """
    An account that is now active.

    Attributes:
        username: Its name, to sign in with.
        backup_codes: Single-use recovery codes, shown exactly once.
    """

    username: str
    backup_codes: list[str]


class ExceptionInfo(BaseModel):
    """
    A documented exception to the separation of duties.

    Attributes:
        id: Store id.
        person_ref: The person it covers.
        reason: Why it exists.
        expires_at: When it stops applying, UNIX seconds.
        created_at: When it was recorded.
        created_by: Who recorded it.
        revoked_at: When it was withdrawn early.
        in_force: Whether it applies now.
    """

    id: int
    person_ref: str
    reason: str
    expires_at: float
    created_at: float
    created_by: str | None = None
    revoked_at: float | None = None
    in_force: bool

    @classmethod
    def of(cls, exception: SodException) -> ExceptionInfo:
        """
        Args:
            exception: The exception.

        Returns:
            Its description.
        """
        return cls(**exception.to_dict())


class ExceptionCreate(BaseModel):
    """
    A separation-of-duties exception to record.

    Attributes:
        person_ref: The person it covers.
        reason: Why; what an auditor reads.
        days: How long it applies.
    """

    person_ref: str = Field(min_length=1, max_length=254)
    reason: str = Field(min_length=10, max_length=1000)
    days: int = Field(ge=1, le=366)


class ExceptionsResponse(BaseModel):
    """
    Every exception ever recorded.

    Attributes:
        exceptions: Newest first.
    """

    exceptions: list[ExceptionInfo]


class AccountActionResponse(BaseModel):
    """
    An account after an action on it.

    Attributes:
        account: The account.
        message: What was done.
    """

    account: AccountInfo
    message: str


def _caller_account(session: dict[str, Any]) -> Account | None:
    """
    The account a request acts as: the signed-in account, or an API token's owner.

    Args:
        session: The authenticated payload.

    Returns:
        The account, or None for the master token, a token nobody owns or a
        central.
    """
    if session.get("fleet"):
        return None
    raw = session.get("account_id")
    if raw is None and session.get("type") == "api_token":
        raw = session.get("owner_account_id")
    if raw is None:
        return None
    return get_token_manager().accounts.get(int(raw))


def _refuse_self(session: dict[str, Any], username: str, action: str) -> None:
    """
    Refuse an action on one's own account that another person has to take.

    Changing one's own role, disabling or removing oneself are how a single
    person would escape separation of duties; the root CLI still can. Who
    "oneself" is comes from the account id - a session's account, or the
    owner of the token making the call - never from a username a token's
    payload does not carry.

    Args:
        session: The authenticated payload.
        username: The account acted on.
        action: What was attempted, for the message.

    Raises:
        AccountError: When the account is the caller's own.
    """
    caller = _caller_account(session)
    target = get_token_manager().accounts.find(username)
    own = (caller is not None and target is not None and caller.id == target.id) or (
        bool(session.get("username")) and str(session["username"]) == username.strip().lower()
    )
    if own:
        raise AccountError(
            f"You cannot {action} your own account",
            details="Another security officer can, or root with 'noust user'.",
        )


def _after_role_granted(account: Account) -> None:
    """
    Hand the tokens issued before accounts existed to the first admin account.

    Args:
        account: An account that just became ``admin``.
    """
    if account.role != ROLE_ADMIN:
        return
    token_manager = get_token_manager()
    first = token_manager.accounts.first_admin()
    if first is not None and first.id == account.id:
        token_manager.adopt_unowned_tokens(account.id)


@router.post("/invitations/open", response_model=InvitationOpened)
def open_invitation(request: Request, body: InvitationCode) -> InvitationOpened:
    """
    Open an invitation: say who it is for and hand out the authenticator to enrol.

    Args:
        request: The incoming request.
        body: The invitation code.

    Returns:
        The account, its authenticator secret and the notice to accept.

    Raises:
        HTTPException: 401 ``invalid_invitation`` for a code that is unknown,
            used or expired - counted by the lockout like a wrong password.
    """
    token_manager = get_token_manager()
    client_ip = get_client_ip(request)
    try:
        account, secret = token_manager.accounts.open_invitation(body.code)
    except AuthenticationFailed as exc:
        record_auth_failure(client_ip, "/api/auth/invitations/open", "invitation")
        raise _invalid_invitation() from exc
    from noust.web.api.auth import enrollment_uri

    policy = token_manager.policy()
    version = policy.notice_version
    return InvitationOpened(
        username=account.username,
        display_name=account.display_name,
        role=account.role,
        totp_secret=secret,
        totp_uri=enrollment_uri(secret, account.username),
        password_min_length=policy.password_min_length,
        notice=InvitationNotice(text=policy.notice_text.strip(), version=version)
        if version
        else None,
    )


@router.post("/invitations/accept", response_model=InvitationAccepted)
def accept_invitation(request: Request, body: InvitationAccept) -> InvitationAccepted:
    """
    Finish an invitation: set the password, confirm the authenticator.

    Args:
        request: The incoming request.
        body: The code, the password, a code from the authenticator and the
            notice accepted.

    Returns:
        The account's name and its backup codes, shown exactly once.

    Raises:
        HTTPException: 401 ``invalid_invitation`` for a code that is not a
            live invitation.
        ValidationError: When the password is refused or the authenticator
            code does not match; the invitation stays usable.
    """
    token_manager = get_token_manager()
    client_ip = get_client_ip(request)
    try:
        account, codes = token_manager.accounts.accept_invitation(
            body.code, body.password, body.totp_code, notice_version=body.notice_version
        )
    except AuthenticationFailed as exc:
        record_auth_failure(client_ip, "/api/auth/invitations/accept", "invitation")
        raise _invalid_invitation() from exc
    _after_role_granted(account)
    audit_event(
        "user.activate",
        "success",
        client_ip=client_ip,
        session={"account_id": account.id, "username": account.username},
        target=f"account:{account.username}",
        detail="invitation accepted, password and authenticator set",
    )
    return InvitationAccepted(username=account.username, backup_codes=codes)


def _invalid_invitation() -> HTTPException:
    """
    Returns:
        The one answer to an invitation code that does not open anything.
    """
    return HTTPException(
        status_code=401,
        detail={
            "error": "invalid_invitation",
            "detail": "This invitation is not valid: it may have expired or been used.",
            "hint": "Ask for a new invitation.",
            "fields": None,
        },
    )


@router.get("/accounts", response_model=AccountsResponse)
def list_accounts(session: dict[str, Any] = Depends(require_auth)) -> AccountsResponse:
    """
    List every account, and anyone holding incompatible roles.

    Args:
        session: The authenticated payload.

    Returns:
        The accounts and the separation-of-duties conflicts.
    """
    accounts = get_token_manager().accounts
    return AccountsResponse(
        accounts=[AccountInfo.of(account) for account in accounts.list_all()],
        conflicts=[SeparationConflict(**conflict) for conflict in accounts.separation_conflicts()],
    )


@router.post("/accounts", response_model=AccountInfo, status_code=201)
def create_account(
    request: Request, body: AccountCreate, session: dict[str, Any] = Depends(require_elevated)
) -> AccountInfo:
    """
    Create an account with a password.

    This is also how the first account is created, signed in with the master
    token on a server that has none. The person enrols an authenticator at
    their first sign-in, before anything else.

    Args:
        request: The incoming request.
        body: The account.
        session: The authenticated payload, in sudo mode.

    Returns:
        The account.

    Raises:
        ValidationError: When a field or the password is refused.
        AccountError: When the name is taken or the role clashes.
    """
    account = get_token_manager().accounts.create(
        body.username,
        body.role,
        password=body.password,
        display_name=body.display_name,
        person_ref=body.person_ref,
        created_by=actor_label(session),
    )
    _after_role_granted(account)
    audit_event(
        "user.create",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target=f"account:{account.username}",
        detail=f"role {account.role}",
    )
    return AccountInfo.of(account)


@router.post("/invitations", response_model=InvitationIssued, status_code=201)
def create_invitation(
    request: Request, body: InvitationCreate, session: dict[str, Any] = Depends(require_elevated)
) -> InvitationIssued:
    """
    Invite a person: they set their own password and authenticator.

    For an existing account it is a recovery: accepting it replaces the
    password and the authenticator.

    Args:
        request: The incoming request.
        body: Who, with what role, and for how long.
        session: The authenticated payload, in sudo mode.

    Returns:
        The account and the code, shown exactly once.

    Raises:
        ValidationError: When a field is refused.
        AccountError: When the role clashes or the account is disabled.
    """
    accounts = get_token_manager().accounts
    existing = accounts.find(body.username)
    if existing is not None:
        # A recovery gives the account's role to whoever holds the code.
        accounts.check_grant(existing, _caller_account(session), "issue a recovery invitation for")
    account, code = accounts.invite(
        body.username,
        body.role,
        display_name=body.display_name,
        person_ref=body.person_ref,
        created_by=actor_label(session),
        expires_hours=body.expires_hours,
    )
    audit_event(
        "user.invite",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target=f"account:{account.username}",
        detail=f"invitation valid for {body.expires_hours} hours",
    )
    return InvitationIssued(
        account=AccountInfo.of(account), code=code, expires_in=body.expires_hours * 3600
    )


@router.get("/accounts/{username}", response_model=AccountInfo)
def get_account(username: str, session: dict[str, Any] = Depends(require_auth)) -> AccountInfo:
    """
    Describe one account.

    Args:
        username: The account's name.
        session: The authenticated payload.

    Returns:
        The account.

    Raises:
        AccountNotFoundError: When no account has that name.
    """
    return AccountInfo.of(get_token_manager().accounts.require(username))


@router.patch("/accounts/{username}", response_model=AccountInfo)
def update_account(
    username: str,
    request: Request,
    body: AccountUpdate,
    session: dict[str, Any] = Depends(require_elevated),
) -> AccountInfo:
    """
    Change an account's role, display name or person reference.

    A role change applies to the account's sessions and tokens on their next
    request: the role is read on every one.

    Args:
        username: The account's name.
        request: The incoming request.
        body: The changes.
        session: The authenticated payload, in sudo mode.

    Returns:
        The account after the change.

    Raises:
        AccountNotFoundError: When no account has that name.
        AccountError: When the change breaks separation of duties, or is a
            change of one's own role.
    """
    accounts = get_token_manager().accounts
    client_ip = get_client_ip(request)
    account = accounts.require(username)
    if body.display_name is not None or body.person_ref is not None:
        account = accounts.update_profile(
            username, display_name=body.display_name, person_ref=body.person_ref
        )
        audit_event(
            "user.update",
            "success",
            client_ip=client_ip,
            session=session,
            target=f"account:{account.username}",
        )
    if body.role is not None and body.role != account.role:
        _refuse_self(session, username, "change the role of")
        previous = account.role
        account = accounts.set_role(username, body.role)
        _after_role_granted(account)
        audit_event(
            "user.role_change",
            "success",
            client_ip=client_ip,
            session=session,
            target=f"account:{account.username}",
            detail=f"role {previous} -> {account.role}",
        )
    return AccountInfo.of(account)


@router.delete("/accounts/{username}", response_model=AccountActionResponse)
def remove_account(
    username: str, request: Request, session: dict[str, Any] = Depends(require_elevated)
) -> AccountActionResponse:
    """
    Remove an account, ending its sessions and tokens.

    Prefer disabling: a removed account's name in the audit log no longer
    names a record.

    Args:
        username: The account's name.
        request: The incoming request.
        session: The authenticated payload, in sudo mode.

    Returns:
        The account as it was.

    Raises:
        AccountNotFoundError: When no account has that name.
        AccountError: For one's own account.
    """
    _refuse_self(session, username, "remove")
    token_manager = get_token_manager()
    account = token_manager.accounts.require(username)
    token_manager.end_account_access(account.id)
    removed = token_manager.accounts.remove(username)
    audit_event(
        "user.remove",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target=f"account:{removed.username}",
    )
    return AccountActionResponse(account=AccountInfo.of(removed), message="Account removed")


@router.post("/accounts/{username}/disable", response_model=AccountActionResponse)
def disable_account(
    username: str,
    request: Request,
    body: AccountDisable,
    session: dict[str, Any] = Depends(require_elevated),
) -> AccountActionResponse:
    """
    Disable an account: its sessions and tokens stop working at once.

    Args:
        username: The account's name.
        request: The incoming request.
        body: Why.
        session: The authenticated payload, in sudo mode.

    Returns:
        The account after the change.

    Raises:
        AccountNotFoundError: When no account has that name.
        AccountError: For one's own account.
    """
    _refuse_self(session, username, "disable")
    token_manager = get_token_manager()
    account = token_manager.accounts.disable(username, body.reason)
    sessions, tokens = token_manager.end_account_access(account.id)
    audit_event(
        "user.disable",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target=f"account:{account.username}",
        detail=f"revoked {sessions} session(s) and {tokens} token(s)",
    )
    return AccountActionResponse(account=AccountInfo.of(account), message="Account disabled")


@router.post("/accounts/{username}/enable", response_model=AccountActionResponse)
def enable_account(
    username: str, request: Request, session: dict[str, Any] = Depends(require_elevated)
) -> AccountActionResponse:
    """
    Enable a disabled account again.

    Args:
        username: The account's name.
        request: The incoming request.
        session: The authenticated payload, in sudo mode.

    Returns:
        The account after the change.

    Raises:
        AccountNotFoundError: When no account has that name.
    """
    account = get_token_manager().accounts.enable(username)
    audit_event(
        "user.enable",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target=f"account:{account.username}",
    )
    return AccountActionResponse(account=AccountInfo.of(account), message="Account enabled")


@router.post("/accounts/{username}/unlock", response_model=AccountActionResponse)
def unlock_account(
    username: str, request: Request, session: dict[str, Any] = Depends(require_elevated)
) -> AccountActionResponse:
    """
    Lift an account's lockout before it runs out.

    Args:
        username: The account's name.
        request: The incoming request.
        session: The authenticated payload, in sudo mode.

    Returns:
        The account after the change.

    Raises:
        AccountNotFoundError: When no account has that name.
    """
    account = get_token_manager().accounts.unlock(username)
    audit_event(
        "user.unlock",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target=f"account:{account.username}",
    )
    return AccountActionResponse(account=AccountInfo.of(account), message="Account unlocked")


@router.post("/accounts/{username}/reset-mfa", response_model=AccountActionResponse)
def reset_account_mfa(
    username: str, request: Request, session: dict[str, Any] = Depends(require_elevated)
) -> AccountActionResponse:
    """
    Remove an account's authenticator; it enrols a new one at its next sign-in.

    Its sessions end, so the next thing it does is sign in again.

    Args:
        username: The account's name.
        request: The incoming request.
        session: The authenticated payload, in sudo mode.

    Returns:
        The account after the change.

    Raises:
        AccountNotFoundError: When no account has that name.
    """
    token_manager = get_token_manager()
    account = token_manager.accounts.reset_mfa(username)
    token_manager.sessions.revoke_account(account.id)
    audit_event(
        "auth.mfa.reset",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target=f"account:{account.username}",
    )
    return AccountActionResponse(account=AccountInfo.of(account), message="Second factor reset")


@router.get("/exceptions", response_model=ExceptionsResponse)
def list_exceptions(session: dict[str, Any] = Depends(require_auth)) -> ExceptionsResponse:
    """
    List every separation-of-duties exception ever recorded.

    Args:
        session: The authenticated payload.

    Returns:
        The exceptions, newest first.
    """
    return ExceptionsResponse(
        exceptions=[
            ExceptionInfo.of(item) for item in get_token_manager().accounts.list_exceptions()
        ]
    )


@router.post("/exceptions", response_model=ExceptionInfo, status_code=201)
def create_exception(
    request: Request, body: ExceptionCreate, session: dict[str, Any] = Depends(require_elevated)
) -> ExceptionInfo:
    """
    Record a documented exception to the separation of duties, with an end date.

    Args:
        request: The incoming request.
        body: The person, the reason and how long.
        session: The authenticated payload, in sudo mode.

    Returns:
        The exception.

    Raises:
        ValidationError: When a field is refused.
    """
    exception = get_token_manager().accounts.add_exception(
        body.person_ref, body.reason, days=body.days, created_by=actor_label(session)
    )
    audit_event(
        "user.sod_exception",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target=f"person:{exception.person_ref}",
        detail=f"exception for {body.days} days: {exception.reason[:200]}",
    )
    return ExceptionInfo.of(exception)


@router.delete("/exceptions/{exception_id}", response_model=ExceptionsResponse)
def revoke_exception(
    exception_id: int, request: Request, session: dict[str, Any] = Depends(require_elevated)
) -> ExceptionsResponse:
    """
    Withdraw a separation-of-duties exception before it runs out.

    Args:
        exception_id: Its id.
        request: The incoming request.
        session: The authenticated payload, in sudo mode.

    Returns:
        Every exception, after the change.

    Raises:
        HTTPException: 404 when no exception in force has that id.
    """
    accounts = get_token_manager().accounts
    if not accounts.revoke_exception(exception_id):
        raise HTTPException(status_code=404, detail=f"No exception in force with id {exception_id}")
    audit_event(
        "user.sod_exception",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target=f"exception:{exception_id}",
        detail="withdrawn",
    )
    return ExceptionsResponse(
        exceptions=[ExceptionInfo.of(item) for item in accounts.list_exceptions()]
    )
