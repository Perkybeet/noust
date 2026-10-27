#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Write ``src/noust/cli/command_summaries.py`` from the commands the root group loads on demand.

``noust --help`` and the shell completion need one line of help for each of about seventy
commands, and that line lives in the module that defines the command. Importing them all to read a
line took a second and a half. The line is generated here, once, from the commands themselves, so
the page can be drawn without importing any of them; ``tests/test_cli_command_summaries.py`` holds
the page to what the commands say. ``--check`` fails when the committed file is not what the
commands generate (a test runs it); without it the file is written.

Usage:
    scripts/generate_command_summaries.py            Write the file.
    scripts/generate_command_summaries.py --check    Fail if it is out of date.
"""

from __future__ import annotations

import argparse
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

DEFAULT_OUTPUT = ROOT / "src" / "noust" / "cli" / "command_summaries.py"

#: ``(hidden, deprecated, short_help, help)``: what ``click`` reads to draw a command's line.
Entry = tuple[bool, bool | str, str, str]

HEADER = '''\
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The line of help of each command the root group loads on demand. Generated, do not edit.

``noust --help`` and the shell completion list every command with the first paragraph of its help,
and the help is in the module that defines it, so drawing the page meant importing all of them
(seven hundred modules, a second and a half). This table is what Click reads instead, and
``noust.cli.app.LazyGroup`` falls back to importing a command that is not in it. A test holds it to
the commands: after changing a command's help or adding a command, run
``python scripts/generate_command_summaries.py``.

Each entry is ``(hidden, deprecated, short_help, help)``: the attributes of the command that decide
its line. ``help`` is its first paragraph only, which is all Click reads for the line.
"""

from __future__ import annotations

SUMMARIES: dict[str, tuple[bool, bool | str, str, str]] = {
'''


def literal(text: str) -> str:
    """
    Write a string as a Python literal that ``ruff format`` leaves as it is.

    Double quotes unless the text has double quotes and no single ones, which is the choice the
    formatter makes, and every character that is not printable as an escape in lowercase, so the
    file is the same before and after formatting.

    Args:
        text: The string.

    Returns:
        The literal, quotes included.
    """
    pieces: list[str] = []
    for char in text:
        if char == "\\":
            pieces.append("\\\\")
        elif char == "\n":
            pieces.append("\\n")
        elif char == "\t":
            pieces.append("\\t")
        elif char == "\r":
            pieces.append("\\r")
        elif char.isprintable():
            pieces.append(char)
        elif ord(char) < 0x100:
            pieces.append(f"\\x{ord(char):02x}")
        elif ord(char) < 0x10000:
            pieces.append(f"\\u{ord(char):04x}")
        else:
            pieces.append(f"\\U{ord(char):08x}")
    body = "".join(pieces)
    if '"' in body and "'" not in body:
        return f"'{body}'"
    return '"' + body.replace('"', '\\"') + '"'


def render(entries: dict[str, Entry]) -> str:
    """
    Write the module.

    Args:
        entries: Each command's name to its entry.

    Returns:
        The text of ``command_summaries.py``, the entries sorted by name.
    """
    lines = [HEADER.rstrip("\n")]
    for name in sorted(entries):
        hidden, deprecated, short_help, help_text = entries[name]
        lines.append(f"    {literal(name)}: (")
        lines.append(f"        {hidden!r},")
        lines.append(
            f"        {literal(deprecated) if isinstance(deprecated, str) else deprecated!r},"
        )
        lines.append(f"        {literal(short_help)},")
        lines.append(f"        {literal(help_text)},")
        lines.append("    ),")
    lines.append("}")
    return "\n".join(lines) + "\n"


def first_paragraph(text: str | None) -> str:
    """
    Reduce a command's help to what Click reads for its line.

    Args:
        text: The help of a command, as Click holds it.

    Returns:
        The first paragraph with its whitespace collapsed: Click draws the line from the words of
        that paragraph, so this reads the same under every version that stores the text raw or
        cleaned.
    """
    if not text:
        return ""
    cleaned = inspect.cleandoc(text).partition("\f")[0]
    return " ".join(cleaned.split("\n\n")[0].split())


def collect() -> dict[str, Entry]:
    """
    Read the commands the root loads on demand.

    Returns:
        Each name to the entry its command gives.

    Raises:
        SystemExit: When a name of the tables resolves to no command.
    """
    import click

    from noust.cli.app import COMMAND_MODULES, WEBAPP_COMMANDS, cli

    context = click.Context(cli)
    entries: dict[str, Entry] = {}
    for name in sorted({*COMMAND_MODULES, *WEBAPP_COMMANDS}):
        command = cli.get_command(context, name)
        if command is None:
            raise SystemExit(f"noust {name} resolves to no command")
        deprecated = getattr(command, "deprecated", False)
        entries[name] = (
            bool(command.hidden),
            deprecated if isinstance(deprecated, str) else bool(deprecated),
            command.short_help or "",
            first_paragraph(command.help),
        )
    return entries


def main(argv: list[str] | None = None) -> int:
    """
    Args:
        argv: The arguments; ``sys.argv`` by default.

    Returns:
        0 when the file was written or is current, 1 when ``--check`` finds it stale.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail when the file is stale.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Where to write.")
    args = parser.parse_args(argv)
    wanted = render(collect())
    present = args.output.read_text(encoding="utf-8") if args.output.exists() else None
    if args.check:
        if present != wanted:
            print(f"{args.output} is stale: run python scripts/generate_command_summaries.py")
            return 1
        return 0
    args.output.write_text(wanted, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
