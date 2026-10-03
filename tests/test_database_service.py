# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one database service behind the CLI and the API (3.1, M1, M2, M7).

What is pinned here is what went wrong when there were two implementations
and none of this existed:

- the store is the truth for what Noust manages: a create records a row and a
  drop removes it, whichever front end asked;
- a drop takes a last dump, and refuses a database an application uses;
- a restore never destroys without a safety copy, and puts it back when the
  restore fails;
- a link writes a percent-encoded connection string, marked secret, and an
  application that does not come back on it gets its previous variables;
- a rotation that leaves an application down is undone, engine password
  first;
- the engine's own accounts and the ``wasm_ro_`` accounts are never changed.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

import pytest

from noust.core.exceptions import (
    DatabaseAccessError,
    DatabaseBackupError,
    DatabaseError,
    DatabaseExistsError,
    DatabaseNotFoundError,
    DatabaseQueryError,
    DatabaseUserError,
    DeploymentError,
    ValidationError,
)
from noust.core.runner import FakeRunner
from noust.core.secrets import SecretStore
from noust.core.store import App, Database, NoustStore
from noust.deployers.helpers import app_env as app_env_module
from noust.deployers.helpers.app_env import read_app_env
from noust.managers.database import linking
from noust.managers.database.base import (
    AccessEntry,
    BackupInfo,
    BaseDatabaseManager,
    DatabaseInfo,
    UserInfo,
)
from noust.managers.database.records import DatabaseLink, DatabaseRecords
from noust.managers.database.service import (
    MAX_QUERY_LENGTH,
    DatabaseService,
    console_request,
)

DOMAIN = "shop.example.com"

#: A password with every character a URL splits on.
NASTY_PASSWORD = "p@ss:w/rd#?%&"


class FakeEngine(BaseDatabaseManager):
    """An in-memory engine: databases, users, grants and dumps, no processes."""

    ENGINE_NAME = "postgresql"
    DISPLAY_NAME = "PostgreSQL"
    DEFAULT_PORT = 5432
    SERVICE_NAME = "postgresql"
    CLIENT_BINARY = "psql"
    VALID_PRIVILEGES = frozenset({"ALL PRIVILEGES", "SELECT"})
    DEFAULT_PRIVILEGES = ("ALL PRIVILEGES",)
    CAPABILITIES = frozenset({"sql", "read_only", "users", "profiles", "dump"})
    INTERNAL_USERS = frozenset({"postgres"})

    def __init__(self, state: dict[str, Any], backup_dir: Path) -> None:
        """
        Args:
            state: The shared in-memory state, so every instance sees it.
            backup_dir: Where dumps are written.
        """
        super().__init__()
        self.state = state
        self.BACKUP_DIR = backup_dir

    def _calls(self, *entry: Any) -> None:
        self.state["calls"].append(entry)

    def is_installed(self) -> bool:
        return True

    def is_running(self) -> bool:
        return True

    def get_version(self) -> str | None:
        return "16.4"

    def server_port(self) -> int:
        return 5433

    def warnings(self) -> list[str]:
        return []

    def create_database(self, name, owner=None, encoding=None, **kwargs):
        self._calls("create_database", name, owner)
        if name in self.state["dbs"]:
            raise DatabaseExistsError(f"Database '{name}' already exists")
        self.state["dbs"][name] = {"owner": owner, "rows": "fresh"}
        return DatabaseInfo(name=name, engine=self.ENGINE_NAME, owner=owner)

    def drop_database(self, name, force=False):
        self._calls("drop_database", name)
        self.state["dbs"].pop(name, None)

    def database_exists(self, name):
        return name in self.state["dbs"]

    def list_databases(self):
        return [
            DatabaseInfo(name=name, engine=self.ENGINE_NAME, owner=entry.get("owner"), size="1 MB")
            for name, entry in self.state["dbs"].items()
        ]

    def get_database_info(self, name):
        if name not in self.state["dbs"]:
            raise DatabaseNotFoundError(f"Database '{name}' does not exist")
        entry = self.state["dbs"][name]
        return DatabaseInfo(name=name, engine=self.ENGINE_NAME, owner=entry.get("owner"))

    def create_user(self, username, password=None, host="localhost", **kwargs):
        self._calls("create_user", username)
        secret = password or self.generate_password()
        self.state["users"][username] = secret
        return UserInfo(username=username, engine=self.ENGINE_NAME, host=host), secret

    def drop_user(self, username, host="localhost"):
        self._calls("drop_user", username)
        self.state["users"].pop(username, None)

    def user_exists(self, username, host="localhost"):
        return username in self.state["users"]

    def list_users(self):
        return [UserInfo(username=name, engine=self.ENGINE_NAME) for name in self.state["users"]]

    def grant_privileges(self, username, database, privileges=None, host="localhost"):
        self._calls("grant", username, database)

    def revoke_privileges(self, username, database, privileges=None, host="localhost"):
        self._calls("revoke", username, database)

    def set_user_password(self, username, password, host="localhost"):
        self._calls("set_password", username, password)
        self.state["users"][username] = password

    def apply_profile(self, username, database, profile, host="localhost"):
        self._calls("profile", username, database, profile)
        self.state["profiles"][(username, database)] = profile

    def list_access(self, database):
        return [
            AccessEntry(username=user, profile=profile)
            for (user, db), profile in self.state["profiles"].items()
            if db == database
        ]

    def drop_read_only_account(self, database):
        self._calls("drop_ro", database)

    def backup(self, database, output_path=None, compress=True, **kwargs):
        self._calls("backup", database)
        self.state["dumps"] += 1
        path = output_path or (
            self.BACKUP_DIR / f"postgresql-{database}-20260101_12000{self.state['dumps']}.dump"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(self.state["dbs"].get(database, {}).get("rows")))
        return BackupInfo(
            path=path,
            database=database,
            engine=self.ENGINE_NAME,
            size=path.stat().st_size,
            created=datetime(2026, 1, 1, 12, 0),
        )

    def _load_backup(self, database, backup_path, **kwargs):
        self._calls("load", database, Path(backup_path).name)
        content = Path(backup_path).read_text()
        if self.state["fail_load"] and content != self.state.get("good"):
            raise DatabaseBackupError("Failed to restore", details="pg_restore: truncated dump")
        self.state["dbs"].setdefault(database, {})["rows"] = content

    def execute_query(self, database, query, **kwargs):
        return True, ""


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """
    Args:
        tmp_path: Per-test temporary directory.

    Yields:
        An isolated store, installed as the process-wide singleton.
    """
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def state() -> dict[str, Any]:
    """
    Returns:
        A fresh engine state with one database and its owner.
    """
    return {
        "dbs": {"shop_db": {"owner": "shop_user", "rows": "live"}},
        "users": {"shop_user": "old-secret", "postgres": "x"},
        "profiles": {},
        "calls": [],
        "dumps": 0,
        "fail_load": False,
    }


@pytest.fixture
def service(
    tmp_path: Path, store: NoustStore, state: dict[str, Any], runner: FakeRunner
) -> DatabaseService:
    """
    Args:
        tmp_path: Per-test temporary directory.
        store: The isolated store.
        state: The engine's state.
        runner: The fake runner, for env file hand-overs.

    Returns:
        A service over the fake engine.
    """
    backups = tmp_path / "dumps"

    def resolve(name: str) -> BaseDatabaseManager | None:
        return FakeEngine(state, backups) if name in ("postgresql", "pg") else None

    return DatabaseService(
        store=store,
        secrets=SecretStore(root=tmp_path / "secrets"),
        resolve=resolve,
        engines=lambda: ["postgresql"],
    )


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store: NoustStore) -> App:
    """
    Register an application deployed in place, with a store row of its own.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.
        store: The store.

    Returns:
        The application row.
    """
    app_path = tmp_path / "apps" / "shop-example-com"
    app_path.mkdir(parents=True)
    (app_path / ".env").write_text("APP_KEY=base64:abc\n")
    monkeypatch.setattr(
        app_env_module,
        "Config",
        lambda: SimpleNamespace(
            apps_directory=tmp_path / "apps", service_user="www-data", service_group="www-data"
        ),
    )
    return store.create_app(App(domain=DOMAIN, app_path=str(app_path)))


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """
    Stand in for the application's health gate.

    Args:
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        The gate's answers, in order, and the restarts it saw.
    """
    answers: dict[str, Any] = {"results": [], "restarts": []}

    def restart_behind_gate(app: App, store: NoustStore, log: Any) -> tuple[bool, str]:
        answers["restarts"].append(read_app_env(app))
        if answers["results"]:
            return answers["results"].pop(0)
        return True, ""

    monkeypatch.setattr(linking, "restart_behind_gate", restart_behind_gate)
    return answers


# ============================================================ store parity


def test_create_records_the_database_in_the_store(service: DatabaseService, store) -> None:
    view = service.create("postgresql", "newdb", owner="shop_user")

    assert view.tracked is True
    row = store.get_database("newdb", "postgresql")
    assert row is not None and row.username == "shop_user" and row.port == 5433


def test_create_for_an_application_puts_it_in_its_backups(service, store, app) -> None:
    service.create("postgresql", "newdb", domain=DOMAIN)

    assert [row.name for row in store.list_databases(app_id=app.id)] == ["newdb"]


def test_drop_forgets_the_row_and_the_read_only_account(service, store, state) -> None:
    service.create("postgresql", "newdb")

    outcome = service.drop("postgresql", "newdb")

    assert store.get_database("newdb", "postgresql") is None
    assert ("drop_ro", "newdb") in state["calls"]
    assert outcome.safety_copy is not None and Path(outcome.safety_copy).exists()


def test_drop_takes_its_last_dump_before_dropping(service, state) -> None:
    service.drop("postgresql", "shop_db")

    names = [call[0] for call in state["calls"]]
    assert names.index("backup") < names.index("drop_database")


def test_drop_without_a_last_dump_is_the_callers_choice(service, state) -> None:
    outcome = service.drop("postgresql", "shop_db", keep_backup=False)

    assert outcome.safety_copy is None
    assert "backup" not in [call[0] for call in state["calls"]]


def test_drop_refuses_a_database_an_application_uses(service, store, state, app) -> None:
    store.create_database(Database(app_id=app.id, name="shop_db", engine="postgresql"))

    with pytest.raises(DatabaseError) as excinfo:
        service.drop("postgresql", "shop_db")

    assert DOMAIN in str(excinfo.value)
    assert "shop_db" in state["dbs"], "nothing may be dropped"


def test_list_marks_a_tracked_database_the_engine_no_longer_has(service, store) -> None:
    store.create_database(Database(name="gone", engine="postgresql"))

    views = {view.name: view for view in service.list_databases()}

    assert views["gone"].missing is True and views["gone"].tracked is True
    assert views["shop_db"].tracked is False


def test_adopt_records_what_the_engine_holds_and_the_store_does_not(service, store) -> None:
    assert service.adopt() == ["postgresql/shop_db"]
    assert store.get_database("shop_db", "postgresql") is not None
    assert service.adopt() == []


def test_forget_refuses_a_database_that_still_exists(service, store) -> None:
    store.create_database(Database(name="shop_db", engine="postgresql"))

    with pytest.raises(DatabaseError):
        service.forget("postgresql", "shop_db")


def test_an_unknown_engine_is_refused_by_name(service) -> None:
    with pytest.raises(DatabaseError) as excinfo:
        service.manager("nosuch")

    assert "postgresql" in excinfo.value.details


# ============================================================ restore


def test_restore_replacing_takes_a_safety_copy_first(service, state, tmp_path) -> None:
    dump = tmp_path / "dumps" / "postgresql-shop_db-20250101_000000.dump"
    dump.parent.mkdir(parents=True)
    dump.write_text("restored")

    outcome = service.restore("postgresql", "shop_db", dump, drop_existing=True)

    names = [call[0] for call in state["calls"]]
    assert names.index("backup") < names.index("drop_database") < names.index("load")
    assert outcome.safety_copy is not None and outcome.safety_copy.read_text() == "live"
    assert state["dbs"]["shop_db"]["rows"] == "restored"
    assert state["dbs"]["shop_db"]["owner"] == "shop_user", "recreated with its owner"


def test_a_failed_restore_puts_the_previous_contents_back(service, state, tmp_path) -> None:
    dump = tmp_path / "broken.dump"
    dump.write_text("truncated")
    state["fail_load"] = True
    state["good"] = "live"

    with pytest.raises(DatabaseBackupError) as excinfo:
        service.restore("postgresql", "shop_db", dump, drop_existing=True)

    assert "put back" in str(excinfo.value)
    assert "pg_restore: truncated dump" in excinfo.value.details
    assert state["dbs"]["shop_db"]["rows"] == "live"


def test_restore_as_a_new_database_leaves_the_original_alone(service, state, store, tmp_path):
    dump = tmp_path / "copy.dump"
    dump.write_text("restored")

    outcome = service.restore("postgresql", "shop_db", dump, new_name="shop_copy")

    assert state["dbs"]["shop_db"]["rows"] == "live"
    assert state["dbs"]["shop_copy"]["rows"] == "restored"
    assert outcome.safety_copy is None
    assert store.get_database("shop_copy", "postgresql") is not None


def test_restore_as_new_refuses_a_name_in_use(service, tmp_path) -> None:
    dump = tmp_path / "copy.dump"
    dump.write_text("restored")

    with pytest.raises(DatabaseExistsError):
        service.restore("postgresql", "shop_db", dump, new_name="shop_db")


@pytest.mark.parametrize("name", ["../../etc/shadow", "/etc/shadow", "missing.dump"])
def test_a_dump_is_named_never_pathed(service, name: str) -> None:
    with pytest.raises((DatabaseNotFoundError, ValidationError)):
        service.dump_path("postgresql", name)


# ============================================================ users


@pytest.mark.parametrize("username", ["postgres", "wasm_ro_shop_db"])
def test_internal_accounts_are_never_changed(service, state, username: str) -> None:
    state["users"][username] = "x"
    for action in (
        lambda: service.drop_user("postgresql", username),
        lambda: service.rotate_password("postgresql", username),
        lambda: service.set_profile("postgresql", "shop_db", username, "read_only"),
        lambda: service.grant("postgresql", username, "shop_db"),
    ):
        with pytest.raises(DatabaseUserError):
            action()
    assert state["users"][username] == "x"


def test_an_account_named_like_the_read_only_console_cannot_be_created(service) -> None:
    with pytest.raises(DatabaseUserError):
        service.create_user("postgresql", "wasm_ro_shop_db")


def test_a_new_user_gets_its_profile_and_noust_keeps_its_password(service, state) -> None:
    _user, password = service.create_user(
        "postgresql", "reporter", database="shop_db", profile="read_only"
    )

    assert state["profiles"][("reporter", "shop_db")] == "read_only"
    assert service.reveal_password("postgresql", "reporter") == password
    [view] = service.access("postgresql", "shop_db")
    assert view.managed is True and view.password_changed_at is not None


def test_an_unknown_profile_is_refused_before_anything_is_created(service, state) -> None:
    with pytest.raises(DatabaseUserError):
        service.create_user("postgresql", "reporter", database="shop_db", profile="admin")

    assert "reporter" not in state["users"]


def test_a_user_an_application_signs_in_as_is_not_dropped(service, store, app, state) -> None:
    store.create_database(
        Database(app_id=app.id, name="shop_db", engine="postgresql", username="shop_user")
    )

    with pytest.raises(DatabaseUserError) as excinfo:
        service.drop_user("postgresql", "shop_user")

    assert DOMAIN in str(excinfo.value)
    assert "shop_user" in state["users"]


# ============================================================ links


def test_a_link_writes_an_encoded_url_marked_secret(service, store, app, state, gate) -> None:
    service.secrets.write("databases/postgresql/shop_user", NASTY_PASSWORD)
    store.create_database(Database(name="shop_db", engine="postgresql", username="shop_user"))

    outcome = service.link(DOMAIN, "postgresql", "shop_db")

    env = read_app_env(app)
    assert env["DATABASE_URL"] == (
        f"postgresql://shop_user:{quote(NASTY_PASSWORD, safe='')}@localhost:5433/shop_db"
    )
    assert env["APP_KEY"] == "base64:abc", "the rest of the environment is kept"
    assert store.get_app(DOMAIN).env_secret_marks.get("DATABASE_URL") is True
    assert outcome.restarted is True and len(gate["restarts"]) == 1
    assert store.get_database("shop_db", "postgresql").app_id == app.id, "now in its backups"
    [link] = DatabaseRecords(store).links(app_id=app.id)
    assert (link.env_var, link.username) == ("DATABASE_URL", "shop_user")


def test_a_link_the_application_does_not_survive_is_undone(service, store, app, gate) -> None:
    service.secrets.write("databases/postgresql/shop_user", "secret")
    store.create_database(Database(name="shop_db", engine="postgresql", username="shop_user"))
    gate["results"] = [(False, "probe: connection refused"), (True, "")]

    with pytest.raises(DeploymentError) as excinfo:
        service.link(DOMAIN, "postgresql", "shop_db")

    assert "probe: connection refused" in excinfo.value.details
    assert "DATABASE_URL" not in read_app_env(app)
    assert DatabaseRecords(store).links(app_id=app.id) == []


def test_a_link_never_overwrites_a_variable_noust_did_not_write(service, store, app, gate):
    (Path(app.app_path) / ".env").write_text("DATABASE_URL=postgres://external/db\n")
    service.secrets.write("databases/postgresql/shop_user", "secret")
    store.create_database(Database(name="shop_db", engine="postgresql", username="shop_user"))

    with pytest.raises(DatabaseError) as excinfo:
        service.link(DOMAIN, "postgresql", "shop_db")

    assert "already set" in str(excinfo.value)
    assert read_app_env(app)["DATABASE_URL"] == "postgres://external/db"


def test_a_link_without_a_known_account_creates_one_that_owns_the_database(
    service, store, app, state, gate
) -> None:
    state["dbs"]["legacy"] = {"owner": "postgres", "rows": "x"}

    outcome = service.link(DOMAIN, "postgresql", "legacy", extra_vars=True)

    assert outcome.created_user is True
    assert state["profiles"][(outcome.username, "legacy")] == "owner"
    env = read_app_env(app)
    assert env["DB_USER"] == outcome.username and env["DB_PORT"] == "5433"
    assert env["DB_PASSWORD"] == state["users"][outcome.username]


def test_unlink_removes_the_variables_and_the_ownership(service, store, app, gate) -> None:
    service.secrets.write("databases/postgresql/shop_user", "secret")
    store.create_database(Database(name="shop_db", engine="postgresql", username="shop_user"))
    service.link(DOMAIN, "postgresql", "shop_db")

    service.unlink(DOMAIN, "postgresql", "shop_db")

    assert "DATABASE_URL" not in read_app_env(app)
    assert store.get_database("shop_db", "postgresql").app_id is None
    assert DatabaseRecords(store).links(app_id=app.id) == []


def test_the_application_tab_lists_links_with_the_password_masked(service, store, app, gate):
    service.secrets.write("databases/postgresql/shop_user", "secret")
    store.create_database(Database(name="shop_db", engine="postgresql", username="shop_user"))
    service.link(DOMAIN, "postgresql", "shop_db")

    [view] = service.app_databases(DOMAIN)

    assert view.url == "postgresql://shop_user:********@localhost:5433/shop_db"
    assert "secret" not in str(view.to_dict())
    assert service.reveal_url(DOMAIN, "postgresql", "shop_db").endswith(
        ":secret@localhost:5433/shop_db"
    )


def test_drop_with_unlink_removes_the_variables_first(service, store, app, state, gate) -> None:
    service.secrets.write("databases/postgresql/shop_user", "secret")
    store.create_database(Database(name="shop_db", engine="postgresql", username="shop_user"))
    service.link(DOMAIN, "postgresql", "shop_db")

    outcome = service.drop("postgresql", "shop_db", unlink=True)

    assert outcome.unlinked == [DOMAIN]
    assert "DATABASE_URL" not in read_app_env(app)
    assert DatabaseRecords(store).links(app_id=app.id) == []


# ============================================================ rotation


def test_a_rotation_reaches_every_application_that_signs_in(service, store, app, state, gate):
    service.secrets.write("databases/postgresql/shop_user", "old-secret")
    store.create_database(Database(name="shop_db", engine="postgresql", username="shop_user"))
    service.link(DOMAIN, "postgresql", "shop_db")

    outcome = service.rotate_password("postgresql", "shop_user")

    assert state["users"]["shop_user"] == outcome.password != "old-secret"
    assert read_app_env(app)["DATABASE_URL"].endswith(f":{outcome.password}@localhost:5433/shop_db")
    assert outcome.apps == [DOMAIN] and outcome.restarted == [DOMAIN]
    assert "password" not in outcome.to_dict()


def test_a_rotation_rewrites_a_recipe_url_that_carries_the_old_password(
    service, store, app, state, gate
) -> None:
    """Provisioned before 3.1: no link row, but the application's own variable."""
    service.secrets.write("databases/postgresql/shop_user", "old-secret")
    store.create_database(
        Database(app_id=app.id, name="shop_db", engine="postgresql", username="shop_user")
    )
    (Path(app.app_path) / ".env").write_text(
        "DATABASE_URL=postgresql://shop_user:old-secret@localhost:5432/shop_db\n"
    )

    outcome = service.rotate_password("postgresql", "shop_user")

    assert read_app_env(app)["DATABASE_URL"] == (
        f"postgresql://shop_user:{outcome.password}@localhost:5432/shop_db"
    )


def test_a_rotation_an_application_does_not_survive_is_undone(service, store, app, state, gate):
    service.secrets.write("databases/postgresql/shop_user", "old-secret")
    store.create_database(Database(name="shop_db", engine="postgresql", username="shop_user"))
    service.link(DOMAIN, "postgresql", "shop_db")
    before = read_app_env(app)
    gate["results"] = [(False, "journal: FATAL password authentication failed")]
    state["calls"].clear()

    with pytest.raises(DatabaseError) as excinfo:
        service.rotate_password("postgresql", "shop_user")

    assert "journal: FATAL" in excinfo.value.details
    assert state["users"]["shop_user"] == "old-secret"
    assert read_app_env(app) == before
    passwords = [call for call in state["calls"] if call[0] == "set_password"]
    assert passwords[-1] == ("set_password", "shop_user", "old-secret"), (
        "the engine gets the old password back"
    )
    assert service.reveal_password("postgresql", "shop_user") == "old-secret"


# ============================================================ console guard


def test_the_console_guard_holds_one_statement() -> None:
    assert console_request("SELECT 1;", single=True) == "SELECT 1"
    with pytest.raises(DatabaseQueryError):
        console_request("SELECT 1; DROP TABLE t", single=True)
    assert console_request("SELECT 1; SELECT 2", single=False) == "SELECT 1; SELECT 2"


def test_the_console_guard_bounds_the_length() -> None:
    with pytest.raises(DatabaseQueryError) as excinfo:
        console_request("SELECT " + "a" * MAX_QUERY_LENGTH, single=False)

    assert "noust db connect" in excinfo.value.details


def test_no_keyword_list_stands_between_a_read_and_the_server() -> None:
    """The server enforces read-only; a leading comment or a CTE is not refused here."""
    for statement in (
        "-- report\nSELECT 1",
        "(SELECT 1) UNION (SELECT 2)",
        "WITH x AS (SELECT 1) SELECT * FROM x",
    ):
        assert console_request(statement, single=True)


# ============================================================ wizard


def test_the_wizard_plan_names_what_will_be_written(service) -> None:
    plan = service.plan_for_app("new-app.example.com", "postgresql")

    assert plan["database"] == "new_app_example_com_db"
    assert plan["username"] == "new_app_example_com_user"
    assert plan["env_vars"] == ["DATABASE_URL"]
    assert plan["url"] == (
        "postgresql://new_app_example_com_user:********@localhost:5433/new_app_example_com_db"
    )


def test_records_round_trip(store, app) -> None:
    records = DatabaseRecords(store)
    records.save_link(
        DatabaseLink(app_id=app.id or 0, engine="redis", db_name="0", env_var="REDIS_URL")
    )
    records.save_link(
        DatabaseLink(app_id=app.id or 0, engine="redis", db_name="0", env_var="CACHE_URL")
    )

    [link] = records.links(app_id=app.id)
    assert link.env_var == "CACHE_URL", "one link per application and database"
    first = records.save_account("mysql", "app", profile="read_only", password_changed=True)
    second = records.save_account("mysql", "app", profile="owner")
    assert second.password_changed_at == first.password_changed_at
    assert second.profile == "owner"


# ============================================================ a new application's database


NEW_DOMAIN = "new-app.example.com"


@pytest.fixture
def audited(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    """
    Capture the audit trail instead of writing it.

    Args:
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        ``(event, target, outcome)`` for every event recorded.
    """
    events: list[tuple[str, str, str]] = []

    def record(event: str, *, target: str, outcome: str = "ok", **_: Any) -> None:
        events.append((event, target, outcome))

    monkeypatch.setattr("noust.managers.database.service.audit.record", record)
    return events


def test_a_new_application_gets_its_database_before_it_exists(
    service, store, state, audited
) -> None:
    prepared = service.prepare_for_new_app(NEW_DOMAIN, "postgresql")

    assert state["dbs"]["new_app_example_com_db"]["owner"] == "new_app_example_com_user"
    password = state["users"]["new_app_example_com_user"]
    assert prepared.values == {
        "DATABASE_URL": (
            f"postgresql://new_app_example_com_user:{password}@localhost:5433/"
            "new_app_example_com_db"
        )
    }
    assert prepared.secret_marks == {"DATABASE_URL": True}
    assert prepared.created_database is True
    assert store.get_app(NEW_DOMAIN) is None, "the deploy creates the application, not this"
    assert store.get_database("new_app_example_com_db", "postgresql").app_id is None
    assert service.secrets.read("databases/postgresql/new_app_example_com_user.owner") == (
        NEW_DOMAIN
    )
    assert ("db.create", "db:postgresql/new_app_example_com_db", "ok") in audited
    assert password not in repr(prepared), "the connection string never reaches a log line"


def test_a_retried_first_deploy_reuses_the_database_and_its_password(service, state) -> None:
    first = service.prepare_for_new_app(NEW_DOMAIN, "postgresql", extra_vars=True)
    again = service.prepare_for_new_app(NEW_DOMAIN, "postgresql", extra_vars=True)

    assert again.values == first.values
    assert again.created_database is False
    assert set(again.values) == {
        "DATABASE_URL",
        "DB_HOST",
        "DB_PORT",
        "DB_NAME",
        "DB_USER",
        "DB_PASSWORD",
    }
    assert [call[0] for call in state["calls"]].count("create_user") == 1


def test_a_new_application_keeps_a_variable_it_was_given(service, state) -> None:
    with pytest.raises(DatabaseError) as excinfo:
        service.prepare_for_new_app(
            NEW_DOMAIN, "postgresql", env={"DATABASE_URL": "postgres://elsewhere/db"}
        )

    assert "DATABASE_URL" in str(excinfo.value)
    assert "new_app_example_com_db" not in state["dbs"], "nothing is created when refused"


def test_a_deployed_application_gets_its_database_through_provision(service, app) -> None:
    with pytest.raises(DatabaseError) as excinfo:
        service.prepare_for_new_app(DOMAIN, "postgresql")

    assert f"noust db provision {DOMAIN}" in excinfo.value.details


def test_the_link_is_recorded_once_the_first_deploy_created_the_application(
    service, store, audited, request: pytest.FixtureRequest
) -> None:
    prepared = service.prepare_for_new_app(DOMAIN, "postgresql", name="shop_new")
    app = request.getfixturevalue("app")  # what the first deploy registers
    (Path(app.app_path) / ".env").write_text(f"DATABASE_URL={prepared.values['DATABASE_URL']}\n")

    outcome = service.link_new_app(prepared)

    [link] = DatabaseRecords(store).links(app_id=app.id)
    assert (link.db_name, link.env_var, link.username) == (
        "shop_new",
        "DATABASE_URL",
        "shop_example_com_user",
    )
    assert store.get_database("shop_new", "postgresql").app_id == app.id, "in its backups"
    assert outcome.created_database is True and outcome.restarted is False
    assert ("db.link", "db:postgresql/shop_new", "ok") in audited


def test_a_first_deploy_that_fails_keeps_the_database_and_says_how_to_drop_it(
    service, state, audited
) -> None:
    prepared = service.prepare_for_new_app(NEW_DOMAIN, "postgresql")
    error = DeploymentError("Deployment failed for new-app.example.com", details="npm ci failed")

    service.keep_after_failed_deploy(prepared, error)

    assert "new_app_example_com_db" in state["dbs"], "data is never dropped silently"
    assert error.details.startswith("npm ci failed")
    assert "was kept" in error.details
    assert "noust db drop new_app_example_com_db --engine postgresql" in error.details
    assert "noust db user-delete new_app_example_com_user --engine postgresql" in error.details
    assert ("db.link", "db:postgresql/new_app_example_com_db", "failure") in audited


# ============================================================ Redis without a password


class FakeRedis(FakeEngine):
    """An instance whose ``requirepass`` the state holds; empty is no password."""

    ENGINE_NAME = "redis"
    DISPLAY_NAME = "Redis"
    INTERNAL_USERS = frozenset()

    def client_password(self) -> str | None:
        return self.state["requirepass"] or None

    def requirepass_set(self) -> bool | None:
        return bool(self.state["requirepass"])

    def set_user_password(self, username, password, host="localhost"):
        self._calls("set_password", username, password)
        self.state["requirepass"] = password


@pytest.fixture
def redis_service(tmp_path: Path, store: NoustStore, state: dict[str, Any]) -> DatabaseService:
    state["requirepass"] = ""

    def resolve(name: str) -> BaseDatabaseManager | None:
        return FakeRedis(state, tmp_path / "dumps") if name == "redis" else None

    return DatabaseService(
        store=store,
        secrets=SecretStore(root=tmp_path / "secrets"),
        resolve=resolve,
        engines=lambda: ["redis"],
    )


def test_a_first_redis_password_needs_to_be_asked_for(redis_service, state) -> None:
    """Every application that connects without one would start getting NOAUTH."""
    with pytest.raises(DatabaseUserError) as raised:
        redis_service.rotate_password("redis", "default")

    assert "NOAUTH" in (raised.value.details or "")
    assert state["requirepass"] == ""
    assert not [call for call in state["calls"] if call[0] == "set_password"]


def test_a_first_redis_password_that_fails_is_undone_to_no_password(
    redis_service, store, app, state, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No password is a state to go back to, not an unknown one."""
    from noust.core.exceptions import DeploymentError
    from noust.managers.database import service as service_module

    monkeypatch.setattr(redis_service, "_apps_using", lambda engine, username, old: [app])

    def refuses(*args: Any, **kwargs: Any) -> Any:
        raise DeploymentError("did not come back", details="NOAUTH Authentication required")

    monkeypatch.setattr(service_module, "change_app_env", refuses)

    with pytest.raises(DatabaseError):
        redis_service.rotate_password("redis", "default", first_password=True)

    assert state["requirepass"] == ""
    assert [c for c in state["calls"] if c[0] == "set_password"][-1] == (
        "set_password",
        "default",
        "",
    )


class TestListingProblems:
    """An engine that cannot be read says so, and does not lose its tracked rows."""

    def test_an_engine_refusing_noust_is_a_problem_not_an_empty_engine(
        self, service: DatabaseService, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store.create_database(Database(name="shop_db", engine="postgresql"))
        denied = 'FATAL:  password authentication failed for user "postgres"'

        def refuse(self):
            raise DatabaseAccessError(
                "PostgreSQL does not let Noust sign in", details="store it", output=denied
            )

        monkeypatch.setattr(FakeEngine, "list_databases", refuse)

        listing = service.listing()

        assert [p.engine for p in listing.problems] == ["postgresql"]
        assert listing.problems[0].access is True
        assert listing.problems[0].output == denied
        (row,) = listing.databases
        assert row.name == "shop_db"
        assert row.unverified is True
        assert row.missing is False, "an engine that was not read cannot prove a database gone"

    def test_a_readable_engine_still_marks_a_dropped_database_missing(
        self, service: DatabaseService, store: NoustStore
    ) -> None:
        store.create_database(Database(name="gone_db", engine="postgresql"))

        listing = service.listing()

        assert listing.problems == []
        gone = next(view for view in listing.databases if view.name == "gone_db")
        assert gone.missing is True
        assert gone.unverified is False


class FakeMySQL(FakeEngine):
    """An engine that signs in with a stored account, and checks it."""

    ENGINE_NAME = "mysql"
    DISPLAY_NAME = "MySQL/MariaDB"

    def list_databases(self):
        account = self.config.get("databases", {}).get("credentials", {}).get("mysql", {})
        if account.get("password") != "right":
            self._listing_failed("databases", "ERROR 1045 (28000): Access denied for user 'root'")
        return []


class TestSetCredentials:
    """An account is tried through the code that will use it before it is saved."""

    @pytest.fixture
    def mysql_service(
        self,
        tmp_path: Path,
        store: NoustStore,
        state: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> tuple[DatabaseService, dict[str, Any]]:
        saved: dict[str, Any] = {}

        class FakeConfig:
            def set(self, key: str, value: Any) -> None:
                saved[key] = value

            def save(self) -> bool:
                return True

        from noust.managers.database import service as service_module

        monkeypatch.setattr(service_module, "Config", FakeConfig)

        def resolve(name: str) -> BaseDatabaseManager | None:
            if name == "mysql":
                engine = FakeMySQL(state, tmp_path / "dumps")
                engine.config = FakeConfig()
                engine.config.get = lambda key, default=None: default  # type: ignore[attr-defined]
                return engine
            return FakeEngine(state, tmp_path / "dumps") if name == "postgresql" else None

        service = DatabaseService(
            store=store,
            secrets=SecretStore(root=tmp_path / "secrets"),
            resolve=resolve,
            engines=lambda: ["mysql", "postgresql"],
        )
        return service, saved

    def test_a_refused_account_is_not_saved(self, mysql_service) -> None:
        service, saved = mysql_service

        with pytest.raises(DatabaseAccessError) as caught:
            service.set_credentials("mysql", "root", "wrong")

        assert "1045" in (caught.value.output or "")
        assert saved == {}

    def test_an_accepted_account_is_saved(self, mysql_service) -> None:
        service, saved = mysql_service

        service.set_credentials("mysql", "root", "right")

        assert saved == {
            "databases.credentials.mysql.user": "root",
            "databases.credentials.mysql.password": "right",
        }

    def test_postgresql_has_no_stored_account(self, mysql_service) -> None:
        service, _ = mysql_service

        with pytest.raises(ValidationError, match="does not sign in with a stored account"):
            service.set_credentials("postgresql", "postgres", "x")
