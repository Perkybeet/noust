# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Reading a ``mysqldump --all-databases`` and a file of accounts (4.0, checklist H-18).

The fixtures are what a real MariaDB 11.4 wrote for a server with two user databases (one holding
rows that look like the boundaries the reader cuts at, and a view, which mysqldump writes in a second
pass under a second ``-- Current Database`` marker), the system schema ``mysql``, and three accounts
(one of them a root-like administrator). What is pinned:

- each user database comes out as one file that holds everything of it, both passes, without the
  statements that name the database (they are the marker and the target's choice), with the header
  the dump set up and the footer it restored;
- the server's own schemas are recognised and not cut out: their tables are the server's accounts and
  their loading would overwrite them;
- accounts are data: a text file of ``CREATE USER`` and ``GRANT`` is read into what Noust is willing to
  make (a password hash of a shape the server stores, privileges on a database or below), and
  everything else is named as not carried;
- the statements Noust writes are built from that data.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.managers.database.mysql_cluster import (
    SYSTEM_SCHEMAS,
    MysqlAccount,
    MysqlGrant,
    is_mysql_cluster,
    read_accounts,
    read_mysql_cluster,
    render_account,
)

FIXTURES = Path(__file__).parent / "fixtures" / "mysql_dump"


def scratch(tmp_path: Path) -> Path:
    directory = tmp_path / "scratch"
    directory.mkdir(exist_ok=True)
    return directory


@pytest.fixture
def cluster(tmp_path: Path):  # type: ignore[no-untyped-def]
    return read_mysql_cluster(FIXTURES / "all-mariadb11.sql", scratch(tmp_path))


class TestTheDatabases:
    def test_the_user_databases_are_found_in_order_with_their_character_set(self, cluster) -> None:  # type: ignore[no-untyped-def]
        assert [(db.name, db.encoding, db.system) for db in cluster.databases] == [
            ("blog", "latin1", False),
            ("mysql", "utf8mb4", True),
            ("shop", "utf8mb4", False),
        ]

    def test_the_servers_own_schema_is_not_cut_out(self, cluster) -> None:  # type: ignore[no-untyped-def]
        by_name = {db.name: db for db in cluster.databases}

        assert by_name["mysql"].path is None
        assert {"mysql", "sys", "performance_schema", "information_schema"} == SYSTEM_SCHEMAS

    def test_a_database_holds_both_passes_and_not_the_statements_that_name_it(
        self, cluster
    ) -> None:  # type: ignore[no-untyped-def]
        shop = {db.name: db for db in cluster.databases}["shop"].path.read_text()  # type: ignore[union-attr]

        assert "CREATE TABLE `orders`" in shop
        assert "VIEW `v_orders` AS select `orders`.`id`" in shop  # the second pass
        assert "CREATE DATABASE" not in shop
        assert not any(line.startswith("USE ") for line in shop.splitlines())

    def test_rows_that_look_like_markers_are_data(self, cluster) -> None:  # type: ignore[no-untyped-def]
        shop = {db.name: db for db in cluster.databases}["shop"].path.read_text()  # type: ignore[union-attr]
        blog = {db.name: db for db in cluster.databases}["blog"].path.read_text()  # type: ignore[union-attr]

        assert "(4,'-- Current Database: `blog`');" in shop
        assert "(3,'line\\none -- not a marker')" in shop
        assert "CREATE TABLE `posts`" in blog and "CREATE TABLE `orders`" not in blog

    def test_each_file_has_the_header_the_dump_set_up_and_the_footer_it_restored(
        self, cluster
    ) -> None:  # type: ignore[no-untyped-def]
        for db in cluster.databases:
            if db.path is None:
                continue
            text = db.path.read_text()
            assert "/*!40101 SET NAMES utf8mb4 */;" in text
            assert "/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;" in text
            assert text.rstrip().splitlines()[-1].startswith("-- Dump completed")

    def test_the_files_are_private_and_their_size_is_known(self, cluster) -> None:  # type: ignore[no-untyped-def]
        for db in cluster.databases:
            if db.path is not None:
                assert (db.path.stat().st_mode & 0o777) == 0o600
                assert db.size == db.path.stat().st_size

    def test_the_file_is_told_by_its_head(self) -> None:
        head = (FIXTURES / "all-mariadb11.sql").read_bytes()[:8192]

        assert is_mysql_cluster(head)
        assert not is_mysql_cluster(b"-- MySQL dump 10.13\nCREATE TABLE t (i int);\n")


GRANTS = FIXTURES / "grants-mariadb11.sql"


class TestTheAccounts:
    def test_each_account_is_read_with_its_hash_and_what_it_may_do(self) -> None:
        accounts, _ignored = read_accounts(GRANTS)

        by_name = {account.name: account for account in accounts}
        assert set(by_name) == {"shopapp@%", "reader@10.0.%", "admin2@localhost"}
        shopapp = by_name["shopapp@%"]
        assert shopapp.password_hash == "*7648E52EC79D4BB879B7B913F0B8BBF8DC4AB6CC"
        assert shopapp.grants == (
            MysqlGrant(("SELECT", "INSERT", "UPDATE", "DELETE"), "shop", None),
        )
        assert by_name["reader@10.0.%"].grants == (MysqlGrant(("SELECT",), "shop", "orders"),)

    def test_the_power_of_a_root_like_account_is_named_and_not_carried(self) -> None:
        accounts, _ignored = read_accounts(GRANTS)

        admin = next(account for account in accounts if account.user == "admin2")

        assert admin.grants == ()
        assert "ALL PRIVILEGES ON *.*" in admin.dropped and "GRANT OPTION" in admin.dropped

    def test_a_hash_the_text_cannot_carry_makes_a_locked_account(self, tmp_path: Path) -> None:
        file = tmp_path / "g.sql"
        file.write_text(
            "CREATE USER `app`@`%` IDENTIFIED WITH 'caching_sha2_password' AS "
            "'$A$005$Md\\\\\"-j(Z(r^F' REQUIRE NONE PASSWORD EXPIRE DEFAULT ACCOUNT UNLOCK;\n"
            "GRANT USAGE ON *.* TO `app`@`%`;\n"
            "GRANT SELECT ON `shop`.* TO `app`@`%`;\n"
        )

        (account,), ignored = read_accounts(file)

        assert account.password_hash is None and account.locked
        assert any("caching_sha2_password" in note for note in account.dropped)
        assert ignored == []

    @pytest.mark.parametrize(
        "line",
        [
            "CREATE USER `a'; DROP TABLE x`@`%` IDENTIFIED BY PASSWORD '*" + "A" * 40 + "';",
            "CREATE USER `app`@`%; GRANT ALL ON *.* TO x` IDENTIFIED BY PASSWORD '*"
            + "A" * 40
            + "';",
            "DROP USER `app`@`%`;",
            "SET PASSWORD FOR `app`@`%` = 'x';",
            "GRANT SELECT ON `shop`.* TO `ghost`@`%`;",
        ],
    )
    def test_what_is_not_an_account_of_the_shapes_read_is_listed_not_run(
        self, tmp_path: Path, line: str
    ) -> None:
        file = tmp_path / "g.sql"
        file.write_text(line + "\n")

        accounts, ignored = read_accounts(file)

        assert accounts == []
        assert ignored

    @pytest.mark.parametrize(
        ("privilege", "carried"),
        [
            ("SELECT", True),
            ("CREATE ROUTINE", True),
            ("LOCK TABLES", True),
            ("FILE", False),
            ("SUPER", False),
            ("PROCESS", False),
            ("SHUTDOWN", False),
            ("RELOAD", False),
            ("CREATE USER", False),
            ("REPLICATION SLAVE", False),
        ],
    )
    def test_a_privilege_is_carried_only_when_it_is_about_the_data(
        self, tmp_path: Path, privilege: str, carried: bool
    ) -> None:
        file = tmp_path / "g.sql"
        file.write_text(
            "CREATE USER `app`@`%` IDENTIFIED BY PASSWORD '*" + "B" * 40 + "';\n"
            f"GRANT {privilege} ON `shop`.* TO `app`@`%`;\n"
        )

        (account,), _ = read_accounts(file)

        assert bool(account.grants) is carried
        assert (privilege in account.dropped) is (not carried)

    def test_a_grant_with_the_grant_option_is_carried_without_it(self, tmp_path: Path) -> None:
        file = tmp_path / "g.sql"
        file.write_text(
            "CREATE USER `app`@`%` IDENTIFIED BY PASSWORD '*" + "C" * 40 + "';\n"
            "GRANT SELECT ON `shop`.* TO `app`@`%` WITH GRANT OPTION;\n"
        )

        (account,), _ = read_accounts(file)

        assert account.grants == (MysqlGrant(("SELECT",), "shop", None),)
        assert "GRANT OPTION" in account.dropped

    def test_a_locked_account_stays_locked(self, tmp_path: Path) -> None:
        file = tmp_path / "g.sql"
        file.write_text(
            "CREATE USER `app`@`%` IDENTIFIED BY PASSWORD '*" + "D" * 40 + "' ACCOUNT LOCK;\n"
        )

        (account,), _ = read_accounts(file)

        assert account.locked and "ACCOUNT LOCK" in render_account(account, mariadb=True)


class TestWhatIsWritten:
    ACCOUNT = MysqlAccount(
        user="shopapp",
        host="%",
        password_hash="*7648E52EC79D4BB879B7B913F0B8BBF8DC4AB6CC",
        grants=(
            MysqlGrant(("SELECT", "INSERT"), "shop", None),
            MysqlGrant(("SELECT",), "shop", "o"),
        ),
    )

    def test_mariadb_is_given_its_own_words(self) -> None:
        text = render_account(self.ACCOUNT, mariadb=True)

        assert text.splitlines() == [
            "CREATE USER 'shopapp'@'%' IDENTIFIED BY PASSWORD '*7648E52EC79D4BB879B7B913F0B8BBF8DC4AB6CC';",
            "GRANT SELECT, INSERT ON `shop`.* TO 'shopapp'@'%';",
            "GRANT SELECT ON `shop`.`o` TO 'shopapp'@'%';",
        ]

    def test_mysql_is_given_its_own(self) -> None:
        text = render_account(self.ACCOUNT, mariadb=False)

        assert text.splitlines()[0] == (
            "CREATE USER 'shopapp'@'%' IDENTIFIED WITH mysql_native_password "
            "AS '*7648E52EC79D4BB879B7B913F0B8BBF8DC4AB6CC';"
        )

    def test_an_account_without_a_usable_password_is_locked(self) -> None:
        text = render_account(MysqlAccount(user="app", host="%", locked=True), mariadb=True)

        assert text.splitlines()[0] == "CREATE USER 'app'@'%' ACCOUNT LOCK;"
