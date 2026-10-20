# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A backup destination names a host the machine will connect to as root with the
backups' credentials, so the host is judged where destinations are made and
where they are used (checklist L-21; :mod:`noust.core.outbound`).

The rule has two moments. Configuring a destination refuses a host inside the
machine's own networks unless the operator names it in
``security.allow_private_hosts``. Using it again, which is every scheduled
backup, refuses only the addresses that can never be a destination, so the NAS
a server has backed up to for a year keeps being backed up to the night after an
upgrade.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.config import Config
from noust.core.exceptions import BackupError
from noust.core.runner import FakeRunner
from noust.core.store import BackupDestinationRecord, NoustStore, get_store
from noust.managers.backup_destinations import BackupDestinationManager, destination_host


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def config(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Config]:
    monkeypatch.setattr(
        "noust.core.config.DEFAULT_CONFIG_PATH", sandbox / "etc" / "noust" / "config.yaml"
    )
    Config.reset_instance()
    try:
        yield Config()
    finally:
        Config.reset_instance()


@pytest.fixture
def manager(runner: FakeRunner) -> BackupDestinationManager:
    runner.only_knows("rclone")
    return BackupDestinationManager(runner=runner)


def sftp(host: str) -> dict[str, str]:
    return {"host": host, "user": "noust", "pass": "a-long-password-value"}


def existing(name: str, backend: str, **settings: str) -> None:
    """A destination as an earlier version stored it, without being asked."""
    get_store().save_backup_destination(
        BackupDestinationRecord(
            name=name, backend=backend, settings={"path": "noust-backups", **settings}
        )
    )


class TestWhichHostADestinationNames:
    @pytest.mark.parametrize(
        ("backend", "settings", "host"),
        [
            ("sftp", {"host": "nas.lan"}, "nas.lan"),
            ("smb", {"host": "192.168.1.5"}, "192.168.1.5"),
            (
                "webdav",
                {"url": "https://cloud.example.com/remote.php/webdav/"},
                "cloud.example.com",
            ),
            ("s3", {"endpoint": "https://s3.example.com:9000/bucket"}, "s3.example.com"),
            ("s3", {"endpoint": "minio.private.test:9000"}, "minio.private.test"),
            ("s3", {"endpoint": "http://[fd00::1]:9000"}, "fd00::1"),
            ("s3", {"endpoint": "  "}, None),
            ("s3", {}, None),
            ("b2", {"account": "a"}, None),
            ("drive", {}, None),
            ("local", {}, None),
        ],
    )
    def test_each_backend_names_its_host_in_its_own_field(
        self, backend: str, settings: dict[str, str], host: str | None
    ) -> None:
        assert destination_host(backend, settings) == host


class TestConfiguringADestination:
    def test_a_host_on_the_machines_own_network_is_refused(
        self, manager: BackupDestinationManager
    ) -> None:
        with pytest.raises(BackupError) as raised:
            manager.add("nas", "sftp", sftp("nas.private.test"))

        assert "nas.private.test" in str(raised.value)
        assert "security.allow_private_hosts" in str(raised.value.details)
        assert get_store().get_backup_destination("nas") is None

    def test_nothing_is_stored_when_it_is_refused(self, manager: BackupDestinationManager) -> None:
        from noust.core.secrets import SecretStore

        with pytest.raises(BackupError):
            manager.add("nas", "sftp", sftp("nas.private.test"))

        assert SecretStore().read_protected_json("backup-destinations/nas") == {}

    def test_the_metadata_service_is_refused_even_as_an_address_literal(
        self, manager: BackupDestinationManager
    ) -> None:
        with pytest.raises(BackupError, match=re.escape("169.254.169.254")):
            manager.add("meta", "sftp", sftp("169.254.169.254"))

    def test_the_operator_can_name_a_host_on_their_own_network(
        self, manager: BackupDestinationManager, config: Config
    ) -> None:
        config.set("security.allow_private_hosts", ["nas.private.test"])

        destination = manager.add("nas", "sftp", sftp("nas.private.test"))

        assert destination.settings["host"] == "nas.private.test"

    def test_an_s3_endpoint_is_judged_like_any_other_host(
        self, manager: BackupDestinationManager
    ) -> None:
        fields = {
            "provider": "Minio",
            "access_key_id": "key",
            "secret_access_key": "a-long-secret-value",
            "endpoint": "http://minio.private.test:9000",
            "path": "backups/noust",
        }

        with pytest.raises(BackupError, match=re.escape("minio.private.test")):
            manager.add("minio", "s3", fields)

    def test_a_provider_with_no_host_to_name_has_nothing_to_judge(
        self, manager: BackupDestinationManager
    ) -> None:
        fields = {
            "provider": "AWS",
            "access_key_id": "key",
            "secret_access_key": "a-long-secret",
            "path": "backups/noust",
        }

        assert manager.add("aws", "s3", fields).backend == "s3"

    def test_a_name_that_does_not_resolve_yet_is_accepted(
        self, manager: BackupDestinationManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A destination can be set up while it is down; it is judged again when used.
        def down(host: str) -> tuple[str, ...]:
            raise OSError("Name or service not known")

        monkeypatch.setattr("noust.core.outbound.resolve_host", down)

        assert manager.add("later", "sftp", sftp("backup.example.com")).name == "later"

    def test_changing_the_host_to_an_internal_one_is_refused(
        self, manager: BackupDestinationManager
    ) -> None:
        manager.add("nas", "sftp", sftp("backup.example.com"))

        with pytest.raises(BackupError, match=re.escape("nas.private.test")):
            manager.update("nas", {"host": "nas.private.test"})

        stored = get_store().get_backup_destination("nas")
        assert stored is not None and stored.settings["host"] == "backup.example.com"

    def test_changing_something_else_does_not_reopen_a_hosts_judgement(
        self, manager: BackupDestinationManager
    ) -> None:
        existing("nas", "sftp", host="nas.private.test", user="noust")

        updated = manager.update("nas", {"path": "elsewhere"})

        assert updated.settings["host"] == "nas.private.test"


class TestUsingADestination:
    def test_a_destination_on_a_lan_made_before_keeps_working(
        self, manager: BackupDestinationManager
    ) -> None:
        existing("nas", "sftp", host="nas.private.test", user="noust")

        env = manager.remote_env("nas")

        assert env["RCLONE_CONFIG_NAS_HOST"] == "nas.private.test"

    def test_a_name_that_now_resolves_to_the_metadata_service_stops_the_backup(
        self, manager: BackupDestinationManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        existing("nas", "sftp", host="backup.example.com", user="noust")
        monkeypatch.setattr("noust.core.outbound.resolve_host", lambda host: ("169.254.169.254",))

        with pytest.raises(BackupError, match=re.escape("169.254.169.254")):
            manager.remote_env("nas")

    def test_the_test_button_judges_the_host_in_full_before_rclone_runs(
        self, manager: BackupDestinationManager, runner: FakeRunner
    ) -> None:
        existing("nas", "sftp", host="nas.private.test", user="noust")

        with pytest.raises(BackupError, match=re.escape("security.allow_private_hosts")):
            manager.test("nas")

        assert not [call for call in runner.calls if call[:1] == ("rclone",)]

    def test_a_trusted_host_passes_the_test(
        self, manager: BackupDestinationManager, runner: FakeRunner, config: Config
    ) -> None:
        existing("nas", "sftp", host="nas.private.test", user="noust")
        config.set("security.allow_private_hosts", ["nas.private.test"])
        runner.script(("rclone", "mkdir"))
        runner.script(("rclone", "lsf"))

        assert manager.test("nas")["ok"] is True
