# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The documentation a client reads stays true (J-21).

``docs/llms.txt`` and ``docs/llms-full.txt`` are generated from ``docs/``, so a
document edited without regenerating them leaves a client reading a stale
index; and the MCP document names tools and numbers that a change to the tool
list would silently falsify.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

from noust.web.mcp.tools import FORBIDDEN_PERMISSIONS, GROUPS, TOOLS

ROOT = Path(__file__).resolve().parent.parent


def generator() -> ModuleType:
    """
    Returns:
        ``scripts/generate_llms.py`` as a module.
    """
    spec = importlib.util.spec_from_file_location(
        "generate_llms", ROOT / "scripts" / "generate_llms.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_llms"] = module
    spec.loader.exec_module(module)
    return module


def test_the_committed_index_is_what_the_generator_writes() -> None:
    assert generator().main(["--check"]) == 0, "run: python scripts/generate_llms.py"


def test_the_index_follows_the_llms_txt_format() -> None:
    text = (ROOT / "docs" / "llms.txt").read_text(encoding="utf-8")
    lines = text.splitlines()

    assert lines[0] == "# Noust" and lines[2].startswith("> ")
    links = [line for line in lines if line.startswith("- [")]
    assert len(links) >= 15
    assert all(re.fullmatch(r"- \[[^\]]+\]\(https://[^)]+\.(md|txt)\): .+", line) for line in links)


def test_every_listed_document_exists_and_is_in_the_full_file() -> None:
    module = generator()
    full = module.render_full()

    for name, _purpose in module.DOCUMENTS:
        assert (ROOT / "docs" / name).exists(), name
        assert f"<!-- docs/{name} -->" in full


def test_no_secret_looking_text_is_published_to_clients() -> None:
    full = generator().render_full()

    assert not re.search(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", full)
    assert not re.search(r"noust_tok_[A-Za-z0-9_\-]{20,}", full)


def test_the_mcp_document_says_how_many_tools_there_are() -> None:
    text = (ROOT / "docs" / "mcp.md").read_text(encoding="utf-8")
    words = {27: "Twenty-seven", 26: "Twenty-six", 28: "Twenty-eight", 30: "Thirty"}

    assert f"{words.get(len(TOOLS), str(len(TOOLS)))} tools, in three groups" in text


def test_the_mcp_document_names_every_tool_and_no_tool_that_does_not_exist() -> None:
    text = (ROOT / "docs" / "mcp.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"`([a-z_]+)`\*?", text.split("## Setting it up")[0]))
    named = {tool.name for tool in TOOLS}

    assert named <= documented, sorted(named - documented)
    snake = {
        name
        for name in documented
        if "_" in name
        and name.split("_")[0]
        in {
            "list",
            "get",
            "deploy",
            "rollback",
            "activate",
            "cancel",
            "create",
            "restart",
            "start",
            "stop",
            "renew",
            "server",
            "application",
            "diagnose",
            "deployment",
            "job",
        }
    }
    assert snake <= named | {"mcp_disabled"}, sorted(snake - named)


@pytest.mark.parametrize("group", GROUPS)
def test_every_group_is_documented(group: str) -> None:
    assert f"`{group}`" in (ROOT / "docs" / "mcp.md").read_text(encoding="utf-8")


def test_the_forbidden_permissions_are_the_ones_the_document_lists() -> None:
    text = (ROOT / "docs" / "mcp.md").read_text(encoding="utf-8")
    paragraph = text.split("A test holds the")[1].split("A tool takes")[0]

    for permission in (
        "secrets.reveal",
        "root_equivalent",
        "apps.manage",
        "apps.terminal",
        "settings.manage",
        "security.manage",
        "teams.manage",
        "fleet.manage",
        "databases.write",
        "backups.manage",
    ):
        assert permission in paragraph
        assert permission in FORBIDDEN_PERMISSIONS
