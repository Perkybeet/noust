# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Installing ``cloudflared`` from Noust: Cloudflare's own repository, its pinned key, then the package.

A tunnel's connector is the one program Noust did not install for the operator: the page said
"install it from Cloudflare's package repository" and left the steps to a person. Noust now adds
the repository the way it adds the ones of the database engines (the key is downloaded, read with a
throwaway gpg home and refused unless it is exactly the pinned primary key; only then is the source
line written) and installs the package through the platform's own package manager.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli
from noust.core.exceptions import DependencyError, PrerequisiteError
from noust.core.fs import DryRunFileSystem, get_fs, set_fs
from noust.core.package_family import PackageFamily
from noust.core.runner import DryRunRunner, FakeRunner, set_runner
from noust.managers import cloudflared_install
from noust.managers.cloudflared_install import (
    CLOUDFLARE_KEY,
    CloudflaredInstaller,
    repository_for,
)
from noust.managers.database import flavours
from noust.managers.server.host import HostPaths, OsRelease, Platform
from tests.support.database_flavours import colons

PINNED = "CC94B39C77AE7342A68B89628A682D308D4E5E73"
PATH = "/usr/bin/cloudflared"


def platform(family: PackageFamily, *, transactional: bool = False) -> Platform:
    ids = {
        PackageFamily.APT: OsRelease(id="debian", pretty_name="Debian GNU/Linux 12"),
        PackageFamily.DNF: OsRelease(id="rocky", id_like=("rhel",), pretty_name="Rocky Linux 9"),
        PackageFamily.ZYPPER: OsRelease(id="opensuse-leap", pretty_name="openSUSE Leap 15.6"),
    }
    return Platform(
        family=family,
        program={"apt": "apt-get", "dnf": "dnf", "zypper": "zypper"}.get(family.value, ""),
        transactional=transactional,
        os=ids.get(family, OsRelease()),
    )


class Which:
    """``which`` that finds the connector only after the package manager installed it."""

    def __init__(self, runner: FakeRunner) -> None:
        self.runner = runner

    def __call__(self, program: str) -> str | None:
        installed = any(
            call[-1] == "cloudflared" and "install" in call for call in self.runner.calls
        )
        return PATH if program == "cloudflared" and installed else None


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for directory in (
        "etc/apt/sources.list.d",
        "usr/share/keyrings",
        "etc/yum.repos.d",
        "etc/zypp/repos.d",
        "etc/pki/rpm-gpg",
    ):
        (tmp_path / directory).mkdir(parents=True)
    monkeypatch.setattr(flavours, "HOST", HostPaths(tmp_path))
    return tmp_path


def installer(
    runner: FakeRunner, family: PackageFamily = PackageFamily.APT, **overrides: Any
) -> CloudflaredInstaller:
    values: dict[str, Any] = {"platform": platform(family), "which": Which(runner)}
    values.update(overrides)
    return CloudflaredInstaller(runner=runner, fs=get_fs(), **values)


def serve_the_pinned_key(runner: FakeRunner) -> None:
    runner.script(["curl"], stdout="key")
    runner.script(["gpg"], stdout=colons(PINNED))


def test_the_key_is_the_one_cloudflare_signs_its_packages_with() -> None:
    assert CLOUDFLARE_KEY.fingerprint == PINNED
    assert CLOUDFLARE_KEY.url == "https://pkg.cloudflare.com/cloudflare-main.gpg"


# -- Debian and Ubuntu ---------------------------------------------------------------------


def test_on_apt_it_adds_the_repository_with_the_verified_key_then_installs(
    host: Path, runner: FakeRunner
) -> None:
    serve_the_pinned_key(runner)

    done = installer(runner).install()

    assert (done.path, done.installed_now) == (PATH, True)
    source = (host / "etc/apt/sources.list.d/noust-cloudflared.list").read_text()
    assert source == (
        "deb [signed-by=/usr/share/keyrings/noust-cloudflared.gpg] "
        "https://pkg.cloudflare.com/cloudflared any main\n"
    )
    argvs = [call for call in runner.calls if call[0] in ("curl", "apt-get")]
    assert argvs[0][-1] == "https://pkg.cloudflare.com/cloudflare-main.gpg"
    assert [call[:2] for call in argvs[1:]] == [("apt-get", "update"), ("apt-get", "install")]
    assert argvs[-1] == ("apt-get", "install", "-y", "cloudflared")


def test_a_server_that_has_it_is_left_alone(host: Path, runner: FakeRunner) -> None:
    done = installer(runner, which=lambda program: PATH).install()

    assert (done.path, done.installed_now) == (PATH, False)
    # It asks the program its version and nothing else: no key, no repository, no package manager.
    assert runner.calls == [(PATH, "--version")]


def test_a_key_that_is_not_the_pinned_one_adds_nothing(host: Path, runner: FakeRunner) -> None:
    runner.script(["curl"], stdout="key")
    runner.script(["gpg"], stdout=colons("A" * 40))

    with pytest.raises(DependencyError, match="not the one Noust trusts"):
        installer(runner).install()

    assert not [call for call in runner.calls if call[0] == "apt-get"]
    assert not (host / "etc/apt/sources.list.d/noust-cloudflared.list").exists()


def test_a_repository_apt_cannot_read_is_taken_back_out(host: Path, runner: FakeRunner) -> None:
    serve_the_pinned_key(runner)
    runner.script(["apt-get", "update"], exit_code=100, stderr="E: The repository is not signed")

    with pytest.raises(DependencyError) as failed:
        installer(runner).install()

    assert "not signed" in (failed.value.details or "")
    assert not (host / "etc/apt/sources.list.d/noust-cloudflared.list").exists()
    assert not [call for call in runner.calls if call[:2] == ("apt-get", "install")]


def test_a_package_that_does_not_install_says_what_apt_said(host: Path, runner: FakeRunner) -> None:
    serve_the_pinned_key(runner)
    runner.script(["apt-get", "install"], exit_code=100, stderr="E: Unable to locate package")

    with pytest.raises(DependencyError) as failed:
        installer(runner, which=lambda program: None).install()

    assert "Unable to locate package" in (failed.value.details or "")


def test_a_repository_the_operator_already_configured_is_used_as_it_is(
    host: Path, runner: FakeRunner
) -> None:
    (host / "etc/apt/sources.list.d/cloudflared.list").write_text(
        "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] "
        "https://pkg.cloudflare.com/cloudflared any main\n"
    )

    installer(runner).install()

    assert not [call for call in runner.calls if call[0] in ("curl", "gpg")]
    assert not (host / "etc/apt/sources.list.d/noust-cloudflared.list").exists()
    assert ("apt-get", "install", "-y", "cloudflared") in runner.calls


# -- The RPM families ----------------------------------------------------------------------


def test_on_dnf_it_writes_a_repository_file_trusting_only_the_verified_key(
    host: Path, runner: FakeRunner
) -> None:
    serve_the_pinned_key(runner)

    done = installer(runner, PackageFamily.DNF).install()

    assert done.installed_now is True
    definition = (host / "etc/yum.repos.d/noust-cloudflared.repo").read_text()
    assert "baseurl=https://pkg.cloudflare.com/cloudflared/rpm" in definition
    assert "gpgcheck=1" in definition
    assert "gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-noust-cloudflared" in definition
    assert ("dnf", "-y", "install", "cloudflared") in runner.calls


def test_on_zypper_the_key_is_imported_and_the_package_installed(
    host: Path, runner: FakeRunner
) -> None:
    serve_the_pinned_key(runner)

    installer(runner, PackageFamily.ZYPPER).install()

    assert (host / "etc/zypp/repos.d/noust-cloudflared.repo").is_file()
    assert any(call[:2] == ("rpm", "--import") for call in runner.calls)
    assert ("zypper", "--non-interactive", "install", "cloudflared") in runner.calls


def test_the_repository_is_the_one_of_each_family() -> None:
    apt = repository_for(platform(PackageFamily.APT))
    dnf = repository_for(platform(PackageFamily.DNF))

    assert apt.url == "https://pkg.cloudflare.com/cloudflared"  # type: ignore[union-attr]
    assert dnf.baseurl == "https://pkg.cloudflare.com/cloudflared/rpm"  # type: ignore[union-attr]
    assert dnf.alias == "cloudflared"  # type: ignore[union-attr]


# -- What cannot be done -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("chosen", "transactional"),
    [(PackageFamily.NONE, False), (PackageFamily.DNF, True)],
)
def test_without_a_package_manager_to_use_it_says_how_to_do_it_by_hand(
    host: Path, runner: FakeRunner, chosen: PackageFamily, transactional: bool
) -> None:
    with pytest.raises(PrerequisiteError) as refused:
        CloudflaredInstaller(
            runner=runner,
            fs=get_fs(),
            platform=platform(chosen, transactional=transactional),
            which=lambda program: None,
        ).install()

    assert "https://pkg.cloudflare.com/" in (refused.value.details or "")
    assert runner.calls == []


def test_a_rehearsal_downloads_and_installs_nothing(host: Path) -> None:
    inner = FakeRunner()
    set_runner(DryRunRunner(inner))
    set_fs(DryRunFileSystem())
    try:
        done = CloudflaredInstaller(
            platform=platform(PackageFamily.APT), which=lambda program: None
        ).install()
    finally:
        set_runner(None)
        set_fs(None)

    assert inner.calls == []
    assert (done.installed_now, done.rehearsal) == (False, True)
    assert not (host / "etc/apt/sources.list.d/noust-cloudflared.list").exists()


def test_an_install_that_leaves_no_connector_on_the_path_is_an_error(
    host: Path, runner: FakeRunner
) -> None:
    serve_the_pinned_key(runner)

    with pytest.raises(DependencyError, match="not found"):
        installer(runner, which=lambda program: None).install()


# -- The command and the API ---------------------------------------------------------------


def test_the_command_installs_it_and_says_what_it_did(monkeypatch: pytest.MonkeyPatch) -> None:
    result = cloudflared_install.CloudflaredInstall(
        path=PATH, installed_now=True, version="2026.9.0"
    )
    monkeypatch.setattr(CloudflaredInstaller, "install", lambda self: result)
    monkeypatch.setattr(cloudflared_install, "audit", SimpleNamespace(record=lambda *a, **k: None))

    shown = CliRunner().invoke(cli, ["--json", "tunnel", "install-cloudflared"])

    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output) == {
        "path": PATH,
        "installed_now": True,
        "version": "2026.9.0",
        "rehearsal": False,
    }


def test_the_command_is_listed_for_a_tunnel_that_cannot_be_made_without_it() -> None:
    from noust.core.server_text import say

    assert "noust tunnel install-cloudflared" in (
        say("tunnels.cloudflared_missing", url="https://pkg.cloudflare.com/").hint or ""
    )
    assert any("install-cloudflared" in command.name for command in _tunnel_commands())


def _tunnel_commands() -> list[Any]:
    from noust.cli.commands.tunnel import cli as tunnel_cli

    return list(tunnel_cli.commands.values())  # type: ignore[attr-defined]


def test_the_api_queues_the_install_as_a_job_and_asks_for_sudo_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient

    from noust.web.api import tunnels as tunnels_api
    from noust.web.api.auth import get_current_session
    from noust.web.api.deps import install_error_handlers, require_elevated

    queued: list[dict[str, Any]] = []

    class Jobs:
        def create_job(self, **kwargs: Any) -> Any:
            queued.append(kwargs)
            return SimpleNamespace(
                id="j1", status=SimpleNamespace(value="pending"), to_dict=lambda: {"id": "j1"}
            )

    monkeypatch.setattr(tunnels_api, "get_job_manager", lambda: Jobs())
    session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(tunnels_api.router, prefix="/api/tunnels")
    app.dependency_overrides[get_current_session] = lambda: session
    elevated = {"on": False}

    def elevation() -> dict[str, Any]:
        if not elevated["on"]:
            raise HTTPException(status_code=403, detail={"error": "elevation_required"})
        return session

    app.dependency_overrides[require_elevated] = elevation
    client = TestClient(app)

    assert client.post("/api/tunnels/install-cloudflared").status_code == 403
    elevated["on"] = True
    accepted = client.post("/api/tunnels/install-cloudflared")

    assert accepted.status_code == 202, accepted.text
    [job] = queued
    assert job["job_type"].value == "cloudflared_install"
    assert job["func"] is tunnels_api.install_cloudflared_job
    assert job["actor"] == "alice" or job["actor"]


def test_the_job_runs_the_installer_and_returns_what_it_did(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from noust.web.api import tunnels as tunnels_api

    result = cloudflared_install.CloudflaredInstall(path=PATH, installed_now=True, version="1")
    monkeypatch.setattr(CloudflaredInstaller, "install", lambda self: result)

    assert tunnels_api.install_cloudflared_job("alice")["path"] == PATH


def test_the_route_asks_for_the_permission_and_the_scope_the_others_of_tunnels_do() -> None:
    from noust.web.permissions import Permission
    from noust.web.permissions.routes_tunnels import ROUTES

    assert ROUTES[("POST", "/api/tunnels/install-cloudflared")] == Permission.SERVER_MANAGE
