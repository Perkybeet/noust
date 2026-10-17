# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/auth/ldap``: signing in against a directory, and administering the directories.

An endpoint translates HTTP to :class:`~noust.core.accounts.directory.DirectoryManager` and back
(rule 3); what a sign-in does is decided there. The person types the name and password they have in
the directory; the directory proves the password; the account's own second factor still follows,
through the same ending every other sign-in has
(:func:`~noust.web.api.auth.external_sign_in`), so a directory is never a way around it.

A failed sign-in says one thing whatever went wrong with the credentials (an unknown name, a wrong
password), is counted against the address and name like a wrong password is, and leaves the reason
for the audit trail. A directory that cannot be reached is not a guess and is not counted.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from noust.core.accounts.directory import SETTINGS, DirectoryManager
from noust.core.accounts.directory_store import LdapDirectory
from noust.core.accounts.oidc_model import OidcError, OidcRefused
from noust.core.exceptions import ValidationError
from noust.web.api.auth import (
    LoginResponse,
    external_sign_in,
    login_failure,
    refuse_locked_sign_in,
)
from noust.web.api.deps import NoustErrorRoute, require_elevated
from noust.web.api.federation_models import CheckInfo, Mapping, TestResponse
from noust.web.auth import (
    actor_label,
    audit_event,
    get_client_ip,
    record_auth_failure,
    require_auth,
    sign_in_lockout_key,
)
from noust.web.pydantic_compat import dump_model
from noust.web.server import get_token_manager

router = APIRouter(route_class=NoustErrorRoute)

LOGIN_PATH = "/api/auth/ldap/login"

#: What a person is told when the credentials are wrong, whichever part was.
WRONG = "Invalid username or password."


def directory_manager() -> DirectoryManager:
    """
    Returns:
        The directory manager, over the console's own account manager.
    """
    accounts = get_token_manager().accounts
    return DirectoryManager(accounts.store, accounts=accounts)


class LdapLoginRequest(BaseModel):
    """
    A person signing in with the name and password they have in a directory.

    Attributes:
        directory: The directory's name, as the sign-in page lists it.
        username: What they type to sign in there.
        password: Their password.
        bearer: Also return the session token in the body, for clients without a cookie jar.
    """

    directory: str = Field(max_length=32)
    username: str = Field(max_length=256)
    password: str = Field(max_length=1024)
    bearer: bool = False


def _refusal(status: int, error: str, detail: str) -> HTTPException:
    """
    Args:
        status: The HTTP status.
        error: A machine-readable reason.
        detail: The sentence.

    Returns:
        The exception, in the shape every sign-in failure has.
    """
    return HTTPException(
        status_code=status, detail={"error": error, "detail": detail, "hint": None, "fields": None}
    )


@router.post("/login", response_model=LoginResponse)
def login(request: Request, response: Response, body: LdapLoginRequest) -> LoginResponse:
    """
    Sign in with a directory's name and password.

    An account with a second factor is answered the step it owes
    (``second_factor``), to be finished at ``POST /api/auth/login/second-factor``;
    one without is signed in, its session fit only to enrol one.

    Args:
        request: The request.
        response: Used to set the session cookies.
        body: The directory and the credentials.

    Returns:
        Exactly what ``POST /api/auth/login`` answers.

    Raises:
        HTTPException: 401 ``invalid_credentials`` for a wrong name or password; 403
            ``sign_in_refused`` when the policy does not admit the person; 503
            ``sign_in_unavailable`` when the directory cannot be asked; 429 ``locked_out``.
    """
    client_ip = get_client_ip(request)
    lockout_key = sign_in_lockout_key(client_ip, body.username)
    refuse_locked_sign_in(lockout_key, client_ip, LOGIN_PATH)
    try:
        result = directory_manager().authenticate(
            body.directory, body.username, body.password, client_ip
        )
    except OidcRefused as exc:
        _failed(client_ip, f"sign-in refused: {exc.reason}", exc.who)
        raise _refusal(
            403, "sign_in_refused", "This sign-in is not allowed on this server."
        ) from exc
    except OidcError as exc:
        _failed(client_ip, f"sign-in failed: {exc.reason}", exc.who)
        if exc.public == "unavailable":
            raise _refusal(
                503, "sign_in_unavailable", "The directory cannot be asked right now."
            ) from exc
        record_auth_failure(client_ip, LOGIN_PATH, "ldap", lockout_key=lockout_key)
        raise login_failure("invalid_credentials", WRONG) from exc
    return external_sign_in(
        request, response, result.account, via="ldap", detail=result.detail, bearer=body.bearer
    )


def _failed(client_ip: str, detail: str, who: str | None) -> None:
    """
    Put a refused sign-in on the audit trail, without what was typed: a password is often
    typed where the name goes.

    Args:
        client_ip: Where it came from.
        detail: One sentence.
        who: ``<directory>:<subject>`` once the person was identified, else None.
    """
    audit_event(
        "auth.ldap.login",
        "failure",
        client_ip=client_ip,
        target=f"identity:{who}" if who else "directory",
        detail=detail,
    )


# ---------------------------------------------------------------------- admin


class DirectoryInfo(BaseModel):
    """
    A configured directory, with no password in it.

    Attributes:
        name: Its short name.
        label: What the sign-in page says.
        enabled: Whether the sign-in page offers it.
        host: Its name or address.
        port: Its port.
        tls: ``ldaps``, ``starttls`` or ``none``.
        ca_file: The authority bundle it is trusted by, when not the system's.
        bind_dn: The service account that searches; empty searches anonymously.
        bind_password_set: Whether that account's password is stored (never its value).
        user_base: Where people are searched from.
        user_filter: The search, with ``{username}``.
        subject_attribute: The directory's stable id for a person.
        email_attribute: Where their address is.
        name_attribute: Where their display name is.
        username_attribute: Where their login name is.
        groups_attribute: Where the groups they are in are listed.
        group_base: Where groups are searched, when the entry does not list them.
        group_filter: That search, with ``{dn}`` and ``{username}``.
        timeout_seconds: How long one sign-in may take.
        provisioning: A first sign-in: ``off``, ``invite`` or ``auto``.
        default_role: The role of an account that provisioning creates.
        default_team: The team it joins.
        default_team_role: Its role there.
        link_by_email: Link a first sign-in to the account of its address.
        allowed_domains: Only these e-mail domains.
        require_email_verified: Refuse a person without an address.
        assume_email_verified: Take the directory's address as verified.
        group_sync: Whether groups decide roles, memberships and scoped roles.
        group_mappings: What each group means.
        created_at: When it was configured.
        created_by: Who configured it.
    """

    name: str
    label: str
    enabled: bool
    host: str
    port: int
    tls: str
    ca_file: str | None = None
    bind_dn: str
    bind_password_set: bool
    user_base: str
    user_filter: str
    subject_attribute: str
    email_attribute: str
    name_attribute: str
    username_attribute: str
    groups_attribute: str
    group_base: str
    group_filter: str
    timeout_seconds: int
    provisioning: str
    default_role: str
    default_team: str | None = None
    default_team_role: str | None = None
    link_by_email: bool
    allowed_domains: list[str]
    require_email_verified: bool
    assume_email_verified: bool
    group_sync: bool
    group_mappings: list[Mapping]
    created_at: float
    created_by: str | None = None

    @classmethod
    def of(cls, directory: LdapDirectory, password_set: bool) -> DirectoryInfo:
        """
        Args:
            directory: A directory.
            password_set: Whether its service account's password is stored.

        Returns:
            Its public description.
        """
        shown = directory.to_dict()
        keep = {key: shown[key] for key in cls.__annotations__ if key in shown}
        return cls(**keep, bind_password_set=password_set)


class DirectoriesResponse(BaseModel):
    """
    The configured directories.

    Attributes:
        directories: Each one.
    """

    directories: list[DirectoryInfo]


class DirectorySettings(BaseModel):
    """
    The settings of a directory to set; omitted ones are left as they are.

    Attributes:
        label: What the sign-in page says.
        enabled: Whether the sign-in page offers it.
        host: Its name or address.
        port: Its port.
        tls: ``ldaps``, ``starttls`` or ``none`` (this machine only).
        ca_file: An authority bundle to trust instead of the system's.
        bind_dn: The service account that searches.
        bind_password: Its password, stored apart and never shown again.
        clear_bind_password: Forget the stored password (search anonymously).
        user_base: Where people are searched from.
        user_filter: The search, with ``{username}``.
        subject_attribute: The directory's stable id for a person.
        email_attribute: Where their address is.
        name_attribute: Where their display name is.
        username_attribute: Where their login name is.
        groups_attribute: Where the groups they are in are listed.
        group_base: Where groups are searched.
        group_filter: That search, with ``{dn}`` and ``{username}``.
        timeout_seconds: How long one sign-in may take.
        provisioning: ``off``, ``invite`` or ``auto``.
        default_role: The role of a created account.
        default_team: The team a created account joins.
        default_team_role: Its role there.
        link_by_email: Link a first sign-in to the account of its address.
        allowed_domains: Only these e-mail domains.
        require_email_verified: Refuse a person without an address.
        assume_email_verified: Take the directory's address as verified.
        group_sync: Let groups decide roles, memberships and scoped roles.
        group_mappings: What each group means.
    """

    label: str | None = Field(default=None, max_length=64)
    enabled: bool | None = None
    host: str | None = Field(default=None, max_length=255)
    port: int | None = None
    tls: str | None = Field(default=None, max_length=16)
    ca_file: str | None = Field(default=None, max_length=255)
    bind_dn: str | None = Field(default=None, max_length=512)
    bind_password: str | None = Field(default=None, max_length=4096)
    clear_bind_password: bool = False
    user_base: str | None = Field(default=None, max_length=512)
    user_filter: str | None = Field(default=None, max_length=1024)
    subject_attribute: str | None = Field(default=None, max_length=64)
    email_attribute: str | None = Field(default=None, max_length=64)
    name_attribute: str | None = Field(default=None, max_length=64)
    username_attribute: str | None = Field(default=None, max_length=64)
    groups_attribute: str | None = Field(default=None, max_length=64)
    group_base: str | None = Field(default=None, max_length=512)
    group_filter: str | None = Field(default=None, max_length=1024)
    timeout_seconds: int | None = None
    provisioning: str | None = Field(default=None, max_length=16)
    default_role: str | None = Field(default=None, max_length=16)
    default_team: str | None = Field(default=None, max_length=64)
    default_team_role: str | None = Field(default=None, max_length=16)
    link_by_email: bool | None = None
    allowed_domains: list[str] | None = None
    require_email_verified: bool | None = None
    assume_email_verified: bool | None = None
    group_sync: bool | None = None
    group_mappings: list[Mapping] | None = None


class DirectoryRemoved(BaseModel):
    """
    A confirmation that a directory was removed.

    Attributes:
        success: Always true.
        removed: Its name.
    """

    success: bool
    removed: str


def _not_found(exc: ValidationError) -> HTTPException:
    """
    Args:
        exc: The manager's "no such directory".

    Returns:
        The 404 that says so.
    """
    return HTTPException(status_code=404, detail=exc.message)


@router.get("/directories", response_model=DirectoriesResponse)
def list_directories(_session: dict[str, Any] = Depends(require_auth)) -> DirectoriesResponse:
    """
    List the configured directories, without their passwords.

    Args:
        _session: The authenticated payload.

    Returns:
        Each directory and whether its service account's password is stored.
    """
    manager = directory_manager()
    return DirectoriesResponse(
        directories=[
            DirectoryInfo.of(found, manager.data.has_secret(found.name))
            for found in manager.directories()
        ]
    )


@router.put("/directories/{name}", response_model=DirectoryInfo)
def put_directory(
    name: str,
    request: Request,
    body: DirectorySettings,
    session: dict[str, Any] = Depends(require_elevated),
) -> DirectoryInfo:
    """
    Add a directory or change one, in sudo mode: it decides who may sign in.

    Args:
        name: The directory's name.
        request: The request.
        body: The settings to set.
        session: The authenticated payload, in sudo mode.

    Returns:
        The directory as it is now.

    Raises:
        ValidationError: When a setting is refused; nothing is written.
    """
    given = dump_model(body)
    password = given.pop("bind_password")
    clear = bool(given.pop("clear_bind_password"))
    changes = {key: value for key, value in given.items() if value is not None and key in SETTINGS}
    if body.group_mappings is not None:
        changes["group_mappings"] = [
            {key: value for key, value in item.items() if value} for item in given["group_mappings"]
        ]
    manager = directory_manager()
    existed = manager.directory(name) is not None
    saved = manager.configure(
        name,
        changes,
        bind_password=password,
        clear_bind_password=clear,
        actor=actor_label(session),
    )
    audit_event(
        "auth.ldap.config",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target=f"directory:{saved.name}",
        detail=(
            ("changed " if existed else "added ")
            + ", ".join(sorted([*changes, *(["bind_password"] if password else [])]))
        ),
    )
    return DirectoryInfo.of(saved, manager.data.has_secret(saved.name))


@router.delete("/directories/{name}", response_model=DirectoryRemoved)
def delete_directory(
    name: str, request: Request, session: dict[str, Any] = Depends(require_elevated)
) -> DirectoryRemoved:
    """
    Remove a directory and its password, in sudo mode.

    Args:
        name: The directory's name.
        request: The request.
        session: The authenticated payload, in sudo mode.

    Returns:
        A confirmation.

    Raises:
        HTTPException: 404 when there is no such directory.
        LocalLoginRefused: 400 while local sign-in is off and this is the last way in.
    """
    try:
        removed = directory_manager().remove(name)
    except ValidationError as exc:
        raise _not_found(exc) from exc
    audit_event(
        "auth.ldap.config",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target=f"directory:{removed.name}",
        detail="removed",
    )
    return DirectoryRemoved(success=True, removed=removed.name)


@router.post("/directories/{name}/test", response_model=TestResponse)
def test_directory(name: str, _session: dict[str, Any] = Depends(require_auth)) -> TestResponse:
    """
    Look at a directory the way a sign-in would: the connection, the service account, the base.

    Signs nobody in and writes nothing. A directory that cannot be reached is a failed check,
    not an error.

    Args:
        name: The directory's name.
        _session: The authenticated payload.

    Returns:
        Each check and whether all passed.

    Raises:
        HTTPException: 404 when there is no such directory.
    """
    try:
        checks = directory_manager().test(name)
    except ValidationError as exc:
        raise _not_found(exc) from exc
    return TestResponse(
        ok=all(check.ok for check in checks),
        checks=[CheckInfo(name=c.name, ok=c.ok, detail=c.detail) for c in checks],
    )
