# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``ens-medium`` security profile is stated in one module, and every area reads it there.

What is pinned: the profile's numbers are the spec's (§12.7), a typo in the
profile's name fails strict, and the sign-in policy, the CLI's ``--reason``
rule, the approvals and the audit retention all take their values from
:mod:`noust.core.ens.profile` rather than from copies of their own.
"""

from __future__ import annotations

from typing import Any

import pytest

from noust.core.ens import profile
from noust.core.ens.profile import (
    ENS_MEDIUM,
    PROFILE_ENS_MEDIUM,
    PROFILE_STANDARD,
    STANDARD,
    baseline,
    defaults_for,
    normalise_profile,
)


class FakeConfig:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    def get(self, key: str, default: Any = None) -> Any:
        node: Any = self.values
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


class TestTheProfilesNumbers:
    def test_the_ens_values_are_the_spec_decisions(self) -> None:
        assert ENS_MEDIUM.idle_minutes == 15
        assert ENS_MEDIUM.absolute_hours == 8
        assert (ENS_MEDIUM.lockout_threshold, ENS_MEDIUM.lockout_minutes) == (5, 15)
        assert ENS_MEDIUM.password_min_length == 14
        assert ENS_MEDIUM.token_max_days == 90
        assert ENS_MEDIUM.audit_retention_days == 365
        assert ENS_MEDIUM.approvals is True
        assert ENS_MEDIUM.cli_reason_required is True
        assert ENS_MEDIUM.master_token == "recovery"
        assert ENS_MEDIUM.backup_encryption == "required"
        assert ENS_MEDIUM.tls_certificate == "operator"

    def test_outside_the_profile_nothing_is_forced(self) -> None:
        assert STANDARD.idle_minutes == 30
        assert STANDARD.absolute_hours == 12
        assert STANDARD.token_max_days is None
        assert STANDARD.approvals is False
        assert STANDARD.cli_reason_required is False
        assert STANDARD.backup_encryption == "optional"

    def test_defaults_for_names_the_profile(self) -> None:
        assert defaults_for("ens-medium") is ENS_MEDIUM
        assert defaults_for("standard") is STANDARD
        assert defaults_for(None) is STANDARD


class TestTheName:
    @pytest.mark.parametrize("spelling", ["ens-medium", "ENS_MEDIUM", "ens", " Medium "])
    def test_every_spelling_of_the_profile_is_the_profile(self, spelling: str) -> None:
        assert normalise_profile(spelling) == PROFILE_ENS_MEDIUM

    @pytest.mark.parametrize("value", [None, "", "standard", "STANDARD"])
    def test_empty_or_standard_is_standard(self, value: Any) -> None:
        assert normalise_profile(value) == PROFILE_STANDARD

    def test_a_typo_fails_strict(self) -> None:
        assert normalise_profile("ens-meduim") == PROFILE_ENS_MEDIUM

    def test_the_current_profile_is_read_from_the_configuration(self) -> None:
        config = FakeConfig({"security": {"profile": "ens-medium"}})

        assert profile.current_profile(config) == PROFILE_ENS_MEDIUM
        assert profile.is_ens(config) is True
        assert profile.active_defaults(config) is ENS_MEDIUM
        assert profile.is_ens(FakeConfig({})) is False


class TestTheBaseline:
    def test_every_entry_names_a_measure_and_both_values(self) -> None:
        items = baseline()

        assert items, "the profile states what it fixes"
        for item in items:
            assert item.measures, item.key
            assert item.ens_value != "" and item.description
        keys = {item.key for item in items}
        assert {"auth.session.idle_minutes", "backup.encryption", "approval.enabled"} <= keys


class TestEveryAreaReadsTheOneModule:
    def test_the_sign_in_policy_caps_at_the_profile(self) -> None:
        from noust.core.accounts import policy

        built = policy.build_policy({"profile": "ens-medium", "idle_minutes": 60})

        assert built.idle_minutes == ENS_MEDIUM.idle_minutes
        assert built.absolute_hours == ENS_MEDIUM.absolute_hours
        assert built.token_max_days == ENS_MEDIUM.token_max_days
        assert policy.normalise_profile is normalise_profile
        assert policy.ENS_PASSWORD_MIN_LENGTH == ENS_MEDIUM.password_min_length

    def test_the_cli_asks_for_a_reason_by_the_same_name(self, monkeypatch) -> None:
        from noust.cli import audit_policy

        monkeypatch.setattr(
            profile, "current_profile", lambda config=None: PROFILE_ENS_MEDIUM, raising=True
        )

        assert audit_policy.security_profile() == PROFILE_ENS_MEDIUM
        assert audit_policy.ENS_PROFILE == PROFILE_ENS_MEDIUM

    def test_the_cli_reads_an_alternative_spelling_as_the_profile(self, monkeypatch) -> None:
        from noust.cli import audit_policy
        from noust.core import config as config_module

        monkeypatch.setattr(
            config_module.Config,
            "get",
            lambda self, key, default=None: "ENS_MEDIUM" if key == "security.profile" else default,
        )

        assert audit_policy.security_profile() == PROFILE_ENS_MEDIUM

    def test_the_audit_retention_is_raised_to_the_profiles_floor(self) -> None:
        from noust.core.audit.settings import load_settings

        config = FakeConfig(
            {"security": {"profile": "ens-medium"}, "audit": {"retention_days": 120}}
        )

        settings = load_settings(config)

        assert settings.retention_days == ENS_MEDIUM.audit_retention_days
        assert any("ens-medium" in problem for problem in settings.problems)

    def test_outside_the_profile_the_retention_is_the_operators(self) -> None:
        from noust.core.audit.settings import load_settings

        settings = load_settings(FakeConfig({"audit": {"retention_days": 120}}))

        assert settings.retention_days == 120
