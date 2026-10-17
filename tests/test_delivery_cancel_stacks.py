# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Cancelling the deployment of a Compose stack, a container and a monorepo on releases (checklist B-05).

A stack's images are built by the Docker daemon, a container's too, and a monorepo's workspaces
by pnpm; what none of them touches until the end is what serves: the containers are recreated and
the units restarted only once the build is done. So the build can be stopped, as long as what was
changed to get there goes back: the tree a stack pulls in place is checked out at the commit that
serves, the compose file of a container application is the one that serves, a monorepo's half-built
release is removed. A monorepo that builds in place, in the tree its units run from, still cannot
be stopped, and says why.
"""

# The harnesses are imported rather than replicated.
# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core import cancellation
from noust.core.applock import app_lock, holder_of
from noust.core.cancellation import operation_window, request_cancel
from noust.core.exceptions import CancelRefused, DeploymentCancelled
from noust.core.runner import CommandCancelled, FakeRunner
from noust.core.store import App, AppLayout, NoustStore
from tests.test_compose_release_retention import (  # noqa: F401  (pytest resolves fixtures by name)
    DOMAIN as STACK,
    Docker,
    Probe,
    deployer as stack_deployer,
    docker,
    no_waiting,
    probe,
    root,
    store,
)

WHO = "token:ci"


class CancellingDocker(Docker):
    """
    A Docker whose build is stopped by an operator while it runs.

    The real runner kills the command and raises ``CommandCancelled`` when the operation's cancel
    event is set; this one asks for the cancellation from inside the command and raises the same,
    which is what the deployer sees.
    """

    def __init__(self, domain: str, on: tuple[str, ...]) -> None:
        super().__init__()
        self.domain = domain
        self.on = on

    def _arm(self, argv: Any) -> None:
        words = tuple(argv)
        if all(word in words for word in self.on):
            request_cancel(self.domain, by=WHO)
            raise CommandCancelled(f"Cancelled: {' '.join(words)}")

    def run(self, argv: Any, **kwargs: Any) -> Any:
        self._arm(argv)
        return super().run(argv, **kwargs)

    def stream(self, argv: Any, **kwargs: Any) -> Any:
        self._arm(argv)
        return super().stream(argv, **kwargs)


class TestWhatCanBeStopped:
    def test_a_stack_a_container_and_a_monorepo_on_releases_can(
        self, store: NoustStore
    ) -> None:
        for kind, layout in (
            ("docker-compose", AppLayout.INPLACE.value),
            ("container", AppLayout.INPLACE.value),
            ("monorepo", AppLayout.RELEASES.value),
        ):
            domain = f"{kind}.example.com"
            store.create_app(
                App(domain=domain, app_type=kind, app_path=f"/var/www/apps/{kind}", layout=layout)
            )

            assert cancellation.supports_cancel(domain, store) == (True, "")

    def test_a_monorepo_that_builds_in_place_cannot_and_says_why(
        self, store: NoustStore
    ) -> None:
        store.create_app(
            App(
                domain="mono.example.com",
                app_type="monorepo",
                app_path="/var/www/apps/mono",
                layout=AppLayout.INPLACE.value,
            )
        )

        supported, why = cancellation.supports_cancel("mono.example.com", store)

        assert supported is False
        assert "in place" in why

    @pytest.mark.parametrize("kind", ["docker-compose", "container", "monorepo"])
    def test_a_new_application_of_any_type_can(self, store: NoustStore, kind: str) -> None:
        """Nothing serves yet, so nothing can be broken."""
        assert cancellation.supports_cancel("brand-new.example.com", store, kind) == (
            True,
            "",
        )


class TestAStackUpdate:
    def test_a_build_that_is_stopped_puts_the_tree_and_the_file_back(
        self, root: Path, store: NoustStore, probe: Probe
    ) -> None:
        runner = CancellingDocker(STACK, ("build",))
        (root / ".env").write_text("KEPT=1\n")
        made = stack_deployer(root, runner)
        before = (root / ".env").read_text()

        with app_lock(STACK, "update"), operation_window(STACK, "update"):
            with pytest.raises(DeploymentCancelled, match="cancelled by token:ci"):
                made.update()

        # The containers that serve were never touched, and the tree is on the commit they run.
        assert not runner.argv_with("compose", "up")
        assert [
            call
            for call in runner.argv_with("git", "checkout", "--force")
            if call[-1].startswith("1111111")
        ]
        assert (root / ".env").read_text() == before
        row = store.list_deployments(STACK)[0]
        assert row.cancelled is True and row.cancelled_by == WHO
        app = store.get_app(STACK)
        assert app is not None and app.status == "running"
        assert holder_of(STACK) is None

    def test_a_request_after_the_build_is_refused_as_too_late(
        self, root: Path, store: NoustStore, docker: Docker, probe: Probe
    ) -> None:
        refusals: list[CancelRefused] = []
        original = docker._lookup

        def lookup(argv: Any, user: str | None = None, env: Any = None) -> Any:
            if "up" in tuple(argv) and "compose" in tuple(argv):
                try:
                    request_cancel(STACK, by=WHO)
                except CancelRefused as refused:
                    refusals.append(refused)
            return original(argv, user, env)

        docker._lookup = lookup  # type: ignore[method-assign]
        docker.after = list(
            [
                ("web", "stack-web:latest", "sha256:newweb", "stack"),
                ("db", "postgres:16", "sha256:old0pg16", "stack"),
            ]
        )

        with app_lock(STACK, "update"), operation_window(STACK, "update"):
            stack_deployer(root, docker).update()

        assert refusals and refusals[0].reason == "too_late"
