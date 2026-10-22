# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Choosing frameworks and controls: what a change would do, and doing it.

What is pinned: a change is planned before it is made and says which controls move and which
clause asks for each; turning something on never asks for more than the permission to change
it, and loosening is what the caller must confirm; a control cannot be set looser than an
enabled framework asks; what would leave nobody able to govern accounts is a warning that
must be acknowledged; and ``security.profile`` always agrees with the list.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.accounts import AccountManager, AuthPolicy, passwords
from noust.core.compliance import resolve
from noust.core.compliance.manager import ComplianceManager
from noust.core.config import Config
from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore

PASSWORD = "correct horse battery staple"


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


def reread(config: Config) -> list[str]:
    """What a fresh start of the console would find on disk."""
    Config.reset_instance()
    return resolve.enabled_ids(Config())


class TestThePicture:
    def test_nothing_is_on_by_default(self, manager: ComplianceManager) -> None:
        state = manager.describe()

        assert [f["id"] for f in state["frameworks"] if f["enabled"]] == []
        assert not any(c["locked"] for c in state["controls"])
        assert state["legacy_profile"] == "standard"

    def test_a_framework_that_is_off_says_what_enabling_it_would_change(
        self, manager: ComplianceManager
    ) -> None:
        state = manager.describe()

        iso = next(f for f in state["frameworks"] if f["id"] == "iso-27001")
        moved = {change["control"]: change for change in iso["changes"]}
        assert moved["separation_of_duties"]["clauses"] == ["A.5.3"]
        assert (
            moved["session_idle_minutes"]["before"] == 30
            and moved["session_idle_minutes"]["after"] == 15
        )

    def test_a_framework_that_is_on_changes_nothing_more(self, manager: ComplianceManager) -> None:
        manager.apply(manager.plan(enable=["iso-27001"]), acknowledge=True)

        iso = next(f for f in manager.describe()["frameworks"] if f["id"] == "iso-27001")

        assert iso["enabled"] and iso["changes"] == [] and iso["source"] == "setting"

    def test_the_old_profile_setting_shows_as_ens_media_by_profile(
        self, manager: ComplianceManager, real_config: Config
    ) -> None:
        real_config.set("security.profile", "ens-medium")

        ens = next(f for f in manager.describe()["frameworks"] if f["id"] == "ens-media")

        assert ens["enabled"] and ens["source"] == "profile"


class TestPlanning:
    def test_enabling_a_framework_lists_each_control_and_the_clause(
        self, manager: ComplianceManager
    ) -> None:
        plan = manager.plan(enable=["ens-media"])

        moved = {effect.control: effect for effect in plan.effects}
        assert moved["approvals"].before is False and moved["approvals"].after is True
        assert "op.exp.5.4" in moved["approvals"].clauses["ens-media"]
        assert plan.enabled_after == ("ens-media",)
        assert not plan.loosens

    def test_disabling_loosens_and_enabling_does_not(self, manager: ComplianceManager) -> None:
        manager.apply(manager.plan(enable=["iso-27001"]), acknowledge=True)

        assert manager.plan(disable=["iso-27001"]).loosens
        assert not manager.plan(enable=["nis2"]).loosens

    def test_asking_for_what_is_already_so_changes_nothing(
        self, manager: ComplianceManager
    ) -> None:
        manager.apply(manager.plan(enable=["iso-27001"]), acknowledge=True)

        plan = manager.plan(enable=["iso-27001"])

        assert plan.empty and plan.effects == ()

    def test_a_second_framework_shows_only_what_it_adds(self, manager: ComplianceManager) -> None:
        manager.apply(manager.plan(enable=["ens-media"]), acknowledge=True)

        plan = manager.plan(enable=["cis-ig1"])

        assert {e.control for e in plan.effects} == {"stale_account_days", "backup_offsite"}
        assert plan.effects[0].forced_by[0] == "cis-ig1"

    def test_a_control_set_stricter_is_not_a_loosening(self, manager: ComplianceManager) -> None:
        plan = manager.plan(settings={"session_idle_minutes": 10})

        assert plan.settings == {"session_idle_minutes": 10} and not plan.loosens
        # 45 minutes is looser than the default's 30: that is a loosening.
        assert manager.plan(settings={"session_idle_minutes": 45}).loosens is True

    def test_a_control_set_looser_than_a_framework_asks_is_refused(
        self, manager: ComplianceManager
    ) -> None:
        manager.apply(manager.plan(enable=["ens-media"]), acknowledge=True)

        with pytest.raises(ValidationError, match="ens-media"):
            manager.plan(settings={"session_idle_minutes": 60})
        with pytest.raises(ValidationError):
            manager.plan(settings={"approvals": False})
        assert manager.plan(settings={"session_idle_minutes": 5}).settings == {
            "session_idle_minutes": 5
        }

    def test_disabling_the_framework_in_the_same_request_allows_the_looser_value(
        self, manager: ComplianceManager
    ) -> None:
        manager.apply(manager.plan(enable=["ens-media"]), acknowledge=True)

        plan = manager.plan(disable=["ens-media"], settings={"session_idle_minutes": 60})

        assert plan.settings == {"session_idle_minutes": 60} and plan.loosens

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"enable": ["iso-27002"]}, "Unknown compliance framework"),
            ({"disable": ["nope"]}, "Unknown compliance framework"),
            ({"settings": {"nothing": True}}, "Unknown control"),
            ({"settings": {"session_idle_minutes": "soon"}}, "number"),
            ({"settings": {"backup_encryption": "sometimes"}}, "one of"),
            ({"settings": {"approvals": "perhaps"}}, "on or off"),
        ],
    )
    def test_nonsense_is_refused_and_nothing_is_written(
        self, manager: ComplianceManager, kwargs: dict[str, Any], message: str
    ) -> None:
        with pytest.raises(ValidationError, match=message):
            manager.plan(**kwargs)

    def test_a_framework_cannot_be_enabled_and_disabled_at_once(
        self, manager: ComplianceManager
    ) -> None:
        manager.apply(manager.plan(enable=["nis2"]), acknowledge=True)

        with pytest.raises(ValidationError, match="at once"):
            manager.plan(enable=["nis2"], disable=["nis2"])


class TestWarnings:
    def make(self, store: NoustStore, role: str) -> None:
        AccountManager(store, policy=AuthPolicy()).create("maria", role, password=PASSWORD)

    def test_splitting_the_roles_with_nobody_to_govern_is_a_warning(
        self, manager: ComplianceManager, store: NoustStore
    ) -> None:
        self.make(store, "admin")

        plan = manager.plan(enable=["ens-media"])

        assert [w.id for w in plan.warnings] == ["no_security_officer"]
        with pytest.raises(ValidationError, match="confirmation"):
            manager.apply(plan)
        manager.apply(plan, acknowledge=True)
        assert resolve.is_on("roles_split", manager.config)

    def test_a_security_officer_makes_it_no_warning(
        self, manager: ComplianceManager, store: NoustStore
    ) -> None:
        self.make(store, "admin")
        AccountManager(store, policy=AuthPolicy()).create("sam", "security", password=PASSWORD)

        assert manager.plan(enable=["ens-media"]).warnings == ()

    def test_separation_with_people_who_hold_two_roles_is_a_warning_with_a_count(
        self, manager: ComplianceManager, store: NoustStore
    ) -> None:
        accounts = AccountManager(store, policy=AuthPolicy())
        accounts.create("ana", "admin", password=PASSWORD, person_ref="p@example.com")
        accounts.create("ben", "security", password=PASSWORD, person_ref="p@example.com")

        plan = manager.plan(enable=["iso-27001"])

        warning = next(w for w in plan.warnings if w.id == "separation_conflicts")
        assert warning.count == 1

    def test_a_server_with_no_accounts_has_nobody_to_warn_about(
        self, manager: ComplianceManager
    ) -> None:
        assert manager.plan(enable=["ens-media"]).warnings == ()

    def test_loosening_never_warns(self, manager: ComplianceManager, store: NoustStore) -> None:
        manager.apply(manager.plan(enable=["nis2"]), acknowledge=True)
        self.make(store, "admin")

        assert manager.plan(disable=["nis2"]).warnings == ()


class TestApplying:
    def test_the_list_and_the_old_setting_agree(
        self, manager: ComplianceManager, real_config: Config
    ) -> None:
        manager.apply(manager.plan(enable=["ens-media", "iso-27001"]), acknowledge=True)

        assert real_config.get("security.frameworks") == ["ens-media", "iso-27001"]
        assert real_config.get("security.profile") == "ens-medium"

        manager.apply(manager.plan(disable=["ens-media"]), acknowledge=True)

        assert real_config.get("security.frameworks") == ["iso-27001"]
        assert real_config.get("security.profile") == "standard"

    def test_a_server_configured_with_the_old_setting_moves_onto_the_list(
        self, manager: ComplianceManager, real_config: Config
    ) -> None:
        real_config.set("security.profile", "ens-medium")

        manager.apply(manager.plan(enable=["nis2"]), acknowledge=True)

        assert real_config.get("security.frameworks") == ["ens-media", "nis2"]

    def test_disabling_a_framework_the_old_setting_enabled_turns_it_off(
        self, manager: ComplianceManager, real_config: Config
    ) -> None:
        real_config.set("security.profile", "ens-medium")

        manager.apply(manager.plan(disable=["ens-media"]), acknowledge=True)

        assert resolve.enabled_ids(real_config) == []

    def test_it_is_written_to_the_file(
        self, manager: ComplianceManager, real_config: Config
    ) -> None:
        manager.apply(
            manager.plan(enable=["gdpr-32"], settings={"change_reasons": True}), acknowledge=True
        )

        assert reread(real_config) == ["gdpr-32"]
        assert Config().get("security.change_reasons") is True

    def test_a_control_is_stored_the_way_its_area_reads_it(
        self, manager: ComplianceManager, real_config: Config
    ) -> None:
        manager.apply(
            manager.plan(
                settings={"audit_fail_closed": True, "backup_verify_days": None, "approvals": True}
            ),
            acknowledge=True,
        )

        assert real_config.get("audit.on_failure") == "refuse"
        assert real_config.get("backup.verify_days") == 0
        assert real_config.get("approval.enabled") is True

    def test_what_a_framework_forces_stays_when_the_setting_is_turned_off_again(
        self, manager: ComplianceManager, real_config: Config
    ) -> None:
        manager.apply(manager.plan(enable=["soc2-security"]), acknowledge=True)

        with pytest.raises(ValidationError):
            manager.plan(settings={"approvals": False})
        assert resolve.is_on("approvals", real_config)

    def test_nothing_is_written_when_the_plan_is_refused(
        self, manager: ComplianceManager, real_config: Config
    ) -> None:
        with pytest.raises(ValidationError):
            manager.plan(enable=["nis2"], settings={"unknown": 1})

        assert real_config.get("security.frameworks") == []
