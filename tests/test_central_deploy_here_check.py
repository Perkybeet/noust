# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The check a NAS's central makes before it deploys on the machine's Docker.

Deploying on the machine goes wrong later and far from its cause: a socket the central's account may
not open fails at the first deploy, and a folder that is not at the same path inside the container and
on the machine does not fail at all, it makes a stack keep its data somewhere nobody looks. The check
(``noust central deploy-here check``, ``POST /api/central/container-host/check``) finds each of those first and
says, with the sentence's code, what is so and the change that fixes what is not. What is held here is
that every failure is found with its own sentence and its fix, that the witness is proved from the
machine's side and never guessed, that nothing is left behind in the folder, and that a check that
could not be made is never reported as a pass.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from noust.central import deploy_check, machine
from noust.central.deploy_check import (
    STATUS_FAIL,
    STATUS_OK,
    STATUS_SKIPPED,
    STATUS_WARN,
    Report,
    Seams,
    SocketAccess,
    check_deploy_here,
)
from noust.cli.app import cli as root_cli
from noust.core import paths
from noust.core.config import Config
from noust.core.runner import FakeRunner, set_runner
from noust.core.server_text import CATALOG
from noust.core.store import NoustStore
from noust.managers.docker_volumes import HELPER_IMAGE
from noust.web.server import create_app
from tests.support.web_auth import make_config
from tests.support.web_central import elevate, sign_in

TOKEN = "0123456789abcdef0123456789abcdef"

CONTEXT = json.dumps(
    [{"Name": "default", "Endpoints": {"docker": {"Host": "unix:///var/run/docker.sock"}}}]
)


def version_json(*, server: str = "29.6.2", api: str = "1.52", min_api: str = "1.40") -> str:
    return json.dumps(
        {
            "Client": {"Version": "28.5.2", "ApiVersion": "1.51"},
            "Server": {"Version": server, "ApiVersion": api, "MinAPIVersion": min_api},
        }
    )


def engine(
    *,
    server: str = "29.6.2",
    api: str = "1.52",
    compose: str | None = "Docker Compose version v2.40.3",
    programs: tuple[str, ...] = ("docker",),
) -> FakeRunner:
    """A machine whose Docker answers, as ``docker`` is asked by the engine's doctor."""
    runner = FakeRunner().only_knows(*programs)
    runner.script(["docker", "version"], stdout=version_json(server=server, api=api))
    runner.script(["docker", "info"], stdout=json.dumps({"Driver": "overlay2", "DriverStatus": []}))
    if compose is None:
        runner.script(["docker", "compose", "version"], stderr="unknown command", exit_code=1)
    else:
        runner.script(["docker", "compose", "version"], stdout=compose)
    runner.script(["docker", "context", "inspect"], stdout=CONTEXT)
    runner.script(["docker", "inspect", "--type", "container"], stdout="sha256:" + "ab" * 32 + "\n")
    runner.script(["docker", "run"], stdout=TOKEN)
    return runner


def socket_of(group: str = "docker", gid: int = 999, *, accessible: bool = True) -> Any:
    def look(path: str) -> SocketAccess | None:
        return SocketAccess(path, is_socket=True, gid=gid, group=group, accessible=accessible)

    return look


def seams(**changes: Any) -> Seams:
    base: dict[str, Any] = {
        "socket_access": socket_of(),
        "token": lambda: TOKEN,
        "hostname": lambda: "noust",
    }
    return Seams(**{**base, **changes})


@pytest.fixture(autouse=True)
def fresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A hub that was told to deploy here, with its folders under this test's directory."""
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
    monkeypatch.setenv("NOUST_DEPLOY_HERE", "containers")
    Config.reset_instance()
    NoustStore.reset_instance()
    apps = tmp_path / "apps"
    apps.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("NOUST_APPS_DIR", str(apps))
    Config.reset_instance()
    monkeypatch.setattr(paths, "DATA_DIR", data)
    machine.reset_machine_facts()
    yield tmp_path
    machine.reset_machine_facts()
    Config.reset_instance()
    NoustStore.reset_instance()


def by_key(report: Report) -> dict[str, Any]:
    return {item.key: item for item in report.checks}


def run_calls(runner: FakeRunner) -> list[tuple[str, ...]]:
    return [call for call in runner.calls if call[:2] == ("docker", "run")]


class TestWhenEverythingIsRight:
    def test_the_report_is_ready_and_every_check_passes_in_the_order_they_are_made(self) -> None:
        report = check_deploy_here(engine(), seams=seams())

        assert report.ready
        assert [item.key for item in report.checks] == [
            "setting",
            "socket",
            "engine",
            "group",
            "apps_folder",
            "data_folder",
        ]
        assert [item.status for item in report.checks] == [STATUS_OK] * 6
        assert report.role == "hub"
        assert report.deploy_here == "containers"

    def test_an_old_nas_engine_fits_because_what_counts_is_the_api_it_speaks(self) -> None:
        """Synology's Docker is 24.0.2, API 1.43, and a client that does not negotiate breaks on it."""
        report = check_deploy_here(engine(server="24.0.2", api="1.43"), seams=seams())

        assert report.ready
        said = by_key(report)["engine"].said
        assert said.code == "deploy_here.engine.ok"
        assert said.params["version"] == "24.0.2"
        assert said.params["api"] == "1.43"

    def test_the_machine_s_engine_is_asked_afresh_and_once(self) -> None:
        runner = engine()

        check_deploy_here(runner, seams=seams())

        assert [call[:2] for call in runner.calls].count(("docker", "version")) == 1

    def test_the_report_says_each_check_as_a_sentence_with_a_code_and_its_english(self) -> None:
        said = check_deploy_here(engine(), seams=seams()).to_dict()

        assert said["ready"] is True
        assert said["deploy_here"] == "containers"
        for item in said["checks"]:
            assert set(item) == {"key", "status", "code", "params", "text", "hint"}
            assert item["code"] in CATALOG
            assert item["text"]


class TestTheSetting:
    def test_a_hub_that_was_not_told_says_how_to_tell_it_and_is_not_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("NOUST_DEPLOY_HERE")
        Config.reset_instance()

        report = check_deploy_here(engine(), seams=seams())

        setting = by_key(report)["setting"]
        assert setting.status == STATUS_FAIL
        assert setting.said.code == "deploy_here.setting.off"
        assert setting.said.hint is not None
        assert "NOUST_DEPLOY_HERE=containers" in setting.said.hint
        assert not report.ready
        # The rest is still looked at: whoever is setting this up learns everything at once.
        assert by_key(report)["engine"].status == STATUS_OK

    def test_a_server_deploys_here_without_being_asked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NOUST_CENTRAL_ROLE", "server")
        monkeypatch.delenv("NOUST_DEPLOY_HERE")
        Config.reset_instance()

        report = check_deploy_here(engine(), seams=seams())

        assert by_key(report)["setting"].said.code == "deploy_here.setting.server"
        assert report.ready


class TestTheSocket:
    def test_a_machine_whose_socket_was_not_mounted_says_what_to_mount(self) -> None:
        report = check_deploy_here(engine(), seams=seams(socket_access=lambda path: None))

        socket = by_key(report)["socket"]
        assert socket.status == STATUS_FAIL
        assert socket.said.code == "deploy_here.socket.missing"
        assert socket.said.params == {"socket": "/var/run/docker.sock"}
        assert socket.said.hint is not None
        assert "/var/run/docker.sock:/var/run/docker.sock" in socket.said.hint
        assert not report.ready

    def test_something_that_is_not_a_socket_is_not_one(self) -> None:
        def a_file(path: str) -> SocketAccess:
            return SocketAccess(path, is_socket=False, gid=0, group="root", accessible=True)

        report = check_deploy_here(engine(), seams=seams(socket_access=a_file))

        assert by_key(report)["socket"].said.code == "deploy_here.socket.missing"

    def test_what_cannot_be_checked_without_it_is_said_unchecked_never_passed(self) -> None:
        runner = engine()

        report = check_deploy_here(runner, seams=seams(socket_access=lambda path: None))

        for key in ("engine", "group"):
            assert by_key(report)[key].status == STATUS_SKIPPED, key
            assert by_key(report)[key].said.code == "deploy_here.unchecked.socket", key
        for key in ("apps_folder", "data_folder"):
            assert by_key(report)[key].status == STATUS_SKIPPED, key
            assert by_key(report)[key].said.code == "deploy_here.unchecked.docker", key
        assert run_calls(runner) == []

    def test_the_socket_is_where_the_programs_context_says_it_is(self) -> None:
        runner = engine()
        runner.script(
            ["docker", "context", "inspect"],
            stdout=json.dumps(
                [{"Endpoints": {"docker": {"Host": "unix:///volume1/@docker/docker.sock"}}}]
            ),
        )

        report = check_deploy_here(runner, seams=seams())

        assert by_key(report)["socket"].said.params == {"socket": "/volume1/@docker/docker.sock"}


class TestTheGroup:
    def test_a_socket_the_account_may_not_open_names_the_group_to_give_the_container(self) -> None:
        report = check_deploy_here(
            engine(), seams=seams(socket_access=socket_of("administrators", 101, accessible=False))
        )

        group = by_key(report)["group"]
        assert group.status == STATUS_FAIL
        assert group.said.code == "deploy_here.group.denied"
        assert group.said.params["group"] == "administrators"
        assert group.said.params["gid"] == 101
        assert group.said.params["user"]
        assert group.said.hint is not None
        assert "group_add" in group.said.hint
        assert "101" in group.said.hint
        assert not report.ready

    def test_the_engine_is_not_asked_through_a_socket_that_cannot_be_opened(self) -> None:
        runner = engine()

        report = check_deploy_here(runner, seams=seams(socket_access=socket_of(accessible=False)))

        assert by_key(report)["engine"].status == STATUS_SKIPPED
        assert run_calls(runner) == []

    def test_a_group_with_no_name_here_is_named_by_its_number(self) -> None:
        report = check_deploy_here(engine(), seams=seams(socket_access=socket_of("999", 999)))

        assert by_key(report)["group"].said.params == {"group": "999"}


class TestTheEngine:
    def test_an_image_without_docker_s_command_line_says_to_pull_the_current_one(self) -> None:
        report = check_deploy_here(engine(programs=()), seams=seams())

        item = by_key(report)["engine"]
        assert item.status == STATUS_FAIL
        assert item.said.code == "deploy_here.engine.no_client"
        assert not report.ready
        assert by_key(report)["apps_folder"].status == STATUS_SKIPPED

    def test_an_engine_under_what_noust_needs_is_said_in_the_doctor_s_words(self) -> None:
        report = check_deploy_here(engine(server="19.3.0", api="1.40"), seams=seams())

        item = by_key(report)["engine"]
        assert item.status == STATUS_FAIL
        assert item.said.code == "docker.engine.too_old"
        assert not report.ready

    def test_a_machine_without_compose_v2_is_not_offered(self) -> None:
        report = check_deploy_here(engine(compose=None), seams=seams())

        assert by_key(report)["engine"].said.code == "docker.compose.missing"
        assert not report.ready

    def test_a_daemon_that_does_not_answer_says_so_in_its_own_words(self) -> None:
        runner = engine()
        runner.script(
            ["docker", "version"],
            stderr="permission denied while trying to connect to the Docker daemon socket",
            exit_code=1,
        )

        report = check_deploy_here(runner, seams=seams())

        item = by_key(report)["engine"]
        assert item.said.code == "docker.engine.unreachable"
        assert "permission denied" in item.said.params["reason"]


class TestTheFolders:
    def test_the_witness_is_proved_from_the_machine_s_side_and_with_a_mount_that_never_creates(
        self, fresh: Path
    ) -> None:
        runner = engine()

        check_deploy_here(runner, seams=seams())

        argv = run_calls(runner)[0]
        assert "-v" not in argv and "--volume" not in argv
        mount = argv[argv.index("--mount") + 1]
        assert mount == f"type=bind,source={fresh / 'apps'},target=/witness,readonly"
        assert argv[argv.index("--network") + 1] == "none"
        assert argv[argv.index("--entrypoint") + 1] == "cat"
        assert argv[-1] == f"/witness/.noust-witness-{TOKEN}"
        assert "io.noust.helper=deploy-here-check" in argv

    def test_the_central_s_own_image_is_the_one_the_witness_starts_from(self) -> None:
        runner = engine()

        check_deploy_here(runner, seams=seams())

        argv = run_calls(runner)[0]
        assert argv[-2] == "sha256:" + "ab" * 32
        inspected = next(call for call in runner.calls if call[:2] == ("docker", "inspect"))
        assert inspected[-1] == "noust"

    def test_without_its_own_image_the_one_noust_runs_helpers_in_is_used(self) -> None:
        runner = engine()
        runner.script(
            ["docker", "inspect", "--type", "container"], stderr="No such object", exit_code=1
        )

        check_deploy_here(runner, seams=seams())

        assert run_calls(runner)[0][-2] == HELPER_IMAGE

    def test_a_name_with_a_comma_is_quoted_as_the_mount_option_needs(
        self, fresh: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        folder = fresh / "my,apps"
        folder.mkdir()
        monkeypatch.setenv("NOUST_APPS_DIR", str(folder))
        Config.reset_instance()
        runner = engine()

        check_deploy_here(runner, seams=seams())

        mount = run_calls(runner)[0][run_calls(runner)[0].index("--mount") + 1]
        assert mount == f'type=bind,"source={folder}",target=/witness,readonly'

    def test_nothing_is_left_in_the_folder_whichever_way_it_went(self, fresh: Path) -> None:
        for answer in (
            {"stdout": TOKEN},
            {"stderr": "bind source path does not exist", "exit_code": 125},
            {"stderr": "cat: can't open", "exit_code": 1},
        ):
            runner = engine()
            runner.script(["docker", "run"], **answer)

            check_deploy_here(runner, seams=seams())

            assert sorted(path.name for path in (fresh / "apps").iterdir()) == []
            assert sorted(path.name for path in (fresh / "data").iterdir()) == []

    def test_a_folder_the_machine_does_not_have_at_that_path_is_the_failure_it_exists_to_find(
        self,
    ) -> None:
        runner = engine()
        runner.script(
            ["docker", "run"],
            stderr='docker: Error response from daemon: invalid mount config for type "bind": '
            "bind source path does not exist: /data/apps.",
            exit_code=125,
        )

        report = check_deploy_here(runner, seams=seams())

        item = by_key(report)["apps_folder"]
        assert item.status == STATUS_FAIL
        assert item.said.code == "deploy_here.folder.missing_outside"
        assert item.said.hint is not None
        assert f"{item.said.params['folder']}:{item.said.params['folder']}" in item.said.hint
        assert not report.ready

    def test_a_different_folder_at_the_same_path_is_found_by_the_witness_not_being_there(
        self,
    ) -> None:
        runner = engine()
        runner.script(
            ["docker", "run"], stderr="cat: can't open '/witness/x': No such file", exit_code=1
        )

        report = check_deploy_here(runner, seams=seams())

        assert by_key(report)["apps_folder"].said.code == "deploy_here.folder.other"
        assert not report.ready

    def test_a_check_that_could_not_run_is_not_a_pass_and_carries_dockers_own_words(self) -> None:
        runner = engine()
        runner.script(
            ["docker", "run"],
            stderr="Unable to find image 'alpine' locally\ndocker: Error: no route to host",
            exit_code=125,
        )

        report = check_deploy_here(runner, seams=seams())

        item = by_key(report)["apps_folder"]
        assert item.status == STATUS_FAIL
        assert item.said.code == "deploy_here.folder.unverified"
        assert item.said.params["reason"] == "Unable to find image 'alpine' locally"

    def test_a_folder_that_is_not_inside_the_container_is_said_before_docker_is_asked(
        self, fresh: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NOUST_APPS_DIR", str(fresh / "nowhere"))
        Config.reset_instance()
        runner = engine()

        report = check_deploy_here(runner, seams=seams())

        item = by_key(report)["apps_folder"]
        assert item.said.code == "deploy_here.folder.missing_inside"
        assert item.said.hint is not None
        assert "nowhere:" in item.said.hint
        assert all("nowhere" not in " ".join(call) for call in run_calls(runner))

    @pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
    def test_a_folder_the_account_cannot_write_says_which_chown_gives_it_to_it(
        self, fresh: Path
    ) -> None:
        (fresh / "apps").chmod(0o555)
        try:
            report = check_deploy_here(engine(), seams=seams())
        finally:
            (fresh / "apps").chmod(0o755)

        item = by_key(report)["apps_folder"]
        assert item.said.code == "deploy_here.folder.unwritable"
        assert (
            item.said.params["command"] == f"chown -R {os.getuid()}:{os.getgid()} {fresh / 'apps'}"
        )

    def test_the_data_folder_only_warns_because_deploying_works_without_it(
        self, fresh: Path
    ) -> None:
        runner = engine()
        runner.script(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--label",
                "io.noust.helper=deploy-here-check",
                "--mount",
                f"type=bind,source={fresh / 'data'},target=/witness,readonly",
            ],
            stderr="bind source path does not exist",
            exit_code=125,
        )

        report = check_deploy_here(runner, seams=seams())

        assert by_key(report)["apps_folder"].status == STATUS_OK
        assert by_key(report)["data_folder"].status == STATUS_WARN
        assert by_key(report)["data_folder"].said.code == "deploy_here.folder.missing_outside"
        assert report.ready

    def test_there_is_no_data_folder_check_for_a_noust_that_keeps_its_data_in_the_system(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(paths, "DATA_DIR", None)

        report = check_deploy_here(engine(), seams=seams())

        assert [item.key for item in report.checks][-1] == "apps_folder"


class TestEverySentenceIsInTheCatalogWithItsFix:
    def test_a_failure_always_says_what_to_change(self) -> None:
        failures = [
            check_deploy_here(engine(), seams=seams(socket_access=lambda path: None)),
            check_deploy_here(engine(), seams=seams(socket_access=socket_of(accessible=False))),
            check_deploy_here(engine(programs=()), seams=seams()),
        ]
        for report in failures:
            for item in report.checks:
                if item.status == STATUS_FAIL:
                    assert item.said.hint, (item.key, item.said.code)

    def test_every_deploy_here_sentence_is_said_by_this_module(self) -> None:
        said = {
            code
            for code in CATALOG
            if code.startswith("deploy_here.") and not code.endswith(".hint")
        }
        source = Path(deploy_check.__file__).read_text(encoding="utf-8")

        assert {code for code in said if f'"{code}"' not in source} == set()


class TestTheCommand:
    @pytest.fixture(autouse=True)
    def a_machine_with_no_socket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Whatever the machine running the tests has: its own socket is nobody's business."""
        monkeypatch.setattr(
            deploy_check, "default_seams", lambda: seams(socket_access=lambda path: None)
        )

    @pytest.fixture
    def machine_runner(self) -> Iterator[FakeRunner]:
        runner = engine()
        set_runner(runner)
        yield runner
        set_runner(None)

    def test_it_says_each_check_and_what_to_change(self, machine_runner: FakeRunner) -> None:
        result = CliRunner().invoke(root_cli, ["central", "deploy-here", "check"])

        assert result.exit_code == 1, result.output
        assert "Docker's socket" in result.output
        assert "/var/run/docker.sock:/var/run/docker.sock" in result.output

    def test_json_is_the_report_the_api_gives(self, machine_runner: FakeRunner) -> None:
        result = CliRunner().invoke(root_cli, ["central", "deploy-here", "check", "--json"])

        said = json.loads(result.output)
        assert set(said) == {"ready", "role", "deploy_here", "checks"}
        assert [item["key"] for item in said["checks"]][:3] == ["setting", "socket", "engine"]
        assert said["ready"] is False
        assert result.exit_code == 1

    def test_a_rehearsal_asks_nothing_of_docker_and_writes_nothing(
        self, machine_runner: FakeRunner, fresh: Path
    ) -> None:
        result = CliRunner().invoke(root_cli, ["--dry-run", "central", "deploy-here", "check"])

        assert result.exit_code == 0, result.output
        assert "would" in result.output
        assert run_calls(machine_runner) == []
        assert list((fresh / "apps").iterdir()) == []

    def test_it_is_on_a_hub_and_on_a_server_because_the_container_is_the_one_that_needs_it(
        self, machine_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NOUST_CENTRAL_ROLE", "server")
        Config.reset_instance()

        result = CliRunner().invoke(root_cli, ["central", "deploy-here", "check", "--json"])

        assert json.loads(result.output)["role"] == "server"


class TestTheRoute:
    @pytest.fixture
    def client(self, sandbox: Path, runner: FakeRunner) -> TestClient:
        client = TestClient(create_app(make_config(sandbox)), client=("testclient", 50000))
        elevate(client, sign_in(client))
        return client

    def test_the_console_reads_the_same_report_as_the_command(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wanted = check_deploy_here(engine(), seams=seams())
        monkeypatch.setattr("noust.central.deploy_check.check_deploy_here", lambda *a, **k: wanted)

        response = client.post("/api/central/container-host/check")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ready"] is True
        assert [item["key"] for item in body["checks"]] == [
            "setting",
            "socket",
            "engine",
            "group",
            "apps_folder",
            "data_folder",
        ]
        assert body["checks"][0]["code"] == "deploy_here.setting.on"
        assert set(body["checks"][0]["params"]) == set()
        assert body["checks"][0]["text"] and body["checks"][0]["status"] == "ok"

    def test_the_failures_travel_with_their_fix(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wanted = check_deploy_here(engine(), seams=seams(socket_access=lambda path: None))
        monkeypatch.setattr("noust.central.deploy_check.check_deploy_here", lambda *a, **k: wanted)

        body = client.post("/api/central/container-host/check").json()

        assert body["ready"] is False
        socket = next(item for item in body["checks"] if item["key"] == "socket")
        assert socket["status"] == "fail"
        assert socket["hint"]
        assert socket["params"] == {"socket": "/var/run/docker.sock"}

    def test_nobody_is_let_in_without_signing_in(self, client: TestClient) -> None:
        stranger = TestClient(client.app, client=("testclient", 50001))

        assert stranger.post("/api/central/container-host/check").status_code == 401

    def test_it_is_the_administrators_and_the_machines_not_a_team_s(self) -> None:
        from noust.web.permissions import Permission
        from noust.web.permissions.roles import ADMIN, OPERATOR, VIEWER
        from noust.web.permissions.routes_central import ROUTES
        from noust.web.permissions.scopes import INSTANCE, rule_for

        permission = ROUTES[("POST", "/api/central/container-host/check")]

        assert permission == Permission.DOCKER_MANAGE
        assert permission in ADMIN
        assert permission not in OPERATOR and permission not in VIEWER
        rule = rule_for("POST", "/api/central/container-host/check")
        assert rule is not None and rule.scoping == INSTANCE
        # Unlocking is the central's own state and stays open to whoever may do it.
        unlock = rule_for("POST", "/api/central/unlock")
        assert unlock is not None and unlock.scoping != INSTANCE
