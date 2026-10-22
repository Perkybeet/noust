# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The web server's own configuration travels in the bundle of Noust's state (audit gap, checklist H-27, H-28).

A site edited by hand in the console's editor, the site of an adopted application and the
http-level settings an operator added to nginx are not in the store: ``restore-server`` renders the
sites Noust wrote from the store and can rebuild nothing else. So the bundle holds ``/etc/nginx``
and Apache's directory, with their links and their modes, and a restore puts them back.
"""

# The fixtures are imported from the tests of the thing they set up.
# ruff: noqa: F811

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from noust.managers.self_backup import SelfBackupManager
from noust.managers.self_restore import SelfRestore
from tests.test_self_backup import (  # noqa: F401
    PASSPHRASE,
    Runner,
    Server,
    _reset_store,
    cheap_scrypt,
    old,
    origin,
    runner,
    server,
)

pytestmark = pytest.mark.allow_subprocess


def web_tree(root: Path) -> Path:
    """An nginx configuration directory the way Debian lays it out."""
    nginx = root / "etc-nginx"
    (nginx / "sites-available").mkdir(parents=True)
    (nginx / "sites-enabled").mkdir()
    (nginx / "conf.d").mkdir()
    (nginx / "ssl").mkdir()
    (nginx / "nginx.conf").write_text("http { include sites-enabled/*; }\n")
    os.chmod(nginx / "nginx.conf", 0o644)
    (nginx / "sites-available" / "shop.conf").write_text(
        "server { server_name shop.example.com; }\n"
    )
    # Debian's `ln -s /etc/nginx/sites-available/x sites-enabled/x`: absolute, inside the tree.
    (nginx / "sites-enabled" / "shop.conf").symlink_to(nginx / "sites-available" / "shop.conf")
    (nginx / "sites-enabled" / "evil.conf").symlink_to("/etc/passwd")
    (nginx / "conf.d" / "limits.conf").write_text(
        "limit_req_zone $binary_remote_addr zone=a:10m rate=5r/s;\n"
    )
    (nginx / "ssl" / "shop.key").write_text("PRIVATE")
    os.chmod(nginx / "ssl" / "shop.key", 0o600)
    return nginx


def bundle_of(
    base: Path, runner: Runner, **webservers: Path
) -> tuple[Server, SelfBackupManager, Path]:
    """Take a bundle of a server that has these web server directories."""
    made = origin(base)
    manager = made.manager(runner, webservers=webservers)
    return made, manager, manager.create(PASSPHRASE).path


class TestWhatTheBundleHolds:
    def test_the_sites_the_snippets_and_the_keys_of_nginx_are_in_it(
        self, tmp_path: Path, runner: Runner
    ) -> None:
        nginx = web_tree(tmp_path)
        made, manager, bundle = bundle_of(tmp_path, runner, nginx=nginx)
        try:
            with_root, manifest = manager.open_bundle(bundle, PASSPHRASE, tmp_path / "open")
            names = {entry["path"] for entry in manifest["files"]}
            assert "nginx/sites-available/shop.conf" in names
            assert "nginx/conf.d/limits.conf" in names
            assert "nginx/nginx.conf" in names
            assert "nginx/ssl/shop.key" in names
            assert manifest["contains"]["webservers"] == ["nginx"]
            assert manifest["sources"]["nginx"] == str(nginx)
            assert (with_root / "nginx" / "nginx.conf").is_file()
        finally:
            for connection in made.held:
                connection.close()

    def test_a_link_inside_the_tree_is_kept_relative_and_one_outside_is_left_out(
        self, tmp_path: Path, runner: Runner
    ) -> None:
        nginx = web_tree(tmp_path)
        made, manager, bundle = bundle_of(tmp_path, runner, nginx=nginx)
        try:
            _root, manifest = manager.open_bundle(bundle, PASSPHRASE, tmp_path / "open")
            links = {entry["path"]: entry["target"] for entry in manifest["links"]}
            assert links["nginx/sites-enabled/shop.conf"] == "../sites-available/shop.conf"
            assert "nginx/sites-enabled/evil.conf" not in links
            assert any(
                entry["path"] == "nginx/sites-enabled/evil.conf" for entry in manifest["skipped"]
            )
        finally:
            for connection in made.held:
                connection.close()

    def test_each_file_keeps_its_mode_so_a_key_stays_private_and_a_config_stays_readable(
        self, tmp_path: Path, runner: Runner
    ) -> None:
        nginx = web_tree(tmp_path)
        made, manager, bundle = bundle_of(tmp_path, runner, nginx=nginx)
        try:
            _root, manifest = manager.open_bundle(bundle, PASSPHRASE, tmp_path / "open")
            modes = {entry["path"]: entry["mode"] for entry in manifest["files"] if "mode" in entry}
            assert all(path.startswith("nginx/") for path in modes)
            assert modes["nginx/nginx.conf"] == 0o644
            assert modes["nginx/ssl/shop.key"] == 0o600
        finally:
            for connection in made.held:
                connection.close()

    def test_a_server_without_a_web_server_directory_has_none_in_its_bundle(
        self, tmp_path: Path, runner: Runner
    ) -> None:
        made, manager, bundle = bundle_of(tmp_path, runner, nginx=tmp_path / "not-installed")
        try:
            _root, manifest = manager.open_bundle(bundle, PASSPHRASE, tmp_path / "open")
            assert manifest["contains"]["webservers"] == []
            assert not any(entry["path"].startswith("nginx/") for entry in manifest["files"])
        finally:
            for connection in made.held:
                connection.close()

    def test_apache_is_in_it_too(self, tmp_path: Path, runner: Runner) -> None:
        apache = tmp_path / "etc-apache2"
        (apache / "sites-available").mkdir(parents=True)
        (apache / "sites-available" / "shop.conf").write_text(
            "<VirtualHost *:80>\n</VirtualHost>\n"
        )
        made, manager, bundle = bundle_of(tmp_path, runner, apache2=apache)
        try:
            _root, manifest = manager.open_bundle(bundle, PASSPHRASE, tmp_path / "open")
            assert manifest["contains"]["webservers"] == ["apache2"]
            assert any(e["path"] == "apache2/sites-available/shop.conf" for e in manifest["files"])
        finally:
            for connection in made.held:
                connection.close()


class TestPuttingItBack:
    def test_a_new_server_gets_the_configuration_back_with_its_modes_and_links(
        self, tmp_path: Path, runner: Runner
    ) -> None:
        nginx = web_tree(tmp_path)
        made, manager, bundle = bundle_of(tmp_path, runner, nginx=nginx)
        try:
            fresh = server(tmp_path, "fresh")
            target = tmp_path / "fresh-nginx"
            (target / "sites-enabled").mkdir(parents=True)
            replacement = fresh.manager(runner, webservers={"nginx": target})

            SelfRestore(replacement, restart=False).restore(str(bundle), PASSPHRASE)

            assert (target / "sites-available" / "shop.conf").read_text().startswith("server {")
            assert stat.S_IMODE((target / "nginx.conf").stat().st_mode) == 0o644
            assert stat.S_IMODE((target / "ssl" / "shop.key").stat().st_mode) == 0o600
            link = target / "sites-enabled" / "shop.conf"
            assert link.is_symlink() and os.readlink(link) == "../sites-available/shop.conf"
            assert link.read_text().startswith("server {")
        finally:
            for connection in made.held:
                connection.close()

    def test_a_mode_a_bundle_asks_for_never_makes_a_file_writable_by_others(
        self, tmp_path: Path, runner: Runner
    ) -> None:
        nginx = web_tree(tmp_path)
        made, manager, bundle = bundle_of(tmp_path, runner, nginx=nginx)
        try:
            # A bundle altered after it was made is refused by its MAC; what a bundle that is
            # genuine but hostile can ask for is what is clamped, so ask the planner directly.
            fresh = server(tmp_path, "fresh")
            target = tmp_path / "fresh-nginx"
            replacement = fresh.manager(runner, webservers={"nginx": target})
            root, manifest = replacement.open_bundle(bundle, PASSPHRASE, tmp_path / "open2")
            for entry in manifest["files"]:
                if entry["path"] == "nginx/nginx.conf":
                    entry["mode"] = 0o4777
            (root / "MANIFEST.json").write_text(json.dumps(manifest))
            restore = SelfRestore(replacement, restart=False)
            plan = restore._plan(
                root,
                manifest,
                {"nginx": target, "config": fresh.config, "state": fresh.state},
                restore_report(),
            )
            modes = {destination.name: mode for _kind, _src, destination, _t, mode in plan}
            assert modes["nginx.conf"] & 0o7022 == 0
        finally:
            for connection in made.held:
                connection.close()


def restore_report():  # type: ignore[no-untyped-def]
    from noust.managers.self_restore import RestoreReport

    return RestoreReport()
