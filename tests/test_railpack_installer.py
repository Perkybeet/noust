# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust setup railpack``: a pinned release, downloaded, checked, installed.

The binary runs as root and builds untrusted repositories, so it is installed
only when its SHA-256 is the one pinned in the code for that release, and only
as the regular file the release is known to hold.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest

from noust.core.exceptions import DeploymentError, ValidationError
from noust.core.fs import DryRunFileSystem, RealFileSystem
from noust.core.runner import CommandResult, CommandRunner, FakeRunner
from noust.managers import railpack

BINARY = b"#!/bin/sh\necho railpack\n"


def make_archive(
    members: Sequence[tuple[str, bytes]] = (("railpack", BINARY),),
    *,
    links: Mapping[str, str] | None = None,
) -> bytes:
    """A gzip tar holding the given files, and symbolic links when asked."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in members:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o755
            archive.addfile(info, io.BytesIO(content))
        for name, target in (links or {}).items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            archive.addfile(info)
    return buffer.getvalue()


class DownloadingRunner(FakeRunner):
    """A FakeRunner whose curl leaves the given bytes at ``--output``, and whose tar unpacks."""

    def __init__(self, payload: bytes, version: Callable[[], str | None]):
        super().__init__()
        self.payload = payload
        self.version = version

    def run(self, argv: Sequence[str], **kwargs) -> CommandResult:
        result = super().run(argv, **kwargs)
        if argv[0] == "curl" and result.success:
            Path(argv[argv.index("--output") + 1]).write_bytes(self.payload)
        if argv[0] == "tar" and result.success:
            directory = Path(argv[argv.index("--directory") + 1])
            with tarfile.open(argv[argv.index("--file") + 1], "r:gz") as archive:
                member = archive.extractfile(argv[-1])
                assert member is not None
                (directory / argv[-1]).write_bytes(member.read())
        if argv[:2] == ["railpack", "--version"]:
            version = self.version()
            if version is None:
                return CommandResult(argv=tuple(argv), exit_code=127, stderr="not found")
            return CommandResult(
                argv=tuple(argv), exit_code=0, stdout=f"railpack version {version}"
            )
        return result


@pytest.fixture
def pinned(monkeypatch: pytest.MonkeyPatch) -> bytes:
    """The archive the test pins for x86_64."""
    payload = make_archive()
    monkeypatch.setitem(
        railpack.ASSETS,
        "x86_64",
        railpack.Asset(
            "railpack-v0.40.1-x86_64-unknown-linux-musl.tar.gz", hashlib.sha256(payload).hexdigest()
        ),
    )
    return payload


def installer(runner: CommandRunner, tmp_path: Path, fs=None) -> railpack.RailpackInstaller:
    return railpack.RailpackInstaller(
        runner=runner,
        fs=fs or RealFileSystem(),
        work_dir=tmp_path / "work",
        install_path=tmp_path / "bin" / "railpack",
    )


class TestThePin:
    def test_the_release_and_its_digests_are_pinned_for_both_architectures(self):
        assert railpack.VERSION == "0.40.1"
        assert set(railpack.ASSETS) == {"x86_64", "aarch64"}
        for asset in railpack.ASSETS.values():
            assert railpack.VERSION in asset.name
            assert len(asset.sha256) == 64 and set(asset.sha256) <= set("0123456789abcdef")

    def test_the_url_is_the_releases_own_over_https(self):
        asset = railpack.asset_for("x86_64")
        url = railpack.RELEASE_URL.format(version=railpack.VERSION, name=asset.name)

        assert url.startswith("https://github.com/railwayapp/railpack/releases/download/v0.40.1/")

    @pytest.mark.parametrize(
        ("machine", "expected"),
        [("x86_64", "x86_64"), ("amd64", "x86_64"), ("aarch64", "aarch64"), ("arm64", "aarch64")],
    )
    def test_the_architecture_names_are_understood(self, machine, expected):
        assert railpack.asset_for(machine) is railpack.ASSETS[expected]

    def test_an_architecture_without_a_build_is_said_with_what_to_do(self):
        with pytest.raises(DeploymentError) as caught:
            railpack.asset_for("riscv64")
        assert "riscv64" in caught.value.message
        assert "Dockerfile" in caught.value.details

    def test_the_frontend_image_is_named_after_the_version(self):
        assert (
            railpack.frontend_reference("0.40.1") == "ghcr.io/railwayapp/railpack-frontend:v0.40.1"
        )
        with pytest.raises(ValidationError):
            railpack.frontend_reference("latest")


class TestInstalling:
    def test_it_downloads_over_https_verifies_and_installs(self, tmp_path, pinned):
        target = tmp_path / "bin" / "railpack"
        # Once the binary is in place it answers; before, nothing does.
        runner = DownloadingRunner(pinned, lambda: railpack.VERSION if target.exists() else None)

        outcome = installer(runner, tmp_path).install(machine="x86_64")

        assert outcome == railpack.InstallOutcome(railpack.VERSION, target, changed=True)
        assert target.read_bytes() == BINARY
        assert target.stat().st_mode & 0o777 == 0o755
        assert not (tmp_path / "work").exists()

        curl = next(call for call in runner.calls if call[0] == "curl")
        assert "--proto" in curl and curl[curl.index("--proto") + 1] == "=https"
        assert "--fail" in curl
        assert curl[-1].startswith(
            "https://github.com/railwayapp/railpack/releases/download/v0.40.1/"
        )

    def test_a_download_that_is_not_the_pinned_release_is_never_installed(self, tmp_path, pinned):
        runner = DownloadingRunner(make_archive([("railpack", b"#!/bin/sh\nevil\n")]), lambda: None)

        with pytest.raises(DeploymentError) as caught:
            installer(runner, tmp_path).install(machine="x86_64")

        assert "checksum" in caught.value.message
        assert railpack.ASSETS["x86_64"].sha256 in caught.value.details
        assert not (tmp_path / "bin" / "railpack").exists()
        assert not (tmp_path / "work").exists()
        assert not any(call[0] == "tar" for call in runner.calls)

    def test_an_archive_without_the_binary_as_a_regular_file_is_refused(
        self, tmp_path, monkeypatch
    ):
        payload = make_archive([("README.md", b"x")], links={"railpack": "/etc/passwd"})
        monkeypatch.setitem(
            railpack.ASSETS,
            "x86_64",
            railpack.Asset("a.tar.gz", hashlib.sha256(payload).hexdigest()),
        )
        runner = DownloadingRunner(payload, lambda: None)

        with pytest.raises(DeploymentError) as caught:
            installer(runner, tmp_path).install(machine="x86_64")

        assert "regular file" in caught.value.message
        assert not any(call[0] == "tar" for call in runner.calls)
        assert not (tmp_path / "bin" / "railpack").exists()

    def test_only_the_binary_is_asked_of_tar_and_nothing_keeps_the_archives_owner(
        self, tmp_path, pinned
    ):
        runner = DownloadingRunner(pinned, lambda: railpack.VERSION)

        installer(runner, tmp_path).install(machine="x86_64", force=True)

        tar = next(call for call in runner.calls if call[0] == "tar")
        assert tar[-1] == "railpack"
        assert "--no-same-owner" in tar

    def test_the_same_version_is_left_alone(self, tmp_path, pinned):
        runner = DownloadingRunner(pinned, lambda: railpack.VERSION)

        outcome = installer(runner, tmp_path).install(machine="x86_64")

        assert outcome.changed is False
        assert not any(call[0] == "curl" for call in runner.calls)

    def test_force_installs_it_again(self, tmp_path, pinned):
        runner = DownloadingRunner(pinned, lambda: railpack.VERSION)

        outcome = installer(runner, tmp_path).install(machine="x86_64", force=True)

        assert outcome.changed is True
        assert any(call[0] == "curl" for call in runner.calls)

    def test_another_version_is_replaced(self, tmp_path, pinned):
        runner = DownloadingRunner(pinned, lambda: "0.39.0")
        seen = ["0.39.0"]
        runner.version = lambda: (
            seen[0] if not (tmp_path / "bin" / "railpack").exists() else railpack.VERSION
        )

        outcome = installer(runner, tmp_path).install(machine="x86_64")

        assert outcome.changed is True

    def test_a_failed_download_installs_nothing(self, tmp_path, pinned):
        runner = DownloadingRunner(pinned, lambda: None)
        runner.script(
            ["curl"], exit_code=22, stderr="curl: (22) The requested URL returned error: 404"
        )

        with pytest.raises(DeploymentError) as caught:
            installer(runner, tmp_path).install(machine="x86_64")

        assert "404" in caught.value.details
        assert not (tmp_path / "bin" / "railpack").exists()
        assert not (tmp_path / "work").exists()

    def test_missing_curl_is_said(self, tmp_path, pinned):
        runner = DownloadingRunner(pinned, lambda: None).only_knows("tar")

        with pytest.raises(DeploymentError) as caught:
            installer(runner, tmp_path).install(machine="x86_64")

        assert "curl" in caught.value.message

    def test_a_binary_that_reports_another_version_is_an_error(self, tmp_path, pinned):
        runner = DownloadingRunner(
            pinned, lambda: "0.1.0" if (tmp_path / "bin" / "railpack").exists() else None
        )

        with pytest.raises(DeploymentError) as caught:
            installer(runner, tmp_path).install(machine="x86_64")

        assert "0.1.0" in caught.value.message

    def test_a_rehearsal_downloads_and_installs_nothing(self, tmp_path, pinned):
        runner = FakeRunner()
        dry = DryRunFileSystem()

        from noust.core.fs import set_fs

        set_fs(dry)
        outcome = installer(runner, tmp_path, fs=dry).install(machine="x86_64")

        assert outcome.changed is True
        assert not (tmp_path / "bin").exists()
        assert not (tmp_path / "work").exists()


class TestTheVersionAsked:
    def test_it_reads_the_version_the_binary_prints(self):
        runner = FakeRunner().script(["railpack", "--version"], stdout="railpack version 0.40.1\n")

        assert railpack.installed_version(runner) == "0.40.1"

    def test_it_is_none_when_railpack_is_not_installed(self):
        assert railpack.installed_version(FakeRunner().only_knows("docker")) is None

    def test_it_is_none_when_the_binary_does_not_answer(self):
        runner = FakeRunner().script(["railpack", "--version"], exit_code=1, stderr="boom")

        assert railpack.installed_version(runner) is None
