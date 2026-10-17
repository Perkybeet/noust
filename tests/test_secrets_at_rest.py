# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where a secret may go once Noust holds it (checklist L-22).

Noust keeps a secret in a file only root can read, next to the data it belongs to.
Encrypting it under a key kept on the same host would protect it from nothing a host
compromise does not already have, so the guarantee that is worth testing is the
other one: a secret does not travel. It is not in a read of the API, not in a dump
of the store, not in what an application exports, not in the evidence of an incident.

The sweep is the part that keeps working as routes are added: it plants a secret in
every place the product keeps one, then reads every route of the API that reads
without being asked for a name and fails if any answer holds one. A route that is
meant to show a secret on request (a webhook secret, a database password) needs a
name or a parameter to ask for it, so it is out of the sweep by construction, and
each of them has its own audited event instead.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.config import Config
from noust.core.store import NoustStore
from tests.teams_support import World, build_world

SHOP = "shop.acme.example"

#: One recognisable value for each place a secret lives. None of them looks like anything but itself,
#: so a hit in a response is this secret and not a coincidence.
SECRETS = {
    "smtp": "zq-smtp-password-7c1e90aa",
    "telegram": "zq-telegram-bot-token-51f2b7",
    "webhook-channel": "zq-notify-webhook-signing-04d9c3",
    "ntfy": "zq-ntfy-token-a9b17e22",
    "database": "zq-database-admin-password-3e8d51",
    "app-env": "zq-app-env-api-key-88aa12c4",
    "app-env-url": "zq-url-password-9b02e7",
    "service-env": "zq-service-env-secret-6d41f0",
    "webhook": "zq-deploy-webhook-secret-2b7c4d",
    "shared": "zq-shared-license-value-73e0a9",
}


@pytest.fixture
def world(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sandbox: Path, runner: Any
) -> Iterator[World]:
    """The console over two teams, with a secret planted in every place one is kept."""
    built = build_world(tmp_path, monkeypatch)
    # The configuration is a file the API reads from disk: plant in the test's own.
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    Config.reset_instance()
    config = Config()
    config.set("monitor.smtp.password", SECRETS["smtp"])
    config.set("notifications.channels.telegram.bot_token", SECRETS["telegram"])
    config.set("notifications.channels.webhook.secret", SECRETS["webhook-channel"])
    config.set("notifications.channels.ntfy.token", SECRETS["ntfy"])
    config.set("databases.credentials.postgresql.password", SECRETS["database"])
    assert config.save()
    app = built.store.get_app(SHOP)
    assert app is not None
    app.env_vars = {
        "STRIPE_API_KEY": SECRETS["app-env"],
        "DATABASE_URL": f"postgres://shop:{SECRETS['app-env-url']}@db.internal/shop",
        "NODE_ENV": "production",
    }
    built.store.update_app(app)
    with built.store._transaction() as cursor:
        cursor.execute(
            "UPDATE apps SET webhook_secret = ? WHERE domain = ?", (SECRETS["webhook"], SHOP)
        )
        cursor.execute(
            "INSERT INTO shared_variables (scope, owner, name, value, secret, created_at, updated_at)"
            " VALUES ('server', 0, 'LICENSE_KEY', ?, 1, '2026-10-16T10:00:00', '2026-10-16T10:00:00')",
            (SECRETS["shared"],),
        )
    env_file = Path(app.app_path) / ".env"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text(
        f"STRIPE_API_KEY={SECRETS['app-env']}\nDATABASE_URL=postgres://shop:{SECRETS['app-env-url']}@db.internal/shop\n"
    )
    assert (tmp_path / "etc" / "config.yaml").exists(), "the secrets were not written to the file"
    try:
        yield built
    finally:
        Config.reset_instance()
        NoustStore.reset_instance()


def leaks(text: str) -> list[str]:
    """
    Args:
        text: What was shown, exported or answered.

    Returns:
        The planted secrets it holds, by where they were planted.
    """
    return sorted(name for name, value in SECRETS.items() if value in text)


def readable_routes(world: World) -> list[str]:
    """
    Returns:
        Every GET route of the API that needs no name and no parameter, with the
        application's domain filled in where the path takes it.
    """
    schema = world.client.get("/api/openapi.json", headers=world.master).json()
    found: list[str] = []
    for path, item in schema["paths"].items():
        operation = item.get("get")
        if operation is None:
            continue
        required = [
            p for p in operation.get("parameters", []) if p.get("required") and p["in"] == "query"
        ]
        if required:
            continue
        filled = path.replace("{domain}", SHOP)
        if re.search(r"\{[^}]+\}", filled):
            continue
        found.append(filled)
    return sorted(found)


#: Reads that are a stream, or that wait for something to happen, which a sweep must not hang on.
STREAMS = ("/events", "/ws/", "/stream", "/follow")

#: Reads of the machine, not of Noust's data: the host's own processes with their command lines
#: (an audited sensitive read, `processes.cmdline`). Whatever any other process on the machine was
#: started with would be a "leak" of this test.
THE_HOSTS_OWN = ("/api/monitor/processes",)


class TestNoReadOfTheApiCarriesASecret:
    def test_every_route_that_reads_without_being_asked_for_a_name(self, world: World) -> None:
        routes = [
            path
            for path in readable_routes(world)
            if not any(s in path for s in STREAMS) and path not in THE_HOSTS_OWN
        ]
        # The sweep must be wide enough to mean something: the API has hundreds of such reads.
        assert len(routes) > 100, len(routes)
        found: dict[str, list[str]] = {}
        for path in routes:
            response = world.client.get(path, headers=world.master)
            hit = leaks(response.text)
            if hit:
                found[path] = hit
        assert found == {}, json.dumps(found, indent=2)

    def test_the_configuration_shows_the_places_secrets_are_kept_and_not_the_secrets(
        self, world: World
    ) -> None:
        shown = world.client.get("/api/config", headers=world.master)

        assert shown.status_code == 200
        assert Config().get("monitor.smtp.password") == SECRETS["smtp"]
        assert leaks(shown.text) == []
        assert "***" in shown.text

    def test_an_application_shows_its_variables_masked(self, world: World) -> None:
        shown = world.client.get(f"/api/apps/{SHOP}/env", headers=world.master)

        assert shown.status_code == 200, shown.text
        assert leaks(shown.text) == []
        assert '"STRIPE_API_KEY":"***"' in shown.text.replace(" ", "")

    def test_the_sweep_would_notice_a_leak(self, world: World) -> None:
        """A guard that cannot fail guards nothing: the secret is found where it is shown on purpose."""
        revealed = world.client.get(f"/api/apps/{SHOP}/env?unmask=true", headers=world.master)

        assert revealed.status_code == 200, revealed.text
        assert "app-env" in leaks(revealed.text)


class TestNoExportCarriesASecret:
    def test_an_application_exported_without_asking_for_its_secrets(self, world: World) -> None:
        exported = world.client.get(f"/api/apps/{SHOP}/export", headers=world.master)

        assert exported.status_code == 200, exported.text
        assert leaks(exported.text) == []

    def test_a_declaration_never_carries_one(self, world: World) -> None:
        from noust.deployers.project_apply import export_declaration

        assert leaks(export_declaration(SHOP)) == []

    def test_the_dump_of_the_store(self, world: World, tmp_path: Path) -> None:
        from noust.cli.commands import store as store_commands

        target = tmp_path / "dump.json"
        _service(world.store)

        store_commands._store_export(str(target), verbose=False)

        text = target.read_text()
        assert leaks(text) == []
        # It is still an inventory: what is not secret is there.
        assert "shop-unit" in text and SHOP in text

    def test_the_dump_of_the_store_keeps_them_only_when_asked(
        self, world: World, tmp_path: Path
    ) -> None:
        from noust.cli.commands import store as store_commands

        target = tmp_path / "dump-with-secrets.json"
        _service(world.store)

        store_commands._store_export(str(target), verbose=False, with_secrets=True)

        assert {"service-env", "app-env"} <= set(leaks(target.read_text()))


def _freeze(world: World, tmp_path: Path, reason: str) -> Any:
    """
    Args:
        world: The server.
        tmp_path: Where the package is written.
        reason: Why.

    Returns:
        The result of freezing an incident on a server whose commands are scripted.
    """
    from noust.core import audit
    from noust.core.ens import incident
    from noust.core.runner import FakeRunner

    fake = FakeRunner()
    fake.script(["journalctl"], stdout="2026-10-16T10:00:00+0000 host noust-web[1]: started\n")
    fake.script(["systemctl", "list-units"], stdout="noust-web.service loaded active running\n")
    fake.script(["ss", "-tulpn"], stdout="tcp LISTEN 0 128 127.0.0.1:8080\n")
    audit.record("system.start")
    return incident.freeze(
        reason=reason,
        actor="cli:root",
        runner=fake,
        store=world.store,
        output_root=tmp_path / "incidents",
        lock=False,
    )


class TestTheEvidenceOfAnIncidentHoldsNoSecret:
    def test_not_a_file_of_the_package_nor_the_snapshot_of_the_store(
        self, world: World, tmp_path: Path
    ) -> None:
        _service(world.store)

        result = _freeze(world, tmp_path, "INC-7 a key leaked")

        files = [path for path in result.directory.rglob("*") if path.is_file()]
        assert any(path.name == "noust.db" for path in files)
        for path in files:
            assert leaks(path.read_bytes().decode("utf-8", errors="replace")) == [], path.name
        said = json.loads((result.directory / "store" / "scrubbed.json").read_text())
        assert said["rows_changed"]["apps"] >= 1 and said["rows_changed"]["services"] >= 1

    def test_the_store_itself_keeps_what_it_runs_with(self, world: World, tmp_path: Path) -> None:
        _service(world.store)

        _freeze(world, tmp_path, "INC-8")

        live = world.store.get_app(SHOP)
        assert live is not None and live.env_vars["STRIPE_API_KEY"] == SECRETS["app-env"]


def _plant(connection: sqlite3.Connection, value: str) -> None:
    """
    Put ``value`` in every column the rules name, in a real row of its table.

    Args:
        connection: A connection to a store.
        value: A recognisable secret.
    """
    now = "2026-10-16T10:00:00"
    env = json.dumps({"API_SECRET": value, "NODE_ENV": "production"})
    connection.execute(
        "INSERT INTO accounts (username, role, password_hash, totp_secret, totp_pending_secret, "
        "backup_codes, created_at, updated_at) VALUES ('probe', 'viewer', ?, ?, ?, ?, 1, 1)",
        (value, value, value, json.dumps([value])),
    )
    connection.execute(
        "INSERT INTO site_auth_users VALUES ('shop.acme.example', 'ana', ?, NULL, ?)", (value, now)
    )
    app_id = connection.execute("SELECT id FROM apps LIMIT 1").fetchone()[0]
    connection.execute(
        "INSERT INTO app_container (app_id, build_args, updated_at) VALUES (?, ?, ?)",
        (app_id, env, now),
    )
    connection.execute(
        "INSERT INTO approval_requests (action, kind, method, path, parameters, fingerprint, "
        "requester_kind, requester_name, created_at, expires_at) "
        "VALUES ('x', 'infrastructure', 'PATCH', '/p', ?, 'f', 'account', 'ana', 1, 2)",
        (json.dumps({"set": {"DB_PASSWORD": value}, "note": f"token={value}"}),),
    )
    connection.execute(
        "INSERT INTO cloud_init_scripts VALUES ('boot', 'shell', NULL, ?, ?, NULL, ?)",
        (f"#!/bin/sh\nexport API_TOKEN={value}\n", now, now),
    )
    connection.execute(
        "INSERT INTO database_query_history (owner, engine, db_name, statement) VALUES "
        "('ana', 'postgresql', 'shop', ?)",
        (f"CREATE USER bob PASSWORD '{value}'",),
    )
    connection.execute(
        "INSERT INTO shared_variables (scope, owner, name, value, secret, created_at, updated_at) "
        "VALUES ('server', 0, 'LICENSE', ?, 1, ?, ?)",
        (value, now, now),
    )
    connection.execute(
        "INSERT INTO app_env_variables (app_id, name, build_value) VALUES (?, 'NPM_TOKEN', ?)",
        (app_id, value),
    )
    connection.commit()


class TestTheCopyOfTheStore:
    def test_every_column_that_could_hold_a_secret_has_been_decided(self, world: World) -> None:
        from noust.core.store_secrets import columns_to_review

        connection = sqlite3.connect(world.store.db_path)
        try:
            undecided = columns_to_review(connection)
        finally:
            connection.close()

        assert undecided == [], (
            "A column that reads as a secret, or that holds a value under another name, needs a "
            "rule in noust.core.store_secrets.RULES, or an entry in NOT_SECRET saying why it is "
            f"not one: {undecided}"
        )

    def test_the_rules_name_columns_that_exist(self, world: World) -> None:
        from noust.core import store_secrets

        connection = sqlite3.connect(world.store.db_path)
        try:
            have = {
                table: {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                for (table,) in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        finally:
            connection.close()
        named = [(t, c) for t, cols in store_secrets.RULES.items() for c in cols]
        named += list(store_secrets.NOT_SECRET)
        for table, (name, value, flag) in store_secrets.VARIABLE_TABLES.items():
            named += [(table, name), (table, value)] + ([(table, flag)] if flag else [])

        missing = [(t, c) for t, c in named if c not in have.get(t, set())]

        assert missing == [], f"renamed or removed, and the rule was left behind: {missing}"

    def test_a_copy_loses_the_value_of_every_column_the_rules_name(
        self, world: World, tmp_path: Path
    ) -> None:
        from noust.core import store_secrets

        value = "zq-planted-4f6e0c91b7a2"
        connection = sqlite3.connect(world.store.db_path)
        try:
            _plant(connection, value)
        finally:
            connection.close()
        copy = tmp_path / "copy.db"
        reader = sqlite3.connect(world.store.db_path)
        writer = sqlite3.connect(copy)
        reader.backup(writer)
        writer.close()
        reader.close()
        assert value.encode() in copy.read_bytes()

        changed = store_secrets.scrub_database(copy)

        assert value.encode() not in copy.read_bytes()
        assert set(changed) >= {
            "accounts",
            "site_auth_users",
            "app_container",
            "approval_requests",
            "cloud_init_scripts",
            "database_query_history",
            "shared_variables",
            "app_env_variables",
        }
        # What is not secret is still there, and the live store was not touched.
        kept = sqlite3.connect(copy)
        try:
            assert ("probe",) in kept.execute("SELECT username FROM accounts").fetchall()
            assert (
                "production" in kept.execute("SELECT build_args FROM app_container").fetchone()[0]
            )
        finally:
            kept.close()
        live = sqlite3.connect(world.store.db_path)
        try:
            assert live.execute(
                "SELECT totp_secret FROM accounts WHERE username = 'probe'"
            ).fetchone() == (value,)
        finally:
            live.close()

    def test_a_copy_of_an_older_store_is_scrubbed_of_what_it_has(self, tmp_path: Path) -> None:
        from noust.core import store_secrets

        old = tmp_path / "old.db"
        connection = sqlite3.connect(old)
        connection.execute("CREATE TABLE services (name TEXT, environment TEXT)")
        connection.execute(
            "INSERT INTO services VALUES ('a', ?)",
            (json.dumps({"DB_PASSWORD": "hunter2-hunter2"}),),
        )
        connection.commit()
        connection.close()

        assert store_secrets.scrub_database(old) == {"services": 1}
        assert b"hunter2-hunter2" not in old.read_bytes()


def _service(store: NoustStore) -> Any:
    """
    Args:
        store: The store.

    Returns:
        A service that runs with a secret in its environment.
    """
    from noust.core.store import Service

    return store.create_service(
        Service(name="shop-unit", environment={"WORKER_SECRET": SECRETS["service-env"]})
    )
