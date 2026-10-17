# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Deploy keys: an SSH key of its own for each repository an application is built from.

One key for the whole server is a key whose theft reaches every repository, and
GitHub refuses to register one deploy key on two repositories anyway. So each
repository gets its own pair: generated here (ed25519, no passphrase, because
nothing is at the keyboard when a webhook deploys) or imported, shown to the
operator as a public key to paste into the code host, assigned to the
repository, rotated and removed. ``noust setup ssh`` and the key it manages stay
as they were and remain what git uses for a repository that has no key of its
own.

**Where the secrets are.** The private half is a secret file
(``deploy-keys/<name>/key``) in the secret store: 0600 in a 0700 directory,
written through the filesystem seam, sealed with the rest on a sealed central.
It never travels in an argument or an environment variable: git is given the
*path* in ``GIT_SSH_COMMAND`` (``ssh -i <path> -o IdentitiesOnly=yes``), and on a
sealed store that path is a decrypted copy removed once git has finished.

**How git reaches a repository.** Every git network command that names an ssh
URL asks :meth:`DeployKeyManager.identities_for`; the answer is the identity to
run it with, or none, and git then does what it always did. A key is found by
the repository's address (:func:`~noust.core.repositories.repository_identity`),
because a clone, a fetch and a webhook all know that and none knows the
application.

**Host keys** are pinned in a ``known_hosts`` of Noust's own and checked
strictly, the way the fleet pins a node: the first contact with a host records
its key (``StrictHostKeyChecking=accept-new``), and a key that later differs is
refused, never accepted silently. ``noust app deploy-key forget-host`` is the
one deliberate way to accept a new one. The first contact can be taken out of
trust-on-first-use too: ``pin-host`` asks the host for its keys and records only
the one whose fingerprint the operator brings from the code host's own published
list, so a server that has not connected yet already knows who it is talking to.
Noust ships no fingerprints: a list in the code goes stale the day a code host
rotates a key, and every install behind it would refuse a host that is right.

**Rotation** never leaves a repository without a working key: the new pair
waits beside the old one, the operator adds it at the code host, and the first
git command that the code host answers with it makes it the key and removes the
old one. Until then git tries the new key first and falls back to the old.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import re
import shlex
import struct
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from noust.core import audit, sealing
from noust.core.deploy_keys import (
    GENERATED,
    IMPORTED,
    DeployKey,
    DeployKeyStore,
    validate_name,
)
from noust.core.exceptions import NoustError, SourceError, ValidationError
from noust.core.fs import SECRET_DIR_MODE, SECRET_MODE, FileSystem, is_rehearsal
from noust.core.repositories import repository_identity
from noust.core.runner import CommandError, CommandRunner, get_runner
from noust.core.secrets import SecretStore
from noust.core.store import App, NoustStore, get_store
from noust.core.teams import ScopedStore
from noust.core.utils import domain_to_app_name
from noust.managers.source_manager import SourceManager
from noust.validators.ssh import get_public_key, is_ssh_url, ssh_key_exists

logger = logging.getLogger(__name__)

#: Where a key's files are, under the secrets: ``deploy-keys/<name>/<leaf>``.
NAMESPACE = "deploy-keys"
PRIVATE = "key"
PUBLIC = "key.pub"
NEXT_PRIVATE = "next_key"
NEXT_PUBLIC = "next_key.pub"

#: Longest private key accepted on import. An ed25519 key is under 500 bytes,
#: a 4096-bit RSA key a little over 3 KB.
MAX_PRIVATE_KEY = 16 * 1024

#: How long ssh-keygen may take. It is instantaneous; this only bounds a hang.
KEYGEN_TIMEOUT = 30

#: The key types a deploy key may be.
KEY_TYPES = frozenset(
    {
        "ssh-ed25519",
        "ssh-rsa",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521",
    }
)

#: The headers of the private key formats ssh reads.
_PRIVATE_HEADERS = (
    "-----BEGIN OPENSSH PRIVATE KEY-----",
    "-----BEGIN RSA PRIVATE KEY-----",
    "-----BEGIN EC PRIVATE KEY-----",
)

#: The fingerprint of an SSH key as ``ssh-keygen -l`` prints it and code hosts publish it:
#: SHA-256 of the key, base64 without padding (43 characters).
_FINGERPRINT = re.compile(r"SHA256:[A-Za-z0-9+/]{43}")

#: How long ``ssh-keyscan`` may wait for a host to answer, in seconds.
KEYSCAN_TIMEOUT = 15

#: A host as ``known_hosts`` is asked about it: a name, or ``name:port``.
_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}(?::\d{1,5})?")
#: How ``ssh-keygen -l`` names a host pinned on another port, which is how
#: :meth:`DeployKeyManager.pinned_hosts` lists it: what a client got from the
#: list has to be what it can send back to forget the host.
_BRACKETED_HOST = re.compile(r"\[(?P<name>[A-Za-z0-9][A-Za-z0-9.-]{0,252})\]:(?P<port>\d{1,5})")

Which = Literal["current", "pending"]


def secret_name(name: str, leaf: str) -> str:
    """
    Name one of a key's secret files.

    Args:
        name: The key's validated name.
        leaf: ``key``, ``key.pub``, ``next_key`` or ``next_key.pub``.

    Returns:
        ``deploy-keys/<name>/<leaf>``.
    """
    return f"{NAMESPACE}/{validate_name(name)}/{leaf}"


@dataclass(frozen=True)
class PublicKeyLine:
    """
    An OpenSSH public key line, parsed and checked.

    Attributes:
        key_type: ``ssh-ed25519``, ``ssh-rsa`` or ``ecdsa-sha2-nistp...``.
        blob: The base64 key blob.
        comment: The comment, if there is one.
    """

    key_type: str
    blob: str
    comment: str = ""

    @property
    def fingerprint(self) -> str:
        """``SHA256:...``, as ``ssh-keygen -l`` prints it."""
        digest = hashlib.sha256(base64.b64decode(self.blob)).digest()
        return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")

    def line(self, comment: str | None = None) -> str:
        """
        Spell the key as one ``.pub`` line.

        Args:
            comment: A comment to use instead of the key's own.

        Returns:
            ``type blob [comment]``.
        """
        text = self.comment if comment is None else comment
        return f"{self.key_type} {self.blob} {text}".rstrip()


def parse_public_key_line(text: str, *, what: str = "public key") -> PublicKeyLine:
    """
    Parse one OpenSSH public key line, refusing anything that only looks like one.

    The blob is decoded and the type it declares inside must be the type the
    line declares.

    Args:
        text: ``ssh-ed25519 AAAA... [comment]``.
        what: What the key is, for the messages.

    Returns:
        The key.

    Raises:
        ValidationError: When it is not one valid public key.
    """
    stripped = (text or "").strip()
    if not stripped or len(stripped) > 2048 or any(ord(char) < 32 for char in stripped):
        raise ValidationError(f"The {what} is not one line of an OpenSSH public key")
    fields = stripped.split(None, 2)
    if len(fields) < 2 or fields[0] not in KEY_TYPES:
        raise ValidationError(
            f"The {what} is not a key type Noust uses",
            details=f"Expected one of: {', '.join(sorted(KEY_TYPES))}.",
        )
    key_type, blob = fields[0], fields[1]
    try:
        raw = base64.b64decode(blob, validate=True)
        (length,) = struct.unpack(">I", raw[:4])
        declared = raw[4 : 4 + length].decode("ascii")
    except (binascii.Error, struct.error, UnicodeDecodeError, ValueError) as exc:
        raise ValidationError(f"The {what} is damaged") from exc
    if declared != key_type:
        raise ValidationError(f"The {what} says {key_type} but holds a {declared} key")
    comment = "".join(char for char in (fields[2] if len(fields) > 2 else "") if char.isprintable())
    return PublicKeyLine(key_type=key_type, blob=blob, comment=comment[:128].strip())


@dataclass(frozen=True)
class SshIdentity:
    """
    One way git can sign in to a repository's host.

    Attributes:
        key: The deploy key's name.
        which: ``current``, or ``pending`` for the key a rotation waits to
            put in place.
        command: The ``GIT_SSH_COMMAND`` that uses it.
    """

    key: str
    which: Which
    command: str


class KeyUse:
    """
    The identities git may use for one repository, and what to do with the outcome.

    Args:
        manager: The manager that made it.
        identities: The identities in the order to try them: a pending key
            first, so the first command that the code host answers with it
            completes the rotation.
        copies: Secret names whose decrypted copies :meth:`close` removes.
    """

    def __init__(
        self, manager: DeployKeyManager, identities: list[SshIdentity], copies: list[str]
    ) -> None:
        self._manager = manager
        self.identities = identities
        self._copies = copies

    def succeeded(self, identity: SshIdentity) -> None:
        """
        Record that git reached the repository with an identity.

        A pending key that worked becomes the key, and the old one is removed.

        Args:
            identity: The one that worked.
        """
        if is_rehearsal():
            return
        if identity.which == "pending":
            self._manager._promote(identity.key)
        else:
            self._manager.keystore.touch_used(identity.key)

    def close(self) -> None:
        """Remove the decrypted copies made for ssh, if the store is sealed."""
        for name in self._copies:
            self._manager.secrets.discard_usable_copy(name)
        self._copies = []


@dataclass(frozen=True)
class DeployKeyView:
    """
    What an application's deploy key is, for the console and the CLI.

    Attributes:
        domain: The application.
        repository: Its repository's identity, or None when its source is not
            a git repository.
        source_is_ssh: Whether its source is an ssh URL, the only kind a deploy
            key signs in to.
        ssh_url: The ssh spelling of the repository, to deploy from, when the
            source is not ssh and one can be built.
        key: The key assigned to the repository, or None.
        shared_with: The other applications built from the same repository.
        server_key: The server-wide key ``noust setup ssh`` manages, which git
            uses for a repository with no key of its own, or None.
        server_key_fingerprint: Its fingerprint.
        warnings: What the operator should know.
    """

    domain: str
    repository: str | None
    source_is_ssh: bool
    ssh_url: str | None
    key: DeployKey | None
    shared_with: tuple[str, ...] = ()
    server_key: str | None = None
    server_key_fingerprint: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PinnedHost:
    """
    A host key Noust pinned the first time it connected.

    Attributes:
        host: The host as ``known_hosts`` names it.
        fingerprint: ``SHA256:...``.
        key_type: ``ED25519``, ``RSA``...
    """

    host: str
    fingerprint: str
    key_type: str


class DeployKeyManager:
    """
    Make, find, rotate and remove the deploy keys, and tell git which one to use.

    Args:
        store: The store; the process-wide one by default.
        secrets: The secret store; one beside the store by default.
        runner: The runner ssh-keygen goes through; the process-wide one by
            default.
        actor: Who acts, for the audit trail.
    """

    def __init__(
        self,
        *,
        store: NoustStore | None = None,
        secrets: SecretStore | None = None,
        runner: CommandRunner | None = None,
        actor: audit.Actor | None = None,
    ) -> None:
        self._store = store
        self._secrets = secrets
        self._runner = runner
        self.actor = actor
        self._keystore: DeployKeyStore | None = None

    # ------------------------------------------------------------ plumbing

    @property
    def store(self) -> NoustStore:
        """The store, resolved on first use."""
        if self._store is None:
            self._store = get_store()
        return self._store

    @property
    def keystore(self) -> DeployKeyStore:
        """The deploy key tables."""
        if self._keystore is None:
            self._keystore = DeployKeyStore(self.store)
        return self._keystore

    @property
    def secrets(self) -> SecretStore:
        """The secret store the private halves are in."""
        if self._secrets is None:
            self._secrets = SecretStore(runner=self._runner)
        return self._secrets

    @property
    def runner(self) -> CommandRunner:
        """The runner ssh-keygen goes through."""
        return self._runner or get_runner()

    @property
    def fs(self) -> FileSystem:
        """The filesystem every file is written through."""
        return self.secrets.fs

    def _visible(self) -> ScopedStore:
        """
        Read applications as whoever is being served.

        A key is found by repository, so one key can reach the applications of
        several teams. What the manager says about the applications around a
        key (which share it, which it reaches) is limited to the ones the
        caller sees; outside a request the scope is the instance's and nothing
        is left out.

        Returns:
            A scoped store over this manager's store, with the ambient scope.
        """
        return ScopedStore(store=self.store)

    def _audit(self, event: str, domain: str | None, key: str, **details: object) -> None:
        """
        Record a change in the audit trail.

        Args:
            event: A catalog name.
            domain: The application it was asked for, if any.
            key: The key's name.
            **details: Anything else worth keeping. Never a key.
        """
        audit.record(
            event,
            actor=self.actor,
            target=f"app:{domain}" if domain else f"deploy-key:{key}",
            details={"key": key, **{k: v for k, v in details.items() if v is not None}},
        )

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
        app = self._visible().get_app(domain)
        if app is None:
            raise ValidationError(
                f"Application not found: {domain}",
                details="Run 'noust list' to see what is deployed.",
                field="domain",
            )
        return app

    def _repository(self, app: App) -> str:
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
                details="A deploy key is for a repository an application is cloned from.",
                field="domain",
            )
        return identity

    # --------------------------------------------------------------- views

    def view(self, domain: str) -> DeployKeyView:
        """
        Describe an application's deploy key.

        Args:
            domain: The application.

        Returns:
            The view; reads only.

        Raises:
            ValidationError: When the application does not exist.
        """
        app = self._app(domain)
        identity = repository_identity(app.source)
        key = self._narrowed(self.keystore.for_repository(identity)) if identity else None
        source = app.source or ""
        ssh_source = is_ssh_url(source.split("#", 1)[0].strip())
        warnings: list[str] = []
        ssh_url = None
        if identity is not None and not ssh_source:
            host, _, path = identity.partition("/")
            ssh_url = f"git@{host}:{path}.git"
            if key is not None:
                warnings.append(
                    f"{app.domain} is cloned over https, which a deploy key does not sign in "
                    f"to. Deploy from {ssh_url} to use it."
                )
        if identity is None:
            warnings.append(f"{app.domain} is not built from a git repository.")
        shared = tuple(
            sorted(
                other.domain
                for other in self._visible().list_apps()
                if other.domain != app.domain
                and identity is not None
                and repository_identity(other.source) == identity
            )
        )
        server_key, server_fingerprint = self._server_key()
        return DeployKeyView(
            domain=app.domain,
            repository=identity,
            source_is_ssh=ssh_source,
            ssh_url=ssh_url,
            key=key,
            shared_with=shared,
            server_key=server_key,
            server_key_fingerprint=server_fingerprint,
            warnings=tuple(warnings),
        )

    def _server_key(self) -> tuple[str | None, str | None]:
        """
        Read the server-wide key ``noust setup ssh`` manages.

        Returns:
            Its public line and fingerprint, or None for both.
        """
        exists, path = ssh_key_exists()
        if not exists or path is None:
            return None, None
        line = get_public_key(path)
        if not line:
            return None, None
        try:
            parsed = parse_public_key_line(line, what="server key")
        except ValidationError:
            return line, None
        return line, parsed.fingerprint

    def _narrowed(self, key: DeployKey | None) -> DeployKey | None:
        """
        Limit a key to what the caller may know it reaches.

        Args:
            key: A key, or None.

        Returns:
            For the instance, the key as it is. For a team member, the key
            with only the repositories its visible applications are built
            from, or None when it reaches none of them: a key that exists but
            serves no application of the caller's is not the caller's to see.
        """
        if key is None:
            return None
        scoped = self._visible()
        if scoped.scope.sees_all:
            return key
        mine = {repository_identity(app.source) for app in scoped.list_apps()}
        reached = tuple(repository for repository in key.repositories if repository in mine)
        return replace(key, repositories=reached) if reached else None

    def keys(self) -> list[DeployKey]:
        """
        List the deploy keys the caller may see.

        Returns:
            The keys, by name. For a team member only those that reach the
            repository of one of its applications, each showing only those.
        """
        found = (self._narrowed(key) for key in self.keystore.keys())
        return [key for key in found if key is not None]

    def apps_of(self, key: DeployKey) -> list[str]:
        """
        Name the applications whose repository a key reaches.

        Args:
            key: The key.

        Returns:
            Their domains, sorted; those the caller sees.
        """
        reached = set(key.repositories)
        return sorted(
            app.domain
            for app in self._visible().list_apps()
            if repository_identity(app.source) in reached
        )

    # --------------------------------------------------------- generating

    def _unused_name(self, domain: str, requested: str | None) -> str:
        """
        Choose a key's name.

        Args:
            domain: The application it is for.
            requested: The name the operator gave, if any.

        Returns:
            The validated name.

        Raises:
            ValidationError: When it is invalid or taken.
        """
        name = validate_name(requested or domain_to_app_name(domain))
        if self.keystore.get(name) is not None:
            raise ValidationError(
                f"A deploy key named {name} already exists",
                details="Choose another name with --name, or assign the one that exists.",
                field="name",
            )
        return name

    def generate(self, domain: str, *, name: str | None = None) -> DeployKey:
        """
        Make an ed25519 key with no passphrase and assign it to an application's repository.

        Args:
            domain: The application.
            name: The key's name; the application's name by default.

        Returns:
            The key; its public half is what the operator gives the code host.

        Raises:
            ValidationError: When the application has no git repository, it
                already has a key, or the name is taken.
            SourceError: When ssh-keygen fails.
        """
        app = self._app(domain)
        repository = self._repository(app)
        held = self.keystore.for_repository(repository)
        if held is not None:
            raise ValidationError(
                f"{repository} already has the deploy key {held.name}",
                details=f"Rotate it with 'noust app deploy-key rotate {domain}', or remove it first.",
            )
        key_name = self._unused_name(domain, name)
        public = self._make_pair(key_name, PRIVATE, PUBLIC)
        try:
            key = self.keystore.create(
                name=key_name,
                origin=GENERATED,
                key_type=public.key_type,
                fingerprint=public.fingerprint,
                public_key=public.line(),
                created_by=self.actor.label if self.actor else None,
            )
            self.keystore.assign(key_name, repository)
        except NoustError:
            self._forget_files(key_name)
            raise
        self._audit("apps.deploy_key.generate", domain, key_name, fingerprint=public.fingerprint)
        return self.keystore.get(key_name) or key

    def _make_pair(self, name: str, private_leaf: str, public_leaf: str) -> PublicKeyLine:
        """
        Make a key pair in the secret store and read its public half.

        Args:
            name: The key's name.
            private_leaf: Where the private half goes.
            public_leaf: Where the public half goes.

        Returns:
            The public key.

        Raises:
            SourceError: When ssh-keygen fails or did not run (a rehearsal).
        """
        comment = f"noust-deploy-{name}"
        private = self.secrets.path(secret_name(name, private_leaf))
        if sealing.is_sealed(self.secrets.root):
            self._generate_sealed(name, comment, private_leaf, public_leaf)
        else:
            self._keygen(name, comment, private)
        text = self.secrets.read(secret_name(name, public_leaf))
        if text is None:
            raise SourceError(
                f"No key pair was generated for {name}",
                details="ssh-keygen did not run; this is expected in a dry run.",
            )
        return parse_public_key_line(text, what="generated key")

    def _keygen(self, name: str, comment: str, private: Path) -> None:
        """
        Run ssh-keygen, writing a pair at ``private`` and ``.pub``.

        Args:
            name: The key's name, for the messages.
            comment: The key's comment.
            private: Where the private half goes.

        Raises:
            SourceError: When ssh-keygen fails.
        """
        self.fs.make_dir(private.parent, mode=SECRET_DIR_MODE, parents=True)
        # ssh-keygen asks before overwriting and its stdin is /dev/null: a
        # half pair left by an interrupted run is removed, not asked about.
        self.fs.remove(private, missing_ok=True)
        self.fs.remove(Path(f"{private}.pub"), missing_ok=True)
        try:
            self.runner.run(
                [
                    "ssh-keygen",
                    "-q",
                    "-t",
                    "ed25519",
                    "-N",
                    "",
                    "-C",
                    comment,
                    "-f",
                    str(private),
                ],
                timeout=KEYGEN_TIMEOUT,
                check=True,
            )
        except CommandError as exc:
            raise SourceError(
                f"Could not generate the deploy key {name}", details=exc.details or exc.message
            ) from exc
        if private.is_file():
            # ssh-keygen writes the private key 0600 already; saying so makes
            # it independent of the umask.
            self.fs.chmod(private, SECRET_MODE)

    def _generate_sealed(
        self, name: str, comment: str, private_leaf: str, public_leaf: str
    ) -> None:
        """
        Make a pair for a sealed store: in a private memory-backed directory, then sealed in.

        Args:
            name: The key's name.
            comment: The key's comment.
            private_leaf: The private half's secret leaf.
            public_leaf: The public half's secret leaf.

        Raises:
            SecretsLockedError: When this process cannot seal: nothing is made.
            SourceError: When ssh-keygen fails.
        """
        root = self.secrets.root
        if not sealing.is_unlocked(root):
            raise sealing.SecretsLockedError(
                f"This central is locked; no deploy key can be made for {name}",
                details="Its secrets are sealed. Unlock it in the console, or with "
                "'noust central unlock' on the central, and try again.",
            )
        copies = sealing.plaintext_copy_dir(root)
        sealing.ensure_private_dir(copies, self.fs)
        private = copies / "keygen" / name / private_leaf
        public = Path(f"{private}.pub")
        try:
            self._keygen(name, comment, private)
            if not private.is_file():
                return
            self.secrets.write(secret_name(name, private_leaf), _read_plain(private))
            self.secrets.write(secret_name(name, public_leaf), _read_plain(public))
        finally:
            self.fs.remove(private, missing_ok=True)
            self.fs.remove(public, missing_ok=True)

    # ----------------------------------------------------------- importing

    def import_key(self, domain: str, private_key: str, *, name: str | None = None) -> DeployKey:
        """
        Take a private key the operator already has and assign it to an application's repository.

        The key must have no passphrase: nothing is there to type it when a
        webhook deploys. The public half is derived from it, never taken on
        trust.

        Args:
            domain: The application.
            private_key: The key, in the OpenSSH or PEM format.
            name: The key's name; the application's name by default.

        Returns:
            The key.

        Raises:
            ValidationError: When it is not a usable key, has a passphrase, or
                the application already has a key.
        """
        app = self._app(domain)
        repository = self._repository(app)
        held = self.keystore.for_repository(repository)
        if held is not None:
            raise ValidationError(
                f"{repository} already has the deploy key {held.name}",
                details=f"Remove it first: 'noust app deploy-key remove {domain}'.",
            )
        key_name = self._unused_name(domain, name)
        text = _normalise_private(private_key)
        leaf = secret_name(key_name, PRIVATE)
        self.secrets.write(leaf, text)
        try:
            public = self._derive_public(key_name, leaf)
            self.secrets.write(secret_name(key_name, PUBLIC), public.line() + "\n")
            key = self.keystore.create(
                name=key_name,
                origin=IMPORTED,
                key_type=public.key_type,
                fingerprint=public.fingerprint,
                public_key=public.line(),
                created_by=self.actor.label if self.actor else None,
            )
            self.keystore.assign(key_name, repository)
        except NoustError:
            self._forget_files(key_name)
            raise
        self._audit("apps.deploy_key.import", domain, key_name, fingerprint=public.fingerprint)
        return self.keystore.get(key_name) or key

    def _derive_public(self, name: str, leaf: str) -> PublicKeyLine:
        """
        Ask ssh-keygen for the public half of a stored private key.

        Args:
            name: The key's name.
            leaf: The private half's secret name.

        Returns:
            The public key.

        Raises:
            ValidationError: When the key is not valid or has a passphrase.
        """
        path = self.secrets.usable_path(leaf)
        try:
            result = self.runner.run(
                ["ssh-keygen", "-y", "-P", "", "-f", str(path)], timeout=KEYGEN_TIMEOUT
            )
        finally:
            self.secrets.discard_usable_copy(leaf)
        if not result.success:
            reason = (result.stderr or result.stdout).strip().lower()
            if "passphrase" in reason:
                raise ValidationError(
                    "The key has a passphrase",
                    details="A deploy key signs in unattended, so it cannot have one. Remove it "
                    "with 'ssh-keygen -p -N \"\" -f <key>' on a copy, then import that.",
                    field="private_key",
                )
            raise ValidationError(
                "That is not a private key ssh can read",
                details="Paste the whole file, from '-----BEGIN ... PRIVATE KEY-----' to "
                "'-----END ... PRIVATE KEY-----'.",
                field="private_key",
            )
        return parse_public_key_line(result.stdout, what=f"public half of {name}")

    # ------------------------------------------------------------ assigning

    def assign(self, domain: str, key_name: str) -> DeployKey:
        """
        Make an existing key the one that reaches an application's repository.

        For a key the code host accepts on several repositories (a machine
        user's); a key made for one repository is not accepted on another by
        GitHub.

        Args:
            domain: The application.
            key_name: The key.

        Returns:
            The key.

        Raises:
            ValidationError: When the key does not exist or the repository
                already has another.
        """
        app = self._app(domain)
        repository = self._repository(app)
        name = validate_name(key_name)
        if self._visible().scope.restricted and name not in {key.name for key in self.keys()}:
            # The same answer for a key that does not exist and one the caller may not see:
            # assigning another team's key by guessing its name would be reading its code.
            raise ValidationError(f"There is no deploy key named {name}", field="name")
        self.keystore.assign(name, repository)
        key = self.keystore.get(name)
        if key is None:
            raise ValidationError(f"There is no deploy key named {name}", field="name")
        self._audit("apps.deploy_key.assign", domain, name, repository=repository)
        return key

    # ------------------------------------------------------------- removing

    def remove(self, domain: str) -> str | None:
        """
        Take an application's repository off its deploy key; delete the key when nothing else uses it.

        Args:
            domain: The application.

        Returns:
            The key's name, or None when the repository had none.
        """
        app = self._app(domain)
        repository = self._repository(app)
        name = self.keystore.unassign(repository)
        if name is None:
            return None
        remaining = self.keystore.get(name)
        deleted = remaining is not None and not remaining.repositories
        if deleted:
            self.keystore.delete(name)
            self._forget_files(name)
        self._audit("apps.deploy_key.remove", domain, name, deleted=deleted)
        return name

    def prune(self) -> list[str]:
        """
        Delete the keys whose repositories no application is built from any more.

        Returns:
            The names deleted.
        """
        # Every application, whoever is asking: a key another team's application still uses
        # is not unused because this caller cannot see that application.
        used = {repository_identity(app.source) for app in self.store.list_apps()}
        deleted: list[str] = []
        for key in self.keystore.keys():
            for repository in key.repositories:
                if repository not in used:
                    self.keystore.unassign(repository)
            fresh = self.keystore.get(key.name)
            if fresh is not None and not fresh.repositories:
                self.keystore.delete(key.name)
                self._forget_files(key.name)
                self._audit("apps.deploy_key.remove", None, key.name, deleted=True, pruned=True)
                deleted.append(key.name)
        return deleted

    def _forget_files(self, name: str) -> None:
        """
        Delete a key's secret files, including a waiting rotation's.

        Args:
            name: The key's name.
        """
        self.secrets.delete_namespace(f"{NAMESPACE}/{validate_name(name)}")

    # ------------------------------------------------------------- rotating

    def rotate_start(self, domain: str) -> DeployKey:
        """
        Make the key that will replace an application's, keeping the old one working.

        Asking again while one waits answers with the same key, so the line
        shown to the operator stays valid until the rotation ends.

        Args:
            domain: The application.

        Returns:
            The key, with its ``pending`` half set.

        Raises:
            ValidationError: When the application has no key, or the key also
                reaches repositories the caller's teams have no application for.
            SourceError: When ssh-keygen fails.
        """
        key = self._rotatable_key_of(domain)
        if key.pending is not None:
            return key
        public = self._make_pair(key.name, NEXT_PRIVATE, NEXT_PUBLIC)
        self.keystore.set_pending(
            key.name,
            key_type=public.key_type,
            fingerprint=public.fingerprint,
            public_key=public.line(),
        )
        self._audit("apps.deploy_key.rotate", domain, key.name, fingerprint=public.fingerprint)
        return self.keystore.get(key.name) or key

    def rotate_cancel(self, domain: str) -> DeployKey:
        """
        Give up a rotation; the old key was never replaced.

        Args:
            domain: The application.

        Returns:
            The key, with no pending half.

        Raises:
            ValidationError: When the application has no key, or the key also
                reaches repositories the caller's teams have no application for.
        """
        key = self._rotatable_key_of(domain)
        if key.pending is not None:
            self.keystore.clear_pending(key.name)
            self.secrets.delete(secret_name(key.name, NEXT_PRIVATE))
            self.secrets.delete(secret_name(key.name, NEXT_PUBLIC))
            self._audit("apps.deploy_key.rotate_cancel", domain, key.name)
        return self.keystore.get(key.name) or key

    def _promote(self, name: str) -> None:
        """
        Put a waiting key in place of the old one and remove the old one.

        The new files are written over the old ones first and the row follows,
        so an interruption in between leaves a pending key that is also the
        current one, which the next command settles.

        Args:
            name: The key's name.
        """
        key = self.keystore.get(name)
        if key is None or key.pending is None:
            return
        private = self.secrets.read(secret_name(name, NEXT_PRIVATE))
        public = self.secrets.read(secret_name(name, NEXT_PUBLIC))
        if private is None or public is None:
            logger.warning(
                "The waiting deploy key of %s has lost its files; rotation dropped", name
            )
            self.keystore.clear_pending(name)
            return
        old = key.fingerprint
        self.secrets.write(secret_name(name, PRIVATE), private)
        self.secrets.write(secret_name(name, PUBLIC), public)
        self.keystore.promote_pending(name)
        self.secrets.delete(secret_name(name, NEXT_PRIVATE))
        self.secrets.delete(secret_name(name, NEXT_PUBLIC))
        self.keystore.touch_used(name)
        self._audit(
            "apps.deploy_key.promote",
            None,
            name,
            fingerprint=key.pending.fingerprint,
            replaced=old,
        )

    def _key_of(self, domain: str) -> DeployKey:
        """
        Find the key an application's repository has.

        Args:
            domain: The application.

        Returns:
            The key.

        Raises:
            ValidationError: When it has none.
        """
        app = self._app(domain)
        repository = self._repository(app)
        key = self.keystore.for_repository(repository)
        if key is None:
            raise ValidationError(
                f"{repository} has no deploy key of its own",
                details=f"Make one with: noust app deploy-key generate {domain}",
            )
        return key

    def _rotatable_key_of(self, domain: str) -> DeployKey:
        """
        Find the key an application's repository has, for a change to the key itself.

        Rotating replaces the key for every repository it reaches, and a key
        can reach the repositories of several teams (a machine user's does). A
        team member may not replace what another team's deploys sign in with.

        Args:
            domain: The application.

        Returns:
            The key.

        Raises:
            ValidationError: When it has none, or the caller sees only some of
                the repositories the key reaches.
        """
        key = self._key_of(domain)
        scoped = self._visible()
        if scoped.scope.sees_all:
            return key
        mine = {repository_identity(app.source) for app in scoped.list_apps()}
        if any(repository not in mine for repository in key.repositories):
            raise ValidationError(
                f"The deploy key {key.name} also reaches repositories outside your teams",
                details="Replacing it would stop those deployments, so only an administrator of "
                "the server can rotate it. Make a key of its own for this repository instead: "
                "remove it from the application, then generate one.",
            )
        return key

    # -------------------------------------------------------------- testing

    def verify(self, domain: str, *, source: SourceManager | None = None) -> str:
        """
        Ask the code host for the repository's branches with the key, and complete a rotation.

        This is git's own ``ls-remote``, so it is exactly the access a deploy
        uses; a waiting key that the host accepts is promoted by the same code
        path a deploy takes.

        Args:
            domain: The application.
            source: The source manager that reads the remote; one over this
                manager's runner by default.

        Returns:
            One sentence saying what the repository answered and with which key.

        Raises:
            SourceError: When the repository cannot be read; git's words are
                in the error.
        """
        app = self._app(domain)
        repository = self._repository(app)
        key = self.keystore.for_repository(repository)
        waiting = key.pending is not None if key else False
        reader = source or SourceManager(runner=self._runner)
        head = reader.remote_head(app.source or "", app.branch or None)
        after = self.keystore.get(key.name) if key else None
        if waiting and after is not None and after.pending is None:
            return (
                f"The new key works; it is the deploy key of {domain} now (branch {head.branch})."
            )
        if waiting:
            return (
                f"The repository answered with the current key, not the new one (branch "
                f"{head.branch}): add the new public key at the code host first."
            )
        return f"The repository answered (branch {head.branch})."

    # --------------------------------------------------------------- git

    def known_hosts_path(self) -> Path:
        """
        Where the host keys Noust pinned are, beside the secrets.

        Returns:
            The path of ``known_hosts``; the directory is made when a key is used.
        """
        return self.secrets.root.parent / "ssh" / "known_hosts"

    def has_key_for(self, url: str) -> bool:
        """
        Tell whether a URL names a repository that has a deploy key.

        Args:
            url: A repository URL.

        Returns:
            True when git signs in to it with that key.
        """
        identity = repository_identity(url)
        return identity is not None and self.keystore.for_repository(identity) is not None

    def identities_for(self, url: str) -> KeyUse | None:
        """
        Say how git signs in to a repository's host, when the repository has a key.

        Args:
            url: The URL git is about to use, ssh or not.

        Returns:
            The identities to try, or None when the URL is not ssh or its
            repository has no key: git then does what it always did.

        Raises:
            SourceError: When the key is recorded but its private half is gone.
        """
        if not is_ssh_url(url):
            return None
        identity = repository_identity(url)
        if identity is None:
            return None
        key = self.keystore.for_repository(identity)
        if key is None:
            return None
        known_hosts = self.known_hosts_path()
        self.fs.make_dir(known_hosts.parent, mode=SECRET_DIR_MODE, parents=True)
        identities: list[SshIdentity] = []
        copies: list[str] = []
        try:
            if key.pending is not None:
                waiting = secret_name(key.name, NEXT_PRIVATE)
                if self.secrets.read(waiting) is not None:
                    identities.append(
                        SshIdentity(
                            key.name,
                            "pending",
                            ssh_command(self.secrets.usable_path(waiting), known_hosts),
                        )
                    )
                    copies.append(waiting)
            current = secret_name(key.name, PRIVATE)
            if self.secrets.read(current) is None:
                raise SourceError(
                    f"The deploy key {key.name} has lost its private half",
                    details=f"Make a new one: noust app deploy-key remove <domain>, then "
                    f"generate (or import) one for {identity}.",
                )
            identities.append(
                SshIdentity(
                    key.name, "current", ssh_command(self.secrets.usable_path(current), known_hosts)
                )
            )
            copies.append(current)
        except NoustError:
            for name in copies:
                self.secrets.discard_usable_copy(name)
            raise
        return KeyUse(self, identities, copies)

    # ------------------------------------------------------------ host keys

    def pinned_hosts(self) -> list[PinnedHost]:
        """
        List the host keys Noust pinned.

        Returns:
            One entry per key; empty when nothing was pinned yet.
        """
        path = self.known_hosts_path()
        if not path.is_file():
            return []
        result = self.runner.run(["ssh-keygen", "-l", "-f", str(path)], timeout=KEYGEN_TIMEOUT)
        hosts: list[PinnedHost] = []
        for line in result.stdout.splitlines():
            parts = line.split()
            # "256 SHA256:abc host (ED25519)"
            if len(parts) >= 4 and parts[1].startswith("SHA256:"):
                hosts.append(
                    PinnedHost(host=parts[2], fingerprint=parts[1], key_type=parts[-1].strip("()"))
                )
        return hosts

    def pin_host(self, host: str, fingerprint: str) -> PinnedHost:
        """
        Pin a code host's SSH key before the first connection, by a fingerprint brought in.

        The host is asked for its keys (``ssh-keyscan``) and only a key whose SHA-256 fingerprint
        is the one given is recorded: the fingerprint comes from the code host's own published
        list (GitHub's and GitLab's documentation print them), over a channel the network
        between this server and the host cannot rewrite. A host that shows no key with that
        fingerprint is refused, and the fingerprints it did show are in the error.

        A host that is pinned already is never changed here: with the same key this is done,
        with another one the old must be forgotten first (:meth:`forget_host`), on purpose.

        Args:
            host: The host, or ``host:port``.
            fingerprint: ``SHA256:`` and 43 characters, as ``ssh-keygen -l`` prints it.

        Returns:
            The key now pinned.

        Raises:
            ValidationError: The host is not a host name, the fingerprint is not one, or the
                host already has another key pinned.
            SourceError: The host could not be reached, or showed no key with that fingerprint.
        """
        text = (host or "").strip()
        bracketed = _BRACKETED_HOST.fullmatch(text)
        if bracketed is not None:
            text = f"{bracketed['name']}:{bracketed['port']}"
        if not _HOST.fullmatch(text):
            raise ValidationError(f"{host!r} is not a host name", field="host")
        wanted = (fingerprint or "").strip()
        if not _FINGERPRINT.fullmatch(wanted):
            raise ValidationError(
                "That is not a fingerprint",
                details="It is SHA256: and 43 characters, as the code host publishes it and "
                "'ssh-keygen -l' prints it.",
                field="fingerprint",
            )
        name, _, port = text.partition(":")
        target = f"[{name}]:{port}" if port else name
        path = self.known_hosts_path()
        present = self._pinned_keys(target, path)
        if present:
            if wanted in {key.fingerprint for key in present}:
                return PinnedHost(
                    host=target, fingerprint=wanted, key_type=_type_word(present[0].key_type)
                )
            raise ValidationError(
                f"{text} has another key pinned already",
                details="Forget it first, on purpose, once you have checked the new fingerprint "
                f"with the code host: noust app deploy-key forget-host {text}",
                field="host",
            )
        scan = self.runner.run(
            ["ssh-keyscan", "-T", str(KEYSCAN_TIMEOUT), *(["-p", port] if port else []), name],
            timeout=KEYSCAN_TIMEOUT * 3,
        )
        offered: list[tuple[str, PublicKeyLine]] = []
        for line in scan.stdout.splitlines():
            fields = line.strip().split()
            if len(fields) < 3 or fields[0].startswith("#"):
                continue
            try:
                offered.append((fields[0], parse_public_key_line(" ".join(fields[1:3]))))
            except ValidationError:
                continue
        if not offered:
            raise SourceError(
                f"{text} did not show any host key",
                details=(scan.stderr or scan.stdout or "ssh-keyscan printed nothing.").strip()
                + "\nCheck the name and the port, and that this server can reach the host.",
            )
        matching = [(label, key) for label, key in offered if key.fingerprint == wanted]
        if not matching:
            shown = sorted({key.fingerprint for _, key in offered})
            raise SourceError(
                f"{text} showed no key with the fingerprint {wanted}",
                details="It showed: "
                + ", ".join(shown)
                + ". Compare with the fingerprints the code host publishes: a typo, or something "
                "between this server and the host, is the only way these differ.",
            )
        previous = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        added = [f"{label} {key.key_type} {key.blob}" for label, key in matching]
        body = previous if previous.endswith("\n") or not previous else previous + "\n"
        self.fs.make_dir(path.parent, mode=SECRET_DIR_MODE, parents=True)
        self.fs.write_text(path, body + "\n".join(added) + "\n", mode=SECRET_MODE)
        audit.record(
            "apps.deploy_key.host_pin",
            actor=self.actor,
            target=f"host:{text}",
            details={"host": text, "fingerprint": wanted},
        )
        return PinnedHost(
            host=target, fingerprint=wanted, key_type=_type_word(matching[0][1].key_type)
        )

    def _pinned_keys(self, target: str, path: Path) -> list[PublicKeyLine]:
        """
        Read the keys already pinned for a host, hashed entries included.

        Args:
            target: The host as ``known_hosts`` names it.
            path: The ``known_hosts`` file.

        Returns:
            The keys; none when the file or the host is not there.
        """
        if not path.is_file():
            return []
        found = self.runner.run(
            ["ssh-keygen", "-F", target, "-f", str(path)], timeout=KEYGEN_TIMEOUT
        )
        keys: list[PublicKeyLine] = []
        for line in found.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 3 and not line.startswith("#"):
                try:
                    keys.append(parse_public_key_line(" ".join(fields[1:3])))
                except ValidationError:
                    continue
        return keys

    def forget_host(self, host: str) -> bool:
        """
        Forget a pinned host key, so the next connection pins the key the host shows now.

        This is the one deliberate way to accept a host key that changed. The
        operator is expected to have checked the new fingerprint with the code
        host's published one first.

        Args:
            host: The host, ``host:port`` or ``[host]:port`` (the form
                :meth:`pinned_hosts` lists a host on another port in).

        Returns:
            True when a key was removed.

        Raises:
            ValidationError: When the host is not a host name.
            SourceError: When ssh-keygen fails.
        """
        text = (host or "").strip()
        bracketed = _BRACKETED_HOST.fullmatch(text)
        if bracketed is not None:
            # One spelling from here on, so the audit trail names a host the
            # same way whichever form the operator typed.
            text = f"{bracketed['name']}:{bracketed['port']}"
        if not _HOST.fullmatch(text):
            raise ValidationError(f"{host!r} is not a host name", field="host")
        path = self.known_hosts_path()
        if not path.is_file():
            return False
        name, _, port = text.partition(":")
        target = f"[{name}]:{port}" if port else name
        before = path.read_text(encoding="utf-8", errors="replace")
        try:
            self.runner.run(
                ["ssh-keygen", "-R", target, "-f", str(path)], timeout=KEYGEN_TIMEOUT, check=True
            )
        except CommandError as exc:
            raise SourceError(
                f"Could not forget the host key of {text}", details=exc.details or exc.message
            ) from exc
        removed = path.read_text(encoding="utf-8", errors="replace") != before
        if removed:
            audit.record(
                "apps.deploy_key.host_forget",
                actor=self.actor,
                target=f"host:{text}",
                details={"host": text},
            )
        return removed


def _type_word(key_type: str) -> str:
    """
    Args:
        key_type: A public key's type as the line spells it (``ssh-ed25519``).

    Returns:
        The word ``ssh-keygen -l`` prints for it (``ED25519``).
    """
    if key_type.startswith("ecdsa"):
        return "ECDSA"
    return key_type.removeprefix("ssh-").upper()


def ssh_command(key_path: Path, known_hosts: Path) -> str:
    """
    Build the ``GIT_SSH_COMMAND`` that signs in with one key and nothing else.

    ``IdentitiesOnly`` and ``IdentityAgent=none`` keep every other key out of
    the exchange (ssh-agent's and the user's own); ``-F /dev/null`` keeps a
    configuration file from changing the host or adding a proxy command; the
    host key is checked against Noust's own file only, strictly: the first
    contact is recorded and a different key later is refused.

    Args:
        key_path: Where ssh reads the private key.
        known_hosts: Noust's ``known_hosts``.

    Returns:
        The command, quoted for the shell git runs it in.
    """
    return shlex.join(
        [
            "ssh",
            "-F",
            "/dev/null",
            "-i",
            str(key_path),
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "IdentityAgent=none",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            f"UserKnownHostsFile={known_hosts}",
            "-o",
            "GlobalKnownHostsFile=/dev/null",
            "-o",
            "UpdateHostKeys=no",
            "-o",
            "ConnectTimeout=30",
        ]
    )


def _normalise_private(text: str) -> str:
    """
    Check the shape of a private key given as text, and end it with a newline.

    OpenSSH refuses a key file that does not end with one, and a form field
    or a pasted text loses it.

    Args:
        text: The key as given.

    Returns:
        The key, stripped, with one trailing newline.

    Raises:
        ValidationError: When it is empty, too large or not a private key.
    """
    stripped = (text or "").replace("\r\n", "\n").strip()
    if not stripped or len(stripped) > MAX_PRIVATE_KEY:
        raise ValidationError("The private key is empty or too large", field="private_key")
    first = stripped.splitlines()[0].strip()
    if first not in _PRIVATE_HEADERS or "PRIVATE KEY-----" not in stripped.splitlines()[-1]:
        raise ValidationError(
            "That is not a private key ssh can read",
            details="Paste the whole file, from '-----BEGIN OPENSSH PRIVATE KEY-----' to "
            "'-----END OPENSSH PRIVATE KEY-----'.",
            field="private_key",
        )
    return stripped + "\n"


def _read_plain(path: Path) -> str:
    """
    Read a file ssh-keygen just wrote in the private runtime directory.

    Args:
        path: The file.

    Returns:
        Its content.

    Raises:
        SourceError: When it is missing or is not a regular file.
    """
    import os

    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise SourceError(
            f"Could not read the key ssh-keygen generated at {path}", details=exc.strerror or ""
        ) from exc
    with os.fdopen(descriptor, encoding="utf-8") as handle:
        return handle.read()
