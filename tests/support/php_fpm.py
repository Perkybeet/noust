# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_php_fpm.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.appdir.layout import RELEASES
from noust.core.store import NoustStore
from noust.deployers import lifecycle
from noust.deployers import php_fpm as php_module
from noust.deployers.helpers.php_fpm import PoolSpec
from noust.deployers.php_fpm import PhpFpmDeployer
from tests.support.release_pipeline import FakeGit, FakeServices, FakeWeb, TreeRunner

DOMAIN = "blog.example.com"
APP = "blog-example-com"
GIT_URL = "https://github.com/example/blog.git"
POOL_DIR = "etc/php/8.2/fpm/pool.d"

WORDPRESS = {
    "index.php": "<?php require __DIR__ . '/wp-blog-header.php';\n",
    "wp-config-sample.php": "<?php\n",
    "wp-content/plugins/akismet/akismet.php": "<?php\n",
    "wp-content/themes/twentyfour/style.css": "/* theme */\n",
}


def debian_fpm(root: Path, *versions: str, binaries: tuple[str, ...] | None = None) -> Path:
    """Lay out Debian's per-version FPM directories under a fake root."""
    for version in versions:
        (root / "etc" / "php" / version / "fpm" / "pool.d").mkdir(parents=True, exist_ok=True)
    sbin = root / "usr" / "sbin"
    sbin.mkdir(parents=True, exist_ok=True)
    for version in versions if binaries is None else binaries:
        (sbin / f"php-fpm{version}").write_text("")
    return root


def spec(**overrides: Any) -> PoolSpec:
    """A pool for the test application."""
    values: dict[str, Any] = {
        "app_name": APP,
        "domain": DOMAIN,
        "user": "www-data",
        "group": "www-data",
        "listen_group": "www-data",
        "socket": Path(f"/run/php/wasm-{APP}.sock"),
        "root": Path(f"/var/www/apps/{APP}"),
        "tmp_dir": Path(f"/var/www/apps/.noust-php-tmp/{APP}"),
        "env": {"DB_NAME": "blog", "SALT": 'a$b;c"d'},
    }
    values.update(overrides)
    return PoolSpec(**values)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    """A store in the test's directory."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    monkeypatch.setattr(lifecycle, "get_store", lambda: instance)
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """
    Git, nginx, systemd, PHP-FPM and the FastCGI probe, faked.

    The probe answers 302 unless the active release's index.php says ``fatal``.
    """
    root = tmp_path / "apps" / APP
    fpm_root = debian_fpm(tmp_path / "fpm-root", "8.2")
    monkeypatch.setattr(php_module, "FPM_ROOT", fpm_root)
    probes: list[dict[str, str]] = []

    def fake_probe(socket_path: Path, params: Any, **_kwargs: Any) -> Any:
        def probe(_url: str, **kwargs: Any) -> bool:
            sent = dict(params())
            probes.append({"socket": str(socket_path), **sent})
            script = Path(sent["SCRIPT_FILENAME"])
            if script.is_file() and "fatal" in script.read_text():
                if kwargs.get("on_attempt"):
                    kwargs["on_attempt"](
                        "Health check attempt 1 failed: HTTP 500 is not an expected status\n"
                        "PHP Fatal error: Uncaught Error"
                    )
                return False
            return True

        return probe

    monkeypatch.setattr(php_module, "fastcgi_probe", fake_probe)
    runner = TreeRunner()
    return SimpleNamespace(
        root=root,
        fpm_root=fpm_root,
        pool=fpm_root / POOL_DIR / f"noust-{APP}.conf",
        runner=runner,
        git=FakeGit(),
        web=FakeWeb(),
        services=FakeServices(root),
        probes=probes,
    )


def wire(deployer: PhpFpmDeployer, machine: SimpleNamespace) -> PhpFpmDeployer:
    """Point a deployer at the fakes."""
    deployer.source_manager = machine.git  # type: ignore[assignment]
    deployer.service_manager = machine.services  # type: ignore[assignment]
    deployer.cert_manager = SimpleNamespace(obtain=lambda *a, **k: True)  # type: ignore[assignment]
    deployer._webserver_manager = lambda: machine.web  # type: ignore[method-assign]
    deployer.pre_flight_check = lambda: True  # type: ignore[method-assign]
    return deployer


def deploy(machine: SimpleNamespace, layout: str = RELEASES, **options: Any) -> PhpFpmDeployer:
    """Deploy the application with the WordPress recipe's PHP settings."""
    deployer = wire(PhpFpmDeployer(verbose=False, runner=machine.runner), machine)
    deployer.configure(
        DOMAIN,
        GIT_URL,
        port=3100,
        ssl=False,
        app_path=machine.root,
        layout=layout,
        env_vars={"WORDPRESS_DB_NAME": "blog", "WORDPRESS_AUTH_KEY": "k" + "ey"},
        **options,
    )
    deployer.deploy()
    return deployer


WORDPRESS_OPTIONS: dict[str, Any] = {
    "php_webroot": ".",
    "php_deny": ["wp-config.php", "readme.html"],
    "php_max_upload": "128m",
    "php_shared_from_release": ["wp-content"],
    "php_files": {"wp-config.php": "<?php /* reads getenv() */\n"},
    "persistent_paths": ["wp-content", "wp-config.php"],
    "initial_health": ("/", "200-399", None),
}
