# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The built-in assistant is off by default and nothing of it is loaded until it is used (item 171).

``noust --version`` and the console's start do not import its client, its settings, its limits, its
masking, its probe or its conversations: they import a file that imports nothing, and the one
module that reads whether it is on (``state``). The imports are read from the syntax tree, the way
the start-up budget reads them (``tests/support/import_graph_support.py``), because a test cannot
start a process.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

import pytest

from tests.support import import_graph_support as graph

PACKAGE = Path(__file__).resolve().parent.parent / "src" / "noust"

#: What may be loaded when the console starts: the package, which imports nothing, and the reader
#: of whether the assistant is on.
ALLOWED = {"noust.core.assistant", "noust.core.assistant.state"}

#: What is only loaded when a request or a command uses the assistant.
LOADED_WHEN_USED = (
    "noust.core.assistant.client",
    "noust.core.assistant.settings",
    "noust.core.assistant.limits",
    "noust.core.assistant.masking",
    "noust.core.assistant.probe",
    "noust.core.assistant.conversations",
    "noust.core.assistant.presets",
    "noust.core.assistant.bm25",
    "noust.core.assistant.calls",
    "noust.core.assistant.docsearch",
    "noust.core.assistant.opsearch",
    "noust.core.assistant.prompt",
    "noust.core.assistant.provenance",
    "noust.core.assistant.records",
    "noust.web.assistant",
    "noust.web.assistant.identity",
    "noust.web.assistant.bridge",
    "noust.web.assistant.context",
    "noust.web.assistant.loop",
    "noust.web.assistant.outcome",
    "noust.web.assistant.present",
    "noust.web.assistant.toolbox",
    "noust.web.assistant.turns",
)


@cache
def _edges() -> list[graph.Edge]:
    return graph.read_edges(PACKAGE)


def closure(*roots: str) -> dict[str, str | None]:
    return graph.reach(
        _edges(), roots, frozenset({graph.MODULE}), packages=graph.package_modules(PACKAGE)
    )


@pytest.mark.parametrize(
    "roots",
    [
        ("noust.main", "noust.cli.app"),
        ("noust.web.server",),
        ("noust.web.api.router",),
    ],
    ids=["noust --version", "the console", "the API's router"],
)
def test_only_the_reader_of_the_state_is_loaded_at_start(roots: tuple[str, ...]) -> None:
    loaded = closure(*roots)
    assistant = {
        m for m in loaded if m == "noust.core.assistant" or m.startswith("noust.core.assistant.")
    }
    assert assistant <= ALLOWED, f"loaded at start: {sorted(assistant - ALLOWED)}"
    for module in LOADED_WHEN_USED:
        assert module not in loaded, module


def test_the_state_reader_imports_nothing_of_the_assistant() -> None:
    loaded = set(closure("noust.core.assistant.state"))
    assert not {m for m in loaded if m.startswith(("noust.web", "noust.cli"))}
    assert "noust.core.assistant.client" not in loaded


def test_the_command_is_not_loaded_until_it_is_run() -> None:
    assert "noust.cli.commands.assistant" not in closure("noust.cli.app")
