# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app identity migrate`` reads where the application writes, not only whether it answers.

The migration moved an application whose only test was "does it answer": an application that
writes outside its own tree, into a folder the shared account owns (a customer's
``/var/www/shop-static``, which the panel fills and the web serves; the central's
``/var/www/data/case-files``), would pass that gate and fail days later when somebody uploaded
a file. The paths in its ``.env`` and in its unit are read first; a folder its new account could
not write is refused by name, or made reachable through a group the operator names.
"""

# ruff: noqa: F811

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.exceptions import DeploymentError, ValidationError
from noust.managers import app_identity
from tests.test_app_identity import (  # noqa: F401 - fixtures and helpers used by name
    Gate,
    as_root,
    existing,
    migrate,
    own_store,
)
from tests.test_release_pipeline import (  # noqa: F401 - fixtures used by name
    DOMAIN,
    machine,
    root,
    store,
)

ACCOUNT = "noust-app-shop-example-com"
#: Ids the test gives the accounts: the shared one is the user running the test, so the
#: permission bits of real files in a temporary directory mean what they would on a server.
NEW_UID = 59999
SHARED_GID = 4242


@pytest.fixture
def accounts(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Who the shared account, the new one and a group are, for the permission check."""
    me, my_group = os.getuid(), os.getgid()
    groups: dict[str, int] = {"www-data": my_group, "uploads": SHARED_GID}

    def ids(account: str) -> tuple[int, frozenset[int]] | None:
        if account == "www-data":
            return me, frozenset({my_group})
        if account == ACCOUNT:
            return NEW_UID, frozenset({NEW_UID})
        return None

    monkeypatch.setattr(app_identity, "_ids", ids)
    monkeypatch.setattr(app_identity, "_group_id", lambda name: groups.get(name))
    return SimpleNamespace(groups=groups, me=me, my_group=my_group)


def folder(tmp_path: Path, name: str = "shop-static", mode: int = 0o755) -> Path:
    """A folder the shared account (this user) owns, outside the application's tree."""
    path = tmp_path / name
    path.mkdir()
    path.chmod(mode)
    return path


def env(world: SimpleNamespace, text: str) -> None:
    (world.app_path / ".env").write_text(text)


def test_a_folder_the_env_names_that_only_the_shared_account_can_write_is_refused_by_name(
    tmp_path: Path, existing: SimpleNamespace, accounts: SimpleNamespace
) -> None:
    uploads = folder(tmp_path)
    env(existing, f"SECRET=1\nUPLOAD_DIR={uploads}\n")
    gate = Gate(True)

    with pytest.raises(ValidationError) as refused:
        migrate(existing, gate)

    assert str(uploads) in refused.value.message
    details = refused.value.details or ""
    assert ".env (UPLOAD_DIR)" in details
    assert "0755" in details
    assert "--shared-group" in details and "shared/" in details
    # Nothing was touched: no account made, no file handed over, nothing restarted.
    assert existing.runner.calls == [] and gate.restarts == 0
    app = existing.store.get_app("shop.example.com")
    assert app is not None and app.identity is None


def test_every_such_folder_is_named_not_only_the_first(
    tmp_path: Path, existing: SimpleNamespace, accounts: SimpleNamespace
) -> None:
    first, second = folder(tmp_path, "a"), folder(tmp_path, "b")
    env(existing, f"ONE={first}\nTWO={second}\n")

    with pytest.raises(ValidationError) as refused:
        migrate(existing, Gate())

    assert str(first) in refused.value.message and str(second) in refused.value.message


def test_what_the_new_account_can_write_anyway_is_not_a_problem(
    tmp_path: Path, existing: SimpleNamespace, accounts: SimpleNamespace
) -> None:
    open_to_all = folder(tmp_path, "scratch", mode=0o777)
    inside = existing.app_path / "uploads"
    inside.mkdir()
    env(
        existing,
        f"SCRATCH={open_to_all}\nINSIDE={inside}\nCACHE={existing.cache}\n"
        "URL=https://example.com/uploads/a\nPLAIN=/usr/bin/node\nNAME=uploads\n",
    )

    done = migrate(existing, Gate(True))

    assert done.account == ACCOUNT


def test_a_folder_the_shared_account_could_not_write_either_is_not_blamed_on_the_move(
    tmp_path: Path, existing: SimpleNamespace, accounts: SimpleNamespace, monkeypatch: Any
) -> None:
    somebody_elses = folder(tmp_path, "theirs", mode=0o755)
    env(existing, f"THEIRS={somebody_elses}\n")
    # The shared account is not the owner: it could not write there before either.
    monkeypatch.setattr(
        app_identity,
        "_ids",
        lambda account: (NEW_UID + 1, frozenset({NEW_UID + 1})) if account == "www-data" else None,
    )

    assert (
        app_identity.outside_paths(existing.store.get_app("shop.example.com"), existing.units) == []
    )


def test_a_folder_that_does_not_exist_yet_is_judged_by_the_first_one_that_does(
    tmp_path: Path, existing: SimpleNamespace, accounts: SimpleNamespace
) -> None:
    parent = folder(tmp_path, "var-data")
    env(existing, f"DATA_DIR={parent / 'new' / 'deeper'}\nDB=sqlite:///{parent}/app.db\n")

    found = app_identity.outside_paths(existing.store.get_app("shop.example.com"), existing.units)

    assert sorted(item.path for item in found) == sorted(
        [str(parent / "new" / "deeper"), f"{parent}/app.db"]
    )


def test_the_unit_is_read_too_its_writable_paths_and_its_environment(
    tmp_path: Path, existing: SimpleNamespace, accounts: SimpleNamespace
) -> None:
    declared, in_env = folder(tmp_path, "declared"), folder(tmp_path, "inenv")
    body = existing.units.bodies["shop-example-com"]
    existing.units.bodies["shop-example-com"] = body.replace(
        "[Service]\n",
        f'[Service]\nReadWritePaths={declared}\nEnvironment="MEDIA={in_env}"\n',
        1,
    )

    found = app_identity.outside_paths(existing.store.get_app("shop.example.com"), existing.units)

    where = {item.path: item.found_in for item in found}
    assert where[str(declared)] == "shop-example-com.service (ReadWritePaths)"
    assert where[str(in_env)] == "shop-example-com.service (Environment MEDIA)"


def test_a_group_the_folder_is_writable_by_lets_the_move_go_ahead_and_the_account_joins_it(
    tmp_path: Path, existing: SimpleNamespace, accounts: SimpleNamespace
) -> None:
    uploads = folder(tmp_path, mode=0o775)
    os.chown(uploads, accounts.me, accounts.my_group)
    env(existing, f"UPLOAD_DIR={uploads}\n")
    accounts.groups["uploads"] = accounts.my_group
    gate = Gate(True)

    done = app_identity.migrate(
        "shop.example.com",
        actor="alice",
        logger=app_identity.Logger(verbose=False),
        runner=existing.runner,
        store=existing.store,
        services=existing.units,  # type: ignore[arg-type]
        gate_for=lambda app, store, log: gate,  # type: ignore[arg-type,return-value]
        shared_group="uploads",
    )

    assert done.account == ACCOUNT
    assert ("usermod", "-aG", "uploads", ACCOUNT) in existing.runner.calls


def test_a_group_that_cannot_write_the_folder_does_not_clear_it(
    tmp_path: Path, existing: SimpleNamespace, accounts: SimpleNamespace
) -> None:
    uploads = folder(tmp_path, mode=0o755)
    env(existing, f"UPLOAD_DIR={uploads}\n")

    with pytest.raises(ValidationError) as refused:
        app_identity.refuse_migration(
            existing.store.get_app("shop.example.com"),
            services=existing.units,  # type: ignore[arg-type]
            shared_group="uploads",
        )

    assert str(uploads) in refused.value.message
    assert "group uploads" in (refused.value.details or "")


def test_a_group_that_does_not_exist_is_refused_before_anything_changes(
    existing: SimpleNamespace, accounts: SimpleNamespace
) -> None:
    with pytest.raises(ValidationError, match="no group called nonesuch"):
        app_identity.refuse_migration(
            existing.store.get_app("shop.example.com"),
            services=existing.units,  # type: ignore[arg-type]
            shared_group="nonesuch",
        )


@pytest.mark.parametrize("name", ["", "-x", "a b", "a;b", "a\n", "x" * 40, "../etc"])
def test_the_name_of_a_group_is_judged_before_it_reaches_usermod(
    existing: SimpleNamespace, accounts: SimpleNamespace, name: str
) -> None:
    with pytest.raises(ValidationError):
        app_identity.refuse_migration(
            existing.store.get_app("shop.example.com"),
            services=existing.units,  # type: ignore[arg-type]
            shared_group=name,
        )


def test_joining_the_group_is_undone_when_the_application_does_not_answer(
    tmp_path: Path, existing: SimpleNamespace, accounts: SimpleNamespace
) -> None:
    uploads = folder(tmp_path, mode=0o775)
    env(existing, f"UPLOAD_DIR={uploads}\n")
    accounts.groups["uploads"] = accounts.my_group
    gate = Gate(False, True)

    with pytest.raises(DeploymentError):
        app_identity.migrate(
            "shop.example.com",
            actor="alice",
            logger=app_identity.Logger(verbose=False),
            runner=existing.runner,
            store=existing.store,
            services=existing.units,  # type: ignore[arg-type]
            gate_for=lambda app, store, log: gate,  # type: ignore[arg-type,return-value]
            shared_group="uploads",
        )

    assert ("gpasswd", "-d", ACCOUNT, "uploads") in existing.runner.calls


def test_a_server_where_the_accounts_cannot_be_looked_up_judges_nothing(
    tmp_path: Path, existing: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No shared account in /etc/passwd (a restore on another machine): there is nothing to compare.
    monkeypatch.setattr(app_identity, "_ids", lambda account: None)
    env(existing, f"UPLOAD_DIR={folder(tmp_path)}\n")

    assert (
        app_identity.outside_paths(existing.store.get_app("shop.example.com"), existing.units) == []
    )


def test_the_api_refuses_before_queueing_and_names_the_folder(
    tmp_path: Path, own_store: Any, accounts: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.core.store import App
    from noust.core.teams import ScopedStore
    from tests.test_app_identity import identity_client

    uploads = folder(tmp_path)
    app_path = tmp_path / "apps" / "shop-example-com"
    app_path.mkdir(parents=True)
    (app_path / ".env").write_text(f"UPLOAD_DIR={uploads}\n")
    own_store.create_app(App(domain="shop.example.com", app_type="nodejs", app_path=str(app_path)))
    monkeypatch.setattr("noust.web.api.identity.scoped_store", lambda: ScopedStore(store=own_store))
    monkeypatch.setattr("noust.web.api.deps.scoped_store", lambda: ScopedStore(store=own_store))
    monkeypatch.setattr("noust.managers.app_identity.get_store", lambda: own_store)
    monkeypatch.setattr(app_identity, "running_as_root", lambda: True)

    response = identity_client(elevated=True).post("/api/apps/shop.example.com/identity/migrate")

    assert response.status_code == 400
    assert str(uploads) in response.text


def test_the_command_passes_the_group_on(
    existing: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from click.testing import CliRunner

    from noust.cli.app import cli

    seen: dict[str, Any] = {}

    def fake(domain: str, **kwargs: Any) -> Any:
        seen.update(kwargs, domain=domain)
        return SimpleNamespace(to_dict=lambda: {"domain": domain})

    monkeypatch.setattr(app_identity, "migrate", fake)

    result = CliRunner().invoke(
        cli,
        ["app", "identity", "migrate", "shop.example.com", "--shared-group", "uploads", "-y"],
    )

    assert result.exit_code == 0, result.output
    assert seen["shared_group"] == "uploads" and seen["domain"] == "shop.example.com"
