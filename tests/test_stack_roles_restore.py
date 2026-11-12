# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A real restore of a stack's PostgreSQL dump onto a container that does not have Noust's roles (item 193).

The drill loads a dump without its grants and says so. A real restore cannot: it puts back who owns
what and who may read it, so ``GRANT ... TO noust_ro_<app>`` fails on a container that never had the
read-only role the console's SQL signs in as (a new container, or another server). Noust made that
role, so Noust makes it again before it loads; a role the dump names that Noust did not make is the
operator's, and the restore stops before it touches anything and says which one and how to make it.
"""

from __future__ import annotations

import gzip
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import BackupError
from noust.core.runner import CommandResult, FakeRunner
from noust.managers.stack_databases import StackDatabase, restore_stack_database
from noust.managers.stack_roles import (
    create_statement,
    is_noust_role,
    plan_roles,
    roles_named_by,
)

POSTGRES = StackDatabase("postgres", "postgres", "shop", "shop")

SCRIPT = r'''
--
-- PostgreSQL database dump
--
SET statement_timeout = 0;
CREATE TABLE public.orders (id integer);
ALTER TABLE public.orders OWNER TO shop;
REVOKE ALL ON TABLE public.orders FROM PUBLIC;
GRANT SELECT ON TABLE public.orders TO noust_ro_shop;
GRANT USAGE ON SCHEMA public TO "reports ""team""";
ALTER DEFAULT PRIVILEGES FOR ROLE shop IN SCHEMA public GRANT SELECT ON TABLES TO noust_ro_shop;
'''


class TestWhichRolesADumpNames:
    """Only the statements that need a role to exist are read, and only for what they name."""

    def test_owners_grantees_and_default_privileges(self) -> None:
        assert roles_named_by(SCRIPT) == ["noust_ro_shop", 'reports "team"', "shop"]

    def test_public_and_the_current_user_are_not_roles(self) -> None:
        script = (
            "GRANT SELECT ON TABLE public.t TO PUBLIC;\n"
            "ALTER TABLE public.t OWNER TO CURRENT_USER;\n"
            "GRANT SELECT ON TABLE public.t TO session_user;\n"
        )
        assert roles_named_by(script) == []

    def test_a_list_of_grantees_and_a_grant_option(self) -> None:
        script = "GRANT SELECT, INSERT ON TABLE public.t TO alice, bob WITH GRANT OPTION;\n"
        assert roles_named_by(script) == ["alice", "bob"]

    def test_a_role_granted_to_a_role(self) -> None:
        assert roles_named_by("GRANT readers TO alice GRANTED BY admin;\n") == [
            "admin",
            "alice",
        ]

    def test_a_table_whose_name_looks_like_a_grant_does_not_hide_the_real_one(self) -> None:
        script = 'GRANT SELECT ON TABLE public."a TO b" TO carol;\n'
        assert roles_named_by(script) == ["carol"]

    def test_a_function_body_is_not_a_statement(self) -> None:
        script = (
            "CREATE FUNCTION public.f() RETURNS void AS $$\n"
            "GRANT ALL ON TABLE public.t TO mallory;\n"
            "$$ LANGUAGE sql;\n"
        )
        assert roles_named_by(script) == []

    def test_a_comment_is_not_a_statement(self) -> None:
        assert roles_named_by("-- GRANT ALL ON TABLE t TO mallory;\n") == []


class TestWhichRolesAreNousts:
    """What Noust made is told by its own prefixes, and only by names it could have made."""

    @pytest.mark.parametrize(
        "name", ["noust_ro_shop", "noust_ro_shop-example-com", "noust_load_a1"]
    )
    def test_its_own(self, name: str) -> None:
        assert is_noust_role(name)

    @pytest.mark.parametrize(
        "name",
        [
            "shop",
            "noust_ro_",
            "noust_rooted",
            "Noust_ro_shop",
            "noust_ro_shop;drop",
            'noust_ro_"x',
            "noust_ro_a b",
            "noust_ro_a\nb",
        ],
    )
    def test_nobody_elses_and_never_a_name_that_needs_quoting(self, name: str) -> None:
        assert not is_noust_role(name)

    def test_the_statement_that_makes_one_quotes_it(self) -> None:
        assert create_statement("noust_ro_shop") == 'CREATE ROLE "noust_ro_shop" NOLOGIN'

    def test_a_plan_makes_only_what_is_missing_and_only_what_is_nousts(self) -> None:
        plan = plan_roles(
            ["noust_ro_shop", "shop", "reports", "noust_load_x"],
            existing={"shop", "noust_load_x"},
        )
        assert plan.create == ["noust_ro_shop"]
        assert plan.missing == ["reports"]


class Engine(FakeRunner):
    """A stack's PostgreSQL container: what it prints for a dump's script and for its roles."""

    def __init__(self, *, script: str, roles: list[str]) -> None:
        super().__init__()
        self.script_text = script
        self.roles = roles
        self.running = True

    def run(self, argv: Any, **kwargs: Any) -> CommandResult:
        result = super().run(argv, **kwargs)
        call = tuple(str(part) for part in argv)
        if "ps" in call and "-q" in call:
            return replace(result, stdout="abc123\n" if self.running else "")
        if "pg_restore" in call and "--schema-only" in call:
            return replace(result, stdout=self.script_text)
        if "psql" in call and any("pg_roles" in part for part in call):
            return replace(result, stdout="\n".join(self.roles) + "\n")
        return result


class Host:
    domain = "shop.example.com"

    def __init__(self, runner: FakeRunner, app_path: Path) -> None:
        self.runner = runner
        self.app_path = app_path

    def _compose(self, *args: str, project: str | None = None) -> list[str]:
        return ["docker", "compose", "-p", project or "shop", "-f", "stack.yml", *args]


@pytest.fixture
def dump(tmp_path: Path) -> Path:
    path = tmp_path / "stack-postgres-postgres-shop.dump.gz"
    path.write_bytes(gzip.compress(b"PGDMP"))
    return path


def host_with(tmp_path: Path, runner: FakeRunner) -> Host:
    app = tmp_path / "app"
    app.mkdir()
    return Host(runner, app)


def restores(runner: FakeRunner) -> list[tuple[str, ...]]:
    return [call for call in runner.calls if "--single-transaction" in call]


def role_makers(runner: FakeRunner) -> list[tuple[str, ...]]:
    return [call for call in runner.calls if any(part.startswith("CREATE ROLE") for part in call)]


class TestARealRestoreMakesNoustRolesFirst:
    def test_the_read_only_role_is_made_before_the_dump_is_loaded(
        self, tmp_path: Path, dump: Path
    ) -> None:
        runner = Engine(script=SCRIPT, roles=["shop", "postgres", 'reports "team"'])
        host = host_with(tmp_path, runner)

        restore_stack_database(host, POSTGRES, dump, sleep=lambda _s: None)

        made = role_makers(runner)
        assert len(made) == 1
        assert 'CREATE ROLE "noust_ro_shop" NOLOGIN' in made[0]
        assert runner.calls.index(made[0]) < runner.calls.index(restores(runner)[0])

    def test_the_load_is_the_normal_one_with_its_grants(self, tmp_path: Path, dump: Path) -> None:
        runner = Engine(script=SCRIPT, roles=["shop", 'reports "team"'])

        restore_stack_database(host_with(tmp_path, runner), POSTGRES, dump, sleep=lambda _s: None)

        (load,) = restores(runner)
        assert "--no-acl" not in load and "--no-owner" not in load

    def test_making_a_role_puts_no_password_on_a_command_line(
        self, tmp_path: Path, dump: Path
    ) -> None:
        runner = Engine(script=SCRIPT, roles=["shop", 'reports "team"'])

        restore_stack_database(host_with(tmp_path, runner), POSTGRES, dump, sleep=lambda _s: None)

        (made,) = role_makers(runner)
        # The statement is the last argument; PGPASSWORD above it is only the name of the variable
        # the container's own script fills.
        assert made[-1] == 'CREATE ROLE "noust_ro_shop" NOLOGIN'

    def test_a_role_that_is_already_there_is_not_made_again(
        self, tmp_path: Path, dump: Path
    ) -> None:
        runner = Engine(script=SCRIPT, roles=["shop", "noust_ro_shop", 'reports "team"'])

        restore_stack_database(host_with(tmp_path, runner), POSTGRES, dump, sleep=lambda _s: None)

        assert role_makers(runner) == []
        assert len(restores(runner)) == 1

    def test_a_dump_that_names_no_missing_role_costs_nothing_but_the_reading(
        self, tmp_path: Path, dump: Path
    ) -> None:
        runner = Engine(script="CREATE TABLE public.t (id integer);\n", roles=["shop"])

        restore_stack_database(host_with(tmp_path, runner), POSTGRES, dump, sleep=lambda _s: None)

        assert role_makers(runner) == []
        assert len(restores(runner)) == 1


class TestARoleThatIsNotNoustsStopsTheRestoreBeforeAnythingChanges:
    def test_it_names_the_role_and_the_command_that_makes_it(
        self, tmp_path: Path, dump: Path
    ) -> None:
        runner = Engine(script=SCRIPT, roles=["shop"])
        host = host_with(tmp_path, runner)

        with pytest.raises(BackupError) as caught:
            restore_stack_database(host, POSTGRES, dump, sleep=lambda _s: None)

        text = f"{caught.value.message}\n{caught.value.details}"
        assert 'reports "team"' in text
        assert "CREATE ROLE" in text and "docker compose" in text
        assert restores(runner) == []
        assert role_makers(runner) == []

    def test_a_role_name_that_would_need_escaping_is_never_made_by_noust(
        self, tmp_path: Path, dump: Path
    ) -> None:
        script = 'GRANT SELECT ON TABLE public.t TO "noust_ro_x""; DROP ROLE shop; --";\n'
        runner = Engine(script=script, roles=["shop"])

        with pytest.raises(BackupError):
            restore_stack_database(
                host_with(tmp_path, runner), POSTGRES, dump, sleep=lambda _s: None
            )

        assert role_makers(runner) == []
        assert restores(runner) == []


class TestOtherEnginesAreUntouched:
    def test_a_mysql_restore_reads_no_roles(self, tmp_path: Path) -> None:
        runner = Engine(script="", roles=[])
        mysql = StackDatabase("db", "mysql", "shop", "shop")
        path = tmp_path / "stack-db-mysql-shop.sql.gz"
        path.write_bytes(gzip.compress(b"-- dump"))

        restore_stack_database(host_with(tmp_path, runner), mysql, path, sleep=lambda _s: None)

        assert not any("pg_restore" in call for call in runner.calls)
