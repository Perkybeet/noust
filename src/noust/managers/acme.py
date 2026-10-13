# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which certificate authority Noust orders from, and as whom.

Four settings, kept where the rest of Noust's settings are (``ssl.email``,
``ssl.ca``, ``ssl.acme_server``, ``ssl.eab_kid``), and one secret: the HMAC key some
authorities hand out with an external account (ZeroSSL, Google), kept in the secret
store and read only when an account is registered. certbot remembers the account it
registered per authority, so the key is needed once, not at every order.

The point of choosing is practical: Let's Encrypt's staging environment lets an
operator try a whole setup - DNS, ports, hooks - without spending the rate limit that
a mistake on production burns for a week; ZeroSSL and Google are somewhere to go when
that limit is gone.

Nothing here runs a process. The orders are :class:`~noust.managers.cert_manager.CertManager`'s,
which asks this module for the ``--server`` argument and the file that carries the
external account (``--config``, so the key is never in an argument).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from noust.core.config import ACME_CAS, Config
from noust.core.exceptions import SecurityError, ValidationError
from noust.core.secrets import SecretStore

#: The secret holding the external account, as the ini certbot reads (``--config``).
EAB_SECRET = "acme/eab"  # noqa: S105 - the secret's name, not a password

#: The HMAC key on its own, so the ini can be written again when the key id changes.
EAB_KEY_SECRET = "acme/eab-key"  # noqa: S105 - the secret's name, not a password

#: Let's Encrypt's staging directory: certificates it issues are not trusted by browsers.
STAGING_URL = "https://acme-staging-v02.api.letsencrypt.org/directory"


@dataclass(frozen=True)
class CertificateAuthority:
    """
    A certificate authority Noust knows by name.

    Attributes:
        name: The value of ``ssl.ca``.
        label: How the console calls it.
        directory: Its ACME directory; None for Let's Encrypt, certbot's default.
        needs_eab: It asks for an external account before it orders.
        trusted: Browsers trust what it issues.
    """

    name: str
    label: str
    directory: str | None
    needs_eab: bool = False
    trusted: bool = True


AUTHORITIES: dict[str, CertificateAuthority] = {
    "letsencrypt": CertificateAuthority("letsencrypt", "Let's Encrypt", None),
    "letsencrypt-staging": CertificateAuthority(
        "letsencrypt-staging", "Let's Encrypt (staging)", STAGING_URL, trusted=False
    ),
    "zerossl": CertificateAuthority(
        "zerossl", "ZeroSSL", "https://acme.zerossl.com/v2/DV90", needs_eab=True
    ),
    "google": CertificateAuthority(
        "google",
        "Google Trust Services",
        "https://dv.acme-v02.api.pki.goog/directory",
        needs_eab=True,
    ),
    "custom": CertificateAuthority("custom", "Another authority", None),
}


@dataclass(frozen=True)
class AcmeView:
    """
    The ACME settings as they are.

    Attributes:
        email: The address registered with the authority.
        ca: The authority's name.
        label: How it is called.
        server: The directory URL orders go to; empty for Let's Encrypt's own.
        needs_eab: It asks for an external account.
        eab_kid: The external account's key id.
        eab_configured: The HMAC key is in the secret store (it is never shown).
        trusted: Browsers trust what it issues.
    """

    email: str
    ca: str
    label: str
    server: str
    needs_eab: bool
    eab_kid: str
    eab_configured: bool
    trusted: bool

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready. The HMAC key is not in it.
        """
        return {
            "email": self.email,
            "ca": self.ca,
            "label": self.label,
            "server": self.server,
            "needs_eab": self.needs_eab,
            "eab_kid": self.eab_kid,
            "eab_configured": self.eab_configured,
            "trusted": self.trusted,
        }


class AcmeSettings:
    """
    Read and change the ACME settings.

    Args:
        config: The configuration; the installed one by default.
        secrets: Where the HMAC key lives; the secret store beside the store by default.
    """

    def __init__(self, config: Config | None = None, secrets: SecretStore | None = None) -> None:
        self.config = config or Config()
        self.secrets = secrets or SecretStore()

    def authority(self) -> CertificateAuthority:
        """
        Returns:
            The authority in force; Let's Encrypt when the setting names none Noust knows.
        """
        name = str(self.config.get("ssl.ca", "letsencrypt") or "letsencrypt")
        return AUTHORITIES.get(name, AUTHORITIES["letsencrypt"])

    def server_url(self) -> str | None:
        """
        Returns:
            The directory URL orders go to, or None for certbot's default (Let's Encrypt).
        """
        authority = self.authority()
        if authority.name == "custom":
            return str(self.config.get("ssl.acme_server", "") or "") or None
        return authority.directory

    def current(self) -> AcmeView:
        """
        Returns:
            The settings, with whether the external account's key is present.
        """
        authority = self.authority()
        return AcmeView(
            email=str(self.config.get("ssl.email", "") or ""),
            ca=authority.name,
            label=authority.label,
            server=self.server_url() or "",
            needs_eab=authority.needs_eab,
            eab_kid=str(self.config.get("ssl.eab_kid", "") or ""),
            eab_configured=self.secrets.read(EAB_KEY_SECRET) is not None,
            trusted=authority.trusted,
        )

    def update(
        self,
        *,
        email: str | None = None,
        ca: str | None = None,
        acme_server: str | None = None,
        eab_kid: str | None = None,
        eab_hmac_key: str | None = None,
    ) -> AcmeView:
        """
        Change the settings; what is not named stays.

        Args:
            email: The address to register with.
            ca: The authority (one of :data:`~noust.core.config.ACME_CAS`).
            acme_server: The directory URL, for ``custom``.
            eab_kid: The external account's key id.
            eab_hmac_key: Its HMAC key. Goes to the secret store, not the configuration.

        Returns:
            The settings now in force.

        Raises:
            ValidationError: A value is not acceptable, or ``custom`` has no URL.
            SecurityError: The configuration file cannot be written.
        """
        changes = {
            "ssl.email": email,
            "ssl.ca": ca,
            "ssl.acme_server": acme_server,
            "ssl.eab_kid": eab_kid,
        }
        for key, value in changes.items():
            if value is not None:
                self.config.set(key, value)
        if self.authority().name == "custom" and not self.server_url():
            raise ValidationError(
                "A custom authority needs its directory URL",
                details="Set it too: --server https://acme.example.com/directory",
                field="acme_server",
            )
        if eab_hmac_key == "":
            self.secrets.delete(EAB_KEY_SECRET)
            self.secrets.delete(EAB_SECRET)
        elif eab_hmac_key is not None or eab_kid is not None:
            hmac = eab_hmac_key if eab_hmac_key is not None else self.secrets.read(EAB_KEY_SECRET)
            if hmac:
                kid = str(self.config.get("ssl.eab_kid", "") or "")
                ini = self._eab_ini(kid, hmac)
                if eab_hmac_key is not None:
                    self.secrets.write(EAB_KEY_SECRET, hmac)
                self.secrets.write(EAB_SECRET, ini)
        try:
            self.config.write()
        except OSError as exc:
            raise SecurityError("The configuration could not be written", details=str(exc)) from exc
        return self.current()

    def eab_file(self) -> Path | None:
        """
        Name the file that carries the external account, for ``certbot --config``.

        Returns:
            The path of the secret file, or None when the authority asks for no
            external account or none is stored. The key is in the file, not in
            any argument.

        Raises:
            ValidationError: The authority needs an external account and it is not set.
        """
        authority = self.authority()
        if not authority.needs_eab:
            return None
        if self.secrets.read(EAB_SECRET) is None:
            raise ValidationError(
                f"{authority.label} orders need an external account",
                details="Set its key id and HMAC key: printf %s '$KEY' | noust cert acme --eab-kid ID --eab-hmac-key-stdin",
                field="eab_hmac_key",
            )
        return self.secrets.usable_path(EAB_SECRET)

    @staticmethod
    def _eab_ini(kid: str, key: str) -> str:
        """
        Write the external account as certbot's config file reads it.

        Args:
            kid: The key id.
            key: The HMAC key.

        Returns:
            Two ``option = value`` lines.

        Raises:
            ValidationError: Either is empty or could add a line.
        """
        if not kid:
            raise ValidationError("Set the key id with the HMAC key", field="eab_kid")
        if any(ord(char) < 33 or ord(char) == 127 for char in key) or len(key) > 512:
            raise ValidationError("That is not an HMAC key", field="eab_hmac_key")
        return f"eab-kid = {kid}\neab-hmac-key = {key}\n"


__all__ = ["ACME_CAS", "AUTHORITIES", "AcmeSettings", "AcmeView", "CertificateAuthority"]
