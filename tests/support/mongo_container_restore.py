# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_mongo_container_restore.py`` that other tests build on."""

from __future__ import annotations

import struct


def bson(**fields: str | int) -> bytes:
    """Encode a flat BSON document of strings and int32s."""
    body = b""
    for name, value in fields.items():
        key = name.encode() + b"\x00"
        if isinstance(value, int):
            body += b"\x10" + key + struct.pack("<i", value)
        else:
            raw = value.encode() + b"\x00"
            body += b"\x02" + key + struct.pack("<i", len(raw)) + raw
    return struct.pack("<i", len(body) + 5) + body + b"\x00"


def archive(*namespaces: tuple[str, str]) -> bytes:
    """A mongodump archive's prelude, followed by a body the reader never needs."""
    prelude = struct.pack("<I", 0x8199E26D)
    prelude += bson(concurrent_collections=4, version="0.1", server_version="7.0.0")
    for db, collection in namespaces:
        prelude += bson(db=db, collection=collection, metadata="{}", size=0)
    prelude += b"\xff\xff\xff\xff"
    return prelude + bson(db=namespaces[0][0] if namespaces else "", collection="x") + b"body"
