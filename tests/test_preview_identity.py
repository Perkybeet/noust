# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a preview is told about itself (checklist B-35).

A preview's code often needs to know which pull request it is for (a banner, a
database name, a seed): it is given ``NOUST_PR_NUMBER`` and ``NOUST_PREVIEW_OF``
beside the variables every application gets, and no other application is.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.preview_store import MANUAL_BASE, preview_identity
from noust.core.store import App, NoustStore, PreviewRecord
from noust.core.variables.expressions import APP_FIELDS
from noust.core.variables.predefined import RESERVED, Deployment
from noust.deployers.helpers.env_variables import deployment_for

PARENT = "shop.example.com"
CHILD = "pr-7-shop-example-com.previews.example.com"
BRANCH_CHILD = "x-1-shop-example-com.previews.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    instance.create_app(
        App(domain=PARENT, app_type="nodejs", port=3000, app_path=str(tmp_path / "shop"))
    )
    instance.create_app(
        App(
            domain=CHILD,
            app_type="nodejs",
            port=3001,
            app_path=str(tmp_path / "pr"),
            preview_parent=PARENT,
        )
    )
    instance.create_app(
        App(
            domain=BRANCH_CHILD,
            app_type="nodejs",
            port=3002,
            app_path=str(tmp_path / "x"),
            preview_parent=PARENT,
        )
    )
    for domain, number in ((CHILD, 7), (BRANCH_CHILD, MANUAL_BASE + 1)):
        instance.save_preview(
            PreviewRecord(
                parent_domain=PARENT,
                domain=domain,
                number=number,
                branch="feature",
                provider="github" if number < MANUAL_BASE else "manual",
                expires_at="2099-01-01T00:00:00+00:00",
            )
        )
    yield instance
    instance.close()
    NoustStore.reset_instance()


class TestWhoAPreviewIs:
    def test_a_pull_request_preview_knows_its_number_and_its_application(
        self, store: NoustStore
    ) -> None:
        assert preview_identity(store, CHILD) == (7, PARENT)

    def test_a_preview_of_a_branch_has_no_pull_request_number(self, store: NoustStore) -> None:
        assert preview_identity(store, BRANCH_CHILD) == (None, PARENT)

    def test_an_ordinary_application_is_no_preview(self, store: NoustStore) -> None:
        assert preview_identity(store, PARENT) == (None, None)
        assert preview_identity(store, "gone.example.com") == (None, None)


class TestWhatItIsGiven:
    def test_the_environment_of_a_pull_request_preview(self) -> None:
        deployment = Deployment(domain=CHILD, pull_request=7, preview_of=PARENT)

        env = deployment.environment()

        assert env["NOUST_PR_NUMBER"] == "7"
        assert env["NOUST_PREVIEW_OF"] == PARENT

    def test_a_preview_of_a_branch_is_given_only_its_application(self) -> None:
        env = Deployment(domain=CHILD, preview_of=PARENT).environment()

        assert "NOUST_PR_NUMBER" not in env
        assert env["NOUST_PREVIEW_OF"] == PARENT

    def test_an_ordinary_application_gets_neither(self) -> None:
        env = Deployment(domain=PARENT).environment()

        assert "NOUST_PR_NUMBER" not in env and "NOUST_PREVIEW_OF" not in env

    def test_the_build_is_given_them_too_they_do_not_change_between_deploys(self) -> None:
        env = Deployment(domain=CHILD, pull_request=7, preview_of=PARENT).environment(build=True)

        assert env["NOUST_PR_NUMBER"] == "7"

    def test_the_names_are_noust_s_not_the_operator_s(self) -> None:
        assert {"NOUST_PR_NUMBER", "NOUST_PREVIEW_OF"} <= RESERVED

    def test_a_value_can_refer_to_them(self) -> None:
        assert {"PR_NUMBER", "PREVIEW_OF"} <= set(APP_FIELDS)

    def test_the_deployment_of_a_stored_preview_carries_them(self, store: NoustStore) -> None:
        app = store.get_app(CHILD)
        assert app is not None

        deployment = deployment_for(app)

        assert deployment.pull_request == 7
        assert deployment.preview_of == PARENT
