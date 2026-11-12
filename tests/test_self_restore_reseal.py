# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Restoring Noust's bundle between a user's store and the system's seals its secrets again (item 194).

The secrets Noust protects (the credentials of a backup destination, the GitHub leases) are sealed
under a key that lives beside a user's store and in ``/etc/noust`` for the system's. A bundle of a
server of one kind restored onto the other used to put the key where the first kept it, and the
secrets could not be opened: the restore said so and left it there. It now finishes the job, as
``noust setup move-store`` does: each protected secret is opened with the key the bundle carries
and sealed under the key of the server it lands on, the bundle's key is not left beside ciphertext
it no longer opens, and a bundle whose secrets its own key cannot open is refused before anything
changes, with what to do.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import paths
from noust.core.exceptions import BackupError
from noust.core.secrets import SecretStore
from noust.core.store import App, NoustStore
from noust.managers.self_backup import SelfBackupManager
from noust.managers.self_restore import SelfRestore
from tests.support.self_backup import (  # noqa: F401
    PASSPHRASE,
    NoRoles,
    Runner,
    _reset_store,
    cheap_scrypt,
    runner,
)

# ruff: noqa: F811

pytestmark = pytest.mark.allow_subprocess

SECRET = "backups/destinations/nas"


class Machine:
    """
    A server whose store is in the user's directory, and a server that keeps it in the system's.
    """

    def __init__(self, base: Path) -> None:
        self.config = base / "etc-noust"
        self.system = base / "var-lib-noust"
        self.home = base / "root" / ".local" / "share" / "noust"
        self.bundles = base / "backups" / "self"
        self.letsencrypt = base / "etc-letsencrypt"
        self.units = base / "systemd"
        for directory in (self.config, self.system, self.home, self.units):
            directory.mkdir(parents=True)

    def user_server(self, runner: Runner) -> SelfBackupManager:
        return SelfBackupManager(
            runner=runner,
            sources={"config": self.config, "state": self.home},
            letsencrypt=self.letsencrypt,
            systemd_dir=self.units,
            directory=self.bundles,
            webservers={},
            database_service=lambda: NoRoles(),
        )

    def system_server(self, runner: Runner) -> SelfBackupManager:
        return SelfBackupManager(
            runner=runner,
            sources={"config": self.config, "state": self.system},
            letsencrypt=self.letsencrypt,
            systemd_dir=self.units,
            directory=self.bundles,
            webservers={},
            database_service=lambda: NoRoles(),
        )


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Machine]:
    made = Machine(tmp_path)
    monkeypatch.setattr(paths, "CONFIG_DIR", made.config)
    monkeypatch.setattr(paths, "STATE_DIR", made.system)
    monkeypatch.setattr(paths, "BACKUP_DIR", tmp_path / "backups")
    (made.config / "config.yaml").write_text("web:\n  port: 8443\n")
    NoustStore.reset_instance()
    store = NoustStore(made.home / "noust.db")
    store.create_app(App(domain="shop.example.com", app_type="nodejs", app_path="/srv/shop"))
    SecretStore(root=made.home / "secrets").write_protected_json(SECRET, {"password": "nas-pass"})
    yield made
    store.close()
    NoustStore.reset_instance()


def opens(root: Path, name: str = SECRET) -> dict[str, str]:
    return SecretStore(root=root).read_protected_json(name)  # type: ignore[return-value]


class TestABundleOfAUsersStoreOntoTheSystemsOne:
    def test_the_secrets_open_with_the_key_of_the_server_they_landed_on(
        self, machine: Machine, runner: Runner
    ) -> None:
        made = machine.user_server(runner).create(PASSPHRASE)

        report = SelfRestore(machine.system_server(runner), restart=False).restore(
            made.path.name, PASSPHRASE, force=True
        )

        assert opens(machine.system / "secrets") == {"password": "nas-pass"}
        assert (machine.config / "secrets.key").is_file()
        assert any("sealed again" in note for note in report.notes)

    def test_the_key_of_the_other_kind_of_server_is_not_left_beside_the_secrets(
        self, machine: Machine, runner: Runner
    ) -> None:
        made = machine.user_server(runner).create(PASSPHRASE)

        SelfRestore(machine.system_server(runner), restart=False).restore(
            made.path.name, PASSPHRASE, force=True
        )

        assert not (machine.system / "secrets.key").exists()

    def test_nothing_says_they_cannot_be_opened_any_more(
        self, machine: Machine, runner: Runner
    ) -> None:
        made = machine.user_server(runner).create(PASSPHRASE)

        report = SelfRestore(machine.system_server(runner), restart=False).restore(
            made.path.name, PASSPHRASE, force=True
        )

        assert not [note for note in report.notes if "enter them again" in note]

    def test_what_the_server_already_had_sealed_still_opens(
        self, machine: Machine, runner: Runner
    ) -> None:
        SecretStore(root=machine.system / "secrets").write_protected_json(
            "github/lease", {"token": "kept"}
        )
        made = machine.user_server(runner).create(PASSPHRASE)

        # The server being restored onto has secrets and no store yet, so it has nothing to take a
        # bundle of first.
        SelfRestore(machine.system_server(runner), restart=False).restore(
            made.path.name, PASSPHRASE, force=True, safety_copy=False
        )

        assert opens(machine.system / "secrets", "github/lease") == {"token": "kept"}
        assert opens(machine.system / "secrets") == {"password": "nas-pass"}


class TestABundleOfTheSystemsStoreOntoAUsersOne:
    def test_the_secrets_open_with_the_key_beside_the_users_secrets(
        self, machine: Machine, runner: Runner
    ) -> None:
        NoustStore.reset_instance()
        NoustStore(machine.system / "noust.db")
        SecretStore(root=machine.system / "secrets").write_protected_json(
            SECRET, {"password": "system-pass"}
        )
        made = machine.system_server(runner).create(PASSPHRASE)
        assert made.manifest["secrets_key"] == {"path": "config/secrets.key"}
        # The server the bundle goes to keeps its store (and so its key) in a user's directory.
        target = tmp_user_server(machine, runner)

        SelfRestore(target, restart=False).restore(made.path.name, PASSPHRASE, force=True)

        assert opens(machine.home / "secrets") == {"password": "system-pass"}


def tmp_user_server(machine: Machine, runner: Runner) -> SelfBackupManager:
    # Its secrets are not in the system's state directory, so its key is not in the configuration's.
    (machine.home / "secrets").mkdir(parents=True, exist_ok=True)
    return machine.user_server(runner)


class TestABundleThatCannotBeOpenedWithItsOwnKeyChangesNothing:
    def test_it_is_refused_with_the_step_to_take(self, machine: Machine, runner: Runner) -> None:
        manager = machine.user_server(runner)
        # The key the bundle will carry no longer opens the secret beside it.
        (machine.home / "secrets.key").write_text(secrets.token_hex(32) + "\n")
        made = manager.create(PASSPHRASE)
        target = machine.system_server(runner)
        (machine.system / "secrets").mkdir()
        before = sorted(path.name for path in machine.system.rglob("*"))

        with pytest.raises(BackupError) as refused:
            SelfRestore(target, restart=False).restore(made.path.name, PASSPHRASE, force=True)

        text = f"{refused.value.message}\n{refused.value.details}"
        assert SECRET in text
        assert "Nothing was" in text
        assert sorted(path.name for path in machine.system.rglob("*")) == before
        assert not (machine.config / "secrets.key").exists()
        assert ("systemctl", "stop", "noust-web.service") not in runner.calls


class TestAServerThatKeepsItsKeyWhereTheBundleDoes:
    def test_nothing_is_sealed_again_and_nothing_is_said_of_it(
        self, machine: Machine, runner: Runner
    ) -> None:
        manager = machine.user_server(runner)
        made = manager.create(PASSPHRASE)

        report = SelfRestore(manager, restart=False).restore(made.path.name, PASSPHRASE, force=True)

        assert not [n for n in report.notes if "sealed again" in n or "enter them again" in n]
        assert opens(machine.home / "secrets") == {"password": "nas-pass"}
