# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How previews are named, told to stay out of search engines, and approved
(checklist B-30, B-34).

The deploy, update and removal of a preview's application are the ones every application
goes through, covered elsewhere; here they are fakes. What is pinned: the domain template
and what it refuses, the ``noindex`` a preview's site is written with, and the approval a
pull request from a fork waits for, commit by commit.
"""

from __future__ import annotations

from typing import Any

import pytest

from noust.core.exceptions import ValidationError
from noust.core.preview_names import check_template, new_random, preview_domain_for
from noust.core.preview_store import (
    DATA_CLONE,
    FORK_APPROVE,
    FORK_REFUSE,
    MANUAL_BASE,
    PreviewStore,
)
from noust.core.store import NoustStore
from noust.managers import previews
from tests.test_previews import (
    BASE,
    PARENT,
    FakeContext,
    child_domain,
    clock,
    deployed_child,
    enable,
    event,
    jobs,
    posted,
    store,
    stored_preview,
    timer,
)

FIXTURES = (clock, jobs, posted, store, timer, FakeContext)


class TestTheTemplate:
    def test_without_one_the_name_is_what_it_always_was(self) -> None:
        assert preview_domain_for(PARENT, 12, BASE) == f"pr-12-shop-example-com.{BASE}"
        assert preview_domain_for(PARENT, 12, BASE, template=None) == (
            f"pr-12-shop-example-com.{BASE}"
        )

    def test_the_placeholders_are_filled(self) -> None:
        assert (
            preview_domain_for(PARENT, 12, BASE, template="{{pr_id}}.{{domain}}")
            == "12.shop.example.com"
        )
        assert (
            preview_domain_for(PARENT, 12, BASE, template="{{pr_id}}-{{name}}.{{base}}")
            == f"12-shop-example-com.{BASE}"
        )

    def test_random_is_the_one_given_or_a_new_one(self) -> None:
        assert (
            preview_domain_for(PARENT, 1, BASE, template="{{random}}.{{base}}", random="abc12345")
            == f"abc12345.{BASE}"
        )
        first = preview_domain_for(PARENT, 1, BASE, template="{{random}}.{{base}}")
        second = preview_domain_for(PARENT, 1, BASE, template="{{random}}.{{base}}")
        assert first != second
        assert len(new_random()) == 8 and new_random().isalnum()

    def test_a_preview_made_by_hand_is_x_and_its_count(self) -> None:
        assert (
            preview_domain_for(PARENT, MANUAL_BASE + 3, BASE, template="{{pr_id}}.{{base}}")
            == f"x3.{BASE}"
        )

    def test_spaces_inside_the_braces_and_capitals_do_not_matter(self) -> None:
        assert (
            preview_domain_for(PARENT, 5, BASE, template="PR-{{ pr_id }}.{{base}}")
            == f"pr-5.{BASE}"
        )

    @pytest.mark.parametrize(
        ("template", "field"),
        [
            ("", "domain_template"),
            ("preview.{{base}}", "domain_template"),  # nothing tells two previews apart
            ("{{pr_id}}-{{nope}}.{{base}}", "domain_template"),
            ("{{pr_id}} bad.{{base}}", "domain_template"),
            ("{{pr_id}}", "domain_template"),  # not a domain
            ("x" * 300 + "{{pr_id}}.{{base}}", "domain_template"),
            ("{{pr_id}}-{{domain}}-{{domain}}-{{domain}}.{{base}}", "domain_template"),
        ],
    )
    def test_what_cannot_name_a_preview_is_refused(self, template: str, field: str) -> None:
        with pytest.raises(ValidationError) as caught:
            check_template(template)

        assert caught.value.field == field

    def test_a_good_template_is_accepted_trimmed(self) -> None:
        assert check_template("  pr-{{pr_id}}.{{base}}  ") == "pr-{{pr_id}}.{{base}}"

    def test_a_name_that_does_not_fit_is_refused_with_the_reason(self) -> None:
        long_base = "a" * 50 + ".example.com"
        with pytest.raises(ValidationError) as caught:
            preview_domain_for(PARENT, 1, long_base, template="{{pr_id}}-{{domain}}.{{base}}")

        assert "too long" in caught.value.message


class TestSettingsKeepThePolicy:
    def test_previews_turned_on_name_themselves_the_old_way_and_say_noindex(
        self, store: NoustStore
    ) -> None:
        previews.enable_previews(PARENT, BASE)

        options = PreviewStore(store).options(PARENT)

        assert options.domain_template is None
        assert options.noindex is True
        assert options.fork_policy == FORK_REFUSE

    def test_a_template_a_policy_and_a_copy_are_stored(self, store: NoustStore) -> None:
        from noust.core.store import Database

        app = store.get_app(PARENT)
        assert app is not None
        store.create_database(Database(app_id=app.id, name="shop", engine="postgresql"))

        previews.enable_previews(
            PARENT,
            BASE,
            domain_template="{{pr_id}}-{{name}}.{{base}}",
            noindex=False,
            fork_policy=FORK_APPROVE,
            data_mode=DATA_CLONE,
        )

        options = PreviewStore(store).options(PARENT)
        assert options.domain_template == "{{pr_id}}-{{name}}.{{base}}"
        assert options.noindex is False
        assert options.fork_policy == FORK_APPROVE
        assert options.data_mode == DATA_CLONE

    def test_an_empty_template_goes_back_to_the_default(self, store: NoustStore) -> None:
        previews.enable_previews(PARENT, BASE, domain_template="{{pr_id}}.{{domain}}")

        previews.enable_previews(PARENT, None, domain_template="")

        assert PreviewStore(store).options(PARENT).domain_template is None

    def test_settings_not_given_keep_what_was_set(self, store: NoustStore) -> None:
        previews.enable_previews(PARENT, BASE, fork_policy=FORK_APPROVE, noindex=False)

        previews.enable_previews(PARENT, None, max_previews=5)

        options = PreviewStore(store).options(PARENT)
        assert options.fork_policy == FORK_APPROVE and options.noindex is False

    def test_a_template_that_cannot_name_one_changes_nothing(self, store: NoustStore) -> None:
        with pytest.raises(ValidationError):
            previews.enable_previews(PARENT, BASE, domain_template="same.{{base}}")

        assert store.get_preview_settings(PARENT) is None

    def test_a_copy_of_the_data_needs_a_database_to_copy(self, store: NoustStore) -> None:
        with pytest.raises(ValidationError) as caught:
            previews.enable_previews(PARENT, BASE, data_mode=DATA_CLONE)

        assert caught.value.field == "data_mode"

    def test_a_fork_policy_that_does_not_exist_is_refused(self, store: NoustStore) -> None:
        with pytest.raises(ValidationError) as caught:
            previews.enable_previews(PARENT, BASE, fork_policy="allow")

        assert caught.value.field == "fork_policy"

    def test_turning_previews_off_forgets_the_policy(self, store: NoustStore) -> None:
        previews.enable_previews(PARENT, BASE, fork_policy=FORK_APPROVE)

        previews.remove_previews_of(PARENT)

        assert PreviewStore(store).options(PARENT).fork_policy == FORK_REFUSE
        with store._transaction() as cursor:
            cursor.execute("SELECT COUNT(*) FROM preview_policy")
            assert cursor.fetchone()[0] == 0

    def test_the_template_names_the_preview_a_pull_request_gets(
        self, store: NoustStore, jobs: list[dict[str, Any]], posted: list[dict[str, Any]]
    ) -> None:
        previews.enable_previews(PARENT, BASE, domain_template="{{pr_id}}.{{domain}}")

        previews.handle_pull_request(event())

        record = store.get_preview(PARENT, 7)
        assert record is not None and record.domain == "7.shop.example.com"

    def test_the_name_a_preview_has_is_kept_when_the_template_changes(
        self, store: NoustStore, jobs: list[dict[str, Any]], posted: list[dict[str, Any]]
    ) -> None:
        previews.enable_previews(PARENT, BASE)
        previews.handle_pull_request(event())
        previews.enable_previews(PARENT, None, domain_template="{{pr_id}}.{{domain}}")

        previews.handle_pull_request(event(action=previews.PullRequestAction.UPDATED))

        record = store.get_preview(PARENT, 7)
        assert record is not None and record.domain == child_domain(7)


class TestSearchEnginesAreToldToStayOut:
    def test_a_preview_of_an_application_that_did_not_turn_it_off(self, store: NoustStore) -> None:
        previews.enable_previews(PARENT, BASE)
        deployed_child(store)
        stored_preview(store)

        assert PreviewStore(store).noindex_for(child_domain()) is True

    def test_not_one_that_was_asked_not_to(self, store: NoustStore) -> None:
        previews.enable_previews(PARENT, BASE, noindex=False)
        deployed_child(store)
        stored_preview(store)

        assert PreviewStore(store).noindex_for(child_domain()) is False

    def test_never_the_application_itself(self, store: NoustStore) -> None:
        previews.enable_previews(PARENT, BASE)

        assert PreviewStore(store).noindex_for(PARENT) is False
        assert PreviewStore(store).noindex_for("elsewhere.example.com") is False

    def test_the_site_is_written_with_the_header(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.managers.site_policy import SitePolicy
        from noust.managers.webserver import WebServerManager

        previews.enable_previews(PARENT, BASE)
        deployed_child(store)
        stored_preview(store)
        manager = WebServerManager.__new__(WebServerManager)
        manager.store = store  # type: ignore[attr-defined]
        manager.logger = type("L", (), {"debug": lambda *a, **k: None})()  # type: ignore[attr-defined]

        policy = manager._with_preview_rules(child_domain(), SitePolicy())

        assert policy.noindex is True
        assert manager._with_preview_rules(PARENT, SitePolicy()).noindex is False


class TestApproval:
    FORK = {"from_fork": True, "author": "mallory", "association": "NONE"}

    def hold(self, **extra: Any) -> None:
        previews.handle_pull_request(event(**{**self.FORK, **extra}))

    def test_by_default_a_fork_gets_no_preview(
        self, store: NoustStore, jobs: list[dict[str, Any]], posted: list[dict[str, Any]]
    ) -> None:
        enable(store)

        self.hold()

        assert store.get_preview(PARENT, 7) is None and jobs == []

    def test_asked_to_it_waits_for_an_operator_and_builds_nothing(
        self, store: NoustStore, jobs: list[dict[str, Any]], posted: list[dict[str, Any]]
    ) -> None:
        enable(store)
        previews.enable_previews(PARENT, None, fork_policy=FORK_APPROVE)

        self.hold()

        record = store.get_preview(PARENT, 7)
        assert record is not None and record.status == previews.AWAITING
        assert record.head_sha == "a" * 40
        assert jobs == []
        assert "approve" in posted[-1]["body"] and "Waiting for approval" in posted[-1]["body"]
        # Nothing of the fork's code is described as running.
        assert "| URL |" not in posted[-1]["body"]

    def test_a_bot_is_not_something_to_approve(
        self, store: NoustStore, jobs: list[dict[str, Any]], posted: list[dict[str, Any]]
    ) -> None:
        enable(store)
        previews.enable_previews(PARENT, None, fork_policy=FORK_APPROVE)

        previews.handle_pull_request(event(bot=True, author="dependabot[bot]"))

        assert store.get_preview(PARENT, 7) is None

    def test_approving_builds_the_commit_that_was_waiting(
        self, store: NoustStore, jobs: list[dict[str, Any]], posted: list[dict[str, Any]]
    ) -> None:
        enable(store)
        previews.enable_previews(PARENT, None, fork_policy=FORK_APPROVE)
        self.hold()

        created = previews.approve_preview(child_domain(), actor="ana")

        assert created.record.status == previews.PENDING
        assert len(jobs) == 1 and jobs[0]["kwargs"] == {"parent_domain": PARENT, "number": 7}
        origin = PreviewStore(store).origin(child_domain())
        assert (origin.approved_sha, origin.approved_by) == ("a" * 40, "ana")

    def test_a_push_of_another_commit_asks_again(
        self, store: NoustStore, jobs: list[dict[str, Any]], posted: list[dict[str, Any]]
    ) -> None:
        enable(store)
        previews.enable_previews(PARENT, None, fork_policy=FORK_APPROVE)
        self.hold()
        previews.approve_preview(child_domain(), actor="ana")
        jobs.clear()

        self.hold(action=previews.PullRequestAction.UPDATED, head_sha="b" * 40)

        record = store.get_preview(PARENT, 7)
        assert record is not None
        assert (record.status, record.head_sha) == (previews.AWAITING, "b" * 40)
        assert jobs == []

    def test_the_commit_that_was_approved_arriving_again_is_built(
        self, store: NoustStore, jobs: list[dict[str, Any]], posted: list[dict[str, Any]]
    ) -> None:
        enable(store)
        previews.enable_previews(PARENT, None, fork_policy=FORK_APPROVE)
        self.hold()
        previews.approve_preview(child_domain(), actor="ana")
        jobs.clear()

        self.hold(action=previews.PullRequestAction.UPDATED)

        assert len(jobs) == 1

    def test_only_what_waits_can_be_approved(
        self, store: NoustStore, jobs: list[dict[str, Any]], posted: list[dict[str, Any]]
    ) -> None:
        enable(store)
        deployed_child(store)
        stored_preview(store, status="ready")

        with pytest.raises(ValidationError, match="not waiting for approval"):
            previews.approve_preview(child_domain())
        with pytest.raises(ValidationError, match="not waiting for approval"):
            previews.reject_preview(child_domain())

    def test_rejecting_forgets_it_and_says_so_on_the_pull_request(
        self, store: NoustStore, jobs: list[dict[str, Any]], posted: list[dict[str, Any]]
    ) -> None:
        enable(store)
        previews.enable_previews(PARENT, None, fork_policy=FORK_APPROVE)
        self.hold()

        previews.reject_preview(child_domain(), actor="ana")

        assert store.get_preview(PARENT, 7) is None
        assert "did not approve" in posted[-1]["body"]

    def test_what_waits_cannot_be_rebuilt_around_the_approval(
        self, store: NoustStore, jobs: list[dict[str, Any]], posted: list[dict[str, Any]]
    ) -> None:
        enable(store)
        previews.enable_previews(PARENT, None, fork_policy=FORK_APPROVE)
        self.hold()

        with pytest.raises(ValidationError, match="waiting for an operator"):
            previews.redeploy_preview(child_domain())
        with pytest.raises(ValidationError, match="waiting for an operator"):
            previews.rebuild_preview(PARENT, 7, actor="ci")

    def test_what_waits_counts_toward_the_limit(
        self, store: NoustStore, jobs: list[dict[str, Any]], posted: list[dict[str, Any]]
    ) -> None:
        previews.enable_previews(PARENT, BASE, max_previews=1, fork_policy=FORK_APPROVE)
        self.hold(number=1)

        self.hold(number=2)

        assert store.get_preview(PARENT, 2) is None

    def test_an_approved_commit_is_built_over_the_applications_own_branch(
        self,
        store: NoustStore,
        jobs: list[dict[str, Any]],
        posted: list[dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        enable(store)
        previews.enable_previews(PARENT, None, fork_policy=FORK_APPROVE)
        self.hold()
        previews.approve_preview(child_domain(), actor="ana")
        calls: list[tuple[str, Any]] = []

        def deploy(
            parent: Any, record: Any, context: Any, settings: Any, *, by_commit: bool = False
        ):
            calls.append(("deploy", by_commit))
            deployed_child(store)
            return {"status": "deployed"}

        def update(domain: str, commit: str | None, context: Any):
            calls.append(("update", commit))
            return {"status": "updated"}

        monkeypatch.setattr(previews, "_deploy", deploy)
        monkeypatch.setattr(previews, "_update", update)

        previews.preview_deploy_job(PARENT, 7, job_context=FakeContext())

        assert calls == [("deploy", True), ("update", "a" * 40)]
        record = store.get_preview(PARENT, 7)
        assert record is not None and record.status == previews.READY
