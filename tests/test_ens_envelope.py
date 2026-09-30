# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
File envelopes: whole files encrypted and authenticated with the sealing construction.

Over real openssl (the construction is openssl's AES-256-CBC with an
HMAC-SHA256 over everything, encrypt-then-MAC): a file round-trips, a wrong
passphrase and a changed byte are refused before openssl decrypts anything,
the passphrase never reaches an argv, and the output is owner-only.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from noust.core.ens import envelope
from noust.core.ens.envelope import (
    EnvelopeError,
    open_envelope,
    read_header,
    seal_file,
    verify_envelope,
)
from noust.core.runner import FakeRunner, SubprocessRunner

PASSPHRASE = "correct horse battery"

pytestmark = pytest.mark.allow_subprocess


@pytest.fixture(autouse=True)
def cheap_scrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(envelope, "SCRYPT_N", 2**10)


@pytest.fixture
def plain(tmp_path: Path) -> Path:
    path = tmp_path / "data.bin"
    path.write_bytes(os.urandom(300_000) + b"the end")
    return path


class TestTheRoundTrip:
    def test_a_file_comes_back_as_it_was(self, plain: Path, tmp_path: Path) -> None:
        sealed = tmp_path / "data.bin.enc"
        opened = tmp_path / "opened.bin"

        info = seal_file(plain, sealed, PASSPHRASE, runner=SubprocessRunner(), label="test")
        open_envelope(sealed, opened, PASSPHRASE, runner=SubprocessRunner())

        assert opened.read_bytes() == plain.read_bytes()
        assert stat.S_IMODE(sealed.stat().st_mode) == 0o600
        assert stat.S_IMODE(opened.stat().st_mode) == 0o600
        assert info.size == sealed.stat().st_size and len(info.sha256) == 64
        assert read_header(sealed)["label"] == "test"

    def test_verification_needs_no_decryption(self, plain: Path, tmp_path: Path) -> None:
        sealed = tmp_path / "data.bin.enc"
        seal_file(plain, sealed, PASSPHRASE, runner=SubprocessRunner())

        verify_envelope(sealed, PASSPHRASE)

    def test_the_plaintext_is_not_in_the_envelope(self, tmp_path: Path) -> None:
        plain = tmp_path / "secret.txt"
        plain.write_text("TOPSECRET-VALUE " * 100)
        sealed = tmp_path / "secret.enc"

        seal_file(plain, sealed, PASSPHRASE, runner=SubprocessRunner())

        assert b"TOPSECRET" not in sealed.read_bytes()


class TestRefusals:
    def test_a_wrong_passphrase_is_refused(self, plain: Path, tmp_path: Path) -> None:
        sealed = tmp_path / "data.bin.enc"
        seal_file(plain, sealed, PASSPHRASE, runner=SubprocessRunner())

        with pytest.raises(EnvelopeError, match="passphrase"):
            open_envelope(sealed, tmp_path / "out", "not the passphrase", runner=SubprocessRunner())
        assert not (tmp_path / "out").exists()

    def test_a_changed_byte_is_refused_before_openssl_runs(
        self, plain: Path, tmp_path: Path
    ) -> None:
        sealed = tmp_path / "data.bin.enc"
        seal_file(plain, sealed, PASSPHRASE, runner=SubprocessRunner())
        data = bytearray(sealed.read_bytes())
        data[-10] ^= 0x01
        sealed.write_bytes(bytes(data))
        runner = FakeRunner()

        with pytest.raises(EnvelopeError, match="integrity"):
            open_envelope(sealed, tmp_path / "out", PASSPHRASE, runner=runner)
        assert runner.calls == []

    def test_a_file_that_is_not_an_envelope_is_refused(self, plain: Path) -> None:
        with pytest.raises(EnvelopeError, match="not a Noust envelope"):
            read_header(plain)

    def test_a_short_passphrase_is_refused(self, plain: Path, tmp_path: Path) -> None:
        with pytest.raises(EnvelopeError):
            seal_file(plain, tmp_path / "x.enc", "short", runner=FakeRunner())


class TestTheKeyTravelsInTheEnvironment:
    def test_openssl_gets_the_key_in_its_environment_only(
        self, plain: Path, tmp_path: Path
    ) -> None:
        runner = FakeRunner()
        with pytest.raises(EnvelopeError):
            # The fake writes no ciphertext, which the envelope refuses.
            seal_file(plain, tmp_path / "x.enc", PASSPHRASE, runner=runner)

        argv = runner.calls[0]
        assert argv[:3] == ("openssl", "enc", "-e")
        assert PASSPHRASE not in " ".join(argv)
        env = runner.envs[0] or {}
        assert list(env) == [envelope.KEY_ENV]
        assert PASSPHRASE not in env[envelope.KEY_ENV]
