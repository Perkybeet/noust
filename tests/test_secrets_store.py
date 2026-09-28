# SPDX-License-Identifier: AGPL-3.0-or-later
"""WASM's own secret files: 0600, namespaced, never through a symlink."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from wasm.core.exceptions import ConfigError
from wasm.core.fs import DryRunFileSystem
from wasm.core.secrets import SecretStore, secrets_dir
from wasm.core.store import WASMStore


def test_written_secret_is_private_and_reads_back(tmp_path: Path) -> None:
    store = SecretStore(tmp_path / "secrets")
    store.write("github/private-key.pem", "-----BEGIN KEY-----")

    path = tmp_path / "secrets" / "github" / "private-key.pem"
    assert store.read("github/private-key.pem") == "-----BEGIN KEY-----"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_missing_secret_reads_as_none(tmp_path: Path) -> None:
    assert SecretStore(tmp_path).read("nothing/here") is None
    assert SecretStore(tmp_path).read_json("nothing/here") == {}


@pytest.mark.parametrize(
    "name", ["", "../etc/passwd", "a/../b", "/abs", ".hidden", "a//b", "a/.git", "sp ace"]
)
def test_path_like_names_are_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(ConfigError):
        SecretStore(tmp_path).write(name, "x")


def test_a_symlink_is_refused_not_followed(tmp_path: Path) -> None:
    target = tmp_path / "shadow"
    target.write_text("root:hash")
    (tmp_path / "ns").mkdir()
    os.symlink(target, tmp_path / "ns" / "key")

    with pytest.raises(ConfigError):
        SecretStore(tmp_path).read("ns/key")


def test_json_round_trip_and_delete(tmp_path: Path) -> None:
    store = SecretStore(tmp_path)
    store.write_json("backup-destinations/nas", {"pass": "s3cret", "user": "u"})
    assert store.read_json("backup-destinations/nas") == {"pass": "s3cret", "user": "u"}

    store.delete("backup-destinations/nas")
    store.delete("backup-destinations/nas")
    assert store.read("backup-destinations/nas") is None


def test_delete_namespace_removes_every_secret_under_it(tmp_path: Path) -> None:
    store = SecretStore(tmp_path)
    store.write("github/a", "1")
    store.write("github/b", "2")
    store.write("other/c", "3")

    store.delete_namespace("github")

    assert store.read("github/a") is None
    assert store.read("other/c") == "3"


def test_damaged_json_is_an_error(tmp_path: Path) -> None:
    store = SecretStore(tmp_path)
    store.write("ns/key", "not json")
    with pytest.raises(ConfigError):
        store.read_json("ns/key")


def test_rehearsal_writes_nothing(tmp_path: Path) -> None:
    SecretStore(tmp_path / "secrets", fs=DryRunFileSystem()).write("ns/key", "x")
    assert not (tmp_path / "secrets").exists()


def test_default_directory_sits_beside_the_store(tmp_path: Path) -> None:
    WASMStore.reset_instance()
    try:
        store = WASMStore(tmp_path / "state" / "wasm.db")
        assert secrets_dir() == store.db_path.parent / "secrets"
    finally:
        WASMStore.reset_instance()
