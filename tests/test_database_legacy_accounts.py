# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The read-only console's accounts carry Noust's name from 4.0, and the ones an earlier release made go.

``noust_ro_<database>`` is made by the next read of a database; an account made under the old
prefix is dropped in the same step, since its password was never kept anywhere that survives a
read. A dump an earlier release took grants to the old name, so a restore makes both.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core import legacy_names
from noust.core.runner import FakeRunner
from noust.managers.database.base import (
    LEGACY_READ_ONLY_ACCOUNT_PREFIX,
    READ_ONLY_ACCOUNT_PREFIX,
    READ_ONLY_ACCOUNT_PREFIXES,
)
from noust.managers.database.mysql import MySQLManager
from noust.managers.database.postgres import PostgresManager
from tests.test_database_managers import mysql, postgres  # noqa: F401 - fixtures


def test_the_prefixes_are_the_two_spellings() -> None:
    assert READ_ONLY_ACCOUNT_PREFIX == "noust_ro_"
    assert LEGACY_READ_ONLY_ACCOUNT_PREFIX == legacy_names.READ_ONLY_PREFIX
    assert READ_ONLY_ACCOUNT_PREFIXES == (READ_ONLY_ACCOUNT_PREFIX, LEGACY_READ_ONLY_ACCOUNT_PREFIX)


@pytest.mark.parametrize("name", ["noust_ro_shop", f"{legacy_names.READ_ONLY_PREFIX}shop"])
def test_both_are_noust_s_own_and_never_listed_as_somebody_else_s(
    postgres: PostgresManager,  # noqa: F811
    name: str,
) -> None:
    assert postgres.is_internal_user(name) is True


class TestPostgres:
    def test_the_next_read_drops_the_role_of_before_and_makes_the_new_one(
        self,
        postgres: PostgresManager,  # noqa: F811
        runner: FakeRunner,
    ) -> None:
        runner.script(["psql"], stdout="1")

        role, _ = postgres._ensure_read_only_role("shop")

        assert role == "noust_ro_shop"
        script = next(text for text in runner.inputs if text and "DROP OWNED BY" in text)
        before, after = script.index("DROP OWNED BY"), script.index('CREATE ROLE "noust_ro_shop"')
        assert before < after, "the old role goes first"
        assert "'wasm_ro_shop'" in script
        assert 'ALTER ROLE "noust_ro_shop"' in script

    def test_its_password_file_goes_with_it(
        self,
        postgres: PostgresManager,  # noqa: F811
        runner: FakeRunner,
    ) -> None:
        runner.script(["psql"], stdout="1")
        old_file = postgres._read_only_password_file("wasm_ro_shop")
        old_file.parent.mkdir(parents=True, exist_ok=True)
        old_file.write_text("a-password-from-before\n")
        old_file.chmod(0o600)

        postgres._ensure_read_only_role("shop")

        assert not old_file.exists()
        assert postgres._read_only_password_file("noust_ro_shop").is_file()

    def test_a_restore_makes_both_roles_so_a_dump_of_before_restores(
        self,
        postgres: PostgresManager,  # noqa: F811
        runner: FakeRunner,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(PostgresManager, "create_database", lambda self, name, owner=None: None)

        postgres._create_for_restore("shop", "shop_user")

        made = [text for text in runner.inputs if text and "CREATE ROLE" in text]
        assert len(made) == 2
        assert any('CREATE ROLE "noust_ro_shop" NOLOGIN' in text for text in made)
        assert any('CREATE ROLE "wasm_ro_shop" NOLOGIN' in text for text in made)
        assert all("PASSWORD" not in text for text in made)

    def test_a_database_that_never_had_the_old_role_costs_one_read_and_drops_nothing(
        self,
        postgres: PostgresManager,  # noqa: F811
        runner: FakeRunner,
    ) -> None:
        runner.script(["psql"], stdout="")

        assert postgres.drop_legacy_read_only_account("shop") is False

    def test_a_long_name_keeps_its_digest_under_both_prefixes(
        self,
        postgres: PostgresManager,  # noqa: F811
        runner: FakeRunner,
    ) -> None:
        """The old name is worked out the same way, or the wrong role would be dropped."""
        from noust.managers.database.postgres import _read_only_role_name

        database = "d" * 80
        new = _read_only_role_name(database)
        old = _read_only_role_name(database, legacy_names.READ_ONLY_PREFIX)

        assert len(new) <= 63 and len(old) <= 63
        assert new.startswith("noust_ro_") and old.startswith(legacy_names.READ_ONLY_PREFIX)
        assert new[len("noust_ro_") :] == old[len(legacy_names.READ_ONLY_PREFIX) :] or (
            new.rsplit("_", 1)[1] == old.rsplit("_", 1)[1]
        ), "the same digest identifies the database"


class TestMySql:
    def test_the_next_read_drops_the_account_of_before(
        self,
        mysql: MySQLManager,  # noqa: F811
        runner: FakeRunner,
    ) -> None:
        runner.script(["mysql"], stdout="shop\n")

        mysql.execute_query(database="shop", query="SELECT 1", read_only=True)

        provisioning = next(
            text for text in runner.inputs if text and "CREATE USER IF NOT EXISTS" in text
        )
        assert "CREATE USER IF NOT EXISTS 'noust_ro_shop'@'localhost'" in provisioning
        assert "DROP USER IF EXISTS 'wasm_ro_shop'@'localhost'" in provisioning
        assert provisioning.index("GRANT SELECT") < provisioning.index("DROP USER IF EXISTS")

    def test_dropping_a_database_drops_both_accounts(
        self,
        mysql: MySQLManager,  # noqa: F811
        runner: FakeRunner,
    ) -> None:
        runner.script(["mysql"], stdout="1\n")

        mysql.drop_read_only_account("shop")

        statements = [text for text in runner.inputs if text]
        assert any("DROP USER IF EXISTS 'noust_ro_shop'@'localhost'" in text for text in statements)
        assert any("DROP USER IF EXISTS 'wasm_ro_shop'@'localhost'" in text for text in statements)

    def test_an_engine_that_never_had_the_old_account_has_nothing_to_drop(
        self,
        mysql: MySQLManager,  # noqa: F811
        runner: FakeRunner,
        tmp_path: Path,
    ) -> None:
        runner.script(["mysql"], stdout="")

        assert mysql.drop_legacy_read_only_account("shop") is False
