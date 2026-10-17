# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The web application firewall over the API and the command line.

What the server has, how a site is rendered and how the log is read are pinned in
``tests/test_waf.py``. Here: that the application's team reads its own events and another team does
not even see that it exists, that the mode and the rules left out travel through the policy like any
other field, that installing the packages is the server's (sudo mode, instance scope), and that the
flags become the same change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.store import NoustStore
from noust.deployers import access
from noust.managers.site_policy import SitePolicy, validated
from noust.managers.waf import WafEvent, WafSupport
from tests.teams_support import World, build_world

APP = "shop.acme.example"


def view(**changes: Any) -> access.AccessView:
    return access.AccessView(
        app=APP, policy=validated(SitePolicy(), changes), users=(), webserver="nginx"
    )


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Any:
    built = build_world(tmp_path, monkeypatch)
    try:
        yield built
    finally:
        NoustStore.reset_instance()


SUPPORT = WafSupport(
    available=False,
    webserver="nginx",
    module_loaded=False,
    module_installed=False,
    rules=False,
    problem="nginx has no ModSecurity module",
    hint="Install it: noust app policy waf install",
    installable=True,
    packages=("libnginx-mod-http-modsecurity", "modsecurity-crs"),
)


def event(uri: str, status: int, *rules: tuple[str, str]) -> WafEvent:
    return WafEvent(
        time="Sat Oct 17 01:21:33 2026",
        client="203.0.113.9",
        host="shop.acme.example",
        method="GET",
        uri=uri,
        status=status,
        rules=tuple((rule_id, message, "2") for rule_id, message in rules),
    )


# -- the API -----------------------------------------------------------------------


def test_the_team_reads_what_the_server_has_and_what_is_missing(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(access, "waf_support", lambda domain, **k: SUPPORT)

    response = world.client.get(f"/api/apps/{APP}/waf", headers=world.as_("dana"))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is False and body["problem"] == "nginx has no ModSecurity module"
    assert body["installable"] is True and body["packages"][1] == "modsecurity-crs"


def test_another_teams_application_is_not_there_to_ask_about(world: World) -> None:
    for path in (f"/api/apps/{APP}/waf", f"/api/apps/{APP}/waf/events"):
        assert world.client.get(path, headers=world.as_("bob")).status_code == 404, path


def test_the_events_come_with_the_rules_that_matched_most(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = [
        event("/?id=1' OR '1'='1", 403, ("942100", "SQL Injection"), ("949110", "Score")),
        event("/search?q=select", 200, ("942100", "SQL Injection"), ("949110", "Score")),
    ]
    seen: list[int] = []

    def fake(domain: str, *, limit: int = 50, **k: Any) -> Any:
        seen.append(limit)
        return events, [("942100", "SQL Injection", 2)]

    monkeypatch.setattr(access, "waf_events", fake)

    response = world.client.get(f"/api/apps/{APP}/waf/events?limit=5", headers=world.as_("alice"))

    assert response.status_code == 200, response.text
    body = response.json()
    assert seen == [5]
    first, second = body["events"]
    assert first["blocked"] is True and first["would_block"] is False
    assert second["blocked"] is False and second["would_block"] is True
    assert body["noisy"] == [{"id": "942100", "message": "SQL Injection", "count": 2}]


def test_a_limit_out_of_range_is_refused(world: World) -> None:
    response = world.client.get(
        f"/api/apps/{APP}/waf/events?limit=5000", headers=world.as_("alice")
    )

    assert response.status_code == 422


def test_the_mode_and_the_rules_left_out_go_through_the_policy(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []

    def fake(domain: str, changes: dict[str, Any], **kwargs: Any) -> access.AccessView:
        seen.append(changes)
        return view(**changes)

    monkeypatch.setattr(access, "set_policy", fake)

    response = world.client.put(
        f"/api/apps/{APP}/policy",
        json={"waf": "detect", "waf_exclusions": [942100, 941100]},
        headers=world.as_("alice"),
    )

    assert response.status_code == 200, response.text
    assert seen == [{"waf": "detect", "waf_exclusions": [942100, 941100]}]
    assert response.json()["policy"]["waf"] == "detect"
    assert response.json()["policy"]["waf_exclusions"] == [941100, 942100]


def test_a_policy_that_does_not_mention_it_does_not_touch_it(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(
        access, "set_policy", lambda d, changes, **k: seen.append(changes) or view()
    )

    world.client.put(f"/api/apps/{APP}/policy", json={"noindex": True}, headers=world.as_("alice"))

    assert "waf" not in seen[0] and "waf_exclusions" not in seen[0]


def test_installing_the_packages_is_the_servers_and_a_team_may_not(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed: list[bool] = []
    monkeypatch.setattr(
        access, "install_waf", lambda **k: installed.append(True) or ["modsecurity-crs"]
    )

    refused = world.client.post("/api/waf/install", headers=world.as_("alice"))
    allowed = world.client.post("/api/waf/install", headers=world.as_("admin"))

    assert refused.status_code == 403
    assert allowed.status_code == 200, allowed.text
    assert allowed.json() == {"packages": ["modsecurity-crs"]}
    assert installed == [True]


# -- the command line --------------------------------------------------------------


@pytest.fixture
def asked(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake(domain: str, changes: dict[str, Any], **kwargs: Any) -> access.AccessView:
        calls.append(changes)
        return view(**changes)

    monkeypatch.setattr(access, "set_policy", fake)
    monkeypatch.setattr(access, "describe", lambda *a, **k: view())
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    return calls


def invoke(*args: str) -> Any:
    return CliRunner().invoke(root_cli, ["app", "policy", *args])


def test_the_flags_become_the_mode_and_the_rules(asked: list[dict[str, Any]]) -> None:
    result = invoke(
        "set", "shop.example.com", "--waf", "block", "--waf-exclude", "942100", "--waf-exclude",
        "941100",
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert asked == [{"waf": "block", "waf_exclusions": [942100, 941100]}]


def test_off_and_clearing_the_rules_are_flags_too(asked: list[dict[str, Any]]) -> None:
    result = invoke("set", "shop.example.com", "--waf", "off", "--clear-waf-exclusions")

    assert result.exit_code == 0, result.output
    assert asked == [{"waf": "off", "waf_exclusions": []}]


def test_a_rule_that_is_not_a_number_is_a_usage_error(asked: list[dict[str, Any]]) -> None:
    result = invoke("set", "shop.example.com", "--waf-exclude", "942100;deny")

    assert result.exit_code == 2 and asked == []


def test_the_policy_shows_the_firewall_when_it_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        access, "describe", lambda *a, **k: view(waf="detect", waf_exclusions=[942100])
    )

    result = invoke("show", "shop.example.com")

    assert result.exit_code == 0, result.output


def test_the_events_are_listed_with_the_rule_to_leave_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        access,
        "waf_events",
        lambda domain, **k: (
            [event("/?id=1' OR '1'='1", 200, ("942100", "SQL Injection"), ("949110", "Score"))],
            [("942100", "SQL Injection", 1)],
        ),
    )

    result = invoke("waf", "events", "shop.example.com", "--json")

    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["events"][0]["would_block"] is True
    assert body["noisy"][0]["id"] == "942100"


def test_installing_is_rehearsed_and_not_done() -> None:
    result = CliRunner().invoke(root_cli, ["--dry-run", "app", "policy", "waf", "install"])

    assert result.exit_code == 0, result.output
