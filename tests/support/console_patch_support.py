# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Replacing a name of the console's start and stop in a test.

``noust web`` is two modules: the commands (``noust.cli.commands.web``) and what they do to the
console, its daemon, its unit and its port (``noust.managers.console_service``). A name the
commands import from the service is bound in both, and each module looks it up in its own, so a
replacement on one leaves the other running the real thing. This replaces the name in every one
of the two that has it; ``tests/test_console_patch_support.py`` holds that it does.
"""

from __future__ import annotations

from typing import Any

import pytest


def patch_console(monkeypatch: pytest.MonkeyPatch, name: str, value: Any) -> None:
    """
    Replace a name wherever the console's commands or its service look it up.

    Args:
        monkeypatch: The test's patcher, so the name is put back when it ends.
        name: The attribute, which the commands, the service or both have.
        value: What to put in its place.

    Raises:
        AttributeError: When neither module has the name, which is a replacement that would
            test nothing.
    """
    from noust.cli.commands import web
    from noust.managers import console_service

    holders = [module for module in (web, console_service) if hasattr(module, name)]
    if not holders:
        raise AttributeError(f"neither the console's commands nor its service has {name!r}")
    for module in holders:
        monkeypatch.setattr(module, name, value)
