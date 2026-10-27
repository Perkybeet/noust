# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A token limit the operator chose counts on its own, with or without a framework.

``auth.tokens.max_days`` is 90 in every file by default, so the number alone says nothing about
what the operator chose, and a limit that only counted under a framework left ``noust compliance
set token_max_days 90`` answering "No control changes value" on a server with none, and a limit
nobody could keep after taking a framework off. A choice is recorded beside the number
(``auth.tokens.enforce_max_days``) and from then on counts everywhere: that is what an operator who
sets a limit expects, and a number nobody chose (the default) still limits nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.accounts import passwords
from noust.core.accounts.policy import build_policy, load_policy
from noust.core.compliance import resolve
from noust.core.compliance.manager import ComplianceManager
from noust.core.config import Config
from noust.core.store import NoustStore


@pytest.fixture(autouse=True)
def cheap_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def manager(real_config: Config, store: NoustStore) -> ComplianceManager:
    return ComplianceManager(real_config, store)


class TestWhatTheManagerDoes:
    def test_a_server_that_chose_nothing_has_no_limit_whatever_the_file_says(
        self, manager: ComplianceManager, real_config: Config
    ) -> None:
        real_config.set("auth.tokens.max_days", 30)

        found = resolve.resolve("token_max_days", real_config)

        assert found.value is None and found.chosen is False and not found.own_counts

    def test_setting_it_with_no_framework_limits_tokens_and_says_so(
        self, manager: ComplianceManager, real_config: Config
    ) -> None:
        plan = manager.plan(settings={"token_max_days": 90})

        [effect] = plan.effects
        assert (effect.control, effect.before, effect.after) == ("token_max_days", None, 90)
        assert plan.loosens is False

        manager.apply(plan, acknowledge=True)

        assert real_config.get("auth.tokens.max_days") == 90
        assert real_config.get("auth.tokens.enforce_max_days") is True
        found = resolve.resolve("token_max_days", real_config)
        assert found.value == 90 and found.chosen and found.own_counts and not found.locked

    def test_a_number_the_file_already_holds_is_still_recorded_as_a_choice(
        self, manager: ComplianceManager, real_config: Config
    ) -> None:
        """90 is the default, and asking for 90 is not "nothing changes"."""
        assert real_config.get("auth.tokens.max_days") == 90

        plan = manager.plan(settings={"token_max_days": 90})

        assert plan.settings == {"token_max_days": 90} and not plan.empty

    def test_it_is_kept_when_a_framework_is_taken_off(
        self, manager: ComplianceManager, real_config: Config
    ) -> None:
        manager.apply(manager.plan(enable=["ens-media"]), acknowledge=True)
        plan = manager.plan(settings={"token_max_days": 60})

        assert plan.effects[0].after == 60
        manager.apply(plan, acknowledge=True)
        manager.apply(manager.plan(disable=["ens-media"]), acknowledge=True)

        assert resolve.value("token_max_days", real_config) == 60

    def test_asking_for_what_a_framework_forces_while_it_is_on_records_the_choice(
        self, manager: ComplianceManager, real_config: Config
    ) -> None:
        """No value in force changes now (the framework asks 90), but the choice outlives it."""
        manager.apply(manager.plan(enable=["ens-media"]), acknowledge=True)

        plan = manager.plan(settings={"token_max_days": 90})
        manager.apply(plan, acknowledge=True)

        assert plan.effects == () and plan.settings == {"token_max_days": 90}
        manager.apply(manager.plan(disable=["ens-media"]), acknowledge=True)
        assert resolve.value("token_max_days", real_config) == 90

    def test_none_gives_the_choice_up(
        self, manager: ComplianceManager, real_config: Config
    ) -> None:
        manager.apply(manager.plan(settings={"token_max_days": 45}), acknowledge=True)

        plan = manager.plan(settings={"token_max_days": None})
        manager.apply(plan, acknowledge=True)

        assert plan.loosens is True
        assert real_config.get("auth.tokens.enforce_max_days") is False
        assert resolve.value("token_max_days", real_config) is None

    def test_a_framework_that_is_on_still_wins_over_a_looser_choice(
        self, manager: ComplianceManager, real_config: Config
    ) -> None:
        manager.apply(manager.plan(settings={"token_max_days": 365}), acknowledge=True)
        manager.apply(manager.plan(enable=["ens-media"]), acknowledge=True)

        assert resolve.value("token_max_days", real_config) == 90

    def test_the_policy_that_makes_a_token_follows_it(
        self, manager: ComplianceManager, real_config: Config
    ) -> None:
        assert load_policy().token_max_days is None

        manager.apply(manager.plan(settings={"token_max_days": 30}), acknowledge=True)

        assert load_policy().token_max_days == 30

    def test_the_description_says_whether_the_number_is_a_choice(
        self, manager: ComplianceManager, real_config: Config
    ) -> None:
        before = {item["id"]: item for item in manager.describe()["controls"]}
        assert before["token_max_days"]["own_counts"] is False
        assert before["session_idle_minutes"]["own_counts"] is True

        manager.apply(manager.plan(settings={"token_max_days": 30}), acknowledge=True)

        after = {item["id"]: item for item in manager.describe()["controls"]}
        assert after["token_max_days"]["own_counts"] is True


class TestWhatThePolicyDoes:
    def test_a_chosen_limit_applies_without_a_framework(self) -> None:
        policy = build_policy({"token_max_days": 30, "token_max_days_enforced": True})

        assert policy.token_max_days == 30

    def test_the_number_alone_is_not_a_choice(self) -> None:
        # 90 is in every file by default.
        assert build_policy({"token_max_days": 90}).token_max_days is None
        off = build_policy({"token_max_days": 90, "token_max_days_enforced": False})
        assert off.token_max_days is None

    def test_a_framework_still_caps_a_looser_choice(self) -> None:
        policy = build_policy(
            {"frameworks": ["ens-media"], "token_max_days": 365, "token_max_days_enforced": True}
        )

        assert policy.token_max_days == 90

    def test_a_stricter_choice_wins_over_a_framework(self) -> None:
        policy = build_policy(
            {"frameworks": ["ens-media"], "token_max_days": 30, "token_max_days_enforced": True}
        )

        assert policy.token_max_days == 30

    def test_a_framework_that_asks_nothing_of_tokens_leaves_the_choice(self) -> None:
        # CIS asks nothing of a token's life.
        policy = build_policy(
            {"frameworks": ["cis-ig1"], "token_max_days": 45, "token_max_days_enforced": "yes"}
        )

        assert policy.token_max_days == 45
