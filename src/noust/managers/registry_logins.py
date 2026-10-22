# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Logging in to container registries, for the applications that pull from them.

An operator adds an account once (``noust registry add``) and says which
applications use it. Whatever then runs ``docker`` for one of them - a Compose
stack's build, pull and up, a container application's image build - does it with
the account logged in to a configuration directory of its own for the length of
the command (:meth:`RegistryManager.session`), never in root's
``~/.docker/config.json``: Docker writes the credential it is given into that
file in clear, and a login that outlives the command is a credential that outlives
the application.

``docker login`` is run through the command runner with the password on standard
input, so it is never in an argument; the runner is also given it as a secret to
redact from whatever is logged. An Amazon ECR account is a pair of AWS keys, not
a password: its token expires in twelve hours, so each login asks for a fresh one
(``aws ecr get-login-password``) with the keys in the environment.
"""

from __future__ import annotations

import secrets as token
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from noust.core import audit
from noust.core.exceptions import DeploymentError, DockerError, NoustError, ValidationError
from noust.core.fs import SECRET_DIR_MODE, FileSystem, get_fs
from noust.core.registry_store import (
    RegistryLogin,
    RegistryStore,
    check_login,
    check_name,
    secret_name_for,
)
from noust.core.runner import CommandRunner, get_runner
from noust.core.secrets import SecretStore

#: Seconds ``docker login`` and ``aws ecr get-login-password`` may take.
LOGIN_TIMEOUT = 60


class RegistryManager:
    """Adds, tests and uses the registry accounts of this server."""

    def __init__(
        self,
        *,
        store: RegistryStore | None = None,
        secrets: SecretStore | None = None,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
    ) -> None:
        """
        Args:
            store: The accounts; the store's by default.
            secrets: Where passwords are kept; the secret store by default.
            runner: What runs docker; the process-wide runner by default.
            fs: The filesystem the session directories go through.
        """
        self.accounts = store or RegistryStore()
        self.secrets = secrets or SecretStore()
        self._runner = runner
        self._fs = fs

    @property
    def runner(self) -> CommandRunner:
        """The runner docker goes through."""
        return self._runner if self._runner is not None else get_runner()

    @property
    def fs(self) -> FileSystem:
        """The filesystem session directories go through."""
        return self._fs if self._fs is not None else get_fs()

    # -- Managing the accounts -------------------------------------------------

    def add(
        self,
        name: str,
        kind: str,
        username: str,
        password: str,
        *,
        server: str | None = None,
        actor: str | None = None,
    ) -> RegistryLogin:
        """
        Add an account.

        Args:
            name: What to call it.
            kind: ``dockerhub``, ``ghcr``, ``gitlab``, ``ecr``, ``acr``, ``gar`` or
                ``custom``.
            username: The account (for ECR, the AWS access key id).
            password: Its password or token (for ECR, the AWS secret access key).
                Kept in the secret store, never in the row.
            server: The registry host; the kind's own when it has one.
            actor: Who added it.

        Returns:
            The account.

        Raises:
            ValidationError: A value is not valid, the account exists, or the
                password is empty.
        """
        check_name(name)
        host, user = check_login(kind, server, username)
        if not password.strip():
            raise ValidationError(
                "The password is empty",
                details="Give the registry's token or password; it is read from standard input "
                "or the NOUST_REGISTRY_PASSWORD variable, never an argument.",
                field="password",
            )
        added = self.accounts.add(name, kind, host, user, created_by=actor)
        try:
            self.secrets.write(secret_name_for(name), password)
        except (NoustError, OSError):
            # An account without its password is a login that can never work.
            self.accounts.remove(name)
            raise
        audit.record(
            "registry.add", target=f"registry:{name}", details={"kind": kind, "server": host}
        )
        return added

    def update(
        self,
        name: str,
        *,
        kind: str | None = None,
        server: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> RegistryLogin:
        """
        Change an account.

        Args:
            name: Its name.
            kind: A new kind.
            server: A new host.
            username: A new user.
            password: A new password; the old one is kept when None.

        Returns:
            The account as it is now. A change forgets the last test.

        Raises:
            NoustError: There is no such account.
            ValidationError: A value is not valid.
        """
        if password is not None and not password.strip():
            raise ValidationError("The password is empty", field="password")
        updated = self.accounts.update(name, kind=kind, server=server, username=username)
        if password is not None:
            self.secrets.write(secret_name_for(name), password)
        audit.record(
            "registry.update",
            target=f"registry:{name}",
            details={
                "changed": [
                    key
                    for key, value in (
                        ("kind", kind),
                        ("server", server),
                        ("username", username),
                        ("password", password),
                    )
                    if value is not None
                ]
            },
        )
        return updated

    def remove(self, name: str) -> list[str]:
        """
        Remove an account, its password and its assignments.

        Args:
            name: Its name.

        Returns:
            The applications that used it: they pull anonymously from now on.

        Raises:
            NoustError: There is no such account.
        """
        found = self.accounts.get(name)
        if found is None:
            raise NoustError(
                f"No registry login named {name}", details="List them with: noust registry list"
            )
        self.accounts.remove(name)
        self.secrets.delete(secret_name_for(name))
        audit.record(
            "registry.remove", target=f"registry:{name}", details={"apps": list(found.apps)}
        )
        return list(found.apps)

    def attach(self, domain: str, names: Sequence[str], *, actor: str | None = None) -> None:
        """
        Set the accounts an application pulls and builds with.

        Args:
            domain: The application.
            names: The accounts' names; empty for none.
            actor: Who set them.

        Raises:
            NoustError: The application or an account is unknown.
        """
        self.accounts.assign(domain, list(names), assigned_by=actor)
        audit.record("registry.attach", target=f"app:{domain}", details={"logins": list(names)})

    # -- Using them -------------------------------------------------------------

    def _login(self, login: RegistryLogin, config: str, env_base: dict[str, str]) -> None:
        """
        Log one account in to a configuration directory.

        Args:
            login: The account.
            config: The directory ``DOCKER_CONFIG`` names.
            env_base: The environment of the login commands.

        Raises:
            DeploymentError: The password is missing from the secret store.
            DockerError: The registry did not accept the account.
        """
        password = self.secrets.read(login.secret_name)
        if not password:
            raise DeploymentError(
                f"The password of the registry login {login.name} is missing",
                details=f"Give it again: noust registry update {login.name} --password-stdin",
            )
        username = login.username
        if login.kind == "ecr":
            password = self._ecr_token(login, username, password)
            username = "AWS"
        done = self.runner.run(
            ["docker", "login", login.server, "--username", username, "--password-stdin"],
            input=password,
            env=env_base,
            secrets=[password],
            timeout=LOGIN_TIMEOUT,
        )
        if not done.success:
            raise DockerError(
                f"{login.server} did not accept the login {login.name}",
                details=(done.stderr or done.stdout).replace(password, "***").strip(),
            )

    def _ecr_token(self, login: RegistryLogin, key_id: str, secret_key: str) -> str:
        """
        Trade an AWS key pair for the password ECR accepts, good for twelve hours.

        Args:
            login: The account.
            key_id: The AWS access key id.
            secret_key: The AWS secret access key.

        Returns:
            The token.

        Raises:
            DockerError: The AWS CLI is missing or refused the keys.
        """
        region = login.region
        if region is None or not self.runner.exists("aws"):
            raise DockerError(
                "Amazon ECR logins need the AWS CLI on this server",
                details="Install it (awscli) and test the login again.",
            )
        done = self.runner.run(
            ["aws", "ecr", "get-login-password", "--region", region],
            env={"AWS_ACCESS_KEY_ID": key_id, "AWS_SECRET_ACCESS_KEY": secret_key},
            secrets=[secret_key],
            timeout=LOGIN_TIMEOUT,
        )
        text = done.stdout.strip()
        if not done.success or not text:
            raise DockerError(
                f"AWS did not give a token for {login.server}",
                details=(done.stderr or done.stdout).replace(secret_key, "***").strip(),
            )
        return text

    @contextmanager
    def session(self, domain: str) -> Iterator[dict[str, str]]:
        """
        Log in the accounts an application uses, for the length of a block.

        Args:
            domain: The application.

        Yields:
            The environment docker commands are run with: see :meth:`session_for`.

        Raises:
            DeploymentError: A password is missing.
            DockerError: A registry did not accept its account.
        """
        with self.session_for(
            domain, [login.name for login in self.accounts.assigned_to(domain)]
        ) as env:
            yield env

    @contextmanager
    def session_for(self, domain: str, names: Sequence[str]) -> Iterator[dict[str, str]]:
        """
        Log in the named accounts, for the length of a block.

        Args:
            domain: The application, for the message of a failure.
            names: The accounts to log in; none yields an empty environment.

        Yields:
            The environment docker commands are run with: empty when the
            application uses no account, otherwise ``DOCKER_CONFIG`` naming a
            private directory that holds the logins. The directory is removed when
            the block ends, however it ends.

        Raises:
            DeploymentError: A password is missing.
            DockerError: A registry did not accept its account.
        """
        logins = []
        for name in names:
            found = self.accounts.get(name)
            if found is None:
                raise NoustError(
                    f"No registry login named {name}",
                    details=f"{domain} was asked to use it. List them with: noust registry list",
                )
            logins.append(found)
        if not logins:
            yield {}
            return
        config = self.secrets.root / "registry-sessions" / token.token_hex(8)
        self.fs.make_dir(config, mode=SECRET_DIR_MODE, parents=True)
        env = {"DOCKER_CONFIG": str(config)}
        try:
            for login in logins:
                self._login(login, str(config), env)
            yield env
        finally:
            self.fs.remove_tree(config)

    def test(self, name: str) -> RegistryLogin:
        """
        Try an account against its registry, in a private configuration.

        Args:
            name: Its name.

        Returns:
            The account, with how the test went.

        Raises:
            NoustError: There is no such account.
        """
        login = self.accounts.get(name)
        if login is None:
            raise NoustError(
                f"No registry login named {name}", details="List them with: noust registry list"
            )
        config = self.secrets.root / "registry-sessions" / token.token_hex(8)
        self.fs.make_dir(config, mode=SECRET_DIR_MODE, parents=True)
        try:
            self._login(login, str(config), {"DOCKER_CONFIG": str(config)})
            self.accounts.record_test(name, True, None)
        except (DockerError, DeploymentError) as exc:
            self.accounts.record_test(name, False, f"{exc.message}. {exc.details}".strip())
        finally:
            self.fs.remove_tree(config)
        audit.record("registry.test", target=f"registry:{name}", details={})
        tested = self.accounts.get(name)
        if tested is None:
            raise NoustError(f"The registry login {name} disappeared while it was tested")
        return tested
