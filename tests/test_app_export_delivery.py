# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How an application is delivered travels with its export (checklist B-80, B-08, B-21, B-23, B-17).

The export carries the history it keeps, how its unit stops, whether a rollback pauses
what deploys it by itself, when it updates itself, and how its previews are named and
what they are given. An import puts them back, a part that is refused is reported and
the rest goes on, and a clone leaves the schedule behind: it is another application.
What is never exported is a state of the moment, a pause or a freeze.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core import app_rollout_store, update_schedule_store
from noust.core.exceptions import DeploymentError, ValidationError
from noust.core.preview_store import PreviewOptions, PreviewStore
from noust.core.store import App, NoustStore
from noust.deployers import app_export
from noust.deployers.app_clone import plan_clone
from noust.deployers.app_export import (
    apply_import,
    export_app,
    plan_import,
    validate_document,
)
from tests.test_app_clone import app as clone_app
from tests.test_app_export import (
    DOMAIN,
    NEW,
    FakeCron,
    cron,
    engine,
    fake_deploy,
    no_sweep_timer,
    shop,
    store,
)

# Fixtures of the neighbouring modules, used by name in the signatures below.
FIXTURES = (clone_app, cron, engine, no_sweep_timer, shop, store)

SOURCE = "https://github.com/acme/shop.git"


class FakeScheduler:
    """Writes the schedule row the real one writes, without a timer."""

    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def set(self, domain: str, calendar: str, **options: Any) -> Any:
        self.calls.append(f"schedule {calendar}")
        return update_schedule_store.save(
            domain,
            calendar,
            enabled=options["enabled"],
            backup_first=options["backup_first"],
            only_if_new=options["only_if_new"],
            by=options.get("by"),
        )


@pytest.fixture
def delivered(shop: App, store: NoustStore) -> App:
    """The shop, delivered with care: its own history, stop, schedule and preview options."""
    app_rollout_store.set_history_keep(DOMAIN, 40)
    app_rollout_store.set_stop_policy(DOMAIN, timeout=45, signal="SIGINT")
    app_rollout_store.set_pause_on_rollback(DOMAIN, False)
    update_schedule_store.save(
        DOMAIN, "Sun *-*-* 03:00:00", enabled=True, backup_first=False, only_if_new=True, by="ana"
    )
    PreviewStore().save_options(
        PreviewOptions(
            app_domain=DOMAIN,
            inherit_env="allowlist",
            allow_env=("PUBLIC_URL",),
            data_mode="clone",
            domain_template="{{pr_id}}.{{base}}",
            noindex=False,
            fork_policy="approve",
        )
    )
    return shop


class TestExport:
    def test_the_document_carries_how_the_app_is_delivered(
        self, delivered: App, cron: FakeCron
    ) -> None:
        document = export_app(DOMAIN, cron=cron)  # type: ignore[arg-type]

        assert document["delivery"] == {
            "history_keep": 40,
            "stop_timeout": 45,
            "stop_signal": "SIGINT",
            "pause_on_rollback": False,
            "update_schedule": {
                "calendar": "Sun *-*-* 03:00:00",
                "enabled": True,
                "backup_first": False,
                "only_if_new": True,
            },
        }

    def test_the_previews_carry_what_they_are_given_and_how_they_are_named(
        self, delivered: App, cron: FakeCron
    ) -> None:
        previews = export_app(DOMAIN, cron=cron)["previews"]  # type: ignore[arg-type]

        assert previews["inherit_env"] == "allowlist"
        assert previews["allow_env"] == ["PUBLIC_URL"]
        assert previews["data_mode"] == "clone"
        assert previews["domain_template"] == "{{pr_id}}.{{base}}"
        assert previews["noindex"] is False
        assert previews["fork_policy"] == "approve"

    def test_a_pause_is_a_state_and_is_not_exported(self, delivered: App, cron: FakeCron) -> None:
        app_rollout_store.pause(DOMAIN, reason="looking", by="ana")

        document = export_app(DOMAIN, cron=cron)  # type: ignore[arg-type]

        assert "paused" not in str(document["delivery"])
        assert "looking" not in str(document)

    def test_an_app_with_nothing_chosen_exports_the_defaults(
        self, shop: App, cron: FakeCron
    ) -> None:
        delivery = export_app(DOMAIN, cron=cron)["delivery"]  # type: ignore[arg-type]

        assert delivery == {
            "history_keep": None,
            "stop_timeout": None,
            "stop_signal": None,
            "pause_on_rollback": True,
            "update_schedule": None,
        }


class TestDocument:
    def test_an_older_document_without_the_section_reads_as_the_defaults(
        self, shop: App, cron: FakeCron
    ) -> None:
        document = export_app(DOMAIN, cron=cron)  # type: ignore[arg-type]
        document.pop("delivery")

        checked = validate_document(document)

        # Nothing is invented for an older document: the import reads the absence as the defaults.
        assert checked["delivery"] == {}

    @pytest.mark.parametrize(
        ("delivery", "field"),
        [
            ({"history_keep": "many"}, "delivery.history_keep"),
            ({"stop_timeout": True}, "delivery.stop_timeout"),
            ({"stop_signal": 9}, "delivery.stop_signal"),
            ({"pause_on_rollback": "no"}, "delivery.pause_on_rollback"),
            ({"update_schedule": {"enabled": True}}, "delivery.update_schedule.calendar"),
            (
                {"update_schedule": {"calendar": "daily", "backup_first": "yes"}},
                "delivery.update_schedule.backup_first",
            ),
        ],
    )
    def test_a_field_of_the_wrong_type_is_named(
        self, shop: App, cron: FakeCron, delivery: dict[str, Any], field: str
    ) -> None:
        document = export_app(DOMAIN, cron=cron)  # type: ignore[arg-type]
        document["delivery"] = delivery

        with pytest.raises(ValidationError) as caught:
            validate_document(document)

        assert caught.value.field == field

    def test_preview_options_of_the_wrong_type_are_named(self, shop: App, cron: FakeCron) -> None:
        document = export_app(DOMAIN, cron=cron)  # type: ignore[arg-type]
        document["previews"]["noindex"] = "yes"

        with pytest.raises(ValidationError) as caught:
            validate_document(document)

        assert caught.value.field == "previews.noindex"


class TestPlan:
    def test_the_plan_says_what_it_will_set_and_asks_about_a_schedule(
        self, delivered: App, cron: FakeCron
    ) -> None:
        document = export_app(DOMAIN, with_secrets=True, cron=cron)  # type: ignore[arg-type]

        plan = plan_import(document, domain=NEW, source=SOURCE)

        assert "keep 40 deployments of history" in plan.steps
        assert "stop with SIGINT, killed after 45 s" in plan.steps
        assert "keep deploying by itself after a rollback" in plan.steps
        assert "update itself at Sun *-*-* 03:00:00, without a backup" in plan.steps
        assert any(
            "schedules updates that deploy this application by itself" in line
            for line in plan.confirm
        )

    def test_the_previews_line_says_the_template_the_copy_and_the_forks(
        self, delivered: App, cron: FakeCron
    ) -> None:
        plan = plan_import(
            export_app(DOMAIN, with_secrets=True, cron=cron), domain=NEW, source=SOURCE
        )  # type: ignore[arg-type]

        line = next(step for step in plan.steps if step.startswith("previews under"))
        assert "only PUBLIC_URL copied" in line
        assert "named {{pr_id}}.{{base}}" in line
        assert "each with a copy of the database" in line
        assert "forks wait for approval" in line
        assert any(
            "only the variables the document names" in text and "forks" in text
            for text in plan.confirm
        )

    def test_an_app_with_nothing_chosen_adds_no_step(self, shop: App, cron: FakeCron) -> None:
        plan = plan_import(
            export_app(DOMAIN, with_secrets=True, cron=cron), domain=NEW, source=SOURCE
        )  # type: ignore[arg-type]

        assert not [
            step
            for step in plan.steps
            if step.startswith(("keep ", "stop with", "update itself")) and "releases" not in step
        ]


class TestImport:
    def test_the_import_puts_the_delivery_back(
        self,
        delivered: App,
        cron: FakeCron,
        engine: list[str],
        store: NoustStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def stop(domain: str, *, timeout: Any, signal: Any, **_: Any) -> None:
            engine.append(f"stop {timeout} {signal}")
            app_rollout_store.set_stop_policy(domain, timeout=timeout, signal=signal)

        monkeypatch.setattr(app_export, "set_stop_policy", stop)
        monkeypatch.setattr(app_export, "UpdateScheduler", lambda: FakeScheduler(engine))
        document = export_app(DOMAIN, with_secrets=True, cron=cron)  # type: ignore[arg-type]
        plan = plan_import(document, domain=NEW, source=SOURCE)

        report = apply_import(plan, deploy=fake_deploy(store, tmp_path, engine), cron=cron)  # type: ignore[arg-type]

        assert not [
            step.part
            for step in report.not_applied
            if "stop" in step.part or "schedule" in step.part
        ]
        copy = app_rollout_store.get(NEW)
        assert (copy.history_keep, copy.stop_timeout, copy.stop_signal) == (40, 45, "SIGINT")
        assert copy.pause_on_rollback is False
        scheduled = update_schedule_store.get(NEW)
        assert scheduled is not None
        assert (scheduled.calendar, scheduled.backup_first, scheduled.only_if_new) == (
            "Sun *-*-* 03:00:00",
            False,
            True,
        )
        options = PreviewStore().options(NEW)
        # A copy of the database needs a database, and an import makes none: said, not guessed.
        assert (options.inherit_env, options.allow_env, options.data_mode) == (
            "allowlist",
            ("PUBLIC_URL",),
            "shared",
        )
        held = {step.part: step.detail for step in report.not_applied}[
            "previews under previews.example.com"
        ]
        assert held is not None and "copy of the database is not set" in held
        assert (options.domain_template, options.noindex, options.fork_policy) == (
            "{{pr_id}}.{{base}}",
            False,
            "approve",
        )
        assert engine[-1] == "zero-downtime", "zero-downtime still goes last"

    def test_a_stop_policy_the_app_cannot_take_is_reported_and_the_rest_goes_on(
        self,
        delivered: App,
        cron: FakeCron,
        engine: list[str],
        store: NoustStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def refuse(*_: Any, **__: Any) -> None:
            raise DeploymentError(
                "Its processes are Docker's",
                details="Set the stop grace period in the compose file.",
            )

        monkeypatch.setattr(app_export, "set_stop_policy", refuse)
        monkeypatch.setattr(app_export, "UpdateScheduler", lambda: FakeScheduler(engine))
        document = export_app(DOMAIN, with_secrets=True, cron=cron)  # type: ignore[arg-type]
        plan = plan_import(document, domain=NEW, source=SOURCE)

        report = apply_import(plan, deploy=fake_deploy(store, tmp_path, engine), cron=cron)  # type: ignore[arg-type]

        details = {step.part: step.detail for step in report.not_applied}
        assert "Its processes are Docker's" in details["stop policy"]
        assert app_rollout_store.get(NEW).history_keep == 40, "the part after it was still applied"
        assert "schedule Sun *-*-* 03:00:00" in engine


class TestClone:
    def test_a_clone_is_not_given_the_schedule_of_the_original(
        self, clone_app: App, store: NoustStore
    ) -> None:
        update_schedule_store.save(
            "shop.example.com", "daily", enabled=True, backup_first=True, only_if_new=True, by="ana"
        )
        app_rollout_store.set_history_keep("shop.example.com", 12)

        plan = plan_clone("shop.example.com", "shop-copy.example.com")

        delivery = plan.plan.document["delivery"]
        assert delivery["update_schedule"] is None
        assert delivery["history_keep"] == 12, "the rest of how it is delivered is copied"
        assert any("scheduled update" in line for line in plan.omitted)
        assert not any("update itself" in step for step in plan.plan.steps)

    def test_a_clone_of_an_app_that_does_not_update_itself_omits_nothing_about_it(
        self, clone_app: App
    ) -> None:
        plan = plan_clone("shop.example.com", "shop-copy.example.com")

        assert not any("scheduled update" in line for line in plan.omitted)
