# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
SCIM 2.0 provisioning: an identity provider keeps the roster of who may have an account here.

Retiring a person at the identity provider has to retire their access, and waiting for their
next sign-in is too late: a session or an API token they already hold keeps working. SCIM
(RFC 7643 and 7644) is how the provider says so as it happens. :class:`ScimService` is the
one implementation of what a provider's calls do, called by ``/api/scim/v2`` and tested
without HTTP; :class:`ScimManager` configures the sources and authenticates their tokens.

**What a provider can and cannot do.**

- It creates the accounts of people it tells us about. They are ``invited`` and have no
  credential: the person's first sign-in at an identity provider (OpenID Connect, SAML or a
  directory), whose verified e-mail is the account's, activates it. SCIM keeps the roster;
  single sign-on signs people in; the e-mail ties them.
- It switches an account off when the person is deactivated or deleted, which ends their
  sessions and API tokens, and back on only if it was the provider that switched it off:
  an administrator's own decision is never overridden.
- Its groups, through the same mapping every source has, give roles, team memberships and
  roles at a scope, as they are pushed, so leaving a group takes effect at once.
- It never reaches an account it did not provision, never changes an account's e-mail (the
  reference separation of duties judges by), never deletes an account (the audit trail keeps
  naming somebody) and never gives ``security``, ``auditor`` or a team ``owner``.

A source authenticates with a bearer token of its own, kept as a SHA-256: 256 random bits
need no slow hash, and a stolen database yields nothing to present.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import sqlite3
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from noust.core.accounts.federation import (
    Admission,
    apply_admission_setting,
    check_references,
    settle,
)
from noust.core.accounts.manager import AccountManager
from noust.core.accounts.model import (
    STATUS_DISABLED,
    Account,
    AccountError,
    clean_display_name,
)
from noust.core.accounts.oidc_model import IdentityClaims, OidcRefused, validate_name
from noust.core.accounts.oidc_store import OidcStore
from noust.core.accounts.scim_schema import SCHEMA_GROUP, SCHEMA_LIST, SCHEMA_USER
from noust.core.accounts.scim_store import (
    PROTOCOL_SCIM,
    ScimGroup,
    ScimSource,
    ScimStore,
    ScimUser,
)
from noust.core.audit import record as audit_record
from noust.core.audit.actor import Actor
from noust.core.exceptions import NoustError, ValidationError

if TYPE_CHECKING:
    from noust.core.store import NoustStore

logger = logging.getLogger(__name__)

SCHEMA_PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
SCHEMA_ERROR = "urn:ietf:params:scim:api:messages:2.0:Error"

#: A SCIM token is told apart from an API token (``noust_tok_``) by its prefix, so that one
#: can never be presented where the other is expected.
TOKEN_PREFIX = "noust_scim_"  # noqa: S105 - a public prefix, not a credential

#: The settings :meth:`ScimManager.configure` accepts.
SETTINGS: tuple[str, ...] = (
    "label",
    "enabled",
    "default_role",
    "default_team",
    "default_team_role",
    "link_by_email",
    "allowed_domains",
    "group_sync",
    "group_mappings",
)

MAX_PAGE = 200
MAX_MEMBERS = 5000
MAX_NAME = 256
_FILTER = re.compile(r'\s*([A-Za-z][A-Za-z0-9._]*)\s+eq\s+"((?:[^"\\]|\\.)*)"\s*', re.IGNORECASE)
_MEMBER_FILTER = re.compile(r'members\[\s*value\s+eq\s+"((?:[^"\\]|\\.)*)"\s*\]', re.IGNORECASE)
_EMAIL_PATH = re.compile(r"emails(?:\[[^\]]*\])?(?:\.value)?", re.IGNORECASE)
_DISABLED_BY = "deprovisioned by SCIM source "


class ScimError(NoustError):
    """
    A request the provider made cannot be done, in the terms RFC 7644 §3.12 answers it.

    Attributes:
        status: The HTTP status.
        scim_type: The ``scimType`` keyword, when the protocol has one.
    """

    def __init__(self, status: int, detail: str, scim_type: str | None = None) -> None:
        """
        Args:
            status: The HTTP status.
            detail: One sentence saying what is wrong.
            scim_type: ``uniqueness``, ``invalidValue``, ``invalidFilter``, ``noTarget``,
                ``invalidPath`` or ``mutability``.
        """
        super().__init__(detail)
        self.status = status
        self.scim_type = scim_type

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The error as the protocol writes it.
        """
        body: dict[str, Any] = {
            "schemas": [SCHEMA_ERROR],
            "status": str(self.status),
            "detail": self.message,
        }
        if self.scim_type:
            body["scimType"] = self.scim_type
        return body


def new_token() -> str:
    """
    Returns:
        A fresh bearer token: 256 random bits behind a recognisable prefix.
    """
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """
    Args:
        token: A bearer token as presented.

    Returns:
        Its SHA-256, which is all that is kept.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def parse_filter(text: str | None) -> tuple[str, str] | None:
    """
    Read the one filter shape providers use to find a resource they know by name.

    Args:
        text: ``attribute eq "value"``, or nothing.

    Returns:
        The attribute (lower-case) and the value; None for no filter.

    Raises:
        ScimError: 400 ``invalidFilter`` for anything else. A server that ignored a filter it
            did not understand would answer "all of them" to a question about one, and a
            provider would conclude the user it looked for exists.
    """
    if text is None or not text.strip():
        return None
    matched = _FILTER.fullmatch(text)
    if matched is None:
        raise ScimError(400, 'Only the filter attribute eq "value" is supported', "invalidFilter")
    value = re.sub(r"\\(.)", r"\1", matched.group(2))
    return matched.group(1).lower(), value


def truthy(value: Any) -> bool | None:
    """
    Args:
        value: What a provider sent for a flag: a boolean, or text (Entra sends "True").

    Returns:
        The flag, or None when it is neither.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def _stamp(value: float) -> str:
    """
    Args:
        value: UNIX seconds.

    Returns:
        The time as SCIM writes it.
    """
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(value: Any, what: str, *, limit: int = MAX_NAME, required: bool = False) -> str | None:
    """
    Args:
        value: A value from a provider's document.
        what: Its name, for the message.
        limit: The longest it may be.
        required: Refuse its absence.

    Returns:
        The text, trimmed, or None when absent and not required.

    Raises:
        ScimError: 400 ``invalidValue`` when it is not short printable text.
    """
    if value is None or value == "":
        if required:
            raise ScimError(400, f"{what} is required", "invalidValue")
        return None
    if not isinstance(value, str):
        raise ScimError(400, f"{what} must be text", "invalidValue")
    text = value.strip()
    if len(text) > limit or any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ScimError(400, f"{what} is not acceptable", "invalidValue")
    if required and not text:
        raise ScimError(400, f"{what} is required", "invalidValue")
    return text or None


@dataclass(frozen=True)
class UserFields:
    """
    What a provider's user document says that matters here.

    Attributes:
        user_name: ``userName``.
        external_id: ``externalId``.
        display_name: How the person is greeted.
        email: Their primary address, lower-case; it is how the person is tied to the account.
        active: Whether the provider has them active; None when it did not say.
    """

    user_name: str | None = None
    external_id: str | None = None
    display_name: str | None = None
    email: str | None = None
    active: bool | None = None


def user_fields(doc: Mapping[str, Any]) -> UserFields:
    """
    Read a user document.

    Args:
        doc: The provider's JSON, ``schemas`` and all.

    Returns:
        The fields that matter.

    Raises:
        ScimError: 400 ``invalidValue`` for one that is not what it should be.
    """
    user_name = _text(doc.get("userName"), "userName")
    raw_name = doc.get("name")
    name: Mapping[str, Any] = raw_name if isinstance(raw_name, dict) else {}
    display = _text(doc.get("displayName"), "displayName") or _text(
        name.get("formatted"), "name.formatted"
    )
    if display is None:
        given = _text(name.get("givenName"), "name.givenName") or ""
        family = _text(name.get("familyName"), "name.familyName") or ""
        display = f"{given} {family}".strip() or None
    email: str | None = None
    emails = doc.get("emails")
    if isinstance(emails, list):
        entries = [e for e in emails if isinstance(e, dict) and e.get("value")]
        entries.sort(key=lambda entry: 0 if truthy(entry.get("primary")) is True else 1)
        if entries:
            email = _text(entries[0].get("value"), "emails.value")
    if email is None and user_name and "@" in user_name:
        email = user_name
    active = doc.get("active")
    flag = None if active is None else truthy(active)
    if active is not None and flag is None:
        raise ScimError(400, "active must be true or false", "invalidValue")
    return UserFields(
        user_name=user_name,
        external_id=_text(doc.get("externalId"), "externalId"),
        display_name=display,
        email=email.lower() if email else None,
        active=flag,
    )


def patch_operations(body: Mapping[str, Any]) -> list[tuple[str, str, Any]]:
    """
    Read the operations of a PATCH.

    Args:
        body: The provider's document.

    Returns:
        ``(op, path, value)`` for each, the operation lower-cased and the path as written
        (empty when the value is a document of attributes).

    Raises:
        ScimError: 400 ``invalidValue`` when it is not a PatchOp or an operation is unknown.
    """
    operations = body.get("Operations", body.get("operations"))
    if not isinstance(operations, list) or not operations:
        raise ScimError(400, "A PATCH carries a list of Operations", "invalidValue")
    if len(operations) > 100:
        raise ScimError(400, "At most 100 operations in one PATCH", "invalidValue")
    found: list[tuple[str, str, Any]] = []
    for operation in operations:
        if not isinstance(operation, dict):
            raise ScimError(400, "An operation is an object", "invalidValue")
        op = str(operation.get("op") or "").strip().lower()
        if op not in ("add", "remove", "replace"):
            raise ScimError(400, f"Unknown operation: {op[:20]!r}", "invalidValue")
        path = operation.get("path")
        found.append((op, path.strip() if isinstance(path, str) else "", operation.get("value")))
    return found


class ScimService:
    """
    What a provider's calls do to the accounts, the roster and the groups.

    Args:
        source: The source that authenticated.
        accounts: The account manager.
        data: The roster's store.
        admission: The shared admission, for creating accounts and applying groups.
        base_url: Where the SCIM API is, for the ``location`` of each resource.
        client_ip: Where the request came from, for the audit trail.
        clock: The time source.
    """

    def __init__(
        self,
        source: ScimSource,
        accounts: AccountManager,
        data: ScimStore,
        admission: Admission,
        *,
        base_url: str = "/api/scim/v2",
        client_ip: str = "",
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.source = source
        self.accounts = accounts
        self.data = data
        self.admission = admission
        self.base_url = base_url.rstrip("/")
        self.client_ip = client_ip
        self._clock = clock or time.time
        #: Accounts switched off in this call, for the web layer to end the sessions and
        #: API tokens of (it holds the token manager; this module does not).
        self.ended: list[int] = []

    # --------------------------------------------------------------- helpers

    def actor(self) -> Actor:
        """
        Returns:
            Who acts in the audit trail: the source, not a person.
        """
        return Actor(
            kind="system",
            name=f"{PROTOCOL_SCIM}:{self.source.name}",
            via=PROTOCOL_SCIM,
            source=self.client_ip,
        )

    def _audit(self, event: str, username: str, detail: str) -> None:
        """
        Args:
            event: A catalog event.
            username: The account it concerns.
            detail: One sentence.
        """
        audit_record(
            event, actor=self.actor(), target=f"account:{username}", details={"detail": detail}
        )

    def _account(self, user: ScimUser) -> Account:
        """
        Args:
            user: A roster entry.

        Returns:
            Its account.

        Raises:
            ScimError: 404 when the account is gone (the roster entry is then stale).
        """
        account = self.accounts.get(user.account_id)
        if account is None:
            self.data.delete_user(user.id)
            raise ScimError(404, "User not found")
        return account

    def _require_user(self, user_id: str) -> ScimUser:
        """
        Args:
            user_id: The id a provider used.

        Returns:
            The roster entry.

        Raises:
            ScimError: 404 when this source has no such user.
        """
        user = self.data.get_user(self.source.name, user_id)
        if user is None:
            raise ScimError(404, f"User {user_id[:64]} not found")
        return user

    def _require_group(self, group_id: str) -> ScimGroup:
        """
        Args:
            group_id: The id a provider used.

        Returns:
            The group.

        Raises:
            ScimError: 404 when this source has no such group.
        """
        group = self.data.get_group(self.source.name, group_id)
        if group is None:
            raise ScimError(404, f"Group {group_id[:64]} not found")
        return group

    def _check_domain(self, email: str) -> None:
        """
        Args:
            email: The person's address.

        Raises:
            ScimError: 400 when the source is limited to some domains and this is not one.
        """
        if self.source.allowed_domains:
            domain = email.rsplit("@", 1)[-1]
            if domain not in self.source.allowed_domains:
                raise ScimError(
                    400, "This e-mail's domain is not allowed to be provisioned", "invalidValue"
                )

    # ------------------------------------------------------------- rendering

    def render_user(self, user: ScimUser) -> dict[str, Any]:
        """
        Args:
            user: A roster entry.

        Returns:
            Its SCIM resource: the account as the provider should see it.
        """
        account = self._account(user)
        body: dict[str, Any] = {
            "schemas": [SCHEMA_USER],
            "id": user.id,
            "userName": user.user_name,
            "active": account.status != STATUS_DISABLED,
            "meta": {
                "resourceType": "User",
                "created": _stamp(user.created_at),
                "lastModified": _stamp(user.updated_at),
                "location": f"{self.base_url}/Users/{user.id}",
            },
        }
        if user.external_id:
            body["externalId"] = user.external_id
        if account.display_name:
            body["displayName"] = account.display_name
            body["name"] = {"formatted": account.display_name}
        if account.person_ref and "@" in account.person_ref:
            body["emails"] = [{"value": account.person_ref, "primary": True, "type": "work"}]
        groups = self.data.groups_of(user.id)
        if groups:
            body["groups"] = [
                {
                    "value": group.id,
                    "display": group.display_name,
                    "$ref": f"{self.base_url}/Groups/{group.id}",
                }
                for group in groups
            ]
        return body

    def render_group(self, group: ScimGroup) -> dict[str, Any]:
        """
        Args:
            group: A group.

        Returns:
            Its SCIM resource, with who is in it.
        """
        members = []
        for user_id in self.data.members(group.id):
            user = self.data.get_user(self.source.name, user_id)
            if user is not None:
                members.append(
                    {
                        "value": user.id,
                        "display": user.user_name,
                        "$ref": f"{self.base_url}/Users/{user.id}",
                    }
                )
        body: dict[str, Any] = {
            "schemas": [SCHEMA_GROUP],
            "id": group.id,
            "displayName": group.display_name,
            "members": members,
            "meta": {
                "resourceType": "Group",
                "created": _stamp(group.created_at),
                "lastModified": _stamp(group.updated_at),
                "location": f"{self.base_url}/Groups/{group.id}",
            },
        }
        if group.external_id:
            body["externalId"] = group.external_id
        return body

    @staticmethod
    def listing(resources: list[dict[str, Any]], total: int, start: int) -> dict[str, Any]:
        """
        Args:
            resources: The page.
            total: How many match.
            start: The 1-based index of the first.

        Returns:
            A ListResponse.
        """
        return {
            "schemas": [SCHEMA_LIST],
            "totalResults": total,
            "startIndex": start,
            "itemsPerPage": len(resources),
            "Resources": resources,
        }

    @staticmethod
    def window(start_index: int | None, count: int | None) -> tuple[int, int, int]:
        """
        Args:
            start_index: ``startIndex`` (1-based), or None.
            count: ``count``, or None.

        Returns:
            The 1-based start, the offset and the limit, kept within :data:`MAX_PAGE`.
        """
        start = max(1, start_index or 1)
        limit = MAX_PAGE if count is None else max(0, min(count, MAX_PAGE))
        return start, start - 1, limit

    # ------------------------------------------------------------------ users

    def list_users(
        self, filter_text: str | None, start_index: int | None, count: int | None
    ) -> dict[str, Any]:
        """
        Args:
            filter_text: ``userName eq "..."``, ``externalId eq "..."`` or ``id eq "..."``.
            start_index: ``startIndex``.
            count: ``count``.

        Returns:
            A ListResponse of users.

        Raises:
            ScimError: 400 ``invalidFilter`` for any other filter.
        """
        parsed = parse_filter(filter_text)
        start, offset, limit = self.window(start_index, count)
        user_name: str | None = None
        external_id: str | None = None
        if parsed is not None:
            attribute, value = parsed
            if attribute == "id":
                found = self.data.get_user(self.source.name, value)
                resources = [self.render_user(found)] if found is not None and limit else []
                return self.listing(resources, 1 if found is not None else 0, start)
            if attribute == "username":
                user_name = value
            elif attribute == "externalid":
                external_id = value
            else:
                raise ScimError(400, f"Cannot filter users by {attribute}", "invalidFilter")
        total, users = self.data.list_users(
            self.source.name,
            user_name=user_name,
            external_id=external_id,
            offset=offset,
            limit=limit,
        )
        return self.listing([self.render_user(user) for user in users], total, start)

    def get_user(self, user_id: str) -> dict[str, Any]:
        """
        Args:
            user_id: The id a provider used.

        Returns:
            The user's resource.

        Raises:
            ScimError: 404 when this source has no such user.
        """
        return self.render_user(self._require_user(user_id))

    def create_user(self, doc: Mapping[str, Any]) -> dict[str, Any]:
        """
        Provision a person: an invited account with no credential, and a roster entry.

        Args:
            doc: The provider's user document.

        Returns:
            The user's resource.

        Raises:
            ScimError: 400 for a document without a userName or an e-mail, or a domain not
                allowed; 409 ``uniqueness`` when the user or the e-mail is taken.
        """
        fields = user_fields(doc)
        if not fields.user_name:
            raise ScimError(400, "userName is required", "invalidValue")
        if fields.email:
            self._check_domain(fields.email)
        elif self.source.allowed_domains:
            raise ScimError(
                400,
                "This source provisions only some e-mail domains, so a person needs an e-mail",
                "invalidValue",
            )
        name = self.source.name
        if self.data.find_user(name, user_name=fields.user_name) or (
            fields.external_id and self.data.find_user(name, external_id=fields.external_id)
        ):
            raise ScimError(409, "A user with this userName or externalId exists", "uniqueness")
        user_id = uuid.uuid4().hex
        actor = self.actor()
        person = IdentityClaims(
            issuer=f"{PROTOCOL_SCIM}:{name}",
            subject=user_id,
            email=fields.email,
            name=fields.display_name,
            username=(fields.email or fields.user_name).split("@", 1)[0],
        )
        # A person without an e-mail is made, invited: nobody can sign in as them until the
        # provider sends one or an administrator links them, because the e-mail is what ties a
        # sign-in to an account.
        existing = self.accounts.find_by_person(fields.email) if fields.email else []
        if existing:
            account = self._attach(existing, fields)
            event, detail = "auth.scim.link", "attached to the account of the same e-mail"
        else:
            try:
                account = self.admission.create(self.source, person, actor, invited=True)
            except OidcRefused as exc:
                raise ScimError(500 if exc.reason == "team_missing" else 409, exc.message) from exc
            event, detail = "auth.scim.provision", f"created as {account.role}, waiting to sign in"
        now = self._clock()
        user = ScimUser(
            id=user_id,
            source=name,
            account_id=account.id,
            external_id=fields.external_id,
            user_name=fields.user_name,
            disabled_by_scim=False,
            mapped_role=None,
            created_at=now,
            updated_at=now,
        )
        try:
            self.data.add_user(user)
        except sqlite3.IntegrityError as exc:
            raise ScimError(409, "This account is already in the roster", "uniqueness") from exc
        self._audit(event, account.username, detail)
        if fields.active is False and account.status != STATUS_DISABLED:
            self._deactivate(user, account)
        return self.render_user(user)

    def _attach(self, existing: list[Account], fields: UserFields) -> Account:
        """
        Attach a provisioned user to an account that already has their e-mail.

        Args:
            existing: The accounts of that e-mail.
            fields: What the provider said.

        Returns:
            The account, enabled again if this source switched it off and deleted the user.

        Raises:
            ScimError: 409 ``uniqueness`` unless the source links by e-mail and there is one.
        """
        if not self.source.link_by_email or len(existing) != 1:
            raise ScimError(
                409,
                "An account with this e-mail exists and is not linked to this provider",
                "uniqueness",
            )
        account = existing[0]
        if self.data.find_user(self.source.name, account_id=account.id):
            raise ScimError(409, "This account is already in the roster", "uniqueness")
        if account.status == STATUS_DISABLED:
            if not (account.disabled_reason or "").startswith(_DISABLED_BY + self.source.name):
                raise ScimError(
                    409,
                    "The account of this e-mail was disabled by an administrator",
                    "uniqueness",
                )
            try:
                account = self.accounts.enable(account.username)
            except AccountError as exc:
                raise ScimError(409, exc.message, "uniqueness") from exc
        return account

    def replace_user(self, user_id: str, doc: Mapping[str, Any]) -> dict[str, Any]:
        """
        Args:
            user_id: The id a provider used.
            doc: The provider's whole user document.

        Returns:
            The user's resource after.

        Raises:
            ScimError: 404, or 400 and 409 as for a change.
        """
        user = self._require_user(user_id)
        fields = user_fields(doc)
        if not fields.user_name:
            raise ScimError(400, "userName is required", "invalidValue")
        return self._change(user, fields, clears=("externalId",) if not fields.external_id else ())

    def patch_user(self, user_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """
        Apply the operations of a PATCH. An attribute this server does not model is ignored,
        as providers send many (the enterprise extension, phone numbers) that mean nothing here.

        Args:
            user_id: The id a provider used.
            body: The provider's PatchOp.

        Returns:
            The user's resource after.

        Raises:
            ScimError: 404, 400 and 409 as for a change.
        """
        user = self._require_user(user_id)
        values: dict[str, Any] = {}
        clears: list[str] = []
        for op, path, value in patch_operations(body):
            if not path and isinstance(value, dict):
                values.update(value)
                continue
            key = path.split(":")[-1] if path.lower().startswith("urn:") else path
            lowered = key.lower()
            if op == "remove":
                if lowered in ("externalid", "displayname"):
                    clears.append("externalId" if lowered == "externalid" else "displayName")
                elif lowered in ("name", "name.formatted", "name.givenname", "name.familyname"):
                    clears.append("displayName")
                continue
            plain = {
                "active": "active",
                "username": "userName",
                "externalid": "externalId",
                "displayname": "displayName",
            }
            if lowered in plain:
                values[plain[lowered]] = value
            elif lowered in ("name.formatted", "name.givenname", "name.familyname"):
                part = {
                    "formatted": "formatted",
                    "givenname": "givenName",
                    "familyname": "familyName",
                }
                names = values.setdefault("name", {})
                if isinstance(names, dict):
                    names[part[lowered.split(".", 1)[1]]] = value
            elif _EMAIL_PATH.fullmatch(key):
                values["emails"] = (
                    [{"value": value, "primary": True}] if isinstance(value, str) else value
                )
        fields = user_fields(values)
        return self._change(user, fields, clears=tuple(clears))

    def _change(
        self, user: ScimUser, fields: UserFields, *, clears: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        """
        Apply what a provider said about a user.

        An e-mail that differs from the account's is not applied: the reference separation of
        duties judges by is the one the account was provisioned with.

        Args:
            user: The roster entry.
            fields: What the provider said.
            clears: Attributes the provider removed.

        Returns:
            The user's resource after.

        Raises:
            ScimError: 409 ``uniqueness`` for a userName taken; 400 for a value refused.
        """
        account = self._account(user)
        updates: dict[str, Any] = {}
        if fields.user_name and fields.user_name != user.user_name:
            other = self.data.find_user(self.source.name, user_name=fields.user_name)
            if other is not None and other.id != user.id:
                raise ScimError(409, "A user with this userName exists", "uniqueness")
            updates["user_name"] = fields.user_name
        if fields.external_id != user.external_id and (
            fields.external_id is not None or "externalId" in clears
        ):
            updates["external_id"] = fields.external_id
        if updates:
            try:
                self.data.update_user(user.id, **updates)
            except sqlite3.IntegrityError as exc:
                raise ScimError(409, "A user with this userName exists", "uniqueness") from exc
        if fields.display_name is not None or "displayName" in clears:
            wanted = fields.display_name or ""
            if wanted != account.display_name:
                try:
                    account = self.accounts.update_profile(
                        account.username, display_name=clean_display_name(wanted)
                    )
                except ValidationError as exc:
                    raise ScimError(400, exc.message, "invalidValue") from exc
        if fields.email and fields.email != account.person_ref:
            if account.person_ref:
                self._audit(
                    "auth.scim.update",
                    account.username,
                    "the provider changed the e-mail; the account's reference is not changed by it",
                )
            else:
                account = self._give_email(account, fields.email)
        refreshed = self.data.get_user(self.source.name, user.id) or user
        if fields.active is False and account.status != STATUS_DISABLED:
            self._deactivate(refreshed, account)
        elif fields.active is True and account.status == STATUS_DISABLED:
            self._reactivate(refreshed, account)
        self.data.update_user(user.id)
        final = self.data.get_user(self.source.name, user.id)
        return self.render_user(final or refreshed)

    def _give_email(self, account: Account, email: str) -> Account:
        """
        Give an account its first e-mail: the one it was provisioned without.

        Once an account has one it is not changed from here; this is the single time a provider
        sets it, with the checks creating a person would have had.

        Args:
            account: An account of this source's roster that has no e-mail.
            email: The address the provider sent.

        Returns:
            The account after.

        Raises:
            ScimError: 400 for a domain not allowed, 409 ``uniqueness`` when another account has
                the address.
        """
        self._check_domain(email)
        if self.accounts.find_by_person(email):
            raise ScimError(409, "Another account already has this e-mail", "uniqueness")
        try:
            account = self.accounts.update_profile(account.username, person_ref=email)
        except (AccountError, ValidationError) as exc:
            raise ScimError(409, exc.message, "uniqueness") from exc
        self._audit(
            "auth.scim.update", account.username, "the provider gave the account its e-mail"
        )
        return account

    def _deactivate(self, user: ScimUser, account: Account) -> None:
        """
        Switch an account off because the provider deactivated the person.

        Args:
            user: The roster entry.
            account: Its account.
        """
        self.accounts.disable(account.username, f"{_DISABLED_BY}{self.source.name}")
        self.data.update_user(user.id, disabled_by_scim=True)
        self.ended.append(account.id)
        self._audit("auth.scim.deprovision", account.username, "disabled by the provider")

    def _reactivate(self, user: ScimUser, account: Account) -> None:
        """
        Switch an account back on, only if it was this provider that switched it off.

        Args:
            user: The roster entry.
            account: Its account.
        """
        if not user.disabled_by_scim:
            self._audit(
                "auth.scim.update",
                account.username,
                "the provider asked to activate an account an administrator disabled; left disabled",
            )
            return
        try:
            self.accounts.enable(account.username)
        except AccountError as exc:
            raise ScimError(409, exc.message, "mutability") from exc
        self.data.update_user(user.id, disabled_by_scim=False)
        self._audit("auth.scim.provision", account.username, "enabled again by the provider")

    def delete_user(self, user_id: str) -> None:
        """
        The provider no longer knows the person: switch the account off and forget the entry.

        The account is kept, disabled: the audit trail keeps naming somebody, and an
        administrator removes it (``noust user remove``) when its retention is over.

        Args:
            user_id: The id a provider used.

        Raises:
            ScimError: 404 when this source has no such user.
        """
        user = self._require_user(user_id)
        account = self._account(user)
        if account.status != STATUS_DISABLED:
            self._deactivate(user, account)
        self.data.delete_user(user.id)
        self._audit("auth.scim.deprovision", account.username, "removed from the roster")

    # ----------------------------------------------------------------- groups

    def list_groups(
        self, filter_text: str | None, start_index: int | None, count: int | None
    ) -> dict[str, Any]:
        """
        Args:
            filter_text: ``displayName eq "..."``, ``externalId eq "..."`` or ``id eq "..."``.
            start_index: ``startIndex``.
            count: ``count``.

        Returns:
            A ListResponse of groups.

        Raises:
            ScimError: 400 ``invalidFilter`` for any other filter.
        """
        parsed = parse_filter(filter_text)
        start, offset, limit = self.window(start_index, count)
        display_name: str | None = None
        external_id: str | None = None
        if parsed is not None:
            attribute, value = parsed
            if attribute == "id":
                found = self.data.get_group(self.source.name, value)
                resources = [self.render_group(found)] if found is not None and limit else []
                return self.listing(resources, 1 if found is not None else 0, start)
            if attribute == "displayname":
                display_name = value
            elif attribute == "externalid":
                external_id = value
            else:
                raise ScimError(400, f"Cannot filter groups by {attribute}", "invalidFilter")
        total, groups = self.data.list_groups(
            self.source.name,
            display_name=display_name,
            external_id=external_id,
            offset=offset,
            limit=limit,
        )
        return self.listing([self.render_group(group) for group in groups], total, start)

    def get_group(self, group_id: str) -> dict[str, Any]:
        """
        Args:
            group_id: The id a provider used.

        Returns:
            The group's resource.

        Raises:
            ScimError: 404 when this source has no such group.
        """
        return self.render_group(self._require_group(group_id))

    def _members_of(self, value: Any) -> list[str]:
        """
        Args:
            value: A ``members`` list: ``[{"value": id}]``.

        Returns:
            The roster ids it names.

        Raises:
            ScimError: 400 ``invalidValue`` for a member this source did not provision.
        """
        if value is None:
            return []
        if isinstance(value, dict):
            value = [value]
        if not isinstance(value, list) or len(value) > MAX_MEMBERS:
            raise ScimError(400, "members is a list of at most 5000 users", "invalidValue")
        found: list[str] = []
        for entry in value:
            member = str(entry.get("value") or "") if isinstance(entry, dict) else ""
            if not member or self.data.get_user(self.source.name, member) is None:
                raise ScimError(400, f"Unknown member: {member[:64]!r}", "invalidValue")
            if member not in found:
                found.append(member)
        return found

    def create_group(self, doc: Mapping[str, Any]) -> dict[str, Any]:
        """
        Args:
            doc: The provider's group document.

        Returns:
            The group's resource.

        Raises:
            ScimError: 400 for a missing name or an unknown member; 409 for a name taken.
        """
        display = _text(doc.get("displayName"), "displayName", required=True) or ""
        external = _text(doc.get("externalId"), "externalId")
        members = self._members_of(doc.get("members"))
        now = self._clock()
        group = ScimGroup(
            id=uuid.uuid4().hex,
            source=self.source.name,
            display_name=display,
            external_id=external,
            created_at=now,
            updated_at=now,
        )
        try:
            self.data.add_group(group)
        except sqlite3.IntegrityError as exc:
            raise ScimError(409, "A group with this displayName exists", "uniqueness") from exc
        self.data.set_members(group.id, members)
        self._resync(members, f"joined group {display}")
        return self.render_group(group)

    def replace_group(self, group_id: str, doc: Mapping[str, Any]) -> dict[str, Any]:
        """
        Args:
            group_id: The id a provider used.
            doc: The provider's whole group document.

        Returns:
            The group's resource after.

        Raises:
            ScimError: 404, 400 and 409 as for a creation.
        """
        group = self._require_group(group_id)
        display = _text(doc.get("displayName"), "displayName", required=True) or ""
        members = self._members_of(doc.get("members"))
        return self._store_group(
            group, display, _text(doc.get("externalId"), "externalId"), members
        )

    def patch_group(self, group_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """
        Apply the operations of a PATCH: members added, removed or replaced, a new name.

        Args:
            group_id: The id a provider used.
            body: The provider's PatchOp.

        Returns:
            The group's resource after.

        Raises:
            ScimError: 404, 400 and 409.
        """
        group = self._require_group(group_id)
        display = group.display_name
        external = group.external_id
        members = self.data.members(group.id)
        for op, path, value in patch_operations(body):
            lowered = path.lower()
            if not path and isinstance(value, dict):
                display = _text(value.get("displayName"), "displayName") or display
                if "members" in value:
                    members = self._members_of(value["members"])
                continue
            filtered = _MEMBER_FILTER.fullmatch(path)
            if filtered is not None and op == "remove":
                gone = re.sub(r"\\(.)", r"\1", filtered.group(1))
                members = [m for m in members if m != gone]
            elif lowered == "members":
                named = self._members_of(value) if op != "remove" or value else []
                if op == "add":
                    members = list(dict.fromkeys([*members, *named]))
                elif op == "replace":
                    members = named
                else:
                    members = [m for m in members if m not in named] if named else []
            elif lowered == "displayname" and op != "remove":
                display = _text(value, "displayName", required=True) or display
            elif lowered == "externalid":
                external = None if op == "remove" else _text(value, "externalId")
        return self._store_group(group, display, external, members)

    def _store_group(
        self, group: ScimGroup, display: str, external: str | None, members: list[str]
    ) -> dict[str, Any]:
        """
        Write a group's name and members, and apply the difference to the people it touches.

        Args:
            group: The group before.
            display: Its name after.
            external: Its provider id after.
            members: Who is in it after.

        Returns:
            The group's resource after.

        Raises:
            ScimError: 409 when the name is taken by another group.
        """
        before = self.data.members(group.id)
        try:
            self.data.update_group(group.id, display_name=display, external_id=external)
        except sqlite3.IntegrityError as exc:
            raise ScimError(409, "A group with this displayName exists", "uniqueness") from exc
        self.data.set_members(group.id, members)
        touched = list(dict.fromkeys([*before, *members]))
        renamed = display != group.display_name
        changed = set(before) ^ set(members)
        self._resync(
            touched if renamed else [m for m in touched if m in changed], f"group {display}"
        )
        return self.render_group(self.data.get_group(self.source.name, group.id) or group)

    def delete_group(self, group_id: str) -> None:
        """
        Args:
            group_id: The id a provider used.

        Raises:
            ScimError: 404 when this source has no such group.
        """
        group = self._require_group(group_id)
        members = self.data.members(group.id)
        self.data.delete_group(group.id)
        self._resync(members, f"left group {group.display_name}")

    def _resync(self, user_ids: list[str], why: str) -> None:
        """
        Recompute the roles, memberships and scoped roles of people whose groups changed.

        A role the groups cannot give (separation of duties) is a note in the trail, not a
        failure of the provider's call: its push must not stall on a decision only an
        administrator can make.

        Args:
            user_ids: The roster ids to recompute.
            why: For the record.
        """
        if not self.source.group_sync:
            return
        actor = self.actor()
        for user_id in user_ids:
            user = self.data.get_user(self.source.name, user_id)
            if user is None:
                continue
            account = self.accounts.get(user.account_id)
            if account is None or account.status == STATUS_DISABLED:
                continue
            groups = {group.display_name for group in self.data.groups_of(user.id)}
            notes: list[str] = []
            try:
                mapped = self.admission.sync_groups(
                    self.source, groups, account, user.mapped_role, actor, notes
                )
            except OidcRefused as exc:
                notes.append(f"not applied: {exc.message}")
                mapped = False
            if mapped is not False and mapped != user.mapped_role:
                self.data.update_user(user.id, mapped_role=mapped)
            if notes:
                self._audit("auth.scim.update", account.username, f"{why}: {'; '.join(notes)}")


class ScimManager:
    """
    Configure the sources that provision accounts, and authenticate their tokens.

    Args:
        store: The store; the process-wide one by default.
        accounts: The account manager; one over the same store by default.
        scim_store: The persistence; one over the same store by default.
        clock: The time source, replaceable in tests.
    """

    def __init__(
        self,
        store: NoustStore | None = None,
        *,
        accounts: AccountManager | None = None,
        scim_store: ScimStore | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._clock = clock or time.time
        self.accounts = accounts or AccountManager(store, clock=clock)
        self.data = scim_store or ScimStore(store, clock=clock)
        self.admission = Admission(self.accounts, OidcStore(store, clock=clock), self._clock)

    def now(self) -> float:
        """
        Returns:
            The current time, UNIX seconds.
        """
        return self._clock()

    def sources(self) -> list[ScimSource]:
        """
        Returns:
            Every configured source.
        """
        return self.data.list_sources()

    def source(self, name: str) -> ScimSource | None:
        """
        Args:
            name: A source's name.

        Returns:
            The source, or None.
        """
        return self.data.get_source(name)

    def require_source(self, name: str) -> ScimSource:
        """
        Args:
            name: A source's name.

        Returns:
            The source.

        Raises:
            ValidationError: When there is none.
        """
        found = self.data.get_source(name)
        if found is None:
            raise ValidationError(
                f"No SCIM source named {name!r}",
                details="List them with 'noust auth scim show'.",
                field="name",
            )
        return found

    def configure(
        self,
        name: str,
        changes: Mapping[str, Any],
        *,
        rotate_token: bool = False,
        actor: str | None = None,
    ) -> tuple[ScimSource, str | None]:
        """
        Add a source, or change one: only the settings named are touched.

        Args:
            name: The source's name.
            changes: Setting to value, from :data:`SETTINGS`.
            rotate_token: Issue a new token (a new source always gets one).
            actor: Who is making the change, for the record.

        Returns:
            The source as it is now and its new token, shown once, or None when it kept its token.

        Raises:
            ValidationError: When a setting is refused or names something that does not exist;
                nothing is written then.
        """
        key = validate_name(name)
        unknown = sorted(set(changes) - set(SETTINGS))
        if unknown:
            raise ValidationError(
                f"Unknown setting: {', '.join(unknown)}",
                details=f"The settings are: {', '.join(SETTINGS)}.",
            )
        existing = self.data.get_source(key)
        now = self.now()
        token: str | None = None
        base = existing
        if base is None or rotate_token:
            token = new_token()
        if base is None:
            base = ScimSource(
                name=key,
                label=key,
                token_hash=hash_token(token or ""),
                token_hint=(token or "")[-4:],
                token_created_at=now,
                created_at=now,
                created_by=actor,
            )
        elif token is not None:
            base = replace(
                base, token_hash=hash_token(token), token_hint=token[-4:], token_created_at=now
            )
        updated = base
        for setting, value in changes.items():
            updated = apply_admission_setting(updated, setting, value)
        updated = settle(updated, self.accounts)
        updated = replace(updated, updated_at=now, updated_by=actor)
        check_references(updated, self.accounts)
        self.data.save_source(updated)
        return updated, token

    def remove(self, name: str) -> ScimSource:
        """
        Remove a source with its roster. Its accounts stay, as they are.

        Args:
            name: The source's name.

        Returns:
            The source as it was.

        Raises:
            ValidationError: When there is none.
        """
        found = self.require_source(name)
        self.data.delete_source(found.name)
        return found

    def authenticate(self, token: str | None) -> ScimSource | None:
        """
        Find the source a bearer token belongs to.

        Args:
            token: The credential as presented.

        Returns:
            The source, or None when the token is unknown or the source is switched off. The two
            are told apart by the caller only in the audit trail, never to the provider.
        """
        if not token or not token.startswith(TOKEN_PREFIX) or len(token) > 256:
            return None
        found = self.data.source_by_token(hash_token(token))
        return found if found is not None and found.enabled else None

    def service(self, source: ScimSource, *, base_url: str, client_ip: str) -> ScimService:
        """
        Args:
            source: The authenticated source.
            base_url: Where the SCIM API is.
            client_ip: Where the request came from.

        Returns:
            The service its calls are answered by.
        """
        return ScimService(
            source,
            self.accounts,
            self.data,
            self.admission,
            base_url=base_url,
            client_ip=client_ip,
            clock=self._clock,
        )
