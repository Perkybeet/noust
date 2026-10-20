# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A manifest that holds cron jobs and the fleet (J-15).

The server's manifest used to say projects and applications. It now also says the cron jobs the server
runs, and, on a central, the servers it manages (their labels and the team that has each), where an
application is placed across them, and which server an application lives on at all. The promises are
the same: the schema is closed, a plan reads and prints no value (a cron command may hold a secret, so
it is never printed), applying creates and sets and never deletes, and doing it twice finds nothing.
"""

from __future__ import annotations

from typing import Any

import pytest

from noust.manifest import Applier, ManifestError, RefusingResolver, parse, plan, restart_changed
from noust.manifest.export import export
from tests.manifest_support import FakeServer

SHOP = "shop.acme.test"
API = "api.acme.test"

COMMAND = "/usr/bin/python3 /opt/report.py --token s3cr3t-token"


def manifest(extra: str = "", apps: str = "") -> Any:
    return parse(f"version: 1\n{extra}\napplications:\n{apps or '  []'}\n")


def shop(extra: str = "") -> str:
    return f"  - domain: {SHOP}\n    source: https://example.test/shop.git\n{extra}"


def make_plan(text: Any, server: FakeServer, **kwargs: Any) -> Any:
    return plan(text, server, RefusingResolver(), compare_env=True, **kwargs)


def apply(text: Any, server: FakeServer) -> Any:
    result = Applier(server, wait=True).apply(make_plan(text, server))
    assert result.ok, [o.to_dict() for o in result.outcomes]
    return result


CRON = f"""
cron:
  - name: nightly-report
    command: "{COMMAND}"
    schedule: daily
    user: www-data
    working_directory: /opt
    app: {SHOP}
    timeout: 600
"""


class TestCronInTheSchema:
    def test_a_job_is_read(self) -> None:
        parsed = manifest(CRON, shop())

        (job,) = parsed.cron
        assert (job.name, job.command, job.schedule, job.user) == (
            "nightly-report",
            COMMAND,
            "daily",
            "www-data",
        )
        assert (job.working_directory, job.app, job.timeout, job.enabled) == (
            "/opt",
            SHOP,
            600,
            True,
        )

    def test_a_job_needs_a_name_and_a_command(self) -> None:
        with pytest.raises(ManifestError, match=r"cron\[0\]\.command"):
            parse("version: 1\ncron:\n  - name: x\n")
        with pytest.raises(ManifestError, match=r"cron\[0\]\.name"):
            parse("version: 1\ncron:\n  - command: ls\n")

    def test_the_schedule_is_an_alias_or_a_calendar_and_defaults_to_daily(self) -> None:
        assert parse("version: 1\ncron:\n  - {name: a, command: ls}\n").cron[0].schedule == "daily"
        assert (
            parse("version: 1\ncron:\n  - {name: a, command: ls, schedule: '*-*-* 03:30:00'}\n")
            .cron[0]
            .schedule
            == "*-*-* 03:30:00"
        )
        with pytest.raises(ManifestError, match=r"cron\[0\]\.schedule"):
            parse("version: 1\ncron:\n  - {name: a, command: ls, schedule: 'every; rm -rf /'}\n")

    @pytest.mark.parametrize(
        ("line", "path"),
        [
            ("{name: a, command: ls, colour: red}", r"cron\[0\]\.colour"),
            ("{name: 'A B', command: ls}", r"cron\[0\]\.name"),
            ("{name: a, command: ls, timeout: 0}", r"cron\[0\]\.timeout"),
            ("{name: a, command: ls, timeout: 100000}", r"cron\[0\]\.timeout"),
            ("{name: a, command: ls, enabled: maybe}", r"cron\[0\]\.enabled"),
            (
                "{name: a, command: ls, working_directory: relative/dir}",
                r"cron\[0\]\.working_directory",
            ),
            ("{name: a, command: ls, app: 'not a domain'}", r"cron\[0\]\.app"),
        ],
    )
    def test_a_mistake_names_the_field(self, line: str, path: str) -> None:
        with pytest.raises(ManifestError, match=path):
            parse(f"version: 1\ncron:\n  - {line}\n")

    def test_the_same_job_twice_is_refused(self) -> None:
        with pytest.raises(ManifestError, match="appears twice"):
            parse("version: 1\ncron:\n  - {name: a, command: ls}\n  - {name: a, command: pwd}\n")

    def test_the_same_name_on_two_servers_is_two_jobs(self) -> None:
        parsed = parse(
            "version: 1\ncron:\n  - {name: a, command: ls}\n  - {name: a, command: ls, server: web-2}\n"
        )

        assert [job.server for job in parsed.cron] == [None, "web-2"]


class TestCronInThePlan:
    def test_a_missing_job_is_planned_and_needs_sudo_mode(self) -> None:
        server = FakeServer()
        server.add_app(SHOP)

        result = make_plan(manifest(CRON, shop()), server)

        (action,) = [a for a in result.actions if a.kind == "cron.set"]
        assert (action.target, action.elevated) == ("nightly-report", True)

    def test_the_plan_never_prints_the_command(self) -> None:
        server = FakeServer()
        server.add_app(SHOP)

        result = make_plan(manifest(CRON, shop()), server)

        assert "s3cr3t-token" not in repr(result.to_dict())
        assert "s3cr3t-token" not in " ".join(a.summary for a in result.actions)

    def test_a_job_as_written_has_nothing_to_do(self) -> None:
        server = FakeServer()
        server.add_app(SHOP)
        apply(manifest(CRON, shop()), server)

        assert [
            a
            for a in make_plan(manifest(CRON, shop()), server).actions
            if a.kind.startswith("cron")
        ] == []

    def test_the_command_is_compared_as_words_not_as_spaces(self) -> None:
        server = FakeServer()
        server.add_app(SHOP)
        apply(manifest(CRON, shop()), server)
        spaced = CRON.replace("/opt/report.py --token", "/opt/report.py    --token")

        assert make_plan(manifest(spaced, shop()), server).empty

    def test_a_different_command_is_a_change(self) -> None:
        server = FakeServer()
        server.add_app(SHOP)
        apply(manifest(CRON, shop()), server)

        result = make_plan(manifest(CRON.replace("--token", "--other"), shop()), server)

        (action,) = [a for a in result.actions if a.kind == "cron.set"]
        assert "change" in action.summary

    def test_a_different_schedule_is_a_change_even_when_written_as_an_alias(self) -> None:
        server = FakeServer()
        server.add_app(SHOP)
        apply(manifest(CRON, shop()), server)

        result = make_plan(manifest(CRON.replace("daily", "weekly"), shop()), server)

        assert [a.kind for a in result.actions] == ["cron.set"]

    def test_a_calendar_equal_to_an_alias_is_not_a_change(self) -> None:
        server = FakeServer()
        server.add_app(SHOP)
        apply(manifest(CRON, shop()), server)

        result = make_plan(manifest(CRON.replace("daily", "'*-*-* 00:00:00'"), shop()), server)

        assert result.empty

    def test_what_the_manifest_does_not_say_is_left_as_it_is(self) -> None:
        server = FakeServer()
        server.add_app(SHOP)
        apply(manifest(CRON, shop()), server)
        server.cron["nightly-report"]["user"] = "someone-else"
        bare = 'cron:\n  - {name: nightly-report, command: "' + COMMAND + '", schedule: daily}\n'

        assert [
            a for a in make_plan(manifest(bare, shop()), server).actions if a.kind == "cron.set"
        ] == []

    def test_a_job_that_is_off_is_switched_on_and_the_other_way_round(self) -> None:
        server = FakeServer()
        server.add_app(SHOP)
        apply(manifest(CRON, shop()), server)
        server.cron["nightly-report"]["enabled"] = False

        on = make_plan(manifest(CRON, shop()), server)
        off = make_plan(manifest(CRON + "    enabled: false\n", shop()), server)

        assert [a.kind for a in on.actions] == ["cron.state"]
        assert off.empty

    def test_jobs_the_manifest_does_not_name_are_listed_and_left_alone(self) -> None:
        server = FakeServer()
        server.add_app(SHOP)
        server.cron["old-backup"] = {
            "name": "old-backup", "command": "ls", "user": "root", "working_directory": "",
            "app_domain": "", "schedule": "daily", "on_calendar": "*-*-* 00:00:00",
            "enabled": True, "timeout": None, "service": "",
        }  # fmt: skip

        result = make_plan(manifest(CRON, shop()), server)
        apply(manifest(CRON, shop()), server)

        assert "cron:old-backup" in result.unmanaged
        assert "old-backup" in server.cron

    def test_a_server_that_does_not_let_the_token_read_cron_says_so_and_plans_nothing_of_it(
        self,
    ) -> None:
        server = FakeServer()
        server.add_app(SHOP)
        server.cron_readable = False

        result = make_plan(manifest(CRON, shop()), server)

        assert [a for a in result.actions if a.kind.startswith("cron")] == []
        assert any("cron" in warning for warning in result.warnings)


class TestCronWhenApplied:
    def test_a_job_is_created_with_everything_the_manifest_says(self) -> None:
        server = FakeServer()
        server.add_app(SHOP)

        apply(manifest(CRON, shop()), server)

        (call,) = [c for c in server.calls if c[0] == "POST" and c[1] == "/cron"]
        assert call[2] == {
            "name": "nightly-report",
            "command": COMMAND,
            "schedule": "daily",
            "user": "www-data",
            "working_directory": "/opt",
            "app_domain": SHOP,
            "timeout": 600,
        }

    def test_doing_it_twice_finds_nothing_left(self) -> None:
        server = FakeServer()
        server.add_app(SHOP)
        apply(manifest(CRON, shop()), server)

        assert make_plan(manifest(CRON, shop()), server).empty

    def test_a_job_written_off_is_created_and_switched_off(self) -> None:
        server = FakeServer()
        server.add_app(SHOP)

        apply(manifest(CRON + "    enabled: false\n", shop()), server)

        assert server.cron["nightly-report"]["enabled"] is False
        assert make_plan(manifest(CRON + "    enabled: false\n", shop()), server).empty

    def test_a_job_of_an_application_is_made_after_the_application(self) -> None:
        server = FakeServer()

        result = make_plan(manifest(CRON, shop()), server)

        kinds = [a.kind for a in result.actions]
        assert kinds.index("app.create") < kinds.index("cron.set")

    def test_nothing_is_ever_deleted(self) -> None:
        server = FakeServer()
        server.add_app(SHOP)
        apply(manifest(CRON, shop()), server)

        apply(parse("version: 1\n"), server)

        assert "nightly-report" in server.cron
        assert not [c for c in server.calls if c[0] == "DELETE"]


FLEET = f"""
servers:
  - name: web-2
    labels: {{role: web, region: eu}}
    team: acme
placements:
  - domain: {SHOP}
    servers: [web-1, web-2]
    serial: 1
    canary: web-1
"""


def fleet_server() -> FakeServer:
    server = FakeServer()
    server.add_app(SHOP)
    server.add_node("web-1")
    server.add_node("web-2", labels={"env": "prod"})
    return server


class TestTheFleetInTheSchema:
    def test_servers_and_placements_are_read(self) -> None:
        parsed = manifest(FLEET, shop())

        (node,) = parsed.servers
        assert (node.name, dict(node.labels or {}), node.team) == (
            "web-2",
            {"role": "web", "region": "eu"},
            "acme",
        )
        (placement,) = parsed.placements
        assert (placement.domain, placement.nodes, placement.serial, placement.canary) == (
            SHOP,
            ("web-1", "web-2"),
            1,
            "web-1",
        )

    @pytest.mark.parametrize(
        ("text", "path"),
        [
            ("servers:\n  - {labels: {a: b}}\n", r"servers\[0\]\.name"),
            ("servers:\n  - {name: web-2, colour: red}\n", r"servers\[0\]\.colour"),
            ("servers:\n  - {name: web-2, labels: {Bad Key: x}}\n", r"servers\[0\]\.labels"),
            ("servers:\n  - {name: web-2, labels: {a: 'has space'}}\n", r"servers\[0\]\.labels"),
            ("servers:\n  - {name: web-2, team: 'Not A Slug'}\n", r"servers\[0\]\.team"),
            ("placements:\n  - {servers: [a]}\n", r"placements\[0\]\.domain"),
            ("placements:\n  - {domain: x.test, serial: 0}\n", r"placements\[0\]\.serial"),
            (
                "placements:\n  - {domain: x.test, max_failures: -1}\n",
                r"placements\[0\]\.max_failures",
            ),
            ("placements:\n  - {domain: x.test, servers: web-2}\n", r"placements\[0\]\.servers"),
        ],
    )
    def test_a_mistake_names_the_field(self, text: str, path: str) -> None:
        with pytest.raises(ManifestError, match=path):
            parse("version: 1\n" + text)

    def test_a_server_twice_is_refused(self) -> None:
        with pytest.raises(ManifestError, match="appears twice"):
            parse("version: 1\nservers:\n  - {name: web-2}\n  - {name: web-2}\n")

    def test_serial_may_be_a_percentage(self) -> None:
        parsed = parse("version: 1\nplacements:\n  - {domain: x.test, serial: '25%'}\n")

        assert parsed.placements[0].serial == "25%"

    def test_an_application_names_the_server_it_lives_on(self) -> None:
        parsed = manifest("", shop("    server: web-2\n"))

        assert parsed.applications[0].server == "web-2"


class TestTheFleetInThePlan:
    def test_labels_are_merged_with_the_ones_the_server_has(self) -> None:
        server = fleet_server()

        result = make_plan(manifest(FLEET, shop()), server)
        apply(manifest(FLEET, shop()), server)

        assert any(a.kind == "server.labels" for a in result.actions)
        assert server.nodes["web-2"]["labels"] == {"env": "prod", "role": "web", "region": "eu"}

    def test_a_server_that_has_the_labels_and_the_team_has_nothing_to_do(self) -> None:
        server = fleet_server()
        apply(manifest(FLEET, shop()), server)

        result = make_plan(manifest(FLEET, shop()), server)

        assert not [a for a in result.actions if a.kind.startswith("server.")]

    def test_the_team_is_given_to_the_server(self) -> None:
        server = fleet_server()

        apply(manifest(FLEET, shop()), server)

        assert server.nodes["web-2"]["team"] == "acme"

    def test_a_server_the_central_does_not_manage_is_a_warning(self) -> None:
        server = fleet_server()
        text = FLEET.replace("web-2", "web-9", 1)

        result = make_plan(manifest(text, shop()), server)

        assert any("web-9" in warning for warning in result.warnings)
        assert not [a for a in result.actions if a.kind.startswith("server.")]

    def test_a_placement_is_made_after_its_application(self) -> None:
        server = FakeServer()
        server.add_node("web-1")
        server.add_node("web-2")

        result = make_plan(manifest(FLEET, shop()), server)

        kinds = [a.kind for a in result.actions]
        assert kinds.index("app.create") < kinds.index("placement.set")

    def test_the_placement_is_compared_and_converges(self) -> None:
        server = fleet_server()
        apply(manifest(FLEET, shop()), server)

        assert make_plan(manifest(FLEET, shop()), server).empty
        changed = FLEET.replace("serial: 1", "serial: 2")
        assert [a.kind for a in make_plan(manifest(changed, shop()), server).actions] == [
            "placement.set"
        ]

    def test_the_placement_sends_what_the_manifest_says(self) -> None:
        server = fleet_server()

        apply(manifest(FLEET, shop()), server)

        (call,) = [
            c for c in server.calls if c[0] == "PUT" and c[1].startswith("/fleet/placements")
        ]
        assert call[2] == {
            "nodes": ["web-1", "web-2"],
            "serial": 1,
            "max_failures": None,
            "canary": "web-1",
            "rollback_on_failure": True,
        }

    def test_a_token_that_does_not_see_the_fleet_gets_a_warning_and_no_actions(self) -> None:
        server = fleet_server()
        server.fleet_readable = False

        result = make_plan(manifest(FLEET, shop()), server)

        assert not [a for a in result.actions if a.kind.startswith(("server.", "placement."))]
        assert any("fleet" in warning.lower() for warning in result.warnings)


NODE_APP = f"""
  - domain: {API}
    source: https://example.test/api.git
    server: web-2
    env: {{NODE_ENV: production}}
    domains: [www.{API}]
    backups: {{schedule: daily}}
"""


class TestAnApplicationOnAnotherServer:
    def test_it_is_created_on_that_server_and_not_here(self) -> None:
        server = fleet_server()
        node = server.node_servers["web-2"]

        apply(manifest("", NODE_APP), server)

        assert API in node.apps
        assert API not in server.apps
        assert any(c[0] == "POST" and c[1] == "/nodes/web-2/api/apps" for c in server.calls), (
            server.calls
        )

    def test_everything_after_it_follows_it_there(self) -> None:
        server = fleet_server()
        node = server.node_servers["web-2"]

        apply(manifest("", NODE_APP), server)

        assert node.aliases[API] == [f"www.{API}"]
        assert node.schedules[API]["schedule"] == "daily"
        assert node.env[API]["NODE_ENV"] == "production"

    def test_doing_it_twice_finds_nothing_left(self) -> None:
        server = fleet_server()
        apply(manifest("", NODE_APP), server)

        assert make_plan(manifest("", NODE_APP), server).empty

    def test_the_plan_says_which_server_each_change_is_on(self) -> None:
        server = fleet_server()

        result = make_plan(manifest("", NODE_APP), server)

        assert all(a.server == "web-2" for a in result.actions)
        assert all(a.target.startswith("web-2:") for a in result.actions)
        assert result.to_dict()["actions"][0]["server"] == "web-2"

    def test_the_applications_of_this_server_and_the_other_do_not_mix(self) -> None:
        server = fleet_server()
        text = manifest("", shop() + NODE_APP)

        apply(text, server)

        assert SHOP in server.apps and API not in server.apps
        assert API in server.node_servers["web-2"].apps
        assert SHOP not in server.node_servers["web-2"].apps

    def test_what_the_other_server_has_that_the_manifest_lacks_is_unmanaged_there(self) -> None:
        server = fleet_server()
        server.node_servers["web-2"].add_app("blog.acme.test")

        result = make_plan(manifest("", NODE_APP), server)

        assert "web-2:blog.acme.test" in result.unmanaged

    def test_a_server_that_is_not_managed_is_a_warning_and_its_applications_wait(self) -> None:
        server = fleet_server()
        text = NODE_APP.replace("web-2", "web-9")

        result = make_plan(manifest("", text), server)

        assert result.actions == []
        assert any("web-9" in warning for warning in result.warnings)

    def test_variables_changed_there_are_restarted_there(self) -> None:
        server = fleet_server()
        apply(manifest("", NODE_APP), server)
        changed = manifest("", NODE_APP.replace("production", "staging"))
        result = Applier(server, wait=True).apply(
            plan(changed, server, RefusingResolver(), compare_env=True)
        )
        warnings: list[str] = []

        restarted = restart_changed(result, server, warnings.append)

        assert restarted == ["web-2:" + API]
        assert ("POST", f"/nodes/web-2/api/apps/{API}/restart", None) in server.calls
        assert warnings == []

    def test_cron_can_live_on_a_node_too(self) -> None:
        server = fleet_server()
        node = server.node_servers["web-2"]
        text = manifest("cron:\n  - {name: tick, command: ls, server: web-2}\n", NODE_APP)

        apply(text, server)

        assert "tick" in node.cron and "tick" not in server.cron
        assert make_plan(text, server).empty


class TestExport:
    def test_a_server_with_cron_and_a_fleet_is_written_down_and_plans_to_nothing(self) -> None:
        server = fleet_server()
        apply(manifest(CRON + FLEET, shop()), server)

        text = export(server)
        again = parse(text)

        assert [job.name for job in again.cron] == ["nightly-report"]
        assert [s.name for s in again.servers] == ["web-1", "web-2"]
        assert [p.domain for p in again.placements] == [SHOP]
        assert make_plan(again, server).empty

    def test_the_header_says_commands_are_exported_as_they_are(self) -> None:
        server = fleet_server()

        header = export(server).split("applications:")[0].lower()

        assert "cron" in header and "before you commit" in header

    def test_a_token_that_may_not_read_cron_or_the_fleet_still_exports_the_rest(self) -> None:
        server = fleet_server()
        server.cron_readable = False
        server.fleet_readable = False

        parsed = parse(export(server))

        assert [a.domain for a in parsed.applications] == [SHOP]
        assert parsed.cron == () and parsed.servers == ()
