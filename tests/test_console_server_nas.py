# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The NAS the console server models for the console of a central that deploys containers (``--deploy-here``).

The owner's central runs in a container on a NAS, and whether it deploys there is decided outside the console, so
the console has two states to be looked at and tested in. What is held here is that the state is asked for and
never a default, that the Docker it models answers the way a NAS's old engine does for what the deploy-here check and
the console read (the engine, the machine's name, a witness file read back through the machine's own mount), and that
the two faults a person can ask for are found by the same check a real central runs, each with its own sentence.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from noust.central import deploy_check, machine
from noust.core.config import Config
from noust.core.server_text import say
from scripts.console_server import container_host
from scripts.console_server.arguments import parse_args
from scripts.console_server.machine import INSTALLED_PROGRAMS
from scripts.console_server.runner import make_runner
from scripts.console_server.sandbox import Sandbox, write_config


@pytest.fixture(autouse=True)
def nothing_left_behind(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # The launcher replaces the check's one door to the machine; a test gives it back afterwards.
    monkeypatch.setattr(deploy_check, "default_seams", deploy_check.default_seams)
    monkeypatch.setattr(container_host, "_MACHINE", None)
    yield


def runner_for(tmp_path: Path):
    return make_runner({}, {}, {}, [], tmp_path / "units", programs=(*INSTALLED_PROGRAMS, "docker"))


class TestTheOptions:
    def test_a_hub_is_what_it_always_was_unless_it_is_told(self) -> None:
        args = parse_args(["--central-role", "hub", "--host-profile", "container"])

        assert args.deploy_here is None
        assert args.docker_fault is None

    def test_it_is_told_to_deploy_containers(self) -> None:
        args = parse_args(["--central-role", "hub", "--deploy-here", "containers"])

        assert args.deploy_here == "containers"

    def test_a_server_is_not_told_it_deploys_here_because_it_already_does(self) -> None:
        with pytest.raises(SystemExit):
            parse_args(["--deploy-here", "containers"])

    def test_a_fault_is_something_wrong_with_the_nas_so_it_needs_the_nas(self) -> None:
        with pytest.raises(SystemExit):
            parse_args(["--central-role", "hub", "--docker-fault", "folder"])

    def test_only_the_faults_the_check_can_find_are_offered(self) -> None:
        for fault in container_host.FAULTS:
            args = parse_args(
                ["--central-role", "hub", "--deploy-here", "containers", "--docker-fault", fault]
            )
            assert args.docker_fault == fault
        with pytest.raises(SystemExit):
            parse_args(
                ["--central-role", "hub", "--deploy-here", "containers", "--docker-fault", "nope"]
            )

    def test_the_setting_is_written_the_way_a_shell_writes_it(self, tmp_path: Path) -> None:
        sandbox = Sandbox(tmp_path)
        sandbox.config_file.parent.mkdir(parents=True, exist_ok=True)
        write_config(sandbox, central_role="hub", deploy_here="containers")

        written = yaml.safe_load(sandbox.config_file.read_text(encoding="utf-8"))
        assert written["central"] == {"role": "hub", "deploy_here": "containers"}

    def test_nothing_is_written_when_it_is_not_asked_for(self, tmp_path: Path) -> None:
        sandbox = Sandbox(tmp_path)
        sandbox.config_file.parent.mkdir(parents=True, exist_ok=True)
        write_config(sandbox, central_role="hub")

        written = yaml.safe_load(sandbox.config_file.read_text(encoding="utf-8"))
        assert written["central"] == {"role": "hub"}


class TestTheEngine:
    def test_a_machine_that_was_not_modelled_answers_nothing_for_docker(self) -> None:
        assert container_host.answer(["docker", "ps"]) is None

    def test_it_is_as_old_as_the_engines_nas_vendors_ship(self, tmp_path: Path) -> None:
        container_host.install(tmp_path / "apps")
        runner = runner_for(tmp_path)

        said = json.loads(runner.run(["docker", "version", "--format", "json"]).stdout)

        assert said["Server"]["Version"] == "24.0.2"
        assert said["Server"]["ApiVersion"] == "1.43"
        assert runner.run(["docker", "compose", "version"]).stdout.strip().endswith("v2.20.2")

    def test_the_machine_has_a_name_an_address_is_made_of(self, tmp_path: Path) -> None:
        container_host.install(tmp_path / "apps")
        runner = runner_for(tmp_path)

        assert runner.run(["docker", "info", "--format", "{{.Name}}"]).stdout.strip() == "nas-casa"

    def test_the_application_noust_deployed_runs_from_its_folder_and_the_rest_is_not_its(
        self, tmp_path: Path
    ) -> None:
        container_host.install(tmp_path / "apps")
        runner = runner_for(tmp_path)

        rows = [json.loads(line) for line in runner.run(["docker", "ps", "-a"]).stdout.splitlines()]
        by_name = {row["name"]: row for row in rows}

        ours = by_name["vault-casa-example-org-app-1"]
        assert ours["working_dir"] == str(tmp_path / "apps" / "vault-casa-example-org")
        assert by_name["nextcloud-app-1"]["working_dir"] == "/volume1/docker/nextcloud"

    def test_compose_lists_the_containers_of_a_project(self, tmp_path: Path) -> None:
        container_host.install(tmp_path / "apps")
        runner = runner_for(tmp_path)

        listed = runner.run(
            ["docker", "compose", "-p", "nextcloud", "ps", "-a", "--format", "json"]
        )

        rows = [json.loads(line) for line in listed.stdout.splitlines()]
        assert {row["Service"] for row in rows} == {"app", "db"}
        assert {row["State"] for row in rows} == {"running"}

    def test_stopping_a_project_is_seen_by_the_next_listing(self, tmp_path: Path) -> None:
        container_host.install(tmp_path / "apps")
        runner = runner_for(tmp_path)

        runner.run(["docker", "compose", "-p", "nextcloud", "stop"])
        listed = runner.run(
            ["docker", "compose", "-p", "nextcloud", "ps", "-a", "--format", "json"]
        )

        assert {json.loads(line)["State"] for line in listed.stdout.splitlines()} == {"exited"}


class TestWhatTheCheckFinds:
    @pytest.fixture(autouse=True)
    def a_central_that_deploys_here(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> Iterator[Path]:
        monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
        monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
        monkeypatch.setenv("NOUST_DEPLOY_HERE", "containers")
        apps = tmp_path / "apps"
        apps.mkdir()
        monkeypatch.setenv("NOUST_APPS_DIR", str(apps))
        monkeypatch.setattr("noust.core.paths.DATA_DIR", None)
        Config.reset_instance()
        machine.reset_machine_facts()
        yield apps
        machine.reset_machine_facts()
        Config.reset_instance()

    def checks(self, tmp_path: Path, fault: str | None) -> dict[str, tuple[str, str]]:
        container_host.install(tmp_path / "apps", fault=fault)
        report = deploy_check.check_deploy_here(runner_for(tmp_path))
        return {item.key: (item.status, item.said.code) for item in report.checks}

    def test_a_nas_that_is_right_passes_every_check(self, tmp_path: Path) -> None:
        found = self.checks(tmp_path, None)

        assert {status for status, _ in found.values()} == {deploy_check.STATUS_OK}
        assert found["engine"] == ("ok", "deploy_here.engine.ok")

    def test_a_folder_the_machine_sees_elsewhere_is_found_with_its_own_sentence(
        self, tmp_path: Path
    ) -> None:
        found = self.checks(tmp_path, "folder")

        assert found["apps_folder"] == ("fail", "deploy_here.folder.other")
        # The rest was fine: the check says exactly one thing is wrong.
        assert found["engine"][0] == found["group"][0] == "ok"

    def test_an_account_outside_the_sockets_group_is_told_which_group(self, tmp_path: Path) -> None:
        found = self.checks(tmp_path, "group")

        assert found["group"] == ("fail", "deploy_here.group.denied")
        # What cannot be asked until the group is given is said to be unchecked, never passed.
        assert found["engine"] == ("skipped", say("deploy_here.unchecked.socket").code)
        assert found["apps_folder"][0] == "skipped"

    def test_the_witness_is_removed_whichever_way_it_went(self, tmp_path: Path) -> None:
        self.checks(tmp_path, None)

        assert not list((tmp_path / "apps").glob(".noust-witness-*"))
