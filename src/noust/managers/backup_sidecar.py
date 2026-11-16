# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The MAC that signs a backup's metadata (ENS G12, mp.si.2).

A backup's sidecar says what the archive is (its checksum, its application, when it was taken) and is
signed with a key that lives in the secret store, not beside the backups: whoever can write the backup
directory, or the remote it is copied to, cannot forge one. Moved out of :mod:`noust.managers.backup_manager`,
which does not grow past its size (``tests/test_layering.py``); that module still says every name here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

from noust.core.exceptions import ConfigError
from noust.core.logger import Logger
from noust.core.sealing import SealError

__all__ = ["SIDECAR_KEY_SECRET", "sidecar_mac_state", "sign_sidecar"]

#: The secret that signs every sidecar this server writes (ENS G12, mp.si.2).
#: In the secret store, not beside the backups: whoever can write the backup
#: directory, or the remote it is copied to, cannot forge a sidecar.
SIDECAR_KEY_SECRET = "backups/sidecar-mac-key"  # noqa: S105 - a secret's name

#: The fields a sidecar's MAC does not cover: the MAC itself.
_MAC_FIELDS = ("mac", "mac_key_id")


def _sidecar_key(*, create: bool) -> bytes | None:
    """
    Read the sidecar key, creating it when asked and missing.

    Args:
        create: Create a key when there is none.

    Returns:
        The key; None when there is none, or it cannot be read now (a sealed
        store that is locked), in which case the reason is logged.
    """
    from noust.core.secrets import SecretStore

    secrets_store = SecretStore()
    try:
        text = secrets_store.read(SIDECAR_KEY_SECRET)
        if text is None and create:
            text = os.urandom(32).hex()
            secrets_store.write(SIDECAR_KEY_SECRET, text)
    except (SealError, ConfigError, OSError) as exc:
        Logger(verbose=False).warning(f"The key that signs backup metadata cannot be used: {exc}")
        return None
    if not text:
        return None
    try:
        return bytes.fromhex(text.strip())
    except ValueError:
        Logger(verbose=False).warning(f"{SIDECAR_KEY_SECRET} does not hold a key")
        return None


def _key_id(key: bytes) -> str:
    """
    Args:
        key: A sidecar key.

    Returns:
        A short public name for it, so a sidecar says which key signed it.
    """
    return hashlib.sha256(b"noust-backup-sidecar-key\0" + key).hexdigest()[:16]


def _sidecar_mac(key: bytes, data: dict[str, Any]) -> str:
    """
    Args:
        key: The sidecar key.
        data: The sidecar's fields.

    Returns:
        HMAC-SHA256 over every field but the MAC's own, canonically encoded.
    """
    payload = json.dumps(
        {name: value for name, value in data.items() if name not in _MAC_FIELDS},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(key, b"noust-backup-sidecar:v1\0" + payload, hashlib.sha256).hexdigest()


def sign_sidecar(data: dict[str, Any]) -> dict[str, Any]:
    """
    Add the MAC to a sidecar's fields.

    The MAC covers the archive's checksum, so the archive is authenticated
    with it: a sidecar and archive replaced together on the backup storage
    no longer verify. Without a usable key (a locked sealed store) the
    sidecar is written unsigned rather than the backup lost; ``verify`` says
    so.

    Args:
        data: The sidecar's fields.

    Returns:
        The fields, with ``mac`` and ``mac_key_id``.
    """
    key = _sidecar_key(create=True)
    if key is None:
        return {**data, "mac": None, "mac_key_id": None}
    return {**data, "mac": _sidecar_mac(key, data), "mac_key_id": _key_id(key)}


def sidecar_mac_state(data: dict[str, Any]) -> str:
    """
    Check a sidecar's MAC.

    Args:
        data: The sidecar as read from its file, every field.

    Returns:
        ``valid``; ``invalid`` (it was changed); ``unsigned`` (from before
        3.1); ``other_key`` (another server signed it); ``no_key`` (the key
        cannot be read here).
    """
    mac, key_id = data.get("mac"), data.get("mac_key_id")
    if not mac:
        return "unsigned"
    key = _sidecar_key(create=False)
    if key is None:
        return "no_key"
    if key_id != _key_id(key):
        return "other_key"
    return "valid" if hmac.compare_digest(_sidecar_mac(key, data), str(mac)) else "invalid"
