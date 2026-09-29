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

ssh needs file paths, so these are files: 0600 in 0700 directories, written
through the filesystem seam. The key has no passphrase because the process
that uses it runs unattended; the sealing layer protects the files at rest.
"""

from __future__ import annotations

from pathlib import Path

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
        text = self._secrets.read(secret_name(node, PUBLIC_KEY))
        if text is None or not self.private_key_path(node).is_file():
            return None
        return parse_public_key(text, what=f"central key for {node}")

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
        private = self.private_key_path(node)
        fs = self._secrets.fs
        fs.make_dir(private.parent, mode=SECRET_DIR_MODE, parents=True)
        # A half pair (one file without the other) is regenerated whole:
        # ssh-keygen asks before overwriting, and its stdin is /dev/null.
        fs.remove(private, missing_ok=True)
        fs.remove(self._secrets.path(secret_name(node, PUBLIC_KEY)), missing_ok=True)
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
        generated = self.public_key(node)
        if generated is None:
            raise NodeError(
                f"No key pair was generated for node {node}",
                details="ssh-keygen did not run; this is expected in a dry run.",
            )
        return generated

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
