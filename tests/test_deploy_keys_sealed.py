# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Deploy keys on a central whose secrets are sealed (checklist A-02).

On a sealed store every file under ``secrets/`` is ciphertext, so ssh is handed
a decrypted private copy that must not outlive the git command, a pair is made
in a private memory-backed directory and sealed in at once, and a locked
central makes and uses nothing, and says how to unlock.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import sealing
from noust.core.runner import SubprocessRunner
from noust.core.sealing import SecretsLockedError
from noust.core.secrets import SecretStore
from noust.core.store import App, NoustStore
from noust.managers.source_keys import DeployKeyManager, secret_name
from tests.deploy_key_support import DeployKeyRunner, private_key_text

PASSPHRASE = "correct horse battery staple"
DOMAIN = "shop.example.com"
SSH_SOURCE = "git@github.com:acme/shop.git"


@pytest.fixture
def runtime_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The private directory decrypted copies are made in."""
    directory = tmp_path / "run"
    directory.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(directory))
    return directory


@pytest.fixture
def sealed(tmp_path: Path, runtime_dir: Path) -> Iterator[DeployKeyManager]:
    """A manager over a sealed, unlocked store; locked again afterwards."""
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "state" / "noust.db")
    store.create_app(App(domain=DOMAIN, app_type="nodejs", source=SSH_SOURCE, branch="main"))
    root = tmp_path / "state" / "secrets"
    root.mkdir(parents=True, mode=0o700)
    secrets = SecretStore(root=root, runner=SubprocessRunner())
    sealing.seal_store(root, PASSPHRASE, runner=SubprocessRunner())
    try:
        yield DeployKeyManager(store=store, secrets=secrets, runner=DeployKeyRunner())
    finally:
        sealing.lock(root)
        store.close()
        NoustStore.reset_instance()


@pytest.mark.allow_subprocess
class TestSealedKeys:
    """The pair is sealed at rest and never left in clear."""

    def test_a_generated_pair_is_sealed_at_rest(self, sealed: DeployKeyManager) -> None:
        key = sealed.generate(DOMAIN)

        for leaf in ("key", "key.pub"):
            on_disk = sealed.secrets.path(secret_name(key.name, leaf)).read_text()
            assert on_disk.startswith(sealing.SEALED_PREFIX)
        assert sealed.secrets.read(secret_name(key.name, "key.pub")).startswith("ssh-ed25519 ")  # type: ignore[union-attr]

    def test_keygen_writes_into_the_private_runtime_directory_and_leaves_nothing(
        self, sealed: DeployKeyManager, runtime_dir: Path
    ) -> None:
        sealed.generate(DOMAIN)

        (call,) = [c for c in sealed.runner.calls if c[0] == "ssh-keygen"]  # type: ignore[attr-defined]
        written = Path(call[call.index("-f") + 1])
        assert written.is_relative_to(runtime_dir)
        assert not written.exists()
        assert not Path(f"{written}.pub").exists()

    def test_a_locked_central_makes_nothing(self, sealed: DeployKeyManager) -> None:
        sealing.lock(sealed.secrets.root)
        with pytest.raises(SecretsLockedError):
            sealed.generate(DOMAIN)
        assert sealed.keys() == []

    def test_an_imported_key_is_derived_from_a_copy_that_does_not_stay(
        self, sealed: DeployKeyManager, runtime_dir: Path
    ) -> None:
        key = sealed.import_key(DOMAIN, private_key_text(4))

        assert (
            sealed.secrets.path(secret_name(key.name, "key"))
            .read_text()
            .startswith(sealing.SEALED_PREFIX)
        )
        assert [p for p in runtime_dir.rglob("*") if p.is_file()] == []


@pytest.mark.allow_subprocess
class TestSealedGit:
    """What ssh reads is a copy, and the copy goes."""

    def test_ssh_is_handed_a_decrypted_copy_that_close_removes(
        self, sealed: DeployKeyManager, runtime_dir: Path
    ) -> None:
        key = sealed.generate(DOMAIN)

        use = sealed.identities_for(SSH_SOURCE)
        assert use is not None
        try:
            (identity,) = use.identities
            path = Path(identity.command.split("-i ", 1)[1].split(" ", 1)[0])
            assert path.is_relative_to(runtime_dir)
            assert not path.is_relative_to(sealed.secrets.root)
            assert path.read_text().startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
        finally:
            use.close()
        assert [p for p in runtime_dir.rglob("*") if p.is_file()] == []
        assert (
            sealed.secrets.path(secret_name(key.name, "key"))
            .read_text()
            .startswith(sealing.SEALED_PREFIX)
        )

    def test_a_locked_central_uses_no_key_and_says_how_to_unlock(
        self, sealed: DeployKeyManager
    ) -> None:
        sealed.generate(DOMAIN)
        sealing.lock(sealed.secrets.root)
        with pytest.raises(SecretsLockedError) as caught:
            sealed.identities_for(SSH_SOURCE)
        assert "unlock" in caught.value.details.lower()

    def test_a_rotation_promotes_through_the_seal(
        self, sealed: DeployKeyManager, runtime_dir: Path
    ) -> None:
        old = sealed.generate(DOMAIN)
        waiting = sealed.rotate_start(DOMAIN)
        use = sealed.identities_for(SSH_SOURCE)
        assert use is not None
        try:
            use.succeeded(use.identities[0])
        finally:
            use.close()

        done = sealed.keystore.get(old.name)
        assert done is not None and done.pending is None
        assert waiting.pending is not None and done.public_key == waiting.pending.public_key
        for leaf in ("key", "key.pub"):
            assert (
                sealed.secrets.path(secret_name(old.name, leaf))
                .read_text()
                .startswith(sealing.SEALED_PREFIX)
            )
        assert not sealed.secrets.path(secret_name(old.name, "next_key")).exists()
