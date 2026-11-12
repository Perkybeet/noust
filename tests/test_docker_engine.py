# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which Docker there is and what does not fit in it: the engine's doctor (``noust.core.docker.engine``).

Panels that talk to an engine they do not control fail with the daemon's words ("client version 1.53
is too new", 2025-11 and 2026-03: Traefik, Portainer, Dockge, Dockhand, Dokploy, Coolify), and the
case that matters most to Noust is the reverse, a NAS whose Docker is older than a client that does
not negotiate. So Noust asks the engine its facts (version, API both sides, Compose, image store,
whether it runs without root, whose the socket is) and says in sentences, with a code each, what does
not fit: a Docker under what Noust needs, Compose 1 or no Compose, a Compose that cannot rehearse an
adoption, an image store Railpack cannot use.

What is held here is the reading of the real shapes of Docker's JSON (the new engine's and the old
engine's), that every command is declared read-only to a dry run, and the sentences.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from noust.core.docker import engine
from noust.core.docker.engine import EngineInfo, SocketInfo, diagnose, read_engine
from noust.core.runner import FakeRunner, is_read_only


def version_json(
    *,
    client: str = "29.1.3",
    client_api: str = "1.52",
    server: str | None = "29.1.3",
    server_api: str = "1.52",
    min_api: str = "1.44",
) -> str:
    """What ``docker version --format '{{json .}}'`` prints, as the modern engine prints it."""
    document: dict[str, Any] = {
        "Client": {"Version": client, "ApiVersion": client_api, "Context": "default"}
    }
    if server is not None:
        document["Server"] = {
            "Platform": {"Name": "Docker Engine - Community"},
            "Components": [
                {
                    "Name": "Engine",
                    "Version": server,
                    "Details": {"ApiVersion": server_api, "MinAPIVersion": min_api},
                }
            ],
            "Version": server,
            "ApiVersion": server_api,
            "MinAPIVersion": min_api,
        }
    return json.dumps(document)


def info_json(
    *,
    driver: str = "overlay2",
    containerd: bool = False,
    rootless: bool = False,
    root_dir: str = "/var/lib/docker",
) -> str:
    options = ["name=apparmor", "name=seccomp,profile=builtin", "name=cgroupns"]
    if rootless:
        options.append("name=rootless")
    status = [["driver-type", "io.containerd.snapshotter.v1"]] if containerd else []
    return json.dumps(
        {
            "ServerVersion": "29.1.3",
            "Driver": "overlayfs" if containerd else driver,
            "DriverStatus": status or [["Backing Filesystem", "extfs"]],
            "SecurityOptions": options,
            "DockerRootDir": root_dir,
            "OperatingSystem": "Ubuntu 24.04.3 LTS",
            "CgroupVersion": "2",
        }
    )


CONTEXT = json.dumps(
    [{"Name": "default", "Endpoints": {"docker": {"Host": "unix:///var/run/docker.sock"}}}]
)


def machine(
    *,
    version: str | None = None,
    info: str | None = None,
    compose: str | None = "Docker Compose version v2.40.3",
    context: str = CONTEXT,
    programs: tuple[str, ...] = ("docker",),
) -> FakeRunner:
    runner = FakeRunner().only_knows(*programs)
    runner.script(["docker", "version"], stdout=version if version is not None else version_json())
    runner.script(["docker", "info"], stdout=info if info is not None else info_json())
    if compose is None:
        runner.script(["docker", "compose", "version"], stderr="unknown command", exit_code=1)
    else:
        runner.script(["docker", "compose", "version"], stdout=compose)
    runner.script(["docker", "context", "inspect"], stdout=context)
    return runner


def socket_of_root(path: str) -> SocketInfo | None:
    return SocketInfo(owner="root", group="docker", mode="0660")


def codes(texts: Any) -> list[str]:
    return [text.code for text in texts]


class TestReadingTheEngine:
    def test_a_modern_engine_says_what_it_is(self) -> None:
        info = read_engine(machine(), stat_socket=socket_of_root)

        assert info == EngineInfo(
            installed=True,
            reachable=True,
            client_version="29.1.3",
            server_version="29.1.3",
            client_api="1.52",
            server_api="1.52",
            min_api="1.44",
            api="1.52",
            compose_version="2.40.3",
            image_store="classic",
            storage_driver="overlay2",
            rootless=False,
            host="unix:///var/run/docker.sock",
            socket="/var/run/docker.sock",
            socket_owner="root",
            socket_group="docker",
            socket_mode="0660",
            root_dir="/var/lib/docker",
            os="Ubuntu 24.04.3 LTS",
        )

    def test_the_api_that_is_spoken_is_the_lower_of_the_two(self) -> None:
        """A client newer than its engine speaks the engine's: that is what negotiation is."""
        nas = machine(version=version_json(server="24.0.2", server_api="1.43", min_api="1.12"))

        info = read_engine(nas, stat_socket=socket_of_root)

        assert (info.client_api, info.server_api, info.api) == ("1.52", "1.43", "1.43")

    def test_the_containerd_image_store_is_told_from_the_classic_one(self) -> None:
        info = read_engine(machine(info=info_json(containerd=True)), stat_socket=socket_of_root)

        assert (info.image_store, info.storage_driver) == ("containerd", "overlayfs")

    def test_rootless_and_the_users_own_socket(self) -> None:
        context = json.dumps(
            [{"Endpoints": {"docker": {"Host": "unix:///run/user/1000/docker.sock"}}}]
        )

        info = read_engine(
            machine(info=info_json(rootless=True), context=context),
            stat_socket=lambda path: SocketInfo("alice", "alice", "0600"),
        )

        assert info.rootless is True
        assert (info.socket, info.socket_owner) == ("/run/user/1000/docker.sock", "alice")

    def test_a_docker_reached_over_the_network_has_a_host_and_no_socket(self) -> None:
        context = json.dumps([{"Endpoints": {"docker": {"Host": "ssh://deploy@nas"}}}])

        info = read_engine(machine(context=context), stat_socket=socket_of_root)

        assert (info.host, info.socket, info.socket_owner) == ("ssh://deploy@nas", "", "")

    def test_the_old_engine_of_a_debian_12_has_no_compose_and_says_what_it_is(self) -> None:
        old = machine(
            version=version_json(
                client="20.10.24",
                client_api="1.41",
                server="20.10.24",
                server_api="1.41",
                min_api="1.12",
            ),
            compose=None,
            programs=("docker", "docker-compose"),
        )

        info = read_engine(old, stat_socket=socket_of_root)

        assert (info.server_version, info.api, info.compose_version) == ("20.10.24", "1.41", "")
        assert info.compose_v1 is True

    def test_a_daemon_that_does_not_answer_still_has_a_client_and_says_why(self) -> None:
        runner = machine(version=version_json(server=None))
        runner.script(
            ["docker", "version"],
            stdout=version_json(server=None),
            stderr="permission denied while trying to connect to the docker API at "
            "unix:///var/run/docker.sock\n",
            exit_code=1,
        )
        runner.script(["docker", "info"], stderr="permission denied", exit_code=1)

        info = read_engine(runner, stat_socket=socket_of_root)

        assert info.installed is True and info.reachable is False
        assert info.client_version == "29.1.3" and info.server_version == ""
        assert info.error.startswith("permission denied")

    def test_without_docker_nothing_is_asked(self) -> None:
        runner = FakeRunner().only_knows("systemctl")

        info = read_engine(runner, stat_socket=socket_of_root)

        assert info == EngineInfo(installed=False, reachable=False)
        assert runner.calls == []

    def test_text_that_is_not_json_is_a_daemon_that_cannot_be_read_and_not_a_crash(self) -> None:
        info = read_engine(machine(version="<html>", info="nope", context="[]"))

        assert info.installed is True and info.reachable is False

    def test_every_command_is_one_a_dry_run_may_run(self) -> None:
        runner = machine()

        read_engine(runner, stat_socket=socket_of_root)

        assert runner.calls, "nothing was asked"
        assert all(is_read_only(call) for call in runner.calls), runner.calls

    def test_every_question_has_a_deadline_and_the_formats_are_the_old_clients(self) -> None:
        runner = machine()

        read_engine(runner, stat_socket=socket_of_root)

        assert ("docker", "version", "--format", "{{json .}}") in runner.calls
        assert ("docker", "info", "--format", "{{json .}}") in runner.calls
        assert ("docker", "compose", "version") in runner.calls
        assert not any("json" in call and "--format" not in call for call in runner.calls)


class TestWhatDoesNotFit:
    def test_a_current_engine_with_compose_fits_and_says_what_it_is(self) -> None:
        report = diagnose(read_engine(machine(), stat_socket=socket_of_root))

        assert report.limits == ()
        assert codes(report.facts) == [
            "docker.engine.version",
            "docker.compose.version",
            "docker.engine.image_store",
            "docker.engine.socket",
        ]
        version = report.facts[0]
        assert version.params == {"version": "29.1.3", "api": "1.52"}
        assert version.text == "Docker 29.1.3, speaking API 1.52"
        assert report.ok is True

    def test_no_docker_is_the_one_thing_said(self) -> None:
        report = diagnose(EngineInfo(installed=False, reachable=False))

        assert codes(report.limits) == ["docker.engine.missing"]
        assert report.facts == ()
        assert report.ok is False
        assert report.limits[0].hint is not None

    def test_a_daemon_that_does_not_answer_gives_its_own_words_and_the_socket(self) -> None:
        info = EngineInfo(
            installed=True,
            reachable=False,
            client_version="29.1.3",
            compose_version="2.40.3",
            error="permission denied while trying to connect",
            socket="/var/run/docker.sock",
            socket_owner="root",
            socket_group="docker",
        )

        (limit,) = diagnose(info).limits

        assert limit.code == "docker.engine.unreachable"
        assert limit.params["reason"] == "permission denied while trying to connect"
        assert limit.params["socket"] == "/var/run/docker.sock"
        assert limit.hint is not None and "docker" in limit.hint

    def test_an_engine_under_what_noust_needs_says_both_numbers(self) -> None:
        old = machine(
            version=version_json(server="19.03.15", server_api="1.40", min_api="1.12"),
        )

        report = diagnose(read_engine(old, stat_socket=socket_of_root))

        (limit,) = [item for item in report.limits if item.code == "docker.engine.too_old"]
        assert limit.params == {"version": "19.03.15", "api": "1.40", "minimum": "1.41"}
        assert report.ok is False

    def test_the_api_a_nas_speaks_fits(self) -> None:
        """Synology's Docker 24.0.2 is API 1.43: a client of API 1.52 speaks it, and Noust does not mind."""
        nas = machine(version=version_json(server="24.0.2", server_api="1.43", min_api="1.12"))

        report = diagnose(read_engine(nas, stat_socket=socket_of_root))

        assert report.limits == ()
        assert report.facts[0].params["api"] == "1.43"

    def test_no_compose_and_compose_1_are_told_apart(self) -> None:
        nothing = diagnose(read_engine(machine(compose=None), stat_socket=socket_of_root))
        one = diagnose(
            read_engine(
                machine(compose=None, programs=("docker", "docker-compose")),
                stat_socket=socket_of_root,
            )
        )

        assert codes(nothing.limits) == ["docker.compose.missing"]
        assert codes(one.limits) == ["docker.compose.v1"]
        assert nothing.ok is False and one.ok is False

    def test_a_compose_that_cannot_rehearse_an_adoption_still_deploys(self) -> None:
        report = diagnose(
            read_engine(
                machine(compose="Docker Compose version v2.12.2"), stat_socket=socket_of_root
            )
        )

        (limit,) = report.limits
        assert limit.code == "docker.compose.too_old"
        assert limit.params == {"version": "2.12.2", "needed": "2.20"}
        assert report.ok is True

    def test_railpack_needs_the_containerd_store_and_a_classic_one_is_told(self) -> None:
        classic = diagnose(read_engine(machine(), stat_socket=socket_of_root))
        containerd = diagnose(
            read_engine(machine(info=info_json(containerd=True)), stat_socket=socket_of_root)
        )

        assert "docker.engine.no_containerd" not in codes(classic.limits)
        assert classic.facts[2].params["store"] == "classic"
        assert containerd.facts[2].params["store"] == "containerd"
        assert "docker.engine.no_containerd" not in codes(containerd.limits)

    def test_rootless_is_a_fact_with_the_users_socket(self) -> None:
        context = json.dumps(
            [{"Endpoints": {"docker": {"Host": "unix:///run/user/1000/docker.sock"}}}]
        )
        info = read_engine(
            machine(info=info_json(rootless=True), context=context),
            stat_socket=lambda path: SocketInfo("alice", "alice", "0600"),
        )

        facts = codes(diagnose(info).facts)

        assert "docker.engine.rootless" in facts
        assert "docker.engine.socket" in facts

    def test_a_docker_over_the_network_says_where_instead_of_a_socket(self) -> None:
        context = json.dumps([{"Endpoints": {"docker": {"Host": "tcp://10.0.0.5:2376"}}}])

        facts = diagnose(read_engine(machine(context=context), stat_socket=socket_of_root)).facts

        assert "docker.engine.remote" in codes(facts)
        assert "docker.engine.socket" not in codes(facts)

    def test_every_sentence_is_in_the_catalog_with_the_values_it_says_it_with(self) -> None:
        """``say`` refuses a code the catalog lacks and a value it has no place for."""
        reports = [
            diagnose(read_engine(machine(), stat_socket=socket_of_root)),
            diagnose(EngineInfo(installed=False, reachable=False)),
            diagnose(read_engine(machine(compose=None), stat_socket=socket_of_root)),
            diagnose(
                read_engine(
                    machine(compose="Docker Compose version v2.1.0"), stat_socket=socket_of_root
                )
            ),
        ]

        for report in reports:
            for text in (*report.facts, *report.limits):
                assert text.text
            for limit in report.limits:
                assert limit.hint, limit.code

    def test_the_report_is_json(self) -> None:
        report = diagnose(read_engine(machine(compose=None), stat_socket=socket_of_root))

        body = json.loads(json.dumps(report.to_dict()))

        assert body["ok"] is False
        assert body["limits"][0]["code"] == "docker.compose.missing"
        assert body["facts"][0]["code"] == "docker.engine.version"
        assert body["info"]["server_version"] == "29.1.3"


class TestWhereTheEngineStandsForTheZone:
    """The Docker zone looks at containers whenever the engine answers and speaks an API Noust does."""

    def report(self, **options: Any) -> engine.EngineReport:
        return diagnose(read_engine(machine(**options), stat_socket=socket_of_root))

    def test_an_engine_that_fits_is_ok_and_usable(self) -> None:
        found = self.report()

        assert (found.state, found.usable) == ("ok", True)

    def test_no_docker_is_missing_and_not_usable(self) -> None:
        found = diagnose(EngineInfo(installed=False, reachable=False))

        assert (found.state, found.usable) == ("missing", False)

    def test_a_daemon_that_does_not_answer_is_unreachable(self) -> None:
        found = self.report(version=version_json(server=None))

        assert (found.state, found.usable) == ("unreachable", False)

    def test_an_api_under_the_minimum_is_too_old_and_not_usable(self) -> None:
        found = self.report(
            version=version_json(client_api="1.40", server_api="1.40", min_api="1.12"),
        )

        assert (found.state, found.usable) == ("too_old", False)

    def test_a_machine_without_compose_is_still_a_machine_with_containers_to_look_at(self) -> None:
        found = self.report(compose=None)

        assert found.state == "ok" and found.usable is True
        assert found.ok is False  # stacks need Compose; containers do not

    def test_the_report_carries_both_in_its_dictionary(self) -> None:
        body = self.report().to_dict()

        assert body["state"] == "ok" and body["usable"] is True


def test_the_oldest_engine_noust_works_with_is_the_one_compose_v2_needs() -> None:
    assert engine.MIN_API == (1, 41)
    assert engine.MIN_COMPOSE == (2, 0)
    assert engine.ADOPTION_COMPOSE == (2, 20)


@pytest.mark.parametrize(
    ("text", "parsed"),
    [
        ("1.52", (1, 52)),
        ("1.41", (1, 41)),
        ("29.1.3", (29, 1, 3)),
        ("v2.40.3", (2, 40, 3)),
        ("2.20.0-desktop.1", (2, 20, 0)),
        ("", ()),
        ("garbage", ()),
    ],
)
def test_a_version_is_read_as_numbers_and_never_as_text(text: str, parsed: tuple[int, ...]) -> None:
    assert engine.version_tuple(text) == parsed
