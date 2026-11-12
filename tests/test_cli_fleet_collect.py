# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust fleet collect ...``: the copies a central takes from its servers, from a terminal (item 195).

The engine is tested on its own; these pin what the commands add: what is said, what is recorded in
the audit trail, that a rehearsal changes nothing, that a knob that is not right is refused naming the
setting, and that the command a message suggests runs.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core import audit
from noust.core.runner import set_runner
from noust.core.store import NoustStore
from noust.fleet.collect import collector as collector_module
from noust.fleet.collect.collector import PassResult
from noust.fleet.collect.records import Collected, CollectedFile, CollectedStore
from noust.fleet.collect.settings import Follows, SettingsStore
from tests.support.node_proxy import FakeManager, FakeTunnels


@pytest.fixture(autouse=True)
def default_runner_after_a_rehearsal() -> Iterator[None]:
    yield
    set_runner(None)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    template = FakeManager(FakeTunnels(0)).records["web-2"]
    for name in ("web-1", "web-2"):
        instance.save_node(replace(template, name=name, ssh_host=f"{name}.example.com"))
    monkeypatch.setattr(
        "noust.fleet.collect.layout.Layout.default",
        classmethod(lambda cls: cls(tmp_path / "central" / ".collected")),
    )
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    recorded: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(audit, "record", lambda event, **kw: recorded.append((event, kw)))
    monkeypatch.setattr("noust.cli.commands.fleet_collect.record_audit", audit.record)
    return recorded


def said(result: Any) -> str:
    """Everything a command said, an error's words and its hint included."""
    error = result.exception
    return f"{result.output}\n{error}\n{getattr(error, 'details', '') or ''}"


def invoke(*args: str, input: str | None = None) -> Any:
    return CliRunner().invoke(root_cli, ["fleet", "collect", *args], input=input)


def held(node: str = "web-1", item: str = "shop_1") -> Collected:
    return Collected(
        node=node,
        kind="app",
        item_id=item,
        owner="shop.example.com",
        folder="shop",
        origin="scheduled",
        created_at="2026-11-10T02:00:00+00:00",
        collected_at="2026-11-10T02:05:00+00:00",
        size=2048,
        files=(CollectedFile(f"{item}.tar.gz", 2048, "a" * 64, "archive"),),
        verified="digest",
        meta={"domain": "shop.example.com"},
    )


class TestStatus:
    def test_it_says_nothing_is_followed_and_how_to_change_that(self, store: NoustStore) -> None:
        result = invoke("status")

        assert result.exit_code == 0, result.output
        assert "0 of 2 servers" in result.output
        assert "noust fleet collect enable" in result.output

    def test_it_is_json_for_a_script(self, store: NoustStore) -> None:
        SettingsStore(store).set(enabled=True)

        body = json.loads(invoke("status", "--json").output)

        assert body["following"] == {"following": 2, "of": 2, "exceptions": []}
        assert body["settings"]["enabled"] is True and body["space"]["free"] > 0


class TestTurningItOn:
    def test_enable_and_disable_are_recorded(
        self, store: NoustStore, events: list[tuple[str, dict[str, Any]]]
    ) -> None:
        assert invoke("enable").exit_code == 0
        assert SettingsStore(store).get().enabled is True
        assert invoke("disable").exit_code == 0
        assert SettingsStore(store).get().enabled is False
        assert [e for e, _ in events if e.startswith("central_backup.")] == [
            "central_backup.settings"
        ] * 2

    def test_a_rehearsal_changes_nothing(self, store: NoustStore) -> None:
        result = CliRunner().invoke(root_cli, ["--dry-run", "fleet", "collect", "enable"])

        assert result.exit_code == 0 and "Would enable" in result.output
        assert SettingsStore(store).get().enabled is False

    def test_a_server_gets_its_own_answer_and_gives_it_back(self, store: NoustStore) -> None:
        assert invoke("follow", "web-1", "on").exit_code == 0
        assert Follows(store).follows("web-1") is True
        assert invoke("follow", "web-1", "fleet").exit_code == 0
        assert Follows(store).follows("web-1") is False

    def test_a_server_that_is_not_managed_is_refused(self, store: NoustStore) -> None:
        result = invoke("follow", "ghost", "on")

        assert result.exit_code != 0 and "ghost" in said(result)


class TestTheKnobs:
    def test_the_speed_limit_is_set_and_taken_off(self, store: NoustStore) -> None:
        assert invoke("set", "rate_limit_kbps", "2048").exit_code == 0
        assert SettingsStore(store).get().rate_limit_kbps == 2048
        assert invoke("set", "rate_limit_kbps", "none").exit_code == 0
        assert SettingsStore(store).get().rate_limit_kbps is None

    def test_the_kinds_are_a_comma_separated_list(self, store: NoustStore) -> None:
        invoke("set", "kinds", "app,noust")

        assert SettingsStore(store).get().kinds == ("app", "noust")

    def test_a_value_that_is_not_right_is_refused_naming_the_setting(
        self, store: NoustStore
    ) -> None:
        result = invoke("set", "poll_seconds", "1")

        assert result.exit_code != 0 and "poll_seconds" in said(result)

    def test_a_setting_that_does_not_exist_names_the_ones_that_do(self, store: NoustStore) -> None:
        result = invoke("set", "speed", "1")

        assert result.exit_code != 0 and "rate_limit_kbps" in said(result)

    def test_a_number_that_is_not_one_is_refused(self, store: NoustStore) -> None:
        assert invoke("set", "retention_gb", "lots").exit_code != 0


class TestWhatIsHeld:
    def test_the_list_shows_each_copy(self, store: NoustStore) -> None:
        CollectedStore(store).add(held())

        result = invoke("list")

        assert "shop.example.com" in result.output and "shop_1" in result.output
        body = json.loads(invoke("list", "--json").output)
        assert body["total"] == 1 and body["items"][0]["verified"] == "digest"

    def test_an_empty_central_says_where_to_look(self, store: NoustStore) -> None:
        assert "noust fleet collect status" in invoke("list").output

    def test_a_copy_is_let_go_after_asking_and_recorded(
        self,
        store: NoustStore,
        events: list[tuple[str, dict[str, Any]]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        CollectedStore(store).add(held())
        gone: list[str] = []
        monkeypatch.setattr(
            collector_module.NodeCollector,
            "let_go",
            lambda self, copy, *, reason: gone.append(copy.item_id),
        )

        refused = invoke("delete", "web-1", "app", "shop_1", input="n\n")
        done = invoke("delete", "web-1", "app", "shop_1", "-y")

        assert refused.exit_code != 0 and gone == ["shop_1"]
        assert done.exit_code == 0
        assert [e for e, _ in events if e.startswith("central_backup.")] == [
            "central_backup.delete"
        ]

    def test_a_copy_the_central_does_not_hold_is_refused_with_the_command_that_lists(
        self, store: NoustStore
    ) -> None:
        result = invoke("delete", "web-1", "app", "nope", "-y")

        assert result.exit_code != 0 and "noust fleet collect list" in said(result)


class TestRunningAPass:
    def test_a_pass_is_made_over_every_followed_server_and_reported(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        SettingsStore(store).set(enabled=True)
        Follows(store).set_follow("web-2", False)
        asked: list[tuple[str, bool]] = []

        def collect(self: Any, node: str, *, force: bool = False) -> PassResult:
            asked.append((node, force))
            return PassResult(node, "ok", fetched=3, held=1)

        monkeypatch.setattr(collector_module.NodeCollector, "collect", collect)

        result = invoke("run", "--force")

        assert result.exit_code == 0 and asked == [("web-1", True)]
        assert "web-1: ok, 3 taken, 1 already held" in result.output

    def test_a_server_that_failed_is_an_exit_status_and_says_why(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def collect(self: Any, node: str, *, force: bool = False) -> PassResult:
            return PassResult(node, "failed", failures=["web-1 did not answer"], code="unreachable")

        monkeypatch.setattr(collector_module.NodeCollector, "collect", collect)

        result = invoke("run", "web-1")

        assert result.exit_code == 1 and "web-1 did not answer" in result.output

    def test_a_rehearsal_takes_nothing(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        SettingsStore(store).set(enabled=True)
        monkeypatch.setattr(
            collector_module.NodeCollector, "collect", lambda *a, **k: pytest.fail("it ran")
        )

        result = CliRunner().invoke(root_cli, ["--dry-run", "fleet", "collect", "run"])

        assert result.exit_code == 0 and "Would take the copies of: web-1, web-2" in result.output


class TestGivingACopyBack:
    def test_it_goes_to_the_server_asked_as_the_operator_of_this_central(
        self,
        store: NoustStore,
        events: list[tuple[str, dict[str, Any]]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        CollectedStore(store).add(held())
        seen: dict[str, Any] = {}

        class Giver:
            def __init__(self, **kw: Any) -> None:
                seen.update(kw)

            def run(self, **kw: Any) -> dict[str, Any]:
                seen["run"] = kw
                return {
                    "node": seen["target"],
                    "result": {"command": "noust self-backup restore x"},
                }

        monkeypatch.setattr("noust.fleet.collect.restore.GiveBack", Giver)

        result = invoke(
            "restore",
            "web-1",
            "app",
            "shop_1",
            "--to",
            "web-2",
            "--domain",
            "shop.example.com",
            "-y",
        )

        assert result.exit_code == 0, result.output
        assert seen["target"] == "web-2" and seen["asker"].elevated is True
        assert seen["run"] == {"target_args": {"domain": "shop.example.com"}}
        assert "Restored on web-2" in result.output
        assert "noust self-backup restore x" in result.output
        assert [e for e, _ in events if e.startswith("central_backup.")] == [
            "central_backup.restore"
        ]

    def test_it_asks_before_it_replaces_anything(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        CollectedStore(store).add(held())
        monkeypatch.setattr(
            "noust.fleet.collect.restore.GiveBack", lambda **kw: pytest.fail("it restored")
        )

        result = invoke("restore", "web-1", "app", "shop_1", input="n\n")

        assert result.exit_code != 0
