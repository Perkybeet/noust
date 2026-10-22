# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a preview is given to run with, made by hand, and managed one by one
(checklist B-32, B-33, B-36, B-37).

The deploy, the update and the removal of a preview's application are the ones
every application goes through, covered elsewhere; here they are fakes, and what
is pinned is the policy around them: which variables a preview is given, the
database of its own and its removal, how a preview is made from a pull request,
a branch or a tag, and what is done to one afterwards.
"""

from __future__ import annotations

from typing import Any

import pytest

from noust.core.exceptions import IntegrationError, SourceError, ValidationError
from noust.core.logger import Logger
from noust.core.preview_store import (
    DATA_EMPTY,
    DATA_SHARED,
    INHERIT_ALL,
    INHERIT_ALLOWLIST,
    MANUAL_BASE,
    PreviewOrigin,
    PreviewStore,
)
from noust.core.store import App, Database, NoustStore
from noust.core.variables.model import PREVIEW
from noust.core.variables.store import VariableStore
from noust.deployers.helpers.env_variables import AppEnvironment
from noust.integrations.github import pulls
from noust.integrations.github.pulls import OpenPullRequest
from noust.managers import preview_env, previews
from tests.test_previews import (
    BASE,
    PARENT,
    REPO,
    FakeContext,
    child_domain,
    clock,
    deployed_child,
    jobs,
    posted,
    store,
    stored_preview,
    timer,
)

FIXTURES = (clock, jobs, posted, store, timer)


def parent_env(monkeypatch: pytest.MonkeyPatch, values: dict[str, str], **extra: Any) -> None:
    """Make the previewed application hold these variables."""
    monkeypatch.setattr(
        preview_env, "read_environment_of", lambda app: AppEnvironment(values, **extra)
    )


def parent_of(store: NoustStore) -> App:
    app = store.get_app(PARENT)
    assert app is not None
    return app


class TestWhatTheSettingsDefaultTo:
    def test_previews_turned_on_now_are_given_only_what_is_named(self, store: NoustStore) -> None:
        previews.enable_previews(PARENT, BASE)

        options = PreviewStore(store).options(PARENT)

        assert (options.inherit_env, options.allow_env) == (INHERIT_ALLOWLIST, ())

    def test_and_a_database_of_their_own_when_the_application_has_one(
        self, store: NoustStore
    ) -> None:
        app = parent_of(store)
        store.create_database(Database(app_id=app.id, name="shop", engine="postgresql"))

        previews.enable_previews(PARENT, BASE)

        assert PreviewStore(store).options(PARENT).data_mode == DATA_EMPTY

    def test_without_one_there_is_nothing_to_isolate(self, store: NoustStore) -> None:
        previews.enable_previews(PARENT, BASE)

        assert PreviewStore(store).options(PARENT).data_mode == DATA_SHARED

    def test_previews_turned_on_before_keep_behaving_as_they_did_until_asked(
        self, store: NoustStore
    ) -> None:
        from noust.core.store import PreviewSettings

        store.save_preview_settings(PreviewSettings(app_domain=PARENT, base_domain=BASE))

        previews.enable_previews(PARENT, None, max_previews=5)

        assert not PreviewStore(store).has_options(PARENT)
        assert PreviewStore(store).options(PARENT).inherit_env == INHERIT_ALL
        previews.enable_previews(PARENT, None, allow_env=["API_URL"])
        assert PreviewStore(store).options(PARENT).allow_env == ("API_URL",)
        assert PreviewStore(store).options(PARENT).inherit_env == INHERIT_ALL

    def test_a_way_that_does_not_exist_is_refused(self, store: NoustStore) -> None:
        with pytest.raises(ValidationError):
            previews.enable_previews(PARENT, BASE, inherit_env="everything")
        with pytest.raises(ValidationError):
            previews.enable_previews(PARENT, BASE, data_mode="mirror")
        with pytest.raises(ValidationError):
            previews.enable_previews(PARENT, BASE, allow_env=["not a name"])
        assert store.get_preview_settings(PARENT) is None

    def test_turning_previews_off_forgets_how_they_were_made(self, store: NoustStore) -> None:
        previews.enable_previews(PARENT, BASE)

        previews.remove_previews_of(PARENT)

        assert not PreviewStore(store).has_options(PARENT)


class TestTheVariablesAPreviewIsGiven:
    def test_only_the_names_allowed_and_never_the_ones_the_unit_sets(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        parent_env(monkeypatch, {"API_URL": "u", "STRIPE_KEY": "sk", "PORT": "3000", "A": "1"})
        settings = previews.enable_previews(PARENT, BASE, allow_env=["API_URL", "PORT", "GONE"])

        composed = preview_env.compose(parent_of(store), settings)

        assert composed.values == {"API_URL": "u"}
        assert composed.given == ("API_URL",)

    def test_all_gives_everything_but_the_excluded(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        parent_env(monkeypatch, {"API_URL": "u", "STRIPE_KEY": "sk"})
        settings = previews.enable_previews(
            PARENT, BASE, inherit_env="all", exclude_env=["STRIPE_KEY"], data_mode="shared"
        )

        assert preview_env.compose(parent_of(store), settings).values == {"API_URL": "u"}

    def test_the_set_defined_for_previews_wins_and_carries_its_marks(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        parent_env(monkeypatch, {"API_URL": "https://shop.example.com"})
        settings = previews.enable_previews(PARENT, BASE, allow_env=["API_URL"])
        app = parent_of(store)
        assert app.id is not None
        variables = VariableStore(store)
        variables.set(PREVIEW, app.id, "API_URL", "https://staging.api", secret=False)
        variables.set(PREVIEW, app.id, "SANDBOX_KEY", "sk_test_1234567890", audience="build")

        composed = preview_env.compose(app, settings)

        assert composed.values == {
            "API_URL": "https://staging.api",
            "SANDBOX_KEY": "sk_test_1234567890",
        }
        assert composed.audience == {"SANDBOX_KEY": "build"}
        assert composed.marks["API_URL"] is False

    def test_a_variable_made_of_references_is_copied_as_the_references(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        parent_env(
            monkeypatch,
            {"NEXTAUTH_URL": "https://shop.example.com"},
            expressions={"NEXTAUTH_URL": "{{app.URL}}"},
        )
        settings = previews.enable_previews(PARENT, BASE, allow_env=["NEXTAUTH_URL"])

        composed = preview_env.compose(parent_of(store), settings)

        # Resolved by the preview, for its own address.
        assert composed.values == {"NEXTAUTH_URL": "{{app.URL}}"}

    def test_a_preview_with_a_database_of_its_own_is_given_nothing_that_reaches_the_applications(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        parent_env(
            monkeypatch,
            {
                "DATABASE_URL": "postgres://u:p@localhost:5432/shop",
                "DB_PASSWORD": "p",
                "API_URL": "u",
            },
        )
        settings = previews.enable_previews(
            PARENT,
            BASE,
            inherit_env="all",
            data_mode="empty",
        )

        composed = preview_env.compose(parent_of(store), settings)

        assert composed.values == {"API_URL": "u"}


class TestADatabaseOfItsOwn:
    def application_with_database(self, store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> App:
        app = parent_of(store)
        store.create_database(Database(app_id=app.id, name="shop", engine="postgresql"))
        parent_env(
            monkeypatch,
            {"DATABASE_URL": "postgres://u:p@localhost:5432/shop", "DB_HOST": "localhost"},
        )
        return app

    def test_it_is_named_for_the_preview_and_replaces_the_variable_that_connects(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = self.application_with_database(store, monkeypatch)

        wish = preview_env.database_wish(app, 7)

        assert wish is not None
        assert (wish.engine, wish.name, wish.env_var) == ("postgresql", "shop_pr7", "DATABASE_URL")
        assert wish.extra_vars is True

    def test_an_application_with_no_database_needs_none(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        parent_env(monkeypatch, {"DATABASE_URL": "postgres://u:p@localhost:5432/other"})

        assert preview_env.database_wish(parent_of(store), 7) is None

    def test_the_deploy_asks_for_it_and_records_it_before_anything_is_made(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.web import jobs as jobs_module

        self.application_with_database(store, monkeypatch)
        settings = previews.enable_previews(PARENT, BASE)
        called: dict[str, Any] = {}
        monkeypatch.setattr(
            jobs_module, "deploy_app_job", lambda **kwargs: called.update(kwargs) or {}
        )

        previews._deploy(parent_of(store), stored_preview(store), FakeContext(), settings)

        assert called["database"]["name"] == "shop_pr7"
        assert called["database"]["env_var"] == "DATABASE_URL"
        origin = PreviewStore(store).origin(child_domain())
        assert origin.databases == ("postgresql/shop_pr7",)
        assert "DATABASE_URL" in origin.owned_env

    def test_a_shared_preview_asks_for_none(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.web import jobs as jobs_module

        self.application_with_database(store, monkeypatch)
        settings = previews.enable_previews(PARENT, BASE, data_mode="shared")
        called: dict[str, Any] = {}
        monkeypatch.setattr(
            jobs_module, "deploy_app_job", lambda **kwargs: called.update(kwargs) or {}
        )

        previews._deploy(parent_of(store), stored_preview(store), FakeContext(), settings)

        assert called["database"] is None

    def test_it_is_dropped_with_the_preview_and_a_failure_is_a_warning(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core.exceptions import DatabaseError
        from noust.managers.database import service as service_module

        dropped: list[tuple[str, str]] = []

        class Service:
            def drop(self, engine: str, name: str, **kwargs: Any) -> None:
                dropped.append((engine, name))
                if name == "broken":
                    raise DatabaseError("the engine said no")

        monkeypatch.setattr(service_module, "DatabaseService", Service)
        monkeypatch.setattr(previews, "_delete", lambda *args: ())
        stored_preview(store)
        deployed_child(store)
        PreviewStore(store).save_origin(
            PreviewOrigin(child_domain(), databases=("postgresql/shop_pr7", "mysql/broken"))
        )

        warnings = previews.remove_preview(child_domain())

        assert dropped == [("postgresql", "shop_pr7"), ("mysql", "broken")]
        assert len(warnings) == 1 and "the engine said no" in warnings[0]
        assert PreviewStore(store).origin(child_domain()).databases == ()


class TestAnExistingPreviewFollowsTheSettings:
    def child_with(self, store: NoustStore, monkeypatch: pytest.MonkeyPatch, values: dict) -> list:
        deployed_child(store)
        written: list[dict[str, str]] = []
        from noust.deployers.helpers import app_env

        monkeypatch.setattr(
            app_env,
            "read_environment_of",
            lambda app: AppEnvironment(dict(values)),
        )
        monkeypatch.setattr(
            app_env, "write_app_env", lambda app, vals, **_: written.append(dict(vals))
        )
        return written

    def test_what_the_settings_no_longer_give_is_taken_back_and_nothing_else(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        parent_env(monkeypatch, {"A": "1", "B": "2"})
        settings = previews.enable_previews(PARENT, BASE, allow_env=["A"])
        written = self.child_with(store, monkeypatch, {"A": "1", "B": "2", "HAND_ADDED": "x"})
        PreviewStore(store).save_origin(PreviewOrigin(child_domain(), given_env=("A", "B")))

        previews._sync_from_parent(child_domain(), parent_of(store), settings)

        assert written == [{"A": "1", "HAND_ADDED": "x"}]
        assert PreviewStore(store).origin(child_domain()).given_env == ("A",)

    def test_the_variables_made_for_it_are_never_taken_back(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        parent_env(monkeypatch, {"A": "1"})
        settings = previews.enable_previews(PARENT, BASE, allow_env=["A"])
        written = self.child_with(
            store, monkeypatch, {"A": "1", "DATABASE_URL": "postgres://own/db"}
        )
        PreviewStore(store).save_origin(
            PreviewOrigin(
                child_domain(), given_env=("A", "DATABASE_URL"), owned_env=("DATABASE_URL",)
            )
        )

        previews._sync_from_parent(child_domain(), parent_of(store), settings)

        assert written == []  # nothing to change

    def test_the_set_defined_for_previews_is_applied_again(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        parent_env(monkeypatch, {})
        settings = previews.enable_previews(PARENT, BASE)
        app = parent_of(store)
        assert app.id is not None
        VariableStore(store).set(PREVIEW, app.id, "API_URL", "https://new")
        written = self.child_with(store, monkeypatch, {"API_URL": "https://old"})

        previews._sync_from_parent(child_domain(), app, settings)

        assert written == [{"API_URL": "https://new"}]


def open_pull(**changes: Any) -> OpenPullRequest:
    fields: dict[str, Any] = {
        "number": 42,
        "title": "Add search",
        "branch": "feature/search",
        "base_branch": "main",
        "head_sha": "c" * 40,
        "author": "alice",
        "author_association": "MEMBER",
        "draft": False,
        "from_fork": False,
        "url": "https://github.com/acme/shop/pull/42",
    }
    fields.update(changes)
    return OpenPullRequest(**fields)


class TestMakingOneByHand:
    @pytest.fixture(autouse=True)
    def turned_on(self, store: NoustStore) -> None:
        previews.enable_previews(PARENT, BASE, data_mode="shared")

    def test_from_an_open_pull_request(
        self, store: NoustStore, jobs: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pulls, "get", lambda repository, number, installation=None: open_pull())

        created = previews.create_preview(PARENT, pull_request=42, actor="ana")

        record = created.record
        assert (record.number, record.branch, record.head_sha) == (42, "feature/search", "c" * 40)
        assert (record.provider, record.repository, record.status) == ("github", REPO, "pending")
        assert record.domain == f"pr-42-shop-example-com.{BASE}"
        assert created.job_id == "job-1" and jobs[0]["actor"] == "ana"
        assert jobs[0]["job_type"].name == "DEPLOY"
        assert PreviewStore(store).origin(record.domain).created_by == "ana"

    def test_a_pull_request_that_is_closed_or_from_a_fork_is_refused(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pulls, "get", lambda repository, number, installation=None: None)
        with pytest.raises(ValidationError, match="is not open"):
            previews.create_preview(PARENT, pull_request=42)

        monkeypatch.setattr(
            pulls, "get", lambda repository, number, installation=None: open_pull(from_fork=True)
        )
        with pytest.raises(ValidationError, match="fork"):
            previews.create_preview(PARENT, pull_request=42)
        assert previews.create_preview(PARENT, pull_request=42, allow_fork=True).record.number == 42

    def test_a_pull_request_that_cannot_be_read_says_how_to_make_one_from_a_branch(
        self, store: NoustStore
    ) -> None:
        with pytest.raises(IntegrationError, match="GitHub App"):
            previews.create_preview(PARENT, pull_request=42)

    def test_from_a_branch_and_from_a_tag_with_numbers_above_every_pull_request(
        self, store: NoustStore, jobs: list[dict[str, Any]]
    ) -> None:
        first = previews.create_preview(PARENT, branch="feature/login")
        second = previews.create_preview(PARENT, tag="v1.4.0")

        assert (first.record.number, second.record.number) == (MANUAL_BASE + 1, MANUAL_BASE + 2)
        assert first.record.domain == f"x-1-shop-example-com.{BASE}"
        assert second.record.domain == f"x-2-shop-example-com.{BASE}"
        assert (first.kind, second.kind) == ("branch", "tag")
        assert (first.record.branch, second.record.branch) == ("feature/login", "v1.4.0")
        assert first.record.provider == "manual" and first.record.repository is None
        origin = PreviewStore(store).origin(first.record.domain)
        assert (origin.kind, origin.ref) == ("branch", "feature/login")
        assert len(jobs) == 2

    def test_exactly_one_of_the_three(self, store: NoustStore) -> None:
        with pytest.raises(ValidationError, match="exactly one"):
            previews.create_preview(PARENT)
        with pytest.raises(ValidationError, match="exactly one"):
            previews.create_preview(PARENT, branch="a", tag="b")

    def test_a_ref_git_would_refuse_is_refused(self, store: NoustStore) -> None:
        with pytest.raises((ValidationError, SourceError)):
            previews.create_preview(PARENT, branch="--upload-pack=evil")

    def test_the_limit_applies(self, store: NoustStore) -> None:
        previews.enable_previews(PARENT, BASE, max_previews=1)
        previews.create_preview(PARENT, branch="a")

        with pytest.raises(ValidationError, match="1 of 1"):
            previews.create_preview(PARENT, branch="b")

    def test_previews_must_be_on(self, store: NoustStore) -> None:
        store.delete_preview_settings(PARENT)

        with pytest.raises(ValidationError, match="Previews are off"):
            previews.create_preview(PARENT, branch="a")

    def test_the_terminal_records_it_and_builds_it_itself(
        self, store: NoustStore, jobs: list[dict[str, Any]]
    ) -> None:
        created = previews.create_preview(PARENT, branch="a", queue=False)

        assert created.job_id is None and jobs == []
        assert created.record.status == "pending"

    def test_a_queue_with_no_room_leaves_the_preview_failed_not_pending(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core.exceptions import QueueFullError

        class Full:
            def create_job(self, **kwargs: Any) -> Any:
                raise QueueFullError("the queue is full", limit=3)

        monkeypatch.setattr(previews, "_jobs", Full)

        created = previews.create_preview(PARENT, branch="a")

        assert created.job_id is None
        record = store.get_preview(PARENT, created.record.number)
        assert record is not None and record.status == "failed"


class TestListingPullRequests:
    def test_with_the_preview_each_already_has(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stored_preview(store, number=42)
        monkeypatch.setattr(
            pulls,
            "list_open",
            lambda repository, installation=None: [
                open_pull(),
                open_pull(number=43, from_fork=True, author_association="NONE"),
            ],
        )

        found = previews.list_pull_requests(PARENT)

        assert [(p["number"], p["status"], p["trusted"]) for p in found] == [
            (42, "ready", True),
            (43, None, False),
        ]


class TestManagingOne:
    def test_redeploy_builds_again_from_the_head_of_the_branch(
        self, store: NoustStore, jobs: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        previews.enable_previews(PARENT, BASE)
        stored_preview(store)
        deployed_child(store)
        monkeypatch.setattr(
            pulls,
            "get",
            lambda repository, number, installation=None: open_pull(number=7, head_sha="d" * 40),
        )

        created = previews.redeploy_preview(child_domain(), actor="ana")

        assert created.record.status == "pending" and created.record.head_sha == "d" * 40
        assert jobs[0]["job_type"].name == "UPDATE"

    def test_a_preview_being_built_or_removed_cannot_be_built_again(
        self, store: NoustStore
    ) -> None:
        previews.enable_previews(PARENT, BASE)
        stored_preview(store, status="deploying")

        with pytest.raises(ValidationError, match="being built"):
            previews.redeploy_preview(child_domain())

    def test_something_that_is_not_a_preview_is_refused(self, store: NoustStore) -> None:
        with pytest.raises(ValidationError, match="not a preview"):
            previews.redeploy_preview("shop.example.com")

    def test_stop_and_start(self, store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> None:
        from noust.managers import service_manager

        calls: list[tuple[str, str]] = []

        class Manager:
            def __init__(self, verbose: bool = False) -> None:
                pass

            def service_exists(self, name: str) -> bool:
                return True

            def stop(self, name: str) -> None:
                calls.append(("stop", name))

            def start(self, name: str) -> None:
                calls.append(("start", name))

        monkeypatch.setattr(service_manager, "ServiceManager", Manager)
        previews.enable_previews(PARENT, BASE)
        stored_preview(store)
        deployed_child(store)

        stopped = previews.stop_preview(child_domain())
        started = previews.start_preview(child_domain())

        assert (stopped.status, started.status) == ("stopped", "ready")
        assert [action for action, _ in calls] == ["stop", "start"]

    def test_stopping_what_never_ran_says_so(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.managers import service_manager

        class Manager:
            def __init__(self, verbose: bool = False) -> None:
                pass

            def service_exists(self, name: str) -> bool:
                return False

        monkeypatch.setattr(service_manager, "ServiceManager", Manager)
        previews.enable_previews(PARENT, BASE)
        stored_preview(store)

        with pytest.raises(ValidationError, match="no service"):
            previews.stop_preview(child_domain())

    def test_the_last_build_log(self, store: NoustStore, tmp_path: Any) -> None:
        deployed_child(store)
        log = store.db_path.parent / "deploy-logs" / "build.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("npm ci\nbuilt\n")
        row = store.record_deployment_start(child_domain(), "webhook")
        store.annotate_deployment(row, log_path=str(log))

        assert "built" in (previews.last_build_log(child_domain()) or "")
        assert previews.last_build_log("nothing.example.com") is None

    def test_building_in_this_process_reports_through_the_logger(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[Any] = []
        monkeypatch.setattr(
            previews,
            "preview_deploy_job",
            lambda *a, job_context=None, **k: seen.append(job_context) or {},
        )

        previews.build_in_process(PARENT, 7, Logger())

        assert isinstance(seen[0], previews.TerminalContext)
        seen[0].update("Building", 10)
        seen[0].log("careful", "warning")
        seen[0].set_metadata("x", 1)
