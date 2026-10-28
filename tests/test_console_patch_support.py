# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A test replaces a name of the console's start and stop through ``patch_console``.

The commands (``noust.cli.commands.web``) import a good part of the service
(``noust.managers.console_service``) and each module looks a name up in its own namespace, so a
replacement on one of them leaves the other running the real thing. ``tests/support/console_patch_support.py``
replaces the name in every module that has it, and this holds that it does and that tests use it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import noust
from tests.support.console_patch_support import patch_console
from tests.support.patch_scan_support import patches_in_tree

ROOT = Path(noust.__file__).resolve().parent.parent.parent
CONSOLE_MODULES = ("noust.cli.commands.web", "noust.managers.console_service")


class TestPatchConsole:
    def test_a_name_both_modules_look_up_is_replaced_in_both(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.cli.commands import web
        from noust.managers import console_service

        assert web._port_in_use is console_service._port_in_use

        patch_console(monkeypatch, "_port_in_use", lambda host, port: True)

        assert web._port_in_use("127.0.0.1", 1) is True
        assert console_service._port_in_use("127.0.0.1", 1) is True

    def test_a_name_only_the_service_has_is_replaced_there(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.cli.commands import web
        from noust.managers import console_service

        assert not hasattr(web, "WEB_UNIT_TEMPLATE")

        patch_console(monkeypatch, "WEB_UNIT_TEMPLATE", "unit")

        assert console_service.WEB_UNIT_TEMPLATE == "unit"
        assert not hasattr(web, "WEB_UNIT_TEMPLATE")

    def test_a_name_only_the_commands_have_is_replaced_there(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.cli.commands import web
        from noust.managers import console_service

        assert not hasattr(console_service, "_disable")

        patch_console(monkeypatch, "_disable", lambda verbose: None)

        assert web._disable(False) is None

    def test_the_name_is_put_back_when_the_test_ends(self) -> None:
        from noust.cli.commands import web
        from noust.managers import console_service

        original = (web._port_in_use, console_service._port_in_use)
        with pytest.MonkeyPatch.context() as patch:
            patch_console(patch, "_port_in_use", lambda host, port: True)

        assert (web._port_in_use, console_service._port_in_use) == original

    def test_a_name_neither_has_is_an_error_not_a_replacement_that_tests_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with pytest.raises(AttributeError, match="no_such_name"):
            patch_console(monkeypatch, "no_such_name", 1)


def test_no_test_replaces_a_name_of_either_module_by_itself() -> None:
    """The commands and the service hold the same names; a replacement on one is a half of it."""
    offenders = [
        f"{site.path}:{site.line}  {module}.{site.name}"
        for module in CONSOLE_MODULES
        for site in patches_in_tree(ROOT, ("tests",), module)
        if site.path != "tests/support/console_patch_support.py"
    ]

    assert not offenders, (
        "Use patch_console (tests/support/console_patch_support.py):\n  " + "\n  ".join(offenders)
    )
