# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Adopting a stack that runs with what gives this server to a container.

A stack that already runs is proven to have what it has, so adopting it is never refused for it. But
whoever adopts puts under Noust something whose containers own the machine, and says so: the plan
carries each line of the compose file that gives the server away, adopting without having accepted
them is refused with the list in front, and accepting records them, with who and when, as the
application's compose exception (the same one ``noust app sandbox compose-exception`` records, and
nothing else). Only who is equivalent to root can accept: no team role, and no central whose node did
not allow host access.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.core.exceptions import DeploymentError
from noust.core.store import NoustStore
from noust.deployers.helpers import sandbox as build_sandbox
from noust.managers.nginx_manager import NginxManager
from tests.support.compose_adopt import (  # noqa: F401
    DOMAIN,
    UNIT,
    Docker,
    adopt_client,
    docker,
    root,
    shown_digest,
    store,
    units,
    web,
)

# ruff: noqa: F811

SOCKET_LINE = "mounts the Docker socket"


def accepted(store: NoustStore) -> Any:
    """The compose exception the adoption recorded, if any."""
    return build_sandbox.get_compose_exception(DOMAIN, store=store)


def refused_by(root: Path, web: NginxManager) -> Any:
    """Adopt without having accepted anything."""
    from noust.deployers.compose_adopt import AdoptionRefusedError, adopt_stack

    with pytest.raises(AdoptionRefusedError) as raised:
        adopt_stack(DOMAIN, root, web=web)
    return raised.value


class TestTheListInFront:
    """Without the lines accepted, nothing is adopted."""

    def test_the_refusal_lists_each_line_and_says_how_to_go_on(
        self, root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
    ) -> None:
        refusal = refused_by(root, web)

        assert len(refusal.host_access) == 1
        assert SOCKET_LINE in refusal.host_access[0]
        assert refusal.host_access[0] in (refusal.details or "")
        assert f"noust app adopt {DOMAIN} --path {root} --accept-host-access" in refusal.details
        assert "equivalent to root" in refusal.details
        assert refusal.coded is not None and refusal.coded.code == "compose_guard.adopt_host_access"

    def test_nothing_was_registered_written_or_started(
        self, root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
    ) -> None:
        refused_by(root, web)

        assert store.get_app(DOMAIN) is None
        assert list(units.iterdir()) == []
        assert accepted(store) is None
        assert docker.mutating() == []

    def test_the_command_the_refusal_names_works_as_written(
        self,
        root: Path,
        docker: Docker,
        web: NginxManager,
        store: NoustStore,
        units: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from noust.cli.app import cli as root_cli
        from noust.deployers import compose_adopt

        monkeypatch.setattr(compose_adopt, "_default_web", lambda: web)
        said = refused_by(root, web).details
        # The command ends the sentence: "If you still want it adopted: noust app adopt ...".
        argv = said.rsplit(": noust ", 1)[1].split()

        result = CliRunner().invoke(root_cli, [*argv, "--yes"])

        assert result.exit_code == 0, result.output
        assert store.get_app(DOMAIN) is not None

    def test_a_stack_that_runs_with_nothing_of_this_adopts_as_before(
        self, root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
    ) -> None:
        from noust.deployers.compose_adopt import adopt_stack

        compose = root / "docker-compose.prod.yml"
        compose.write_text(
            compose.read_text().replace(
                "      - /var/run/docker.sock:/var/run/docker.sock:ro\n", ""
            )
        )

        result = adopt_stack(DOMAIN, root, web=web)

        assert result.plan.host_access == ()
        assert accepted(store) is None


class TestAcceptingIt:
    """The adoption records the exception, with who accepted it."""

    def adopt(self, root: Path, web: NginxManager, by: str = "alice") -> Any:
        from noust.deployers.compose_adopt import adopt_stack

        return adopt_stack(DOMAIN, root, web=web, accept_host_access=True, accepted_by=by)

    def test_the_plan_carries_the_lines_and_the_one_who_accepted(
        self, root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
    ) -> None:
        result = self.adopt(root, web)

        assert len(result.plan.host_access) == 1
        assert SOCKET_LINE in result.plan.host_access[0]
        assert result.plan.accepted_by == "alice"
        assert any("alice" in warning for warning in result.plan.warnings)

    def test_it_is_recorded_as_the_compose_exception_with_who_and_when(
        self, root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
    ) -> None:
        self.adopt(root, web, by="alice")

        exception = accepted(store)
        assert exception is not None
        assert exception.allowed_by == "alice"
        assert exception.allowed_at
        assert SOCKET_LINE in exception.reason

    def test_it_is_audited_with_the_lines(
        self, root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
    ) -> None:
        from noust.core import audit

        recorded: list[Any] = []
        original = audit.record

        def spy(event: str, **kwargs: Any) -> Any:
            recorded.append((event, kwargs))
            return original(event, **kwargs)

        audit.record = spy  # type: ignore[assignment]
        try:
            self.adopt(root, web)
        finally:
            audit.record = original  # type: ignore[assignment]

        adopted = [kwargs for event, kwargs in recorded if event == "apps.adopt"]
        assert adopted and adopted[0]["details"]["accepted_host_access"]

    def test_a_unit_that_cannot_be_made_leaves_no_exception_behind(
        self, root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
    ) -> None:
        (units / f"{UNIT}.service").write_text("[Service]\nExecStart=/bin/true\n")

        with pytest.raises(DeploymentError, match="already"):
            self.adopt(root, web)

        assert store.get_app(DOMAIN) is None
        assert accepted(store) is None

    def test_an_application_that_has_the_exception_already_is_not_asked_again(
        self, root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
    ) -> None:
        from noust.deployers.compose_adopt import adopt_stack

        build_sandbox.set_compose_exception(
            DOMAIN, allowed=True, actor="bob", reason="the portainer edge service", store=store
        )

        result = adopt_stack(DOMAIN, root, web=web)

        assert result.plan.host_access == ()
        assert accepted(store).allowed_by == "bob"  # type: ignore[union-attr]

    def test_a_rehearsal_records_nothing(
        self, root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
    ) -> None:
        from noust.core.fs import DryRunFileSystem  # noqa: F401
        from noust.deployers.compose_adopt import plan_adoption

        plan = plan_adoption(DOMAIN, root, web=web, accept_host_access=True, accepted_by="alice")

        assert plan.host_access
        assert accepted(store) is None
        assert store.get_app(DOMAIN) is None

    def test_a_stack_that_is_not_running_is_a_new_stack_whatever_is_accepted(
        self, root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
    ) -> None:
        from noust.deployers.compose_adopt import adopt_stack

        docker.containers = []

        with pytest.raises(DeploymentError, match="asks for root"):
            adopt_stack(DOMAIN, root, web=web, accept_host_access=True, accepted_by="alice")

        assert store.get_app(DOMAIN) is None


class TestTheCommand:
    """``noust app adopt``."""

    def invoke(self, root: Path, web: NginxManager, monkeypatch: Any, *extra: str) -> Any:
        from noust.cli.app import cli as root_cli
        from noust.deployers import compose_adopt

        monkeypatch.setattr(compose_adopt, "_default_web", lambda: web)
        return CliRunner().invoke(root_cli, ["app", "adopt", DOMAIN, "--path", str(root), *extra])

    def test_without_the_flag_it_stops_with_the_list(
        self,
        root: Path,
        docker: Docker,
        web: NginxManager,
        store: NoustStore,
        units: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        result = self.invoke(root, web, monkeypatch, "--yes")

        assert result.exit_code != 0
        refusal = result.exception
        assert getattr(refusal, "host_access", ()), refusal
        assert SOCKET_LINE in refusal.details
        assert "--accept-host-access" in refusal.details
        assert store.get_app(DOMAIN) is None

    def test_with_the_flag_it_shows_the_lines_and_records_who_accepted(
        self,
        root: Path,
        docker: Docker,
        web: NginxManager,
        store: NoustStore,
        units: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        result = self.invoke(root, web, monkeypatch, "--accept-host-access", "--yes")

        assert result.exit_code == 0, result.output
        assert "Gives this server" in result.output
        assert SOCKET_LINE in result.output
        assert accepted(store) is not None

    def test_the_json_carries_the_lines(
        self,
        root: Path,
        docker: Docker,
        web: NginxManager,
        store: NoustStore,
        units: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        result = self.invoke(root, web, monkeypatch, "--accept-host-access", "--yes", "--json")

        body = json.loads(result.output.strip().splitlines()[-1])
        assert len(body["host_access"]) == 1


class TestTheApi:
    """``POST /api/apps/adopt``."""

    def post(self, root: Path, web: NginxManager, monkeypatch: Any, **body: Any) -> Any:
        from noust.deployers import compose_adopt

        monkeypatch.setattr(compose_adopt, "_default_web", lambda: web)
        client = adopt_client(elevated=True)
        if body.get("accept_host_access"):
            # What a person accepts is what they were shown: the digest of the lines the refusal listed.
            body.setdefault("host_access_digest", shown_digest(client, root))
        return client.post("/api/apps/adopt", json={"domain": DOMAIN, "path": str(root), **body})

    def test_without_accepting_it_is_a_409_with_the_lines_in_the_hint(
        self,
        root: Path,
        docker: Docker,
        web: NginxManager,
        store: NoustStore,
        units: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        response = self.post(root, web, monkeypatch)

        assert response.status_code == 409
        assert SOCKET_LINE in response.json()["hint"]
        assert [SOCKET_LINE in line for line in response.json()["host_access"]] == [True]
        assert response.json()["coded"]["code"] == "compose_guard.adopt_host_access"
        assert "accept_host_access" not in response.json()["detail"]
        assert store.get_app(DOMAIN) is None

    def test_accepting_it_adopts_and_records_the_exception_under_the_sessions_name(
        self,
        root: Path,
        docker: Docker,
        web: NginxManager,
        store: NoustStore,
        units: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        response = self.post(root, web, monkeypatch, accept_host_access=True)

        assert response.status_code == 200, response.text
        assert response.json()["adopted"] is True
        assert len(response.json()["host_access"]) == 1
        exception = accepted(store)
        assert exception is not None and exception.allowed_by

    def test_the_preview_shows_the_lines_without_recording_anything(
        self,
        root: Path,
        docker: Docker,
        web: NginxManager,
        store: NoustStore,
        units: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        response = self.post(root, web, monkeypatch, accept_host_access=True, preview=True)

        assert response.status_code == 200, response.text
        assert response.json()["adopted"] is False
        assert SOCKET_LINE in response.json()["host_access"][0]
        assert accepted(store) is None
        assert store.get_app(DOMAIN) is None
