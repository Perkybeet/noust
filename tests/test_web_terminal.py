# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The terminal over HTTP and WebSocket, against the whole console.

Opening a shell in the browser is two steps (a call that mints a ticket, a
socket that redeems it) and each is held to everything the rest of the console
is held to, plus what only a shell needs. These tests are written as the people
who would misuse it: the team member who asks for another team's application,
the one who only holds a viewer's role, the one who reads a ticket out of a log
and replays it, the one who finds the server's shell switched off, and the
administrator whose role was changed between the call and the socket.
"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from starlette.websockets import WebSocketDisconnect

from noust.core import audit
from noust.core.runner import FakeRunner, get_runner
from noust.core.store import App, NoustStore
from noust.terminal import recording as recordings
from noust.terminal import targets
from noust.terminal.sessions import SessionRegistry, get_registry, reset_registry
from noust.terminal.settings import TerminalSettings
from noust.terminal.tickets import reset_tickets
from noust.web.auth import WS_CLOSE_FORBIDDEN
from noust.web.websockets.router import WS_SUBPROTOCOL, WS_TOKEN_PREFIX
from tests.teams_support import World, build_world, make_person

ACME = "shop.acme.example"
GLOBEX = "blog.globex.example"


class Knobs:
    """The terminal's settings, which a test turns."""

    def __init__(self) -> None:
        self.settings = TerminalSettings()

    def set(self, **changes: Any) -> None:
        self.settings = replace(self.settings, **changes)

    def __call__(self) -> TerminalSettings:
        return self.settings


@pytest.fixture
def knobs(monkeypatch: pytest.MonkeyPatch) -> Knobs:
    held = Knobs()
    monkeypatch.setattr("noust.web.api.terminal.load_settings", held)
    reset_registry(SessionRegistry(settings=held, runner=get_runner))
    reset_tickets()
    yield held
    reset_registry(None)
    reset_tickets()


@pytest.fixture(autouse=True)
def no_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(targets, "_unit_variables", lambda app: {"PORT": "3000"})


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner, knobs: Knobs):
    from noust.web.jobs import JobManager

    # The job manager is one per process: a job a test queued must not run in the next.
    JobManager.reset_instance()
    built = build_world(tmp_path, monkeypatch)
    # An auditor's token holds what the audit gives it: reading, the recordings among it.
    built.people["ada"] = make_person(
        built.teams, "ada", "auditor", permissions=["audit.read", "apps.read"]
    )
    try:
        yield built
    finally:
        JobManager.reset_instance()
        NoustStore.reset_instance()


def identity(world: World, who: str) -> tuple[str, str]:
    """
    Args:
        world: The world.
        who: One of its people.

    Returns:
        The ``sid`` and the audit label the console knows that person's token by.
    """
    from noust.web.auth import actor_label
    from noust.web.server import get_token_manager

    payload = get_token_manager().verify_api_token(world.people[who].token, "127.0.0.1")
    assert payload is not None
    return str(payload["sid"]), actor_label(payload)


def subprotocols(world: World, who: str) -> list[str]:
    return [WS_SUBPROTOCOL, f"{WS_TOKEN_PREFIX}{world.people[who].token}"]


def ticket_for(world: World, who: str, domain: str = ACME, **body: Any) -> dict[str, Any]:
    response = world.client.post(f"/api/apps/{domain}/terminal", json=body, headers=world.as_(who))
    assert response.status_code == 200, response.text
    return response.json()


def open_socket(world: World, who: str, ticket: str | None):
    query = f"?terminal_ticket={ticket}" if ticket else ""
    return world.client.websocket_connect(
        f"/ws/terminal{query}", subprotocols=subprotocols(world, who)
    )


def receive_until(ws: Any, kind: str, limit: int = 20) -> dict[str, Any]:
    for _ in range(limit):
        message = ws.receive_json()
        if message["type"] == kind:
            return message
    raise AssertionError(f"no {kind!r} frame arrived")


def wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def error_code(response: Any) -> str:
    return response.json().get("error", "")


class TestMintingATicket:
    def test_an_administrator_gets_a_ticket_for_an_application(self, world):
        body = ticket_for(world, "admin")

        assert len(body["ticket"]) >= 40
        assert body["expires_in"] == 20
        assert body["path"] == "/ws/terminal"
        assert body["resume"] is False
        assert body["target"] == {
            "kind": "app",
            "domain": ACME,
            "service": None,
            "label": ACME,
            "account": "www-data",
        }
        assert body["idle_seconds"] == TerminalSettings().idle_seconds
        assert body["recorded"] is True

    def test_it_is_on_record_without_the_ticket_in_it(self, world):
        body = ticket_for(world, "admin")

        event = audit.get_log().read(action="terminal.ticket")[0]

        assert event["actor"] == "token:root-admin-token"
        assert event["resource"] == f"app:{ACME}"
        assert event["details"]["session"] == body["session"]
        assert body["ticket"] not in str(event)

    def test_a_team_admin_opens_a_terminal_in_its_own_teams_application(self, world):
        assert ticket_for(world, "alice")["target"]["domain"] == ACME

    def test_another_teams_application_is_not_found_not_forbidden(self, world):
        response = world.client.post(
            f"/api/apps/{GLOBEX}/terminal", json={}, headers=world.as_("alice")
        )

        assert response.status_code == 404

    def test_a_viewer_of_the_team_cannot(self, world):
        response = world.client.post(
            f"/api/apps/{ACME}/terminal", json={}, headers=world.as_("dana")
        )

        assert response.status_code == 403
        assert error_code(response) == "permission_denied"
        assert "apps.terminal" in response.json()["detail"]

    def test_an_operator_who_deploys_but_does_not_read_secrets_cannot(self, world):
        response = world.client.post(
            f"/api/apps/{ACME}/terminal", json={}, headers=world.as_("omar")
        )

        assert response.status_code == 403

    def test_an_auditor_reads_everything_and_opens_nothing(self, world):
        response = world.client.post(
            f"/api/apps/{ACME}/terminal", json={}, headers=world.as_("ada")
        )

        assert response.status_code == 403

    def test_an_application_that_does_not_exist_is_not_found(self, world):
        response = world.client.post(
            "/api/apps/nope.example/terminal", json={}, headers=world.as_("admin")
        )

        assert response.status_code == 404

    @pytest.mark.parametrize(
        "body", [{"shell": "/bin/evil"}, {"shell": "python3"}, {"service": "web;id"}, {"rows": 0}]
    )
    def test_what_is_not_a_shell_a_service_or_a_size_is_refused(self, world, body):
        response = world.client.post(
            f"/api/apps/{ACME}/terminal", json=body, headers=world.as_("admin")
        )

        assert response.status_code in (400, 422)

    def test_a_static_site_says_it_has_no_process(self, world):
        world.store.create_app(
            App(
                domain="static.acme.example",
                app_type="static",
                app_path="/srv/static",
                is_static=True,
                project_id=world.targets["acme"].project_id,
                environment_id=world.targets["acme"].environment_id,
            )
        )

        response = world.client.post(
            "/api/apps/static.acme.example/terminal", json={}, headers=world.as_("admin")
        )

        assert response.status_code in (400, 500)
        assert "static" in response.text

    def test_terminals_can_be_turned_off(self, world, knobs):
        knobs.set(enabled=False)

        response = world.client.post(
            f"/api/apps/{ACME}/terminal", json={}, headers=world.as_("admin")
        )

        assert response.status_code == 403
        assert "turned off" in response.json()["detail"]
        assert audit.get_log().read(action="terminal.denied")

    def test_too_many_open_is_a_429_with_what_to_change(self, world, knobs):
        knobs.set(max_sessions=1)
        ticket = ticket_for(world, "admin")
        with open_socket(world, "admin", ticket["ticket"]) as ws:
            receive_until(ws, "ready")

            response = world.client.post(
                f"/api/apps/{ACME}/terminal", json={}, headers=world.as_("admin")
            )

            assert response.status_code == 429
            assert "terminal.max_sessions" in str(response.json())

    def test_a_session_cookie_that_is_not_in_sudo_mode_is_asked_to_confirm(self, world):
        # The master token signs in, but a cookie session is a browser tab that may
        # have been left open: sudo mode is what stands between it and a shell.
        token = world.master["Authorization"].removeprefix("Bearer ")
        login = world.client.post("/api/auth/login", json={"token": token})
        assert login.status_code == 200, login.text
        headers = {"X-Noust-CSRF": login.json()["csrf_token"]}

        response = world.client.post(f"/api/apps/{ACME}/terminal", json={}, headers=headers)

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"
        world.client.cookies.clear()


class TestTheServersShell:
    def test_it_is_off_until_an_administrator_turns_it_on(self, world):
        response = world.client.post("/api/server/terminal", json={}, headers=world.as_("admin"))

        assert response.status_code == 403
        assert "noust config set terminal.server.enabled true" in str(response.json())

    def test_once_on_an_administrator_gets_a_root_shell_ticket(self, world, knobs):
        knobs.set(server_enabled=True)

        response = world.client.post("/api/server/terminal", json={}, headers=world.as_("admin"))

        assert response.status_code == 200, response.text
        target = response.json()["target"]
        assert (target["kind"], target["account"], target["label"]) == ("server", "root", "server")

    def test_a_team_admin_never_gets_it_even_when_it_is_on(self, world, knobs):
        knobs.set(server_enabled=True)

        response = world.client.post("/api/server/terminal", json={}, headers=world.as_("alice"))

        assert response.status_code == 403

    def test_the_profile_keeps_it_off_unless_forced(self, world, knobs):
        knobs.set(server_enabled=True, ens=True)
        refused = world.client.post("/api/server/terminal", json={}, headers=world.as_("admin"))
        assert refused.status_code == 403
        assert "compliance controls" in refused.json()["detail"]

        knobs.set(server_forced=True)
        allowed = world.client.post("/api/server/terminal", json={}, headers=world.as_("admin"))
        assert allowed.status_code == 200

    def test_the_route_needs_a_second_person_when_approvals_are_on(self, world, knobs, monkeypatch):
        from noust.core.accounts import approvals as approvals_module
        from noust.core.accounts.approvals import ApprovalPolicy

        knobs.set(server_enabled=True)
        monkeypatch.setattr(
            approvals_module, "load_approval_policy", lambda: ApprovalPolicy(enabled=True)
        )
        # An API token cannot request an approval (a person decides), so the master
        # token's cookie session stands for the person asking.
        rule = approvals_module.route_requirement(
            "POST", "/api/server/terminal", {"server.terminal"}
        )

        assert rule == {"action": "terminal.server", "kind": "infrastructure", "when": "always"}


class TestTheStatus:
    def test_it_says_what_is_allowed_and_how_to_change_it(self, world):
        body = world.client.get("/api/terminal/status", headers=world.as_("dana")).json()

        assert body["enabled"] is True
        assert body["server"]["allowed"] is False
        assert "terminal.server.enabled" in body["server"]["how"]
        assert body["shells"][0] == "bash"
        assert body["max_sessions"] == TerminalSettings().max_sessions


class TestTheSocket:
    def test_a_session_from_the_ticket_to_the_end(self, world, runner):
        ticket = ticket_for(world, "admin", rows=30, cols=100)
        with open_socket(world, "admin", ticket["ticket"]) as ws:
            ready = receive_until(ws, "ready")
            pty = runner.ptys[0]
            pty.feed("hello from the app\r\n")
            output = receive_until(ws, "output")
            ws.send_json({"type": "input", "data": "whoami\r"})
            ws.send_json({"type": "resize", "rows": 50, "cols": 120})
            assert wait_for(lambda: pty.written == [b"whoami\r"] and pty.resizes == [(50, 120)])
            ws.send_json({"type": "close"})
            ended = receive_until(ws, "ended")

        assert ready["session"] == ticket["session"]
        assert ready["target"]["label"] == ACME
        assert ready["recorded"] is True and ready["resumed"] is False
        assert (ready["rows"], ready["cols"]) == (30, 100)
        assert output["data"] == "hello from the app\r\n"
        assert ended["reason"] == "closed"
        assert pty.argv[-1].endswith(("bash", "sh")) or "-i" in pty.argv
        start = audit.get_log().read(action="terminal.start")[0]
        end = audit.get_log().read(action="terminal.end")[0]
        assert start["details"]["session"] == end["details"]["session"] == ticket["session"]
        assert end["details"]["reason"] == "closed"
        events = recordings.read_recording(ready["recording"]).events
        assert [(kind, data) for _t, kind, data in events if kind in ("o", "i")] == [
            ("o", "hello from the app\r\n"),
            ("i", "whoami\r"),
        ]

    def test_the_shell_runs_as_the_application_in_its_sandbox(self, world, runner):
        ticket = ticket_for(world, "admin")
        with open_socket(world, "admin", ticket["ticket"]) as ws:
            receive_until(ws, "ready")

            argv, spec = runner.sandboxed[-1]
            assert spec.interactive and spec.user == "www-data" and spec.strict is False
            assert runner.calls[-1][0] == "systemd-run" and "--pty" in runner.calls[-1]
            ws.send_json({"type": "close"})
            receive_until(ws, "ended")

    def test_a_ticket_is_spent_by_its_first_use(self, world):
        ticket = ticket_for(world, "admin")
        with open_socket(world, "admin", ticket["ticket"]) as ws:
            receive_until(ws, "ready")
            ws.send_json({"type": "close"})
            receive_until(ws, "ended")

        with open_socket(world, "admin", ticket["ticket"]) as ws:
            refusal = ws.receive_json()
            assert refusal["type"] == "error" and refusal["code"] == "ticket"
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
        assert closed.value.code == WS_CLOSE_FORBIDDEN
        assert audit.get_log().read(action="terminal.denied")

    def test_without_a_ticket_there_is_no_shell(self, world, runner):
        with open_socket(world, "admin", None) as ws:
            refusal = ws.receive_json()

        assert refusal["code"] == "ticket"
        assert runner.ptys == []

    def test_somebody_elses_ticket_does_not_open_a_shell_for_you(self, world, runner):
        ticket = ticket_for(world, "alice")

        with open_socket(world, "admin", ticket["ticket"]) as ws:
            refusal = ws.receive_json()

        assert refusal["code"] == "ticket"
        assert runner.ptys == []
        # ... and it is burned for its owner too: it was read out of somewhere.
        with open_socket(world, "alice", ticket["ticket"]) as ws:
            assert ws.receive_json()["code"] == "ticket"

    def test_a_ticket_for_another_teams_application_is_refused_at_the_socket_too(
        self, world, runner
    ):
        from noust.terminal.tickets import get_tickets

        # A ticket the minting route would never have issued: the socket does not rely on that.
        ticket, _ = get_tickets().mint(
            kind="app",
            domain=GLOBEX,
            service=None,
            shell_name=None,
            rows=24,
            cols=80,
            sid=identity(world, "alice")[0],
            actor=identity(world, "alice")[1],
            client_ip="testclient",
        )

        with open_socket(world, "alice", ticket) as ws:
            refusal = ws.receive_json()

        assert refusal["code"] == "forbidden"
        assert "not found" in refusal["message"]
        assert runner.ptys == []

    def test_a_role_that_lost_the_permission_does_not_open_what_it_was_ticketed_for(
        self, world, runner
    ):
        ticket = ticket_for(world, "alice")
        world.teams.add_member("acme", "alice", "viewer")  # demoted in the seconds between

        # The handshake is refused before the handler sees it, by the route's permission.
        with pytest.raises(WebSocketDisconnect) as refused:
            with open_socket(world, "alice", ticket["ticket"]) as ws:
                ws.receive_json()

        assert refused.value.code == WS_CLOSE_FORBIDDEN
        assert runner.ptys == []

    def test_the_servers_shell_needs_what_a_root_shell_needs_at_the_socket_as_well(
        self, world, knobs, runner
    ):
        from noust.terminal.tickets import get_tickets

        knobs.set(server_enabled=True)
        ticket, _ = get_tickets().mint(
            kind="server",
            domain=None,
            service=None,
            shell_name=None,
            rows=24,
            cols=80,
            sid=identity(world, "alice")[0],
            actor=identity(world, "alice")[1],
            client_ip="testclient",
        )

        with open_socket(world, "alice", ticket) as ws:
            refusal = ws.receive_json()

        assert refusal["code"] == "forbidden"
        assert runner.ptys == []

    def test_the_servers_shell_opens_for_an_administrator(self, world, knobs, runner):
        knobs.set(server_enabled=True)
        ticket = world.client.post(
            "/api/server/terminal", json={}, headers=world.as_("admin")
        ).json()

        with open_socket(world, "admin", ticket["ticket"]) as ws:
            ready = receive_until(ws, "ready")
            assert ready["target"]["account"] == "root"
            assert runner.ptys[0].argv[0].endswith("sh") or runner.ptys[0].argv[0].endswith("bash")
            ws.send_json({"type": "close"})
            receive_until(ws, "ended")

        assert audit.get_log().read(action="terminal.start")[0]["resource"] == "server"

    def test_a_shell_that_cannot_be_recorded_does_not_open(self, world, monkeypatch, runner):
        monkeypatch.setattr(
            recordings,
            "SessionRecorder",
            lambda *a, **k: (_ for _ in ()).throw(PermissionError("read-only")),
        )
        ticket = ticket_for(world, "admin")

        with open_socket(world, "admin", ticket["ticket"]) as ws:
            refusal = ws.receive_json()

        assert refusal["code"] == "unavailable"
        assert "recorded" in refusal["message"]
        assert runner.ptys == []

    def test_an_input_frame_that_is_not_json_is_ignored_and_the_session_goes_on(
        self, world, runner
    ):
        ticket = ticket_for(world, "admin")
        with open_socket(world, "admin", ticket["ticket"]) as ws:
            receive_until(ws, "ready")
            ws.send_text("this is not json")
            ws.send_text("[1, 2, 3]")
            ws.send_json({"type": "ping"})
            assert receive_until(ws, "pong")
            ws.send_json({"type": "close"})
            receive_until(ws, "ended")

    def test_the_program_ending_ends_the_socket_with_why(self, world, runner):
        ticket = ticket_for(world, "admin")
        with open_socket(world, "admin", ticket["ticket"]) as ws:
            receive_until(ws, "ready")
            runner.ptys[0].die(4)
            ended = receive_until(ws, "ended")

        assert ended == {"type": "ended", "reason": "exited", "exit_code": 4}

    def test_a_cross_site_page_cannot_open_one(self, world, runner):
        ticket = ticket_for(world, "admin")

        with pytest.raises(WebSocketDisconnect) as refused:
            with world.client.websocket_connect(
                f"/ws/terminal?terminal_ticket={ticket['ticket']}",
                subprotocols=subprotocols(world, "admin"),
                headers={"Origin": "https://evil.example"},
            ):
                pass

        assert refused.value.code == WS_CLOSE_FORBIDDEN
        assert runner.ptys == []


class TestComingBack:
    def test_a_dropped_connection_comes_back_to_the_same_shell(self, world, runner):
        first = ticket_for(world, "admin")
        with open_socket(world, "admin", first["ticket"]) as ws:
            receive_until(ws, "ready")
        # the socket is gone; the shell is not
        registry = get_registry()
        assert wait_for(lambda: registry.get(first["session"]) is not None)
        assert wait_for(lambda: registry.get(first["session"]).attached is False)
        runner.ptys[0].feed("printed while away\r\n")
        time.sleep(0.3)

        again = ticket_for(world, "admin", resume=first["session"])
        assert again["resume"] is True and again["session"] == first["session"]
        with open_socket(world, "admin", again["ticket"]) as ws:
            ready = receive_until(ws, "ready")
            output = receive_until(ws, "output")
            ws.send_json({"type": "close"})
            receive_until(ws, "ended")

        assert ready["resumed"] is True and ready["lost"] is False
        assert output["data"] == "printed while away\r\n"
        assert len(runner.ptys) == 1  # no second shell was ever started

    def test_only_the_person_who_opened_it_can_come_back_to_it(self, world, runner):
        first = ticket_for(world, "alice")
        with open_socket(world, "alice", first["ticket"]) as ws:
            receive_until(ws, "ready")

        response = world.client.post(
            f"/api/apps/{ACME}/terminal",
            json={"resume": first["session"]},
            headers=world.as_("admin"),
        )

        assert response.status_code == 404
        assert "no longer open" in response.json()["detail"]
        get_registry().kill(first["session"], by=None)  # type: ignore[arg-type]

    def test_a_session_that_ended_while_away_is_gone(self, world, runner):
        first = ticket_for(world, "admin")
        with open_socket(world, "admin", first["ticket"]) as ws:
            receive_until(ws, "ready")
        runner.ptys[0].die(0)
        assert get_registry().get(first["session"]) is None or wait_for(
            lambda: get_registry().get(first["session"]) is None
        )

        response = world.client.post(
            f"/api/apps/{ACME}/terminal",
            json={"resume": first["session"]},
            headers=world.as_("admin"),
        )

        assert response.status_code == 404


class TestListingAndEnding:
    def open_two(self, world):
        mine = ticket_for(world, "alice")
        theirs = world.client.post(
            f"/api/apps/{GLOBEX}/terminal", json={}, headers=world.as_("admin")
        ).json()
        return mine, theirs

    def test_a_team_member_sees_the_sessions_of_its_applications_only(self, world):
        mine, theirs = self.open_two(world)
        with open_socket(world, "alice", mine["ticket"]) as first:
            receive_until(first, "ready")
            with open_socket(world, "admin", theirs["ticket"]) as second:
                receive_until(second, "ready")

                seen = world.client.get("/api/terminal/sessions", headers=world.as_("alice"))
                everything = world.client.get("/api/terminal/sessions", headers=world.as_("admin"))

                assert [s["domain"] for s in seen.json()["sessions"]] == [ACME]
                assert {s["domain"] for s in everything.json()["sessions"]} == {ACME, GLOBEX}
                first.send_json({"type": "close"})
                second.send_json({"type": "close"})
                receive_until(first, "ended")
                receive_until(second, "ended")

    def test_a_team_admin_ends_its_own_sessions_and_not_another_teams(self, world):
        mine, theirs = self.open_two(world)
        with open_socket(world, "alice", mine["ticket"]) as first:
            receive_until(first, "ready")
            with open_socket(world, "admin", theirs["ticket"]) as second:
                receive_until(second, "ready")

                other = world.client.delete(
                    f"/api/terminal/sessions/{theirs['session']}", headers=world.as_("alice")
                )
                own = world.client.delete(
                    f"/api/terminal/sessions/{mine['session']}", headers=world.as_("alice")
                )
                ended = receive_until(first, "ended")

                assert other.status_code == 404
                assert own.status_code == 200 and own.json() == {
                    "id": mine["session"],
                    "ended": True,
                }
                assert ended["reason"] == "killed"
                second.send_json({"type": "close"})
                receive_until(second, "ended")
        kill = audit.get_log().read(action="terminal.kill")[0]
        assert kill["actor"] == "token:alice-token"
        assert kill["details"]["opened_by"] == "token:alice-token"

    def test_a_viewer_cannot_list_or_end(self, world):
        assert (
            world.client.get("/api/terminal/sessions", headers=world.as_("dana")).status_code == 403
        )
        assert (
            world.client.delete(
                f"/api/terminal/sessions/{'0' * 16}", headers=world.as_("dana")
            ).status_code
            == 403
        )

    def test_the_servers_sessions_are_not_a_teams_to_see(self, world, knobs):
        knobs.set(server_enabled=True)
        ticket = world.client.post(
            "/api/server/terminal", json={}, headers=world.as_("admin")
        ).json()
        with open_socket(world, "admin", ticket["ticket"]) as ws:
            receive_until(ws, "ready")

            seen = world.client.get("/api/terminal/sessions", headers=world.as_("alice")).json()
            hidden = world.client.delete(
                f"/api/terminal/sessions/{ticket['session']}", headers=world.as_("alice")
            )

            assert seen["sessions"] == []
            assert hidden.status_code == 404
            ws.send_json({"type": "close"})
            receive_until(ws, "ended")


class TestTheRecordings:
    def record_one(self, world, runner) -> str:
        ticket = ticket_for(world, "admin")
        with open_socket(world, "admin", ticket["ticket"]) as ws:
            ready = receive_until(ws, "ready")
            runner.ptys[0].feed("\x1b[31mred\x1b[0m output\r\n")
            receive_until(ws, "output")
            ws.send_json({"type": "input", "data": "ls\r"})
            assert wait_for(lambda: runner.ptys[0].written)
            ws.send_json({"type": "close"})
            receive_until(ws, "ended")
        return ready["recording"]

    def test_an_auditor_lists_and_reads_them(self, world, runner):
        identifier = self.record_one(world, runner)

        listed = world.client.get("/api/terminal/recordings", headers=world.as_("ada")).json()
        read = world.client.get(
            f"/api/terminal/recordings/{identifier}", headers=world.as_("ada")
        ).json()

        assert [r["id"] for r in listed["recordings"]] == [identifier]
        assert listed["recordings"][0]["status"] == "complete"
        assert listed["recordings"][0]["domain"] == ACME
        assert read["intact"] is True
        assert "red output" in read["transcript"] and "\x1b" not in read["transcript"]
        assert "> ls<enter>" in read["transcript"]
        assert ["o", "i"] == [event[1] for event in read["events"]][:2]
        assert audit.get_log().read(action="terminal.recording.read")[0]["sensitive"] is True

    def test_a_team_admin_does_not_read_what_the_server_recorded(self, world, runner):
        identifier = self.record_one(world, runner)

        assert (
            world.client.get("/api/terminal/recordings", headers=world.as_("alice")).status_code
            == 403
        )
        assert (
            world.client.get(
                f"/api/terminal/recordings/{identifier}", headers=world.as_("alice")
            ).status_code
            == 403
        )

    def test_a_filter_narrows_them(self, world, runner):
        self.record_one(world, runner)

        none = world.client.get(
            "/api/terminal/recordings?domain=other.example", headers=world.as_("ada")
        ).json()
        server = world.client.get(
            "/api/terminal/recordings?kind=server", headers=world.as_("ada")
        ).json()
        bad = world.client.get("/api/terminal/recordings?kind=db", headers=world.as_("ada"))

        assert none["recordings"] == [] and server["recordings"] == []
        assert bad.status_code == 400

    def test_an_id_that_is_a_path_is_refused(self, world):
        response = world.client.get(
            "/api/terminal/recordings/..%2F..%2Fetc%2Fpasswd", headers=world.as_("ada")
        )

        assert response.status_code in (400, 404)

    def test_a_recording_that_does_not_exist_is_an_error_with_a_way_forward(self, world):
        response = world.client.get(
            f"/api/terminal/recordings/20260101T000000Z-{'a' * 16}", headers=world.as_("ada")
        )

        assert response.status_code == 404
        assert "audit recordings" in str(response.json())


class TestOneOffCommands:
    def wait_for_job(self, world, job_id: str, who: str = "admin") -> dict[str, Any]:
        for _ in range(500):
            job = world.client.get(f"/api/jobs/{job_id}", headers=world.as_(who)).json()
            if job["status"] in ("completed", "failed", "cancelled"):
                return job
            time.sleep(0.02)
        raise AssertionError("the command never finished")

    def test_it_runs_as_a_job_and_its_output_is_the_log(self, world, runner):
        runner.script(["rails", "runner"], stdout="42\nthe answer\n")

        response = world.client.post(
            f"/api/apps/{ACME}/exec",
            json={"argv": ["rails", "runner", "puts 6*7"], "timeout": 60},
            headers=world.as_("admin"),
        )
        assert response.status_code == 202, response.text
        job = self.wait_for_job(world, response.json()["job_id"])
        log = world.client.get(
            f"/api/jobs/{response.json()['job_id']}/log", headers=world.as_("admin")
        ).json()

        assert job["status"] == "completed"
        assert job["result"]["exit_code"] == 0
        assert "42" in str(log) and "the answer" in str(log)
        assert runner.sandboxed[-1][0] == ("rails", "runner", "puts 6*7")
        assert runner.sandboxed[-1][1].user == "www-data"

    def test_a_command_that_fails_is_a_failed_job_with_its_status_and_output(self, world, runner):
        runner.script(["rake"], exit_code=2, stdout="rake aborted!\n")

        response = world.client.post(
            f"/api/apps/{ACME}/exec",
            json={"argv": ["rake", "db:migrate"]},
            headers=world.as_("admin"),
        )
        job = self.wait_for_job(world, response.json()["job_id"])

        assert job["status"] == "failed"
        assert job["result"]["exit_code"] == 2
        assert "exited with status 2" in job["error"]

    def test_it_is_held_to_the_same_permission_and_team_as_a_terminal(self, world):
        body = {"argv": ["ls"]}

        assert (
            world.client.post(
                f"/api/apps/{ACME}/exec", json=body, headers=world.as_("dana")
            ).status_code
            == 403
        )
        assert (
            world.client.post(
                f"/api/apps/{ACME}/exec", json=body, headers=world.as_("omar")
            ).status_code
            == 403
        )
        assert (
            world.client.post(
                f"/api/apps/{GLOBEX}/exec", json=body, headers=world.as_("alice")
            ).status_code
            == 404
        )
        accepted = world.client.post(
            f"/api/apps/{ACME}/exec", json=body, headers=world.as_("alice")
        )
        assert accepted.status_code == 202
        # The command runs in the job thread: left running, it opened the store after this test.
        self.wait_for_job(world, accepted.json()["job_id"], who="alice")

    @pytest.mark.parametrize(
        "body",
        [
            {"argv": []},
            {"argv": [""]},
            {"argv": ["ls"], "timeout": 0},
            {"argv": ["ls"], "timeout": 10**7},
            {},
        ],
    )
    def test_a_command_that_is_not_one_is_refused_before_a_job_exists(self, world, body):
        before = len(world.client.get("/api/jobs", headers=world.as_("admin")).json()["jobs"])

        response = world.client.post(
            f"/api/apps/{ACME}/exec", json=body, headers=world.as_("admin")
        )

        assert response.status_code in (400, 422)
        after = len(world.client.get("/api/jobs", headers=world.as_("admin")).json()["jobs"])
        assert after == before

    def test_a_cookie_session_outside_sudo_mode_is_asked_to_confirm(self, world):
        token = world.master["Authorization"].removeprefix("Bearer ")
        login = world.client.post("/api/auth/login", json={"token": token})
        headers = {"X-Noust-CSRF": login.json()["csrf_token"]}

        response = world.client.post(
            f"/api/apps/{ACME}/exec", json={"argv": ["ls"]}, headers=headers
        )

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"
        world.client.cookies.clear()


class TestTheSchemaAndTheFleet:
    def test_the_three_that_open_a_shell_or_run_a_command_ask_for_sudo_mode(self, world):
        from noust.web.api.openapi import ELEVATION_EXTENSION

        schema = world.client.get("/api/openapi.json", headers=world.as_("admin")).json()["paths"]

        for path in (
            "/api/apps/{domain}/terminal",
            "/api/apps/{domain}/exec",
            "/api/server/terminal",
        ):
            assert schema[path]["post"].get(ELEVATION_EXTENSION) is True, path
        assert schema["/api/terminal/status"]["get"].get(ELEVATION_EXTENSION) is not True

    def test_a_centrals_socket_to_a_nodes_terminal_needs_both_permissions(self):
        from noust.web.permissions import Permission
        from noust.web.permissions.websockets import websocket_permissions

        assert websocket_permissions("/ws/terminal") == [Permission.APPS_TERMINAL]
        assert websocket_permissions("/ws/nodes/nas/terminal") == [
            Permission.FLEET_READ,
            Permission.APPS_TERMINAL,
        ]

    def test_a_node_lets_a_central_open_an_application_terminal_only_at_admin(self):
        from noust.fleet.policy import FleetAccess, permits

        for level, allowed in (("read", False), ("deploy", False), ("admin", True)):
            assert permits(FleetAccess(level), "apps.terminal") is allowed, level

    def test_a_nodes_root_shell_needs_host_access_as_well(self):
        from noust.fleet.policy import FleetAccess, permits

        assert permits(FleetAccess("admin", host_access=False), "server.terminal") is False
        assert permits(FleetAccess("admin", host_access=True), "server.terminal") is True
        assert permits(FleetAccess("deploy", host_access=True), "server.terminal") is False

    def test_the_roles_that_hold_it(self):
        from noust.web.permissions import Permission
        from noust.web.permissions.roles import ADMIN, AUDITOR, OPERATOR, SECURITY, VIEWER

        for permission in (Permission.APPS_TERMINAL, Permission.SERVER_TERMINAL):
            assert permission in ADMIN
            assert not {permission} & (VIEWER | OPERATOR | SECURITY | AUDITOR)

    def test_a_team_can_hold_the_application_terminal_but_never_the_servers(self):
        from noust.web.permissions import Permission
        from noust.web.permissions.team_roles import TEAM_ADMIN, TEAM_OPERATOR, TENANT_PERMISSIONS

        assert Permission.APPS_TERMINAL in TEAM_ADMIN
        assert Permission.APPS_TERMINAL not in TEAM_OPERATOR
        assert Permission.SERVER_TERMINAL not in TENANT_PERMISSIONS
