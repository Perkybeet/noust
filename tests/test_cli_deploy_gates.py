# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust deploy pause|resume|freeze|unfreeze|freezes|keep|held`` and ``noust app stop-grace``
(checklist B-08, B-17, B-21, B-26).

The stores and the guard are ``test_deploy_gates.py``'s; here are the commands: what they
print, what they refuse, that ``--json`` is the same facts, and that ``--dry-run`` changes
nothing.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from noust.cli.app import cli as root_cli
from noust.core import app_rollout_store, deploy_freeze
from noust.core.store import App, AppLayout, NoustStore

DOMAIN = "shop.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "store" / "noust.db")
    instance.create_app(
        App(
            domain=DOMAIN,
            app_type="nodejs",
            app_path=f"/var/www/apps/{DOMAIN}",
            port=3000,
            layout=AppLayout.RELEASES.value,
        )
    )
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


def invoke(*args: str) -> Result:
    return CliRunner().invoke(root_cli, list(args))


def said(result: Result) -> str:
    return result.output + (str(result.exception) if result.exception else "")


class TestPause:
    def test_pausing_and_resuming(self, store: NoustStore) -> None:
        paused = invoke("deploy", "pause", DOMAIN, "--reason", "looking at 20261001")

        assert paused.exit_code == 0, said(paused)
        assert "paused" in paused.output
        settings = app_rollout_store.get(DOMAIN)
        assert settings.paused and settings.paused_reason == "looking at 20261001"

        resumed = invoke("deploy", "resume", DOMAIN)

        assert resumed.exit_code == 0, said(resumed)
        assert "runs again" in resumed.output
        assert not app_rollout_store.get(DOMAIN).paused

    def test_resuming_what_was_not_paused_says_so(self, store: NoustStore) -> None:
        result = invoke("deploy", "resume", DOMAIN)

        assert result.exit_code == 0 and "was not paused" in result.output

    def test_an_unknown_application_is_refused(self, store: NoustStore) -> None:
        result = invoke("deploy", "pause", "gone.example.com")

        assert result.exit_code != 0
        assert "not found" in said(result)

    def test_a_dry_run_pauses_nothing(self, store: NoustStore) -> None:
        result = invoke("--dry-run", "deploy", "pause", DOMAIN)

        assert result.exit_code == 0
        assert not app_rollout_store.get(DOMAIN).paused

    def test_json_is_the_same_facts(self, store: NoustStore) -> None:
        result = invoke("deploy", "pause", DOMAIN, "--json")

        body = json.loads(result.output)
        assert body["domain"] == DOMAIN and body["paused"]["by"]

    def test_pause_on_rollback_is_shown_and_set(self, store: NoustStore) -> None:
        shown = invoke("deploy", "pause-on-rollback", DOMAIN, "--json")
        assert json.loads(shown.output)["pause_on_rollback"] is True

        invoke("deploy", "pause-on-rollback", DOMAIN, "off")

        assert app_rollout_store.get(DOMAIN).pause_on_rollback is False


class TestFreeze:
    def test_a_freeze_needs_a_reason(self, store: NoustStore) -> None:
        result = invoke("deploy", "freeze")

        assert result.exit_code != 0
        assert deploy_freeze.list_freezes() == []

    def test_freezing_the_server_and_lifting_it(self, store: NoustStore) -> None:
        frozen = invoke("deploy", "freeze", "--reason", "incident 412", "--hours", "2")

        assert frozen.exit_code == 0, said(frozen)
        assert "frozen" in frozen.output
        assert deploy_freeze.active_for(DOMAIN) is not None

        listed = invoke("deploy", "freezes")
        assert "incident 412" in listed.output and "the whole server" in listed.output

        lifted = invoke("deploy", "unfreeze")
        assert lifted.exit_code == 0 and "Lifted freeze" in lifted.output
        assert deploy_freeze.active_for(DOMAIN) is None
        assert "Nothing is frozen" in invoke("deploy", "freezes").output

    def test_one_application_alone(self, store: NoustStore) -> None:
        invoke("deploy", "freeze", DOMAIN, "--reason", "sale")

        freeze = deploy_freeze.active_for(DOMAIN)
        assert freeze is not None and freeze.domain == DOMAIN
        assert (
            "Lift it with: noust deploy unfreeze " + DOMAIN
            in invoke("deploy", "freeze", DOMAIN, "--reason", "again").output
        )

    def test_a_window_in_the_future_is_announced_not_started(self, store: NoustStore) -> None:
        result = invoke(
            "deploy",
            "freeze",
            "--reason",
            "holiday",
            "--from",
            "2099-12-24T18:00",
            "--until",
            "2099-12-26T08:00",
        )

        assert result.exit_code == 0
        assert "will freeze" in result.output
        assert deploy_freeze.active_for(DOMAIN) is None
        assert len(deploy_freeze.list_freezes()) == 1

    def test_a_bad_time_is_refused_naming_the_field(self, store: NoustStore) -> None:
        result = invoke("deploy", "freeze", "--reason", "x", "--from", "friday")

        assert result.exit_code != 0
        assert "Not a date and time" in said(result)

    def test_unfreezing_what_is_not_frozen_says_so(self, store: NoustStore) -> None:
        result = invoke("deploy", "unfreeze", DOMAIN)

        assert result.exit_code == 0 and "were not frozen" in result.output

    def test_the_server_s_freeze_is_not_lifted_by_the_application_s(
        self, store: NoustStore
    ) -> None:
        invoke("deploy", "freeze", "--reason", "server")

        invoke("deploy", "unfreeze", DOMAIN)

        assert deploy_freeze.active_for(DOMAIN) is not None

    def test_a_dry_run_freezes_nothing(self, store: NoustStore) -> None:
        invoke("--dry-run", "deploy", "freeze", "--reason", "x")

        assert deploy_freeze.list_freezes() == []

    def test_held_says_what_holds_it(self, store: NoustStore) -> None:
        invoke("deploy", "freeze", DOMAIN, "--reason", "sale")
        invoke("deploy", "pause", DOMAIN)

        body = json.loads(invoke("deploy", "held", DOMAIN, "--json").output)

        assert "sale" in body["manual"] and "sale" in body["automatic"]


class TestKeep:
    def test_it_shows_the_servers_number_until_the_application_has_its_own(
        self, store: NoustStore
    ) -> None:
        shown = json.loads(invoke("deploy", "keep", DOMAIN, "--json").output)
        assert shown["history_keep"] is None and shown["history_keep_effective"] == 20

        invoke("deploy", "keep", DOMAIN, "50")

        shown = json.loads(invoke("deploy", "keep", DOMAIN, "--json").output)
        assert shown["history_keep"] == 50 and shown["history_keep_effective"] == 50

        invoke("deploy", "keep", DOMAIN, "default")
        assert app_rollout_store.get(DOMAIN).history_keep is None

    def test_a_number_out_of_range_or_not_a_number_is_refused(self, store: NoustStore) -> None:
        for bad in ("0", "5000", "many"):
            result = invoke("deploy", "keep", DOMAIN, bad)
            assert result.exit_code != 0, bad
        assert app_rollout_store.get(DOMAIN).history_keep is None


class TestStopGrace:
    def test_it_shows_systemd_s_own_until_set(self, store: NoustStore) -> None:
        result = invoke("app", "stop-grace", DOMAIN)

        assert result.exit_code == 0, said(result)
        assert "90 seconds" in result.output and "SIGTERM" in result.output

    def test_setting_it_goes_through_the_one_operation(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.cli.commands import app_stop_grace
        from noust.deployers.lifecycle import StopPolicyChange

        calls: list[dict] = []

        def fake(domain: str, *, timeout: int | None, signal: str | None, logger: object) -> object:
            calls.append({"domain": domain, "timeout": timeout, "signal": signal})
            app_rollout_store.set_stop_policy(domain, timeout=timeout, signal=signal)
            return StopPolicyChange(domain, timeout, signal, (domain,))

        monkeypatch.setattr(app_stop_grace, "set_stop_policy", fake)

        result = invoke("app", "stop-grace", DOMAIN, "--seconds", "45", "--signal", "sigint")

        assert result.exit_code == 0, said(result)
        assert calls == [{"domain": DOMAIN, "timeout": 45, "signal": "SIGINT"}]
        # Setting only one keeps the other.
        invoke("app", "stop-grace", DOMAIN, "--seconds", "60")
        assert calls[-1] == {"domain": DOMAIN, "timeout": 60, "signal": "SIGINT"}
        invoke("app", "stop-grace", DOMAIN, "--reset")
        assert calls[-1] == {"domain": DOMAIN, "timeout": None, "signal": None}

    def test_reset_with_values_is_a_usage_error(self, store: NoustStore) -> None:
        result = invoke("app", "stop-grace", DOMAIN, "--reset", "--seconds", "30")

        assert result.exit_code != 0
