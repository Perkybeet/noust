# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The value of a control is the strictest of what asks for it.

What is pinned: with nothing chosen every control is the permissive default; with several
frameworks on each control takes the strictest value any asks (in every combination worth
naming); the operator may tighten further and never loosen below an enabled framework; and
``security.profile: ens-medium`` and its spellings keep meaning "ENS MEDIA is on", failing
strict as they always did.
"""

from __future__ import annotations

import itertools

import pytest

from noust.core.accounts.policy import build_policy
from noust.core.compliance import controls, frameworks, resolve
from tests.compliance_support import FakeConfig, frameworks_on


class TestNothingChosen:
    def test_every_control_is_the_permissive_default(self) -> None:
        config = FakeConfig()

        for control in controls.CONTROLS:
            found = resolve.resolve(control.id, config)
            assert found.value == control.standard, control.id
            assert not found.locked and found.forced_by == ()

    def test_no_framework_is_on(self) -> None:
        assert resolve.enabled_ids(FakeConfig()) == []

    def test_an_operator_may_set_a_limit_looser_than_the_default(self) -> None:
        config = frameworks_on(auth__session__idle_minutes=60)

        assert resolve.value("session_idle_minutes", config) == 60

    def test_a_flag_can_be_turned_on_by_itself(self) -> None:
        config = frameworks_on(security__change_reasons=True)

        assert resolve.is_on("change_reasons", config)
        assert resolve.resolve("change_reasons", config).forced_by == ()


class TestWhichFrameworksAreOn:
    def test_the_list_names_them_in_catalog_order(self) -> None:
        config = frameworks_on("iso-27001", "ens-media")

        assert resolve.enabled_ids(config) == ["ens-media", "iso-27001"]

    def test_text_typed_by_hand_is_read(self) -> None:
        config = FakeConfig({"security": {"frameworks": "nis2, gdpr-32  cis-ig1"}})

        assert resolve.enabled_ids(config) == ["nis2", "cis-ig1", "gdpr-32"]

    def test_an_unknown_name_is_ignored_and_reported(self) -> None:
        config = frameworks_on("iso-27001", "iso-27002")

        assert resolve.enabled_ids(config) == ["iso-27001"]
        assert resolve.unknown_names(config) == ["iso-27002"]

    @pytest.mark.parametrize(
        "spelling", ["ens-medium", "ens_medium", "ENS", "ens-media", "medium", "ens_typo", "wat"]
    )
    def test_the_profile_from_before_frameworks_means_ens_media(self, spelling: str) -> None:
        config = FakeConfig({"security": {"profile": spelling}})

        assert resolve.enabled_ids(config) == ["ens-media"]
        assert resolve.is_on("approvals", config)

    @pytest.mark.parametrize("spelling", [None, "", "standard", "Standard"])
    def test_standard_or_nothing_is_not_a_framework(self, spelling: str | None) -> None:
        config = FakeConfig({"security": {"profile": spelling}})

        assert resolve.enabled_ids(config) == []

    def test_the_old_setting_and_the_list_do_not_double_up(self) -> None:
        config = FakeConfig({"security": {"profile": "ens-medium", "frameworks": ["ens-media"]}})

        assert resolve.enabled_ids(config) == ["ens-media"]


class TestStrictestWins:
    def test_two_frameworks_take_the_stricter_number(self) -> None:
        # ISO and ENS MEDIA both ask 15 minutes; CIS IG1 also 15; GDPR 15: a single value.
        config = frameworks_on("cis-ig1", "ens-media")

        assert resolve.value("session_idle_minutes", config) == 15

    def test_a_framework_that_asks_more_beats_one_that_asks_less(self) -> None:
        # ENS BÁSICA asks 90-day access reviews; no framework asks fewer than 90.
        assert resolve.value("stale_account_days", frameworks_on("ens-basica")) == 90
        # CIS fixes 45 days for dormant accounts, stricter than ENS's 90.
        assert resolve.value("stale_account_days", frameworks_on("ens-basica", "cis-ig1")) == 45

    def test_flags_are_on_when_any_framework_asks(self) -> None:
        assert not resolve.is_on("approvals", frameworks_on("iso-27001"))
        assert resolve.is_on("approvals", frameworks_on("iso-27001", "soc2-security"))

    def test_choices_take_the_stricter_end(self) -> None:
        assert resolve.value("backup_encryption", frameworks_on("ens-basica")) == "optional"
        assert (
            resolve.value("backup_encryption", frameworks_on("ens-basica", "gdpr-32")) == "required"
        )

    @pytest.mark.parametrize(
        "combination", list(itertools.combinations(frameworks.FRAMEWORK_IDS, 2))
    )
    def test_every_pair_is_at_least_as_strict_as_each_of_its_members(
        self, combination: tuple[str, str]
    ) -> None:
        both = frameworks_on(*combination)
        for name in combination:
            alone = frameworks_on(name)
            for control in controls.CONTROLS:
                assert control.satisfies(
                    resolve.value(control.id, both), resolve.value(control.id, alone)
                ), (combination, control.id)

    def test_all_of_them_are_at_least_as_strict_as_the_strictest_member(self) -> None:
        everything = frameworks_on(*frameworks.FRAMEWORK_IDS)

        for control in controls.CONTROLS:
            asked = [
                required.value
                for _, required in resolve.requirements_of(control.id, frameworks.FRAMEWORK_IDS)
            ]
            if asked:
                assert resolve.value(control.id, everything) == control.strictest(*asked), (
                    control.id
                )

    def test_the_order_they_are_listed_in_changes_nothing(self) -> None:
        forward = frameworks_on("ens-media", "iso-27001", "nis2")
        backward = frameworks_on("nis2", "iso-27001", "ens-media")

        for control in controls.CONTROLS:
            assert resolve.value(control.id, forward) == resolve.value(control.id, backward)

    def test_what_forces_a_control_is_named_strictest_first(self) -> None:
        found = resolve.resolve("stale_account_days", frameworks_on("ens-basica", "cis-ig1"))

        assert found.forced_by[0] == "cis-ig1"
        assert set(found.forced_by) == {"cis-ig1", "ens-basica"}
        assert found.locked


class TestTightenNeverLoosen:
    def test_an_operator_can_be_stricter_than_the_framework(self) -> None:
        config = frameworks_on("ens-media", auth__session__idle_minutes=10)

        assert resolve.value("session_idle_minutes", config) == 10

    def test_an_operator_cannot_be_looser_than_the_framework(self) -> None:
        config = frameworks_on("ens-media", auth__session__idle_minutes=60)

        found = resolve.resolve("session_idle_minutes", config)
        assert found.value == 15
        assert found.operator == 60 and found.floor == 15

    def test_a_flag_set_off_stays_on_while_a_framework_asks(self) -> None:
        config = frameworks_on("ens-media", approval__enabled=False)

        assert resolve.is_on("approvals", config)

    def test_a_token_limit_is_only_a_limit_once_a_framework_asks(self) -> None:
        # auth.tokens.max_days is 90 in the file by default; on its own it limits nothing.
        alone = frameworks_on(auth__tokens__max_days=30)
        assert resolve.value("token_max_days", alone) is None
        with_framework = frameworks_on("ens-media", auth__tokens__max_days=30)
        assert resolve.value("token_max_days", with_framework) == 30
        looser = frameworks_on("ens-media", auth__tokens__max_days=365)
        assert resolve.value("token_max_days", looser) == 90

    def test_verification_can_be_switched_off_only_when_nothing_asks(self) -> None:
        off = frameworks_on(backup__verify_days=0)
        assert resolve.value("backup_verify_days", off) is None
        on = frameworks_on("ens-media", backup__verify_days=0)
        assert resolve.value("backup_verify_days", on) == 7

    def test_an_unreadable_setting_is_the_default(self) -> None:
        config = frameworks_on(auth__session__idle_minutes="soon")

        assert resolve.value("session_idle_minutes", config) == 30


class TestTheSignInPolicyFollowsTheFrameworks:
    def test_nothing_chosen_keeps_what_is_configured(self) -> None:
        policy = build_policy({"idle_minutes": 45, "absolute_hours": 24})

        assert (policy.idle_minutes, policy.absolute_hours) == (45, 24)
        assert policy.frameworks == () and policy.token_max_days is None

    def test_a_framework_caps_every_limit_it_asks(self) -> None:
        policy = build_policy(
            {"frameworks": ["iso-27001"], "idle_minutes": 60, "absolute_hours": 24}
        )

        assert (policy.idle_minutes, policy.absolute_hours) == (15, 8)
        assert policy.sudo_require_password is True
        assert policy.token_max_days == 90
        assert policy.password_min_length == 14

    def test_a_framework_that_asks_nothing_of_a_limit_leaves_it(self) -> None:
        # NIS2 asks nothing of how long a session may idle.
        policy = build_policy({"frameworks": ["nis2"], "idle_minutes": 60})

        assert policy.idle_minutes == 60
        assert policy.lockout_threshold == 5 and policy.password_min_length == 14

    def test_the_old_profile_name_is_still_a_framework(self) -> None:
        policy = build_policy({"profile": "ens-medium"})

        assert policy.frameworks == ("ens-media",) and policy.ens
        assert policy.profile == "ens-medium"

    def test_a_policy_built_by_hand_with_the_profile_name_has_its_framework(self) -> None:
        from noust.core.accounts.policy import AuthPolicy

        policy = AuthPolicy(profile="ens-medium")

        assert policy.frameworks == ("ens-media",)
        assert policy.requires("separation_of_duties")
        assert not AuthPolicy().requires("separation_of_duties")

    def test_the_policy_judges_by_its_own_frameworks(self) -> None:
        from noust.core.accounts.policy import AuthPolicy

        policy = AuthPolicy(frameworks=("iso-27001",))

        assert policy.requires("separation_of_duties")
        assert not policy.requires("approvals")

    def test_two_frameworks_make_the_stricter_policy(self) -> None:
        policy = build_policy({"frameworks": ["ens-basica", "cis-ig1"]})

        assert policy.idle_minutes == 15 and policy.profile == "ens-medium"


class TestTheLegacyShape:
    def test_a_second_framework_raises_the_floor_field_by_field(self) -> None:
        from noust.core.ens import profile

        floor = profile.active_defaults(frameworks_on("ens-basica", "cis-ig1"))

        assert floor.stale_account_days == 45
        assert floor.access_review_days == 90
        assert floor.name == "ens-medium"

    def test_without_an_ens_framework_the_name_stays_standard(self) -> None:
        from noust.core.ens import profile

        floor = profile.active_defaults(frameworks_on("iso-27001"))

        assert floor.name == "standard"
        assert floor.idle_minutes == 15
        assert not profile.is_ens(frameworks_on("iso-27001"))
        assert profile.is_ens(frameworks_on("ens-alta"))
        assert profile.current_profile(frameworks_on("ens-basica")) == "ens-medium"
