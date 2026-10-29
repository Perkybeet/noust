# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust webserver``: which web server serves each site, and moving them to the other one (item 105).

The command is what the guard of ``noust config set webserver`` points to, so the line it suggests
is run here as it is written. The machine is :mod:`tests.support.web_host`; nothing runs.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from noust.cli.app import cli as root_cli
from noust.core.config import Config
from noust.core.exceptions import ConfigError
from noust.core.store import App, get_store
from noust.deployers.webserver_move_plan import Managers
from tests.support.web_host import TwoServers, two_servers

DOMAINS = ("docs.example.com", "api.example.com")


@pytest.fixture
def servers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TwoServers]:
    """nginx serving a static site and a proxied one, Apache installed and stopped."""
    two = two_servers(tmp_path)
    store = get_store()
    for domain, app_type, static in (
        ("docs.example.com", "static", True),
        ("api.example.com", "node", False),
    ):
        store.create_app(
            App(
                domain=domain,
                app_type=app_type,
                app_path=f"/var/www/apps/{domain.replace('.', '-')}",
                port=None if static else 3000,
                is_static=static,
                webserver="nginx",
            )
        )
    two.nginx.create_site(
        "docs.example.com", "static", {"static_dir": "/var/www/apps/docs-example-com/current"}
    )
    two.nginx.create_site("api.example.com", "proxy", {"port": 3000})
    for domain in DOMAINS:
        two.nginx.enable_site(domain)
        row, app = store.get_site(domain), store.get_app(domain)
        assert row is not None and app is not None
        row.app_id = app.id
        store.update_site(row)

    def managers(source: str, target: str) -> Managers:
        assert {source, target} == {"nginx", "apache"}
        mine = {"nginx": two.nginx, "apache": two.apache}
        return Managers(mine[source], mine[target])

    monkeypatch.setattr("noust.deployers.webserver_move_plan.managers_between", managers)
    monkeypatch.setattr("noust.deployers.webserver_move.wait_until_healthy", two.probe)
    monkeypatch.setattr("noust.deployers.webserver_move.https_check", two.tls)
    monkeypatch.setattr(
        "noust.deployers.webserver_move_plan.ServerProvisioner",
        lambda **_: type("P", (), {"check_webserver": lambda self, name: _present(name)})(),
    )
    yield two


def _present(name: str):  # type: ignore[no-untyped-def]
    from noust.managers.server_provision import ProvisionStep

    return ProvisionStep(name, "present", "installed")


def run(*args: str, input: str | None = None) -> Result:
    return CliRunner().invoke(root_cli, ["webserver", *args], input=input)


class TestStatus:
    def test_it_says_which_server_holds_the_ports_and_how_many_sites_are_behind_each(
        self, servers: TwoServers
    ) -> None:
        result = run("status", "--json")

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["webserver"] == "nginx"
        assert data["holds_ports"] == "nginx"
        assert data["servers"]["nginx"]["applications"] == 2
        assert data["servers"]["apache"]["applications"] == 0
        assert data["servers"]["nginx"]["running"] is True
        assert data["servers"]["apache"]["running"] is False

    def test_in_words_it_names_the_setting_as_the_one_for_new_applications(
        self, servers: TwoServers
    ) -> None:
        result = run("status")

        assert result.exit_code == 0, result.output
        assert "new applications" in result.output
        assert "2 applications" in result.output


class TestPlan:
    def test_it_shows_each_site_and_that_the_ports_would_change_hands(
        self, servers: TwoServers
    ) -> None:
        result = run("plan", "--to", "apache", "--all")

        assert result.exit_code == 0, result.output
        for domain in DOMAINS:
            assert domain in result.output
        assert "handover" in result.output.lower() or "ports 80 and 443" in result.output
        assert servers.host.active["nginx"] is True

    def test_json_has_what_a_screen_needs(self, servers: TwoServers) -> None:
        result = run("plan", "--to", "apache", "--all", "--json")

        data = json.loads(result.output)
        assert data["mode"] == "handover" and data["blocked"] is False
        assert [site["domain"] for site in data["sites"]] == sorted(DOMAINS)

    def test_naming_some_sites_of_a_machine_that_must_hand_over_is_blocked_with_the_whole_command(
        self, servers: TwoServers
    ) -> None:
        result = run("plan", "--to", "apache", "api.example.com")

        assert result.exit_code == 0
        assert "noust webserver migrate --to apache --all" in result.output

    def test_show_prints_the_translated_site(self, servers: TwoServers) -> None:
        result = run("plan", "--to", "apache", "--all", "--show", "api.example.com")

        assert result.exit_code == 0, result.output
        assert "<VirtualHost *:80>" in result.output
        assert "ProxyPass / http://127.0.0.1:3000/" in result.output

    def test_it_needs_the_sites_or_all(self, servers: TwoServers) -> None:
        assert run("plan", "--to", "apache").exit_code != 0
        assert run("plan", "--to", "apache", "--all", "api.example.com").exit_code != 0

    def test_a_loss_is_listed_with_its_line(self, servers: TwoServers) -> None:
        path = servers.nginx.config_path("api.example.com")
        path.write_text(
            path.read_text().replace(
                "server_tokens off;", "server_tokens off;\n    sub_filter a b;", 1
            )
        )

        result = run("plan", "--to", "apache", "--all")

        assert "sub_filter" in result.output and "no equivalent" in result.output


class TestMigrate:
    def test_without_yes_it_shows_the_plan_asks_and_a_no_changes_nothing(
        self, servers: TwoServers
    ) -> None:
        result = run("migrate", "--to", "apache", "--all", input="n\n")

        assert result.exit_code != 0
        assert servers.host.active == {"nginx": True, "apache2": False}
        assert get_store().get_app("api.example.com").webserver == "nginx"  # type: ignore[union-attr]

    def test_json_without_yes_prints_the_plan_and_changes_nothing(
        self, servers: TwoServers
    ) -> None:
        result = run("migrate", "--to", "apache", "--all", "--json")

        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["plan"]["mode"] == "handover"
        assert servers.host.active == {"nginx": True, "apache2": False}

    def test_the_line_the_switch_guard_suggests_runs_as_it_is_written(
        self, servers: TwoServers
    ) -> None:
        from noust.core.webserver_switch import suggested_command

        line = suggested_command("apache")
        assert line.startswith("noust webserver migrate")

        result = CliRunner().invoke(root_cli, [*line.split()[1:], "--json"])

        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["plan"]["target"] == "apache"

    def test_with_yes_everything_moves(self, servers: TwoServers) -> None:
        result = run("migrate", "--to", "apache", "--all", "--yes")

        assert result.exit_code == 0, result.output
        assert servers.host.active == {"nginx": False, "apache2": True}
        assert get_store().get_app("api.example.com").webserver == "apache"  # type: ignore[union-attr]
        assert "docs.example.com" in result.output

    def test_after_it_the_setting_can_change(self, servers: TwoServers) -> None:
        with pytest.raises(ConfigError):
            Config().set("webserver", "apache")

        assert run("migrate", "--to", "apache", "--all", "--yes").exit_code == 0

        Config().set("webserver", "apache")
        assert Config().get("webserver") == "apache"

    def test_losses_need_to_be_accepted_by_name(self, servers: TwoServers) -> None:
        path = servers.nginx.config_path("api.example.com")
        path.write_text(
            path.read_text().replace(
                "server_tokens off;", "server_tokens off;\n    sub_filter a b;", 1
            )
        )

        refused = run("migrate", "--to", "apache", "--all", "--yes")
        assert refused.exit_code != 0
        assert "accept" in refused.output.lower()
        assert servers.host.active["nginx"] is True

        accepted = run("migrate", "--to", "apache", "--all", "--yes", "--accept-losses")
        assert accepted.exit_code == 0, accepted.output
        assert servers.host.active["apache2"] is True

    def test_a_site_that_does_not_answer_leaves_everything_as_it_was(
        self, servers: TwoServers
    ) -> None:
        servers.unanswered.add("api.example.com")

        result = run("migrate", "--to", "apache", "--all", "--yes")

        assert result.exit_code != 0
        assert "api.example.com" in result.output
        assert servers.host.active == {"nginx": True, "apache2": False}
        assert get_store().get_app("api.example.com").webserver == "nginx"  # type: ignore[union-attr]

    def test_a_dry_run_changes_nothing(self, servers: TwoServers) -> None:
        result = CliRunner().invoke(
            root_cli, ["--dry-run", "webserver", "migrate", "--to", "apache", "--all", "--yes"]
        )

        assert result.exit_code == 0, result.output
        assert servers.host.active == {"nginx": True, "apache2": False}
        assert get_store().get_app("api.example.com").webserver == "nginx"  # type: ignore[union-attr]
