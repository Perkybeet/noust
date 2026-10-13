# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the previews manager does for a Docker Compose application (checklist B-31, B-32).

The compose file a preview runs is the deployer's business
(``tests/test_compose_preview.py``); here is what the manager asks of the deploy and of the
removal: no resource limits (a stack's containers are not in its unit's cgroup), the compose
file the stack was deployed from, the stack's volumes going with the preview, and a monorepo
still refused with the reason.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.exceptions import ValidationError
from noust.core.store import App, NoustStore
from noust.deployers import docker_compose, lifecycle
from noust.managers import previews
from tests.test_previews import (
    BASE,
    PARENT,
    FakeContext,
    clock,
    jobs,
    posted,
    store,
    stored_preview,
    timer,
)

FIXTURES = (clock, jobs, posted, store, timer)


def make_stack(store: NoustStore) -> App:
    """Make the previewed application a Docker Compose stack."""
    app = store.get_app(PARENT)
    assert app is not None
    app.app_type = "docker-compose"
    app.layout = "inplace"
    return store.update_app(app)


class TestWhichApplicationsHavePreviews:
    def test_a_compose_stack_can_have_them(self, store: NoustStore) -> None:
        make_stack(store)

        settings = previews.enable_previews(PARENT, BASE)

        assert settings.base_domain == BASE

    def test_a_monorepo_is_refused_and_told_why(self, store: NoustStore) -> None:
        app = store.get_app(PARENT)
        assert app is not None
        app.app_type = "monorepo"
        store.update_app(app)

        with pytest.raises(ValidationError, match="cannot have previews") as caught:
            previews.enable_previews(PARENT, BASE)

        assert "monorepo" in (caught.value.details or "")


class TestTheDeployOfAStackPreview:
    def deploy(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch, compose_file: str | None
    ) -> dict[str, Any]:
        from noust.web import jobs as jobs_module

        make_stack(store)
        settings = previews.enable_previews(PARENT, BASE)
        called: dict[str, Any] = {}
        monkeypatch.setattr(
            jobs_module, "deploy_app_job", lambda **kwargs: called.update(kwargs) or {}
        )
        monkeypatch.setattr(
            docker_compose,
            "stack_deployer",
            lambda app, **_: SimpleNamespace(compose_file=compose_file),
        )
        parent = store.get_app(PARENT)
        assert parent is not None

        previews._deploy(parent, stored_preview(store), FakeContext(), settings)
        return called

    def test_it_asks_for_no_limits_which_a_stack_refuses(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called = self.deploy(store, monkeypatch, None)

        assert called["memory_max_mb"] is None
        assert called["cpu_quota_percent"] is None
        assert called["layout"] is None
        assert called["app_type"] == "docker-compose"
        assert called["preview_parent"] == PARENT

    def test_it_runs_the_compose_file_the_stack_was_deployed_from(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called = self.deploy(store, monkeypatch, "deploy/compose.prod.yml")

        assert called["compose_file"] == "deploy/compose.prod.yml"

    def test_an_application_of_another_type_is_deployed_as_before(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.web import jobs as jobs_module

        settings = previews.enable_previews(PARENT, BASE)
        called: dict[str, Any] = {}
        monkeypatch.setattr(
            jobs_module, "deploy_app_job", lambda **kwargs: called.update(kwargs) or {}
        )
        parent = store.get_app(PARENT)
        assert parent is not None

        previews._deploy(parent, stored_preview(store), FakeContext(), settings)

        assert called["layout"] == "releases"
        assert called["memory_max_mb"] == previews.PREVIEW_MEMORY_MB
        assert called["compose_file"] is None


class TestTheRemovalOfAPreview:
    def test_its_volumes_go_with_it(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        def delete_app(domain: str, **kwargs: Any) -> Any:
            seen.update(kwargs, domain=domain)
            return SimpleNamespace(warnings=[])

        monkeypatch.setattr(lifecycle, "delete_app", delete_app)

        previews._delete("pr-7-shop-example-com." + BASE, None, None)

        assert seen["remove_volumes"] is True
        assert seen["remove_files"] is True
