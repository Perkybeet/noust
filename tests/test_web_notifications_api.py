# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for ``/api/notifications`` and ``/api/alerts``.

Checklist K-25, K-26, K-27 (channels with their own events, routes and
instances), K-30 (the record of deliveries and sending one again) and G-18 (alert
rules). What is asserted is the contract: credentials never come back, a refusal
names the channel and the setting, a change needs sudo mode, and nothing is
written when it is refused.

This file is also read by the ``pydantic-v1`` CI job: the request models go
through the same compatibility layer as every other router.
"""

from __future__ import annotations

import io
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.request import Request

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.alerts import AlertRule, AlertRules, AlertState
from noust.core.config import Config
from noust.core.notification_ledger import DeliveryLedger
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import Notification, State
from noust.core.notifier import Notifier
from noust.core.store import NoustStore
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager
from tests.config_files import stored_config

SLACK = "https://hooks.slack.test/services/T0/B0/prod-secret"
STAGING = "https://hooks.slack.test/services/T0/B1/staging-secret"


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "etc" / "noust" / "config.yaml"
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", path)
    Config.reset_instance()
    try:
        yield path
    finally:
        Config.reset_instance()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def app(tmp_path: Path, store: NoustStore, config_path: Path) -> FastAPI:
    return create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    signed_in = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    token = get_token_manager().generate_master_token()
    response = signed_in.post("/api/auth/login", json={"token": token})
    assert response.status_code == 200, response.text
    signed_in.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return signed_in


@pytest.fixture
def anonymous(app: FastAPI) -> TestClient:
    return TestClient(app, client=("testclient", 50000), follow_redirects=False)


def elevate(client: TestClient) -> None:
    response = client.post(
        "/api/auth/elevate", json={"token": get_token_manager().generate_master_token()}
    )
    assert response.status_code == 200, response.text


def by_name(response_json: dict, name: str) -> dict:
    return next(item for item in response_json["items"] if item["name"] == name)


class TestChannels:
    def test_a_fresh_server_lists_the_default_channel_of_each_type(
        self, client: TestClient
    ) -> None:
        response = client.get("/api/notifications/channels")

        assert response.status_code == 200
        body = response.json()
        assert [item["name"] for item in body["items"]][:5] == [
            "webhook",
            "slack",
            "discord",
            "telegram",
            "email",
        ]
        assert {t["name"] for t in body["types"]} >= {"pushover", "ntfy", "gotify", "teams"}
        assert body["enabled"] is False
        pushover = next(t for t in body["types"] if t["name"] == "pushover")
        assert {k["key"]: (k["secret"], k["required"]) for k in pushover["keys"]} == {
            "user_key": (True, True),
            "app_token": (True, True),
            "device": (False, False),
        }

    def test_changing_a_channel_needs_sudo_mode(self, client: TestClient) -> None:
        response = client.put(
            "/api/notifications/channels/slack", json={"settings": {"webhook_url": SLACK}}
        )

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"

    def test_a_channel_is_saved_and_its_credential_never_comes_back(
        self, client: TestClient, config_path: Path
    ) -> None:
        elevate(client)

        saved = client.put(
            "/api/notifications/channels/slack", json={"settings": {"webhook_url": SLACK}}
        )

        assert saved.status_code == 200, saved.text
        assert saved.json()["settings"] == {"webhook_url": "***"}
        assert saved.json()["configured"] is True
        listed = client.get("/api/notifications/channels")
        assert SLACK not in listed.text
        assert by_name(listed.json(), "slack")["settings"] == {"webhook_url": "***"}
        # Kept, in the 0600 file, encrypted: only a server that holds the key reads it back.
        assert SLACK not in config_path.read_text()
        assert SLACK in str(stored_config(config_path))

    def test_a_second_channel_of_the_same_type(self, client: TestClient) -> None:
        elevate(client)
        client.put("/api/notifications/channels/slack", json={"settings": {"webhook_url": SLACK}})

        response = client.put(
            "/api/notifications/channels/slack-staging",
            json={
                "type": "slack",
                "settings": {"webhook_url": STAGING},
                "events": {"deploy_started": True},
                "route": {"environments": ["staging"], "min_severity": "warning"},
                "quiet": {"start": "22:00", "end": "07:00", "timezone": "Europe/Madrid"},
            },
        )

        assert response.status_code == 200, response.text
        staging = response.json()
        assert (staging["type"], staging["default"]) == ("slack", False)
        assert staging["events"] == {"deploy_started": True}
        assert staging["route"] == {"environments": ["staging"], "min_severity": "warning"}
        assert staging["quiet"]["timezone"] == "Europe/Madrid"
        names = [i["name"] for i in client.get("/api/notifications/channels").json()["items"]]
        assert "slack" in names and names[-1] == "slack-staging"

    def test_a_cap_and_a_summary_are_saved_shown_and_cleared(self, client: TestClient) -> None:
        elevate(client)

        saved = client.put(
            "/api/notifications/channels/slack-ops",
            json={
                "type": "slack",
                "settings": {"webhook_url": STAGING},
                "flood": {"max": 20, "minutes": 10},
                "summary": {"at": "08:00", "timezone": "Europe/Madrid"},
            },
        )

        assert saved.status_code == 200, saved.text
        assert saved.json()["flood"] == {"max": 20, "minutes": 10}
        assert saved.json()["summary"]["below"] == "warning"
        listed = {i["name"]: i for i in client.get("/api/notifications/channels").json()["items"]}
        assert listed["slack-ops"]["flood"] == {"max": 20, "minutes": 10}
        assert listed["slack"]["flood"] is None and listed["slack"]["summary"] is None

        cleared = client.put(
            "/api/notifications/channels/slack-ops", json={"flood": {}, "summary": {}}
        )
        assert cleared.json()["flood"] is None and cleared.json()["summary"] is None

    def test_saving_with_the_star_keeps_the_stored_credential(
        self, client: TestClient, config_path: Path
    ) -> None:
        elevate(client)
        client.put("/api/notifications/channels/slack", json={"settings": {"webhook_url": SLACK}})

        client.put(
            "/api/notifications/channels/slack",
            json={"settings": {"webhook_url": "***"}, "enabled": True},
        )

        assert SLACK in str(stored_config(config_path))

    @pytest.mark.parametrize(
        ("name", "body", "needle"),
        [
            ("slack", {"settings": {"webhook_url": "ftp://x/y"}}, "http:// or https://"),
            ("slack-b", {"type": "slack", "route": {"apps": ["bad name"]}}, "route.apps"),
            ("slack-c", {"type": "slack", "quiet": {"start": "9", "end": "7"}}, "quiet.start"),
            ("slack-e", {"type": "slack", "flood": {"max": 0, "minutes": 5}}, "flood.max"),
            ("slack-f", {"type": "slack", "summary": {"at": "soon"}}, "summary.at"),
            ("slack-d", {"type": "fax"}, "Unknown channel type"),
            ("mine", {}, "needs a type"),
            ("Bad Name", {"type": "slack"}, "Invalid channel name"),
            ("slack", {"settings": {"colour": "red"}}, "no setting"),
            ("slack", {"events": {"no_such": True}}, "no event"),
        ],
    )
    def test_a_refusal_names_the_channel_and_the_setting_and_writes_nothing(
        self, client: TestClient, config_path: Path, name: str, body: dict, needle: str
    ) -> None:
        elevate(client)

        response = client.put(f"/api/notifications/channels/{name}", json=body)

        assert response.status_code == 400, response.text
        assert needle in response.text
        assert not config_path.exists() or name.lower() not in config_path.read_text().replace(
            "slack", ""
        )

    def test_a_channel_is_removed_and_a_default_is_emptied(self, client: TestClient) -> None:
        elevate(client)
        client.put("/api/notifications/channels/slack", json={"settings": {"webhook_url": SLACK}})
        client.put(
            "/api/notifications/channels/slack-staging",
            json={"type": "slack", "settings": {"webhook_url": STAGING}},
        )

        assert client.delete("/api/notifications/channels/slack-staging").status_code == 204
        assert client.delete("/api/notifications/channels/slack").status_code == 204

        listed = client.get("/api/notifications/channels").json()
        assert [i["name"] for i in listed["items"]].count("slack-staging") == 0
        assert by_name(listed, "slack")["configured"] is False
        assert client.delete("/api/notifications/channels/slack-staging").status_code == 404
        assert client.delete("/api/notifications/channels/slack").status_code == 404

    def test_a_viewer_may_read_but_not_change(self, anonymous: TestClient) -> None:
        assert anonymous.get("/api/notifications/channels").status_code == 401
        assert anonymous.put("/api/notifications/channels/slack", json={}).status_code in (401, 403)

    def test_a_test_goes_through_any_channels_name(self, client: TestClient) -> None:
        elevate(client)
        client.put(
            "/api/notifications/channels/slack-staging",
            json={"type": "slack", "settings": {"webhook_url": STAGING}},
        )

        response = client.post("/api/config/notifications/nope/test")

        assert response.status_code == 200
        assert response.json()["ok"] is False
        assert "Unknown notification channel" in response.json()["detail"]
        assert "slack-staging" in response.json()["detail"]


class TestEvents:
    def test_every_event_is_listed_with_its_default(self, client: TestClient) -> None:
        response = client.get("/api/notifications/events")

        assert response.status_code == 200
        items = {item["kind"]: item for item in response.json()["items"]}
        assert items["deploy_failed"] == {
            "kind": "deploy_failed",
            "default_on": True,
            "enabled": True,
        }
        assert items["cron_success"]["default_on"] is False
        assert {"alert_firing", "backup_missing", "os_patches", "token_expiring"} <= set(items)


def note(subject: str = "shop.example.com") -> Notification:
    return Notification(
        kind="deploy_failed",
        code="deploy.failed",
        state=State.FAILED,
        locale="en",
        title="Deploy failed",
        subject=subject,
        summary="It stopped.",
        server="web-1",
    )


class TestDeliveries:
    def test_the_record_is_read_with_its_counts(
        self, client: TestClient, store: NoustStore
    ) -> None:
        ledger = DeliveryLedger(store)
        ledger.record_sent(note("a.example.com"), "slack", "slack")
        ledger.record_failure(note("b.example.com"), "teams", "teams", "HTTP 404", retry_in=None)

        body = client.get("/api/notifications/deliveries").json()

        assert [i["subject"] for i in body["items"]] == ["b.example.com", "a.example.com"]
        assert body["counts"] == {"retrying": 0, "held": 0, "sent": 1, "failed": 1}
        assert "payload" not in body["items"][0]
        failed = client.get("/api/notifications/deliveries?state=failed").json()["items"]
        assert [i["last_error"] for i in failed] == ["HTTP 404"]
        assert (
            client.get("/api/notifications/deliveries?channel=slack").json()["items"][0]["channel"]
            == "slack"
        )

    def test_an_unknown_state_is_refused(self, client: TestClient) -> None:
        response = client.get("/api/notifications/deliveries?state=lost")

        assert response.status_code == 400
        assert "retrying" in response.text

    def test_a_failed_delivery_is_sent_again(
        self, client: TestClient, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        elevate(client)
        client.put("/api/notifications/channels/slack", json={"settings": {"webhook_url": SLACK}})
        config = Config()
        config.set("notifications.enabled", True)
        config.write()
        delivery_id = DeliveryLedger(store).record_failure(
            note(), "slack", "slack", "HTTP 503", retry_in=None
        )
        sent: list[str] = []

        def opener(request: Request, timeout: float | None = None) -> io.BytesIO:
            sent.append(request.full_url)
            return io.BytesIO(b"ok")

        monkeypatch.setattr("noust.core.notifier._resolve_host", lambda host: ("93.184.216.34",))
        monkeypatch.setattr(
            "noust.web.api.notifications._notifier",
            lambda: Notifier(Config(), opener=opener, ledger=DeliveryLedger(store)),
        )

        response = client.post(f"/api/notifications/deliveries/{delivery_id}/retry")

        assert response.status_code == 200, response.text
        assert response.json()["state"] == "sent"
        assert sent == [SLACK]

    def test_one_that_went_out_cannot_be_sent_again(
        self, client: TestClient, store: NoustStore
    ) -> None:
        delivery_id = DeliveryLedger(store).record_sent(note(), "slack", "slack")

        response = client.post(f"/api/notifications/deliveries/{delivery_id}/retry")

        assert response.status_code == 404

    def test_the_sweep_answers_with_what_it_did(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "noust.web.api.notifications._notifier", lambda: Notifier(Config(), record=False)
        )

        response = client.post("/api/notifications/deliveries/sweep")

        assert response.status_code == 200
        assert response.json() == {"delivered": 0, "retried": 0, "failed": 0, "digests": 0}


class TestAlertRules:
    BODY = {
        "metric": "cpu",
        "comparator": ">",
        "threshold": "90%",
        "duration_s": 300,
        "hysteresis": 5,
        "cooldown_s": 1800,
        "severity": "failed",
    }

    def test_a_rule_is_created_listed_and_removed(self, client: TestClient) -> None:
        saved = client.put("/api/alerts/rules/cpu-high", json=self.BODY)

        assert saved.status_code == 200, saved.text
        assert saved.json()["threshold"] == 90.0
        assert saved.json()["unit"] == "percent" and saved.json()["scope"] == "server"
        listed = client.get("/api/alerts/rules").json()
        assert [r["name"] for r in listed["items"]] == ["cpu-high"]
        assert {m["id"] for m in listed["metrics"]} >= {"cpu", "app_5xx", "app_disk_read"}
        assert listed["firing"] == 0

        assert client.delete("/api/alerts/rules/cpu-high").status_code == 204
        assert client.get("/api/alerts/rules").json()["items"] == []
        assert client.delete("/api/alerts/rules/cpu-high").status_code == 404

    def test_a_limit_is_read_with_the_unit_of_the_metric(self, client: TestClient) -> None:
        client.put(
            "/api/alerts/rules/mem",
            json={
                "metric": "app_memory",
                "comparator": ">",
                "threshold": "512MB",
                "app_domain": "shop.example.com",
            },
        )
        client.put(
            "/api/alerts/rules/net",
            json={"metric": "app_net_tx", "comparator": ">", "threshold": "2MB/s"},
        )

        items = {r["name"]: r for r in client.get("/api/alerts/rules").json()["items"]}

        assert items["mem"]["threshold"] == 512 * 1024**2
        assert items["mem"]["app_domain"] == "shop.example.com"
        assert items["net"]["threshold"] == 2 * 1024**2

    @pytest.mark.parametrize(
        ("name", "overrides", "needle"),
        [
            ("cpu-high", {"metric": "joy"}, "Unknown metric"),
            ("cpu-high", {"comparator": "="}, "Unknown comparator"),
            ("cpu-high", {"threshold": "MB"}, "Not a limit"),
            ("cpu-high", {"threshold": "90MB"}, "does not fit"),
            ("cpu-high", {"duration_s": 5}, "duration"),
            ("Bad Name", {}, "Invalid rule name"),
            ("cpu-high", {"app_domain": "shop.example.com"}, "server metric"),
            ("cpu-high", {"severity": "meh"}, "severity"),
        ],
    )
    def test_a_rule_that_cannot_be_kept_is_refused_naming_the_field(
        self, client: TestClient, name: str, overrides: dict, needle: str
    ) -> None:
        response = client.put(f"/api/alerts/rules/{name}", json={**self.BODY, **overrides})

        assert response.status_code in (400, 422), response.text
        assert needle in response.text
        assert client.get("/api/alerts/rules").json()["items"] == []

    def firing(self, store: NoustStore, subject: str = "") -> None:
        rules = AlertRules(store)
        saved = rules.save(
            AlertRule(
                name="cpu-high",
                metric="cpu",
                comparator=">",
                threshold=90.0,
                duration_s=300,
                cooldown_s=1800,
            )
        )
        assert saved.id is not None
        rules.put_state(
            AlertState(saved.id, subject, "firing", time.time() - 600, 95.0, announced=True)
        )

    @pytest.fixture
    def published(self, monkeypatch: pytest.MonkeyPatch) -> list[Notification]:
        sent: list[Notification] = []
        context = NotificationContext(locale="en", server="web-1", public_url="")
        monkeypatch.setattr(
            "noust.web.api.alerts._publish", lambda: lambda build: sent.append(build(context))
        )
        return sent

    def test_somebody_takes_a_firing_alert_and_the_channels_hear_who(
        self, client: TestClient, store: NoustStore, published: list[Notification]
    ) -> None:
        client.put("/api/alerts/rules/cpu-high", json=self.BODY)
        self.firing(store)

        taken = client.post("/api/alerts/rules/cpu-high/acknowledge")

        assert taken.status_code == 200, taken.text
        (state,) = taken.json()["states"]
        assert state["acknowledged_by"] and state["acknowledged_at"] is not None
        (note,) = published
        assert note.kind == "alert_acknowledged"
        assert state["acknowledged_by"] in note.summary
        listed = client.get("/api/alerts/rules").json()["items"][0]["states"][0]
        assert listed["acknowledged_by"] == state["acknowledged_by"]

        again = client.post("/api/alerts/rules/cpu-high/acknowledge")
        assert again.status_code == 200
        assert len(published) == 1

    def test_an_application_alert_is_taken_by_application(
        self, client: TestClient, store: NoustStore, published: list[Notification]
    ) -> None:
        client.put("/api/alerts/rules/cpu-high", json=self.BODY)
        self.firing(store, "shop.example.com")

        wrong = client.post("/api/alerts/rules/cpu-high/acknowledge")
        right = client.post(
            "/api/alerts/rules/cpu-high/acknowledge", params={"subject": "shop.example.com"}
        )

        assert wrong.status_code == 400 and "not firing" in wrong.text
        assert right.status_code == 200
        assert published[0].domain == "shop.example.com"

    def test_it_is_handed_back(
        self, client: TestClient, store: NoustStore, published: list[Notification]
    ) -> None:
        client.put("/api/alerts/rules/cpu-high", json=self.BODY)
        self.firing(store)
        client.post("/api/alerts/rules/cpu-high/acknowledge")

        back = client.delete("/api/alerts/rules/cpu-high/acknowledge")

        assert back.status_code == 200, back.text
        assert back.json()["states"][0]["acknowledged_by"] is None
        assert len(published) == 1

    def test_what_is_not_firing_or_does_not_exist_is_refused(
        self, client: TestClient, published: list[Notification]
    ) -> None:
        client.put("/api/alerts/rules/cpu-high", json=self.BODY)

        calm = client.post("/api/alerts/rules/cpu-high/acknowledge")
        unknown = client.post("/api/alerts/rules/nope/acknowledge")
        unknown_back = client.delete("/api/alerts/rules/nope/acknowledge")

        assert calm.status_code == 400 and "not firing" in calm.text
        assert unknown.status_code == 404 and unknown_back.status_code == 404
        assert published == []

    def test_taking_it_is_audited_and_needs_a_session(
        self,
        client: TestClient,
        anonymous: TestClient,
        store: NoustStore,
        published: list[Notification],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        events: list[dict] = []
        monkeypatch.setattr(
            "noust.web.api.alerts.audit.record",
            lambda event, **fields: events.append({"event": event, **fields}),
        )
        client.put("/api/alerts/rules/cpu-high", json=self.BODY)
        self.firing(store)

        assert anonymous.post("/api/alerts/rules/cpu-high/acknowledge").status_code == 401
        client.post("/api/alerts/rules/cpu-high/acknowledge")

        taken = [e for e in events if e["event"] == "alert.rule.acknowledge"]
        assert len(taken) == 1
        assert taken[0]["target"] == "alert:cpu-high" and taken[0]["details"]["taken"] is True

    def test_a_rule_is_switched_off_and_on(self, client: TestClient) -> None:
        client.put("/api/alerts/rules/cpu-high", json=self.BODY)

        off = client.post("/api/alerts/rules/cpu-high/disable")
        on = client.post("/api/alerts/rules/cpu-high/enable")

        assert off.json()["enabled"] is False and on.json()["enabled"] is True
        assert client.post("/api/alerts/rules/nope/enable").status_code == 404

    def test_where_a_rule_stands_is_listed_with_it(
        self, client: TestClient, store: NoustStore
    ) -> None:
        from noust.core.alerts import AlertRules, AlertState

        client.put("/api/alerts/rules/cpu-high", json=self.BODY)
        rules = AlertRules(store)
        rule = rules.get("cpu-high")
        assert rule is not None and rule.id is not None
        rules.put_state(AlertState(rule.id, "", "firing", 1.0, 95.0, announced=True))

        body = client.get("/api/alerts/rules").json()

        assert body["firing"] == 1
        assert body["items"][0]["states"] == [
            {
                "subject": "",
                "state": "firing",
                "since": 1.0,
                "last_value": 95.0,
                "announced": True,
                "acknowledged_at": None,
                "acknowledged_by": None,
            }
        ]

    def test_it_needs_a_session(self, anonymous: TestClient) -> None:
        assert anonymous.get("/api/alerts/rules").status_code == 401
        assert anonymous.put("/api/alerts/rules/x", json={}).status_code in (401, 403)
