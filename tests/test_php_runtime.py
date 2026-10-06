# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
PHP applications pinned to a PHP version.

A server may have several PHP-FPM installed at once (Debian and Ubuntu install
one per version, and the Sury and Ondrej builds carry them side by side), and
an application can be pinned to one: its pool is written in that FPM, and
everything that later controls the pool (start, stop, restart, the gate, the
limits, deletion) finds it where it is, not in the newest.
"""

# ruff: noqa: F811

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.exceptions import DeploymentError
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.deployers import php_fpm as php_module
from noust.deployers.helpers import php_fpm as fpm_module
from noust.deployers.helpers.php_fpm import find_fpm, find_fpm_for, fpm_versions
from noust.deployers.php_fpm import PhpFpmDeployer, control_pool, pool_serving, remove_pool_of
from noust.managers.runtimes import RuntimeManager
from tests.test_php_fpm import (  # noqa: F401 - fixtures used by name
    APP,
    DOMAIN,
    GIT_URL,
    WORDPRESS,
    debian_fpm,
    machine,
    store,
    wire,
)
from tests.test_release_pipeline import write_tree

LARAVEL = {
    "composer.json": json.dumps({"require": {"php": "^8.1"}}),
    "public/index.php": "<?php\n",
}


def pool_of(machine: SimpleNamespace, version: str) -> Path:
    """Where the application's pool is in the FPM of a version."""
    return machine.fpm_root / "etc" / "php" / version / "fpm" / "pool.d" / f"noust-{APP}.conf"


def deploy_with(
    machine: SimpleNamespace,
    tmp_path: Path,
    noust_yaml: str = "",
    files: dict[str, str] | None = None,
) -> PhpFpmDeployer:
    """Deploy a PHP application whose repository says what its build block says."""
    machine.git.publish(
        write_tree(
            tmp_path / f"src-{len(machine.git.commits)}",
            {**(files or LARAVEL), **({"noust.yaml": noust_yaml} if noust_yaml else {})},
        )
    )
    deployer = wire(PhpFpmDeployer(verbose=False, runner=machine.runner), machine)
    deployer._runtime_manager = RuntimeManager(
        runner=machine.runner, root=tmp_path / "runtimes", php_root=machine.fpm_root
    )
    deployer.configure(
        DOMAIN, GIT_URL, port=3100, ssl=False, app_path=machine.root, layout="releases"
    )
    deployer.deploy()
    return deployer


class TestFindingAnFpm:
    def test_the_versions_installed_are_listed_newest_first(self, tmp_path: Path) -> None:
        root = debian_fpm(tmp_path, "7.4", "8.1", "8.10", "8.3", binaries=("8.1", "8.10", "8.3"))

        assert fpm_versions(root) == ["8.10", "8.3", "8.1"]

    def test_a_pinned_version_is_found_though_a_newer_one_is_installed(
        self, tmp_path: Path
    ) -> None:
        root = debian_fpm(tmp_path, "8.1", "8.3")

        found = find_fpm(root, "8.1")

        assert found.version == "8.1"
        assert found.service == "php8.1-fpm"
        assert found.pool_dir == root / "etc/php/8.1/fpm/pool.d"

    def test_a_version_that_is_not_installed_says_what_is_and_how_to_install_it(
        self, tmp_path: Path
    ) -> None:
        root = debian_fpm(tmp_path, "8.1", "8.3")

        with pytest.raises(DeploymentError) as excinfo:
            find_fpm(root, "8.2")

        assert "PHP 8.2 is not installed" in excinfo.value.message
        assert "8.3, 8.1" in excinfo.value.message
        assert "noust runtime install php@8.2" in excinfo.value.details

    def test_where_there_is_one_unversioned_fpm_a_version_cannot_be_told_apart(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "etc/php-fpm.d").mkdir(parents=True)
        (tmp_path / "usr/sbin").mkdir(parents=True)
        (tmp_path / "usr/sbin/php-fpm").write_text("")

        assert find_fpm(tmp_path, "8.3").version is None

    def test_the_fpm_that_holds_a_pool_is_the_one_found_for_it(self, tmp_path: Path) -> None:
        root = debian_fpm(tmp_path, "8.1", "8.3")
        (root / f"etc/php/8.1/fpm/pool.d/noust-{APP}.conf").write_text("[pool]\n")

        assert find_fpm_for(APP, root).version == "8.1"

    def test_a_pool_moved_aside_by_a_stop_is_still_held(self, tmp_path: Path) -> None:
        root = debian_fpm(tmp_path, "8.1", "8.3")
        (root / f"etc/php/8.1/fpm/pool.d/noust-{APP}.conf.disabled").write_text("[pool]\n")

        assert find_fpm_for(APP, root).version == "8.1"

    def test_an_application_with_no_pool_gets_the_pinned_or_the_newest(
        self, tmp_path: Path
    ) -> None:
        root = debian_fpm(tmp_path, "8.1", "8.3")

        assert find_fpm_for(APP, root).version == "8.3"
        assert find_fpm_for(APP, root, "8.1").version == "8.1"

    def test_a_pool_of_the_legacy_prefix_is_held_too(self, tmp_path: Path) -> None:
        root = debian_fpm(tmp_path, "8.1", "8.3")
        (root / f"etc/php/8.1/fpm/pool.d/wasm-{APP}.conf").write_text("[pool]\n")

        assert find_fpm_for(APP, root).version == "8.1"


class TestDeployingPinned:
    @pytest.fixture
    def two(self, machine: SimpleNamespace) -> SimpleNamespace:
        """The machine, with PHP-FPM 8.2 and 8.3 installed."""
        debian_fpm(machine.fpm_root, "8.2", "8.3")
        return machine

    def test_the_pool_is_written_in_the_fpm_the_build_block_pins(
        self, tmp_path: Path, two: SimpleNamespace, store: NoustStore
    ) -> None:
        deploy_with(two, tmp_path, "build:\n  runtime: php@8.2\n")

        assert pool_of(two, "8.2").is_file()
        assert not pool_of(two, "8.3").exists()
        assert two.runner.ran("/usr/sbin/php-fpm8.2", "-t")
        assert two.runner.ran("systemctl", "reload-or-restart", "php8.2-fpm")
        assert not two.runner.ran("systemctl", "reload-or-restart", "php8.3-fpm")
        # The socket the site proxies to is that FPM's own pool's.
        site = two.web.sites[DOMAIN]
        assert site["context"]["fastcgi_socket"] == f"/run/php/noust-{APP}.sock"

    def test_without_a_pin_the_newest_that_satisfies_composer_is_used(
        self, tmp_path: Path, two: SimpleNamespace, store: NoustStore
    ) -> None:
        deploy_with(two, tmp_path)  # composer.json requires ^8.1

        assert pool_of(two, "8.3").is_file()
        assert not pool_of(two, "8.2").exists()

    def test_composer_pins_below_the_newest_when_it_must(
        self, tmp_path: Path, two: SimpleNamespace, store: NoustStore
    ) -> None:
        files = {**LARAVEL, "composer.json": json.dumps({"require": {"php": "~8.2.0"}})}

        deploy_with(two, tmp_path, files=files)

        assert pool_of(two, "8.2").is_file()
        assert not pool_of(two, "8.3").exists()

    def test_composer_that_nothing_satisfies_goes_on_with_the_newest_and_says_so(
        self, tmp_path: Path, two: SimpleNamespace, store: NoustStore
    ) -> None:
        files = {**LARAVEL, "composer.json": json.dumps({"require": {"php": "^8.5"}})}

        deploy_with(two, tmp_path, files=files)

        assert pool_of(two, "8.3").is_file()

    def test_a_pin_that_is_not_installed_fails_before_any_pool_is_written(
        self, tmp_path: Path, two: SimpleNamespace, store: NoustStore
    ) -> None:
        # apt is not on this machine, so it cannot be installed.
        two.runner.only_knows("composer")

        with pytest.raises(DeploymentError):
            deploy_with(two, tmp_path, "build:\n  runtime: php@7.4\n")

        assert not pool_of(two, "8.2").exists()
        assert not pool_of(two, "8.3").exists()
        assert store.get_app(DOMAIN) is None

    def test_composers_install_runs_with_the_pinned_php_first_on_the_path(
        self, tmp_path: Path, two: SimpleNamespace, store: NoustStore
    ) -> None:
        # The CLI of 8.2, which Composer must ask whether the dependencies fit.
        cli = two.fpm_root / "usr" / "bin" / "php8.2"
        cli.parent.mkdir(parents=True, exist_ok=True)
        cli.write_text("")

        deploy_with(two, tmp_path, "build:\n  runtime: php@8.2\n")

        index = next(
            i for i, call in enumerate(two.runner.calls) if call[:2] == ("composer", "install")
        )
        path = two.runner.envs[index]["PATH"]  # type: ignore[index]
        assert path.split(":")[0] == str(tmp_path / "runtimes" / "php" / "8.2" / "bin")
        assert (tmp_path / "runtimes" / "php" / "8.2" / "bin" / "php").is_symlink()

    def test_a_custom_install_replaces_composers(
        self, tmp_path: Path, two: SimpleNamespace, store: NoustStore
    ) -> None:
        deploy_with(two, tmp_path, "build:\n  install: ./scripts/install.sh --prod\n")

        assert ("./scripts/install.sh", "--prod") in two.runner.calls
        assert not any(call[:2] == ("composer", "install") for call in two.runner.calls)

    def test_changing_the_pin_moves_the_pool_and_removes_the_old_one(
        self, tmp_path: Path, two: SimpleNamespace, store: NoustStore
    ) -> None:
        deploy_with(two, tmp_path, "build:\n  runtime: php@8.2\n")
        assert pool_of(two, "8.2").is_file()

        deploy_with(two, tmp_path, "build:\n  runtime: php@8.3\n")

        assert pool_of(two, "8.3").is_file()
        assert not pool_of(two, "8.2").exists()

    def test_a_requirement_that_the_current_fpm_satisfies_does_not_move_the_application(
        self, tmp_path: Path, two: SimpleNamespace, store: NoustStore
    ) -> None:
        deploy_with(two, tmp_path, "build:\n  runtime: php@8.2\n")
        # The operator's pin goes; composer.json says ^8.1, which 8.2 satisfies.

        deploy_with(two, tmp_path)

        assert pool_of(two, "8.2").is_file()
        assert not pool_of(two, "8.3").exists()

    def test_a_block_that_asks_for_a_base_directory_is_refused_with_the_php_way(
        self, tmp_path: Path, two: SimpleNamespace, store: NoustStore
    ) -> None:
        from noust.core.exceptions import ValidationError

        with pytest.raises(ValidationError) as excinfo:
            deploy_with(two, tmp_path, "build:\n  base_directory: backend\n")

        assert excinfo.value.field == "build.base_directory"
        assert "webroot" in excinfo.value.details


class TestControllingAPinnedPool:
    @pytest.fixture
    def deployed(
        self,
        tmp_path: Path,
        machine: SimpleNamespace,
        store: NoustStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> Any:
        debian_fpm(machine.fpm_root, "8.2", "8.3")
        deploy_with(machine, tmp_path, "build:\n  runtime: php@8.2\n")
        app = store.get_app(DOMAIN)
        assert app is not None
        # What controls a pool outside a deploy asks the process-wide runner.
        monkeypatch.setattr(php_module, "get_runner", lambda: machine.runner)
        monkeypatch.setattr(fpm_module, "FPM_CONTROL_TIMEOUT", 1)
        return SimpleNamespace(machine=machine, app=app)

    def test_a_pool_is_served_by_the_fpm_that_holds_it(self, deployed: Any) -> None:
        assert pool_serving(deployed.app) in {"active", "failed"}
        # Not "stopped": it was found, in 8.2's directory, not looked for in 8.3's.
        assert pool_serving(deployed.app) != "stopped"

    def test_stop_and_start_work_on_the_pool_where_it_is(self, deployed: Any) -> None:
        machine = deployed.machine
        app = deployed.app

        control_pool(app, "stop")
        assert not pool_of(machine, "8.2").exists()
        assert (
            pool_of(machine, "8.2").with_name(pool_of(machine, "8.2").name + ".disabled").is_file()
        )
        assert not pool_of(machine, "8.3").exists()

        control_pool(app, "start")
        assert pool_of(machine, "8.2").is_file()
        assert not pool_of(machine, "8.3").exists()

    def test_restart_reloads_the_fpm_that_runs_it(self, deployed: Any) -> None:
        machine = deployed.machine
        before = len(machine.runner.calls)

        message = control_pool(deployed.app, "restart")

        assert "php8.2-fpm" in message
        assert any(
            call == ("systemctl", "reload-or-restart", "php8.2-fpm")
            for call in machine.runner.calls[before:]
        )

    def test_deleting_the_application_removes_the_pool_from_that_fpm(
        self, deployed: Any, tmp_path: Path
    ) -> None:
        from noust.core.logger import Logger

        removed = remove_pool_of(Path(deployed.app.app_path), Logger(verbose=False))

        assert removed
        assert not pool_of(deployed.machine, "8.2").exists()


class TestTheRuntimeManagerOfAPhpDeployer:
    def test_it_looks_for_fpm_where_the_deployer_does(
        self, machine: SimpleNamespace, store: NoustStore
    ) -> None:
        deployer = PhpFpmDeployer(verbose=False, runner=FakeRunner())

        assert deployer.runtimes.php_root == php_module.FPM_ROOT
