# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An application that verifies the engine's certificate (3.4, C-26).

A link may say how the application treats the engine's certificate, and what is pinned is
that Noust never says what it cannot keep:

- **a mode the engine cannot keep is refused before the application is touched**, naming
  what would let it: the server's own engine can be asked to encrypt and nothing more,
  an instance that does not serve TLS has to be turned on first, Redis cannot at all;
- **what the application is given is the client library's own words**: the mode and the
  authority's public certificate in the connection string (and in ``DB_SSL_*`` when the
  link writes the ``DB_*`` variables), and the authority's key is nowhere near;
- **the mode follows the database**: a move or a migration keeps it, refuses an engine that
  cannot keep it, and a migration from the host's engine makes the new instance serve TLS
  when an application connects with it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

import pytest

from noust.core.db_instance_store import TLS_OFF, TLS_OPTIONAL, DbInstanceStore
from noust.core.exceptions import DatabaseError, ValidationError
from noust.core.runner import set_runner
from noust.managers.database import tls_authority
from noust.managers.database.instance_tls import InstanceTls
from noust.managers.database.moves import DatabaseMover
from noust.managers.database.records import DatabaseLink, DatabaseRecords
from noust.managers.database.tls_authority import TlsAuthority
from noust.managers.database.tls_links import (
    LINK_MODES,
    check_link_tls,
    client_options,
    extra_variables,
)
from tests.database_migration_support import Lab, build_lab
from tests.database_move_support import (
    APP_PASSWORD,
    DOMAIN,
    HOST,
    INSTANCE,
    INSTANCE_KEY,
    FakePolicies,
    Rig,
    build_rig,
)
from tests.test_database_tls import TlsHost, UnprivilegedFs

CA = Path("/etc/noust/tls/ca.crt")


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Rig]:
    monkeypatch.setattr(tls_authority, "authority_dir", lambda: CA.parent)
    yield from build_rig(tmp_path, monkeypatch)


def serve(rig: Rig, mode: str = TLS_OPTIONAL) -> None:
    """Make the instance serve TLS, as an operator's `noust db instance tls` would."""
    DbInstanceStore(rig.store).set_tls_mode(INSTANCE, mode)


def put_database_on_instance(rig: Rig) -> None:
    rig.instance["dbs"]["shop_db"] = {"owner": "shop_user", "rows": "live"}
    rig.instance["users"]["shop_user"] = APP_PASSWORD
    rig.instance["profiles"][("shop_user", "shop_db")] = "owner"


# ===================================================================== the words


class TestWhatAClientIsTold:
    @pytest.mark.parametrize(
        ("engine", "mode", "expected"),
        [
            ("postgresql", "require", {"sslmode": "require"}),
            (
                "postgresql@pg16",
                "verify-full",
                {"sslmode": "verify-full", "sslrootcert": str(CA)},
            ),
            ("mysql@my8", "require", {"ssl-mode": "REQUIRED"}),
            (
                "mysql@maria",
                "verify-ca",
                {"ssl-mode": "VERIFY_CA", "ssl-ca": str(CA)},
            ),
            (
                "mongodb@docs",
                "verify-full",
                {"tls": "true", "tlsCAFile": str(CA)},
            ),
        ],
    )
    def test_each_engine_gets_the_parameters_its_clients_read(
        self, engine: str, mode: str, expected: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(tls_authority, "authority_dir", lambda: CA.parent)

        assert client_options(engine, mode) == expected

    def test_mongodb_that_only_encrypts_does_not_check_who_answers(self) -> None:
        options = client_options("mongodb", "require", CA)

        assert options["tls"] == "true" and options["tlsInsecure"] == "true"
        assert "tlsCAFile" not in options

    def test_verifying_the_authority_but_not_the_name_says_so_for_mongodb(self) -> None:
        assert client_options("mongodb", "verify-ca", CA)["tlsAllowInvalidHostnames"] == "true"

    def test_no_mode_asks_nothing_of_the_client(self) -> None:
        assert client_options("postgresql", None) == {}
        assert extra_variables(None) == {}

    def test_the_variables_name_the_authority_only_where_something_is_verified(self) -> None:
        assert extra_variables("require", CA) == {"DB_SSL_MODE": "require"}
        assert extra_variables("verify-full", CA) == {
            "DB_SSL_MODE": "verify-full",
            "DB_SSL_CA": str(CA),
        }


# ================================================================= what is refused


class TestWhatCannotBeKept:
    def test_a_word_that_is_not_a_mode_is_refused_with_the_ones_that_are(self) -> None:
        with pytest.raises(ValidationError) as caught:
            check_link_tls(HOST, "strict")

        assert caught.value.field == "tls"
        assert all(mode in caught.value.details for mode in LINK_MODES)

    def test_no_mode_is_nothing_to_keep(self, rig: Rig) -> None:
        assert check_link_tls(INSTANCE_KEY, None, instances=DbInstanceStore(rig.store)) is None

    def test_redis_and_valkey_are_refused(self) -> None:
        with pytest.raises(ValidationError) as caught:
            check_link_tls("redis", "require")

        assert "do not serve TLS" in caught.value.message

    def test_the_servers_own_engine_can_be_asked_to_encrypt_and_no_more(self) -> None:
        assert check_link_tls(HOST, "require") is not None
        with pytest.raises(ValidationError) as caught:
            check_link_tls(HOST, "verify-full")

        assert "noust db move" in caught.value.details

    def test_an_instance_that_does_not_serve_tls_has_to_be_turned_on_first(self, rig: Rig) -> None:
        with pytest.raises(DatabaseError) as caught:
            check_link_tls(INSTANCE_KEY, "verify-full", instances=DbInstanceStore(rig.store))

        assert f"noust db instance tls {INSTANCE} --mode optional" in caught.value.details

    def test_one_that_serves_it_keeps_every_mode(self, rig: Rig) -> None:
        serve(rig)

        for mode in LINK_MODES:
            found = check_link_tls(INSTANCE_KEY, mode, instances=DbInstanceStore(rig.store))
            assert found is not None and found.mode == mode

    def test_an_instance_noust_does_not_know_is_refused(self, rig: Rig) -> None:
        with pytest.raises(ValidationError) as caught:
            check_link_tls("postgresql@elsewhere", "require", instances=DbInstanceStore(rig.store))

        assert "cannot tell what TLS it serves" in caught.value.message


# ======================================================================= linking


class TestLinkingWithTls:
    def test_the_connection_string_carries_the_mode_and_the_authority(self, rig: Rig) -> None:
        serve(rig)
        put_database_on_instance(rig)

        rig.service.link(
            DOMAIN, INSTANCE_KEY, "shop_db", env_var="SHOP_SECURE_URL", tls_mode="verify-full"
        )

        url = rig.env()["SHOP_SECURE_URL"]
        assert "sslmode=verify-full" in url and f"sslrootcert={quote(str(CA), safe='')}" in url
        assert "DB_SSL_MODE" not in rig.env(), "the DB_* variables were not asked for"
        [link] = DatabaseRecords(rig.store).links(app_id=rig.app.id, engine=INSTANCE_KEY)
        assert link.tls_mode == "verify-full"

    def test_the_extra_variables_name_the_authority_for_clients_that_take_them_apart(
        self, rig: Rig
    ) -> None:
        serve(rig)
        put_database_on_instance(rig)

        rig.service.link(
            DOMAIN,
            INSTANCE_KEY,
            "shop_db",
            env_var="SHOP_SECURE_URL",
            extra_vars=True,
            tls_mode="verify-ca",
        )

        env = rig.env()
        assert env["DB_SSL_MODE"] == "verify-ca" and env["DB_SSL_CA"] == str(CA)

    def test_the_hosts_engine_is_asked_to_encrypt(self, rig: Rig) -> None:
        rig.service.link(DOMAIN, HOST, "shop_db", tls_mode="require")

        assert "sslmode=require" in rig.env()["DATABASE_URL"]
        assert "sslrootcert" not in rig.env()["DATABASE_URL"], "nothing is verified"

    def test_a_refusal_leaves_the_application_as_it_was(self, rig: Rig) -> None:
        before = rig.env()

        with pytest.raises(ValidationError):
            rig.service.link(DOMAIN, HOST, "shop_db", tls_mode="verify-full")

        assert rig.env() == before
        assert rig.gate["restarts"] == []

    def test_linking_again_without_a_mode_takes_the_variables_it_made_away(self, rig: Rig) -> None:
        rig.service.link(DOMAIN, HOST, "shop_db", extra_vars=True, tls_mode="require")
        assert rig.env()["DB_SSL_MODE"] == "require"

        rig.service.link(DOMAIN, HOST, "shop_db", extra_vars=True)

        assert "DB_SSL_MODE" not in rig.env()
        assert "sslmode" not in rig.env()["DATABASE_URL"]
        [link] = DatabaseRecords(rig.store).links(app_id=rig.app.id, engine=HOST)
        assert link.tls_mode is None

    def test_unlinking_removes_the_tls_variables_too(self, rig: Rig) -> None:
        rig.service.link(DOMAIN, HOST, "shop_db", extra_vars=True, tls_mode="require")

        rig.service.unlink(DOMAIN, HOST, "shop_db")

        assert not any(name.startswith("DB_SSL") for name in rig.env())


# ========================================================================= a move


def link_with_mode(rig: Rig, mode: str) -> None:
    records = DatabaseRecords(rig.store)
    records.save_link(
        DatabaseLink(
            app_id=rig.app.id or 0,
            engine=HOST,
            db_name="shop_db",
            username="shop_user",
            tls_mode=mode,
        )
    )


class TestAMoveKeepsTheMode:
    def test_it_is_refused_towards_an_engine_that_cannot_keep_it(self, rig: Rig) -> None:
        link_with_mode(rig, "require")

        plan = DatabaseMover(rig.service, backups=FakePolicies()).plan(
            HOST, "shop_db", INSTANCE_KEY
        )

        assert any(
            DOMAIN in blocker and "connects with TLS (require)" in blocker
            for blocker in plan.blockers
        )

    def test_towards_one_that_serves_tls_the_application_keeps_the_mode(self, rig: Rig) -> None:
        link_with_mode(rig, "require")
        serve(rig)

        DatabaseMover(rig.service, backups=FakePolicies()).move(HOST, "shop_db", INSTANCE_KEY)

        assert "sslmode=require" in rig.env()["DATABASE_URL"]
        [link] = DatabaseRecords(rig.store).links(app_id=rig.app.id, engine=INSTANCE_KEY)
        assert link.tls_mode == "require"

    def test_a_link_that_asked_for_nothing_is_not_refused_by_an_engine_without_tls(
        self, rig: Rig
    ) -> None:
        plan = DatabaseMover(rig.service, backups=FakePolicies()).plan(
            HOST, "shop_db", INSTANCE_KEY
        )

        assert plan.blockers == []
        assert DbInstanceStore(rig.store).get(INSTANCE).tls_mode == TLS_OFF  # type: ignore[union-attr]


# ================================================================== a migration


@pytest.fixture
def lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Lab]:
    monkeypatch.setattr(tls_authority, "authority_dir", lambda: tmp_path / "etc" / "noust" / "tls")
    for built in build_lab(tmp_path, monkeypatch):
        set_runner(TlsHost(tmp_path))
        built.managed._fs = UnprivilegedFs()
        yield built


def verify_on_the_host(lab: Lab) -> None:
    DatabaseRecords(lab.world.store).save_link(
        DatabaseLink(
            app_id=lab.app.id or 0,
            engine=HOST,
            db_name="shop_db",
            username="shop_user",
            tls_mode="require",
        )
    )


class TestAMigrationKeepsTheMode:
    def test_the_plan_says_the_new_instance_will_serve_tls(self, lab: Lab) -> None:
        verify_on_the_host(lab)

        plan = lab.migrations.plan(HOST, "17", memory="1g")

        assert plan.blockers == []
        assert plan.tls_mode == "optional"
        assert any("connects with TLS" in warning for warning in plan.warnings)

    def test_an_engine_nobody_connects_to_with_tls_makes_one_that_does_not(self, lab: Lab) -> None:
        plan = lab.migrations.plan(HOST, "17", memory="1g")

        assert plan.tls_mode == "off"

    def test_the_new_instance_serves_tls_and_the_application_keeps_asking_for_it(
        self, lab: Lab
    ) -> None:
        verify_on_the_host(lab)

        done = lab.migrations.run(HOST, "17", memory="1g")

        assert lab.managed.get(done.target).tls_mode == "optional"
        assert "sslmode=require" in lab.env()["DATABASE_URL"]
        [link] = DatabaseRecords(lab.world.store).links(app_id=lab.app.id)
        assert link.tls_mode == "require"

    def test_going_back_keeps_it_too(self, lab: Lab) -> None:
        verify_on_the_host(lab)
        done = lab.migrations.run(HOST, "17", memory="1g")

        lab.migrations.rollback(done.migration)

        assert "sslmode=require" in lab.env()["DATABASE_URL"]
        [link] = DatabaseRecords(lab.world.store).links(app_id=lab.app.id)
        assert (link.engine, link.tls_mode) == (HOST, "require")

    def test_an_instance_that_serves_tls_makes_one_that_serves_it_as_well(self, lab: Lab) -> None:
        lab.managed.create("pg16", "postgresql", version="16", memory="1g")
        InstanceTls(lab.managed, TlsAuthority(fs=UnprivilegedFs())).set_mode("pg16", "required")

        plan = lab.migrations.plan("pg16", "17", memory="1g")

        assert plan.tls_mode == "required"
        assert any("serves TLS as required" in warning for warning in plan.warnings)
