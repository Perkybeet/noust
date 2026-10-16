# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Single sign-in through an OpenID Connect provider: who may come in, and as whom.

:class:`OidcManager` is the one implementation of the rules, called by the
console's ``/api/auth/oidc`` routes and by ``noust auth oidc`` alike (rule 3).
What it decides, in order, once the provider's token was verified
(:mod:`noust.core.accounts.oidc_client`):

1. **Is the token acceptable to this server?** The e-mail is verified when
   the provider is asked to (the default), belongs to an allowed domain when
   there are any, and the provider used one of the methods required, when any
   are. No account exists yet? Nobody signs in this way: the first account is
   made by the operator, so a provider can never make the first administrator.
2. **Who is it?** By the pair the provider guarantees, ``(issuer, subject)``,
   in ``account_identities``. Never by e-mail alone: the same address can be
   reassigned by a provider to a different person.
3. **A person never seen before** is linked to an existing account by their
   verified e-mail only when the provider is explicitly trusted for that
   (``link_by_email``) and the account already holds a second factor, so a link
   cannot seize an account nobody has secured yet. Otherwise the provisioning
   policy: ``off`` (an administrator links them), ``invite`` (the account an
   administrator invited for that e-mail is activated) or ``auto`` (an account is
   created with the provider's default role, in its default team).
4. **Their groups**, when asked to, set the role and the team memberships on every
   sign-in, taking back what the provider no longer grants and leaving what an
   administrator set by hand.

**The second factor.** A provider counts as the first factor and nothing else.
Whatever the token says about how the provider authenticated the person
(``amr``, ``acr``) never replaces the account's own authenticator or passkey: it
is asserted by the provider, spelled differently by each, absent from some, and
Noust's rules - every account has a second factor, the console is published only
when they all do, sudo mode asks for one - are about factors Noust verified
itself. A provider can *require* more (``require_amr``), never less.

The sign-in is not finished here. :meth:`OidcManager.finish` ends with a ticket
the console collects (:meth:`OidcManager.collect`), and the account then does what
any account does: its second factor, or enrolling one, through
:func:`noust.web.api.auth.account_signed_in`.
"""

from __future__ import annotations

import hmac
import logging
import re
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from noust.core.accounts.local_login import refuse_to_lose_a_way_in
from noust.core.accounts.manager import AccountManager
from noust.core.accounts.model import (
    ROLE_ADMIN,
    STATUS_DISABLED,
    STATUS_INVITED,
    Account,
    AccountError,
    validate_username,
)
from noust.core.accounts.oidc_client import (
    Discovery,
    OidcClient,
    claim_at,
    get_client,
    identity_from_claims,
    new_pkce_verifier,
)
from noust.core.accounts.oidc_model import (
    PROTOCOL_OIDC,
    PROVISIONING_AUTO,
    PROVISIONING_INVITE,
    IdentityClaims,
    OidcError,
    OidcProvider,
    OidcRefused,
    check_endpoint_url,
    claim_path,
    parse_mappings,
    team_role_or_none,
    validate_client_id,
    validate_default_role,
    validate_domains,
    validate_label,
    validate_methods,
    validate_name,
    validate_provisioning,
    validate_redirect_uri,
    validate_scopes,
    validate_skew,
)
from noust.core.accounts.oidc_store import (
    FLOW_SECONDS,
    TICKET_SECONDS,
    Identity,
    OidcStore,
    PendingFlow,
    Ticket,
    digest,
)
from noust.core.audit import record as audit_record
from noust.core.audit.actor import Actor
from noust.core.exceptions import ConfigError, ValidationError
from noust.core.sealing import SecretsLockedError
from noust.core.teams.model import INSTANCE_ONLY_ROLES, TEAM_ROLES, TeamConflictError, TeamError
from noust.core.teams.repository import TeamStore

if TYPE_CHECKING:
    from noust.core.store import NoustStore

logger = logging.getLogger(__name__)

#: The settings :meth:`OidcManager.configure` accepts.
SETTINGS: tuple[str, ...] = (
    "label",
    "issuer",
    "client_id",
    "scopes",
    "enabled",
    "require_email_verified",
    "assume_email_verified",
    "clock_skew_seconds",
    "provisioning",
    "default_role",
    "default_team",
    "default_team_role",
    "link_by_email",
    "allowed_domains",
    "require_amr",
    "groups_claim",
    "group_sync",
    "group_mappings",
    "redirect_uri",
)

#: Where a person goes after signing in unless they were on their way elsewhere.
HOME = "/"
_MAX_NEXT = 512
_USERNAME_JUNK = re.compile(r"[^a-z0-9._-]+")


def safe_next(value: str | None) -> str:
    """
    Reduce "where to go afterwards" to a path of this console, or the home page.

    Whatever the sign-in page was asked to return to travels through the
    provider and back, and is then redirected to: an address on another site
    there is an open redirect.

    Args:
        value: What the sign-in was started with.

    Returns:
        A path starting with one slash, never ``//``, a backslash, a scheme or a
        control character; the machine paths (``/api``, ``/assets``...) are not
        pages and become the home page.
    """
    text = (value or "").strip()
    if (
        not text.startswith("/")
        or text.startswith(("//", "/\\"))
        or len(text) > _MAX_NEXT
        or "\\" in text
        or any(ord(char) < 32 or ord(char) == 127 for char in text)
    ):
        return HOME
    first = "/" + text[1:].split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if first in ("/api", "/assets", "/events", "/ws", "/hooks", "/health"):
        return HOME
    return text


@dataclass(frozen=True)
class Begun:
    """
    A sign-in sent on its way.

    Attributes:
        url: The provider's authorization URL, to redirect the browser to.
        binding: The secret to put in the browser's cookie, which the answer
            must come back with: it ties the answer to the browser that began.
        expires_in: Seconds the person has.
    """

    url: str
    binding: str
    expires_in: int


@dataclass(frozen=True)
class Answer:
    """
    A sign-in the provider vouched for and Noust admitted.

    Attributes:
        ticket: The single-use handle for the console to collect; the only
            time it exists in clear.
        next_path: Where the console goes once signed in.
        account: The account.
        provider: The provider.
        notes: What happened on the way, one sentence each: linked, created,
            roles and teams changed.
    """

    ticket: str
    next_path: str
    account: Account
    provider: OidcProvider
    notes: tuple[str, ...]


@dataclass(frozen=True)
class Check:
    """One line of a provider's test: what was looked at, whether it is right, and what it said."""

    name: str
    ok: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The check as the API and ``--json`` show it.
        """
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


class OidcManager:
    """
    Configure providers, run their sign-ins, and decide who they let in.

    Args:
        store: The store; the process-wide one by default.
        client: How providers are reached; the shared client by default.
        accounts: The account manager; one over the same store by default.
        oidc_store: The persistence; one over the same store by default.
        clock: The time source, replaceable in tests.
    """

    def __init__(
        self,
        store: NoustStore | None = None,
        *,
        client: OidcClient | None = None,
        accounts: AccountManager | None = None,
        oidc_store: OidcStore | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._client = client
        self._clock = clock or time.time
        self.accounts = accounts or AccountManager(store, clock=clock)
        self.data = oidc_store or OidcStore(store, clock=clock)

    @property
    def client(self) -> OidcClient:
        """How providers are reached."""
        return self._client or get_client()

    def now(self) -> float:
        """
        Returns:
            The current time, UNIX seconds.
        """
        return self._clock()

    # --------------------------------------------------------- configuration

    def providers(self) -> list[OidcProvider]:
        """
        Returns:
            Every configured provider, by name.
        """
        return self.data.list_providers()

    def provider(self, name: str) -> OidcProvider | None:
        """
        Args:
            name: The provider's name.

        Returns:
            The provider, or None.
        """
        return self.data.get_provider(name)

    def require_provider(self, name: str) -> OidcProvider:
        """
        Args:
            name: The provider's name.

        Returns:
            The provider.

        Raises:
            ConfigError: When none has that name.
        """
        found = self.data.get_provider(name)
        if found is None:
            raise ConfigError(
                f"No identity provider named {name!r}",
                details="List them with 'noust auth oidc show', add one with 'noust auth oidc set'.",
            )
        return found

    def public_providers(self) -> list[dict[str, str]]:
        """
        Returns:
            What the sign-in page may show anybody: the name and button label of
            each enabled provider, and nothing else.
        """
        return [
            {"name": found.name, "label": found.label}
            for found in self.data.list_providers()
            if found.enabled
        ]

    def has_secret(self, name: str) -> bool:
        """
        Args:
            name: The provider's name.

        Returns:
            Whether a client secret is stored for it.
        """
        return self.data.has_secret(name)

    def configure(
        self,
        name: str,
        changes: Mapping[str, Any],
        *,
        client_secret: str | None = None,
        clear_secret: bool = False,
        actor: str | None = None,
    ) -> OidcProvider:
        """
        Add a provider, or change one: only the settings named are touched.

        Args:
            name: The provider's name.
            changes: Setting to value, from :data:`SETTINGS`. A new provider
                needs ``issuer`` and ``client_id``.
            client_secret: The client secret to store, when there is one to set.
            clear_secret: Forget the stored client secret (a public client).
            actor: Who is making the change, for the record.

        Returns:
            The provider as it is now.

        Raises:
            ValidationError: When a setting is refused, or a team it names does
                not exist; nothing is written then.
        """
        key = validate_name(name)
        unknown = sorted(set(changes) - set(SETTINGS))
        if unknown:
            raise ValidationError(
                f"Unknown setting: {', '.join(unknown)}",
                details=f"The settings are: {', '.join(SETTINGS)}.",
            )
        existing = self.data.get_provider(key)
        if existing is None and not (changes.get("issuer") and changes.get("client_id")):
            raise ValidationError(
                "A new provider needs an issuer and a client id",
                details="Pass --issuer and --client-id, from the provider's registration of Noust.",
            )
        now = self.now()
        base = existing or OidcProvider(
            name=key, label=key, issuer="", client_id="", created_at=now, created_by=actor
        )
        provider = self._apply(base, changes)
        provider = replace(provider, updated_at=now, updated_by=actor)
        self._check_teams(provider)
        if existing is not None and existing.enabled and not provider.enabled:
            refuse_to_lose_a_way_in(
                self.accounts.store,
                without_provider=existing.name,
                action=f"Disabling the provider {existing.name!r}",
            )
        self.data.save_provider(provider)
        if client_secret:
            self.data.write_secret(key, client_secret)
        elif clear_secret:
            self.data.delete_secret(key)
        return provider

    def _apply(self, provider: OidcProvider, changes: Mapping[str, Any]) -> OidcProvider:
        """
        Args:
            provider: The provider before.
            changes: The settings to change.

        Returns:
            The provider after, every changed setting validated.

        Raises:
            ValidationError: When a setting, or a combination, is refused.
        """
        updated = provider
        for setting, value in changes.items():
            if setting == "label":
                updated = replace(updated, label=validate_label(value, provider.name))
            elif setting == "issuer":
                issuer = check_endpoint_url(str(value), "issuer", field_name="issuer")
                if "?" in issuer:
                    raise ValidationError("An issuer has no query", field="issuer")
                updated = replace(updated, issuer=issuer)
            elif setting == "client_id":
                updated = replace(updated, client_id=validate_client_id(str(value)))
            elif setting == "scopes":
                updated = replace(updated, scopes=validate_scopes(value))
            elif setting == "enabled":
                updated = replace(updated, enabled=bool(value))
            elif setting == "require_email_verified":
                updated = replace(updated, require_email_verified=bool(value))
            elif setting == "assume_email_verified":
                updated = replace(updated, assume_email_verified=bool(value))
            elif setting == "clock_skew_seconds":
                updated = replace(updated, clock_skew_seconds=validate_skew(value))
            elif setting == "provisioning":
                updated = replace(updated, provisioning=validate_provisioning(str(value)))
            elif setting == "default_role":
                updated = replace(updated, default_role=validate_default_role(str(value)))
            elif setting == "default_team":
                team = (str(value).strip().lower() if value else "") or None
                updated = replace(updated, default_team=team)
            elif setting == "default_team_role":
                updated = replace(updated, default_team_role=team_role_or_none(value))
            elif setting == "link_by_email":
                updated = replace(updated, link_by_email=bool(value))
            elif setting == "allowed_domains":
                updated = replace(updated, allowed_domains=tuple(validate_domains(value)))
            elif setting == "require_amr":
                updated = replace(updated, require_amr=tuple(validate_methods(value)))
            elif setting == "groups_claim":
                updated = replace(updated, groups_claim=claim_path(value))
            elif setting == "group_sync":
                updated = replace(updated, group_sync=bool(value))
            elif setting == "group_mappings":
                updated = replace(updated, group_mappings=tuple(parse_mappings(value)))
            elif setting == "redirect_uri":
                updated = replace(updated, redirect_uri=validate_redirect_uri(value))
        if updated.default_team is None:
            updated = replace(updated, default_team_role=None)
        elif updated.default_team_role is None:
            updated = replace(updated, default_team_role="viewer")
        if self.accounts.policy().requires("sso_no_admin"):
            self._refuse_admin_under_ens(updated)
        return updated

    @staticmethod
    def _refuse_admin_under_ens(provider: OidcProvider) -> None:
        """
        Args:
            provider: A provider about to be saved.

        Raises:
            ValidationError: When it would hand out ``admin`` under the ENS profile.
        """
        gives = provider.default_role == ROLE_ADMIN and provider.provisioning == PROVISIONING_AUTO
        gives = gives or any(mapping.role == ROLE_ADMIN for mapping in provider.group_mappings)
        if gives:
            raise ValidationError(
                "Under the ENS profile a provider cannot hand out the admin role",
                details=(
                    "Administrators are made by a security officer (noust user set-role), so that "
                    "granting privileged rights is a recorded decision of a person."
                ),
                field="default_role",
            )

    def _check_teams(self, provider: OidcProvider) -> None:
        """
        Args:
            provider: A provider about to be saved.

        Raises:
            ValidationError: When it names a team that does not exist.
        """
        slugs = {mapping.team for mapping in provider.group_mappings if mapping.team}
        if provider.default_team:
            slugs.add(provider.default_team)
        teams = TeamStore(self.accounts.store)
        for slug in sorted(slugs):
            try:
                teams.require_team(slug)
            except TeamError as exc:
                raise ValidationError(exc.message, details=exc.details, field="team") from exc

    def remove(self, name: str) -> OidcProvider:
        """
        Forget a provider and its client secret.

        Args:
            name: The provider's name.

        Returns:
            The provider as it was.

        Raises:
            ConfigError: When none has that name.
        """
        found = self.require_provider(name)
        refuse_to_lose_a_way_in(
            self.accounts.store,
            without_provider=found.name,
            action=f"Removing the provider {found.name!r}",
        )
        self.data.delete_provider(found.name)
        return found

    # ------------------------------------------------------------- the test

    def test(self, name: str, *, redirect_uri: str | None = None) -> list[Check]:
        """
        Look at a provider the way a sign-in would, without signing anybody in.

        Args:
            name: The provider's name.
            redirect_uri: The callback address Noust would give the provider,
                when it is known, to show what to register.

        Returns:
            One check per thing looked at; it is right when every one is ok.
            A provider that cannot be reached is a failed check, not an error.
        """
        provider = self.require_provider(name)
        checks: list[Check] = []
        try:
            discovery = self.client.discovery(provider, refresh=True)
        except OidcError as exc:
            return [Check("discovery", False, f"{exc.message}. {exc.details}".strip())]
        checks.append(Check("discovery", True, f"issuer {discovery.issuer}"))
        checks.append(Check("endpoints", True, f"authorize {discovery.authorization_endpoint}"))
        checks.append(self._test_keys(discovery))
        algorithms = ", ".join(discovery.signing_algorithms) or "not advertised"
        checks.append(Check("signing algorithms", True, algorithms))
        try:
            secret = self.data.read_secret(provider.name)
        except SecretsLockedError:
            checks.append(Check("client secret", False, "the central's secrets are locked"))
        else:
            checks.append(
                Check(
                    "client secret",
                    True,
                    "set" if secret else "none: a public client, signing in with PKCE alone",
                )
            )
        # Information, not a verdict: the provider alone knows whether it was registered.
        checks.append(
            Check(
                "redirect URI",
                True,
                f"register {redirect_uri} at the provider"
                if redirect_uri
                else "register the console's address followed by /api/auth/oidc/callback "
                "(or set web.public_url, and it is shown here)",
            )
        )
        return checks

    def _test_keys(self, discovery: Discovery) -> Check:
        """
        Args:
            discovery: The provider's discovery.

        Returns:
            Whether the key set can be read, and what is in it.
        """
        try:
            keys = self.client.keys(discovery, force=True)
        except OidcError as exc:
            return Check("signing keys", False, f"{exc.message}. {exc.details}".strip())
        kinds = sorted({str(key.get("kty")) for key in keys})
        return Check("signing keys", True, f"{len(keys)} key(s): {', '.join(kinds)}")

    # ------------------------------------------------------------- the flow

    def begin(
        self, name: str, *, redirect_uri: str, client_ip: str, next_path: str | None = None
    ) -> Begun:
        """
        Start a sign-in: remember it, and say where to send the browser.

        Args:
            name: The provider's name.
            redirect_uri: This console's callback address, exactly as the provider
                registered it.
            client_ip: Where the person is.
            next_path: Where they were going; reduced to a path of this console.

        Returns:
            The authorization URL and the secret for the browser's cookie.

        Raises:
            OidcError: ``unknown_provider`` or ``provider_disabled`` (shown as
                unavailable), or the provider could not be reached.
            OidcRefused: ``no_accounts`` while the server has none.
        """
        provider = self.data.get_provider(name)
        if provider is None or not provider.enabled:
            raise OidcError(
                "unknown_provider" if provider is None else "provider_disabled",
                "That sign-in is not available",
                public="unavailable",
            )
        if not self.accounts.any_exist():
            raise self._no_accounts()
        discovery = self.client.discovery(provider)
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(24)
        verifier = new_pkce_verifier()
        binding = secrets.token_urlsafe(32)
        self.data.begin_flow(
            PendingFlow(
                state_hash=digest(state),
                provider=provider.name,
                nonce=nonce,
                code_verifier=verifier,
                binding_hash=digest(binding),
                redirect_uri=redirect_uri,
                next_path=safe_next(next_path),
                client_ip=client_ip,
                expires_at=self.now() + FLOW_SECONDS,
            )
        )
        url = self.client.authorization_url(
            provider,
            discovery,
            redirect_uri=redirect_uri,
            state=state,
            nonce=nonce,
            verifier=verifier,
        )
        return Begun(url=url, binding=binding, expires_in=FLOW_SECONDS)

    @staticmethod
    def _no_accounts() -> OidcRefused:
        """
        Returns:
            The refusal while the server has no account.
        """
        return OidcRefused(
            "no_accounts",
            "This server has no accounts yet",
            details=(
                "The first account is made by the operator, never by a provider: "
                "noust user create NAME --role admin."
            ),
        )

    def finish(self, *, state: str, code: str, binding: str | None, client_ip: str) -> Answer:
        """
        Take the provider's answer: check it, admit the person, and issue the ticket.

        Args:
            state: The ``state`` the provider echoed.
            code: The authorization code.
            binding: The secret of the browser's cookie.
            client_ip: Where the answer came from.

        Returns:
            The ticket for the console to collect, and what happened.

        Raises:
            OidcError: The sign-in is unknown, expired, spent or from another
                browser (``unknown_state``, ``binding``); the provider refused
                the code or its token was refused (``token_*``).
            OidcRefused: The provider vouched for the person and the policy says no.
        """
        key = digest(state or "")
        flow = self.data.get_flow(key)
        if flow is None:
            raise OidcError(
                "unknown_state",
                "This sign-in is unknown, expired or already answered",
                public="expired",
            )
        # Before the flow is spent: somebody who saw the state in an address
        # but is not the browser that began cannot use the sign-in up.
        if not hmac.compare_digest(flow.binding_hash, digest(binding or "")):
            raise OidcError(
                "binding",
                "This answer came back to a different browser than the one that began",
                public="failed",
            )
        if self.data.spend_flow(key) is None:
            raise OidcError(
                "unknown_state",
                "This sign-in is unknown, expired or already answered",
                public="expired",
            )
        provider = self.data.get_provider(flow.provider)
        if provider is None or not provider.enabled:
            raise OidcError(
                "provider_disabled", "That sign-in is not available", public="unavailable"
            )
        discovery = self.client.discovery(provider)
        try:
            secret = self.data.read_secret(provider.name)
        except SecretsLockedError as exc:
            raise OidcError(
                "secrets_locked",
                "The central's secrets are locked",
                details="Unlock them: noust central unlock.",
                public="unavailable",
            ) from exc
        tokens = self.client.exchange_code(
            provider,
            discovery,
            secret,
            code=code,
            redirect_uri=flow.redirect_uri,
            verifier=flow.code_verifier,
        )
        claims = self.client.verify_id_token(provider, discovery, tokens.id_token, nonce=flow.nonce)
        claims = self._complete(provider, discovery, claims, tokens.access_token)
        person = identity_from_claims(provider, claims)
        return self._admit(provider, person, flow, client_ip)

    def _complete(
        self,
        provider: OidcProvider,
        discovery: Discovery,
        claims: dict[str, Any],
        access: str | None,
    ) -> dict[str, Any]:
        """
        Ask userinfo for what the ID token left out, when something is missing.

        Args:
            provider: The provider.
            discovery: Its discovery.
            claims: The verified token's claims.
            access: The access token of this sign-in.

        Returns:
            The claims; completed when the token lacked the e-mail, or the groups
            the provider is asked to map, and userinfo could say.
        """
        needs_email = "email" not in claims or (
            "email_verified" not in claims and not provider.assume_email_verified
        )
        needs_groups = provider.group_sync and claim_at(claims, provider.groups_claim) is None
        if not (needs_email or needs_groups) or not access or discovery.userinfo_endpoint is None:
            return claims
        try:
            info = self.client.userinfo(discovery, access)
        except OidcError as exc:
            # The token is already verified: a provider whose userinfo is down
            # still lets people in, with what the token said.
            logger.warning("Userinfo of provider %s could not be read: %s", provider.name, exc)
            return claims
        return self.client.merge_userinfo(claims, info)

    def collect(self, handle: str, binding: str | None, client_ip: str) -> tuple[Ticket, Account]:
        """
        Collect a verified answer: the console's second call of a sign-in.

        Args:
            handle: The ticket handle ``finish`` issued.
            binding: The secret of the browser's cookie.
            client_ip: Where the console's call comes from; the address that
                received the provider's answer.

        Returns:
            The ticket, spent, and the account.

        Raises:
            OidcError: ``ticket`` (unknown, expired or spent), ``binding`` or
                ``address`` (another browser, or another address), or
                ``account`` when the account can no longer sign in.
        """
        key = digest(handle or "")
        found = self.data.get_ticket(key)
        if found is None:
            raise OidcError("ticket", "This sign-in is unknown, expired or spent", public="expired")
        if not hmac.compare_digest(found.binding_hash, digest(binding or "")):
            raise OidcError("binding", "This ticket belongs to another browser", public="failed")
        if found.client_ip != client_ip:
            raise OidcError("address", "This ticket belongs to another address", public="failed")
        ticket = self.data.spend_ticket(key)
        if ticket is None:
            raise OidcError("ticket", "This sign-in is unknown, expired or spent", public="expired")
        account = self.accounts.get(ticket.account_id)
        if account is None or not account.can_sign_in(self.now()):
            raise OidcError("account", "The account can no longer sign in", public="refused")
        return ticket, account

    # ------------------------------------------------------------- admission

    def _admit(
        self, provider: OidcProvider, person: IdentityClaims, flow: PendingFlow, client_ip: str
    ) -> Answer:
        """
        Decide whether the person the provider vouched for comes in, and as whom.

        Args:
            provider: The provider.
            person: Who the verified token says it is.
            flow: The sign-in this answers.
            client_ip: Where the answer came from.

        Returns:
            The ticket and what happened.

        Raises:
            OidcRefused: With a reason, when the policy says no.
        """
        who = f"{provider.name}:{person.subject}"
        try:
            notes, account, identity = self._decide(provider, person, client_ip)
        except OidcRefused as exc:
            exc.who = who
            raise
        handle = secrets.token_urlsafe(32)
        detail = f"through {provider.label}" + (f" ({'; '.join(notes)})" if notes else "")
        self.data.issue_ticket(
            Ticket(
                ticket_hash=digest(handle),
                account_id=account.id,
                provider=provider.name,
                binding_hash=flow.binding_hash,
                client_ip=client_ip,
                next_path=flow.next_path,
                amr=person.amr,
                detail=detail,
                expires_at=self.now() + TICKET_SECONDS,
            )
        )
        logger.info(
            "Account %s signed in through %s (identity %s)", account.username, who, identity.id
        )
        return Answer(handle, flow.next_path, account, provider, tuple(notes))

    def _decide(
        self, provider: OidcProvider, person: IdentityClaims, client_ip: str
    ) -> tuple[list[str], Account, Identity]:
        """
        Args:
            provider: The provider.
            person: Who the verified token says it is.
            client_ip: Where the answer came from.

        Returns:
            What happened, the account and its identity.

        Raises:
            OidcRefused: With a reason, when the policy says no.
        """
        email = provider.vouched_email(person)
        if provider.require_email_verified and email is None:
            raise OidcRefused(
                "email_unverified",
                "The provider does not vouch for this person's e-mail",
                details=(
                    "Verify the address at the provider; or, for a directory the organisation "
                    "controls that never says, set assume-email-verified."
                ),
            )
        if provider.allowed_domains:
            domain = email.rsplit("@", 1)[-1] if email else ""
            if domain not in provider.allowed_domains:
                raise OidcRefused(
                    "domain_not_allowed", "This e-mail's domain is not allowed to sign in"
                )
        if provider.require_amr and not set(person.amr) & set(provider.require_amr):
            raise OidcRefused(
                "amr_missing",
                "The provider did not say it used a method this server requires",
                details=f"Required one of: {', '.join(provider.require_amr)}.",
            )
        if not self.accounts.any_exist():
            raise self._no_accounts()

        notes: list[str] = []
        actor = Actor(kind="system", name=f"oidc:{provider.name}", via="oidc", source=client_ip)
        identity = self.data.find_identity(person.issuer, person.subject)
        if identity is None:
            account, identity = self._first_sign_in(provider, person, actor, notes)
        else:
            found = self.accounts.get(identity.account_id)
            if found is None:
                raise OidcRefused("account_missing", "The linked account no longer exists")
            account = found
        self._require_usable(account)
        mapped = self._sync_groups(provider, person, account, identity, actor, notes)
        self.data.touch_identity(
            identity.id, email=person.email, client_ip=client_ip, mapped_role=mapped
        )
        refreshed = self.accounts.get(account.id)
        return notes, refreshed or account, identity

    def _require_usable(self, account: Account) -> None:
        """
        Args:
            account: The account the person resolved to.

        Raises:
            OidcRefused: ``disabled``, ``invited`` or ``locked``.
        """
        if account.status == STATUS_DISABLED:
            raise OidcRefused("disabled", "The account is disabled")
        if account.status == STATUS_INVITED:
            raise OidcRefused("invited", "The account has not accepted its invitation yet")
        if not account.can_sign_in(self.now()):
            raise OidcRefused("locked", "The account is locked")

    def _first_sign_in(
        self, provider: OidcProvider, person: IdentityClaims, actor: Actor, notes: list[str]
    ) -> tuple[Account, Identity]:
        """
        Resolve a person this server has never seen to an account, or refuse.

        Args:
            provider: The provider.
            person: Who the verified token says it is.
            actor: Who is acting, for the audit trail.
            notes: Filled with what happens.

        Returns:
            The account and the identity just linked.

        Raises:
            OidcRefused: ``ambiguous_email``, ``link_needs_second_factor``,
                ``already_linked_account``, ``not_invited``, ``email_taken`` or
                ``no_account``.
        """
        email = provider.vouched_email(person)
        if provider.link_by_email and email:
            # Only an account that may sign in is linked: a link to a disabled or locked one
            # would stay behind a refusal.
            mine = [a for a in self.accounts.find_by_person(email) if a.can_sign_in(self.now())]
            if len(mine) > 1:
                raise OidcRefused(
                    "ambiguous_email",
                    "More than one account belongs to this e-mail",
                    details="Link the right one: noust auth oidc link.",
                )
            if len(mine) == 1:
                account = mine[0]
                if not account.has_mfa:
                    raise OidcRefused(
                        "link_needs_second_factor",
                        "The account of this e-mail has no second factor yet, so it is not "
                        "linked automatically",
                        details="Its owner enrols one first, or an administrator links it: "
                        "noust auth oidc link.",
                    )
                if self._linked_at(account, person.issuer):
                    raise OidcRefused(
                        "already_linked_account",
                        "The account of this e-mail is already linked to another person at "
                        "this provider",
                    )
                identity = self._link(provider, person, account, actor)
                notes.append(f"linked to {account.username} by verified e-mail")
                return account, identity
        if provider.provisioning in (PROVISIONING_INVITE, PROVISIONING_AUTO) and email:
            invited = [a for a in self.accounts.find_by_person(email) if a.status == STATUS_INVITED]
            if len(invited) > 1:
                raise OidcRefused(
                    "ambiguous_email",
                    "More than one invitation is waiting for this e-mail",
                    details="Link the right account: noust auth oidc link.",
                )
            if len(invited) == 1:
                account = self.accounts.activate_external(invited[0].id)
                identity = self._link(provider, person, account, actor)
                self._audit(
                    "auth.oidc.provision", actor, account.username, "invited account activated"
                )
                notes.append(f"activated the invited account {account.username}")
                return account, identity
        if provider.provisioning == PROVISIONING_INVITE:
            raise OidcRefused(
                "not_invited",
                "No invitation is waiting for this e-mail",
                details="Invite the person first: noust user invite NAME --person-ref E-MAIL.",
            )
        if provider.provisioning == PROVISIONING_AUTO:
            if email and self.accounts.find_by_person(email):
                raise OidcRefused(
                    "email_taken",
                    "An account of this e-mail exists and is not linked to this person",
                    details="Link it: noust auth oidc link, or turn on link-by-email.",
                )
            account = self._create(provider, person, actor)
            identity = self._link(provider, person, account, actor)
            self._audit(
                "auth.oidc.provision", actor, account.username, f"created as {account.role}"
            )
            notes.append(f"created the account {account.username} as {account.role}")
            return account, identity
        raise OidcRefused(
            "no_account",
            "No account is linked to this person",
            details=(
                "An administrator links it: noust auth oidc link PROVIDER USER --subject SUBJECT, "
                "or sets the provider's provisioning to invite or auto."
            ),
        )

    def _linked_at(self, account: Account, issuer: str) -> bool:
        """
        Args:
            account: An account.
            issuer: A provider's issuer.

        Returns:
            Whether the account already has a person linked at that issuer.
        """
        return account.id in self.data.accounts_linked_to(issuer)

    def _link(
        self, provider: OidcProvider, person: IdentityClaims, account: Account, actor: Actor
    ) -> Identity:
        """
        Args:
            provider: The provider.
            person: Who the verified token says it is.
            account: The account they are.
            actor: Who is acting.

        Returns:
            The identity.
        """
        identity = self.data.link_identity(
            account.id,
            provider=provider.name,
            issuer=person.issuer,
            subject=person.subject,
            email=person.email,
            created_by=actor.label,
        )
        self._audit("auth.oidc.link", actor, account.username, f"subject {person.subject[:64]}")
        return identity

    def _create(self, provider: OidcProvider, person: IdentityClaims, actor: Actor) -> Account:
        """
        Make the account a first sign-in becomes.

        Args:
            provider: The provider, whose default role and team it follows.
            person: Who the verified token says it is.
            actor: Who is acting.

        Returns:
            The new account.

        Raises:
            OidcRefused: ``team_missing`` when the default team is gone;
                ``account_refused`` when separation of duties refuses the role.
        """
        role = provider.default_role
        if self.accounts.policy().requires("sso_no_admin") and role == ROLE_ADMIN:
            role = "viewer"
        team: tuple[int, str] | None = None
        if provider.default_team:
            try:
                found = TeamStore(self.accounts.store).require_team(provider.default_team)
            except TeamError as exc:
                raise OidcRefused(
                    "team_missing",
                    f"The default team {provider.default_team!r} of this provider does not exist",
                    details="Create it, or change the provider's default team.",
                ) from exc
            team = (found.id, provider.default_team_role or "viewer")
        try:
            return self.accounts.create_external(
                self._username_for(person),
                role,
                display_name=person.name,
                person_ref=provider.vouched_email(person),
                created_by=actor.label,
                team=team,
            )
        except (AccountError, ValidationError) as exc:
            raise OidcRefused("account_refused", exc.message, details=exc.details) from exc

    def _username_for(self, person: IdentityClaims) -> str:
        """
        Invent a free username for a new account.

        Args:
            person: Who the verified token says it is.

        Returns:
            A valid username not used by any account.

        Raises:
            OidcRefused: ``username`` when none could be found.
        """
        local = person.email.split("@", 1)[0] if person.email else ""
        for source in (person.username, local, f"sso-{person.subject[:12]}"):
            # A username that is an address (Microsoft's) is its part before the @.
            base = _USERNAME_JUNK.sub("-", (source or "").split("@", 1)[0].lower()).strip("-._")[
                :40
            ]
            if len(base) < 2:
                continue
            for attempt in range(0, 50):
                candidate = base if attempt == 0 else f"{base}-{attempt + 1}"
                try:
                    name = validate_username(candidate)
                except ValidationError:
                    break
                if self.accounts.find(name) is None:
                    return name
        raise OidcRefused("username", "No username could be made for this person")

    # ---------------------------------------------------------------- groups

    def _sync_groups(
        self,
        provider: OidcProvider,
        person: IdentityClaims,
        account: Account,
        identity: Identity,
        actor: Actor,
        notes: list[str],
    ) -> str | bool | None:
        """
        Set the account's role and team memberships from the provider's groups.

        Recomputed on every sign-in, so a person removed from a group at the
        provider loses what it gave at their next sign-in. What an administrator
        set by hand is never touched: a role changed since the mapping gave it, a
        membership somebody else added.

        Args:
            provider: The provider.
            person: Who the verified token says it is.
            account: The account.
            identity: Their identity, which remembers the role a mapping gave.
            actor: Who is acting.
            notes: Filled with what changed.

        Returns:
            The role a mapping now gives (for the identity to remember), None
            when none does, or False when the sync is off and nothing changes.

        Raises:
            OidcRefused: ``role_conflict`` when separation of duties refuses the
                role the groups give; the sign-in fails closed.
        """
        if not provider.group_sync:
            return False
        groups = set(person.groups)
        current = account
        mapped: str | None = identity.mapped_role
        if account.role not in INSTANCE_ONLY_ROLES:
            wanted = next(
                (m.role for m in provider.group_mappings if m.role and m.group in groups), None
            )
            if wanted == ROLE_ADMIN and self.accounts.policy().requires("sso_no_admin"):
                notes.append("the admin role is not given by groups on this server")
                wanted = None
            if wanted is not None:
                if account.role != wanted:
                    current = self._set_role(
                        account, wanted, actor, "groups of the provider", notes
                    )
                mapped = wanted
            elif mapped is not None:
                if account.role == mapped and mapped != provider.default_role:
                    current = self._set_role(
                        account, provider.default_role, actor, "no longer in the group", notes
                    )
                mapped = None
        self._sync_teams(provider, groups, current, actor, notes)
        return mapped

    def _set_role(
        self, account: Account, role: str, actor: Actor, why: str, notes: list[str]
    ) -> Account:
        """
        Args:
            account: The account.
            role: The role to give.
            actor: Who is acting.
            why: For the record.
            notes: Filled with the change.

        Returns:
            The account after.

        Raises:
            OidcRefused: ``role_conflict`` when separation of duties refuses it.
        """
        try:
            changed = self.accounts.set_role(account.username, role)
        except AccountError as exc:
            raise OidcRefused("role_conflict", exc.message, details=exc.details) from exc
        self._audit("user.role_change", actor, changed.username, f"{account.role} to {role}: {why}")
        notes.append(f"role {account.role} to {role}")
        return changed

    def _sync_teams(
        self,
        provider: OidcProvider,
        groups: set[str],
        account: Account,
        actor: Actor,
        notes: list[str],
    ) -> None:
        """
        Args:
            provider: The provider.
            groups: The person's groups.
            account: The account, with the role it has now.
            actor: Who is acting.
            notes: Filled with the changes.
        """
        if account.role in INSTANCE_ONLY_ROLES:
            return
        creator = f"oidc:{provider.name}"
        desired: dict[str, str] = {}
        for mapping in provider.group_mappings:
            if mapping.team and mapping.group in groups:
                role = mapping.team_role or "viewer"
                held = desired.get(mapping.team)
                if held is None or TEAM_ROLES.index(role) > TEAM_ROLES.index(held):
                    desired[mapping.team] = role
        teams = TeamStore(self.accounts.store)
        have = self.data.team_memberships(account.id)
        for slug, role in sorted(desired.items()):
            existing = have.get(slug)
            if existing is not None and (existing[1] != creator or existing[0] == role):
                continue
            try:
                teams.add_member(slug, account.username, role, created_by=creator)
            except (TeamError, ValidationError) as exc:
                notes.append(f"team {slug} not joined: {exc.message}")
                continue
            self._audit("team.member.add", actor, f"{slug}/{account.username}", f"as {role}")
            notes.append(f"joined team {slug} as {role}")
        for slug, (_role, by) in sorted(have.items()):
            if by != creator or slug in desired:
                continue
            try:
                teams.remove_member(slug, account.username)
            except TeamConflictError as exc:
                notes.append(f"team {slug} kept: {exc.message}")
                continue
            self._audit(
                "team.member.remove", actor, f"{slug}/{account.username}", "no longer in the group"
            )
            notes.append(f"left team {slug}")

    # ------------------------------------------------------------ identities

    def identities(self, username: str | None = None) -> list[Identity]:
        """
        Args:
            username: Only this account's; everybody's when None.

        Returns:
            The identities linked to accounts.

        Raises:
            AccountNotFoundError: When the account does not exist.
        """
        if username is None:
            return self.data.list_identities()
        return self.data.list_identities(self.accounts.require(username).id)

    def link(
        self, provider_name: str, username: str, subject: str, *, actor: str | None = None
    ) -> Identity:
        """
        Link an account to a person at a provider by hand.

        The way in for a provider whose provisioning is ``off``, and for an
        account that does not have the person's e-mail as its reference.

        Args:
            provider_name: The provider.
            username: The account.
            subject: The provider's id (``sub``) for the person.
            actor: Who links it, for the record.

        Returns:
            The identity.

        Raises:
            ValidationError: When the subject is empty or too long.
            OidcError: ``already_linked`` when that person is linked already.
        """
        provider = self.require_provider(provider_name)
        account = self.accounts.require(username)
        text = (subject or "").strip()
        if not text or len(text) > 255 or any(ord(char) < 33 for char in text):
            raise ValidationError(
                "Invalid subject",
                details="Copy the 'sub' the provider gives the person (up to 255 characters).",
                field="subject",
            )
        return self.data.link_identity(
            account.id,
            provider=provider.name,
            issuer=provider.issuer,
            subject=text,
            email=None,
            created_by=actor,
            protocol=PROTOCOL_OIDC,
        )

    def unlink_identity(self, identity_id: int) -> Identity:
        """
        Cut the link between a person at a provider and an account.

        Args:
            identity_id: The identity's id.

        Returns:
            The identity as it was.

        Raises:
            OidcError: ``no_identity`` when there is no such identity.
        """
        if self.data.get_identity(identity_id) is not None:
            refuse_to_lose_a_way_in(
                self.accounts.store,
                without_identity=identity_id,
                action="Cutting this link",
            )
        found = self.data.unlink_identity(identity_id)
        if found is None:
            raise OidcError(
                "no_identity",
                f"No identity with id {identity_id}",
                details="List them with 'noust auth oidc identities'.",
            )
        return found

    # ----------------------------------------------------------------- audit

    @staticmethod
    def _audit(event: str, actor: Actor, target: str, detail: str) -> None:
        """
        Args:
            event: A catalog event.
            actor: Who acted.
            target: What it was done to.
            detail: One sentence.
        """
        audit_record(event, actor=actor, target=f"account:{target}", details={"detail": detail})
