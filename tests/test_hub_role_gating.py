# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A hub refuses local deployments at the managers, not only at the CLI.

The CLI refuses a hub's missing commands where it resolves them
(:func:`noust.central.refuse_command_on_hub`), but the console's API, its
jobs and the webhook reach the same managers without going through Click.
So each manager that deploys, serves or keeps something *here* refuses on a
hub where it starts (rule 4), and every door gets the same message. These
tests call each chokepoint with placeholder arguments: the refusal is the
first thing it does, before any argument is looked at.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from noust.central import RoleError
from noust.core.config import Config
from noust.core.exceptions import CertificateError
from noust.core.runner import FakeRunner

#: Stands in for an argument the refusal comes before.
UNUSED = cast(Any, None)


@pytest.fixture(autouse=True)
def fresh_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A configuration read from an empty file in the test's directory."""
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
    Config.reset_instance()
    yield
    Config.reset_instance()


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make this Noust a hub, the way the container image does."""
    monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
    Config.reset_instance()


def _deploy_base() -> Any:
    from noust.deployers.nodejs import NodeJSDeployer

    return NodeJSDeployer().deploy()


def _update_base() -> Any:
    from noust.deployers.nodejs import NodeJSDeployer

    return NodeJSDeployer().update()


def _deploy_auto() -> Any:
    from noust.deployers.auto import AutoDeployer

    return AutoDeployer().deploy()


def _deploy_monorepo() -> Any:
    from noust.deployers.monorepo import MonorepoDeployer

    return MonorepoDeployer(runner=FakeRunner()).deploy()


def _deploy_compose() -> Any:
    from noust.deployers.docker_compose import DockerComposeDeployer

    return DockerComposeDeployer(runner=FakeRunner()).deploy()


def _lifecycle(name: str, *args: Any, **kwargs: Any) -> Callable[[], Any]:
    def call() -> Any:
        from noust.deployers import lifecycle

        return getattr(lifecycle, name)(*args, **kwargs)

    return call


def _domains(name: str, *args: Any) -> Callable[[], Any]:
    def call() -> Any:
        from noust.deployers import domains

        return getattr(domains, name)(*args)

    return call


def _migrate() -> Any:
    from noust.deployers.migrate import migrate

    return migrate("shop.example.com", UNUSED)


def _apply_import() -> Any:
    from noust.deployers.app_export import apply_import

    return apply_import(UNUSED, deploy=UNUSED)


def _zero_downtime() -> Any:
    from noust.deployers.bluegreen import set_zero_downtime

    return set_zero_downtime("shop.example.com", True)


def _previews() -> Any:
    from noust.managers.previews import enable_previews

    return enable_previews("shop.example.com", "preview.example.com")


def _site(method: str, *args: Any) -> Callable[[], Any]:
    def call() -> Any:
        from noust.managers.nginx_manager import NginxManager

        return getattr(NginxManager(runner=FakeRunner()), method)(*args)

    return call


def _cert(method: str, *args: Any) -> Callable[[], Any]:
    def call() -> Any:
        from noust.managers.cert_manager import CertManager

        return getattr(CertManager(runner=FakeRunner()), method)(*args)

    return call


def _database() -> Any:
    from noust.managers.database import get_db_manager

    manager = get_db_manager("postgresql")
    assert manager is not None
    return manager.create_database("shop")


def _backup(method: str, *args: Any) -> Callable[[], Any]:
    def call() -> Any:
        from noust.managers.backup_manager import BackupManager

        return getattr(BackupManager(verbose=False, runner=FakeRunner()), method)(*args)

    return call


def _rollback(method: str, *args: Any) -> Callable[[], Any]:
    def call() -> Any:
        from noust.managers.backup_manager import RollbackManager

        return getattr(RollbackManager(verbose=False), method)(*args)

    return call


def _backup_schedule() -> Any:
    from noust.managers.backup_scheduler import BackupScheduler

    return BackupScheduler(runner=FakeRunner()).create_schedule(UNUSED)


def _cron(method: str, *args: Any) -> Callable[[], Any]:
    def call() -> Any:
        from noust.managers.cron_manager import CronManager

        return getattr(CronManager(runner=FakeRunner()), method)(*args)

    return call


def _service() -> Any:
    from noust.managers.service_manager import ServiceManager

    return ServiceManager(runner=FakeRunner()).is_installed()


#: Every chokepoint, and the feature its refusal names.
CHOKEPOINTS: list[tuple[str, str, Callable[[], Any]]] = [
    ("BaseDeployer.deploy", "Applications", _deploy_base),
    ("BaseDeployer.update", "Applications", _update_base),
    ("AutoDeployer.deploy", "Applications", _deploy_auto),
    ("MonorepoDeployer.deploy", "Applications", _deploy_monorepo),
    ("DockerComposeDeployer.deploy", "Applications", _deploy_compose),
    ("lifecycle.update_app", "Applications", _lifecycle("update_app", "shop.example.com")),
    (
        "lifecycle.activate_release",
        "Releases",
        _lifecycle("activate_release", "shop.example.com"),
    ),
    (
        "lifecycle.rollback_to_deployment",
        "Rollbacks",
        _lifecycle("rollback_to_deployment", "shop.example.com", 1),
    ),
    (
        "lifecycle.set_resource_limits",
        "Applications",
        _lifecycle("set_resource_limits", "shop.example.com", UNUSED),
    ),
    (
        "lifecycle.set_health_check",
        "Applications",
        _lifecycle("set_health_check", "shop.example.com", path=None, expect=None, timeout=None),
    ),
    (
        "lifecycle.set_release_retention",
        "Releases",
        _lifecycle("set_release_retention", "shop.example.com", 3),
    ),
    ("migrate.migrate", "Applications", _migrate),
    ("app_export.apply_import", "Importing applications", _apply_import),
    ("bluegreen.set_zero_downtime", "Applications", _zero_downtime),
    ("previews.enable_previews", "Preview environments", _previews),
    (
        "domains.add_domain",
        "Domains",
        _domains("add_domain", "shop.example.com", "www.shop.example.com"),
    ),
    (
        "domains.remove_domain",
        "Domains",
        _domains("remove_domain", "shop.example.com", "www.shop.example.com"),
    ),
    (
        "domains.issue_certificate",
        "Certificates",
        _domains("issue_certificate", "shop.example.com"),
    ),
    ("WebServerManager.create_site", "Sites", _site("create_site", "shop.example.com")),
    ("WebServerManager.update_site", "Sites", _site("update_site", "shop.example.com")),
    ("WebServerManager.enable_site", "Sites", _site("enable_site", "shop.example.com")),
    (
        "WebServerManager.replace_site_config",
        "Sites",
        _site("replace_site_config", "shop.example.com", "server {}"),
    ),
    ("WebServerManager.write_upstream", "Sites", _site("write_upstream", "shop.example.com", 3000)),
    ("CertManager.create", "Certificates", _cert("create", ["shop.example.com"])),
    ("CertManager.obtain", "Certificates", _cert("obtain", "shop.example.com")),
    ("CertManager.renew", "Certificates", _cert("renew")),
    ("BaseDatabaseManager.runner", "Databases", _database),
    ("BackupManager.create", "Application backups", _backup("create", "shop.example.com")),
    ("BackupManager.restore", "Application backups", _backup("restore", "shop-1")),
    (
        "BackupManager.restore_archive",
        "Application backups",
        _backup("restore_archive", Path("/nowhere.tar.gz")),
    ),
    (
        "BackupManager.import_backups",
        "Application backups",
        _backup("import_backups", Path("/nowhere")),
    ),
    (
        "RollbackManager.create_pre_deploy_backup",
        "Rollbacks",
        _rollback("create_pre_deploy_backup", "shop.example.com"),
    ),
    ("RollbackManager.rollback", "Rollbacks", _rollback("rollback", "shop.example.com")),
    ("BackupScheduler.create_schedule", "Application backups", _backup_schedule),
    ("CronManager.create_job", "Scheduled jobs", _cron("create_job", UNUSED)),
    ("CronManager.enable_job", "Scheduled jobs", _cron("enable_job", "nightly")),
    ("CronManager.run_now", "Scheduled jobs", _cron("run_now", "nightly")),
    ("ServiceManager.runner", "Services", _service),
]


@pytest.mark.parametrize(
    ("feature", "call"),
    [pytest.param(feature, call, id=name) for name, feature, call in CHOKEPOINTS],
)
def test_a_hub_refuses_at_the_manager(hub: None, feature: str, call: Callable[[], Any]) -> None:
    with pytest.raises(RoleError, match=rf"^{feature}: not available on this central"):
        call()


class TestAServerIsNotRefused:
    """The same chokepoints on a server go on to do their work."""

    def test_an_update_reaches_the_application_lookup(self) -> None:
        from noust.core.exceptions import NoustError
        from noust.deployers.lifecycle import update_app

        with pytest.raises(NoustError) as caught:
            update_app("missing.example.com")
        assert not isinstance(caught.value, RoleError)

    def test_a_certificate_renewal_reaches_certbot(self) -> None:
        from noust.managers.cert_manager import CertManager

        runner = FakeRunner()
        CertManager(runner=runner).renew()
        assert any(call[0] == "certbot" for call in runner.calls)

    def test_a_database_command_reaches_the_runner(self) -> None:
        from noust.managers.database import get_db_manager

        runner = FakeRunner()
        manager = get_db_manager("postgresql")
        assert manager is not None
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr("noust.managers.database.base.get_runner", lambda: runner)
            manager.database_exists("shop")
        assert runner.calls


class TestInteractiveModeReachesTheSameGuard:
    """
    'noust -i' calls handle_webapp/handle_site/handle_service/handle_cert
    directly (cli/interactive.py's _run_command), bypassing Click's
    subcommand resolution entirely and with it refuse_command_on_hub
    (cli/app.py resolves --interactive before any subcommand). The webapp,
    site and cert flows are still refused because they reach the deployer,
    NginxManager and CertManager chokepoints already in CHOKEPOINTS above,
    whichever door calls them; this pins that the service flow is refused
    the same way, now that ServiceManager.runner carries the guard too.
    """

    def test_the_service_flow_is_refused(
        self, hub: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from argparse import Namespace

        from noust.cli.commands.service import handle_service

        exit_code = handle_service(Namespace(action="list", verbose=False, all=False))

        assert exit_code == 1
        combined = "".join(capsys.readouterr())
        assert "Services: not available on this central" in combined


class TestTheHubKeepsItsOwn:
    """What a hub does for itself is not a local deployment."""

    def test_its_self_signed_console_certificate(self, hub: None, tmp_path: Path) -> None:
        from noust.managers.cert_manager import CertManager

        runner = FakeRunner()
        # The fake openssl prints nothing, which the manager reports; what
        # matters is that it got as far as running openssl.
        with pytest.raises(CertificateError, match="openssl"):
            CertManager(runner=runner).generate_self_signed(
                "central.lan", tmp_path / "cert.pem", tmp_path / "key.pem"
            )
        assert any(call[0] == "openssl" for call in runner.calls)

    def test_listing_certificates_and_sites_is_harmless(self, hub: None) -> None:
        from noust.managers.cert_manager import CertManager
        from noust.managers.nginx_manager import NginxManager

        CertManager(runner=FakeRunner()).list_certificates()
        NginxManager(runner=FakeRunner()).list_sites()
