# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What is accepted when a stack that runs with what gives this server to a container is adopted is what was shown (item 171).

The lines of a compose file that give the server away are listed before the stack is adopted or brought, and
accepting them is a decision of its own. Between the preview that listed them and the request that accepts there
is time, and the file is a file: what is recorded as accepted is what was in front of the person, so the preview
gives a digest of the lines and accepting asks for it. If the lines are no longer the same the answer is a 409
with the lines as they are now, and nothing is registered.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.core.store import NoustStore
from noust.deployers import compose_adopt
from noust.deployers.compose_adopt import (
    AdoptionRefusedError,
    host_access_digest_of,
    plan_adoption,
)
from noust.deployers.helpers import sandbox as build_sandbox
from noust.managers.nginx_manager import NginxManager
from noust.web.api.app_adopt import AdoptRequest
from noust.web.api.docker_change_models import DockerBringPreviewRequest, DockerBringRequest
from tests.support.compose_adopt import (  # noqa: F401
    DOMAIN,
    Docker,
    adopt_client,
    docker,
    root,
    store,
    units,
    web,
)

# ruff: noqa: F811

SOCKET_LINE = "mounts the Docker socket"


def shown(root: Path, web: NginxManager) -> AdoptionRefusedError:
    """What a preview says of the stack: the refusal that lists its lines."""
    with pytest.raises(AdoptionRefusedError) as raised:
        plan_adoption(DOMAIN, root, web=web)
    return raised.value


def give_another_thing(root: Path) -> None:
    """Make the file give the server one more thing than it did when it was shown."""
    compose = root / "docker-compose.prod.yml"
    compose.write_text(
        compose.read_text().replace("    group_add:\n", "    privileged: true\n    group_add:\n", 1)
    )


class TestTheDigest:
    def test_it_is_the_same_for_the_same_lines_in_any_order(self) -> None:
        assert host_access_digest_of(["a", "b"]) == host_access_digest_of(["b", "a"])

    def test_it_differs_when_a_line_does(self) -> None:
        assert host_access_digest_of(["a"]) != host_access_digest_of(["a", "b"])
        assert host_access_digest_of(["a b"]) != host_access_digest_of(["a", "b"])

    def test_there_is_none_for_no_lines(self) -> None:
        assert host_access_digest_of([]) == ""

    def test_the_refusal_and_the_plan_carry_the_digest_of_their_lines(
        self, root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
    ) -> None:
        refusal = shown(root, web)
        digest = refusal.host_access_digest

        plan = plan_adoption(
            DOMAIN, root, web=web, accept_host_access=True, host_access_digest=digest
        )

        assert digest == host_access_digest_of(refusal.host_access) != ""
        assert plan.host_access_digest == digest
        assert plan.to_dict()["host_access_digest"] == digest


class TestAcceptingWhatWasShown:
    def test_the_digest_of_the_lines_that_were_shown_accepts_them(
        self, root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
    ) -> None:
        refusal = shown(root, web)

        plan = plan_adoption(
            DOMAIN,
            root,
            web=web,
            accept_host_access=True,
            accepted_by="alice",
            host_access_digest=refusal.host_access_digest,
        )

        assert plan.host_access == refusal.host_access
        assert plan.accepted_by == "alice"

    def test_the_digest_of_nothing_shown_accepts_nothing(
        self, root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
    ) -> None:
        with pytest.raises(AdoptionRefusedError) as raised:
            plan_adoption(DOMAIN, root, web=web, accept_host_access=True, host_access_digest="")

        assert SOCKET_LINE in raised.value.host_access[0]
        assert raised.value.coded is not None
        assert raised.value.coded.code == "compose_guard.adopt_host_access"
        assert store.get_app(DOMAIN) is None

    def test_lines_that_changed_since_are_refused_with_the_lines_as_they_are_now(
        self, root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
    ) -> None:
        before = shown(root, web)
        give_another_thing(root)

        with pytest.raises(AdoptionRefusedError) as raised:
            plan_adoption(
                DOMAIN,
                root,
                web=web,
                accept_host_access=True,
                host_access_digest=before.host_access_digest,
            )

        now = raised.value
        assert now.coded is not None and now.coded.code == "compose_guard.host_access_changed"
        assert len(now.host_access) == len(before.host_access) + 1
        assert any("privileged" in line for line in now.host_access)
        assert now.host_access_digest not in ("", before.host_access_digest)
        assert all(line in (now.details or "") for line in now.host_access)

    def test_what_is_shown_again_can_then_be_accepted(
        self, root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
    ) -> None:
        before = shown(root, web)
        give_another_thing(root)
        with pytest.raises(AdoptionRefusedError) as changed:
            plan_adoption(
                DOMAIN,
                root,
                web=web,
                accept_host_access=True,
                host_access_digest=before.host_access_digest,
            )

        plan = plan_adoption(
            DOMAIN,
            root,
            web=web,
            accept_host_access=True,
            host_access_digest=changed.value.host_access_digest,
        )

        assert plan.host_access == changed.value.host_access

    def test_without_accepting_a_digest_changes_nothing(
        self, root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
    ) -> None:
        with pytest.raises(AdoptionRefusedError) as raised:
            plan_adoption(DOMAIN, root, web=web, host_access_digest="anything")

        assert raised.value.coded is not None
        assert raised.value.coded.code == "compose_guard.adopt_host_access"

    def test_lines_that_went_away_leave_nothing_to_accept(
        self, root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
    ) -> None:
        before = shown(root, web)
        compose = root / "docker-compose.prod.yml"
        compose.write_text(
            compose.read_text().replace(
                "      - /var/run/docker.sock:/var/run/docker.sock:ro\n", ""
            )
        )

        plan = plan_adoption(
            DOMAIN,
            root,
            web=web,
            accept_host_access=True,
            host_access_digest=before.host_access_digest,
        )

        assert plan.host_access == ()

    def test_a_caller_that_records_the_plan_it_shows_needs_no_digest(
        self, root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
    ) -> None:
        # The command line: it prints the plan it has in hand and records that very plan.
        plan = plan_adoption(DOMAIN, root, web=web, accept_host_access=True)

        assert len(plan.host_access) == 1


class TestTheApi:
    def body(self, root: Path, **extra: Any) -> dict[str, Any]:
        return {"domain": DOMAIN, "path": str(root), **extra}

    def lines_shown(self, client: Any, root: Path) -> tuple[list[str], str]:
        listed = client.post("/api/apps/adopt", json=self.body(root, preview=True))
        assert listed.status_code == 409, listed.text
        found = listed.json()
        assert found["host_access"] and found["host_access_digest"]
        return found["host_access"], found["host_access_digest"]

    def test_the_refusal_that_lists_the_lines_gives_their_digest(
        self, root: Path, docker: Docker, store: NoustStore, units: Path
    ) -> None:
        lines, digest = self.lines_shown(adopt_client(elevated=True), root)

        assert digest == host_access_digest_of(lines)

    def test_accepting_with_what_was_shown_adopts_and_records_exactly_that(
        self, root: Path, docker: Docker, store: NoustStore, units: Path
    ) -> None:
        client = adopt_client(elevated=True)
        lines, digest = self.lines_shown(client, root)

        previewed = client.post(
            "/api/apps/adopt",
            json=self.body(root, preview=True, accept_host_access=True, host_access_digest=digest),
        )
        done = client.post(
            "/api/apps/adopt",
            json=self.body(root, accept_host_access=True, host_access_digest=digest),
        )

        assert previewed.status_code == 200, previewed.text
        assert previewed.json()["host_access"] == lines
        assert previewed.json()["host_access_digest"] == digest
        assert done.status_code == 200, done.text
        assert done.json()["host_access"] == lines
        recorded = build_sandbox.get_compose_exception(DOMAIN, store=store)
        assert recorded is not None
        assert all(line in recorded.reason for line in lines)

    def test_accepting_without_the_digest_is_a_409_with_the_lines_and_nothing_is_registered(
        self, root: Path, docker: Docker, store: NoustStore, units: Path
    ) -> None:
        client = adopt_client(elevated=True)

        refused = client.post("/api/apps/adopt", json=self.body(root, accept_host_access=True))

        assert refused.status_code == 409, refused.text
        assert refused.json()["host_access"] and refused.json()["host_access_digest"]
        assert store.get_app(DOMAIN) is None
        assert build_sandbox.get_compose_exception(DOMAIN, store=store) is None

    def test_the_preview_that_accepts_needs_it_too(
        self, root: Path, docker: Docker, store: NoustStore, units: Path
    ) -> None:
        client = adopt_client(elevated=True)

        refused = client.post(
            "/api/apps/adopt", json=self.body(root, preview=True, accept_host_access=True)
        )

        assert refused.status_code == 409, refused.text

    def test_accepting_what_changed_is_a_409_with_the_lines_of_now_and_nothing_is_registered(
        self, root: Path, docker: Docker, store: NoustStore, units: Path
    ) -> None:
        client = adopt_client(elevated=True)
        lines, digest = self.lines_shown(client, root)
        give_another_thing(root)

        refused = client.post(
            "/api/apps/adopt",
            json=self.body(root, accept_host_access=True, host_access_digest=digest),
        )

        assert refused.status_code == 409, refused.text
        found = refused.json()
        assert found["coded"]["code"] == "compose_guard.host_access_changed"
        assert len(found["host_access"]) == len(lines) + 1
        assert found["host_access_digest"] not in ("", digest)
        assert store.get_app(DOMAIN) is None
        assert build_sandbox.get_compose_exception(DOMAIN, store=store) is None

    def test_a_request_that_says_nothing_of_it_asks_for_nothing_to_be_accepted(self) -> None:
        # The default is the digest of nothing, never "whatever is there": a client that leaves it out is
        # refused, it does not accept by omission.
        for model in (AdoptRequest, DockerBringRequest, DockerBringPreviewRequest):
            made = model(domain="a.example.com", path="/x") if model is AdoptRequest else model()
            assert made.host_access_digest == ""


class TestTheCommand:
    def invoke(self, *args: str, input_text: str | None = None) -> Any:
        from noust.cli.app import cli as root_cli

        return CliRunner().invoke(root_cli, list(args), input=input_text)

    def test_the_lines_it_prints_are_the_lines_it_records(
        self,
        root: Path,
        docker: Docker,
        web: NginxManager,
        store: NoustStore,
        units: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(compose_adopt, "_default_web", lambda: web)
        expected = shown(root, web).host_access

        result = self.invoke(
            "app", "adopt", DOMAIN, "--path", str(root), "--accept-host-access", "--yes"
        )

        assert result.exit_code == 0, result.output
        assert all(line in result.output for line in expected)
        recorded = build_sandbox.get_compose_exception(DOMAIN, store=store)
        assert recorded is not None
        assert all(line in recorded.reason for line in expected)


@pytest.fixture
def bringer(root: Path, docker: Docker, store: NoustStore, units: Path) -> Any:
    """What brings the stacks Docker lists, over a stack whose compose file gives the server away."""
    from noust.core.docker.gate import DockerGate
    from noust.core.docker.owners import Registry
    from noust.managers.docker_bring import StackBringer
    from tests.support.compose_adopt import PROJECT
    from tests.support.docker_machine import DockerMachine

    box = DockerMachine()
    for name, service in (("proggest-backend", "backend"), ("proggest-redis", "redis")):
        box.box(
            name,
            project=PROJECT,
            service=service,
            working_dir=str(root),
            config_files=str(root / "docker-compose.prod.yml"),
            policy="unless-stopped",
        )
    gate = DockerGate(box, store=store, registry=lambda: Registry(), disk=lambda path: None)
    return StackBringer(gate, store=store, runner=docker)


class TestBringingAStack:
    def test_the_preview_says_the_lines_and_their_digest(self, bringer: Any) -> None:
        from tests.support.compose_adopt import PROJECT

        with pytest.raises(AdoptionRefusedError) as listed:
            bringer.preview(PROJECT, name=DOMAIN)

        seen = bringer.preview(
            PROJECT,
            name=DOMAIN,
            accept_host_access=True,
            host_access_digest=listed.value.host_access_digest,
        )

        body = seen.to_dict()
        assert body["host_access"] == list(listed.value.host_access)
        assert body["host_access_digest"] == listed.value.host_access_digest

    def test_bringing_with_what_was_shown_registers_the_stack(
        self, bringer: Any, store: NoustStore
    ) -> None:
        from tests.support.compose_adopt import PROJECT

        with pytest.raises(AdoptionRefusedError) as listed:
            bringer.bring(PROJECT, name=DOMAIN)

        done = bringer.bring(
            PROJECT,
            name=DOMAIN,
            accept_host_access=True,
            accepted_by="alice",
            host_access_digest=listed.value.host_access_digest,
        )

        assert done.adopted is True
        assert store.get_app(DOMAIN) is not None

    def test_bringing_lines_that_changed_registers_nothing_and_says_the_new_ones(
        self, bringer: Any, root: Path, store: NoustStore
    ) -> None:
        from tests.support.compose_adopt import PROJECT

        with pytest.raises(AdoptionRefusedError) as listed:
            bringer.preview(PROJECT, name=DOMAIN)
        give_another_thing(root)

        with pytest.raises(AdoptionRefusedError) as raised:
            bringer.bring(
                PROJECT,
                name=DOMAIN,
                accept_host_access=True,
                accepted_by="alice",
                host_access_digest=listed.value.host_access_digest,
            )

        assert raised.value.coded is not None
        assert raised.value.coded.code == "compose_guard.host_access_changed"
        assert any("privileged" in line for line in raised.value.host_access)
        assert store.get_app(DOMAIN) is None


class TestTheBringCommand:
    @pytest.fixture
    def zone(self, bringer: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
        from noust.cli.commands import docker as docker_command

        monkeypatch.setattr(docker_command, "_gate", lambda: bringer._gate)
        return bringer

    def invoke(self, *args: str) -> Any:
        from noust.cli.app import cli as root_cli

        return CliRunner().invoke(root_cli, ["docker", "stack", "bring", *args])

    def test_it_shows_the_lines_it_will_record_and_records_those(
        self, zone: Any, root: Path, store: NoustStore
    ) -> None:
        from tests.support.compose_adopt import PROJECT

        result = self.invoke(PROJECT, "--name", DOMAIN, "--accept-host-access", "--yes")

        assert result.exit_code == 0, result.output
        assert SOCKET_LINE in result.output
        recorded = build_sandbox.get_compose_exception(DOMAIN, store=store)
        assert recorded is not None and SOCKET_LINE in recorded.reason

    def test_the_preview_shows_the_lines_too(self, zone: Any, store: NoustStore) -> None:
        from tests.support.compose_adopt import PROJECT

        result = self.invoke(PROJECT, "--name", DOMAIN, "--accept-host-access", "--preview")

        assert result.exit_code == 0, result.output
        assert SOCKET_LINE in result.output
        assert store.get_app(DOMAIN) is None

    def test_lines_that_change_between_what_it_showed_and_the_registering_are_not_registered(
        self, zone: Any, root: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.managers import docker_bring
        from tests.support.compose_adopt import PROJECT

        real = docker_bring.StackBringer.bring

        def after_the_file_changed(self: Any, *args: Any, **kwargs: Any) -> Any:
            give_another_thing(root)
            return real(self, *args, **kwargs)

        monkeypatch.setattr(docker_bring.StackBringer, "bring", after_the_file_changed)

        result = self.invoke(PROJECT, "--name", DOMAIN, "--accept-host-access", "--yes")

        assert result.exit_code == 1, result.output
        assert "privileged" in result.output
        assert store.get_app(DOMAIN) is None
