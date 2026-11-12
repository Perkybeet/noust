# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust self-backup`` takes the store Noust uses, wherever it is (item 194).

The store lives in the state directory when that exists and is writable, and in the user's
directory otherwise (``core/store.py``). A server whose store had never moved from
``~/.local/share/noust`` and whose ``/var/lib/noust`` held an empty ``noust.db`` (somebody made
the directory, and nothing wrote a store there) got a bundle of the empty file: the bundle failed
its own verification, and without the verification it would have been sent away with no store in
it. What is pinned:

- the directory that holds the store Noust uses is the bundle's ``state``, with its secrets and
  the key they are sealed under, which move with the store;
- the state directory of the system, when it is another one, is kept apart (``system-state``) and
  its empty ``noust.db`` is left out, because it is not a store;
- the bundle says which of its files is the store, and verification and restore read that one;
- a bundle that would hold no store is not made, instead of being made and sent.
"""

from __future__ import annotations

import sqlite3
import tarfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import paths
from noust.core.ens import envelope
from noust.core.exceptions import BackupError
from noust.core.secrets import SecretStore
from noust.core.store import App, NoustStore
from noust.managers.self_backup import SelfBackupManager, store_path_in
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


class Machine:
    """
    A server whose store is in the user's directory and whose system state directory holds only
    an empty ``noust.db`` and something else of its own.
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

    def manager(self, runner: Runner) -> SelfBackupManager:
        return SelfBackupManager(
            runner=runner,
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
    # What was left in the system directory: an empty store file, and state that is not the store's.
    (made.system / "noust.db").write_bytes(b"")
    (made.system / "uploads" / "abc").mkdir(parents=True)
    (made.system / "uploads" / "abc" / "index.html").write_text("<p>uploaded</p>")
    # The store Noust really uses, with a secret sealed under the key that lives beside it.
    NoustStore.reset_instance()
    store = NoustStore(made.home / "noust.db")
    store.create_app(App(domain="shop.example.com", app_type="nodejs", app_path="/srv/shop"))
    SecretStore(root=made.home / "secrets").write_protected_json(
        "backups/destinations/nas", {"password": "nas-password"}
    )
    (made.home / "deploy-logs").mkdir()
    (made.home / "deploy-logs" / "shop.log").write_text("deployed")
    yield made
    store.close()
    NoustStore.reset_instance()


def unpack(bundle: Path, into: Path, runner: Runner) -> Path:
    archive = into / "bundle.tar.gz"
    envelope.open_envelope(bundle, archive, PASSPHRASE, runner=runner)
    with tarfile.open(archive) as tar:
        tar.extractall(into / "x", filter="data")
    return into / "x" / "noust-self"


class TestTheBundleOfAServerWhoseStoreIsInTheUsersDirectory:
    def test_the_directory_of_the_store_is_the_bundles_state(
        self, machine: Machine, runner: Runner
    ) -> None:
        made = machine.manager(runner).create(PASSPHRASE)

        assert made.manifest["sources"]["state"] == str(machine.home)
        assert made.manifest["sources"]["system-state"] == str(machine.system)
        assert made.manifest["sources"]["config"] == str(machine.config)

    def test_the_store_its_secrets_and_the_key_they_are_sealed_under_are_in_it(
        self, machine: Machine, runner: Runner
    ) -> None:
        made = machine.manager(runner).create(PASSPHRASE)

        files = {entry["path"] for entry in made.manifest["files"]}
        assert {
            "state/noust.db",
            "state/secrets.key",
            "state/secrets/backups/destinations/nas",
            "state/deploy-logs/shop.log",
            "system-state/uploads/abc/index.html",
            "config/config.yaml",
        } <= files
        assert made.manifest["store"]["path"] == "state/noust.db"
        assert made.manifest["contains"]["store"] is True
        assert made.manifest["contains"]["secrets"] is True

    def test_an_empty_store_file_is_not_a_store_and_is_left_out_saying_so(
        self, machine: Machine, runner: Runner
    ) -> None:
        made = machine.manager(runner).create(PASSPHRASE)

        files = {entry["path"] for entry in made.manifest["files"]}
        assert "system-state/noust.db" not in files
        (skipped,) = [s for s in made.manifest["skipped"] if s["path"] == "system-state/noust.db"]
        assert "empty" in skipped["reason"]

    def test_the_bundle_verifies_and_the_store_in_it_is_the_one_with_the_application(
        self, machine: Machine, runner: Runner, tmp_path: Path
    ) -> None:
        manager = machine.manager(runner)
        made = manager.create(PASSPHRASE)

        report = manager.verify(made.path.name, PASSPHRASE)

        assert report["ok"], report["problems"]
        assert report["contains"]["store"] is True
        root = unpack(made.path, tmp_path / "opened", runner)
        connection = sqlite3.connect(root / "state" / "noust.db")
        try:
            assert connection.execute("SELECT domain FROM apps").fetchall() == [
                ("shop.example.com",)
            ]
        finally:
            connection.close()

    def test_the_secret_in_the_bundle_still_opens_with_the_key_in_the_bundle(
        self, machine: Machine, runner: Runner, tmp_path: Path
    ) -> None:
        made = machine.manager(runner).create(PASSPHRASE)
        root = unpack(made.path, tmp_path / "opened", runner)

        opened = SecretStore(root=root / "state" / "secrets").read_protected_json(
            "backups/destinations/nas"
        )

        assert opened == {"password": "nas-password"}

    def test_the_restore_puts_each_part_in_a_place_of_this_server(
        self, machine: Machine, runner: Runner
    ) -> None:
        targets = machine.manager(runner).restore_targets()

        assert targets["state"] == machine.home
        assert targets["system-state"] == machine.system
        assert targets["config"] == machine.config


class TestTheKeyTheSecretsAreSealedUnder:
    """It lives beside the store in the user's directory and in /etc/noust for the system's."""

    def test_the_manifest_names_it_where_the_stores_secrets_are_sealed(
        self, machine: Machine, runner: Runner
    ) -> None:
        made = machine.manager(runner).create(PASSPHRASE)

        assert made.manifest["secrets_key"] == {"path": "state/secrets.key"}

    def test_for_a_store_in_the_state_directory_it_is_the_one_in_the_configuration(
        self, machine: Machine, runner: Runner
    ) -> None:
        NoustStore.reset_instance()
        NoustStore(machine.system / "noust.db").close()
        NoustStore.reset_instance()
        NoustStore(machine.system / "noust.db")
        SecretStore(root=machine.system / "secrets").write_protected_json("a/b", {"x": "y"})

        made = machine.manager(runner).create(PASSPHRASE)

        assert made.manifest["secrets_key"] == {"path": "config/secrets.key"}
        assert "system-state" not in made.manifest["sources"]

    def test_a_restore_onto_a_server_that_keeps_its_key_elsewhere_says_what_cannot_be_opened(
        self, machine: Machine, runner: Runner
    ) -> None:
        made = machine.manager(runner).create(PASSPHRASE)
        # Another server, whose store is where a server keeps it: its key is in /etc/noust.
        fresh = SelfBackupManager(
            runner=runner,
            sources={"config": machine.config, "state": machine.system},
            letsencrypt=machine.letsencrypt,
            systemd_dir=machine.units,
            directory=machine.bundles,
            webservers={},
            database_service=lambda: NoRoles(),
        )

        report = SelfRestore(fresh, restart=False).restore(made.path.name, PASSPHRASE, force=True)

        notes = " ".join(report.notes)
        assert str(machine.system / "secrets.key") in notes
        assert str(machine.config / "secrets.key") in notes
        assert "enter them again" in notes

    def test_a_restore_onto_a_server_that_keeps_it_in_the_same_place_says_nothing_of_it(
        self, machine: Machine, runner: Runner
    ) -> None:
        manager = machine.manager(runner)
        made = manager.create(PASSPHRASE)

        report = SelfRestore(manager, restart=False).restore(made.path.name, PASSPHRASE, force=True)

        assert not [note for note in report.notes if "enter them again" in note]


class TestWhichFileIsTheStore:
    def test_the_manifest_names_it_and_an_older_bundle_is_read_by_its_first_noust_db(self) -> None:
        declared = {"store": {"path": "state/noust.db"}, "files": [{"path": "extra-1/noust.db"}]}
        old = {"files": [{"path": "state/noust.db"}, {"path": "extra-1/noust.db"}]}

        assert store_path_in(declared) == "state/noust.db"
        assert store_path_in(old) == "state/noust.db"
        assert store_path_in({"files": [{"path": "config/config.yaml"}]}) is None

    def test_a_bundle_that_would_hold_no_store_is_not_made(
        self, tmp_path: Path, runner: Runner
    ) -> None:
        state = tmp_path / "state-without-a-store"
        config = tmp_path / "config"
        state.mkdir()
        config.mkdir()
        (state / "noust.db").write_bytes(b"")
        manager = SelfBackupManager(
            runner=runner,
            sources={"config": config, "state": state},
            letsencrypt=tmp_path / "le",
            systemd_dir=tmp_path / "units",
            directory=tmp_path / "bundles",
            webservers={},
            database_service=lambda: NoRoles(),
        )

        with pytest.raises(BackupError) as refused:
            manager.create(PASSPHRASE)

        assert "no store" in str(refused.value).lower()
        assert "noust setup doctor" in (refused.value.details or "")
        assert not list((tmp_path / "bundles").glob("*.enc"))
