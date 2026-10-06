# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An HTTPS credential for a private repository, kept as a secret and never in its URL.

A private repository reached over https needs a user and a token. Until now the
only places to put them were the source URL (``https://user:token@host/...``,
stored in the clear in the application's row) or a git credential helper in
root's home. This keeps the pair in the secret store instead, assigned to the
repository the way a deploy key is, and hands it to git the way an
``https://user:token@host`` source always was: as an ``Authorization`` header
in git's environment, scoped to the host, never in an argument
(:func:`noust.managers.source_manager.split_url_credentials` builds it, so there
is one way a credential reaches git).

For Bitbucket Cloud the user and the secret are, in the order Atlassian
documents them: an API token (the user is your Bitbucket username, or the fixed
``x-bitbucket-api-token-auth``), or a repository or workspace access token
(the user is ``x-token-auth``). App passwords stopped working on 28 July 2026.
For other hosts they are whatever the host takes for a personal access token.
A deploy key (:mod:`noust.managers.source_keys`) is the alternative that needs
no secret that can read more than one repository.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

from noust.core import audit
from noust.core.exceptions import ConfigError, ValidationError
from noust.core.repositories import repository_identity
from noust.core.secrets import SecretStore
from noust.core.store import App, NoustStore, get_store
from noust.core.teams import ScopedStore

#: Where a credential is kept, under the secrets: ``git-credentials/<host>/<owner>/<repo>``.
NAMESPACE = "git-credentials"

#: Longest user and secret accepted.
MAX_USERNAME = 128
MAX_SECRET = 1024


@dataclass(frozen=True)
class CredentialView:
    """
    What an application's repository is reached with over https. Never the secret.

    Attributes:
        domain: The application.
        repository: Its repository as ``host/owner/repo``; None when its source
            is not a git repository.
        host: The code host.
        configured: Whether a credential is stored for the repository.
        username: The user it signs in as, when one is stored.
        source_is_https: Whether the application's source is an http(s) URL,
            the only kind a credential signs in to.
    """

    domain: str
    repository: str | None
    host: str | None
    configured: bool
    username: str | None
    source_is_https: bool


def _check_username(username: str) -> str:
    """
    Check the user of a credential.

    Args:
        username: As given.

    Returns:
        It, stripped.

    Raises:
        ValidationError: When it is empty, too long, or holds a colon or a
            control character, which would change what the header says.
    """
    text = (username or "").strip()
    if not text or len(text) > MAX_USERNAME or ":" in text or any(ord(c) < 32 for c in text):
        raise ValidationError(
            "That is not a user name for a repository credential",
            details="Give the user the code host expects: no colon, no spaces at the ends.",
            field="username",
        )
    return text


def _check_secret(secret: str) -> str:
    """
    Check the token of a credential.

    Args:
        secret: As given.

    Returns:
        It, stripped.

    Raises:
        ValidationError: When it is empty, too long or spans lines.
    """
    text = (secret or "").strip()
    if not text or len(text) > MAX_SECRET or any(ord(c) < 32 for c in text):
        raise ValidationError(
            "That is not a token",
            details="Paste the token on one line.",
            field="secret",
        )
    return text


class GitCredentialManager:
    """
    Store, show and remove the HTTPS credential of a repository, and hand it to git.

    Args:
        store: The store; the process-wide one by default.
        secrets: The secret store; one beside the store by default.
        actor: Who acts, for the audit trail.
    """

    def __init__(
        self,
        *,
        store: NoustStore | None = None,
        secrets: SecretStore | None = None,
        actor: audit.Actor | None = None,
    ) -> None:
        self._store = store
        self._secrets = secrets
        self.actor = actor

    @property
    def store(self) -> NoustStore:
        """The store, resolved on first use."""
        if self._store is None:
            self._store = get_store()
        return self._store

    @property
    def secrets(self) -> SecretStore:
        """The secret store the credentials are in."""
        if self._secrets is None:
            self._secrets = SecretStore()
        return self._secrets

    def _app(self, domain: str) -> App:
        """
        Find the application a command is about.

        Args:
            domain: Its domain.

        Returns:
            The application.

        Raises:
            ValidationError: When nothing is deployed there.
        """
        app = ScopedStore(store=self.store).get_app(domain)
        if app is None:
            raise ValidationError(
                f"Application not found: {domain}",
                details="Run 'noust list' to see what is deployed.",
                field="domain",
            )
        return app

    def _secret_name(self, identity: str) -> str:
        """
        Name the secret of a repository.

        Args:
            identity: ``host/owner/repo``.

        Returns:
            The secret's name.

        Raises:
            ValidationError: When the repository's name cannot be a file name.
        """
        name = f"{NAMESPACE}/{identity}"
        try:
            self.secrets.path(name)
        except ConfigError as exc:
            raise ValidationError(
                f"{identity} cannot hold a credential",
                details="Its name has a part that is not a simple name.",
            ) from exc
        return name

    def _identity(self, app: App) -> str:
        """
        Name the repository an application is built from.

        Args:
            app: The application.

        Returns:
            Its identity.

        Raises:
            ValidationError: When its source is not a git repository.
        """
        identity = repository_identity(app.source)
        if identity is None:
            raise ValidationError(
                f"{app.domain} is not built from a git repository",
                details="A credential is for a repository an application is cloned from.",
                field="domain",
            )
        return identity

    def view(self, domain: str) -> CredentialView:
        """
        Say whether an application's repository has a credential.

        Args:
            domain: The application.

        Returns:
            The view; never the secret.

        Raises:
            ValidationError: When the application does not exist.
        """
        app = self._app(domain)
        identity = repository_identity(app.source)
        username = None
        if identity is not None:
            stored = self._read(self._secret_name(identity))
            username = stored.get("username") if stored else None
        return CredentialView(
            domain=app.domain,
            repository=identity,
            host=identity.partition("/")[0] if identity else None,
            configured=username is not None,
            username=username,
            source_is_https=(app.source or "").strip().lower().startswith(("https://", "http://")),
        )

    def put(self, domain: str, username: str, secret: str) -> CredentialView:
        """
        Keep the credential of an application's repository, replacing any.

        Args:
            domain: The application.
            username: The user to sign in as.
            secret: The token.

        Returns:
            The view.

        Raises:
            ValidationError: When the application has no git repository or a
                value is not acceptable.
        """
        app = self._app(domain)
        identity = self._identity(app)
        user = _check_username(username)
        token = _check_secret(secret)
        self.secrets.write_json(self._secret_name(identity), {"username": user, "secret": token})
        audit.record(
            "apps.git_credential.set",
            actor=self.actor,
            target=f"app:{app.domain}",
            details={"repository": identity, "username": user},
        )
        return self.view(domain)

    def remove(self, domain: str) -> bool:
        """
        Forget the credential of an application's repository.

        Args:
            domain: The application.

        Returns:
            True when there was one.

        Raises:
            ValidationError: When the application has no git repository.
        """
        app = self._app(domain)
        identity = self._identity(app)
        name = self._secret_name(identity)
        existed = self._read(name) is not None
        self.secrets.delete(name)
        if existed:
            audit.record(
                "apps.git_credential.remove",
                actor=self.actor,
                target=f"app:{app.domain}",
                details={"repository": identity},
            )
        return existed

    def _read(self, name: str) -> dict[str, str] | None:
        """
        Read a stored credential.

        Args:
            name: The secret's name.

        Returns:
            ``{"username", "secret"}``, or None when none is stored or the
            file is not one this module wrote.
        """
        stored = self.secrets.read_json(name)
        user, token = stored.get("username"), stored.get("secret")
        if not isinstance(user, str) or not isinstance(token, str):
            return None
        return {"username": user, "secret": token}

    def any_stored(self) -> bool:
        """
        Tell whether any credential exists, without reading one.

        Cheap, so every git command can ask before it works out a repository.

        Returns:
            True when the credentials directory has anything in it.
        """
        directory: Path = self.secrets.root / NAMESPACE
        return directory.is_dir() and any(directory.iterdir())

    def auth_for(self, url: str) -> dict[str, str]:
        """
        Build the git environment that signs in to a repository, when it has a credential.

        Args:
            url: The URL git is about to use.

        Returns:
            The variables to add to git's environment (see
            :func:`~noust.managers.source_manager.split_url_credentials`); empty
            for a URL that is not http(s), already carries a credential, or
            names a repository without one.
        """
        # Imported here: this module is imported by the source manager's callers.
        from noust.managers.source_manager import split_url_credentials

        parts = urlsplit(url.split("#", 1)[0])
        if parts.scheme.lower() not in ("http", "https") or "@" in parts.netloc:
            return {}
        identity = repository_identity(url)
        if identity is None:
            return {}
        try:
            stored = self._read(self._secret_name(identity))
        except ValidationError:
            return {}
        if stored is None:
            return {}
        userinfo = f"{quote(stored['username'], safe='')}:{quote(stored['secret'], safe='')}"
        _bare, environment = split_url_credentials(
            f"{parts.scheme}://{userinfo}@{parts.netloc}{parts.path}"
        )
        return environment


def dumps(view: CredentialView) -> str:
    """
    Spell a credential view as JSON, for the command line.

    Args:
        view: The view.

    Returns:
        JSON text with no secret in it.
    """
    return json.dumps(
        {
            "domain": view.domain,
            "repository": view.repository,
            "host": view.host,
            "configured": view.configured,
            "username": view.username,
            "source_is_https": view.source_is_https,
        }
    )
