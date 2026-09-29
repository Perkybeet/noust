# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Who a provisioned database and user belong to.

A monorepo names its database and user in its own ``docker-compose.yml``,
which makes both names untrusted input. Before these tests, a repository
that set ``POSTGRES_USER`` to another application's user and
``POSTGRES_DB`` to a new name was handed that user's password (read from the
secret store) and a grant on its new database; one that named ``postgres``
or a database created outside Noust got ``ALL`` on it. These tests pin down
that the helper only ever reuses a database or a user recorded as the
requesting application's, and that a grant failing on a first deploy never
leaves the monorepo writing a ``DATABASE_URL`` from made-up credentials.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.exceptions import DatabaseError
from noust.core.logger import Logger
from noust.core.secrets import SecretStore
from noust.core.store import App, Database, NoustStore
from noust.deployers.helpers import databases as db_helpers
from noust.deployers.helpers.databases import RESERVED_NAMES, provision_database


class FakeManager:
    """A PostgreSQL manager stand-in whose grant can be made to fail."""

    ENGINE_NAME = "postgresql"
    DISPLAY_NAME = "PostgreSQL"
    MAX_DATABASE_NAME_LENGTH = 63
    MAX_USER_NAME_LENGTH = 63

    def __init__(self) -> None:
        self.databases: set[str] = set()
        self.users: dict[str, str] = {}
        self.calls: list[str] = []
        self.fail_grant = False

    def is_installed(self) -> bool:
        return True

    def validate_database_name(self, name: str) -> str:
        return name

    def validate_user_name(self, username: str) -> str:
        return username

    def database_exists(self, name: str) -> bool:
        return name in self.databases

    def create_database(self, name: str, **_kwargs: object) -> None:
        self.calls.append(f"create_database:{name}")
        self.databases.add(name)

    def user_exists(self, username: str, host: str = "localhost") -> bool:
        return username in self.users

    def create_user(self, username: str, password: str | None = None, **_kwargs: object) -> None:
        self.calls.append(f"create_user:{username}")
        self.users[username] = password or ""

    def grant_privileges(self, *, username: str, database: str) -> None:
        self.calls.append(f"grant:{username}:{database}")
        if self.fail_grant:
            raise DatabaseError("Failed to grant privileges", details="permission denied")

    def server_port(self) -> int:
        return 5432


class FakeRegistry:
    def __init__(self, manager: FakeManager) -> None:
        self._manager = manager

    def get(self, engine: str, verbose: bool = False) -> FakeManager | None:
        return self._manager if engine in ("postgresql", "postgres") else None


@pytest.fixture
def store(tmp_path: Path):
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def secret_store(tmp_path: Path) -> SecretStore:
    return SecretStore(root=tmp_path / "secrets")


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch) -> FakeManager:
    fake = FakeManager()
    monkeypatch.setattr(db_helpers, "DatabaseRegistry", FakeRegistry(fake))
    return fake


def _provision(
    store: NoustStore, secret_store: SecretStore, *, name: str, user: str, domain: str | None
):
    return provision_database(
        "postgresql",
        name=name,
        user=user,
        domain=domain,
        logger=Logger(),
        store=store,
        secret_store=secret_store,
    )


def _app(store: NoustStore, domain: str) -> App:
    return store.create_app(App(domain=domain, app_path=f"/srv/{domain}"))


# ---------------------------------------------------------------------------
# Another application's user
# ---------------------------------------------------------------------------


def test_a_user_owned_by_another_app_is_refused(
    store: NoustStore, secret_store: SecretStore, manager: FakeManager
) -> None:
    _app(store, "victim.example.com")
    victim = _provision(
        store, secret_store, name="victim_db", user="victim_user", domain="victim.example.com"
    )
    _app(store, "evil.example.com")
    manager.calls.clear()

    with pytest.raises(DatabaseError) as excinfo:
        _provision(
            store, secret_store, name="evil_db", user="victim_user", domain="evil.example.com"
        )

    assert "victim.example.com" in str(excinfo.value)
    assert victim.password not in str(excinfo.value)
    assert victim.password not in (excinfo.value.details or "")
    # Refused before anything was created or granted.
    assert manager.calls == []
    assert store.get_database("evil_db", "postgresql") is None


def test_a_user_owned_by_an_app_whose_rows_are_not_linked_yet_is_refused(
    store: NoustStore, secret_store: SecretStore, manager: FakeManager
) -> None:
    # A recipe provisions before its application row exists, so the row is
    # unlinked; the owner recorded beside the password still names it.
    _provision(store, secret_store, name="shop_db", user="shop_user", domain="shop.example.com")
    assert store.get_database("shop_db", "postgresql").app_id is None
    _app(store, "evil.example.com")
    manager.calls.clear()

    with pytest.raises(DatabaseError) as excinfo:
        _provision(store, secret_store, name="evil_db", user="shop_user", domain="evil.example.com")

    assert "shop.example.com" in str(excinfo.value)
    assert manager.calls == []


def test_an_unlinked_database_of_another_domain_is_refused(
    store: NoustStore, secret_store: SecretStore, manager: FakeManager
) -> None:
    _provision(store, secret_store, name="shop_db", user="shop_user", domain="shop.example.com")
    _app(store, "evil.example.com")
    manager.calls.clear()

    with pytest.raises(DatabaseError):
        _provision(store, secret_store, name="shop_db", user="evil_user", domain="evil.example.com")

    assert manager.calls == []


# ---------------------------------------------------------------------------
# Databases and users Noust did not create
# ---------------------------------------------------------------------------


def test_an_existing_database_with_no_store_row_is_refused(
    store: NoustStore, secret_store: SecretStore, manager: FakeManager
) -> None:
    manager.databases.add("accounting")
    _app(store, "evil.example.com")

    with pytest.raises(DatabaseError) as excinfo:
        _provision(
            store, secret_store, name="accounting", user="evil_user", domain="evil.example.com"
        )

    assert "accounting" in str(excinfo.value)
    assert "Noust did not create" in str(excinfo.value)
    assert manager.calls == []
    assert "evil_user" not in manager.users


def test_a_database_created_outside_an_application_is_refused(
    store: NoustStore, secret_store: SecretStore, manager: FakeManager
) -> None:
    # What `noust db create` records: a row with no application.
    manager.databases.add("reports")
    store.create_database(Database(name="reports", engine="postgresql", username="reporter"))
    _app(store, "evil.example.com")

    with pytest.raises(DatabaseError):
        _provision(store, secret_store, name="reports", user="evil_user", domain="evil.example.com")

    assert manager.calls == []
    assert store.get_database("reports", "postgresql").app_id is None


def test_an_existing_user_wasm_did_not_create_is_refused_without_suggesting_its_deletion(
    store: NoustStore, secret_store: SecretStore, manager: FakeManager
) -> None:
    manager.users["someone"] = "their-password"
    _app(store, "evil.example.com")

    with pytest.raises(DatabaseError) as excinfo:
        _provision(store, secret_store, name="evil_db", user="someone", domain="evil.example.com")

    message = f"{excinfo.value} {excinfo.value.details}"
    assert "Noust did not create" in message
    assert "user-delete" not in message
    assert manager.calls == []


def test_an_own_user_from_before_the_secret_store_still_suggests_dropping_it(
    store: NoustStore, secret_store: SecretStore, manager: FakeManager
) -> None:
    # 2.2.1 recorded the database and its user against the app, but kept no
    # password: the user is Noust's own, so dropping it is the right advice.
    app = _app(store, "legacy.example.com")
    manager.databases.add("legacy_db")
    manager.users["legacy_user"] = "unknown"
    store.create_database(
        Database(app_id=app.id, name="legacy_db", engine="postgresql", username="legacy_user")
    )

    with pytest.raises(DatabaseError) as excinfo:
        _provision(
            store, secret_store, name="legacy_db", user="legacy_user", domain="legacy.example.com"
        )

    assert "does not know its password" in str(excinfo.value)
    assert "noust db user-delete legacy_user --engine postgresql" in excinfo.value.details


@pytest.mark.parametrize("name", sorted(RESERVED_NAMES))
def test_reserved_database_names_are_refused(
    store: NoustStore, secret_store: SecretStore, manager: FakeManager, name: str
) -> None:
    with pytest.raises(DatabaseError) as excinfo:
        _provision(store, secret_store, name=name, user="app_user", domain="app.example.com")

    assert "reserved" in str(excinfo.value)
    assert manager.calls == []


@pytest.mark.parametrize("name", ["postgres", "ROOT", "Template1", "mysql"])
def test_reserved_user_names_are_refused_whatever_their_case(
    store: NoustStore, secret_store: SecretStore, manager: FakeManager, name: str
) -> None:
    manager.users[name] = "superuser-password"

    with pytest.raises(DatabaseError) as excinfo:
        _provision(store, secret_store, name="app_db", user=name, domain="app.example.com")

    assert "reserved" in str(excinfo.value)
    assert "user-delete" not in (excinfo.value.details or "")
    assert manager.calls == []


def test_reserved_names_include_every_system_database_and_account() -> None:
    assert {
        "postgres",
        "root",
        "mysql",
        "template0",
        "template1",
        "information_schema",
        "performance_schema",
        "sys",
    } <= RESERVED_NAMES


# ---------------------------------------------------------------------------
# Retries
# ---------------------------------------------------------------------------


def test_a_retry_after_a_failed_grant_reuses_the_user_and_its_password(
    store: NoustStore, secret_store: SecretStore, manager: FakeManager
) -> None:
    _app(store, "app.example.com")
    manager.fail_grant = True

    with pytest.raises(DatabaseError):
        _provision(store, secret_store, name="app_db", user="app_user", domain="app.example.com")

    # What was made is recorded as this application's, not left anonymous.
    row = store.get_database("app_db", "postgresql")
    assert row is not None
    assert row.app_id == store.get_app("app.example.com").id

    manager.fail_grant = False
    creds = _provision(
        store, secret_store, name="app_db", user="app_user", domain="app.example.com"
    )

    assert creds.password == manager.users["app_user"]
    assert manager.calls.count("create_user:app_user") == 1
    assert manager.calls.count("create_database:app_db") == 1


def test_a_retry_after_a_failed_new_deploy_whose_app_row_was_rolled_back(
    store: NoustStore, secret_store: SecretStore, manager: FakeManager
) -> None:
    # A failed new monorepo deploy deletes its app row; the database row's
    # app_id becomes NULL. The next attempt, for the same domain, is the owner.
    _app(store, "app.example.com")
    first = _provision(
        store, secret_store, name="app_db", user="app_user", domain="app.example.com"
    )
    store.delete_app("app.example.com")
    _app(store, "app.example.com")

    second = _provision(
        store, secret_store, name="app_db", user="app_user", domain="app.example.com"
    )

    assert second.password == first.password
    assert store.get_database("app_db", "postgresql").app_id == store.get_app("app.example.com").id


def test_a_stale_record_of_a_user_that_no_longer_exists_does_not_leak_its_password(
    store: NoustStore, secret_store: SecretStore, manager: FakeManager
) -> None:
    old = _provision(store, secret_store, name="old_db", user="app_user", domain="old.example.com")
    # The user was dropped by hand (`noust db user-delete`), and so was the database.
    del manager.users["app_user"]
    manager.databases.discard("old_db")
    store.delete_database("old_db", "postgresql")
    _app(store, "new.example.com")

    creds = _provision(
        store, secret_store, name="new_db", user="app_user", domain="new.example.com"
    )

    assert creds.password != old.password
    assert manager.users["app_user"] == creds.password
