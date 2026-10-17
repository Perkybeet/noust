# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Turning the password and passkey sign-in off once single sign-on is in (I-23).

A server whose people all sign in through the organisation's identity provider
has no use for a second front door with its own passwords. With
``auth.local_login.enabled`` off, a password or a passkey no longer signs an
account in - except the accounts listed in ``auth.local_login.exempt``, the
break-glass administrators - while the provider's sign-in, the master token and
the command line as root work exactly as before. The master token stays the way
back in, and under the ENS profile it only recovers accounts, so this module
also refuses to switch the password off while that would leave nobody able to
administer the server.

The rule is checked where the setting is written (a validator in
:mod:`noust.core.config`, so ``noust config set``, the console and
``noust auth local-login`` cannot disagree) and again where a provider or a link
is removed, because taking away the last way in is the other way to the same
lock-out. What an account may sign in with is decided at the one ending of a
sign-in (:func:`noust.web.api.auth.account_signed_in`) and as soon as the
password is right, so the answer reveals nothing a wrong password did not.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from noust.core.accounts.model import ROLE_ADMIN, ROLE_SECURITY, Account, validate_username
from noust.core.exceptions import ValidationError

if TYPE_CHECKING:
    from noust.core.store import NoustStore

#: The two settings, as ``noust config`` and the console spell them.
SETTING_ENABLED = "auth.local_login.enabled"
SETTING_EXEMPT = "auth.local_login.exempt"

#: Break-glass accounts a server keeps at most: a short list is the point.
MAX_EXEMPT = 10

#: The roles that can still run the server: someone must be able to sign in as one.
ADMINISTERING_ROLES = frozenset({ROLE_ADMIN, ROLE_SECURITY})

#: How sign-ins that are decided here are told apart from the provider's.
LOCAL_METHODS = frozenset({"password", "passkey"})


class LocalLoginRefused(ValidationError):
    """A sign-in by password or passkey that this server no longer accepts."""


@dataclass(frozen=True)
class LocalLoginPolicy:
    """
    Whether passwords and passkeys sign accounts in, and who is exempt.

    Attributes:
        enabled: Whether a password or a passkey signs an account in.
        exempt: The accounts that keep them when ``enabled`` is off.
    """

    enabled: bool = True
    exempt: frozenset[str] = frozenset()

    def allows(self, account: Account) -> bool:
        """
        Args:
            account: An account that proved a password or a passkey.

        Returns:
            Whether that is enough to sign it in.
        """
        return self.enabled or account.username in self.exempt

    def to_dict(self) -> dict[str, object]:
        """
        Returns:
            The policy as the API shows it.
        """
        return {"enabled": self.enabled, "exempt": sorted(self.exempt)}


def load_policy() -> LocalLoginPolicy:
    """
    Read the policy in force, on every call: a change takes effect at once.

    Returns:
        The policy. A value that cannot be read leaves local sign-in on: failing
        towards "closed" would lock everyone out over a typo.
    """
    from noust.core.config import Config

    config = Config()
    raw = config.get(SETTING_ENABLED)
    enabled = True if raw is None else str(raw).strip().lower() not in ("false", "no", "off", "0")
    exempt = config.get(SETTING_EXEMPT)
    names = (
        frozenset(str(item).strip().lower() for item in exempt)
        if isinstance(exempt, list)
        else frozenset()
    )
    return LocalLoginPolicy(enabled=enabled, exempt=names)


def clean_exempt(value: object) -> list[str]:
    """
    Check the break-glass list.

    Args:
        value: A list of usernames, or text with them separated by commas.

    Returns:
        The usernames, normalised, without repeats.

    Raises:
        ValidationError: When it is not a list of usernames, or is too long.
    """
    items: Iterable[object]
    if isinstance(value, str):
        items = [part for part in value.replace(",", " ").split() if part]
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        raise ValidationError(
            f"{SETTING_EXEMPT} must be a list of usernames",
            details=f"Got {value!r}.",
            field=SETTING_EXEMPT,
        )
    names: list[str] = []
    for item in items:
        name = validate_username(str(item))
        if name not in names:
            names.append(name)
    if len(names) > MAX_EXEMPT:
        raise ValidationError(
            f"At most {MAX_EXEMPT} accounts keep their password",
            details="A break-glass list is a short one.",
            field=SETTING_EXEMPT,
        )
    return names


def survivors(
    store: NoustStore | None = None,
    *,
    exempt: Iterable[str] | None = None,
    without_provider: str | None = None,
    without_identity: int | None = None,
) -> list[str]:
    """
    The accounts that could still sign in and administer with local sign-in off.

    Args:
        store: The store; the process-wide one by default.
        exempt: The break-glass list to count; the one in force by default.
        without_provider: A provider to leave out, as if it were removed or disabled.
        without_identity: A link to leave out, as if it were cut.

    Returns:
        The names of the active administrators and security officers that are
        exempt, or linked to an enabled provider.
    """
    from noust.core.accounts.directory_store import DirectoryStore
    from noust.core.accounts.manager import AccountManager
    from noust.core.accounts.oidc_store import OidcStore
    from noust.core.accounts.saml_store import SamlStore

    kept = frozenset(load_policy().exempt if exempt is None else exempt)
    data = OidcStore(store)
    enabled = {
        provider.name: provider.issuer
        for provider in data.list_providers()
        if provider.enabled and provider.name != without_provider
    }
    # A directory is a way in as much as a provider is: its people sign in against it.
    enabled.update(
        {
            directory.name: directory.issuer
            for directory in DirectoryStore(store).list_directories()
            if directory.enabled and directory.name != without_provider
        }
    )
    enabled.update(
        {
            saml.name: saml.issuer
            for saml in SamlStore(store).list_providers()
            if saml.enabled and saml.name != without_provider
        }
    )
    linked = {
        identity.account_id
        for identity in data.list_identities()
        if identity.provider in enabled
        and identity.issuer == enabled[identity.provider]
        and identity.id != without_identity
    }
    names = []
    for account in AccountManager(store).list_all():
        if account.role not in ADMINISTERING_ROLES or not account.can_sign_in():
            continue
        if account.username in kept or account.id in linked:
            names.append(account.username)
    return sorted(names)


def _refuse_if_nobody(
    store: NoustStore | None,
    action: str,
    *,
    exempt: Iterable[str] | None = None,
    without_provider: str | None = None,
    without_identity: int | None = None,
) -> None:
    """
    Args:
        store: The store.
        action: What is being done, for the message.
        exempt: The break-glass list that would be in force.
        without_provider: A provider the change removes or disables.
        without_identity: A link the change cuts.

    Raises:
        LocalLoginRefused: When no administrator or security officer would be
            able to sign in.
    """
    if survivors(
        store, exempt=exempt, without_provider=without_provider, without_identity=without_identity
    ):
        return
    raise LocalLoginRefused(
        f"{action} would leave nobody able to administer this server",
        details=(
            "Link an administrator or a security officer to an enabled identity provider "
            "('noust auth oidc link'), or name a break-glass account that keeps its password "
            "('noust auth local-login exempt add NAME'), first."
        ),
        field=SETTING_ENABLED,
    )


def refuse_to_turn_off(
    store: NoustStore | None = None, *, exempt: Iterable[str] | None = None
) -> None:
    """
    Refuse to switch the password sign-in off while nobody could administer without it.

    Args:
        store: The store; the process-wide one by default.
        exempt: The break-glass list the change would leave; the one in force by default.

    Raises:
        LocalLoginRefused: When no administrator or security officer would be
            able to sign in.
    """
    _refuse_if_nobody(store, "Turning off the password sign-in", exempt=exempt)


def refuse_to_lose_a_way_in(
    store: NoustStore | None = None,
    *,
    without_provider: str | None = None,
    without_identity: int | None = None,
    action: str = "This change",
) -> None:
    """
    Refuse to remove, disable or unlink the last way an administrator signs in.

    Does nothing while local sign-in is on: then passwords still work.

    Args:
        store: The store; the process-wide one by default.
        without_provider: A provider the change removes or disables.
        without_identity: A link the change cuts.
        action: What is being done, for the message.

    Raises:
        LocalLoginRefused: When local sign-in is off and the change would leave
            nobody able to administer the server.
    """
    if load_policy().enabled:
        return
    _refuse_if_nobody(
        store, action, without_provider=without_provider, without_identity=without_identity
    )


def refuse_local_sign_in(account: Account) -> None:
    """
    Refuse a password or passkey sign-in this server no longer accepts.

    Called once the credential has proved right, so a wrong guess is answered
    as always and nothing is learnt about which accounts are exempt.

    Args:
        account: The account that proved its password or its passkey.

    Raises:
        LocalLoginRefused: When local sign-in is off and the account is not exempt.
    """
    if load_policy().allows(account):
        return
    raise LocalLoginRefused(
        "Sign-in with a password or a passkey is turned off on this server",
        details="Use the sign-in with your organisation's identity provider.",
        field="local_login",
    )
