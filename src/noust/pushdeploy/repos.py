# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Deploying an application with ``git push``: turn it on, say who may push, turn it off.

``enable`` gives an application a bare repository (owned by the push account), a hook of root's,
a branch to deploy and, optionally, a first key; and points the application's source at it
(``push:<domain>``, :mod:`noust.pushdeploy.source`). From then on a push to that branch updates
the application through the same door a code host's webhook uses, so the instant way back, the
health gate, the deploy queue and the delivery log are the ones every deployment has. ``disable``
puts the application's source and branch back as they were.

Only an application on the release layout can be turned on: the repository cache a push fills
exists there, and an in-place checkout would be rebuilt under a service that runs from it.
The code that is already deployed is not lost: the repository starts empty, and the next push
replaces what is deployed, behind the same health gate, with the previous release one command
away.
"""

from __future__ import annotations

import builtins
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from noust.core import audit, push_store, webhook_deliveries
from noust.core.applock import app_lock
from noust.core.exceptions import (
    CommandError,
    NoustError,
    PrerequisiteError,
    SourceError,
    ValidationError,
)
from noust.core.fs import FileSystem, get_fs
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import App, NoustStore, get_store, is_compose_backed
from noust.deployers.helpers.layout import RELEASES
from noust.fleet.authorize import TUNNEL_KEYS_DIR
from noust.integrations import webhook as webhook_service
from noust.managers.source_manager import validate_git_ref
from noust.pushdeploy import hook
from noust.pushdeploy.host import PUSH_USER, PushHost
from noust.pushdeploy.keys import KeyEntry, PushKeys, parse_key, validate_label
from noust.pushdeploy.source import repository_path
from noust.validators.domain import validate_domain
from noust.validators.source import push_source

#: The branch deployed when nothing says otherwise.
DEFAULT_BRANCH = "main"

#: Deadline of the quick local commands.
TIMEOUT = 60

#: Mode of the hook, and of its settings (readable by the push account's group alone).
HOOK_MODE = 0o755
CONFIG_MODE = 0o640

#: Settings written into every repository: objects are checked as they arrive, and a force push
#: is the pusher's to make (it is their repository; the instant way back is the release).
REPOSITORY_SETTINGS = (("receive.fsckObjects", "true"), ("transfer.fsckObjects", "true"))


@dataclass
class PushInfo:
    """
    What there is to say about an application's push deployment.

    Attributes:
        domain: The application.
        enabled: Whether it deploys from push.
        branch: The branch a push deploys.
        repository: The bare repository's path.
        remote: The remote to add (``noust-git@host:domain.git``).
        keys: Who may push.
        hook_ok: Whether the hook and its settings are as Noust writes them.
        host_ready: Whether the server takes pushes at all (``noust push setup``).
        problems: What stops a push from deploying, with the command that fixes it.
        last_push_at: When the console last received a push for it.
        enabled_at: When push was turned on.
    """

    domain: str
    enabled: bool
    branch: str | None = None
    repository: str | None = None
    remote: str | None = None
    keys: list[KeyEntry] = field(default_factory=list)
    hook_ok: bool = False
    host_ready: bool = False
    problems: list[str] = field(default_factory=list)
    last_push_at: str | None = None
    enabled_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The description as plain data.
        """
        return {
            "domain": self.domain,
            "enabled": self.enabled,
            "branch": self.branch,
            "repository": self.repository,
            "remote": self.remote,
            "keys": [key.to_dict() for key in self.keys],
            "hook_ok": self.hook_ok,
            "host_ready": self.host_ready,
            "problems": self.problems,
            "last_push_at": self.last_push_at,
            "enabled_at": self.enabled_at,
        }


def local_console_url() -> str:
    """
    Find where the console listens, as the hook reaches it: its loopback address.

    Returns:
        ``http://127.0.0.1:<port>`` (``https`` when the console serves TLS), read the way
        ``noust web expose-hooks`` reads it from the service.
    """
    from noust.cli.commands.web import _console_upstream

    port, scheme = _console_upstream(None)
    return f"{scheme}://127.0.0.1:{port}"


def server_host(domain: str) -> str:
    """
    Name this server for a remote's URL.

    Args:
        domain: The application, whose name points at this server in the usual case.

    Returns:
        The domain.
    """
    return domain


class PushRepos:
    """
    The push deployments of this server.

    Args:
        runner: Runs git and chown.
        fs: The filesystem seam.
        store: The store.
        host: The push account and its sshd block.
        keys: The key file.
        console_url: Where the hook reaches the console, asked when it is written.
        python: The interpreter the hook's first line names.
        actor: Who acts, for the records.
    """

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        store: NoustStore | None = None,
        host: PushHost | None = None,
        keys: PushKeys | None = None,
        console_url: Callable[[], str] | None = None,
        python: str = "/usr/bin/python3",
        actor: str | None = None,
    ) -> None:
        self.runner = runner or get_runner()
        self.fs = fs or get_fs()
        self._store = store
        self.host = host or PushHost(runner=self.runner, fs=self.fs)
        self.keys = keys or PushKeys(PUSH_USER, TUNNEL_KEYS_DIR)
        self._console_url = console_url or local_console_url
        self._python = python
        self.actor = actor

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    # -- Reading ------------------------------------------------------------------------

    def repository(self, domain: str) -> Path:
        """
        Args:
            domain: The application.

        Returns:
            Its bare repository's path.
        """
        return repository_path(validate_domain(domain), self.host.home)

    def remote_url(self, domain: str, host: str | None = None, port: int = 22) -> str:
        """
        Spell the remote a developer adds.

        Args:
            domain: The application.
            host: The server's name; the application's domain when None.
            port: The port sshd listens on.

        Returns:
            ``noust-git@host:domain.git``, or the ``ssh://`` form when sshd is not on 22.
            The path is for the eye: the key's forced command decides what a push reaches.
        """
        name = host or server_host(domain)
        if port == 22:
            return f"{PUSH_USER}@{name}:{domain}.git"
        return f"ssh://{PUSH_USER}@{name}:{port}/{domain}.git"

    def describe(self, domain: str, *, host: str | None = None) -> PushInfo:
        """
        Say how an application's push deployment stands.

        Args:
            domain: The application.
            host: The server's name for the remote's URL.

        Returns:
            What there is to say. Read-only.
        """
        domain = validate_domain(domain)
        record = push_store.get(domain, store=self.store)
        status = self.host.status()
        if record is None:
            return PushInfo(domain=domain, enabled=False, host_ready=status.ready)
        problems = list(status.problems)
        hook_ok = self._hook_ok(domain, record.branch, problems)
        app = self.store.get_app(domain)
        last = None
        if app is not None and app.id is not None:
            last = webhook_deliveries.summarize(app.id, self.store).last_push
        return PushInfo(
            domain=domain,
            enabled=True,
            branch=record.branch,
            repository=str(self.repository(domain)),
            remote=self.remote_url(domain, host, status.ssh_port),
            keys=self.keys.list(domain),
            hook_ok=hook_ok,
            host_ready=status.ready,
            problems=problems,
            last_push_at=last.received_at if last is not None else None,
            enabled_at=record.enabled_at,
        )

    def list(self) -> list[PushInfo]:
        """
        Returns:
            Every application that deploys from push.
        """
        return [self.describe(record.domain) for record in push_store.list_all(store=self.store)]

    def _hook_ok(self, domain: str, branch: str, problems: builtins.list[str]) -> bool:
        """
        Check the hook and its settings are what Noust writes.

        Args:
            domain: The application.
            branch: The branch recorded.
            problems: Where to say what is wrong.

        Returns:
            Whether the hook is right.
        """
        folder = self.repository(domain) / "hooks"
        script, config_file = folder / hook.HOOK_NAME, folder / hook.CONFIG_NAME
        if not self.repository(domain).is_dir():
            problems.append(f"The repository is missing: noust push refresh {domain}")
            return False
        if not script.is_file() or script.read_text(encoding="utf-8") != hook.render_hook(
            self._python
        ):
            problems.append(f"The hook is not the one Noust writes: noust push refresh {domain}")
            return False
        config = (
            hook.read_config(config_file.read_text(encoding="utf-8"))
            if config_file.is_file()
            else None
        )
        if config is None or config.branch != branch:
            problems.append(f"The hook's settings are missing: noust push refresh {domain}")
            return False
        return True

    # -- Turning it on ------------------------------------------------------------------

    def enable(
        self,
        domain: str,
        *,
        branch: str | None = None,
        key: str | None = None,
        label: str | None = None,
        host: str | None = None,
    ) -> PushInfo:
        """
        Make an application deploy from ``git push``.

        Args:
            domain: The application.
            branch: The branch a push deploys: the one the application is pinned to, else
                ``main``.
            key: A public key that may push, as its ``.pub`` line.
            label: The key's name; required with a key.
            host: The server's name for the remote's URL.

        Returns:
            The application's push deployment.

        Raises:
            NoustError: The application is unknown, is not on the release layout, runs as a
                stack or a container, or the server is not set up for pushes.
            ValidationError: The branch, the key or its label are not valid.
        """
        domain = validate_domain(domain)
        app = self.store.get_app(domain)
        if app is None:
            raise NoustError(
                f"Application not found: {domain}",
                details="Create it first (noust create), then turn push on.",
            )
        self._refuse_unsuitable(app)
        wanted = validate_git_ref(branch or app.branch or DEFAULT_BRANCH)
        pusher = parse_key(key) if key is not None else None
        pushing_label = validate_label(label) if pusher is not None and label else None
        if pusher is not None and pushing_label is None:
            raise ValidationError(
                "Name the key",
                details="Give it a label: a person or a machine (laptop, ci).",
                field="label",
            )
        status = self.host.status()
        if not status.ready:
            raise PrerequisiteError(
                "This server is not set up to take git pushes",
                details="\n".join([*status.problems, "Run 'noust push setup' as root."]),
            )
        repository = self.repository(domain)
        with app_lock(domain, "push enable"):
            self._make_repository(repository, wanted)
            record = push_store.get(domain, store=self.store)
            previous_source = record.previous_source if record else app.source
            previous_branch = record.previous_branch if record else app.branch
            self._write_hook(domain, wanted)
            if pusher is not None and pushing_label is not None:
                self.keys.add(domain, pushing_label, pusher, repository)
            push_store.put(
                domain,
                branch=wanted,
                previous_source=previous_source,
                previous_branch=previous_branch,
                enabled_by=self.actor,
                store=self.store,
            )
            # The source and the pin are the application's: written after the repository is
            # there to be read, and last, so a failure above leaves the application as it was.
            app = self.store.get_app(domain) or app
            app.source = push_source(domain)
            app.branch = wanted
            self.store.update_app(app)
            self.store.set_branch_pin(domain, wanted, by=self.actor)
        audit.record(
            "push.enable",
            target=f"app:{domain}",
            details={"branch": wanted, "key": pushing_label, "by": self.actor},
        )
        return self.describe(domain, host=host)

    def _refuse_unsuitable(self, app: App) -> None:
        """
        Refuse an application whose layout or type a push cannot deploy.

        Args:
            app: The application's row.

        Raises:
            NoustError: It is a preview, a stack, a container, or builds in place.
        """
        if app.preview_parent:
            raise ValidationError(
                f"{app.domain} is a preview",
                details="A preview is built from its pull request; turn push on for the "
                "application it belongs to.",
            )
        if is_compose_backed(app.app_type):
            raise ValidationError(
                f"{app.domain} runs as {app.app_type}, which a push cannot deploy",
                details="A Docker Compose stack and a container application are built from "
                "their own files. Push to a repository of a code host and use its webhook.",
            )
        if app.layout != RELEASES:
            raise ValidationError(
                f"{app.domain} is not on the release layout",
                details=f"A push deploys into a new release: noust app migrate {app.domain}",
            )

    def _make_repository(self, repository: Path, branch: str) -> None:
        """
        Make the bare repository, or put its default branch where it should be.

        Args:
            repository: Its path.
            branch: The branch a push deploys, which ``HEAD`` names.

        Raises:
            SourceError: The path is a link, or git failed.
        """
        if repository.is_symlink():
            raise SourceError(
                f"Refusing to use the symlink {repository}",
                details="A push repository has to be a real directory.",
            )
        env = {"GIT_CONFIG_NOSYSTEM": "1", "HOME": str(self.host.home)}
        try:
            if not (repository / "HEAD").is_file():
                self.runner.run(
                    ["git", "init", "--bare", "--quiet", "--", str(repository)],
                    user=PUSH_USER,
                    env=env,
                    timeout=TIMEOUT,
                    check=True,
                )
                for name, value in REPOSITORY_SETTINGS:
                    self.runner.run(
                        ["git", "--git-dir", str(repository), "config", name, value],
                        user=PUSH_USER,
                        env=env,
                        timeout=TIMEOUT,
                        check=True,
                    )
            self.runner.run(
                [
                    "git",
                    "--git-dir",
                    str(repository),
                    "symbolic-ref",
                    "HEAD",
                    f"refs/heads/{branch}",
                ],
                user=PUSH_USER,
                env=env,
                timeout=TIMEOUT,
                check=True,
            )
        except CommandError as exc:
            raise SourceError(
                f"Could not make the push repository {repository}",
                details="git must be installed and the account noust-git must own "
                f"{self.host.home} (noust push setup).",
                output=exc.details,
            ) from exc

    def _write_hook(self, domain: str, branch: str) -> None:
        """
        Write the hook and its settings, owned by root.

        The hooks directory is made again, root's: whatever the repository was created with
        (git's samples) goes, and nothing the push account owns can be swapped for the hook.

        Args:
            domain: The application.
            branch: The branch the hook reports.

        Raises:
            SourceError: The application has no webhook secret to sign with and none could be
                made, or the files could not be given their owner.
        """
        secret = self.store.get_webhook_secret(domain) or webhook_service.mint_secret(domain)
        config = hook.HookConfig(
            domain=domain,
            url=f"{self._console_url().rstrip('/')}/hooks/deploy/{domain}",
            secret=secret,
            branch=branch,
        )
        folder = self.repository(domain) / "hooks"
        script, settings = folder / hook.HOOK_NAME, folder / hook.CONFIG_NAME
        self.fs.remove_tree(folder)
        self.fs.make_dir(folder, mode=0o755)
        self.fs.chmod(folder, 0o755)
        self.fs.write_text(script, hook.render_hook(self._python), mode=HOOK_MODE)
        self.fs.chmod(script, HOOK_MODE)
        self.fs.write_text(settings, config.render() + "\n", mode=CONFIG_MODE)
        self.fs.chmod(settings, CONFIG_MODE)
        try:
            self.runner.run(
                ["chown", f"root:{PUSH_USER}", str(settings)], timeout=TIMEOUT, check=True
            )
        except CommandError as exc:
            raise SourceError(
                f"Could not give the hook's settings to {PUSH_USER}'s group", details=exc.details
            ) from exc

    def refresh(self, domain: str) -> PushInfo:
        """
        Write the hook and its settings again.

        For a webhook secret that was rotated, a console that moved to another port, or a hook
        that was edited by hand.

        Args:
            domain: The application.

        Returns:
            The application's push deployment.

        Raises:
            NoustError: The application does not deploy from push.
        """
        domain = validate_domain(domain)
        record = push_store.get(domain, store=self.store)
        if record is None:
            raise ValidationError(
                f"{domain} does not deploy from git push",
                details=f"Turn it on with: noust push enable {domain}",
            )
        with app_lock(domain, "push refresh"):
            self._make_repository(self.repository(domain), record.branch)
            self._write_hook(domain, record.branch)
        return self.describe(domain)

    def refresh_if_enabled(self, domain: str) -> bool:
        """
        Write the hook again for an application that deploys from push; nothing for another.

        Called when the application's webhook secret changes, so that the hook keeps signing
        with the one the console checks.

        Args:
            domain: The application.

        Returns:
            Whether a hook was rewritten.
        """
        record = push_store.get(domain, store=self.store)
        if record is None:
            return False
        self._write_hook(domain, record.branch)
        return True

    # -- Turning it off -----------------------------------------------------------------

    def disable(self, domain: str, *, delete_repository: bool = False) -> PushInfo:
        """
        Make an application stop deploying from ``git push``.

        Its source and pinned branch go back to what they were before push was turned on. The
        keys that could push are removed, and the repository is kept (the code is the
        pusher's) unless asked.

        Args:
            domain: The application.
            delete_repository: Delete the bare repository too.

        Returns:
            The application's push deployment after: not enabled.

        Raises:
            NoustError: The application does not deploy from push.
        """
        domain = validate_domain(domain)
        record = push_store.get(domain, store=self.store)
        if record is None:
            raise ValidationError(
                f"{domain} does not deploy from git push",
                details="There is nothing to turn off.",
            )
        with app_lock(domain, "push disable"):
            app = self.store.get_app(domain)
            if app is not None:
                if record.previous_source:
                    app.source = record.previous_source
                app.branch = record.previous_branch
                self.store.update_app(app)
                self.store.set_branch_pin(domain, record.previous_branch, by=self.actor)
            removed = self.keys.remove_all(domain)
            push_store.remove(domain, store=self.store)
            if delete_repository:
                self.fs.remove_tree(self.repository(domain))
        audit.record(
            "push.disable",
            target=f"app:{domain}",
            details={
                "keys_removed": removed,
                "repository_deleted": delete_repository,
                "by": self.actor,
            },
        )
        return PushInfo(domain=domain, enabled=False, host_ready=self.host.status().ready)

    def forget(self, domain: str) -> None:
        """
        Remove everything of an application's push deployment, for an application being deleted.

        Args:
            domain: The application.
        """
        if (
            push_store.get(domain, store=self.store) is None
            and not self.repository(domain).exists()
        ):
            return
        self.keys.remove_all(domain)
        push_store.remove(domain, store=self.store)
        self.fs.remove_tree(self.repository(domain))

    # -- Keys ---------------------------------------------------------------------------

    def add_key(self, domain: str, label: str, key: str) -> builtins.list[KeyEntry]:
        """
        Allow a key to push to an application's repository.

        Args:
            domain: The application.
            label: The key's name.
            key: The public key, as its ``.pub`` line.

        Returns:
            The application's keys.

        Raises:
            NoustError: The application does not deploy from push.
            ValidationError: The key or its label are not valid, or the key is already used.
        """
        domain = validate_domain(domain)
        if push_store.get(domain, store=self.store) is None:
            raise ValidationError(
                f"{domain} does not deploy from git push",
                details=f"Turn it on with: noust push enable {domain}",
            )
        self.keys.add(domain, validate_label(label), parse_key(key), self.repository(domain))
        audit.record(
            "push.key",
            target=f"app:{domain}",
            details={"action": "add", "label": label, "by": self.actor},
        )
        return self.keys.list(domain)

    def remove_key(self, domain: str, which: str) -> builtins.list[KeyEntry]:
        """
        Take a key's permission to push away.

        Args:
            domain: The application.
            which: The key's label or fingerprint.

        Returns:
            The application's keys that are left.

        Raises:
            ValidationError: Nothing is named, or no key of the application matches.
        """
        domain = validate_domain(domain)
        if self.keys.remove(domain, which) == 0:
            raise ValidationError(
                f"{domain} has no key {which}",
                details=f"'noust push key list {domain}' lists them.",
                field="label",
            )
        audit.record(
            "push.key",
            target=f"app:{domain}",
            details={"action": "remove", "label": which, "by": self.actor},
        )
        return self.keys.list(domain)


def ssh_config_snippet(domain: str, host: str, port: int = 22) -> str:
    """
    Write the lines of ``~/.ssh/config`` that make one key push to one application.

    A key serves one application (sshd reads the first line a key has), so a developer who
    pushes to several uses a key for each and says which in their ssh configuration.

    Args:
        domain: The application.
        host: The server's name.
        port: The port sshd listens on.

    Returns:
        The block.
    """
    alias = f"noust-{domain}"
    lines = [
        f"Host {alias}",
        f"    HostName {host}",
        f"    User {PUSH_USER}",
        *([f"    Port {port}"] if port != 22 else []),
        f"    IdentityFile ~/.ssh/{alias}",
        "    IdentitiesOnly yes",
    ]
    return "\n".join(lines) + "\n"
