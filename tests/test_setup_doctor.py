# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The installation check as data (checklist N-09): ``noust setup doctor`` and the console's page.

``tests/test_cli_setup.py`` pins what the command prints. What is pinned here is the report both
are drawn from: which item is a failure, which a warning, which the command that fixes it, and
that the console's variant does not try GitHub.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.runner import FakeRunner
from noust.deployers import compose_relay
from noust.managers import setup_doctor
from noust.managers.server.requirements import DiskFree, Resources
from noust.managers.setup_doctor import DoctorEnvironment
from noust.managers.store_location import StoreLocation
from noust.validators import ssh as ssh_module
from noust.web.api import system as system_api
from noust.web.api.auth import get_current_session


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A machine the test describes: programs installed, directories made, keys present."""
    installed: set[str] = set()
    active: set[str] = set()
    apps, logs, config = tmp_path / "apps", tmp_path / "log", tmp_path / "etc" / "config.yaml"
    monkeypatch.setattr(compose_relay, "leftover_relays", lambda: [])
    monkeypatch.setattr(ssh_module, "ssh_key_exists", lambda: (True, "/root/.ssh/id_ed25519"))
    monkeypatch.setattr(ssh_module, "test_ssh_connection", lambda host: (True, ""))

    def run(argv: list[str]) -> Any:
        unit = argv[-1]

        class Result:
            stdout = "active\n" if unit in active else "inactive\n"

        return Result()

    return {
        "installed": installed,
        "active": active,
        "apps": apps,
        "logs": logs,
        "config": config,
        "env": lambda **extra: DoctorEnvironment(
            command_exists=lambda name: name in installed,
            run=run,
            install_hint="apt-get install -y",
            apps_dir=apps,
            log_dir=logs,
            config_path=config,
            **{
                # The size of the machine and its web ports are described, not read: these tests
                # are about what the doctor says, and must not depend on the laptop they run on.
                "resources": lambda: Resources(4096, 0, 2, (DiskFree(str(apps), 51200, 102400),)),
                "listeners": lambda: [(80, ("nginx",)), (443, ("nginx",))],
                # Which kinds of application the store holds is described too, for the same reason.
                "app_types": lambda: {"nodejs"},
                # And where the store is: in the state directory, as a server keeps it.
                "store_location": lambda: StoreLocation(
                    tmp_path / "state" / "noust.db", tmp_path / "state"
                ),
                **extra,
            },
        ),
    }


def prepare(machine: dict[str, Any]) -> None:
    """Make the machine look ready: everything installed, running and present."""
    machine["installed"].update(
        {"git", "curl", "nginx", "node", "npm", "certbot", "python3", "pip3"}
    )
    machine["active"].add("nginx")
    for key in ("apps", "logs"):
        machine[key].mkdir(parents=True)
    machine["config"].parent.mkdir(parents=True)
    machine["config"].write_text("webserver: nginx", encoding="utf-8")


def items(report: setup_doctor.DoctorReport, title: str) -> list[setup_doctor.DoctorItem]:
    return list(next(s for s in report.sections if s.title == title).items)


def test_a_prepared_machine_is_ready_with_nothing_to_say(
    sandbox: dict[str, Any], runner: FakeRunner
) -> None:
    prepare(sandbox)

    report = setup_doctor.collect(sandbox["env"]())

    assert report.ready and report.issues == 0
    assert [s.title for s in report.sections][:4] == [
        "Platform",
        "Resources",
        "Core Dependencies",
        "Web Server",
    ]
    assert all(i.status != "fail" for s in report.sections for i in s.items)


def test_a_missing_program_is_a_failure_with_the_command_that_installs_it_here(
    sandbox: dict[str, Any], runner: FakeRunner
) -> None:
    prepare(sandbox)
    sandbox["installed"].discard("git")

    report = setup_doctor.collect(sandbox["env"]())

    git = items(report, "Core Dependencies")[0]
    assert (git.status, git.text, git.fix) == (
        "fail",
        "git: not installed",
        "sudo apt-get install -y git",
    )
    assert not report.ready
    assert report.issues == 1


def test_without_a_known_package_manager_the_fix_names_none(
    sandbox: dict[str, Any], runner: FakeRunner
) -> None:
    prepare(sandbox)
    sandbox["installed"].discard("curl")
    env = sandbox["env"]()
    env = DoctorEnvironment(
        command_exists=env.command_exists,
        run=env.run,
        install_hint="",
        apps_dir=env.apps_dir,
        log_dir=env.log_dir,
        config_path=env.config_path,
        resources=env.resources,
        listeners=env.listeners,
    )

    curl = items(setup_doctor.collect(env), "Core Dependencies")[1]

    assert curl.fix == "sudo your package manager curl"


def test_apache_stands_in_for_nginx_and_a_stopped_server_is_a_warning_with_its_start_command(
    sandbox: dict[str, Any], runner: FakeRunner
) -> None:
    prepare(sandbox)
    sandbox["installed"].discard("nginx")
    sandbox["installed"].add("apache2")
    sandbox["active"].clear()

    web = items(setup_doctor.collect(sandbox["env"]()), "Web Server")

    assert web[0].status == "ok" and "apache2" in web[0].text
    assert (web[1].status, web[1].fix) == ("warn", "sudo systemctl start apache2")


def test_no_web_server_at_all_is_a_failure(sandbox: dict[str, Any], runner: FakeRunner) -> None:
    prepare(sandbox)
    sandbox["installed"].discard("nginx")

    web = items(setup_doctor.collect(sandbox["env"]()), "Web Server")

    assert [(i.status, i.fix) for i in web] == [("fail", "sudo apt-get install -y nginx")]


def test_node_without_npm_fails_and_the_other_package_managers_are_optional(
    sandbox: dict[str, Any], runner: FakeRunner
) -> None:
    prepare(sandbox)
    sandbox["installed"].discard("npm")

    node = items(setup_doctor.collect(sandbox["env"]()), "Node.js Environment")

    assert node[1].status == "fail"
    assert {i.status for i in node[2:]} == {"info"}


def test_no_node_points_at_setup_init(sandbox: dict[str, Any], runner: FakeRunner) -> None:
    prepare(sandbox)
    sandbox["installed"].discard("node")

    node = items(setup_doctor.collect(sandbox["env"]()), "Node.js Environment")

    assert [(i.status, i.fix) for i in node] == [("fail", "sudo noust setup init")]


@pytest.mark.parametrize("kinds", [set(), {"docker-compose"}, {"python", "static", "container"}])
def test_no_node_is_not_a_failure_on_a_server_with_no_application_that_runs_on_it(
    sandbox: dict[str, Any], runner: FakeRunner, kinds: set[str]
) -> None:
    # A server that only runs Compose stacks (or nothing yet) does not need Node.js, and an
    # installation check that calls it an error sends the operator to install what nothing uses.
    prepare(sandbox)
    sandbox["installed"].discard("node")

    report = setup_doctor.collect(sandbox["env"](app_types=lambda: kinds))

    node = items(report, "Node.js Environment")
    assert [(i.status, i.fix) for i in node] == [("info", "sudo noust setup init")]
    assert "only" in node[0].text
    assert report.ready and report.issues == 0


@pytest.mark.parametrize("kind", ["nodejs", "nextjs", "vite", "nuxt", "monorepo"])
def test_no_node_still_fails_when_an_application_that_runs_on_it_is_registered(
    sandbox: dict[str, Any], runner: FakeRunner, kind: str
) -> None:
    prepare(sandbox)
    sandbox["installed"].discard("node")

    report = setup_doctor.collect(sandbox["env"](app_types=lambda: {"docker-compose", kind}))

    assert [i.status for i in items(report, "Node.js Environment")] == ["fail"]
    assert not report.ready


def test_a_store_the_doctor_cannot_read_does_not_clear_a_missing_node(
    sandbox: dict[str, Any], runner: FakeRunner
) -> None:
    # What cannot be proved unused stays an error: the answer must not depend on the store opening.
    from noust.core.exceptions import NoustError

    prepare(sandbox)
    sandbox["installed"].discard("node")

    def broken() -> set[str]:
        raise NoustError("cannot open the store")

    report = setup_doctor.collect(sandbox["env"](app_types=broken))

    assert [i.status for i in items(report, "Node.js Environment")] == ["fail"]


def test_the_default_reads_the_kinds_of_application_from_the_store(
    sandbox: dict[str, Any], runner: FakeRunner
) -> None:
    from noust.core.store import App, get_store

    prepare(sandbox)
    sandbox["installed"].discard("node")
    env = sandbox["env"](app_types=None)
    get_store().create_app(App(domain="shop.example.com", app_type="docker-compose"))

    assert [i.status for i in items(setup_doctor.collect(env), "Node.js Environment")] == ["info"]

    get_store().create_app(App(domain="www.example.com", app_type="nextjs"))

    assert [i.status for i in items(setup_doctor.collect(env), "Node.js Environment")] == ["fail"]


def test_node_that_is_installed_is_reported_whatever_the_applications(
    sandbox: dict[str, Any], runner: FakeRunner
) -> None:
    prepare(sandbox)

    node = items(
        setup_doctor.collect(sandbox["env"](app_types=lambda: set())), "Node.js Environment"
    )

    assert node[0].status == "ok"


def test_python_and_certbot_are_warnings_because_deployments_of_other_types_still_work(
    sandbox: dict[str, Any], runner: FakeRunner
) -> None:
    prepare(sandbox)
    sandbox["installed"] -= {"python3", "certbot"}

    report = setup_doctor.collect(sandbox["env"]())

    assert items(report, "Python Environment")[0].status == "warn"
    assert items(report, "SSL/TLS (Certbot)")[0].fix == "sudo apt-get install -y certbot"
    assert report.ready
    assert report.warnings == 2


def test_missing_directories_fail_and_a_missing_config_file_only_warns(
    sandbox: dict[str, Any], runner: FakeRunner
) -> None:
    prepare(sandbox)
    sandbox["config"].unlink()
    sandbox["logs"].rmdir()

    config = items(setup_doctor.collect(sandbox["env"]()), "Noust Configuration")

    assert [i.status for i in config] == ["ok", "fail", "warn"]
    assert all(i.fix == "sudo noust setup init" for i in config[1:])


def test_no_ssh_key_is_a_warning_with_the_command_that_makes_one(
    sandbox: dict[str, Any], runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare(sandbox)
    monkeypatch.setattr(ssh_module, "ssh_key_exists", lambda: (False, ""))

    ssh = items(setup_doctor.collect(sandbox["env"]()), "SSH Configuration")

    assert [(i.status, i.fix) for i in ssh] == [("warn", "noust setup ssh --generate")]


def test_github_is_only_tried_when_asked_and_its_refusal_is_not_counted(
    sandbox: dict[str, Any], runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare(sandbox)
    asked: list[str] = []
    monkeypatch.setattr(
        ssh_module, "test_ssh_connection", lambda host: (asked.append(host) or False, "denied")
    )

    quiet = setup_doctor.collect(sandbox["env"](include_ssh=False))
    assert asked == []
    assert items(quiet, "SSH Configuration")[1].text == "GitHub SSH: not tested here"

    loud = setup_doctor.collect(sandbox["env"](include_ssh=True))
    assert asked == ["github.com"]
    refused = items(loud, "SSH Configuration")[1]
    assert (refused.status, refused.counted) == ("warn", False)
    assert loud.warnings == 0


def test_a_relay_an_update_left_is_named_with_the_command_that_resolves_it(
    sandbox: dict[str, Any], runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare(sandbox)
    left = compose_relay.LeftoverRelay(
        domain="shop.example.com",
        what="nginx sends requests to a relay",
        fix="noust update shop.example.com",
    )
    monkeypatch.setattr(compose_relay, "leftover_relays", lambda: [left])

    relays = items(setup_doctor.collect(sandbox["env"]()), "Docker Compose relays")

    assert [(i.status, i.text, i.fix) for i in relays] == [
        (
            "warn",
            "shop.example.com: nginx sends requests to a relay",
            "noust update shop.example.com",
        )
    ]


def test_a_store_the_doctor_cannot_read_is_said_not_hidden(
    sandbox: dict[str, Any], runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.core.exceptions import NoustError

    prepare(sandbox)

    def broken() -> list[Any]:
        raise NoustError("cannot open the store")

    monkeypatch.setattr(compose_relay, "leftover_relays", broken)

    report = setup_doctor.collect(sandbox["env"]())

    note = items(report, "Docker Compose relays")[0]
    assert (note.status, "cannot open the store" in note.text) == ("info", True)
    assert report.ready


def test_the_json_form_names_status_fix_and_the_counts(
    sandbox: dict[str, Any], runner: FakeRunner
) -> None:
    prepare(sandbox)
    sandbox["installed"].discard("git")

    data = setup_doctor.collect(sandbox["env"]()).to_dict()

    assert (data["ready"], data["issues"]) == (False, 1)
    core = next(s for s in data["sections"] if s["title"] == "Core Dependencies")
    assert core["items"][0] == {
        "status": "fail",
        "text": "git: not installed",
        "fix": "sudo apt-get install -y git",
        "notes": [],
        "coded": {"code": "doctor.program.missing", "params": {"command": "git"}},
        "notes_coded": [],
    }
    assert (core["key"], core["explain_coded"]) == ("core", None)


def test_every_heading_has_a_key_the_console_translates_it_by(
    sandbox: dict[str, Any], runner: FakeRunner
) -> None:
    prepare(sandbox)

    report = setup_doctor.collect(sandbox["env"]())

    assert {s.title: s.key for s in report.sections} == {
        "Platform": "platform",
        "Resources": "resources",
        "Core Dependencies": "core",
        "Web Server": "web_server",
        "Node.js Environment": "node",
        "Python Environment": "python",
        "SSL/TLS (Certbot)": "tls",
        "Noust Configuration": "config",
        "SSH Configuration": "ssh",
    }


def test_what_the_doctor_says_of_the_machine_is_all_said_with_a_code(
    sandbox: dict[str, Any], runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Everything wrong at once: each sentence the console would show must be one it can translate.
    # A line is uncoded only when it is another module's words (why a platform is best effort).
    sandbox["config"].parent.mkdir(parents=True)
    monkeypatch.setattr(ssh_module, "ssh_key_exists", lambda: (False, ""))
    sandbox["installed"].add("apache2")
    env = sandbox["env"](
        resources=lambda: Resources(256, 512, 1, (DiskFree("/var/www/apps", 1024, 20480),)),
        listeners=lambda: [(80, ("caddy",)), (443, ())],
    )

    report = setup_doctor.collect(env)

    uncoded = [(s.title, i.text) for s in report.sections for i in s.items if i.coded is None]
    assert [row for row in uncoded if row[0] != "Platform"] == []
    for section in report.sections:
        for found in section.items:
            if found.coded is not None:
                # One source of words: the line the CLI prints is the sentence the console gets.
                assert found.text == found.coded.text
                assert found.notes == tuple(note.text for note in found.notes_coded)


def test_a_sentence_with_values_carries_them_apart_from_the_words(
    sandbox: dict[str, Any], runner: FakeRunner
) -> None:
    prepare(sandbox)
    env = sandbox["env"](
        resources=lambda: Resources(1024, 2048, 4, (DiskFree("/var/www/apps", 30720, 40960),))
    )

    resources = items(setup_doctor.collect(env), "Resources")

    assert resources[0].coded is not None
    assert resources[0].coded.to_dict() == {
        "code": "doctor.memory.ok_swap",
        "params": {"amount": "1 GB", "swap": "2 GB", "minimum": "1 GB"},
    }
    assert resources[1].coded is not None
    assert resources[1].coded.params == {"amount": 4, "minimum": 1}
    assert resources[2].coded is not None
    assert resources[2].coded.params == {
        "free": "30 GB",
        "total": "40 GB",
        "path": "/var/www/apps",
        "minimum": "10 GB",
    }


def test_a_note_under_a_line_is_a_sentence_too(sandbox: dict[str, Any], runner: FakeRunner) -> None:
    prepare(sandbox)
    env = sandbox["env"](resources=lambda: Resources(768, 0, 2, (DiskFree("/x", 30000, 40000),)))

    memory = items(setup_doctor.collect(env), "Resources")[0]

    assert [note.code for note in memory.notes_coded] == ["doctor.memory.note"]
    assert memory.notes[0].startswith("Builds are what need memory")


def test_the_default_environment_asks_the_machine_for_its_package_manager(
    runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "exists", lambda name: name == "dnf")

    env = DoctorEnvironment.default()

    assert env.install_hint == "dnf install"
    assert env.include_ssh is False


# -- the console's route


def test_the_route_answers_the_report_without_trying_github(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[DoctorEnvironment] = []

    def fake_collect(env: DoctorEnvironment | None = None) -> setup_doctor.DoctorReport:
        assert env is not None
        seen.append(env)
        return setup_doctor.DoctorReport(
            (
                setup_doctor.DoctorSection(
                    "Core Dependencies",
                    (
                        setup_doctor.DoctorItem(
                            "fail", "git: not installed", fix="sudo apt install git"
                        ),
                    ),
                ),
            )
        )

    monkeypatch.setattr(setup_doctor, "collect", fake_collect)
    app = FastAPI()
    app.include_router(system_api.router, prefix="/api/system")
    app.dependency_overrides[get_current_session] = lambda: {"session_id": "t", "scope": "read"}

    response = TestClient(app).get("/api/system/doctor")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["ready"], body["issues"], body["warnings"]) == (False, 1, 0)
    assert body["sections"][0]["items"][0]["fix"] == "sudo apt install git"
    assert seen[0].include_ssh is False
    # A line nobody coded (another module's words) is still a line, with no code.
    assert body["sections"][0]["items"][0]["coded"] is None


def test_the_route_sends_the_codes_the_report_carries(
    sandbox: dict[str, Any], runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare(sandbox)
    sandbox["installed"].discard("git")
    report = setup_doctor.collect(sandbox["env"]())
    monkeypatch.setattr(setup_doctor, "collect", lambda env=None: report)
    app = FastAPI()
    app.include_router(system_api.router, prefix="/api/system")
    app.dependency_overrides[get_current_session] = lambda: {"session_id": "t", "scope": "read"}

    body = TestClient(app).get("/api/system/doctor").json()

    core = next(section for section in body["sections"] if section["key"] == "core")
    assert core["items"][0]["coded"] == {
        "code": "doctor.program.missing",
        "params": {"command": "git"},
    }
    platform_section = next(s for s in body["sections"] if s["key"] == "platform")
    assert "explain_coded" in platform_section
