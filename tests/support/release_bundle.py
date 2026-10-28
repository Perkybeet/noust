# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_release_bundle.py`` that other tests build on."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from typing import Any

from noust.deployers import bundle
from noust.deployers.bundle import MANIFEST_NAME, Manifest

RELEASE = "20261013-100000-abc1234"


def manifest(**changes: Any) -> Manifest:
    values: dict[str, Any] = {
        "format": bundle.FORMAT,
        "app_type": "nodejs",
        "domain": "shop.example.com",
        "release_id": RELEASE,
        "commit": "abc1234",
        "branch": "main",
        "app_root": "/var/www/apps/shop-example-com",
        "built_at": "2026-10-17T10:00:00+00:00",
        "builder": "builder-1",
        "noust_version": "3.4.0",
        "files": 0,
        "bytes": 0,
    }
    values.update(changes)
    return Manifest(**values)


def raw_bundle(path: Path, members: list[tuple[str, bytes | str, int]], doc: Any = None) -> Path:
    """
    Write a bundle by hand, to be as hostile as a test needs.

    Members are (name, content, mode); content "-> target" makes a symbolic link.
    """
    document = json.dumps(doc if doc is not None else manifest().to_dict()).encode()
    with tarfile.open(path, "w:gz") as archive:
        head = tarfile.TarInfo(MANIFEST_NAME)
        head.size = len(document)
        archive.addfile(head, io.BytesIO(document))
        for name, content, mode in members:
            info = tarfile.TarInfo(name)
            info.mode = mode
            if isinstance(content, str) and content.startswith("-> "):
                info.type = tarfile.SYMTYPE
                info.linkname = content[3:]
                archive.addfile(info)
            else:
                data = content if isinstance(content, bytes) else content.encode()
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    return path
