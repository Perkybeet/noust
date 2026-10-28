# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the compliance tests share: a configuration that answers from a nested dict. The real,
isolated one is the ``real_config`` fixture of ``conftest.py``.
"""

from __future__ import annotations

from typing import Any


class FakeConfig:
    """A configuration that answers dotted keys from a nested dict, as the profile code tolerates."""

    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.values = values or {}

    def get(self, key: str, default: Any = None) -> Any:
        node: Any = self.values
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def frameworks_on(*names: str, **settings: Any) -> FakeConfig:
    """
    Args:
        *names: Frameworks that are on.
        **settings: Other keys, with dots written as double underscores
            (``auth__session__idle_minutes=20``).

    Returns:
        A configuration holding them.
    """
    values: dict[str, Any] = {"security": {"frameworks": list(names)}}
    for key, value in settings.items():
        node = values
        parts = key.split("__")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return FakeConfig(values)
