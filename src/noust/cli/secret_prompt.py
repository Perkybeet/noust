# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Reading a secret the way a person and a script each give one.

Every command that asks for a password, a token or a passphrase does it with ``click.prompt(...,
hide_input=True)``, which reads through ``getpass``. At a terminal that is right: nothing is echoed.
From a pipe (``printf '%s' "$PASSWORD" | noust db config -e mysql -p``, the way a script gives a secret
without putting it on a command line) ``getpass`` falls back to reading the line with a warning,
"Password input may be echoed.", on standard error, which a script then has to filter out and which
says something untrue: nothing is echoed, there is no terminal to echo to. Fifteen commands share the
prompt, so the fix is in the one place they all read through.
"""

from __future__ import annotations

import getpass
import sys

import click


def read_hidden(prompt: str = "") -> str:
    """
    Read one secret: without echo at a terminal, one quiet line from a pipe.

    Args:
        prompt: What to show before reading; shown only at a terminal, where it is a question to a
            person (a pipe has nobody to ask).

    Returns:
        The line, without its line ending.

    Raises:
        EOFError: A pipe that ends before a line (``click.prompt`` turns it into an abort).
    """
    if sys.stdin.isatty():
        return getpass.getpass(prompt)
    line = sys.stdin.readline()
    if not line:
        raise EOFError
    return line.rstrip("\r\n")


def install() -> None:
    """Make every ``click.prompt(..., hide_input=True)`` of the command line read through this."""
    click.termui.hidden_prompt_func = read_hidden
