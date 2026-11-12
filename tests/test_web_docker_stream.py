# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Looking into a container as it runs: its log followed live and a console in it.

The two streams that notices of 2026 against the tools that manage Docker were about: a member who may
open a shell in their own containers opens one in anyone's because nothing tied the id to its owner
before the permission was checked. Here the container is resolved to its owner first, by the gate, and
the permission is the one its owner asks for; the socket judges it all again, because a role changes in
the seconds between the call and the socket.

- **The log** is followed through the gate, line by line, with the credentials in it masked; a team
  member is told the container is not there.
- **The console** reuses the terminal (a single-use ticket, a session, a recording, the audit trail) with
  a target of its own, and is refused in what Noust runs for itself.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from noust.core import audit
from noust.core.runner import get_runner, set_runner
from noust.core.store import NoustStore
from noust.terminal import recording as recordings
from noust.terminal.sessions import SessionRegistry, reset_registry
from noust.terminal.settings import TerminalSettings
from noust.terminal.tickets import reset_tickets
from noust.web.api import docker as docker_api
from noust.web.jobs import JobManager
from noust.web.websockets.router import WS_SUBPROTOCOL, WS_TOKEN_PREFIX
from tests.support.docker_machine import full_id
from tests.support.docker_web import ACME, DockerWorld, build
from tests.support.teams_support import make_person


class Knobs:
    """The terminal's settings, which a test turns."""

    def __init__(self) -> None:
        self.settings = TerminalSettings()

    def set(self, **changes: Any) -> None:
        self.settings = replace(self.settings, **changes)

    def __call__(self) -> TerminalSettings:
        return self.settings


@pytest.fixture
def docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[DockerWorld]:
    JobManager.reset_instance()
    knobs = Knobs()
    monkeypatch.setattr("noust.web.api.terminal.load_settings", knobs)
    reset_registry(SessionRegistry(settings=knobs, runner=get_runner))
    reset_tickets()
    built = build(tmp_path, monkeypatch)
    built.knobs = knobs  # type: ignore[attr-defined]
    try:
        yield built
    finally:
        docker_api.reset_engine_cache()
        set_runner(None)
        reset_registry(None)
        reset_tickets()
        JobManager.reset_instance()
        NoustStore.reset_instance()


def subprotocols(docker: DockerWorld, who: str) -> list[str]:
    return [WS_SUBPROTOCOL, f"{WS_TOKEN_PREFIX}{docker.world.people[who].token}"]


def receive_until(ws: Any, kind: str, limit: int = 30) -> dict[str, Any]:
    for _ in range(limit):
        message = ws.receive_json()
        if message["type"] == kind:
            return message
    raise AssertionError(f"no {kind!r} frame arrived")


def wait_for(predicate: Any, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def ticket(docker: DockerWorld, who: str, ref: str, body: dict[str, Any] | None = None) -> Any:
    return docker.send(who, "POST", f"/containers/{ref}/terminal", json=body or {})


def open_console(docker: DockerWorld, who: str, issued: dict[str, Any]) -> Any:
    return docker.world.client.websocket_connect(
        f"/ws/terminal?terminal_ticket={issued['ticket']}", subprotocols=subprotocols(docker, who)
    )


class TestTheLogFollowedLive:
    def follow(
        self, docker: DockerWorld, who: str, ref: str, query: str = ""
    ) -> list[dict[str, Any]]:
        frames: list[dict[str, Any]] = []
        with docker.world.client.websocket_connect(
            f"/ws/docker/logs/{ref}{query}", subprotocols=subprotocols(docker, who)
        ) as ws:
            while True:
                frame = ws.receive_json()
                frames.append(frame)
                if frame["type"] in ("ended", "error"):
                    break
        return frames

    def test_it_says_which_container_and_whose_and_then_every_line(
        self, docker: DockerWorld
    ) -> None:
        docker.machine.boxes["adminer"].log = "first line\nsecond line"

        frames = self.follow(docker, "admin", "adminer")

        assert frames[0] == {
            "type": "connected",
            "container": "adminer",
            "owner": {"kind": "nobody", "subject": "", "project": ""},
        }
        assert [f["data"] for f in frames if f["type"] == "log"] == ["first line", "second line"]
        assert frames[-1]["type"] == "ended"

    def test_the_end_of_the_log_it_starts_from_is_asked_for_and_bounded(
        self, docker: DockerWorld
    ) -> None:
        self.follow(docker, "admin", "adminer", "?lines=30")

        asked = [c for c in docker.machine.calls if c[:3] == ("docker", "logs", "--follow")]
        assert asked == [
            ("docker", "logs", "--follow", "--tail", "30", "--timestamps", full_id("adminer"))
        ]

    @pytest.mark.parametrize("lines", [0, 5001])
    def test_a_backlog_that_makes_no_sense_is_refused_by_the_handshake(
        self, docker: DockerWorld, lines: int
    ) -> None:
        with pytest.raises(Exception):  # noqa: B017 - the handshake is closed with a policy code
            with docker.world.client.websocket_connect(
                f"/ws/docker/logs/adminer?lines={lines}", subprotocols=subprotocols(docker, "admin")
            ) as ws:
                ws.receive_json()

    def test_the_credentials_in_a_line_are_masked(self, docker: DockerWorld) -> None:
        frames = self.follow(docker, "admin", "portainer")

        lines = [f["data"] for f in frames if f["type"] == "log"]
        assert lines and all("hunter2hunter2" not in line for line in lines)
        assert lines[0] == "level=info msg=started"

    def test_what_belongs_to_an_application_is_followed_the_same_way(
        self, docker: DockerWorld
    ) -> None:
        docker.machine.boxes["acme-web-1"].log = "GET / 200"

        frames = self.follow(docker, "admin", "acme-web-1")

        assert frames[0]["owner"]["subject"] == ACME
        assert [f["data"] for f in frames if f["type"] == "log"] == ["GET / 200"]

    def test_what_is_not_there_is_an_error_and_nothing_is_started(
        self, docker: DockerWorld
    ) -> None:
        frames = self.follow(docker, "admin", "nothing")

        assert frames[-1]["type"] == "error" and "nothing" in frames[-1]["message"]
        assert not [c for c in docker.machine.calls if c[:2] == ("docker", "logs")]

    @pytest.mark.parametrize("who", ["alice", "omar", "bob"])
    def test_a_person_of_a_team_is_told_it_is_not_there_whatever_it_asks_for(
        self, docker: DockerWorld, who: str
    ) -> None:
        mine = self.follow(docker, who, "acme-web-1")
        anothers = self.follow(docker, who, "globex-web-1")
        missing = self.follow(docker, who, "nothing")

        assert mine[-1]["type"] == anothers[-1]["type"] == missing[-1]["type"] == "error"
        assert [f["message"].split("'")[0] for f in (mine[-1], anothers[-1], missing[-1])] == [
            "No container "
        ] * 3
        assert not [c for c in docker.machine.calls if c[:2] == ("docker", "logs")]

    def test_a_credential_that_does_not_read_docker_is_refused_at_the_door(
        self, docker: DockerWorld
    ) -> None:
        blind = make_person(docker.world.teams, "blind", "admin", permissions=["apps.read"])
        docker.world.people["blind"] = blind

        with pytest.raises(Exception):  # noqa: B017 - refused before any frame
            with docker.world.client.websocket_connect(
                "/ws/docker/logs/adminer", subprotocols=subprotocols(docker, "blind")
            ) as ws:
                ws.receive_json()

    def test_an_auditor_who_reads_docker_follows_any_log(self, docker: DockerWorld) -> None:
        frames = self.follow(docker, "aud", "adminer")

        assert frames[0]["type"] == "connected"

    def test_without_docker_it_says_so_in_the_doctors_words(self, docker: DockerWorld) -> None:
        docker.machine.installed = False

        frames = self.follow(docker, "admin", "adminer")

        assert frames[-1]["type"] == "error"
        assert "Docker" in frames[-1]["message"]

    def test_following_is_on_record_as_a_connection_to_that_container(
        self, docker: DockerWorld
    ) -> None:
        self.follow(docker, "admin", "adminer")

        events = audit.get_log().read(action="ws.connect")

        assert any(e["resource"] == "/ws/docker/logs/adminer" for e in events)


class TestOpeningAConsole:
    def test_an_administrator_gets_a_ticket_for_a_container_nobody_made(
        self, docker: DockerWorld
    ) -> None:
        response = ticket(docker, "admin", "portainer", {"rows": 30, "cols": 100})

        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["ticket"]) >= 40 and body["expires_in"] == 20
        assert body["path"] == "/ws/terminal" and body["recorded"] is True
        assert body["target"] == {
            "kind": "container",
            "domain": None,
            "service": None,
            "label": "portainer",
            "account": "container",
        }

    def test_one_of_an_application_says_which_application_and_service(
        self, docker: DockerWorld
    ) -> None:
        body = ticket(docker, "admin", "acme-web-1").json()

        assert body["target"]["domain"] == ACME and body["target"]["service"] == "web"
        assert body["target"]["label"] == "acme-web-1"

    def test_the_owner_is_resolved_before_the_permission_is_asked(
        self, docker: DockerWorld
    ) -> None:
        # A credential that may open a console in an application's containers and not in the server's.
        holds_apps = make_person(
            docker.world.teams, "apps-only", "admin", permissions=["docker.manage", "apps.terminal"]
        )
        docker.world.people["apps-only"] = holds_apps
        by_id = full_id("portainer")

        assert ticket(docker, "apps-only", "acme-web-1").status_code == 200
        assert ticket(docker, "apps-only", "portainer").status_code == 403
        assert ticket(docker, "apps-only", by_id).status_code == 403
        assert ticket(docker, "apps-only", by_id[:12]).status_code == 403

    def test_a_credential_with_only_the_servers_shell_may_not_enter_an_applications(
        self, docker: DockerWorld
    ) -> None:
        server_only = make_person(
            docker.world.teams,
            "server-only",
            "admin",
            permissions=["docker.manage", "server.terminal"],
        )
        docker.world.people["server-only"] = server_only

        assert ticket(docker, "server-only", "portainer").status_code == 200
        assert ticket(docker, "server-only", "acme-web-1").status_code == 403

    def test_a_refusal_for_lack_of_the_permission_is_on_record(self, docker: DockerWorld) -> None:
        holds_apps = make_person(
            docker.world.teams, "apps-only", "admin", permissions=["docker.manage", "apps.terminal"]
        )
        docker.world.people["apps-only"] = holds_apps

        ticket(docker, "apps-only", "portainer")

        denied = audit.get_log().read(action="terminal.denied")[0]
        assert denied["resource"] == "container:portainer"
        assert "server.terminal" in denied["details"]["reason"]

    def test_an_auditor_who_only_reads_docker_opens_nothing(self, docker: DockerWorld) -> None:
        assert ticket(docker, "aud", "portainer").status_code == 403

    @pytest.mark.parametrize("who", ["alice", "omar", "bob"])
    def test_a_person_of_a_team_is_refused_even_in_its_own_application(
        self, docker: DockerWorld, who: str
    ) -> None:
        assert ticket(docker, who, "acme-web-1").status_code == 403

    def test_a_console_asks_for_sudo_mode(self, docker: DockerWorld) -> None:
        from noust.web.server import get_token_manager

        account = get_token_manager().accounts.find("root-admin")
        issued = get_token_manager().create_api_token(
            "no-sudo", "admin", None, owner=account, allow_elevated=False
        )

        response = docker.world.client.post(
            "/api/docker/containers/portainer/terminal",
            json={},
            headers={"Authorization": f"Bearer {issued['token']}"},
        )

        assert response.status_code == 403 and response.json()["error"] == "elevation_required"

    def test_there_is_none_in_a_container_that_does_not_run(self, docker: DockerWorld) -> None:
        response = ticket(docker, "admin", "adminer")

        assert response.status_code == 400
        assert "not running" in response.json()["detail"]

    def test_there_is_none_in_what_is_not_there(self, docker: DockerWorld) -> None:
        assert ticket(docker, "admin", "nothing").status_code == 404

    def test_there_is_none_in_what_noust_runs_for_itself(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core.docker import owners

        monkeypatch.setattr(
            owners, "own_container_ids", lambda *a, **k: frozenset({full_id("portainer")})
        )

        response = ticket(docker, "admin", "portainer")

        assert response.status_code == 409 and response.json()["error"] == "docker_owned"

    def test_terminals_that_are_off_are_off_for_containers_too(self, docker: DockerWorld) -> None:
        docker.knobs.set(enabled=False)  # type: ignore[attr-defined]

        response = ticket(docker, "admin", "portainer")

        assert response.status_code == 403

    def test_a_shell_that_is_not_one_is_refused(self, docker: DockerWorld) -> None:
        assert ticket(docker, "admin", "portainer", {"shell": "rm -rf /"}).status_code == 400

    def test_the_ticket_is_on_record_without_the_ticket_in_it(self, docker: DockerWorld) -> None:
        body = ticket(docker, "admin", "portainer").json()

        event = audit.get_log().read(action="terminal.ticket")[0]

        assert event["resource"] == "container:portainer"
        assert body["ticket"] not in str(event)

    def test_what_a_row_offers_follows_the_permission_the_shell_needs(
        self, docker: DockerWorld
    ) -> None:
        apps_only = make_person(
            docker.world.teams,
            "apps-only",
            "admin",
            permissions=["docker.manage", "apps.terminal", "docker.read"],
        )
        docker.world.people["apps-only"] = apps_only

        rows = {c["name"]: c for c in docker.get("apps-only", "/containers").json()["items"]}

        assert rows["acme-web-1"]["can"]["console"] is True
        assert rows["portainer"]["can"]["console"] is False
        full = {c["name"]: c for c in docker.get("admin", "/containers").json()["items"]}
        assert (
            full["portainer"]["can"]["console"] is True
            and full["acme-web-1"]["can"]["console"] is True
        )


class TestTheConsoleSocket:
    def test_a_session_from_the_ticket_to_the_end(self, docker: DockerWorld) -> None:
        issued = ticket(docker, "admin", "portainer", {"rows": 30, "cols": 100}).json()
        with open_console(docker, "admin", issued) as ws:
            ready = receive_until(ws, "ready")
            pty = docker.machine.ptys[0]
            pty.feed("hello from the container\r\n")
            output = receive_until(ws, "output")
            ws.send_json({"type": "input", "data": "ls\r"})
            assert wait_for(lambda: pty.written == [b"ls\r"])
            ws.send_json({"type": "close"})
            ended = receive_until(ws, "ended")

        assert ready["target"]["kind"] == "container" and ready["target"]["label"] == "portainer"
        assert ready["recorded"] is True
        assert output["data"] == "hello from the container\r\n"
        assert ended["reason"] == "closed"
        assert pty.argv == (
            "docker",
            "exec",
            "-it",
            "-e",
            "TERM",
            "-e",
            "COLORTERM",
            full_id("portainer"),
            "sh",
        )

    def test_the_shell_is_the_one_that_was_asked_for_by_name(self, docker: DockerWorld) -> None:
        issued = ticket(docker, "admin", "portainer", {"shell": "bash"}).json()
        with open_console(docker, "admin", issued) as ws:
            receive_until(ws, "ready")
            assert docker.machine.ptys[0].argv[-1] == "bash"
            ws.send_json({"type": "close"})
            receive_until(ws, "ended")

    def test_the_session_is_recorded_and_on_record_as_a_container(
        self, docker: DockerWorld
    ) -> None:
        issued = ticket(docker, "admin", "portainer").json()
        with open_console(docker, "admin", issued) as ws:
            ready = receive_until(ws, "ready")
            docker.machine.ptys[0].feed("x")
            receive_until(ws, "output")
            ws.send_json({"type": "close"})
            receive_until(ws, "ended")

        start = audit.get_log().read(action="terminal.start")[0]
        assert start["resource"] == "container:portainer"
        assert start["details"]["session"] == issued["session"]
        assert recordings.read_recording(ready["recording"]).info.meta["kind"] == "container"
        assert recordings.read_recording(ready["recording"]).info.meta["label"] == "portainer"

    def test_a_ticket_opens_a_console_only_for_the_credential_that_minted_it(
        self, docker: DockerWorld
    ) -> None:
        issued = ticket(docker, "admin", "portainer").json()
        docker.world.people["other"] = make_person(docker.world.teams, "other", "admin")

        with open_console(docker, "other", issued) as ws:
            frame = ws.receive_json()

        assert frame["type"] == "error" and frame["code"] == "ticket"
        assert docker.machine.ptys == []

    def test_a_credential_that_lost_the_permission_since_the_ticket_is_refused_at_the_socket(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.web.permissions import Permission
        from noust.web.websockets import terminal as socket_module

        issued = ticket(docker, "admin", "portainer").json()
        held = socket_module.permissions_of
        monkeypatch.setattr(
            socket_module,
            "permissions_of",
            lambda session: held(session) - {Permission.SERVER_TERMINAL},
        )

        with open_console(docker, "admin", issued) as ws:
            frame = receive_until(ws, "error")

        assert frame["code"] == "forbidden" and "server.terminal" in frame["message"]
        assert docker.machine.ptys == []

    def test_a_container_that_went_away_is_said_and_no_shell_opens(
        self, docker: DockerWorld
    ) -> None:
        issued = ticket(docker, "admin", "portainer").json()
        del docker.machine.boxes["portainer"]

        with open_console(docker, "admin", issued) as ws:
            frame = receive_until(ws, "error")

        assert frame["code"] in ("forbidden", "unavailable") and "No container" in frame["message"]
        assert docker.machine.ptys == []

    def test_a_container_that_became_noust_s_own_is_refused_at_the_socket(
        self, docker: DockerWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core.docker import owners

        issued = ticket(docker, "admin", "portainer").json()
        monkeypatch.setattr(
            owners, "own_container_ids", lambda *a, **k: frozenset({full_id("portainer")})
        )

        with open_console(docker, "admin", issued) as ws:
            frame = receive_until(ws, "error")

        assert frame["code"] in ("forbidden", "unavailable")
        assert docker.machine.ptys == []
