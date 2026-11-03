# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/server/software``: the translation of HTTP into the software manager and back (item 118).

What the endpoints do is three things: refuse at once what can be refused before a job exists (a
component that is never removed, one something depends on, a removal nobody confirmed), queue the
right job with the right arguments, and ask for the permission each component's install has always
taken. What the manager does with the machine is tested in ``tests/test_server_software.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from noust.core.store import App, get_store
from noust.managers.server.components import COMPONENTS
from noust.web.api.server import software as software_api
from noust.web.jobs import JobType
from noust.web.permissions import Permission
from noust.web.permissions import routes_server_software as permissions
from noust.web.permissions.registry import route_map
from tests.support.server_api import api, store  # noqa: F401
from tests.support.server_support import (  # noqa: F401
    make_machine,
    no_package_manager_running,
    platform_for,
)

# ruff: noqa: F811

pytestmark = pytest.mark.usefixtures("no_package_manager_running")


@pytest.fixture
def machine(api, monkeypatch: pytest.MonkeyPatch):
    """The server of the API tests, on apt, with nothing of the software installed."""
    monkeypatch.setattr("noust.web.api.server.software.get_job_manager", lambda: api.jobs)
    # The session of these tests holds no role: what the handlers ask for on top of the route's
    # is recorded by the tests that care, and allowed by the rest.
    monkeypatch.setattr("noust.web.api.server.software.check_permission", lambda session, p: None)
    api.machine.ctx._platform = platform_for("apt")
    api.machine.runner.only_knows("apt-get")
    api.machine.runner.script(["dpkg-query"], stdout="")
    return api.machine


def installed(machine, **versions: str) -> None:
    machine.runner.script(
        ["dpkg-query"],
        stdout="".join(f"{n.replace('_', '-')} ii  {v}\n" for n, v in versions.items()),
    )


class TestWhatIsDeclared:
    def test_every_route_has_its_permission_and_the_scope_of_the_instance(self, api) -> None:
        permission = route_map()

        assert permission[("GET", "/api/server/software")] == "server.read"
        assert permission[("GET", "/api/server/software/{component}/impact")] == "server.read"
        assert permission[("POST", "/api/server/software/{component}/install")] == "server.manage"
        assert (
            permission[("POST", "/api/server/software/{component}/remove")] == "server.host_access"
        )

    def test_installing_keeps_the_permission_each_function_has_today(self) -> None:
        assert permissions.INSTALL_PERMISSIONS["cloudflared"] == Permission.SERVER_MANAGE
        assert permissions.INSTALL_PERMISSIONS["waf"] == Permission.SERVER_MANAGE
        assert permissions.INSTALL_PERMISSIONS["knot"] == Permission.SERVER_HOST_ACCESS
        assert permissions.INSTALL_PERMISSIONS["postgresql"] == Permission.DATABASES_MANAGE
        # What no function of the console installed until now changes the host's packages.
        assert permissions.DEFAULT_INSTALL_PERMISSION == Permission.SERVER_HOST_ACCESS

    def test_nothing_in_the_tables_names_a_component_that_does_not_exist(self) -> None:
        ids = {item.id for item in COMPONENTS}

        assert set(permissions.INSTALL_PERMISSIONS) <= ids
        assert set(permissions.REMOVE_PERMISSIONS) <= ids

    def test_the_scope_is_the_instances(self) -> None:
        from noust.web.permissions.scopes import rule_for

        for method, path in (
            ("GET", "/api/server/software"),
            ("GET", "/api/server/software/{component}/impact"),
            ("POST", "/api/server/software/{component}/install"),
            ("POST", "/api/server/software/{component}/remove"),
        ):
            rule = rule_for(method, path)
            assert rule is not None and rule.scoping == "instance", (method, path)


class TestTheList:
    def test_it_lists_every_component_with_what_the_page_shows(self, api, machine) -> None:
        installed(machine, nginx="1.24.0")
        get_store().create_app(
            App(domain="shop.example.com", app_type="static", app_path="/x", webserver="nginx")
        )

        response = api.client.get("/api/server/software")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["managed"] is True and body["family"] == "apt"
        by_id = {item["id"]: item for item in body["items"]}
        assert len(by_id) == len(COMPONENTS)
        nginx = by_id["nginx"]
        assert nginx["installed"] is True and nginx["version"] == "1.24.0"
        assert nginx["origin"] == "unknown" and nginx["removable"] is False
        assert nginx["dependents"] == [{"kind": "application", "names": ["shop.example.com"]}]
        assert nginx["purpose_code"] == "software.nginx.purpose"
        assert by_id["git"]["severity"] == "never"
        assert by_id["cloudflared"]["installed"] is False and by_id["cloudflared"]["installable"]

    def test_refresh_asks_the_machine_again(self, api, machine) -> None:
        api.client.get("/api/server/software")
        installed(machine, nginx="1.24.0")

        stale = api.client.get("/api/server/software").json()
        fresh = api.client.get("/api/server/software?refresh=true").json()

        assert {i["id"]: i for i in stale["items"]}["nginx"]["installed"] is False
        assert {i["id"]: i for i in fresh["items"]}["nginx"]["installed"] is True

    def test_a_system_whose_packages_are_not_noust_s_says_so_and_still_answers(
        self, api, machine
    ) -> None:
        machine.ctx._platform = platform_for("none")

        body = api.client.get("/api/server/software").json()

        assert body["managed"] is False and body["reason"]
        assert body["items"]


class TestTheImpact:
    def test_it_says_what_would_go_and_what_stops_it(self, api, machine) -> None:
        installed(machine, nginx="1.24.0")
        machine.runner.script(["apt-get", "-s"], stdout="Remv nginx [1.24.0]\n")
        get_store().create_app(
            App(domain="shop.example.com", app_type="static", app_path="/x", webserver="nginx")
        )

        response = api.client.get("/api/server/software/nginx/impact")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["removable"] is False
        assert [b["reason"] for b in body["blockers"]] == ["dependents"]
        assert body["blockers"][0]["dependents"] == [
            {"kind": "application", "names": ["shop.example.com"]}
        ]
        assert body["removes"] == ["nginx"]
        assert not [c for c in machine.runner.calls if c[:2] == ("apt-get", "remove")]

    def test_an_id_the_registry_does_not_have_is_a_404(self, api, machine) -> None:
        assert api.client.get("/api/server/software/nope/impact").status_code == 404


class TestInstalling:
    def test_it_queues_a_package_job_with_what_the_installer_needs(self, api, machine) -> None:
        response = api.client.post("/api/server/software/knot/install", json={"epel": True})

        assert response.status_code == 202, response.text
        queued = api.jobs.queued[0]
        assert queued["job_type"] is JobType.SERVER_ACTION
        assert queued["kwargs"]["component"] == "knot"
        assert queued["kwargs"]["epel"] is True
        assert queued["metadata"]["component"] == "knot"

    def test_fail2ban_is_told_never_to_ban_the_client_that_asked(self, api, machine) -> None:
        response = api.client.post(
            "/api/server/software/fail2ban/install",
            headers={"X-Forwarded-For": "203.0.113.9"},
        )

        assert response.status_code == 202, response.text
        assert isinstance(api.jobs.queued[0]["kwargs"]["ignore"], list)

    def test_where_noust_installs_nothing_it_is_a_409_and_no_job(self, api, machine) -> None:
        machine.ctx._platform = platform_for("zypper")

        response = api.client.post("/api/server/software/nginx_stream_module/install")

        assert response.status_code == 409, response.text
        assert response.json()["error"] == "software_refused"
        assert response.json()["reason"] == "not_installable"
        assert api.jobs.queued == []
        assert [r["outcome"] for r in api.audit.records] == ["denied"]

    def test_a_package_manager_that_is_busy_is_a_409_with_who(self, api, machine) -> None:
        api.jobs.active = [type("Job", (), {"name": "Apply updates", "type": JobType.OS_UPDATE})()]

        response = api.client.post("/api/server/software/chrony/install")

        assert response.status_code == 409, response.text
        assert api.jobs.queued == []

    def test_the_permission_of_the_component_is_asked_on_top_of_the_routes(
        self, api, machine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        asked: list[str] = []
        monkeypatch.setattr(software_api, "check_permission", lambda session, p: asked.append(p))

        api.client.post("/api/server/software/postgresql/install")
        api.client.post("/api/server/software/cloudflared/install")
        api.client.post("/api/server/software/rclone/install")

        assert asked == [
            Permission.DATABASES_MANAGE,
            Permission.SERVER_MANAGE,
            Permission.SERVER_HOST_ACCESS,
        ]

    def test_a_refused_permission_queues_nothing(
        self, api, machine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.web.permissions.enforce import PermissionDenied

        def deny(session: Any, permission: str) -> None:
            raise PermissionDenied("permission_denied", permission, "Not allowed")

        monkeypatch.setattr(software_api, "check_permission", deny)

        response = api.client.post("/api/server/software/knot/install")

        assert response.status_code != 202
        assert api.jobs.queued == []

    def test_an_unknown_id_is_a_404(self, api, machine) -> None:
        assert api.client.post("/api/server/software/nope/install").status_code == 404


class TestRemoving:
    def test_a_never_is_a_409_with_its_reason_and_its_sentence_for_the_console(
        self, api, machine
    ) -> None:
        installed(machine, git="2.43")

        response = api.client.post("/api/server/software/git/remove")

        assert response.status_code == 409, response.text
        body = response.json()
        assert body["error"] == "software_refused" and body["reason"] == "never"
        assert body["coded"] == {"code": "software.git.never", "params": {}}
        assert api.jobs.queued == []
        assert api.audit.records[-1]["event"] == "server.software_remove"
        assert api.audit.records[-1]["outcome"] == "denied"

    def test_what_something_depends_on_is_a_409_that_names_what(self, api, machine) -> None:
        installed(machine, nginx="1.24.0")
        get_store().create_app(
            App(domain="shop.example.com", app_type="static", app_path="/x", webserver="nginx")
        )

        response = api.client.post("/api/server/software/nginx/remove")

        assert response.status_code == 409
        body = response.json()
        assert body["reason"] == "dependents"
        assert body["dependents"] == [{"kind": "application", "names": ["shop.example.com"]}]

    def test_an_origin_noust_does_not_know_is_a_question_and_no_job_until_confirmed(
        self, api, machine
    ) -> None:
        installed(machine, fail2ban="1.0")
        machine.runner.script(["apt-get", "-s"], stdout="Remv fail2ban [1.0]\n")

        asked = api.client.post("/api/server/software/fail2ban/remove")

        assert asked.status_code == 409
        assert asked.json()["error"] == "confirmation_required"
        assert asked.json()["required"] == {"unknown_origin": True, "removes": ["fail2ban"]}
        assert asked.json()["coded"]["code"] == "software.refused.unknown_origin"
        assert api.jobs.queued == []

        confirmed = api.client.post(
            "/api/server/software/fail2ban/remove", json={"confirm_unknown_origin": True}
        )

        assert confirmed.status_code == 202, confirmed.text
        queued = api.jobs.queued[0]
        assert queued["job_type"] is JobType.SERVER_ACTION
        assert queued["kwargs"]["component"] == "fail2ban"
        assert queued["kwargs"]["confirm_unknown_origin"] is True

    def test_it_changes_nothing_before_the_job_runs(self, api, machine) -> None:
        installed(machine, fail2ban="1.0")
        machine.runner.script(["apt-get", "-s"], stdout="Remv fail2ban [1.0]\n")

        api.client.post(
            "/api/server/software/fail2ban/remove", json={"confirm_unknown_origin": True}
        )

        assert not [c for c in machine.runner.calls if c[:2] == ("apt-get", "remove")]

    def test_an_engine_also_asks_for_the_databases_permission(
        self, api, machine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        asked: list[str] = []
        monkeypatch.setattr(software_api, "check_permission", lambda session, p: asked.append(p))

        api.client.post("/api/server/software/postgresql/remove")
        api.client.post("/api/server/software/fail2ban/remove")

        assert asked == [Permission.DATABASES_MANAGE]

    def test_an_id_the_registry_does_not_have_is_a_404(self, api, machine) -> None:
        assert api.client.post("/api/server/software/nope/remove").status_code == 404


class TestTheJobs:
    class Context:
        """The job manager's context, as far as the jobs look at it."""

        def __init__(self) -> None:
            self.lines: list[str] = []
            self.steps: list[tuple[str, int]] = []

        def update(self, message: str, progress: int) -> None:
            self.steps.append((message, progress))

        def log(self, message: str, level: str = "info") -> None:
            self.lines.append(message)

    def test_the_install_job_installs_and_returns_what_was_done(self, api, machine) -> None:
        from noust.web.api.server.software import software_install_job

        context = self.Context()
        installed(machine)
        machine.runner.script(["apt-get", "install"], stdout="Setting up chrony ...\n")

        result = software_install_job("chrony", actor="cli:root", job_context=context)  # type: ignore[arg-type]

        assert result["id"] == "chrony" and "installed_now" in result
        assert ("apt-get", "install", "-y", "chrony") in machine.runner.calls
        assert "Setting up chrony ..." in context.lines
        assert context.steps[-1] == ("Done", 100)

    def test_the_remove_job_removes_and_returns_what_was_done(self, api, machine) -> None:
        from noust.web.api.server.software import software_remove_job

        context = self.Context()
        installed(machine, fail2ban="1.0")
        machine.runner.script(["apt-get", "-s"], stdout="Remv fail2ban [1.0]\n")

        result = software_remove_job(
            "fail2ban",
            confirm_unknown_origin=True,
            actor="cli:root",
            job_context=context,  # type: ignore[arg-type]
        )

        assert result["removed"] == ["fail2ban"]
        assert ("apt-get", "remove", "-y", "fail2ban") in machine.runner.calls
        assert not [c for c in machine.runner.calls if "purge" in c]

    def test_a_job_outside_the_job_manager_is_refused(self) -> None:
        from noust.web.api.server.software import software_install_job

        with pytest.raises(ValueError, match="job_context"):
            software_install_job("chrony", actor="cli:root")


def test_every_write_asks_for_sudo_mode(api) -> None:
    from noust.web.api.openapi import ELEVATION_EXTENSION, annotate_elevation

    annotated = annotate_elevation(api.app.openapi(), api.app.routes)

    for path in ("install", "remove"):
        operation = annotated["paths"][f"/api/server/software/{{component}}/{path}"]["post"]
        assert operation.get(ELEVATION_EXTENSION), path
