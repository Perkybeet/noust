# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a PHP application's pool and site refuse, and what they keep to themselves.

- FPM expands an ``env[]`` value that starts with ``$`` from the master's own
  environment, quotes or not (``'$HOSTNAME'`` is the hostname), so such a
  value is refused rather than silently replaced.
- Every pool runs as the same user, so each is confined to its own directory
  with ``open_basedir``, and keeps its sessions and uploads in a temporary
  directory of its own instead of the one ``/tmp`` every pool shares.
- The site hands only ``.php`` to FPM; other PHP extensions and the leftovers
  of an installation (logs, dumps, backups) are refused rather than served
  as text.
"""

# ruff: noqa: F811

from __future__ import annotations

import re
import shutil
import stat
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.test_php_fpm import (  # noqa: F401  (pytest resolves fixtures by name)
    APP,
    DOMAIN,
    WORDPRESS,
    WORDPRESS_OPTIONS,
    deploy,
    machine,
    spec,
    store,
)
from tests.test_release_pipeline import write_tree
from tests.test_webserver_managers import FakeStore
from wasm.core.exceptions import ValidationError
from wasm.core.runner import FakeRunner
from wasm.core.store import WASMStore
from wasm.deployers.helpers.layout import INPLACE
from wasm.deployers.helpers.php_fpm import pool_env_lines, pool_tmp_dir, render_pool
from wasm.deployers.php_fpm import PHP_SETTINGS_FILE, PhpSettings
from wasm.managers.nginx_manager import NGINX_BACKEND, NginxManager

# ---------------------------------------------------------------------------
# env[] values FPM would expand
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["$HOSTNAME", "$secret", "${X}"])
def test_a_value_starting_with_a_dollar_is_refused(value: str) -> None:
    """FPM would hand PHP the master's variable of that name, or nothing."""
    with pytest.raises(ValidationError, match="TOKEN cannot be passed to PHP-FPM") as failure:
        pool_env_lines({"TOKEN": value})

    assert "$" in failure.value.details
    assert "wasm env set" in failure.value.details


def test_a_dollar_anywhere_else_is_carried_literally() -> None:
    """Only the first character triggers the expansion."""
    assert pool_env_lines({"SALT": "a$b", "PRICE": "10$"}) == [("PRICE", "10$"), ("SALT", "a$b")]


# ---------------------------------------------------------------------------
# One pool, one directory
# ---------------------------------------------------------------------------


def test_the_pool_is_confined_to_its_application_directory() -> None:
    """open_basedir with a trailing slash: /apps/blog must not open /apps/blog-other."""
    root = Path("/var/www/apps") / APP
    tmp = pool_tmp_dir(root)
    text = render_pool(spec(root=root, tmp_dir=tmp))

    assert tmp == Path("/var/www/apps/.wasm-php-tmp") / APP
    assert f"php_admin_value[open_basedir] = {root}/:{tmp}/:/tmp/:/usr/share/php/" in text
    assert f"php_admin_value[upload_tmp_dir] = {tmp}" in text
    assert f"php_admin_value[sys_temp_dir] = {tmp}" in text
    assert f"php_admin_value[session.save_path] = {tmp}" in text
    # Debian turns PHP's own session collection off and cleans only its
    # default directory from cron: without this, sessions pile up forever.
    assert "php_admin_value[session.gc_probability] = 1" in text


def test_a_path_the_ini_parser_would_read_as_syntax_is_refused() -> None:
    """The directories are written unquoted."""
    with pytest.raises(ValidationError, match="cannot be written into a PHP-FPM pool"):
        render_pool(spec(root=Path("/srv/my apps;x")))


def test_a_deploy_gives_the_pool_a_private_tmp_outside_its_tree(
    tmp_path: Path, machine: SimpleNamespace, store: WASMStore
) -> None:
    """Beside the applications: the parent root's and 0711, the directory 0700 and the user's."""
    machine.git.publish(write_tree(tmp_path / "v1", WORDPRESS))

    deploy(machine, **WORDPRESS_OPTIONS)

    tmp = pool_tmp_dir(machine.root)
    assert tmp.is_dir()
    assert stat.S_IMODE(tmp.stat().st_mode) == 0o700
    assert stat.S_IMODE(tmp.parent.stat().st_mode) == 0o711
    assert machine.runner.ran("chown", "www-data:www-data", str(tmp))
    pool = machine.pool.read_text()
    assert f"php_admin_value[open_basedir] = {machine.root}/:{tmp}/:" in pool
    assert f"php_admin_value[session.save_path] = {tmp}" in pool


def test_something_planted_where_the_tmp_goes_is_refused(
    tmp_path: Path, machine: SimpleNamespace, store: WASMStore
) -> None:
    """root never chowns through a link."""
    machine.git.publish(write_tree(tmp_path / "v1", WORDPRESS))
    tmp = pool_tmp_dir(machine.root)
    tmp.parent.mkdir(parents=True)
    tmp.symlink_to(tmp_path)

    with pytest.raises(Exception, match="not a plain directory"):
        deploy(machine, **WORDPRESS_OPTIONS)

    assert not machine.runner.ran("chown", "www-data:www-data", str(tmp))


# ---------------------------------------------------------------------------
# The settings file
# ---------------------------------------------------------------------------


def test_the_settings_file_is_written_0644_after_the_tree_is_handed_over(
    tmp_path: Path, machine: SimpleNamespace, store: WASMStore
) -> None:
    """Rewritten after the chown -R, so it is root's again after every deploy."""
    tree = write_tree(tmp_path / "v1", {"index.php": "<?php\n"})

    class CopyFetch:
        def fetch(self, source: str, destination: Path, **_kwargs: Any) -> bool:
            shutil.copytree(tree, destination, dirs_exist_ok=True)
            return True

    machine.git = CopyFetch()
    deploy(machine, layout=INPLACE)

    path = machine.root / PHP_SETTINGS_FILE
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


@pytest.mark.parametrize(
    "settings",
    [
        {"max_upload": "999999g"},
        {"deny": [f"f{i}.txt" for i in range(65)]},
        {"shared_from_release": [f"d{i}" for i in range(17)]},
    ],
)
def test_settings_out_of_bounds_are_refused(settings: dict[str, Any]) -> None:
    """In place the file sits in a tree the service user owns: read it as untrusted."""
    with pytest.raises(ValidationError):
        PhpSettings.from_mapping(settings)


def test_a_large_but_sane_upload_is_accepted() -> None:
    """A video site may want gigabytes."""
    assert PhpSettings.from_mapping({"max_upload": "2g"}).max_upload == "2g"


# ---------------------------------------------------------------------------
# The site
# ---------------------------------------------------------------------------


@pytest.fixture
def site(tmp_path: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> str:
    """The FastCGI site as nginx is given it."""
    fake = FakeStore()
    monkeypatch.setattr("wasm.managers.webserver.get_store", lambda: fake)
    nginx = NginxManager(
        backend=replace(
            NGINX_BACKEND,
            sites_available=tmp_path / "nginx/sites-available",
            sites_enabled=tmp_path / "nginx/sites-enabled",
        )
    )
    return nginx.render_config(
        "example.com",
        "fastcgi",
        {
            "document_root": "/srv/x",
            "fastcgi_socket": "/run/php/x.sock",
            "max_upload": "64m",
            "deny_paths": [],
        },
    )


def refused_patterns(text: str) -> list[re.Pattern[str]]:
    """Every case-insensitive regex location whose body is ``deny all``."""
    found = re.findall(r"location ~\* (\S+) \{\s*deny all;", text)
    return [re.compile(pattern, re.IGNORECASE) for pattern in found]


@pytest.mark.parametrize(
    "uri",
    [
        "/shell.phtml",
        "/x.phar",
        "/old.php5",
        "/lib/config.inc",
        "/wp-content/debug.log",
        "/backup.sql",
        "/wp-config.php.bak",
        "/wp-config.php.old",
        "/wp-config.php.orig",
        "/.wp-config.php.swp",
        "/wp-config.php~",
        "/config.php.dist",
        "/DUMP.SQL",
    ],
)
def test_other_php_extensions_and_leftovers_are_refused(site: str, uri: str) -> None:
    """Served as text they would show source code, credentials or data."""
    assert any(pattern.search(uri) for pattern in refused_patterns(site)), uri


@pytest.mark.parametrize(
    "uri",
    [
        "/index.php",
        "/wp-login.php",
        "/wp-admin/admin-ajax.php",
        "/wp-includes/css/dist/block-library/style.min.css",
        "/wp-includes/js/jquery/jquery.min.js",
        "/wp-content/uploads/2026/05/photo.jpg",
        "/wp-content/themes/twentyfour/assets/fonts/inter.woff2",
        "/wp-content/plugins/akismet/logo.svg",
        "/feed/",
        "/robots.txt",
    ],
)
def test_wordpress_keeps_working(site: str, uri: str) -> None:
    """PHP entry points and every kind of asset WordPress serves are untouched."""
    assert not any(pattern.search(uri) for pattern in refused_patterns(site)), uri


def test_the_new_refusals_come_before_the_php_handler(site: str) -> None:
    """nginx tries regex locations in order; a refusal after the handler never applies."""
    handler = site.index("location ~ \\.php$")
    for pattern in refused_patterns(site):
        assert site.index(pattern.pattern) < handler, pattern.pattern
