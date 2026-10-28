# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_importers.py`` that other tests build on."""

from __future__ import annotations

from pathlib import Path

from noust.deployers.importers.base import Proposal


def tree(root: Path, files: dict[str, str]) -> Path:
    """Write a fake repository."""
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


def joined(proposal: Proposal) -> str:
    """Every warning in one string, to search."""
    return "\n".join(proposal.warnings)
