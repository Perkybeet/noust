# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What needs an operator is one list, joined and ordered by the server (item 161).

The bell counted what the Overview listed and what the console added, the Server page counted its own
facts and the checks of Security another way, and an operator was told 2, 8 and 10 on the same
afternoon. :mod:`noust.managers.attention` is the one place that joins the sources, ranks them,
says where each row comes from (an alert, a state, a security finding) and counts them once.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from noust.managers import attention
from noust.managers.attention import (
    AttentionList,
    machine_items,
    noust_item,
    origin_of,
    reason,
    recipe_items,
    request_items,
    security_items,
)

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


def summary(**sections: Any) -> dict[str, Any]:
    """A server whose summary has nothing to say, with sections replaced."""
    base: dict[str, Any] = {
        "os": {"name": "Ubuntu 24.04", "eol": {"status": "ok", "end_date": "2029-05-31"}},
        "updates": {"supported": True, "pending": 0, "security": 0, "broken": False},
        "reboot": {"required": False},
        "disk": {"status": "ok", "worst_mount": "/", "worst_percent": 40.0, "free_bytes": 10**11},
        "time": {"synchronized": True, "timezone": "Europe/Madrid"},
        "swap": {"total_bytes": 2 * 1024**3, "recommended": False},
        "system": {"state": "running", "failed_units": []},
    }
    base.update(sections)
    return base


def check(identifier: str, status: str = "fail", severity: str = "critical", **fields: Any) -> dict:
    return {
        "id": identifier,
        "group": fields.pop("group", "firewall"),
        "title": fields.pop("title", f"Title of {identifier}"),
        "severity": severity,
        "status": status,
        "reason": fields.pop("text", f"What was found by {identifier}."),
        "evidence": [],
        **fields,
    }


class TestWhereARowComesFrom:
    @pytest.mark.parametrize(
        ("kind", "origin"),
        [
            ("alert", "alert"),
            ("probe", "alert"),
            ("monitor", "alert"),
            ("security", "security"),
            ("state", "state"),
            ("deploy", "state"),
            ("first_deploy", "state"),
            ("certificate", "state"),
            ("unit", "state"),
            ("machine", "state"),
            ("approval", "state"),
            ("noust", "state"),
            ("recipe", "state"),
        ],
    )
    def test_each_kind_of_reason_belongs_to_one_origin(self, kind: str, origin: str) -> None:
        assert origin_of(kind) == origin
        assert reason(kind, "warn", "x")["origin"] == origin

    def test_a_kind_nobody_ruled_on_is_a_state_not_a_hole(self) -> None:
        assert origin_of("something_new") == "state"


class TestTheOneList:
    def test_the_worst_comes_first_then_a_state_before_an_alert_before_a_finding_then_by_name(
        self,
    ) -> None:
        rows = AttentionList()
        rows.add("a", {"kind": "alert"}, "zeta rule", reason("alert", "warn", "alert_firing"))
        rows.add("b", {"kind": "finding"}, "Open port", reason("security", "warn", "finding"))
        rows.add("c", {"kind": "app"}, "beta.example.com", reason("state", "warn", "slow"))
        rows.add("d", {"kind": "app"}, "alpha.example.com", reason("state", "warn", "slow"))
        rows.add("e", {"kind": "probe"}, "shop", reason("probe", "fail", "probe_down"))

        items, total = rows.result()

        assert [item["title"] for item in items] == [
            "shop",
            "alpha.example.com",
            "beta.example.com",
            "zeta rule",
            "Open port",
        ]
        assert total == 5

    def test_a_subject_is_one_row_with_every_reason_and_its_worst_severity(self) -> None:
        rows = AttentionList()
        rows.add("app:shop", {"kind": "app"}, "shop", reason("certificate", "warn", "expires"))
        rows.add("app:shop", {"kind": "app"}, "shop", reason("state", "fail", "service_failed"))

        [item], total = rows.result()

        assert total == 1
        assert item["severity"] == "fail"
        assert [r["code"] for r in item["reasons"]] == ["expires", "service_failed"]

    def test_the_row_says_the_origin_of_the_reason_that_made_it_serious(self) -> None:
        rows = AttentionList()
        rows.add("x", {"kind": "app"}, "x", reason("certificate", "warn", "expires"))
        rows.add("x", {"kind": "app"}, "x", reason("alert", "fail", "alert_firing"))

        [item], _ = rows.result()

        assert item["origin"] == "alert"

    def test_the_list_is_cut_and_the_total_still_counts_every_row(self) -> None:
        rows = AttentionList()
        for index in range(5):
            rows.add(f"k{index}", {"kind": "app"}, f"app-{index}", reason("state", "warn", "x"))

        items, total = rows.result(limit=3)

        assert len(items) == 3
        assert total == 5

    def test_what_a_second_source_adds_joins_the_rows_that_are_already_there(self) -> None:
        rows = AttentionList()
        rows.add("app:shop", {"kind": "app"}, "shop", reason("state", "warn", "slow"))
        rows.extend(
            [
                {
                    "id": "app:shop",
                    "subject": {"kind": "app"},
                    "title": "shop",
                    "severity": "fail",
                    "reasons": [reason("security", "fail", "finding")],
                },
                {
                    "id": "noust:update",
                    "subject": {"kind": "noust"},
                    "title": "Noust",
                    "severity": "warn",
                    "reasons": [reason("noust", "warn", "noust_update")],
                },
            ]
        )

        items, total = rows.result()

        assert total == 2
        assert items[0]["id"] == "app:shop" and len(items[0]["reasons"]) == 2
        assert items[0]["severity"] == "fail"

    def test_an_empty_list_is_empty(self) -> None:
        assert AttentionList().result() == ([], 0)


class TestTheMachine:
    def test_a_server_with_nothing_wrong_says_nothing(self) -> None:
        assert machine_items(summary()) == []

    def test_a_summary_that_could_not_be_read_says_nothing(self) -> None:
        assert machine_items(None) == []
        assert machine_items({"updates": {"error": "locked"}}) == []

    def test_pending_security_updates_are_a_warning_with_their_number(self) -> None:
        [item] = machine_items(summary(updates={"supported": True, "security": 4}))

        assert item["id"] == "machine:security_updates"
        assert item["subject"] == {"kind": "machine", "fact": "security_updates"}
        assert item["severity"] == "warn"
        assert item["reasons"][0]["code"] == "security_updates"
        assert item["reasons"][0]["params"] == {"count": 4}
        assert item["reasons"][0]["origin"] == "state"
        assert item["reasons"][0]["actions"] == ["open_updates"]

    def test_broken_packages_are_a_failure(self) -> None:
        [item] = machine_items(summary(updates={"broken": True}))

        assert (item["id"], item["severity"]) == ("machine:packages_broken", "fail")

    def test_a_reboot_that_is_due_says_why(self) -> None:
        [item] = machine_items(
            summary(
                reboot={
                    "required": True,
                    "since": "2026-11-10T04:00:00+00:00",
                    "packages": ["linux-image-6.8.0-47-generic"],
                }
            )
        )

        assert item["reasons"][0]["code"] == "reboot_required"
        assert item["reasons"][0]["params"]["packages"] == ["linux-image-6.8.0-47-generic"]
        assert item["reasons"][0]["when"] == "2026-11-10T04:00:00+00:00"
        assert item["reasons"][0]["actions"] == ["schedule_reboot"]

    def test_an_operating_system_past_its_support_is_a_failure_and_one_about_to_be_a_warning(
        self,
    ) -> None:
        [expired] = machine_items(
            summary(
                os={"name": "Ubuntu 20.04", "eol": {"status": "expired", "end_date": "2025-04-30"}}
            )
        )
        [ending] = machine_items(
            summary(
                os={
                    "name": "Ubuntu 22.04",
                    "eol": {"status": "warn", "days_left": 40, "end_date": "2026-12-22"},
                }
            )
        )

        assert (expired["reasons"][0]["code"], expired["severity"]) == ("os_unsupported", "fail")
        assert expired["reasons"][0]["params"] == {"name": "Ubuntu 20.04", "date": "2025-04-30"}
        assert (ending["reasons"][0]["code"], ending["severity"]) == ("os_ending", "warn")
        assert ending["reasons"][0]["params"]["days"] == 40

    def test_a_full_disk_is_a_failure_and_a_filling_one_a_warning(self) -> None:
        full = {
            "status": "critical",
            "worst_mount": "/var",
            "worst_percent": 96.0,
            "free_bytes": 10**9,
        }
        filling = {"status": "warn", "worst_mount": "/", "worst_percent": 88.0}

        [critical] = machine_items(summary(disk=full))
        [warning] = machine_items(summary(disk=filling))

        assert (critical["severity"], warning["severity"]) == ("fail", "warn")
        assert critical["reasons"][0]["params"] == {"mount": "/var", "percent": 96.0, "free": 10**9}
        assert warning["reasons"][0]["params"]["free"] is None
        assert critical["reasons"][0]["actions"] == ["free_space"]

    def test_a_clock_out_of_sync_and_a_server_with_no_swap_are_warnings(self) -> None:
        facts = machine_items(
            summary(
                time={"synchronized": False, "timezone": "UTC"},
                swap={"total_bytes": 0, "recommended": True},
            )
        )

        assert {item["id"] for item in facts} == {"machine:clock", "machine:swap"}
        assert {item["severity"] for item in facts} == {"warn"}

    def test_a_server_that_does_not_need_swap_is_not_told_to_add_it(self) -> None:
        assert machine_items(summary(swap={"total_bytes": 0, "recommended": False})) == []

    def test_failed_units_the_list_already_names_are_not_said_twice(self) -> None:
        facts = machine_items(
            summary(
                system={
                    "state": "degraded",
                    "failed_units": ["noust-web.service", "fail2ban.service"],
                }
            ),
            known_units={"noust-web"},
        )

        [item] = facts
        assert item["reasons"][0]["code"] == "failed_units"
        assert item["reasons"][0]["params"] == {"units": ["fail2ban.service"], "count": 1}

    def test_when_every_failed_unit_is_already_in_the_list_there_is_no_row(self) -> None:
        assert (
            machine_items(
                summary(system={"state": "degraded", "failed_units": ["shop-example-com.service"]}),
                known_units={"shop-example-com"},
            )
            == []
        )


class TestSecurity:
    def test_a_critical_finding_is_a_failure_and_a_warning_one_a_warning(self) -> None:
        items = security_items(
            [
                check("fw.docker_bypass", "fail", "critical", group="firewall"),
                check("ssh.password_auth", "warn", "warning", group="ssh"),
            ]
        )

        assert [(item["id"], item["severity"]) for item in items] == [
            ("security:fw.docker_bypass", "fail"),
            ("security:ssh.password_auth", "warn"),
        ]
        assert items[0]["subject"] == {
            "kind": "finding",
            "check": "fw.docker_bypass",
            "group": "firewall",
        }
        assert items[0]["reasons"][0]["origin"] == "security"
        assert items[0]["origin"] == "security"

    def test_what_the_check_found_travels_in_its_own_words(self) -> None:
        [item] = security_items(
            [check("fw.docker_bypass", text="Port 5432 is published on 0.0.0.0.")]
        )

        assert item["title"] == "Title of fw.docker_bypass"
        assert item["reasons"][0]["detail"] == "Port 5432 is published on 0.0.0.0."

    def test_what_passes_what_was_accepted_and_what_is_only_information_are_not_findings(
        self,
    ) -> None:
        assert (
            security_items(
                [
                    check("a", "pass", "critical"),
                    check("b", "accepted", "critical"),
                    check("c", "unknown", "critical"),
                    check("d", "warn", "info"),
                    check("e", "warn", "low"),
                ]
            )
            == []
        )

    def test_a_check_the_machine_facts_already_say_with_numbers_is_left_to_them(self) -> None:
        assert security_items([check("upd.security_pending"), check("disk.full")]) == []


class TestWhatIsForOnePerson:
    def request(self, number: int, by: str = "ana") -> dict[str, Any]:
        return {"id": number, "requester": {"name": by}, "created_at": 1_790_000_000 + number}

    def test_a_request_waiting_is_a_warning_with_who_asked(self) -> None:
        [item] = request_items([self.request(7)])

        assert item["id"] == "approval:7"
        assert item["subject"] == {"kind": "approval", "id": 7}
        assert item["title"] == "#7"
        assert item["severity"] == "warn"
        assert item["reasons"][0]["code"] == "approval_waiting"
        assert item["reasons"][0]["params"] == {"id": 7, "by": "ana"}
        assert item["reasons"][0]["actions"] == ["review_approval"]
        assert item["reasons"][0]["when"].startswith("2026-")

    def test_a_newer_noust_is_a_warning_and_a_security_release_says_so(self) -> None:
        [plain] = noust_item(
            {"has_update": True, "latest_version": "4.3.0", "current_version": "4.2.1"}
        )
        [urgent] = noust_item(
            {
                "has_update": True,
                "latest_version": "4.2.2",
                "current_version": "4.2.1",
                "security_update": True,
            }
        )

        assert plain["id"] == "noust:update"
        assert plain["reasons"][0]["code"] == "noust_update"
        assert plain["reasons"][0]["params"] == {"version": "4.3.0", "current": "4.2.1"}
        assert urgent["reasons"][0]["code"] == "noust_security_update"

    def test_a_noust_that_is_current_or_unchecked_says_nothing(self) -> None:
        assert noust_item({"has_update": False, "current_version": "4.2.1"}) == []
        assert noust_item(None) == []

    def test_an_installed_recipe_with_a_newer_version_is_a_warning(self) -> None:
        items = recipe_items(
            [
                {
                    "domain": "kuma.example.com",
                    "recipe": "uptime-kuma",
                    "update_available": True,
                    "installed_version": "1.0",
                    "latest_version": "1.1",
                },
                {
                    "domain": "blog.example.com",
                    "recipe": "ghost",
                    "update_available": False,
                    "installed_version": "5.0",
                    "latest_version": "5.0",
                },
            ]
        )

        [item] = items
        assert item["id"] == "recipe:kuma.example.com"
        assert item["reasons"][0]["params"] == {
            "recipe": "uptime-kuma",
            "version": "1.1",
            "current": "1.0",
        }


def test_the_limit_is_the_overviews() -> None:
    from noust.managers import overview

    assert attention.ATTENTION_LIMIT == overview.ATTENTION_LIMIT
