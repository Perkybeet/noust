"""
Sealed secrets: the central's secret files encrypted under a passphrase.

The properties that matter: a sealed file reveals nothing, a wrong passphrase
is told apart from a damaged file, a file that was altered or moved over
another secret is refused before openssl sees it, a locked process can
neither read a sealed secret nor write one in clear, and the key never
appears in an argv (which every local user can read in ``ps``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from noust.core import sealing
from noust.core.runner import FakeRunner, SubprocessRunner
from noust.core.sealing import (
    SealError,
    SecretsLockedError,
    WrongPassphraseError,
)
from noust.core.secrets import SecretStore

PASSPHRASE = "correct horse battery staple"
KEY_PEM = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\nBBBB\n-----END OPENSSH PRIVATE KEY-----\n"


@pytest.fixture
def root(tmp_path: Path):
    """The secrets directory, locked again after the test."""
    directory = tmp_path / "secrets"
    yield directory
    sealing.lock(directory)


@pytest.fixture
def store(root: Path) -> SecretStore:
    """A store over real openssl, with a node key and a token in it."""
    secrets = SecretStore(root=root, runner=SubprocessRunner())
    secrets.write("nodes/vps1/key", KEY_PEM)
    secrets.write("nodes/vps1/token", "noust_fleet_token")
    secrets.write_json("github/app", {"id": 7, "secret": "s3cret"})
    return secrets


def on_disk(root: Path, name: str) -> str:
    return (root / name).read_text(encoding="utf-8")


pytestmark_openssl = pytest.mark.allow_subprocess


@pytestmark_openssl
class TestRoundTrip:
    def test_seal_encrypts_every_file_and_reads_stay_the_same(
        self, store: SecretStore, root: Path
    ) -> None:
        report = sealing.seal_store(root, PASSPHRASE, runner=SubprocessRunner())

        assert sorted(report.rewritten) == ["github/app", "nodes/vps1/key", "nodes/vps1/token"]
        for name in report.rewritten:
            text = on_disk(root, name)
            assert text.startswith(sealing.SEALED_PREFIX)
            assert "noust_fleet_token" not in text
            assert "OPENSSH" not in text
            assert "s3cret" not in text
        # The process that sealed holds the key: callers see no difference,
        # byte for byte.
        assert store.read("nodes/vps1/key") == KEY_PEM
        assert store.read("nodes/vps1/token") == "noust_fleet_token"
        assert store.read_json("github/app") == {"id": 7, "secret": "s3cret"}
        assert (root / sealing.SEAL_FILE).stat().st_mode & 0o777 == 0o600

    def test_writes_while_unlocked_are_sealed(self, store: SecretStore, root: Path) -> None:
        sealing.seal_store(root, PASSPHRASE, runner=SubprocessRunner())
        store.write("nodes/vps2/token", "another")

        assert on_disk(root, "nodes/vps2/token").startswith(sealing.SEALED_PREFIX)
        assert store.read("nodes/vps2/token") == "another"

    def test_unseal_restores_the_files_in_clear(self, store: SecretStore, root: Path) -> None:
        sealing.seal_store(root, PASSPHRASE, runner=SubprocessRunner())
        report = sealing.unseal_store(root, PASSPHRASE, runner=SubprocessRunner())

        assert len(report.rewritten) == 3
        assert on_disk(root, "nodes/vps1/token") == "noust_fleet_token"
        assert on_disk(root, "nodes/vps1/key") == KEY_PEM
        assert not sealing.is_sealed(root)
        assert store.read("nodes/vps1/token") == "noust_fleet_token"


@pytestmark_openssl
class TestLocked:
    def test_a_locked_process_cannot_read(self, store: SecretStore, root: Path) -> None:
        sealing.seal_store(root, PASSPHRASE, runner=SubprocessRunner())
        sealing.lock(root)

        with pytest.raises(SecretsLockedError, match="sealed and locked"):
            store.read("nodes/vps1/token")
        # A secret never stored is still simply absent.
        assert store.read("nodes/nope/token") is None

    def test_a_locked_process_cannot_write_in_clear(self, store: SecretStore, root: Path) -> None:
        sealing.seal_store(root, PASSPHRASE, runner=SubprocessRunner())
        sealing.lock(root)

        with pytest.raises(SecretsLockedError):
            store.write("nodes/vps2/token", "would be in clear")
        assert not (root / "nodes/vps2/token").exists()

    def test_unlock_opens_it_again_and_tells_the_listeners(
        self, store: SecretStore, root: Path
    ) -> None:
        sealing.seal_store(root, PASSPHRASE, runner=SubprocessRunner())
        sealing.lock(root)
        told: list[bool] = []

        def listener() -> None:
            told.append(True)

        sealing.add_unlock_listener(listener)
        try:
            sealing.unlock(root, PASSPHRASE)
        finally:
            sealing.remove_unlock_listener(listener)

        assert told == [True]
        assert store.read("nodes/vps1/token") == "noust_fleet_token"

    def test_a_wrong_passphrase_is_refused_and_stays_locked(
        self, store: SecretStore, root: Path
    ) -> None:
        sealing.seal_store(root, PASSPHRASE, runner=SubprocessRunner())
        sealing.lock(root)

        with pytest.raises(WrongPassphraseError):
            sealing.unlock(root, "incorrect horse battery staple")
        assert not sealing.is_unlocked(root)
        with pytest.raises(WrongPassphraseError):
            sealing.unseal_store(root, "incorrect horse battery staple", runner=SubprocessRunner())
        assert on_disk(root, "nodes/vps1/token").startswith(sealing.SEALED_PREFIX)


@pytestmark_openssl
class TestTampering:
    def test_an_altered_ciphertext_fails_its_mac(self, store: SecretStore, root: Path) -> None:
        sealing.seal_store(root, PASSPHRASE, runner=SubprocessRunner())
        path = root / "nodes/vps1/token"
        prefix, salt, ciphertext, tag = path.read_text().rsplit(":", 3)
        flipped = ("B" if ciphertext[0] == "A" else "A") + ciphertext[1:]
        path.write_text(f"{prefix}:{salt}:{flipped}:{tag}")

        with pytest.raises(SealError, match="integrity"):
            store.read("nodes/vps1/token")

    def test_a_file_moved_over_another_secret_is_refused(
        self, store: SecretStore, root: Path
    ) -> None:
        sealing.seal_store(root, PASSPHRASE, runner=SubprocessRunner())
        (root / "nodes/vps1/token").write_text(on_disk(root, "github/app"))

        with pytest.raises(SealError, match="integrity"):
            store.read("nodes/vps1/token")

    def test_a_file_in_clear_in_a_sealed_store_is_refused(
        self, store: SecretStore, root: Path
    ) -> None:
        sealing.seal_store(root, PASSPHRASE, runner=SubprocessRunner())
        (root / "nodes/vps1/token").write_text("planted")

        with pytest.raises(SealError, match="in clear"):
            store.read("nodes/vps1/token")

    def test_a_sealed_file_without_its_header_is_refused(
        self, store: SecretStore, root: Path
    ) -> None:
        sealing.seal_store(root, PASSPHRASE, runner=SubprocessRunner())
        (root / sealing.SEAL_FILE).unlink()

        with pytest.raises(SealError, match="header is missing"):
            store.read("nodes/vps1/token")

    def test_an_altered_header_reads_as_a_wrong_passphrase(
        self, store: SecretStore, root: Path
    ) -> None:
        sealing.seal_store(root, PASSPHRASE, runner=SubprocessRunner())
        header = json.loads((root / sealing.SEAL_FILE).read_text())
        header["kdf"]["n"] = 2**14
        (root / sealing.SEAL_FILE).write_text(json.dumps(header))

        with pytest.raises(WrongPassphraseError):
            sealing.unlock(root, PASSPHRASE)


@pytestmark_openssl
class TestInterrupted:
    def test_a_half_sealed_store_reads_and_the_next_seal_finishes_it(
        self, store: SecretStore, root: Path
    ) -> None:
        sealing.seal_store(root, PASSPHRASE, runner=SubprocessRunner())
        # As if the run had stopped before this file and before the end.
        header = sealing.read_header(root)
        assert header is not None
        keys = sealing.derive_keys(PASSPHRASE, header.salt)
        sealing._write_header(
            root, header.with_state(sealing.STATE_SEALING).signed(keys), sealing.get_fs()
        )
        (root / "nodes/vps1/token").write_text("noust_fleet_token")

        assert store.read("nodes/vps1/token") == "noust_fleet_token"

        report = sealing.seal_store(root, PASSPHRASE, runner=SubprocessRunner())
        assert report.rewritten == ["nodes/vps1/token"]
        assert report.already == 2
        read_back = sealing.read_header(root)
        assert read_back is not None and read_back.state == sealing.STATE_SEALED

    def test_sealing_twice_is_refused(self, store: SecretStore, root: Path) -> None:
        sealing.seal_store(root, PASSPHRASE, runner=SubprocessRunner())
        with pytest.raises(SealError, match="already sealed"):
            sealing.seal_store(root, PASSPHRASE, runner=SubprocessRunner())

    def test_a_symlink_is_never_sealed_through(self, store: SecretStore, root: Path) -> None:
        target = root.parent / "elsewhere"
        target.write_text("not a secret")
        (root / "nodes" / "link").symlink_to(target)

        with pytest.raises(SealError, match="symlink"):
            sealing.seal_store(root, PASSPHRASE, runner=SubprocessRunner())
        assert target.read_text() == "not a secret"


class TestNoKeyMaterialInArgv:
    def test_openssl_gets_the_key_in_its_environment_only(self, root: Path) -> None:
        runner = FakeRunner()
        runner.script(["openssl", "enc", "-e"], stdout="Q0lQSEVSVEVYVA==\n")
        keys = sealing.derive_keys(PASSPHRASE, b"\x01" * sealing.SALT_BYTES)

        sealed = sealing.seal_value("nodes/vps1/token", "the token", keys, runner)

        forbidden = (PASSPHRASE, keys.enc.hex(), keys.mac.hex(), "the token")
        for argv in runner.calls:
            for argument in argv:
                assert not any(secret in argument for secret in forbidden), argv
        assert runner.calls[0][:3] == ("openssl", "enc", "-e")
        assert "env:NOUST_SEAL_KEY" in runner.calls[0]
        assert runner.envs[0] == {"NOUST_SEAL_KEY": keys.enc.hex()}
        # The plaintext travels on stdin, base64-encoded.
        assert runner.inputs[0] is not None and "the token" not in runner.inputs[0]
        assert sealed.startswith(sealing.SEALED_PREFIX)

    def test_decryption_too(self, root: Path) -> None:
        keys = sealing.derive_keys(PASSPHRASE, b"\x02" * sealing.SALT_BYTES)
        encrypting = FakeRunner().script(["openssl", "enc", "-e"], stdout="Q0lQSEVSVEVYVA==")
        sealed = sealing.seal_value("a/b", "x", keys, encrypting)
        runner = FakeRunner().script(["openssl", "enc", "-d"], stdout="eA==\n")

        assert sealing.unseal_value("a/b", sealed, keys, runner) == "x"
        for argv in runner.calls:
            assert keys.enc.hex() not in " ".join(argv)
            assert keys.mac.hex() not in " ".join(argv)
        assert runner.envs[0] == {"NOUST_SEAL_KEY": keys.enc.hex()}

    def test_the_mac_is_checked_before_openssl_runs(self, root: Path) -> None:
        keys = sealing.derive_keys(PASSPHRASE, b"\x03" * sealing.SALT_BYTES)
        sealed = sealing.seal_value(
            "a/b", "x", keys, FakeRunner().script(["openssl", "enc"], stdout="Q0lQSEVS")
        )
        runner = FakeRunner()

        with pytest.raises(SealError, match="integrity"):
            sealing.unseal_value("a/c", sealed, keys, runner)
        assert runner.calls == []

    def test_keys_are_not_in_their_repr(self) -> None:
        keys = sealing.derive_keys(PASSPHRASE, b"\x04" * sealing.SALT_BYTES)
        assert keys.enc.hex() not in repr(keys)
        assert keys.mac.hex() not in repr(keys)


class TestPassphrase:
    def test_a_short_passphrase_is_refused(self, root: Path) -> None:
        with pytest.raises(SealError, match="at least"):
            sealing.seal_store(root, "short", runner=FakeRunner())
        assert not sealing.is_sealed(root)

    def test_the_kdf_is_scrypt_with_the_documented_parameters(self, root: Path) -> None:
        import hashlib

        salt = b"\x05" * 32
        keys = sealing.derive_keys(PASSPHRASE, salt)
        expected = hashlib.scrypt(
            PASSPHRASE.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=2**26, dklen=64
        )
        assert keys.enc + keys.mac == expected

    def test_an_unsealed_store_has_nothing_to_unlock(self, root: Path) -> None:
        with pytest.raises(SealError, match="not sealed"):
            sealing.unlock(root, PASSPHRASE)


@pytestmark_openssl
class TestUsablePath:
    """ssh takes a path; on a sealed store it gets a private decrypted copy."""

    @pytest.fixture(autouse=True)
    def runtime_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        directory = tmp_path / "run"
        directory.mkdir(mode=0o700)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(directory))
        return directory

    def test_unsealed_it_is_the_file_itself(self, store: SecretStore, root: Path) -> None:
        assert store.usable_path("nodes/vps1/key") == root / "nodes/vps1/key"

    def test_sealed_it_is_a_private_copy_removed_on_lock(
        self, store: SecretStore, root: Path, runtime_dir: Path
    ) -> None:
        sealing.seal_store(root, PASSPHRASE, runner=SubprocessRunner())

        copy = store.usable_path("nodes/vps1/key")

        assert copy.is_relative_to(runtime_dir)
        assert copy.read_text() == KEY_PEM
        assert copy.stat().st_mode & 0o777 == 0o600
        assert copy.parent.stat().st_mode & 0o777 == 0o700
        sealing.lock(root)
        assert not copy.exists()

    def test_locked_there_is_no_copy_to_give(self, store: SecretStore, root: Path) -> None:
        sealing.seal_store(root, PASSPHRASE, runner=SubprocessRunner())
        sealing.lock(root)
        with pytest.raises(SecretsLockedError):
            store.usable_path("nodes/vps1/key")

    def test_a_planted_directory_is_refused(
        self, store: SecretStore, root: Path, runtime_dir: Path
    ) -> None:
        sealing.seal_store(root, PASSPHRASE, runner=SubprocessRunner())
        planted = sealing.plaintext_copy_dir(root)
        planted.parent.mkdir(mode=0o755)

        with pytest.raises(SealError, match="Refusing"):
            store.usable_path("nodes/vps1/key")
