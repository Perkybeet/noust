# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_backup_sealing.py`` that other tests build on."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Meta:
    id: str
    domain: str
    checksum: str | None
    size_bytes: int = 0


class Apps:
    """Just enough of the application backup manager."""

    def __init__(self, root: Path, entries: list[Meta]) -> None:
        self.root = root
        self.entries = entries
        self.recorded: list[tuple[str, int, str]] = []

    def list_backups(self) -> list[Meta]:
        return self.entries

    def local_files(self, meta: Meta) -> tuple[Path, Path]:
        return self.root / f"{meta.id}.tar.gz", self.root / f"{meta.id}.json"

    def record_resealed(self, meta: Meta, *, size_bytes: int, checksum: str) -> None:
        meta.size_bytes, meta.checksum = size_bytes, checksum
        self.recorded.append((meta.id, size_bytes, checksum))
