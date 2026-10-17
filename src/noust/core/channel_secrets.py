# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The secrets of the notification channels, encrypted in ``config.yaml`` (checklist K-12).

``config.yaml`` is 0600 and Noust redacts its secrets on the way out, but the webhook URL of a
Slack or Discord channel (a capability: whoever holds it posts as this server), a Telegram bot
token, an ntfy token, the SMTP password and a mail provider's API key sat in it as text, so a copy
of the file in a ticket, a backup or a support bundle disclosed every channel. They are encrypted
now with the mechanism the credentials of the backup destinations use
(:mod:`noust.core.at_rest`, the one implementation): the construction is HMAC-SHA256 in counter
mode and encrypt-then-MAC, the key is a random file next to the configuration (the same
``secrets.key`` the protected secrets of the store use, under a domain of its own so that a value
of one never opens in the other), and every value is bound to the setting it belongs to, so one
moved to another channel is refused instead of read.

The file is encrypted at the boundary, nowhere else: :class:`~noust.core.config.Config` reads it
through :func:`open_sealed` and writes it through :func:`seal`, so every reader of the
configuration sees the text and no file holds it. A server that lost the key keeps the ciphertext
as it is: reading it gives the opaque text (a channel that cannot send, which says so) and saving
the configuration writes the same text back, never an empty string.

What counts as a secret is the configuration's own rule (:func:`noust.core.config.is_secret_key`),
applied to the three places a channel keeps settings: ``notifications.channels``,
``monitor.smtp`` and ``monitor.email_api``.
"""

from __future__ import annotations

import copy
import logging
import secrets as _random
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from noust.core import at_rest
from noust.core.fs import get_fs, is_rehearsal

if TYPE_CHECKING:
    from noust.core.config import Config

log = logging.getLogger(__name__)

__all__ = [
    "SECTIONS",
    "ChannelSecretError",
    "is_sealed",
    "key_path",
    "open_sealed",
    "plain_secrets",
    "protect",
    "protect_at_start",
    "seal",
]

#: Where a channel keeps its settings.
SECTIONS: tuple[tuple[str, ...], ...] = (
    ("notifications", "channels"),
    ("monitor", "smtp"),
    ("monitor", "email_api"),
)


class ChannelSecretError(at_rest.AtRestError):
    """A stored channel secret cannot be opened: altered, moved, or the key is not its key."""


_DOMAIN = at_rest.Domain(
    prefix="noust-channel:v1:",
    label="noust-channel",
    noun="notification secret",
    error=ChannelSecretError,
)


def is_sealed(stored: object) -> bool:
    """
    Report whether a value in the file is encrypted.

    Args:
        stored: A value of the configuration.

    Returns:
        True when it is text that carries this domain's prefix.
    """
    return isinstance(stored, str) and _DOMAIN.is_sealed(stored)


def key_path(config_path: Path) -> Path:
    """
    Say where the key of a configuration file's secrets lives.

    Args:
        config_path: The configuration file.

    Returns:
        ``secrets.key`` in its directory: the file the protected secrets of the server use.
    """
    # Imported here: the secret store reads the configuration, which reads this module.
    from noust.core.secrets import AT_REST_KEY_FILE

    return config_path.parent / AT_REST_KEY_FILE


def _leaves(tree: dict[str, Any]) -> list[tuple[tuple[str, ...], dict[str, Any], str]]:
    """
    Find the settings of the channels that hold a secret.

    Args:
        tree: The configuration.

    Returns:
        ``(path, parent mapping, name)`` for each non-empty text value whose name is a secret's.
    """
    from noust.core.config import is_secret_key

    found: list[tuple[tuple[str, ...], dict[str, Any], str]] = []

    def walk(node: Any, path: tuple[str, ...]) -> None:
        if not isinstance(node, dict):
            return
        for name, value in node.items():
            here = (*path, str(name))
            if isinstance(value, dict):
                walk(value, here)
            elif isinstance(value, str) and value and is_secret_key(str(name)):
                found.append((here, node, str(name)))

    for section in SECTIONS:
        node: Any = tree
        for part in section:
            node = node.get(part) if isinstance(node, dict) else None
        walk(node, section)
    return found


def plain_secrets(tree: dict[str, Any]) -> int:
    """
    Count the channel secrets that are in clear.

    Args:
        tree: The configuration.

    Returns:
        How many.
    """
    return sum(1 for _path, parent, name in _leaves(tree) if not is_sealed(parent[name]))


def seal(tree: dict[str, Any], key_file: Path) -> dict[str, Any]:
    """
    Encrypt the channel secrets of a configuration for the file.

    Args:
        tree: The configuration, in memory. Not changed.
        key_file: Where the key is; made (private) when there is none and a secret needs it.

    Returns:
        A copy in which every secret in clear is encrypted. One already encrypted is left as it is.

    Raises:
        OSError: A secret needs encrypting and the key cannot be read or made: nothing is
            written in clear instead.
    """
    sealed = copy.deepcopy(tree)
    pending = [leaf for leaf in _leaves(sealed) if not is_sealed(leaf[1][leaf[2]])]
    if not pending:
        return sealed
    key = _key(key_file)
    for path, parent, name in pending:
        parent[name] = _DOMAIN.seal(parent[name], key, ".".join(path))
    return sealed


def open_sealed(tree: dict[str, Any], key_file: Path) -> tuple[dict[str, Any], list[str]]:
    """
    Decrypt the channel secrets of a configuration read from the file.

    Args:
        tree: The configuration as the file holds it. Not changed.
        key_file: Where the key is. Never created: reading makes no key.

    Returns:
        ``(configuration, problems)``: the configuration with every secret that could be opened
        in clear; a secret that could not (no key, altered, moved to another setting) stays as
        the sealed text, so that a save writes it back unchanged, and is named in the problems.
    """
    opened = copy.deepcopy(tree)
    sealed = [leaf for leaf in _leaves(opened) if is_sealed(leaf[1][leaf[2]])]
    problems: list[str] = []
    if not sealed:
        return opened, problems
    key: bytes | None = None
    try:
        key = at_rest.load_key(key_file, create=False)
    except OSError as exc:
        problems.append(f"the key {key_file} cannot be read: {exc}")
    for path, parent, name in sealed:
        dotted = ".".join(path)
        try:
            parent[name] = _DOMAIN.open(
                parent[name], key, dotted, missing_key=f"Restore {key_file}."
            )
        except at_rest.AtRestError as exc:
            problems.append(f"{dotted}: {exc}")
    return opened, problems


def _key(key_file: Path) -> bytes:
    """
    Load the key, making it when there is none.

    A rehearsal (``--dry-run``) makes no file: what it seals is thrown away with it.

    Args:
        key_file: The key's path.

    Returns:
        The key.

    Raises:
        OSError: The key cannot be read or made.
    """
    if is_rehearsal():
        found = at_rest.load_key(key_file, create=False)
        return found if found is not None else _random.token_bytes(32)
    get_fs().make_dir(key_file.parent, mode=0o755)
    key = at_rest.load_key(key_file, create=True)
    if key is None:  # create=True gives a key or raises; kept for the type
        raise OSError(f"{key_file} could not be made")
    return key


def protect(config: Config) -> int:
    """
    Encrypt, in the file, the channel secrets that are still in clear.

    The one-time migration of a configuration written before they were encrypted. It looks at
    the file, not at the memory (where a secret is always text): a file with nothing in clear is
    not written.

    Args:
        config: The configuration, which was read from the file.

    Returns:
        How many secrets it encrypted.

    Raises:
        OSError: The file cannot be read or written, or the key cannot be made.
    """
    from noust.core import config as config_module

    path = config_module.DEFAULT_CONFIG_PATH
    if not path.exists():
        return 0
    try:
        stored = yaml.safe_load(path.read_text())
    except yaml.YAMLError:
        return 0
    count = plain_secrets(stored) if isinstance(stored, dict) else 0
    if count:
        config.write(path)
    return count


def protect_at_start() -> int:
    """
    Encrypt the channel secrets a configuration written before they were encrypted still holds.

    Run when the console starts. A failure is logged and the console starts anyway: the secrets
    are then encrypted by the first save of the configuration.

    Returns:
        How many it encrypted; 0 when there was nothing to do or it could not run.
    """
    from noust.core.config import Config

    try:
        count = protect(Config())
    except OSError as exc:
        log.error("Could not encrypt the secrets of the notification channels: %s", exc)
        return 0
    if count:
        log.info("Encrypted %d secret(s) of the notification channels in the configuration", count)
    return count
