# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The central's side of a node's SSH trust: its key pair and the pinned host key.

Everything lives in the secret store, under ``fleet/nodes/<name>/``:

- ``token``: the fleet token the node issued (from the join code).
- ``id_ed25519`` and ``id_ed25519.pub``: the key pair this central uses for
  this node alone, generated here; the node restricts it to forwarding one
  port, so it opens no shell and runs nothing.
- ``known_hosts``: one line, the node's host key under a fixed alias, taken
  from the join code. ssh is pointed at this file only, with
  ``StrictHostKeyChecking=yes``: a changed host key stops the tunnel.
- ``next_id_ed25519`` and ``next_id_ed25519.pub``: while ``noust node rekey``
  rotates the pair, the new one, waiting for the node to authorize it. It
  replaces the current pair only once the node answers with it.

ssh needs file paths, so these are files: 0600 in 0700 directories, written
through the filesystem seam. The key has no passphrase because the process
that uses it runs unattended; the sealing layer protects the files at rest.

On a sealed store those files hold ciphertext, so ssh is never pointed at
them: :meth:`NodeKeys.usable_private_key` and
:meth:`NodeKeys.usable_known_hosts` hand it private decrypted copies, which
:meth:`NodeKeys.discard_usable` removes as soon as ssh has read them. A key
pair for a sealed store is generated into that same private directory and
sealed into the store at once.
"""

from __future__ import annotations

import os
from pathlib import Path

from noust.core import sealing
from noust.core.exceptions import NodeError
from noust.core.fs import SECRET_DIR_MODE, SECRET_MODE
from noust.core.runner import CommandError, CommandRunner, get_runner
from noust.core.secrets import SecretStore
from noust.fleet.models import PublicKey, parse_public_key, validate_node_name

#: Namespace of every node's secrets.
NODES_NAMESPACE = "fleet/nodes"

TOKEN = "token"  # noqa: S105 - a file name, not a credential
PRIVATE_KEY = "id_ed25519"
PUBLIC_KEY = "id_ed25519.pub"
KNOWN_HOSTS = "known_hosts"
NEXT_PRIVATE_KEY = "next_id_ed25519"
NEXT_PUBLIC_KEY = "next_id_ed25519.pub"

#: How long ssh-keygen may take. It is instantaneous; this only bounds a hang.
KEYGEN_TIMEOUT = 30


def secret_name(node: str, leaf: str) -> str:
    """
    Name one of a node's secrets.

    Args:
        node: The node's name.
        leaf: ``token``, ``id_ed25519``, ``id_ed25519.pub`` or ``known_hosts``.

    Returns:
        ``fleet/nodes/<node>/<leaf>``.
    """
    return f"{NODES_NAMESPACE}/{validate_node_name(node)}/{leaf}"


def host_key_alias(node: str) -> str:
    """
    Name a node's host key entry in its ``known_hosts`` file.

    ssh looks the host key up under this alias (``HostKeyAlias``) whatever
    address or port the node is reached on, so moving a node to a new address
    keeps the pin, and two nodes behind one address never share one.

    Args:
        node: The node's name.

    Returns:
        ``noust-node-<node>``.
    """
    return f"noust-node-{validate_node_name(node)}"


def known_hosts_line(node: str, host_key: str) -> str:
    """
    Spell the pinned ``known_hosts`` line for a node.

    Args:
        node: The node's name.
        host_key: The node's host key, ``ssh-ed25519 AAAA...``.

    Returns:
        ``noust-node-<node> ssh-ed25519 AAAA...``.
    """
    return f"{host_key_alias(node)} {parse_public_key(host_key, what='node host key').bare}"


class NodeKeys:
    """
    Generate, read and forget the per-node SSH material on the central.

    Args:
        secrets: The secret store; the process-wide one by default.
        runner: The runner ssh-keygen goes through; the process-wide one by default.
    """

    def __init__(self, secrets: SecretStore | None = None, runner: CommandRunner | None = None):
        self._secrets = secrets or SecretStore()
        self._runner = runner

    @property
    def secrets(self) -> SecretStore:
        """The secret store."""
        return self._secrets

    @property
    def runner(self) -> CommandRunner:
        """The runner ssh-keygen goes through."""
        return self._runner or get_runner()

    def private_key_path(self, node: str) -> Path:
        """
        Where the node's private key is, for ssh's ``-i``.

        Args:
            node: The node's name.

        Returns:
            The path, which may not exist yet.
        """
        return self._secrets.path(secret_name(node, PRIVATE_KEY))

    def known_hosts_path(self, node: str) -> Path:
        """
        Where the node's pinned ``known_hosts`` is, for ``UserKnownHostsFile``.

        Args:
            node: The node's name.

        Returns:
            The path, which may not exist yet.
        """
        return self._secrets.path(secret_name(node, KNOWN_HOSTS))

    def usable_private_key(self, node: str) -> Path:
        """
        Where ssh can read the node's private key from, for ``-i``.

        Args:
            node: The node's name.

        Returns:
            The key's own file, or a private decrypted copy on a sealed store.

        Raises:
            SecretsLockedError: When the store is sealed and this process is locked.
            SealError: When the copy cannot be made privately.
        """
        return self._secrets.usable_path(secret_name(node, PRIVATE_KEY))

    def usable_known_hosts(self, node: str) -> Path:
        """
        Where ssh can read the node's pinned host key from, for ``UserKnownHostsFile``.

        Args:
            node: The node's name.

        Returns:
            The file itself, or a private decrypted copy on a sealed store.

        Raises:
            SecretsLockedError: When the store is sealed and this process is locked.
            SealError: When the copy cannot be made privately.
        """
        return self._secrets.usable_path(secret_name(node, KNOWN_HOSTS))

    def discard_usable(self, node: str) -> None:
        """
        Remove the decrypted copies made for ssh; nothing on a store not sealed.

        Args:
            node: The node's name.
        """
        for leaf in (PRIVATE_KEY, KNOWN_HOSTS):
            self._secrets.discard_usable_copy(secret_name(node, leaf))

    def public_key(self, node: str) -> PublicKey | None:
        """
        Read the public half of the node's key pair.

        Args:
            node: The node's name.

        Returns:
            The key, or None when no pair was generated for this node.

        Raises:
            NodeError: When the file is there but is not a valid key.
        """
        return self._public(node, PRIVATE_KEY, PUBLIC_KEY, f"central key for {node}")

    def _public(self, node: str, private: str, public: str, what: str) -> PublicKey | None:
        """
        Read the public half of one of the node's pairs.

        Args:
            node: The node's name.
            private: The private key's leaf name.
            public: The public key's leaf name.
            what: What the key is, for the messages.

        Returns:
            The key, or None when that pair does not exist.

        Raises:
            NodeError: When the file is there but is not a valid key.
        """
        text = self._secrets.read(secret_name(node, public))
        if text is None or not self._secrets.path(secret_name(node, private)).is_file():
            return None
        return parse_public_key(text, what=what)

    def pending_public_key(self, node: str) -> PublicKey | None:
        """
        Read the public half of the pair waiting to replace the node's current one.

        Args:
            node: The node's name.

        Returns:
            The key, or None when no rotation is under way.

        Raises:
            NodeError: When the file is there but is not a valid key.
        """
        return self._public(node, NEXT_PRIVATE_KEY, NEXT_PUBLIC_KEY, f"new central key for {node}")

    def ensure_pending_keypair(self, node: str, central: str) -> PublicKey:
        """
        Generate the pair that will replace the node's current one, once.

        Asking again while it waits returns the same key, so the command
        printed for the node stays valid until the rotation finishes.

        Args:
            node: The node's name.
            central: This central's name, for the key's comment.

        Returns:
            The new public key.

        Raises:
            NodeError: When ssh-keygen fails, or did not run (a dry run).
        """
        existing = self.pending_public_key(node)
        if existing is not None:
            return existing
        if sealing.is_sealed(self._secrets.root):
            self._generate_sealed(node, central, private_leaf=NEXT_PRIVATE_KEY)
        else:
            self._generate(node, central, self._secrets.path(secret_name(node, NEXT_PRIVATE_KEY)))
        generated = self.pending_public_key(node)
        if generated is None:
            raise NodeError(
                f"No new key pair was generated for node {node}",
                details="ssh-keygen did not run; this is expected in a dry run.",
            )
        return generated

    def current_pair(self, node: str) -> tuple[str, str] | None:
        """
        Read the node's current pair, to put it back if a rotation fails.

        Args:
            node: The node's name.

        Returns:
            ``(private, public)`` text, or None when there is no pair.
        """
        private = self._secrets.read(secret_name(node, PRIVATE_KEY))
        public = self._secrets.read(secret_name(node, PUBLIC_KEY))
        if private is None or public is None:
            return None
        return private, public

    def write_pair(self, node: str, pair: tuple[str, str]) -> None:
        """
        Make a pair the node's current one.

        Args:
            node: The node's name.
            pair: ``(private, public)`` text.
        """
        private, public = pair
        self._secrets.write(secret_name(node, PRIVATE_KEY), private)
        self._secrets.write(secret_name(node, PUBLIC_KEY), public)

    def promote_pending(self, node: str) -> tuple[str, str]:
        """
        Make the waiting pair the node's current one; the waiting files stay until discarded.

        Args:
            node: The node's name.

        Returns:
            The pair it replaced, ``(private, public)``, for :meth:`write_pair`
            to put back.

        Raises:
            NodeError: When no pair is waiting, or the node has no current one.
        """
        private = self._secrets.read(secret_name(node, NEXT_PRIVATE_KEY))
        public = self._secrets.read(secret_name(node, NEXT_PUBLIC_KEY))
        previous = self.current_pair(node)
        if private is None or public is None or previous is None:
            raise NodeError(
                f"No new key is waiting for node {node}",
                details=f"Start the rotation with 'noust node rekey {node}'.",
            )
        self.write_pair(node, (private, public))
        return previous

    def discard_pending(self, node: str) -> None:
        """
        Forget the waiting pair.

        Args:
            node: The node's name.
        """
        self._secrets.delete(secret_name(node, NEXT_PRIVATE_KEY))
        self._secrets.delete(secret_name(node, NEXT_PUBLIC_KEY))

    def ensure_keypair(self, node: str, central: str) -> PublicKey:
        """
        Generate the node's key pair once, and return its public half.

        Args:
            node: The node's name.
            central: This central's name, for the key's comment.

        Returns:
            The public key.

        Raises:
            NodeError: When ssh-keygen fails, or did not run (a dry run).
        """
        existing = self.public_key(node)
        if existing is not None:
            return existing
        root = self._secrets.root
        if sealing.is_sealed(root):
            self._generate_sealed(node, central)
        else:
            self._generate(node, central, self.private_key_path(node))
        generated = self.public_key(node)
        if generated is None:
            raise NodeError(
                f"No key pair was generated for node {node}",
                details="ssh-keygen did not run; this is expected in a dry run.",
            )
        return generated

    def _generate_sealed(self, node: str, central: str, *, private_leaf: str = PRIVATE_KEY) -> None:
        """
        Generate a key pair for a sealed store, and seal it in.

        ssh-keygen writes files in clear, which a sealed store refuses to
        read back; it writes them in the private runtime directory instead,
        and both halves go into the store through the seal before the clear
        copies are removed.

        Args:
            node: The node's name.
            central: This central's name, for the key's comment.
            private_leaf: The private key's secret name (its public half is
                the same with ``.pub``): the current pair, or the next one.

        Raises:
            SecretsLockedError: When this process is locked: nothing could be
                sealed, so nothing is generated.
            NodeError: When ssh-keygen fails.
        """
        root = self._secrets.root
        if not sealing.is_unlocked(root):
            raise sealing.SecretsLockedError(
                f"This central is locked; no key can be generated for {node}",
                details=(
                    "Its secrets are sealed. Unlock it in the console, or with 'noust "
                    "central unlock' on the central, and try again."
                ),
            )
        fs = self._secrets.fs
        copies = sealing.plaintext_copy_dir(root)
        sealing.ensure_private_dir(copies, fs)
        private = copies / "keygen" / validate_node_name(node) / private_leaf
        public = Path(f"{private}.pub")
        try:
            self._generate(node, central, private)
            if not private.is_file():
                return
            self._secrets.write(secret_name(node, private_leaf), _read_private(private))
            self._secrets.write(secret_name(node, f"{private_leaf}.pub"), _read_private(public))
        finally:
            fs.remove(private, missing_ok=True)
            fs.remove(public, missing_ok=True)

    def _generate(self, node: str, central: str, private: Path) -> None:
        """
        Run ssh-keygen for a node's pair, writing it at ``private`` and ``.pub``.

        Args:
            node: The node's name.
            central: This central's name, for the key's comment.
            private: Where the private key goes.

        Raises:
            NodeError: When ssh-keygen fails.
        """
        fs = self._secrets.fs
        fs.make_dir(private.parent, mode=SECRET_DIR_MODE, parents=True)
        # A half pair (one file without the other) is regenerated whole:
        # ssh-keygen asks before overwriting, and its stdin is /dev/null.
        fs.remove(private, missing_ok=True)
        fs.remove(Path(f"{private}.pub"), missing_ok=True)
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
                    f"noust-central@{central}",
                    "-f",
                    str(private),
                ],
                timeout=KEYGEN_TIMEOUT,
                check=True,
            )
        except CommandError as exc:
            raise NodeError(
                f"Could not generate the key pair for node {node}",
                details=exc.details or exc.message,
            ) from exc
        if private.is_file():
            # ssh-keygen honours the umask for the .pub; the private key is
            # 0600 already, and saying so here makes it independent of it.
            fs.chmod(private, SECRET_MODE)

    def pin_host_key(self, node: str, line: str) -> None:
        """
        Write the node's ``known_hosts`` file: exactly one pinned line.

        Args:
            node: The node's name.
            line: :func:`known_hosts_line`'s output.
        """
        self._secrets.write(secret_name(node, KNOWN_HOSTS), line.rstrip("\n") + "\n")

    def forget(self, node: str) -> None:
        """
        Delete every secret of a node: token, key pair, ``known_hosts``.

        Args:
            node: The node's name.
        """
        self._secrets.delete_namespace(f"{NODES_NAMESPACE}/{validate_node_name(node)}")


def _read_private(path: Path) -> str:
    """
    Read a file ssh-keygen just wrote in the private runtime directory.

    Args:
        path: The file.

    Returns:
        Its content.

    Raises:
        NodeError: When it is missing or is not a regular file (never followed).
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise NodeError(
            f"Could not read the key ssh-keygen generated at {path}",
            details=exc.strerror or str(exc),
        ) from exc
    with os.fdopen(descriptor, encoding="utf-8") as handle:
        return handle.read()
