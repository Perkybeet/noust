# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the shared database provisioning helper.

``provision_database`` is what the monorepo deployer (and every future
deployer that needs a shared database) calls instead of hand-rolling
"create it, or find out it is already there" with a bare ``except
Exception``. These tests pin down the properties that used to break in
production: a password is written to the secret store before the user is
created, a retry after a partial failure reuses what the first attempt made
instead of failing on "already exists", and one application can never reach
into another's database.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.exceptions import DatabaseError
from noust.core.logger import Logger
from noust.core.secrets import SecretStore
from noust.core.store import App, Database, NoustStore
from noust.deployers.helpers import databases as db_helpers
from noust.deployers.helpers.databases import (
    SUPPORTED_ENGINES,
    DatabaseCredentials,
    database_identifiers,
    generate_database_password,
    provision_database,
)


class FakeManager:
    """
    A minimal stand-in for a ``BaseDatabaseManager`` subclass.

    Records every call so a test can assert on ordering (the password must
    reach the secret store before ``create_user`` runs) without pulling in a
    real engine manager and its subprocess calls.
    """

    def __init__(
        self,
        engine: str,
        display_name: str,
        *,
        installed: bool = True,
        max_database_name_length: int = 63,
        max_user_name_length: int = 63,
        port: int = 5432,
        calls: list[str] | None = None,
    ) -> None:
        self.ENGINE_NAME = engine
        self.DISPLAY_NAME = display_name
        self.MAX_DATABASE_NAME_LENGTH = max_database_name_length
        self.MAX_USER_NAME_LENGTH = max_user_name_length
        self._installed = installed
        self._port = port
        self._calls = calls if calls is not None else []
        self.databases: set[str] = set()
        self.users: dict[str, str] = {}
        self.create_database_calls: list[str] = []
        self.create_user_calls: list[tuple[str, str | None, bool]] = []
        self.grant_calls: list[tuple[str, str]] = []

    def is_installed(self) -> bool:
        return self._installed

    def validate_database_name(self, name: str) -> str:
        return name

    def validate_user_name(self, username: str) -> str:
        return username

    def database_exists(self, name: str) -> bool:
        return name in self.databases

    def create_database(self, name: str, **_kwargs: object) -> None:
        self._calls.append(f"create_database:{name}")
        self.create_database_calls.append(name)
        self.databases.add(name)

    def user_exists(self, username: str, host: str = "localhost") -> bool:
        return username in self.users

    def create_user(self, username: str, password: str | None = None, **kwargs: object) -> None:
        self._calls.append(f"create_user:{username}")
        self.create_user_calls.append((username, password, bool(kwargs.get("createdb"))))
        self.users[username] = password or ""

    def grant_privileges(self, *, username: str, database: str) -> None:
        self.grant_calls.append((username, database))

    def server_port(self) -> int:
        return self._port


class RecordingSecretStore(SecretStore):
    """A ``SecretStore`` that logs each write into a shared call list."""

    def __init__(self, root: Path, calls: list[str]) -> None:
        super().__init__(root=root)
        self._calls = calls

    def write(self, name: str, value: str) -> None:
        self._calls.append(f"secret_write:{name}")
        super().write(name, value)


class FakeRegistry:
    """Resolves engine names the way ``DatabaseRegistry`` does, without the real managers."""

    def __init__(self, managers: dict[str, FakeManager], aliases: dict[str, str] | None = None):
        self._managers = managers
        self._aliases = aliases or {}

    def get(self, engine: str, verbose: bool = False) -> FakeManager | None:
        key = self._aliases.get(engine.lower(), engine.lower())
        return self._managers.get(key)


@pytest.fixture
def store(tmp_path: Path):
    """Provide an isolated store, installed as the process-wide singleton."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def secrets_root(tmp_path: Path) -> Path:
    return tmp_path / "secrets"


@pytest.fixture
def logger() -> Logger:
    return Logger()


def _patch_registry(monkeypatch: pytest.MonkeyPatch, registry: FakeRegistry) -> None:
    monkeypatch.setattr(db_helpers, "DatabaseRegistry", registry)


# ---------------------------------------------------------------------------
# provision_database
# ---------------------------------------------------------------------------


def test_provision_creates_database_and_user_and_writes_secret_before_create_user(
    store: NoustStore,
    secrets_root: Path,
    logger: Logger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    manager = FakeManager("postgresql", "PostgreSQL", calls=calls)
    _patch_registry(monkeypatch, FakeRegistry({"postgresql": manager}))
    secret_store = RecordingSecretStore(secrets_root, calls)

    store.create_app(App(domain="example.com", app_path="/srv/example"))

    creds = provision_database(
        "postgresql",
        name="app_db",
        user="app_user",
        domain="example.com",
        createdb=True,
        logger=logger,
        store=store,
        secret_store=secret_store,
    )

    assert isinstance(creds, DatabaseCredentials)
    assert creds.engine == "postgresql"
    assert creds.name == "app_db"
    assert creds.user == "app_user"
    assert creds.password
    assert creds.host == "localhost"
    assert creds.port == 5432

    # The secret reaches disk before create_user runs: a crash in between must
    # never leave a user whose password Noust cannot recall.
    write_index = calls.index("secret_write:databases/postgresql/app_user")
    create_user_index = calls.index("create_user:app_user")
    assert write_index < create_user_index

    assert manager.create_user_calls == [("app_user", creds.password, True)]
    assert manager.grant_calls == [("app_user", "app_db")]

    row = store.get_database("app_db", "postgresql")
    assert row is not None
    app = store.get_app("example.com")
    assert row.app_id == app.id
    assert row.username == "app_user"
    assert row.port == 5432

    assert secret_store.read("databases/postgresql/app_user") == creds.password


def test_provision_is_idempotent_on_retry(
    store: NoustStore, secrets_root: Path, logger: Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = FakeManager("postgresql", "PostgreSQL")
    _patch_registry(monkeypatch, FakeRegistry({"postgresql": manager}))
    secret_store = SecretStore(root=secrets_root)
    store.create_app(App(domain="example.com", app_path="/srv/example"))

    first = provision_database(
        "postgresql",
        name="app_db",
        user="app_user",
        domain="example.com",
        logger=logger,
        store=store,
        secret_store=secret_store,
    )
    second = provision_database(
        "postgresql",
        name="app_db",
        user="app_user",
        domain="example.com",
        logger=logger,
        store=store,
        secret_store=secret_store,
    )

    assert second.password == first.password
    assert manager.create_database_calls == ["app_db"]
    assert len(manager.create_user_calls) == 1
    assert len(store.list_databases(engine="postgresql")) == 1


def test_provision_refuses_an_existing_user_it_did_not_create(
    store: NoustStore, secrets_root: Path, logger: Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = FakeManager("postgresql", "PostgreSQL")
    manager.users["app_user"] = "some-password-wasm-never-recorded"
    _patch_registry(monkeypatch, FakeRegistry({"postgresql": manager}))
    secret_store = SecretStore(root=secrets_root)

    with pytest.raises(DatabaseError) as excinfo:
        provision_database(
            "postgresql",
            name="app_db",
            user="app_user",
            logger=logger,
            store=store,
            secret_store=secret_store,
        )

    # Noust did not create it, so dropping it is not Noust's advice to give
    # (see tests/test_database_ownership.py for the user Noust did create).
    assert "Noust did not create" in str(excinfo.value)
    assert "user-delete" not in excinfo.value.details


def test_provision_refuses_database_owned_by_another_app(
    store: NoustStore, secrets_root: Path, logger: Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = FakeManager("postgresql", "PostgreSQL")
    _patch_registry(monkeypatch, FakeRegistry({"postgresql": manager}))
    secret_store = SecretStore(root=secrets_root)

    other_app = store.create_app(App(domain="other.example.com", app_path="/srv/other"))
    store.create_database(
        Database(app_id=other_app.id, name="app_db", engine="postgresql", username="app_user")
    )
    store.create_app(App(domain="mine.example.com", app_path="/srv/mine"))

    with pytest.raises(DatabaseError) as excinfo:
        provision_database(
            "postgresql",
            name="app_db",
            user="app_user",
            domain="mine.example.com",
            logger=logger,
            store=store,
            secret_store=secret_store,
        )

    assert "other.example.com" in str(excinfo.value)
    # Never touch it: the manager must not have been asked to create or grant.
    assert manager.create_database_calls == []
    assert manager.grant_calls == []


def test_provision_refuses_engine_that_is_not_installed(
    store: NoustStore, secrets_root: Path, logger: Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = FakeManager("postgresql", "PostgreSQL", installed=False)
    _patch_registry(monkeypatch, FakeRegistry({"postgresql": manager}))
    secret_store = SecretStore(root=secrets_root)

    with pytest.raises(DatabaseError) as excinfo:
        provision_database(
            "postgresql",
            name="app_db",
            user="app_user",
            logger=logger,
            store=store,
            secret_store=secret_store,
        )

    assert "PostgreSQL is not installed" in str(excinfo.value)
    assert "noust db install postgresql" in excinfo.value.details


def test_provision_refuses_unknown_engine(
    store: NoustStore, secrets_root: Path, logger: Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_registry(monkeypatch, FakeRegistry({}))
    secret_store = SecretStore(root=secrets_root)

    with pytest.raises(DatabaseError):
        provision_database(
            "sqlite",
            name="app_db",
            user="app_user",
            logger=logger,
            store=store,
            secret_store=secret_store,
        )


def test_provision_refuses_engine_it_does_not_support(
    store: NoustStore, secrets_root: Path, logger: Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = FakeManager("redis", "Redis")
    _patch_registry(monkeypatch, FakeRegistry({"redis": manager}))
    secret_store = SecretStore(root=secrets_root)

    with pytest.raises(DatabaseError):
        provision_database(
            "redis",
            name="app_db",
            user="app_user",
            logger=logger,
            store=store,
            secret_store=secret_store,
        )


def test_provision_resolves_mariadb_alias_to_mysql(
    store: NoustStore, secrets_root: Path, logger: Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = FakeManager("mysql", "MySQL/MariaDB", max_user_name_length=32, port=3306)
    _patch_registry(monkeypatch, FakeRegistry({"mysql": manager}, aliases={"mariadb": "mysql"}))
    secret_store = SecretStore(root=secrets_root)

    creds = provision_database(
        "mariadb",
        name="app_db",
        user="app_user",
        logger=logger,
        store=store,
        secret_store=secret_store,
    )

    assert creds.engine == "mysql"
    assert creds.port == 3306
    assert secret_store.read("databases/mysql/app_user") == creds.password
    assert secret_store.read("databases/mariadb/app_user") is None


# ---------------------------------------------------------------------------
# database_identifiers
# ---------------------------------------------------------------------------


def test_database_identifiers_turns_dashes_into_underscores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = FakeManager("postgresql", "PostgreSQL")
    _patch_registry(monkeypatch, FakeRegistry({"postgresql": manager}))

    database, user = database_identifiers("my-app", "postgresql")

    assert database == "my_app_db"
    assert user == "my_app_user"


def test_database_identifiers_truncates_and_hashes_for_mysql_user_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = FakeManager("mysql", "MySQL/MariaDB", max_user_name_length=32)
    _patch_registry(monkeypatch, FakeRegistry({"mysql": manager}))

    app_name = "a-very-long-application-name-that-exceeds-mysql-user-limits"
    _database, user = database_identifiers(app_name, "mysql")

    assert len(user) <= 32
    assert user.endswith("_user")


def test_database_identifiers_are_distinct_for_two_long_names_sharing_a_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = FakeManager("mysql", "MySQL/MariaDB", max_user_name_length=32)
    _patch_registry(monkeypatch, FakeRegistry({"mysql": manager}))

    prefix = "a" * 40
    _db1, user1 = database_identifiers(prefix + "-one", "mysql")
    _db2, user2 = database_identifiers(prefix + "-two", "mysql")

    assert user1 != user2


# ---------------------------------------------------------------------------
# DatabaseCredentials
# ---------------------------------------------------------------------------


def test_url_percent_encodes_user_and_password() -> None:
    creds = DatabaseCredentials(
        engine="postgresql",
        name="app_db",
        user="user@name",
        password="p@ss/word#1",
        host="localhost",
        port=5432,
    )

    assert creds.url == "postgresql://user%40name:p%40ss%2Fword%231@localhost:5432/app_db"


def test_context_includes_url_and_stringified_port() -> None:
    creds = DatabaseCredentials(
        engine="mysql", name="db", user="u", password="p", host="localhost", port=3306
    )

    context = creds.context()

    assert context["port"] == "3306"
    assert context["url"] == creds.url
    assert set(context) == {"engine", "name", "user", "password", "host", "port", "url"}


# ---------------------------------------------------------------------------
# generate_database_password
# ---------------------------------------------------------------------------


def test_generate_database_password_alphabet_and_composition() -> None:
    for _ in range(20):
        password = generate_database_password()
        assert len(password) == 32
        assert password.isalnum()
        assert any(c.islower() for c in password)
        assert any(c.isupper() for c in password)
        assert any(c.isdigit() for c in password)


def test_generate_database_password_respects_length() -> None:
    password = generate_database_password(length=10)
    assert len(password) == 10


def test_supported_engines_are_mysql_and_postgresql() -> None:
    assert SUPPORTED_ENGINES == ("mysql", "postgresql")


# ---------------------------------------------------------------------------
# MonorepoDeployer._provision_postgresql: proves it now uses the helper
# ---------------------------------------------------------------------------


def test_monorepo_provision_postgresql_delegates_to_the_helper(
    tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.core.runner import FakeRunner
    from noust.deployers import monorepo as monorepo_module
    from noust.deployers.monorepo import DatabaseConfig, MonorepoDeployer

    captured: dict[str, object] = {}

    def fake_provision_database(engine: str, **kwargs: object) -> DatabaseCredentials:
        captured["engine"] = engine
        captured["kwargs"] = kwargs
        return DatabaseCredentials(
            engine="postgresql",
            name=str(kwargs["name"]),
            user=str(kwargs["user"]),
            password="the-real-password",
            host="localhost",
            port=5433,
        )

    monkeypatch.setattr(monorepo_module, "provision_database", fake_provision_database)

    deployer = MonorepoDeployer(runner=FakeRunner())
    deployer.configure("example.com", "src", app_path=tmp_path / "app", ssl=False)

    db_config = DatabaseConfig(
        engine="postgresql",
        name="example_db",
        user="example_user",
        password="detection-time-placeholder",
        host="localhost",
        port=0,
    )

    deployer._provision_postgresql(db_config)

    assert captured["engine"] == "postgresql"
    assert captured["kwargs"] == {
        "name": "example_db",
        "user": "example_user",
        "domain": "example.com",
        "createdb": True,
        "logger": deployer.logger,
    }
    # The placeholder generated at detection time is replaced with the real
    # credentials the helper provisioned - what fixes the bug where a
    # freshly generated password no longer matched a user that already
    # existed.
    assert db_config.password == "the-real-password"
    assert db_config.port == 5433


def test_monorepo_provision_databases_treats_a_provisioning_failure_as_a_warning(
    tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.core.runner import FakeRunner
    from noust.deployers import monorepo as monorepo_module
    from noust.deployers.monorepo import DatabaseConfig, MonorepoDeployer

    def failing_provision_database(engine: str, **kwargs: object) -> DatabaseCredentials:
        raise DatabaseError("PostgreSQL is not installed", details="Install it first.")

    monkeypatch.setattr(monorepo_module, "provision_database", failing_provision_database)

    deployer = MonorepoDeployer(runner=FakeRunner())
    deployer.configure("example.com", "src", app_path=tmp_path / "app", ssl=False)
    monkeypatch.setattr(
        deployer,
        "_detect_database_requirements",
        lambda: {
            "postgresql": DatabaseConfig(
                engine="postgresql", name="example_db", user="example_user", password="x"
            )
        },
    )

    # Must not raise: a failed provisioning attempt is a warning, not a
    # reason to fail the whole deployment.
    deployer._provision_databases()
