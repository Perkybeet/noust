# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What holds a deploy, and the settings of an application's delivery
(checklist B-08, B-17, B-26).

An application keeps its own number of deployments in its history, going back pauses
what deploys it by itself, and an operator freezes deploys for a stretch: these are
the stores and the one guard that decides, with the clock moved by the tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from noust.core import app_rollout_store, delivery, deploy_freeze, deploy_guard
from noust.core.exceptions import (
    DeploysFrozenError,
    DeploysPausedError,
    NoustError,
    ValidationError,
)
from noust.core.store import App, NoustStore

DOMAIN = "shop.example.com"
OTHER = "blog.example.com"
PREVIEW = "pr-3-shop-example-com.previews.example.com"
NOW = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    for domain, parent in ((DOMAIN, None), (OTHER, None), (PREVIEW, DOMAIN)):
        instance.create_app(
            App(
                domain=domain,
                app_type="nodejs",
                port=3000,
                app_path=str(tmp_path / domain),
                preview_parent=parent,
            )
        )
    delivery.forget()
    yield instance
    delivery.forget()
    instance.close()
    NoustStore.reset_instance()


class TestHowManyDeploymentsAreKept:
    def test_an_application_follows_the_server_until_it_says_otherwise(
        self, store: NoustStore
    ) -> None:
        assert app_rollout_store.history_keep_for(DOMAIN) == 20
        assert app_rollout_store.get(DOMAIN).history_keep is None

        app_rollout_store.set_history_keep(DOMAIN, 50)

        assert app_rollout_store.history_keep_for(DOMAIN) == 50
        assert app_rollout_store.history_keep_for(OTHER) == 20

    def test_the_server_s_number_comes_from_its_configuration(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            delivery,
            "current",
            lambda **_: delivery.DeliverySettings(history_keep=7),
        )

        assert app_rollout_store.history_keep_for(DOMAIN) == 7

    def test_none_goes_back_to_the_server_s_number(self, store: NoustStore) -> None:
        app_rollout_store.set_history_keep(DOMAIN, 50)

        app_rollout_store.set_history_keep(DOMAIN, None)

        assert app_rollout_store.history_keep_for(DOMAIN) == 20

    @pytest.mark.parametrize("bad", [0, -1, 1001, True, 2.5, "20"])
    def test_a_number_out_of_range_is_refused_naming_the_field(
        self, store: NoustStore, bad: object
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            app_rollout_store.set_history_keep(DOMAIN, bad)

        assert caught.value.field == "history_keep"
        assert app_rollout_store.get(DOMAIN).history_keep is None

    def test_an_unknown_application_is_refused(self, store: NoustStore) -> None:
        with pytest.raises(NoustError):
            app_rollout_store.set_history_keep("gone.example.com", 5)

    def test_the_recorder_prunes_to_the_applications_own_number(self, store: NoustStore) -> None:
        from noust.core.logger import Logger
        from noust.deployers.recorder import DeploymentRecorder

        app_rollout_store.set_history_keep(DOMAIN, 3)

        recorder = DeploymentRecorder(store, DOMAIN, "cli", logger=Logger())

        assert recorder._keep == 3
        assert DeploymentRecorder(store, OTHER, "cli", logger=Logger())._keep == 20
        assert DeploymentRecorder(store, DOMAIN, "cli", logger=Logger(), keep=9)._keep == 9


class TestHowAUnitIsAskedToStop:
    def test_a_policy_is_stored_and_read_back(self, store: NoustStore) -> None:
        settings = app_rollout_store.set_stop_policy(DOMAIN, timeout=45, signal="sigint")

        assert (settings.stop_timeout, settings.stop_signal) == (45, "SIGINT")

    def test_none_goes_back_to_systemd_s(self, store: NoustStore) -> None:
        app_rollout_store.set_stop_policy(DOMAIN, timeout=45, signal="SIGINT")

        settings = app_rollout_store.set_stop_policy(DOMAIN, timeout=None, signal=None)

        assert (settings.stop_timeout, settings.stop_signal) == (None, None)

    @pytest.mark.parametrize("bad", [0, 3601, -5, True, "ten"])
    def test_a_timeout_out_of_range_is_refused(self, store: NoustStore, bad: object) -> None:
        with pytest.raises(ValidationError) as caught:
            app_rollout_store.set_stop_policy(DOMAIN, timeout=bad, signal=None)

        assert caught.value.field == "stop_timeout"

    @pytest.mark.parametrize("bad", ["SIGKILL", "SIGUSR1", "KILL", "9", "", "TERM; reboot"])
    def test_a_signal_that_does_not_end_a_process_is_refused(
        self, store: NoustStore, bad: str
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            app_rollout_store.set_stop_policy(DOMAIN, timeout=None, signal=bad)

        assert caught.value.field == "stop_signal"

    def test_a_signal_may_be_written_without_its_prefix(self, store: NoustStore) -> None:
        assert app_rollout_store.check_stop_signal("term") == "SIGTERM"
        assert app_rollout_store.check_stop_signal(" quit ") == "SIGQUIT"


class TestThePause:
    def test_pausing_says_since_when_by_whom_and_why(self, store: NoustStore) -> None:
        settings = app_rollout_store.pause(DOMAIN, reason="rolled back to 20261001", by="ana")

        assert settings.paused
        assert (settings.paused_by, settings.paused_reason) == ("ana", "rolled back to 20261001")
        assert app_rollout_store.get(OTHER).paused is False

    def test_pausing_again_keeps_when_it_was_paused(self, store: NoustStore) -> None:
        first = app_rollout_store.pause(DOMAIN, reason="one", by="ana")

        second = app_rollout_store.pause(DOMAIN, reason="two", by="bob")

        assert second.paused_at == first.paused_at
        assert (second.paused_by, second.paused_reason) == ("bob", "two")

    def test_resuming_says_whether_it_was_paused(self, store: NoustStore) -> None:
        app_rollout_store.pause(DOMAIN, by="ana")

        assert app_rollout_store.resume(DOMAIN, by="ana") is True
        assert app_rollout_store.resume(DOMAIN, by="ana") is False
        assert app_rollout_store.get(DOMAIN).paused is False

    def test_resuming_an_unknown_application_is_refused(self, store: NoustStore) -> None:
        with pytest.raises(NoustError):
            app_rollout_store.resume("gone.example.com")

    def test_pausing_after_a_rollback_is_on_unless_switched_off(self, store: NoustStore) -> None:
        assert app_rollout_store.get(DOMAIN).pause_on_rollback is True

        app_rollout_store.set_pause_on_rollback(DOMAIN, False)

        assert app_rollout_store.get(DOMAIN).pause_on_rollback is False
        with pytest.raises(ValidationError):
            app_rollout_store.set_pause_on_rollback(DOMAIN, "no")  # type: ignore[arg-type]

    def test_a_reason_too_long_is_refused(self, store: NoustStore) -> None:
        with pytest.raises(ValidationError):
            app_rollout_store.pause(DOMAIN, reason="x" * 301)

    def test_the_settings_go_with_the_application(self, store: NoustStore) -> None:
        app_rollout_store.set_history_keep(OTHER, 5)

        store.delete_app(OTHER)

        with store._transaction() as cursor:
            cursor.execute("SELECT COUNT(*) FROM app_rollout")
            assert cursor.fetchone()[0] == 0


class TestFreezing:
    def test_a_freeze_names_its_reason_and_holds_from_now(self, store: NoustStore) -> None:
        freeze = deploy_freeze.create("incident 412", by="ana")

        assert freeze.domain is None
        assert freeze.state() == "active"
        assert deploy_freeze.active_for(DOMAIN) is not None
        assert deploy_freeze.active_for(OTHER) is not None

    def test_one_application_can_be_frozen_alone(self, store: NoustStore) -> None:
        deploy_freeze.create("sale", domain=DOMAIN, by="ana")

        assert deploy_freeze.active_for(DOMAIN) is not None
        assert deploy_freeze.active_for(OTHER) is None

    def test_a_reason_is_required(self, store: NoustStore) -> None:
        for reason in ("", "   "):
            with pytest.raises(ValidationError) as caught:
                deploy_freeze.create(reason)
            assert caught.value.field == "reason"

    def test_an_unknown_application_is_refused(self, store: NoustStore) -> None:
        with pytest.raises(NoustError):
            deploy_freeze.create("why", domain="gone.example.com")

    def test_a_window_starts_later_and_ends(self, store: NoustStore) -> None:
        start = NOW + timedelta(hours=2)
        deploy_freeze.create("holiday", starts_at=start, until_hours=10)

        assert deploy_freeze.active_for(DOMAIN, now=NOW) is None
        assert deploy_freeze.active_for(DOMAIN, now=NOW + timedelta(hours=3)) is not None
        assert deploy_freeze.active_for(DOMAIN, now=NOW + timedelta(hours=13)) is None

    def test_what_has_not_started_is_listed_as_scheduled(self, store: NoustStore) -> None:
        freeze = deploy_freeze.create("holiday", starts_at=NOW + timedelta(days=1), by="ana")

        assert freeze.state(NOW) == "scheduled"
        assert [f.id for f in deploy_freeze.list_freezes(now=NOW)] == [freeze.id]

    def test_the_end_must_follow_the_start_and_be_within_ninety_days(
        self, store: NoustStore
    ) -> None:
        with pytest.raises(ValidationError):
            deploy_freeze.create("x", starts_at=NOW, ends_at=NOW - timedelta(hours=1))
        with pytest.raises(ValidationError):
            deploy_freeze.create("x", starts_at=NOW, ends_at=NOW + timedelta(days=91))
        with pytest.raises(ValidationError):
            deploy_freeze.create("x", ends_at=NOW, until_hours=3)
        with pytest.raises(ValidationError):
            deploy_freeze.create("x", until_hours=0)

    def test_a_moment_that_is_not_one_is_refused(self, store: NoustStore) -> None:
        with pytest.raises(ValidationError) as caught:
            deploy_freeze.create("x", starts_at="next friday")
        assert caught.value.field == "starts_at"

    def test_lifting_ends_it_and_keeps_the_row(self, store: NoustStore) -> None:
        freeze = deploy_freeze.create("incident", domain=DOMAIN, by="ana")

        lifted = deploy_freeze.lift(DOMAIN, by="bob")

        assert [f.id for f in lifted] == [freeze.id]
        assert deploy_freeze.active_for(DOMAIN) is None
        kept = deploy_freeze.get_freeze(freeze.id)
        assert kept is not None and kept.state() == "lifted" and kept.lifted_by == "bob"
        assert deploy_freeze.list_freezes() == []
        assert len(deploy_freeze.list_freezes(include_past=True)) == 1

    def test_lifting_an_applications_freeze_leaves_the_servers(self, store: NoustStore) -> None:
        deploy_freeze.create("server wide")
        deploy_freeze.create("just this", domain=DOMAIN)

        deploy_freeze.lift(DOMAIN)

        active = deploy_freeze.active_for(DOMAIN)
        assert active is not None and active.domain is None

    def test_lifting_nothing_lifts_nothing(self, store: NoustStore) -> None:
        assert deploy_freeze.lift(DOMAIN) == []

    def test_a_freeze_goes_with_its_application(self, store: NoustStore) -> None:
        deploy_freeze.create("x", domain=OTHER)

        store.delete_app(OTHER)

        assert deploy_freeze.list_freezes() == []


class TestWhatTheGuardHolds:
    def test_nothing_is_held_by_default(self, store: NoustStore) -> None:
        assert deploy_guard.gate_for(DOMAIN, automatic=True) is None
        deploy_guard.require_open(DOMAIN, automatic=True)
        deploy_guard.require_open(DOMAIN)

    def test_a_freeze_holds_a_deploy_started_by_a_person_until_they_say_so(
        self, store: NoustStore
    ) -> None:
        deploy_freeze.create("incident 412", by="ana")

        with pytest.raises(DeploysFrozenError) as caught:
            deploy_guard.require_open(DOMAIN)

        error = caught.value
        assert "incident 412" in error.message and "this server" in error.message
        assert error.field == "frozen_ok" and error.automatic is False
        assert "noust deploy unfreeze" in error.details
        assert error.freeze["scope"] == "server" and error.freeze["reason"] == "incident 412"

    def test_a_person_can_deploy_during_a_freeze_and_it_is_recorded(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            deploy_guard.audit, "record", lambda event, **kw: recorded.append((event, kw))
        )
        deploy_freeze.create("incident 412")

        deploy_guard.require_open(DOMAIN, override=True, actor="ana")

        assert recorded[-1][0] == "deploys.frozen_override"
        assert recorded[-1][1]["details"]["by"] == "ana"

    def test_nobody_overrides_a_freeze_for_something_that_starts_by_itself(
        self, store: NoustStore
    ) -> None:
        deploy_freeze.create("incident 412")

        with pytest.raises(DeploysFrozenError) as caught:
            deploy_guard.require_open(DOMAIN, automatic=True, override=True)

        assert caught.value.automatic is True and caught.value.field is None

    def test_a_freeze_of_one_application_holds_only_it(self, store: NoustStore) -> None:
        deploy_freeze.create("sale", domain=DOMAIN)

        with pytest.raises(DeploysFrozenError):
            deploy_guard.require_open(DOMAIN)
        deploy_guard.require_open(OTHER)

    def test_a_pause_holds_only_what_starts_by_itself(self, store: NoustStore) -> None:
        app_rollout_store.pause(DOMAIN, reason="rolled back", by="rollback")

        deploy_guard.require_open(DOMAIN)
        with pytest.raises(DeploysPausedError) as caught:
            deploy_guard.require_open(DOMAIN, automatic=True)

        assert "rolled back" in caught.value.message
        assert caught.value.paused_by == "rollback"
        assert f"noust deploy resume {DOMAIN}" in caught.value.details

    def test_a_pause_does_not_reach_another_application(self, store: NoustStore) -> None:
        app_rollout_store.pause(DOMAIN)

        deploy_guard.require_open(OTHER, automatic=True)

    def test_a_preview_is_never_held_it_is_not_production(self, store: NoustStore) -> None:
        deploy_freeze.create("incident")
        app_rollout_store.pause(PREVIEW)

        assert deploy_guard.gate_for(PREVIEW, automatic=True) is None
        deploy_guard.require_open(PREVIEW, automatic=True)

    def test_a_freeze_wins_over_a_pause_in_what_it_tells(self, store: NoustStore) -> None:
        app_rollout_store.pause(DOMAIN)
        deploy_freeze.create("incident")

        gate = deploy_guard.gate_for(DOMAIN, automatic=True)

        assert gate is not None and gate.kind == "frozen"

    def test_an_ended_freeze_holds_nothing(self, store: NoustStore) -> None:
        deploy_freeze.create(
            "past", starts_at=NOW - timedelta(hours=5), ends_at=NOW - timedelta(hours=1)
        )

        assert deploy_guard.gate_for(DOMAIN, automatic=False, now=NOW) is None
