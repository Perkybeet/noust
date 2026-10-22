# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Keeping the ownership statements of a dump whose roles exist (4.0, checklist H-18).

An import that restores the accounts of the source has the roles the dump names, so its
``ALTER ... OWNER TO`` and its ``GRANT`` and ``REVOKE`` statements are worth keeping. Some of them
still name a role that is not there or one the loader may not become (the source's ``postgres``):
those statements are left out one by one, and the object stays with whoever loads the dump, instead
of the whole load failing on the first of them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.managers.database.psql_script import RoleFilter, strip_owners

FILTER = RoleFilter(
    owners=frozenset({"shopapp", "legacy"}),
    grantees=frozenset({"shopapp", "legacy", "reporting"}),
)


def strip(tmp_path: Path, text: str, role_filter: RoleFilter = FILTER) -> tuple[str, int]:
    source = tmp_path / "in.sql"
    source.write_text(text)
    destination = tmp_path / "out.sql"
    left_out = strip_owners(source, destination, keep=role_filter)
    return destination.read_text(), left_out


class TestWhoMayOwn:
    def test_an_owner_the_loader_can_become_is_kept(self, tmp_path: Path) -> None:
        text = "ALTER TABLE public.orders OWNER TO shopapp;\n"

        out, left_out = strip(tmp_path, text)

        assert out == text and left_out == 0

    def test_an_owner_it_cannot_become_is_left_out(self, tmp_path: Path) -> None:
        out, left_out = strip(tmp_path, "ALTER FUNCTION public.f() OWNER TO postgres;\n")

        assert out == "" and left_out == 1

    def test_a_role_that_exists_but_is_not_the_loaders_to_become_is_not_an_owner(
        self, tmp_path: Path
    ) -> None:
        # reporting can be granted to, but nothing can be handed to it.
        out, left_out = strip(tmp_path, "ALTER TABLE t OWNER TO reporting;\n")

        assert out == "" and left_out == 1

    def test_names_are_compared_as_the_server_reads_them(self, tmp_path: Path) -> None:
        kept = 'ALTER TABLE t OWNER TO "shopapp";\nALTER TABLE u OWNER TO SHOPAPP;\n'

        out, left_out = strip(tmp_path, kept)

        assert out == kept and left_out == 0


class TestWhoMayBeGranted:
    @pytest.mark.parametrize(
        "statement",
        [
            "GRANT SELECT ON TABLE public.orders TO reporting;",
            "GRANT ALL ON SCHEMA public TO PUBLIC;",
            "REVOKE ALL ON SCHEMA public FROM PUBLIC;",
            "REVOKE CONNECT,TEMPORARY ON DATABASE blog FROM PUBLIC;",
            "GRANT SELECT ON TABLE public.orders TO reporting, shopapp;",
            'GRANT SELECT ON TABLE public."a TO b" TO reporting;',
            "GRANT SELECT ON ALL TABLES IN SCHEMA public TO reporting WITH GRANT OPTION;",
            "GRANT CONNECT ON DATABASE shop TO reporting GRANTED BY shopapp;",
            "REVOKE SELECT ON TABLE t FROM reporting CASCADE;",
        ],
    )
    def test_a_statement_that_names_only_roles_that_exist_is_kept(
        self, tmp_path: Path, statement: str
    ) -> None:
        out, left_out = strip(tmp_path, statement + "\n")

        assert out == statement + "\n" and left_out == 0

    @pytest.mark.parametrize(
        "statement",
        [
            "GRANT SELECT ON TABLE public.orders TO stranger;",
            "GRANT SELECT ON TABLE public.orders TO reporting, stranger;",
            "REVOKE ALL ON TABLE t FROM stranger;",
            'GRANT SELECT ON TABLE public."a TO reporting" TO stranger;',
            "GRANT SELECT ON TABLE t TO GROUP stranger;",
            "ALTER DEFAULT PRIVILEGES FOR ROLE shopapp IN SCHEMA public GRANT SELECT ON TABLES TO reporting;",
        ],
    )
    def test_a_statement_that_names_a_role_that_does_not_exist_is_left_out(
        self, tmp_path: Path, statement: str
    ) -> None:
        out, left_out = strip(tmp_path, statement + "\n")

        assert out == "" and left_out == 1


class TestOnlyWhereItIsAStatement:
    def test_a_line_inside_a_function_body_is_left_alone(self, tmp_path: Path) -> None:
        text = (
            "CREATE FUNCTION f() RETURNS void LANGUAGE sql AS $$\n"
            "GRANT SELECT ON TABLE t TO stranger;\n"
            "$$;\n"
        )

        out, left_out = strip(tmp_path, text)

        assert out == text and left_out == 0

    def test_copy_data_is_left_alone(self, tmp_path: Path) -> None:
        text = "COPY t (note) FROM stdin;\nGRANT SELECT ON TABLE t TO stranger;\n\\.\n"

        out, left_out = strip(tmp_path, text)

        assert out == text and left_out == 0

    def test_what_is_kept_and_what_is_not_come_out_in_order(self, tmp_path: Path) -> None:
        text = (
            "CREATE TABLE t (i int);\n"
            "ALTER TABLE t OWNER TO shopapp;\n"
            "ALTER TABLE t OWNER TO postgres;\n"
            "GRANT SELECT ON TABLE t TO reporting;\n"
            "GRANT SELECT ON TABLE t TO stranger;\n"
        )

        out, left_out = strip(tmp_path, text)

        assert out == (
            "CREATE TABLE t (i int);\n"
            "ALTER TABLE t OWNER TO shopapp;\n"
            "GRANT SELECT ON TABLE t TO reporting;\n"
        )
        assert left_out == 2
