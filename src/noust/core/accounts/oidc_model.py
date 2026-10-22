# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an identity provider is to Noust, and what a sign-in through one says.

Pure data and validation, with no store and no network behind it, so the CLI,
the API and the manager agree on one definition (the same shape as
:mod:`noust.core.accounts.model`). A provider is what the operator configured:
its issuer, the client it registered, and the decisions that are Noust's own,
not the provider's - who may sign in, what an account that signs in for the
first time becomes, and which of its groups mean which role.

Everything the provider says is a *claim*; :class:`IdentityClaims` is the part of
it Noust reads, after the token was verified and before anybody decided what it
entitles a person to.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from noust.core.access.model import ROLE_NAME_PATTERN
from noust.core.accounts.model import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER
from noust.core.exceptions import SecurityError, ValidationError
from noust.core.teams.model import (
    TEAM_ROLE_ADMIN,
    TEAM_ROLE_OPERATOR,
    TEAM_ROLE_VIEWER,
    validate_slug,
    validate_team_role,
)

#: What a first sign-in may do. ``off``: only an account that exists. ``invite``:
#: the account an administrator invited for that e-mail. ``auto``: create one.
PROVISIONING_OFF = "off"
PROVISIONING_INVITE = "invite"
PROVISIONING_AUTO = "auto"
PROVISIONING: tuple[str, ...] = (PROVISIONING_OFF, PROVISIONING_INVITE, PROVISIONING_AUTO)

#: The roles a provider may hand out by itself. ``security`` and ``auditor`` are
#: separation-of-duties roles: a person gives them, never a group claim.
PROVIDER_ROLES: tuple[str, ...] = (ROLE_VIEWER, ROLE_OPERATOR, ROLE_ADMIN)

#: The one identity protocol of 4.0. The column exists so SAML is a row too.
PROTOCOL_OIDC = "oidc"

#: What a provider speaks. ``oidc`` has a discovery document and signs an ID token;
#: ``github`` is OAuth 2.0 without OpenID Connect (fixed endpoints, and the person is read
#: from the provider's API with the token the code buys). Both are linked under
#: :data:`PROTOCOL_OIDC`: a person is ``(issuer, subject)`` either way.
KIND_OIDC = "oidc"
KIND_GITHUB = "github"
KINDS: tuple[str, ...] = (KIND_OIDC, KIND_GITHUB)

#: Always used with fullmatch: ``$`` also matches before a trailing newline.
NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
_SCOPE_TOKEN = re.compile(r"[\x21\x23-\x5b\x5d-\x7e]{1,64}")
_DOMAIN = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
_CLAIM_SEGMENT = re.compile(r"[A-Za-z0-9_:@./-]{1,64}")

DEFAULT_SCOPES = "openid email profile"
DEFAULT_CLAIM = "groups"
MAX_LABEL = 40
MAX_CLIENT_ID = 256
MAX_MAPPINGS = 100
MAX_LIST = 50
MAX_GROUP = 256
MAX_SKEW_SECONDS = 300
DEFAULT_SKEW_SECONDS = 60
CALLBACK_PATHS = ("/api/auth/oidc/callback", "/api/v1/auth/oidc/callback")

_LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})


class OidcError(SecurityError):
    """
    A single sign-in step failed.

    Attributes:
        reason: Why, for the audit log. Whoever started the sign-in is told one
            of a few codes (:attr:`public`), never the reason itself.
        public: The code the console shows: ``unavailable``, ``expired``,
            ``denied``, ``failed`` or ``refused``.
        who: ``<provider>:<subject>`` of the person it concerns, once the token
            was verified, for the audit log.
    """

    who: str | None = None

    def __init__(
        self, reason: str, message: str, *, details: str = "", public: str = "failed"
    ) -> None:
        """
        Args:
            reason: The machine-readable reason, for the audit log.
            message: One sentence saying what happened.
            details: How to fix it, when the operator can.
            public: The code the sign-in page is told.
        """
        super().__init__(message, details)
        self.reason = reason
        self.public = public


class OidcRefused(OidcError):
    """The provider vouched for the person, and Noust's own policy says no."""

    def __init__(self, reason: str, message: str, *, details: str = "") -> None:
        """
        Args:
            reason: The machine-readable reason, for the audit log.
            message: One sentence saying why.
            details: How an administrator lets them in, when they should be.
        """
        super().__init__(reason, message, details=details, public="refused")


def is_loopback_host(host: str) -> bool:
    """
    Args:
        host: A URL's host part.

    Returns:
        Whether it is this machine, the one place plain HTTP is accepted.
    """
    bare = host.strip("[]").lower()
    if bare in _LOOPBACK_NAMES or bare.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(bare).is_loopback
    except ValueError:
        return False


def check_endpoint_url(value: str, what: str, *, field_name: str | None = None) -> str:
    """
    Accept an address Noust will send a request to, or say why not.

    HTTPS only, except for this machine (a provider on loopback, for
    development and tests). No credentials in the address, no fragment, and no
    address that is never a provider's: link-local (cloud metadata), unspecified
    or multicast.

    Args:
        value: The URL.
        what: What it is, for the message.
        field_name: The request field it came from.

    Returns:
        The URL, trimmed.

    Raises:
        ValidationError: When it is not acceptable.
    """
    text = (value or "").strip()
    try:
        parts = urlsplit(text)
        host = parts.hostname or ""
        parts.port  # noqa: B018 - raises ValueError for a port that is not a number
    except ValueError as exc:
        raise ValidationError(
            f"The {what} is not a URL", details=str(exc), field=field_name
        ) from exc
    if (
        len(text) > 512
        or not host
        or parts.scheme not in ("https", "http")
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
        or any(ord(char) < 33 or ord(char) == 127 for char in text)
    ):
        raise ValidationError(
            f"The {what} is not an acceptable URL: {text[:80]!r}",
            details="Use an absolute https:// address with no credentials and no fragment.",
            field=field_name,
        )
    if parts.scheme == "http" and not is_loopback_host(host):
        raise ValidationError(
            f"The {what} must use https",
            details="Sign-in documents and the client secret never travel in clear text.",
            field=field_name,
        )
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return text
    if (
        address.is_link_local
        or address.is_unspecified
        or address.is_multicast
        or address.is_reserved
    ):
        raise ValidationError(
            f"The {what} points at an address no provider uses: {host}",
            details="Link-local, unspecified and multicast addresses are refused.",
            field=field_name,
        )
    return text


def _string_list(value: Any, what: str, *, pattern: re.Pattern[str] | None = None) -> list[str]:
    """
    Args:
        value: A list of strings, or a comma separated string.
        what: What it is, for the message.
        pattern: What each item must match completely, when it has a shape.

    Returns:
        The items, trimmed, lower-cased when ``pattern`` is a domain, without
        duplicates and in order.

    Raises:
        ValidationError: When it is not a list of short printable strings.
    """
    if value is None:
        return []
    items = value.split(",") if isinstance(value, str) else value
    if not isinstance(items, (list, tuple)):
        raise ValidationError(f"{what} must be a list", field=what)
    found: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        if pattern is not None:
            text = text.lower()
            if not pattern.fullmatch(text):
                raise ValidationError(f"Invalid entry in {what}: {text[:80]!r}", field=what)
        if len(text) > MAX_GROUP or any(ord(char) < 32 or ord(char) == 127 for char in text):
            raise ValidationError(f"Invalid entry in {what}", field=what)
        if text not in found:
            found.append(text)
    if len(found) > MAX_LIST:
        raise ValidationError(f"{what} lists too many entries (at most {MAX_LIST})", field=what)
    return found


@dataclass(frozen=True)
class GroupMapping:
    """
    One line of the group mapping: a group of the provider and what it means here.

    There are three shapes. An instance role (``role``). A team membership
    (``team`` with ``team_role``). A role at a scope (``bind`` with the ``team``,
    and a ``project`` or an ``environment`` when it is narrower): a built-in role
    on a project or an environment, or a custom role anywhere below the server.

    Attributes:
        group: The group, as the provider's claim spells it.
        role: The instance role it gives; None for the other two shapes.
        team: The team it puts the person in, or whose scope ``bind`` is held at.
        team_role: Their role in that team (a membership).
        bind: The role held at the scope the team, project and environment name:
            a built-in team role or the name of a custom role.
        project: The project slug a ``bind`` is held at.
        environment: The environment slug a ``bind`` is held at.
    """

    group: str
    role: str | None = None
    team: str | None = None
    team_role: str | None = None
    bind: str | None = None
    project: str | None = None
    environment: str | None = None

    def to_dict(self) -> dict[str, str]:
        """
        Returns:
            The mapping as it is stored and shown.
        """
        body = {"group": self.group}
        if self.role is not None:
            body["role"] = self.role
        if self.bind is not None:
            body["bind"] = self.bind
            body["team"] = self.team or ""
            if self.project:
                body["project"] = self.project
            if self.environment:
                body["environment"] = self.environment
        elif self.team is not None:
            body["team"] = self.team
            body["team_role"] = self.team_role or TEAM_ROLE_VIEWER
        return body

    def to_line(self) -> str:
        """
        Returns:
            The mapping written as one line: ``GROUP=ROLE``, ``GROUP=TEAM:ROLE`` or
            ``GROUP=ROLE@TEAM/PROJECT/ENVIRONMENT``.
        """
        if self.bind is not None:
            where = "/".join(p for p in (self.team, self.project, self.environment) if p)
            return f"{self.group}={self.bind}@{where}"
        if self.team is not None:
            return f"{self.group}={self.team}:{self.team_role or TEAM_ROLE_VIEWER}"
        return f"{self.group}={self.role}"


def mapping_from_line(text: str) -> dict[str, str]:
    """
    Read one line of the group mapping, as the command line and the console write it.

    The group is everything before the last ``=``, so a distinguished name
    (``cn=ops,ou=groups,dc=acme``) is a group like any other.

    Args:
        text: ``GROUP=ROLE`` (a role on the server), ``GROUP=TEAM:ROLE`` (a
            membership) or ``GROUP=ROLE@TEAM[/PROJECT[/ENVIRONMENT]]`` (a role held
            at a scope).

    Returns:
        The mapping in the shape :func:`parse_mappings` takes.

    Raises:
        ValidationError: When it is none of the three.
    """
    group, _, meaning = text.strip().rpartition("=")
    group, meaning = group.strip(), meaning.strip()
    if not group or not meaning:
        raise ValidationError(
            f"Not a group mapping: {text!r}",
            details=(
                "Write GROUP=ROLE, GROUP=TEAM:ROLE or GROUP=ROLE@TEAM/PROJECT, "
                "such as acme-release=release-manager@acme/web."
            ),
            field="group_mappings",
        )
    if "@" in meaning:
        role, _, where = meaning.partition("@")
        parts = where.split("/")
        if not role or not parts[0] or len(parts) > 3 or not all(parts):
            raise ValidationError(
                f"Not a role at a scope: {text!r}",
                details="Write GROUP=ROLE@TEAM, GROUP=ROLE@TEAM/PROJECT or ...@TEAM/PROJECT/ENV.",
                field="group_mappings",
            )
        found = {"group": group, "bind": role, "team": parts[0]}
        if len(parts) > 1:
            found["project"] = parts[1]
        if len(parts) > 2:
            found["environment"] = parts[2]
        return found
    if ":" in meaning:
        team, _, team_role = meaning.partition(":")
        if not team or not team_role:
            raise ValidationError(
                f"Not GROUP=TEAM:ROLE: {text!r}",
                details="Such as acme-ops=acme:operator.",
                field="group_mappings",
            )
        return {"group": group, "team": team, "team_role": team_role}
    return {"group": group, "role": meaning}


_MAPPING_KEYS = frozenset({"group", "role", "team", "team_role", "bind", "project", "environment"})

#: The built-in team roles a claim may give on a project or an environment. ``owner``
#: governs a team's members, which only a person decides.
NARROW_TEAM_ROLES: tuple[str, ...] = (TEAM_ROLE_VIEWER, TEAM_ROLE_OPERATOR, TEAM_ROLE_ADMIN)

#: Built-in roles whose names a custom role cannot take, and a claim never gives.
_PERSON_GIVEN_ROLES = frozenset({"security", "auditor", "owner"})


def _mapping_bind(group: str, entry: dict[str, Any]) -> GroupMapping:
    """
    Args:
        group: The group, already checked.
        entry: A mapping that gives a role at a scope.

    Returns:
        The mapping.

    Raises:
        ValidationError: When it leaves out the team, names a project or an
            environment out of order, or gives a role a claim may not give.
    """
    role = str(entry["bind"]).strip().lower()
    if role in _PERSON_GIVEN_ROLES:
        raise ValidationError(
            f"A group cannot give the role {role!r}",
            details="Those roles are given by a person, so that no claim can grant itself power.",
            field="group_mappings",
        )
    if not (role in NARROW_TEAM_ROLES or ROLE_NAME_PATTERN.fullmatch(role)):
        raise ValidationError(
            f"Invalid role in the mapping of {group!r}: {role[:40]!r}",
            details="A built-in team role (viewer, operator, admin) or a custom role's name.",
            field="group_mappings",
        )
    team = str(entry.get("team") or "").strip()
    project = str(entry.get("project") or "").strip()
    environment = str(entry.get("environment") or "").strip()
    if not team or (environment and not project):
        raise ValidationError(
            f"The mapping of {group!r} names a role at a scope without saying where",
            details=(
                "Name the team, and the project when the role is narrower; an environment "
                "needs its project."
            ),
            field="group_mappings",
        )
    if role in NARROW_TEAM_ROLES and not project:
        raise ValidationError(
            f"{role!r} on a whole team is a membership, not a binding",
            details='Use {"group": ..., "team": ..., "team_role": ...}, or name a project.',
            field="group_mappings",
        )
    return GroupMapping(
        group,
        team=validate_slug(team, what="team"),
        bind=role,
        project=validate_slug(project, what="project") if project else None,
        environment=validate_slug(environment, what="environment") if environment else None,
    )


def parse_mappings(value: Any) -> list[GroupMapping]:
    """
    Check the group mapping an operator wrote.

    Args:
        value: A list of ``{"group", "role"}``, ``{"group", "team", "team_role"}``
            or ``{"group", "bind", "team", "project"?, "environment"?}``.

    Returns:
        The mappings, in the order written (the first role mapping that
        matches decides the role).

    Raises:
        ValidationError: When a line is not one of the three shapes, names a role
            a provider may not hand out, or a team that is not a slug.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError("group_mappings must be a list", field="group_mappings")
    if len(value) > MAX_MAPPINGS:
        raise ValidationError(f"At most {MAX_MAPPINGS} group mappings", field="group_mappings")
    parsed: list[GroupMapping] = []
    for entry in value:
        if not isinstance(entry, dict):
            raise ValidationError("A group mapping is an object", field="group_mappings")
        unknown = set(entry) - _MAPPING_KEYS
        group = str(entry.get("group") or "").strip()
        if unknown or not group or len(group) > MAX_GROUP:
            raise ValidationError(
                "A group mapping needs a group, and then a role, a team or a role at a scope",
                details=(
                    'Such as {"group": "noust-admins", "role": "admin"}, '
                    '{"group": "acme-ops", "team": "acme", "team_role": "operator"} or '
                    '{"group": "acme-release", "bind": "release-manager", "team": "acme", '
                    '"project": "web"}.'
                ),
                field="group_mappings",
            )
        has_role = entry.get("role") not in (None, "")
        has_bind = entry.get("bind") not in (None, "")
        has_team = entry.get("team") not in (None, "") and not has_bind
        if (has_role + has_bind + has_team) != 1 or (has_role and entry.get("team_role")):
            raise ValidationError(
                f"The mapping of {group!r} gives a role, a team or a role at a scope: one of them",
                field="group_mappings",
            )
        if has_bind and entry.get("team_role"):
            raise ValidationError(
                f"The mapping of {group!r} gives a role at a scope; team_role is for a membership",
                field="group_mappings",
            )
        if has_role:
            role = str(entry["role"]).strip().lower()
            if role not in PROVIDER_ROLES:
                raise ValidationError(
                    f"A group cannot give the role {role!r}",
                    details=(
                        f"Groups give {', '.join(PROVIDER_ROLES)}. Security and auditor roles are "
                        "given by a person, so that no claim can make someone their own auditor."
                    ),
                    field="group_mappings",
                )
            parsed.append(GroupMapping(group, role=role))
        elif has_bind:
            parsed.append(_mapping_bind(group, entry))
        else:
            parsed.append(
                GroupMapping(
                    group,
                    team=validate_slug(str(entry["team"]), what="team"),
                    team_role=validate_team_role(str(entry.get("team_role") or TEAM_ROLE_VIEWER)),
                )
            )
    return parsed


def claim_path(value: str | None) -> str:
    """
    Args:
        value: The claim that holds groups, such as ``groups`` or ``realm_access.roles``.

    Returns:
        The path, validated; ``groups`` when empty.

    Raises:
        ValidationError: When a segment is not a claim name.
    """
    text = (value or "").strip() or DEFAULT_CLAIM
    segments = text.split(">")
    if len(text) > 128 or not all(_CLAIM_SEGMENT.fullmatch(segment) for segment in segments):
        raise ValidationError(
            f"Invalid groups claim: {text[:80]!r}",
            details="Name the claim, such as groups. Nest with '>' (realm_access>roles).",
            field="groups_claim",
        )
    return text


@dataclass(frozen=True)
class OidcProvider:
    """
    One configured identity provider, without its client secret.

    Attributes:
        name: Its short name, in the sign-in address and the audit log.
        label: What the button says after "Sign in with".
        issuer: The provider's issuer URL, exactly as the tokens write it.
        client_id: The client Noust is registered as.
        scopes: Space separated; always includes ``openid``.
        enabled: Whether the sign-in page offers it.
        require_email_verified: Refuse a sign-in whose e-mail the provider does
            not vouch for.
        assume_email_verified: Read a token that says nothing about whether the
            e-mail is verified as if it did (a directory the organisation
            controls, such as Microsoft Entra, which never says). A provider that
            says it is *not* verified is never assumed otherwise.
        clock_skew_seconds: How far the provider's clock may differ from ours.
        provisioning: One of :data:`PROVISIONING`.
        default_role: The role an account created by a first sign-in gets.
        default_team: The team it joins instead of the default one.
        default_team_role: Its role there.
        link_by_email: Whether a first sign-in links to the one account whose
            person has that verified e-mail (off by default).
        allowed_domains: When not empty, only e-mail addresses of these domains.
        require_amr: When not empty, the token must say the provider used one of
            these methods (``mfa``, ``hwk``...): a stricter gate, never a way to
            skip Noust's own second factor.
        groups_claim: Where the token lists the person's groups.
        group_sync: Whether the groups decide roles and team memberships.
        group_mappings: What each group means.
        redirect_uri: The callback address to give the provider, when it cannot
            be worked out from the console's own address.
        kind: One of :data:`KINDS`.
        created_at: When it was configured, UNIX seconds.
        created_by: Who configured it.
        updated_at: Last change, UNIX seconds.
        updated_by: Who made it.
    """

    name: str
    label: str
    issuer: str
    client_id: str
    scopes: str = DEFAULT_SCOPES
    enabled: bool = True
    require_email_verified: bool = True
    assume_email_verified: bool = False
    clock_skew_seconds: int = DEFAULT_SKEW_SECONDS
    provisioning: str = PROVISIONING_OFF
    default_role: str = ROLE_VIEWER
    default_team: str | None = None
    default_team_role: str | None = None
    link_by_email: bool = False
    allowed_domains: tuple[str, ...] = ()
    require_amr: tuple[str, ...] = ()
    groups_claim: str = DEFAULT_CLAIM
    group_sync: bool = False
    group_mappings: tuple[GroupMapping, ...] = ()
    redirect_uri: str | None = None
    kind: str = KIND_OIDC
    created_at: float = 0.0
    created_by: str | None = None
    updated_at: float = 0.0
    updated_by: str | None = None

    @property
    def scope_list(self) -> list[str]:
        """The scopes, one by one."""
        return self.scopes.split()

    def vouched_email(self, person: IdentityClaims) -> str | None:
        """
        The e-mail of a person, when this provider's word for it is good enough.

        Every decision that rests on an address (the domain allow-list, finding
        an account, activating an invitation, naming a new account's person)
        uses this and nothing else.

        Args:
            person: Who a verified token says it is.

        Returns:
            The address when the provider says it is verified, or says nothing
            and is assumed to be authoritative; None otherwise.
        """
        return vouch_email(person, assume=self.assume_email_verified)

    @property
    def protocol(self) -> str:
        """The protocol its people are linked under."""
        return PROTOCOL_OIDC

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the provider for the API and ``--json``; the secret is never here.

        Returns:
            Every setting, with the mappings as lists of objects.
        """
        return {
            "name": self.name,
            "label": self.label,
            "issuer": self.issuer,
            "client_id": self.client_id,
            "scopes": self.scopes,
            "enabled": self.enabled,
            "require_email_verified": self.require_email_verified,
            "assume_email_verified": self.assume_email_verified,
            "clock_skew_seconds": self.clock_skew_seconds,
            "provisioning": self.provisioning,
            "default_role": self.default_role,
            "default_team": self.default_team,
            "default_team_role": self.default_team_role,
            "link_by_email": self.link_by_email,
            "allowed_domains": list(self.allowed_domains),
            "require_amr": list(self.require_amr),
            "groups_claim": self.groups_claim,
            "group_sync": self.group_sync,
            "group_mappings": [mapping.to_dict() for mapping in self.group_mappings],
            "redirect_uri": self.redirect_uri,
            "kind": self.kind,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


def validate_name(value: str) -> str:
    """
    Args:
        value: The provider's name as typed.

    Returns:
        The name, trimmed and lower-cased.

    Raises:
        ValidationError: When it is not 1 to 32 lower-case letters, digits or dashes.
    """
    name = (value or "").strip().lower()
    if not NAME_PATTERN.fullmatch(name):
        raise ValidationError(
            f"Invalid provider name: {value!r}",
            details="Use 1 to 32 lower-case letters, digits or '-', such as 'corp' or 'google'.",
            field="name",
        )
    return name


def validate_label(value: str | None, fallback: str) -> str:
    """
    Args:
        value: The button label as typed, or None.
        fallback: What to use when it is empty.

    Returns:
        The label, trimmed.

    Raises:
        ValidationError: When it is too long or has control characters.
    """
    text = (value or "").strip() or fallback
    if len(text) > MAX_LABEL or any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ValidationError(
            "Invalid button label",
            details=f"Use at most {MAX_LABEL} printable characters, such as 'Acme SSO'.",
            field="label",
        )
    return text


def validate_scopes(value: str | list[str] | None) -> str:
    """
    Args:
        value: The scopes, as a string or a list.

    Returns:
        Space separated, ``openid`` first and every scope once.

    Raises:
        ValidationError: When a scope is not a scope token, or there are too many.
    """
    raw = value.split() if isinstance(value, str) else list(value or [])
    scopes = ["openid"]
    for scope in raw or DEFAULT_SCOPES.split():
        text = str(scope).strip()
        if not _SCOPE_TOKEN.fullmatch(text):
            raise ValidationError(f"Invalid scope: {text[:40]!r}", field="scopes")
        if text not in scopes:
            scopes.append(text)
    if len(scopes) > 20:
        raise ValidationError("At most 20 scopes", field="scopes")
    return " ".join(scopes)


def validate_kind(value: Any) -> str:
    """
    Args:
        value: The kind as typed.

    Returns:
        It, lower-cased.

    Raises:
        ValidationError: When it is not one of :data:`KINDS`.
    """
    text = str(value or "").strip().lower()
    if text not in KINDS:
        raise ValidationError(
            f"Unknown kind of provider: {value!r}",
            details=f"The kinds are: {', '.join(KINDS)}.",
            field="kind",
        )
    return text


def validate_client_id(value: str) -> str:
    """
    Args:
        value: The client id as typed.

    Returns:
        The id, trimmed.

    Raises:
        ValidationError: When it is empty, too long or has spaces or control characters.
    """
    text = (value or "").strip()
    if not text or len(text) > MAX_CLIENT_ID or any(ord(c) < 33 or ord(c) == 127 for c in text):
        raise ValidationError(
            "Invalid client id",
            details="Copy it from the provider's registration of this application.",
            field="client_id",
        )
    return text


def validate_redirect_uri(value: str | None) -> str | None:
    """
    Args:
        value: A callback address override, or None.

    Returns:
        The address, or None when there is none.

    Raises:
        ValidationError: When it is not an address of this console's callback.
    """
    text = (value or "").strip()
    if not text:
        return None
    check_endpoint_url(text, "redirect URI", field_name="redirect_uri")
    if urlsplit(text).path not in CALLBACK_PATHS or urlsplit(text).query:
        raise ValidationError(
            "The redirect URI is not this console's callback",
            details="It must end in /api/auth/oidc/callback and have no query.",
            field="redirect_uri",
        )
    return text


def validate_domains(value: Any) -> list[str]:
    """
    Args:
        value: Domains, as a list or comma separated.

    Returns:
        Lower-case domains.

    Raises:
        ValidationError: When one is not a domain.
    """
    return _string_list(value, "allowed_domains", pattern=_DOMAIN)


def validate_methods(value: Any) -> list[str]:
    """
    Args:
        value: Authentication method references (``amr``), as a list or comma separated.

    Returns:
        The methods.

    Raises:
        ValidationError: When one is not a short printable word.
    """
    return _string_list(value, "require_amr")


def validate_skew(value: Any) -> int:
    """
    Args:
        value: Seconds of clock difference to tolerate.

    Returns:
        The seconds.

    Raises:
        ValidationError: When it is not between 0 and :data:`MAX_SKEW_SECONDS`.
    """
    try:
        seconds = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("The clock skew is a number of seconds", field="clock_skew") from exc
    if not 0 <= seconds <= MAX_SKEW_SECONDS:
        raise ValidationError(
            f"The clock skew is between 0 and {MAX_SKEW_SECONDS} seconds", field="clock_skew"
        )
    return seconds


def validate_provisioning(value: str) -> str:
    """
    Args:
        value: The policy as typed.

    Returns:
        The policy.

    Raises:
        ValidationError: When it is not one of :data:`PROVISIONING`.
    """
    policy = (value or "").strip().lower()
    if policy not in PROVISIONING:
        raise ValidationError(
            f"Unknown provisioning policy: {value!r}",
            details=f"Use one of: {', '.join(PROVISIONING)}.",
            field="provisioning",
        )
    return policy


def validate_default_role(value: str) -> str:
    """
    Args:
        value: The role as typed.

    Returns:
        The role.

    Raises:
        ValidationError: When it is not one a provider may hand out.
    """
    role = (value or "").strip().lower()
    if role not in PROVIDER_ROLES:
        raise ValidationError(
            f"A new account cannot start as {role!r}",
            details=(
                f"Use one of: {', '.join(PROVIDER_ROLES)}. Security and auditor accounts are "
                "created by a person."
            ),
            field="default_role",
        )
    return role


def team_role_or_none(value: str | None) -> str | None:
    """
    Args:
        value: A team role, or empty.

    Returns:
        The role, or None.

    Raises:
        ValidationError: When it is not a team role.
    """
    if not (value or "").strip():
        return None
    return validate_team_role(value or "")


def vouch_email(person: IdentityClaims, *, assume: bool) -> str | None:
    """
    The e-mail of a person, when a source's word for it is good enough.

    The one answer behind every source (rule 3): the address a decision may rest
    on is the one the source says is verified, or the one it says nothing about
    when the operator has said that source is authoritative.

    Args:
        person: Who the source vouched for.
        assume: Whether a source that says nothing about verification is trusted.

    Returns:
        The address, or None.
    """
    if person.email is None:
        return None
    if person.email_verified is True:
        return person.email
    if person.email_verified is None and assume:
        return person.email
    return None


@dataclass(frozen=True)
class IdentityClaims:
    """
    What a verified ID token says about the person, and nothing it does not.

    Attributes:
        issuer: ``iss``, checked against the provider's.
        subject: ``sub``: the provider's stable id for the person.
        email: Their address, lower-case, when the token (or the provider's
            userinfo) has one.
        email_verified: Whether the provider vouches for it; None when it says nothing.
        name: Their display name.
        username: ``preferred_username``, a hint for a new account's name.
        groups: The groups the configured claim lists.
        amr: How the provider authenticated them (RFC 8176 values).
        acr: The authentication context class it reports.
    """

    issuer: str
    subject: str
    email: str | None = None
    email_verified: bool | None = None
    name: str | None = None
    username: str | None = None
    groups: tuple[str, ...] = field(default_factory=tuple)
    amr: tuple[str, ...] = field(default_factory=tuple)
    acr: str | None = None
