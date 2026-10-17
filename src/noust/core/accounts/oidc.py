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

from noust.core.accounts.federation import (
    ADMISSION_SETTINGS,
    Admission,
    apply_admission_setting,
    check_references,
    name_in_use,
    settle,
)
from noust.core.accounts.local_login import refuse_to_lose_a_way_in
from noust.core.accounts.manager import AccountManager
from noust.core.accounts.model import (
    Account,
)
from noust.core.accounts.oauth_github import (
    BASE_SCOPES,
    GITHUB_HOST,
    GithubLogin,
    scopes_for,
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
    KIND_GITHUB,
    KIND_OIDC,
    PROTOCOL_OIDC,
    IdentityClaims,
    OidcError,
    OidcProvider,
    check_endpoint_url,
    claim_path,
    validate_client_id,
    validate_kind,
    validate_name,
    validate_redirect_uri,
    validate_scopes,
    validate_skew,
)
from noust.core.accounts.oidc_store import (
    FLOW_SECONDS,
    Identity,
    OidcStore,
    PendingFlow,
    Ticket,
    digest,
)
from noust.core.exceptions import ConfigError, ValidationError
from noust.core.sealing import SecretsLockedError

if TYPE_CHECKING:
    from noust.core.store import NoustStore

logger = logging.getLogger(__name__)

#: The settings :meth:`OidcManager.configure` accepts.
SETTINGS: tuple[str, ...] = (
    "kind",
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
        self.admission = Admission(self.accounts, self.data, self.now)

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
        kind = validate_kind(changes["kind"]) if "kind" in changes else None
        if existing is not None and kind is not None and kind != existing.kind:
            raise ValidationError(
                "A provider's kind is chosen when it is made",
                details=f"{key!r} is a {existing.kind} provider. Make another one instead.",
                field="kind",
            )
        chosen = existing.kind if existing is not None else (kind or KIND_OIDC)
        if existing is None and name_in_use(self.accounts.store, key, except_protocol="oidc"):
            raise ValidationError(
                f"The name {key!r} is already a sign-in provider's",
                details="Names are shared by every way of signing in; pick another.",
                field="name",
            )
        if existing is None and chosen == KIND_GITHUB and not changes.get("client_id"):
            raise ValidationError(
                "A GitHub provider needs a client id",
                details=(
                    "Pass --client-id, from the OAuth App (or GitHub App) registered for Noust. "
                    "Its client secret goes in with --secret."
                ),
                field="client_id",
            )
        if (
            existing is None
            and chosen == KIND_OIDC
            and not (changes.get("issuer") and changes.get("client_id"))
        ):
            raise ValidationError(
                "A new provider needs an issuer and a client id",
                details="Pass --issuer and --client-id, from the provider's registration of Noust.",
            )
        now = self.now()
        base = existing or OidcProvider(
            name=key,
            label=key,
            issuer=GITHUB_HOST if chosen == KIND_GITHUB else "",
            client_id="",
            kind=chosen,
            scopes=BASE_SCOPES if chosen == KIND_GITHUB else "openid email profile",
            created_at=now,
            created_by=actor,
        )
        provider = self._apply(
            base, {name: value for name, value in changes.items() if name != "kind"}
        )
        provider = replace(provider, updated_at=now, updated_by=actor)
        check_references(provider, self.accounts)
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
            if setting in ADMISSION_SETTINGS:
                updated = apply_admission_setting(updated, setting, value)
            elif setting == "issuer":
                issuer = check_endpoint_url(str(value), "issuer", field_name="issuer")
                if "?" in issuer:
                    raise ValidationError("An issuer has no query", field="issuer")
                updated = replace(updated, issuer=issuer)
            elif setting == "client_id":
                updated = replace(updated, client_id=validate_client_id(str(value)))
            elif setting == "scopes":
                if updated.kind == KIND_GITHUB:
                    raise ValidationError(
                        "A GitHub provider's scopes are chosen by Noust",
                        details=(
                            f"It asks for {BASE_SCOPES}, and read:org when it maps "
                            "organisations and teams to roles (--group-sync)."
                        ),
                        field="scopes",
                    )
                updated = replace(updated, scopes=validate_scopes(value))
            elif setting == "clock_skew_seconds":
                updated = replace(updated, clock_skew_seconds=validate_skew(value))
            elif setting == "groups_claim":
                updated = replace(updated, groups_claim=claim_path(value))
            elif setting == "redirect_uri":
                updated = replace(updated, redirect_uri=validate_redirect_uri(value))
        if updated.kind == KIND_GITHUB:
            # What it asks GitHub for follows from whether it maps groups.
            updated = replace(updated, scopes=scopes_for(updated.group_sync))
        return settle(updated, self.accounts)

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
        if provider.kind == KIND_GITHUB:
            return self._test_github(provider, redirect_uri)
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

    def _test_github(self, provider: OidcProvider, redirect_uri: str | None) -> list[Check]:
        """
        Args:
            provider: A GitHub provider.
            redirect_uri: The callback address Noust would give GitHub, when known.

        Returns:
            One check per thing looked at; GitHub being unreachable is a failed check.
        """
        checks: list[Check] = []
        try:
            api = GithubLogin(self.client.http).reachable(provider)
        except OidcError as exc:
            checks.append(Check("api", False, f"{exc.message}. {exc.details}".strip()))
        else:
            checks.append(Check("api", True, f"GitHub answers at {api}"))
        try:
            secret = self.data.read_secret(provider.name)
        except SecretsLockedError:
            checks.append(Check("client secret", False, "the central's secrets are locked"))
        else:
            checks.append(
                Check(
                    "client secret",
                    bool(secret),
                    "set"
                    if secret
                    else "none: GitHub needs the application's client secret to give a token",
                )
            )
        checks.append(Check("scopes", True, provider.scopes))
        checks.append(
            Check(
                "redirect URI",
                True,
                f"set the application's Authorization callback URL to {redirect_uri}"
                if redirect_uri
                else "set the application's Authorization callback URL to the console's "
                "address followed by /api/auth/oidc/callback (or set web.public_url, "
                "and it is shown here)",
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
            raise Admission.no_accounts()
        # GitHub has no discovery document: its endpoints are fixed.
        discovery = None if provider.kind == KIND_GITHUB else self.client.discovery(provider)
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
        if discovery is None:
            url = GithubLogin.authorization_url(
                provider, redirect_uri=redirect_uri, state=state, verifier=verifier
            )
        else:
            url = self.client.authorization_url(
                provider,
                discovery,
                redirect_uri=redirect_uri,
                state=state,
                nonce=nonce,
                verifier=verifier,
            )
        return Begun(url=url, binding=binding, expires_in=FLOW_SECONDS)

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
        try:
            secret = self.data.read_secret(provider.name)
        except SecretsLockedError as exc:
            raise OidcError(
                "secrets_locked",
                "The central's secrets are locked",
                details="Unlock them: noust central unlock.",
                public="unavailable",
            ) from exc
        if provider.kind == KIND_GITHUB:
            return self._admit(
                provider, self._github_person(provider, secret, flow, code), flow, client_ip
            )
        discovery = self.client.discovery(provider)
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

    def _github_person(
        self, provider: OidcProvider, secret: str | None, flow: PendingFlow, code: str
    ) -> IdentityClaims:
        """
        Find out who GitHub says signed in.

        Args:
            provider: A GitHub provider.
            secret: Its client secret, when one is stored.
            flow: The sign-in this answers, with the callback and the PKCE verifier.
            code: The authorization code GitHub sent back.

        Returns:
            The person. The access token lived only for these calls.

        Raises:
            OidcError: ``no_client_secret``; or GitHub refused the code or could not be asked.
        """
        if not secret:
            raise OidcError(
                "no_client_secret",
                "GitHub needs the application's client secret",
                details="Set it: noust auth oidc set NAME --secret (the OAuth App's client secret).",
                public="unavailable",
            )
        github = GithubLogin(self.client.http)
        token = github.exchange_code(
            provider,
            secret,
            code=code,
            redirect_uri=flow.redirect_uri,
            verifier=flow.code_verifier,
        )
        return github.identity(provider, token)

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
        admitted = self.admission.admit(provider, person, flow, client_ip)
        return Answer(admitted.ticket, flow.next_path, admitted.account, provider, admitted.notes)

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
