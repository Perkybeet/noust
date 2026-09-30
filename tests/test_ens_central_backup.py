# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust central backup``: the central's store, secrets and keys, encrypted (ENS G12, op.cont.1).

Over real openssl: the backup holds every file of the data directories with
a SHA-256 manifest, SQLite databases as consistent snapshots, no symlink
followed; it is an envelope nobody opens without the passphrase; verifying
proves every file against the manifest; the backup is an audit event.
"""

from __future__ import annotations

import sqlite3
import stat
from pathlib import Path
from typing import Any

import pytest

from noust.central import backup as central_backup
from noust.core.ens import envelope
from noust.core.ens.envelope import EnvelopeError
from noust.core.runner import SubprocessRunner
from noust.managers.source_manager import extract_archive

PASSPHRASE = "correct horse battery"

pytestmark = pytest.mark.allow_subprocess


@pytest.fixture(autouse=True)
def cheap_scrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(envelope, "SCRYPT_N", 2**10)


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    recorded: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        central_backup, "record", lambda event, **kwargs: recorded.append((event, kwargs))
    )
    return recorded


@pytest.fixture
def data(tmp_path: Path) -> list[Path]:
    config = tmp_path / "etc-noust"
    state = tmp_path / "var-lib-noust"
    (config / "panel-tls").mkdir(parents=True)
    (config / "config.yaml").write_text("web:\n  port: 8443\n")
    (config / "panel-tls" / "panel.key").write_text("PRIVATE KEY")
    (state / "secrets" / "fleet" / "nodes" / "web-1").mkdir(parents=True)
    (state / "secrets" / "fleet" / "nodes" / "web-1" / "id_ed25519").write_text("NODE KEY")
    (state / "incidents" / "old").mkdir(parents=True)
    (state / "incidents" / "old" / "big.log").write_text("x" * 10)
    database = state / "noust.db"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE nodes (name TEXT)")
    connection.execute("INSERT INTO nodes VALUES ('web-1')")
    connection.commit()
    connection.close()
    (state / "outside-link").symlink_to("/etc/passwd")
    return [config, state]


def make(data: list[Path], tmp_path: Path) -> central_backup.CentralBackup:
    return central_backup.create_central_backup(
        PASSPHRASE,
        output_dir=tmp_path / "out",
        sources=data,
        runner=SubprocessRunner(),
    )


class TestTheBackup:
    def test_it_is_an_owner_only_envelope_with_a_manifest(self, data, tmp_path, events) -> None:
        made = make(data, tmp_path)

        assert made.path.parent == tmp_path / "out"
        assert stat.S_IMODE(made.path.stat().st_mode) == 0o600
        assert envelope.read_header(made.path)["label"] == central_backup.LABEL
        assert b"NODE KEY" not in made.path.read_bytes()
        paths = {entry["path"] for entry in made.manifest["files"]}
        assert "config/config.yaml" in paths
        assert "state/secrets/fleet/nodes/web-1/id_ed25519" in paths
        assert "state/noust.db" in paths
        assert not any(path.startswith("state/incidents") for path in paths)
        assert "state/outside-link" in {entry["path"] for entry in made.manifest["skipped"]}

    def test_it_is_on_record(self, data, tmp_path, events) -> None:
        made = make(data, tmp_path)

        name, kwargs = events[-1]
        assert name == "backups.create" and kwargs["target"] == "central"
        assert kwargs["details"]["sha256"] == made.sha256

    def test_verify_proves_every_file(self, data, tmp_path, events) -> None:
        made = make(data, tmp_path)

        report = central_backup.verify_central_backup(
            made.path, PASSPHRASE, runner=SubprocessRunner()
        )

        assert report["ok"] is True
        assert report["files"] == len(made.manifest["files"])
        assert events[-1][0] == "backups.verify"

    def test_decrypting_gives_the_archive_with_a_consistent_database(
        self, data, tmp_path, events
    ) -> None:
        made = make(data, tmp_path)
        archive = tmp_path / "restore.tar.gz"

        central_backup.decrypt_central_backup(
            made.path, PASSPHRASE, archive, runner=SubprocessRunner()
        )

        extracted = tmp_path / "extracted"
        extract_archive(archive, extracted, archive_format="tar.gz")
        root = extracted / central_backup.ROOT_NAME
        connection = sqlite3.connect(root / "state" / "noust.db")
        assert connection.execute("SELECT name FROM nodes").fetchall() == [("web-1",)]
        connection.close()
        assert (root / "config" / "panel-tls" / "panel.key").read_text() == "PRIVATE KEY"

    def test_a_wrong_passphrase_opens_nothing(self, data, tmp_path, events) -> None:
        made = make(data, tmp_path)

        with pytest.raises(EnvelopeError, match="passphrase"):
            central_backup.verify_central_backup(
                made.path, "another passphrase", runner=SubprocessRunner()
            )
