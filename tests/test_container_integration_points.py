# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where a container application is treated as the Compose project it runs as.

The unit of a Compose project starts its containers once and stays active, so
what the unit says is not what a process does: the monitor, the diagnosis, the
state and the sampling look at its containers. A container application is one,
and is excluded from what does not apply to a container (an account of its
own, the build sandbox, blue/green instances), through the same definitions
as a stack.
"""

# ruff: noqa: F811

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.dependencies import check_deployment_ready
from noust.core.exceptions import ValidationError
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore, is_compose_backed
from noust.deployers import bluegreen
from noust.deployers.helpers import sandbox as build_sandbox
from noust.deployers.helpers.sandbox import sandbox_warning
from noust.deployers.unit_environment import _unit_of
from noust.managers import app_identity
from noust.managers.database.service import OWN_DATABASE_TYPES
from tests.test_sampling_plan import (  # noqa: F401 - fixtures used by name
    CONTAINER,
    FakeManager,
    app,
    builder,
    lay_unit,
    mount,
)

DOMAIN = "shop.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


def container_app(**row: object) -> App:
    return App(domain=DOMAIN, app_type="container", app_path="/var/www/apps/shop", **row)  # type: ignore[arg-type]


def test_a_container_and_a_stack_are_the_two_compose_backed_types():
    assert is_compose_backed("container") and is_compose_backed("docker-compose")
    assert not any(is_compose_backed(kind) for kind in ("nodejs", "static", "monorepo", "php-fpm"))


def test_the_monitor_samples_the_containers_and_not_the_oneshot_unit(mount: Path) -> None:
    lay_unit(mount, f"/system.slice/docker-{CONTAINER}.scope")
    runner = FakeRunner().script(["docker", "ps"], stdout=f"{CONTAINER}\tshop-example-com-app-1\n")

    plan = builder(mount, FakeManager({}), runner).build([app(DOMAIN, app_type="container")])[
        DOMAIN
    ]

    assert plan.kind == "compose"
    assert plan.source == "docker"
    assert plan.targets[0].name == "shop-example-com-app-1"


class TestWhatDoesNotApplyToAContainer:
    def test_it_has_no_account_of_its_own(self):
        reason = app_identity.ineligible(container_app())

        assert reason is not None and "containers" in reason
        assert "container" in app_identity.EXCLUDED_TYPES

    def test_its_builds_are_the_daemons_not_the_sandboxs(self, store: NoustStore):
        with pytest.raises(ValidationError, match="do not run in the sandbox"):
            build_sandbox.refuse_unsupported(container_app())

    def test_the_sandbox_has_nothing_to_warn_about_for_it(self, store: NoustStore):
        assert sandbox_warning(container_app()) is None

    def test_blue_green_refuses_it_and_says_why(self, store: NoustStore):
        row = container_app()

        with pytest.raises(ValidationError, match="deploys in place") as refused:
            bluegreen.check_eligible(row, store=store)

        assert "container application" in refused.value.message

    def test_it_has_no_unit_to_carry_variables(self, store: NoustStore):
        with pytest.raises(ValidationError, match="no unit of its own"):
            _unit_of(container_app(), store)

    def test_a_database_is_not_created_with_it(self):
        assert "container" in OWN_DATABASE_TYPES


def test_the_dependency_check_asks_for_docker(runner: FakeRunner) -> None:
    runner.only_knows("git")

    can_deploy, missing, _warnings = check_deployment_ready(
        app_type="container", package_manager="auto", runner=runner
    )

    assert can_deploy is False
    assert any("docker" in item and "container" in item for item in missing)
