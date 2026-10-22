# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A release that fixes a vulnerability (L-43) and installed recipes behind the catalogue (D-15) are
announced with a notification, once.

What is pinned for the security release: it is announced when the update check says a release above
the running one fixes a vulnerability, once per release, again after ``monitor.security_renotify_hours``
while the server stays behind, not at all when the operator switched the update check off or the
check could not be made, and the memory is dropped once the server is up to date. For the recipes:
the list that is behind is announced when it changes and repeated weekly, never while it is empty.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.exceptions import IntegrationError
from noust.core.notifications.model import State
from noust.core.update_checker import UpdateChecker, UpdateCheckInProgress, VersionCheck
from noust.monitor import watch_recipes
from noust.monitor.watch_recipes import RecipeWatch, signature
from noust.monitor.watch_security import SecurityReleaseWatch
from noust.recipes.updates import RecipeUpdateStatus
from tests.monitor_rig import Rig

HOUR = 3600.0


def version_check(*, security: str | None, current: str = "3.3.1") -> VersionCheck:
    return VersionCheck(
        current=current,
        location="/opt/noust",
        method="pipx",
        installable="4.0.0",
        published="4.0.0",
        checked_at=0.0,
        security=security,
        advisory="GHSA-aaaa-bbbb-cccc" if security else None,
    )


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    instance = Rig(tmp_path)
    yield instance
    instance.close()


@pytest.fixture
def asked(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """What the update check answers; a test changes ``answer``."""
    state: dict[str, object] = {"answer": version_check(security="3.3.2"), "enabled": True}

    def check() -> VersionCheck:
        answer = state["answer"]
        if isinstance(answer, Exception):
            raise answer
        assert isinstance(answer, VersionCheck)
        return answer

    monkeypatch.setattr(UpdateChecker, "check", staticmethod(check))
    monkeypatch.setattr(UpdateChecker, "enabled", staticmethod(lambda: bool(state["enabled"])))
    return state


class TestSecurityRelease:
    def test_a_release_that_fixes_a_vulnerability_is_announced(
        self, rig: Rig, asked: dict[str, object]
    ) -> None:
        SecurityReleaseWatch().check(rig.services)

        [message] = rig.sent
        assert message.kind == "security_release"
        assert message.code == "security.release"
        assert message.state is State.WARNING
        assert message.subject == "Noust 3.3.2"
        facts = {fact.key: fact.value for fact in message.facts}
        assert facts["running"] == "3.3.1"
        assert facts["fixed_in"] == "3.3.2"
        assert facts["advisory"].endswith("GHSA-aaaa-bbbb-cccc")
        assert message.command is not None

    def test_the_same_release_is_announced_once(self, rig: Rig, asked: dict[str, object]) -> None:
        watch = SecurityReleaseWatch()
        watch.check(rig.services)
        rig.now += HOUR
        watch.check(rig.services)

        assert len(rig.sent) == 1

    def test_a_server_that_stays_behind_is_reminded_after_the_renotify_hours(
        self, rig: Rig, asked: dict[str, object]
    ) -> None:
        watch = SecurityReleaseWatch()
        watch.check(rig.services)
        rig.now += 71 * HOUR
        watch.check(rig.services)
        assert len(rig.sent) == 1

        rig.now += 2 * HOUR
        watch.check(rig.services)
        assert len(rig.sent) == 2

    def test_a_newer_security_release_is_a_new_announcement(
        self, rig: Rig, asked: dict[str, object]
    ) -> None:
        watch = SecurityReleaseWatch()
        watch.check(rig.services)
        asked["answer"] = version_check(security="3.3.3")
        rig.now += HOUR

        watch.check(rig.services)

        assert [m.subject for m in rig.sent] == ["Noust 3.3.2", "Noust 3.3.3"]

    def test_a_reminder_of_zero_hours_means_never(
        self, tmp_path: Path, asked: dict[str, object]
    ) -> None:
        rig = Rig(tmp_path, monitor__security_renotify_hours=0)
        try:
            watch = SecurityReleaseWatch()
            watch.check(rig.services)
            rig.now += 1000 * HOUR
            watch.check(rig.services)

            assert len(rig.sent) == 1
        finally:
            rig.close()

    def test_an_ordinary_release_says_nothing(self, rig: Rig, asked: dict[str, object]) -> None:
        asked["answer"] = version_check(security=None)

        SecurityReleaseWatch().check(rig.services)

        assert rig.sent == []

    def test_a_check_that_could_not_be_made_changes_nothing(
        self, rig: Rig, asked: dict[str, object]
    ) -> None:
        watch = SecurityReleaseWatch()
        watch.check(rig.services)
        for failure in (UpdateCheckInProgress("busy"), IntegrationError("offline"), OSError("x")):
            asked["answer"] = failure
            rig.now += 100 * HOUR
            watch.check(rig.services)

        assert len(rig.sent) == 1

    def test_the_memory_is_dropped_once_the_server_is_up_to_date(
        self, rig: Rig, asked: dict[str, object]
    ) -> None:
        watch = SecurityReleaseWatch()
        watch.check(rig.services)
        asked["answer"] = version_check(security=None, current="3.3.2")
        watch.check(rig.services)
        asked["answer"] = version_check(security="3.3.2")

        watch.check(rig.services)

        assert len(rig.sent) == 2

    def test_an_operator_who_switched_the_update_check_off_hears_nothing(
        self, rig: Rig, asked: dict[str, object]
    ) -> None:
        asked["enabled"] = False

        assert SecurityReleaseWatch().interval_s(rig.services) == 0

    def test_the_check_interval_is_the_operators(
        self, tmp_path: Path, asked: dict[str, object]
    ) -> None:
        rig = Rig(tmp_path, monitor__security_check_hours=2)
        try:
            assert SecurityReleaseWatch().interval_s(rig.services) == 2 * HOUR
        finally:
            rig.close()


def status(
    domain: str, installed: str, latest: str | None, *, revision: int = 1, ahead: bool = True
) -> RecipeUpdateStatus:
    return RecipeUpdateStatus(
        domain=domain,
        recipe=domain.split(".")[0],
        kind="compose",
        installed_version=installed,
        installed_revision=revision,
        latest_version=latest,
        latest_revision=revision,
        update_available=ahead,
    )


class TestRecipes:
    @pytest.fixture
    def behind(self, monkeypatch: pytest.MonkeyPatch) -> list[RecipeUpdateStatus]:
        rows = [status("vault.example.com", "1.37.4", "1.38.0")]
        monkeypatch.setattr(watch_recipes, "outdated", lambda: list(rows))
        return rows

    def test_recipes_behind_the_catalogue_are_announced(
        self, rig: Rig, behind: list[RecipeUpdateStatus]
    ) -> None:
        RecipeWatch().check(rig.services)

        [message] = rig.sent
        assert message.kind == "recipe_outdated"
        assert message.code == "recipe.outdated"
        assert message.state is State.INFO
        assert "1" in message.summary
        recipe_fact = next(fact for fact in message.facts if fact.key == "recipe")
        assert recipe_fact.value == "vault.example.com: 1.37.4 -> 1.38.0"

    def test_the_same_list_is_announced_once(
        self, rig: Rig, behind: list[RecipeUpdateStatus]
    ) -> None:
        watch = RecipeWatch()
        watch.check(rig.services)
        rig.now += 24 * HOUR
        watch.check(rig.services)

        assert len(rig.sent) == 1

    def test_a_week_later_it_is_said_again(
        self, rig: Rig, behind: list[RecipeUpdateStatus]
    ) -> None:
        watch = RecipeWatch()
        watch.check(rig.services)
        rig.now += 8 * 24 * HOUR
        watch.check(rig.services)

        assert len(rig.sent) == 2

    def test_a_change_of_the_list_is_said_at_once(
        self, rig: Rig, behind: list[RecipeUpdateStatus]
    ) -> None:
        watch = RecipeWatch()
        watch.check(rig.services)
        behind.append(status("blog.example.com", "5.0", "5.1"))
        rig.now += 24 * HOUR

        watch.check(rig.services)

        assert len(rig.sent) == 2
        assert "2" in rig.sent[-1].summary

    def test_nothing_behind_says_nothing_and_forgets(
        self, rig: Rig, behind: list[RecipeUpdateStatus]
    ) -> None:
        watch = RecipeWatch()
        watch.check(rig.services)
        behind.clear()
        watch.check(rig.services)
        behind.append(status("vault.example.com", "1.37.4", "1.38.0"))

        watch.check(rig.services)

        assert len(rig.sent) == 2

    def test_the_signature_ignores_order_and_follows_versions(self) -> None:
        a = status("a.example.com", "1", "2")
        b = status("b.example.com", "1", "2")

        assert signature([a, b]) == signature([b, a])
        assert signature([a]) != signature([status("a.example.com", "1", "3")])

    def test_the_check_can_be_turned_off(self, tmp_path: Path) -> None:
        rig = Rig(tmp_path, monitor__recipe_check_hours=0)
        try:
            assert RecipeWatch().interval_s(rig.services) == 0
        finally:
            rig.close()
