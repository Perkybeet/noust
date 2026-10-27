# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust --help`` lists every command without importing any of them.

The root group has to print a line for each of about seventy commands, and the line is the first
paragraph of the command's own help, which lives in the module that defines it. Importing them all
took a second and a half and pulled in the managers and the deployers (780 modules) to print a
page, and it did the same on every Tab of the shell completion. The lines are kept in
``noust/cli/command_summaries.py``, generated from the commands by
``scripts/generate_command_summaries.py``; what these tests hold is that the generated table is
current and that the page and the completion read from it say exactly what the commands say.
"""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from types import ModuleType

import click
import pytest
from click.shell_completion import CompletionItem

from noust.cli import app as app_module
from noust.cli.app import COMMAND_MODULES, WEBAPP_COMMANDS, LazyGroup, cli
from noust.cli.command_summaries import SUMMARIES

ROOT = Path(__file__).resolve().parents[1]
WIDTHS = (50, 80, 100, 200)


def _generator() -> ModuleType:
    """Load the generator script the way the other generated files' tests do."""
    script = ROOT / "scripts" / "generate_command_summaries.py"
    spec = importlib.util.spec_from_file_location("generate_command_summaries", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _root_help(width: int, *, from_the_commands: bool) -> str:
    """
    Render the root's page.

    Args:
        width: The terminal width.
        from_the_commands: Import every command and read its help, the way the page was drawn
            before the table existed; otherwise the page as it is drawn now.

    Returns:
        The help text.
    """
    summaries = dict(cli._summaries)  # type: ignore[attr-defined]
    try:
        if from_the_commands:
            cli._summaries = {}  # type: ignore[attr-defined]
        with cli.make_context("noust", [], resilient_parsing=True, terminal_width=width) as ctx:
            return cli.get_help(ctx)
    finally:
        cli._summaries = summaries  # type: ignore[attr-defined]


class TestTheTable:
    """The committed table is what the commands generate."""

    def test_the_file_is_what_the_commands_generate(self) -> None:
        """A command whose help changed, or a new one, fails here until the table is rewritten."""
        generator = _generator()
        assert generator.main(["--check"]) == 0, (
            "noust/cli/command_summaries.py is stale: run python scripts/generate_command_summaries.py"
        )

    def test_the_generator_writes_what_it_checks(self, tmp_path: Path) -> None:
        """``--check`` fails on an old file, and writing it makes the check pass."""
        generator = _generator()
        stale = tmp_path / "command_summaries.py"
        stale.write_text("SUMMARIES = {}\n", encoding="utf-8")
        assert generator.main(["--check", "--output", str(stale)]) == 1
        assert generator.main(["--output", str(stale)]) == 0
        assert generator.main(["--check", "--output", str(stale)]) == 0

    def test_every_lazy_command_has_a_line_and_nothing_else_does(self) -> None:
        """The table covers the names the root loads on demand, exactly."""
        assert set(SUMMARIES) == {*COMMAND_MODULES, *WEBAPP_COMMANDS}

    def test_the_text_survives_the_source_it_is_written_in(self) -> None:
        """Quotes, backslashes and non-ASCII text come back as they were generated."""
        generator = _generator()
        source = generator.render(
            {
                "odd": (
                    False,
                    False,
                    'He said "hi" and left',
                    "It's a back\\slash, an é, a tab\tand a\nline",
                )
            }
        )
        namespace: dict[str, object] = {}
        exec(compile(source, "command_summaries.py", "exec"), namespace)  # noqa: S102
        assert namespace["SUMMARIES"] == {
            "odd": (
                False,
                False,
                'He said "hi" and left',
                "It's a back\\slash, an é, a tab\tand a\nline",
            )
        }


class TestThePage:
    """What ``noust --help`` and the shell completion print, and what they load."""

    @pytest.mark.parametrize("width", WIDTHS)
    def test_the_page_is_the_one_the_commands_draw(self, width: int) -> None:
        """Word for word, at any width: the short help is cut at the width click is given."""
        assert _root_help(width, from_the_commands=False) == _root_help(
            width, from_the_commands=True
        )

    def test_the_page_imports_no_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Drawing the root's page never asks for a command's module."""

        def refuse(name: str, *args: object, **kwargs: object) -> object:
            raise AssertionError(f"the root page imported {name}")

        monkeypatch.setattr(app_module.importlib, "import_module", refuse)
        text = _root_help(100, from_the_commands=False)
        assert "Commands:" in text
        assert "backup" in text and "list" in text

    def test_the_page_lists_what_it_listed_before(self) -> None:
        """The same names, hidden commands left out, in the same order."""
        listed = [
            line.split()[0]
            for line in _root_help(200, from_the_commands=False).split("Commands:")[1].splitlines()
            if line.strip()
        ]
        assert listed == sorted(listed)
        assert "backup" in listed and "self-update" in listed

    def test_the_completion_names_the_commands_without_importing_them(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tab on an empty line offers every visible command with its help, and loads none."""
        with cli.make_context("noust", [], resilient_parsing=True) as ctx:
            from_the_commands = {item.value: item.help for item in _complete_from_commands(ctx)}

            def refuse(name: str, *args: object, **kwargs: object) -> object:
                raise AssertionError(f"the completion imported {name}")

            monkeypatch.setattr(app_module.importlib, "import_module", refuse)
            offered = {item.value: item.help for item in cli.shell_complete(ctx, "")}
        for name, text in from_the_commands.items():
            assert offered[name] == text
        assert {"app", "backup", "list"} <= set(offered)

    def test_a_command_missing_from_the_table_is_still_loaded(self) -> None:
        """A name nobody generated a line for is imported, as before, not dropped from the page."""
        group = LazyGroup(name="x", lazy={"late": "noust.cli.commands.health"}, summaries={})
        with click.Context(group) as ctx:
            ctx.meta[app_module._LISTING] = True
            command = group.get_command(ctx, "late")
        assert command is not None
        assert command.help


def _complete_from_commands(ctx: click.Context) -> list[CompletionItem]:
    """
    Complete the way click does with every command loaded.

    Args:
        ctx: The root's context.

    Returns:
        What the completion offers for an empty word, read from the real commands.
    """
    summaries = dict(cli._summaries)  # type: ignore[attr-defined]
    cli._summaries = {}  # type: ignore[attr-defined]
    try:
        return list(cli.shell_complete(ctx, ""))
    finally:
        cli._summaries = summaries  # type: ignore[attr-defined]
