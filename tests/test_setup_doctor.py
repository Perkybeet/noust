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
            # The size of the machine and its web ports are described, not read: these tests are
            # about what the doctor says, and must not depend on the laptop they run on.
            resources=lambda: Resources(4096, 0, 2, (DiskFree(str(apps), 51200, 102400),)),
            listeners=lambda: [(80, ("nginx",)), (443, ("nginx",))],
            **extra,
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
    }


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
