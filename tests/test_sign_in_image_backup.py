# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The picture of the sign-in screen travels in the backup of Noust's own state (item 129).

No list of files names it: ``noust self-backup`` and ``noust central backup`` take every file of the
configuration and state directories, and the picture is kept in the state directory. What is pinned
here is the consequence an operator relies on: after losing the server, the one restored shows the
same sign-in screen, because the bundle carried the picture and its record and the restore put both
back where the console reads them, byte for byte.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from noust.central import backup as central_backup
from noust.core.ens import envelope
from noust.core.runner import SubprocessRunner
from noust.core.sign_in_image import RECORD_NAME, SignInImage
from noust.managers.self_restore import SelfRestore
from tests.support import sign_in_images as make
from tests.support.self_backup import (  # noqa: F401
    PASSPHRASE,
    FakeTimers,
    Runner,
    Server,
    _reset_store,
    audited,
    cheap_scrypt,
    old,
    origin,
    runner,
    server,
)

# A fixture imported from the support module is found by pytest in this module's namespace, and a
# test that asks for it by name looks like a redefinition of an import that is never used.
# ruff: noqa: F811

pytestmark = pytest.mark.allow_subprocess

#: Where the picture lives under a server's state directory.
FOLDER = "sign-in-image"


class TestTheBundleOfNoustsOwnState:
    def test_the_picture_and_its_record_are_in_the_bundle_and_come_back_byte_for_byte(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        chosen = SignInImage(old.state / FOLDER).set(make.png(300, 100), fit="contain", alt="Acme")
        made = old.manager(runner).create(PASSPHRASE)
        in_bundle = {entry["path"] for entry in made.manifest["files"]}
        assert f"state/{FOLDER}/{RECORD_NAME}" in in_bundle
        assert f"state/{FOLDER}/{chosen.sha256}.png" in in_bundle

        new = server(tmp_path, "new")
        SelfRestore(new.manager(runner), restart=True).restore(str(made.path), PASSPHRASE)

        restored = SignInImage(new.state / FOLDER)
        assert restored.get() == chosen
        original = (old.state / FOLDER / f"{chosen.sha256}.png").read_bytes()
        assert restored.read(chosen.sha256) == (original, "image/png")

    def test_a_server_that_never_chose_one_restores_without_one(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        made = old.manager(runner).create(PASSPHRASE)
        new = server(tmp_path, "new")

        SelfRestore(new.manager(runner), restart=True).restore(str(made.path), PASSPHRASE)

        assert SignInImage(new.state / FOLDER).get() is None

    def test_what_a_restore_replaces_is_not_left_beside_the_picture_it_brings(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        # The record names one digest; the picture a server had of its own before is not served.
        SignInImage(old.state / FOLDER).set(make.png(40, 40), fit="cover", alt="")
        made = old.manager(runner).create(PASSPHRASE)
        new = server(tmp_path, "new")
        theirs = SignInImage(new.state / FOLDER).set(make.jpeg(20, 20), fit="cover", alt="")

        SelfRestore(new.manager(runner), restart=True).restore(str(made.path), PASSPHRASE)

        found = SignInImage(new.state / FOLDER).get()
        assert found is not None and found.sha256 != theirs.sha256
        assert SignInImage(new.state / FOLDER).read(theirs.sha256) is None


class TestTheCentralsBackup:
    @pytest.fixture(autouse=True)
    def cheap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(envelope, "SCRYPT_N", 2**10)

    def test_the_picture_is_in_it(self, tmp_path: Path, audited: list[dict[str, Any]]) -> None:
        config = tmp_path / "etc-noust"
        state = tmp_path / "var-lib-noust"
        config.mkdir(parents=True)
        state.mkdir(parents=True)
        (config / "config.yaml").write_text("web:\n  port: 8443\n")
        connection = sqlite3.connect(state / "noust.db")
        connection.execute("CREATE TABLE t (x)")
        connection.commit()
        connection.close()
        chosen = SignInImage(state / FOLDER).set(make.webp(64, 48), fit="cover", alt="")

        made = central_backup.create_central_backup(
            PASSPHRASE,
            output_dir=tmp_path / "out",
            sources=[config, state],
            runner=SubprocessRunner(),
        )

        in_bundle = {entry["path"] for entry in made.manifest["files"]}
        assert f"state/{FOLDER}/{RECORD_NAME}" in in_bundle
        assert f"state/{FOLDER}/{chosen.sha256}.webp" in in_bundle
