# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one door from the rest of Noust to the console's credential store.

The console's package needs FastAPI and friends, which are optional. The command line, the monitor
and the ENS checks reach its token manager through ``noust.core.web_gate``, which turns a missing
dependency into the error that says how to install it. The command line used to own the door, and
the monitor and the checks, which sit under it, imported it from there.
"""

from __future__ import annotations

import sys

import pytest

from noust.cli import web_state
from noust.core import web_gate
from noust.core.exceptions import DependencyError


def test_the_command_line_and_the_layers_below_it_use_the_same_door() -> None:
    assert web_state.token_manager is web_gate.token_manager


def test_without_the_console_s_dependencies_the_error_says_how_to_install_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "noust.web.auth", None)

    with pytest.raises(DependencyError) as caught:
        web_gate.token_manager()

    assert "needs the console's dependencies" in caught.value.message
    assert "noust web install" in (caught.value.details or "")


def test_the_other_doors_of_the_command_line_say_the_same(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "noust.web.permissions.access", None)

    with pytest.raises(DependencyError) as caught:
        web_state.access_manager()

    assert "noust web install" in (caught.value.details or "")
