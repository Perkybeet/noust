# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_source_uploads.py`` that other tests build on."""

from __future__ import annotations

import io
import tarfile
import zipfile


def make_zip(files: dict[str, bytes], *, top: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(f"{top}/{name}" if top else name, content)
    return buffer.getvalue()


def make_tgz(members: list[tuple[str, bytes | str]]) -> bytes:
    """Members are (name, content), or (name, "-> target") for a symbolic link."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in members:
            info = tarfile.TarInfo(name)
            if isinstance(content, str) and content.startswith("-> "):
                info.type = tarfile.SYMTYPE
                info.linkname = content[3:]
                archive.addfile(info)
            else:
                data = content.encode() if isinstance(content, str) else content
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


PROJECT = {"package.json": b'{"name": "shop"}', "src/index.js": b"console.log(1)"}
