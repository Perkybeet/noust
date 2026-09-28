# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The monorepo deployer never writes a ``DATABASE_URL`` it did not provision.

Its detection step guesses a database name, a user and a password from the
repository. The password is only a placeholder: when provisioning fails
(say, the grant is refused after the user was created with the helper's own
password), 2.3's first cut logged a warning and went on to write that
placeholder into ``.env``, an application that could never authenticate. These
tests pin down that a failed provisioning leaves ``DATABASE_URL`` unset and
says so, and that the next deploy picks up the user the failed one made.
"""

from __future__ import annotations

import functools
from pathlib import Path

import pytest

from tests.test_database_ownership import FakeManager, FakeRegistry
from wasm.core.exceptions import DatabaseError
from wasm.core.runner import FakeRunner
from wasm.core.secrets import SecretStore
from wasm.core.store import App, WASMStore
from wasm.deployers import monorepo as monorepo_module
from wasm.deployers.helpers import databases as db_helpers
from wasm.deployers.monorepo import DatabaseConfig, MonorepoDeployer


@pytest.fixture
def store(tmp_path: Path):
    WASMStore.reset_instance()
    instance = WASMStore(tmp_path / "wasm.db")
    yield instance
    WASMStore.reset_instance()


@pytest.fixture
def manager(tmp_path: Path, store: WASMStore, monkeypatch: pytest.MonkeyPatch) -> FakeManager:
    fake = FakeManager()
    monkeypatch.setattr(db_helpers, "DatabaseRegistry", FakeRegistry(fake))
    monkeypatch.setattr(
        monorepo_module,
        "provision_database",
        functools.partial(
            db_helpers.provision_database,
            store=store,
            secret_store=SecretStore(root=tmp_path / "secrets"),
        ),
    )
    return fake


def _deployer(tmp_path: Path, store: WASMStore, monkeypatch: pytest.MonkeyPatch):
    app_path = tmp_path / "app"
    app_path.mkdir(exist_ok=True)
    deployer = MonorepoDeployer(runner=FakeRunner())
    deployer.configure("example.com", "src", app_path=app_path, ssl=False)
    deployer.store = store
    if store.get_app("example.com") is None:
        store.create_app(App(domain="example.com", app_path=str(app_path)))
    monkeypatch.setattr(
        deployer,
        "_detect_database_requirements",
        lambda: {
            "postgresql": DatabaseConfig(
                engine="postgresql",
                name="example_db",
                user="example_user",
                password="detection-time-placeholder",
                port=5432,
            )
        },
    )
    warnings: list[str] = []
    monkeypatch.setattr(deployer.logger, "warning", warnings.append)
    return deployer, warnings


def test_a_failed_provisioning_writes_no_database_url_and_says_so(
    tmp_path: Path, store: WASMStore, manager: FakeManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager.fail_grant = True
    deployer, warnings = _deployer(tmp_path, store, monkeypatch)

    deployer._provision_databases()
    deployer._configure_environment()

    assert "postgresql" not in deployer.databases
    assert "DATABASE_URL" not in deployer.env_vars
    assert not (deployer.app_path / ".env").exists()
    text = "\n".join(warnings)
    assert "Failed to grant privileges" in text
    assert "permission denied" in text
    assert "DATABASE_URL was not written" in text
    assert "detection-time-placeholder" not in text


def test_the_deploy_after_a_failed_grant_writes_the_password_the_user_has(
    tmp_path: Path, store: WASMStore, manager: FakeManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager.fail_grant = True
    deployer, _warnings = _deployer(tmp_path, store, monkeypatch)
    deployer._provision_databases()

    manager.fail_grant = False
    retry, warnings = _deployer(tmp_path, store, monkeypatch)
    retry._provision_databases()
    retry._configure_environment()

    real_password = manager.users["example_user"]
    assert warnings == []
    assert retry.env_vars["DATABASE_URL"] == (
        f"postgresql://example_user:{real_password}@localhost:5432/example_db"
    )
    assert manager.calls.count("create_user:example_user") == 1


def test_a_refused_user_leaves_the_other_databases_in_place(
    tmp_path: Path, store: WASMStore, manager: FakeManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager.users["example_user"] = "not-ours"
    deployer, warnings = _deployer(tmp_path, store, monkeypatch)
    redis = DatabaseConfig(engine="redis", name="", port=6379, db_number=0)
    detected = deployer._detect_database_requirements()
    monkeypatch.setattr(
        deployer, "_detect_database_requirements", lambda: {**detected, "redis": redis}
    )
    monkeypatch.setattr(deployer, "_provision_redis", lambda _config: None)

    deployer._provision_databases()

    assert list(deployer.databases) == ["redis"]
    assert any("WASM did not create" in warning for warning in warnings)


def test_provisioning_failure_is_a_database_error_not_a_crash(
    tmp_path: Path, store: WASMStore, manager: FakeManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    deployer, _warnings = _deployer(tmp_path, store, monkeypatch)
    manager.fail_grant = True

    with pytest.raises(DatabaseError):
        deployer._provision_postgresql(deployer._detect_database_requirements()["postgresql"])
