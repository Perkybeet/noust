# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A command an error of the GitHub sources suggests is a command that exists.

The sentences say what to run (a ceiling to raise, a server to test, a deploy key to give, the
address GitHub delivers to); an error that suggests a command that does not work is worse than one
that suggests nothing. Each is run with ``--help``, which resolves the whole path of the command.
"""

from __future__ import annotations

import shlex

import pytest

from noust.cli.app import main
from noust.core.server_text import CATALOG, values_of

#: What the sources' sentences suggest, as the code writes it (placeholders as the code fills them).
SUGGESTED = [
    "noust fleet access --level deploy",
    "noust node test web-1",
    "noust node list",
    "noust app deploy-key --help",
    "noust github setup",
    "noust config set web.hooks_url https://hooks.example.com/hooks",
    "noust web expose-hooks hooks.example.com",
]


@pytest.mark.parametrize("command", SUGGESTED)
def test_the_command_exists(command: str) -> None:
    argv = [word for word in shlex.split(command)[1:] if word != "--help"]
    # Resolve the command's path and its options without running it: the arguments after the
    # command's name are dropped, which is how a person looks a command up.
    path = []
    for word in argv:
        if word.startswith("-") or "." in word or "/" in word:
            break
        path.append(word)
        if len(path) == 2 and path[0] in {"fleet", "node", "github", "web", "config", "app"}:
            break
    assert main([*path, "--help"]) == 0, command


def test_every_command_a_sources_sentence_takes_is_one_of_these() -> None:
    suggested = {
        word
        for code in CATALOG
        if code.startswith("sources.")
        for word in values_of(code)
        if word == "command" or word.endswith("_command")
    }

    assert suggested == {"command", "config_command"}
