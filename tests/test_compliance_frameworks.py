# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The mapping table: which control each framework turns on, and the clause it serves.

What is pinned: the table is sound (no row without a clause, no value a control does not
accept), ENS MEDIA is exactly what ``ens-medium`` always was, the ENS levels nest, CIS IG2
contains IG1, and every framework says what stays organisational. A preset that turned on
a control without a clause would be a claim the code cannot back.
"""

from __future__ import annotations

import pytest

from noust.core.compliance import controls, frameworks
from noust.core.ens import profile

#: What ens-medium fixed in 3.1 (spec section 12.7), written out here on purpose: the preset
#: that replaced it must be exactly these values, whatever happens to the table.
OLD_ENS_MEDIUM = {
    "idle_minutes": 15,
    "absolute_hours": 8,
    "lockout_threshold": 5,
    "lockout_minutes": 15,
    "password_min_length": 14,
    "token_max_days": 90,
    "audit_retention_days": 365,
    "approvals": True,
    "cli_reason_required": True,
    "master_token": "recovery",
    "backup_encryption": "required",
    "backup_verify_days": 7,
    "backup_max_age_hours": 26,
    "tls_certificate": "operator",
    "certificate_min_days": 30,
    "external_audit_sink": True,
    "explicit_allowlist": True,
    "access_review_days": 90,
    "audit_review_days": 7,
    "stale_account_days": 90,
    "database_remote_listen": False,
    "console_exposure": False,
    "sudo_idle_minutes": 10,
    "sudo_max_minutes": 30,
    "sudo_require_password": True,
    "terminal_idle_minutes": 10,
    "terminal_max_minutes": 60,
    "server_terminal": False,
    "audit_fail_closed": True,
}


class TestTheTableIsSound:
    def test_it_has_no_problem(self) -> None:
        assert frameworks.check_table() == []

    def test_there_are_the_frameworks_that_were_asked_for(self) -> None:
        assert set(frameworks.FRAMEWORK_IDS) == {
            "ens-basica",
            "ens-media",
            "ens-alta",
            "iso-27001",
            "nis2",
            "cis-ig1",
            "cis-ig2",
            "soc2-security",
            "gdpr-32",
        }

    @pytest.mark.parametrize("name", frameworks.FRAMEWORK_IDS)
    def test_every_row_names_a_clause_and_a_control_that_exists(self, name: str) -> None:
        framework = frameworks.get(name)
        for control_id, required in framework.all_requirements().items():
            assert controls.exists(control_id), (name, control_id)
            assert required.clauses, f"{name} turns on {control_id} without a clause"
            for clause in required.clauses:
                assert clause.id and clause.title, (name, control_id)

    @pytest.mark.parametrize("name", frameworks.FRAMEWORK_IDS)
    def test_every_framework_says_what_stays_organisational(self, name: str) -> None:
        framework = frameworks.get(name)

        assert len(framework.organisational) >= 3
        assert framework.summary and framework.reference

    @pytest.mark.parametrize("name", frameworks.FRAMEWORK_IDS)
    def test_every_value_is_one_its_control_accepts(self, name: str) -> None:
        for control_id, required in frameworks.get(name).all_requirements().items():
            control = controls.get(control_id)
            assert control.coerce(required.value) == required.value or required.value is None

    def test_every_control_is_asked_by_some_framework_or_is_a_plain_limit(self) -> None:
        asked = {
            control_id
            for framework in frameworks.FRAMEWORKS
            for control_id in framework.all_requirements()
        }
        # A control nobody asks for would be dead weight in the console.
        assert {control.id for control in controls.CONTROLS} - asked == set()

    def test_a_preset_never_asks_for_something_weaker_than_the_default_unless_it_says_so(
        self,
    ) -> None:
        for framework in frameworks.FRAMEWORKS:
            for control_id, required in framework.all_requirements().items():
                control = controls.get(control_id)
                if control.is_stricter_than_standard(required.value):
                    continue
                # Equal to the default is a floor; weaker would be a preset that loosens.
                assert control.rank(required.value) == control.rank(control.standard), (
                    framework.id,
                    control_id,
                )


class TestEnsMediaIsTheOldProfile:
    def test_its_floor_is_exactly_ens_medium(self) -> None:
        assert profile.defaults_from_floors(["ens-media"]) == profile.ENS_MEDIUM

    def test_every_value_ens_medium_fixed_is_still_fixed_to_the_same_number(self) -> None:
        floor = profile.defaults_from_floors(["ens-media"])

        for field, expected in OLD_ENS_MEDIUM.items():
            assert getattr(floor, field) == expected, field

    def test_nothing_chosen_is_the_standard_profile(self) -> None:
        assert profile.defaults_from_floors([]) == profile.STANDARD


class TestTheEnsLevelsNest:
    def test_a_lower_category_asks_nothing_a_higher_one_does_not(self) -> None:
        basica = frameworks.get("ens-basica").all_requirements()
        media = frameworks.get("ens-media").all_requirements()
        alta = frameworks.get("ens-alta").all_requirements()

        assert set(basica) <= set(media) <= set(alta)
        for control_id, required in basica.items():
            control = controls.get(control_id)
            assert control.satisfies(media[control_id].value, required.value), control_id
        for control_id, required in media.items():
            control = controls.get(control_id)
            assert control.satisfies(alta[control_id].value, required.value), control_id

    def test_basica_asks_only_what_the_lowest_category_applies(self) -> None:
        basica = frameworks.get("ens-basica").all_requirements()

        # op.acc.3, mp.eq.2, mp.si.2 and op.exp.5 do not apply to BÁSICA (RD 311/2022, annex II).
        for control_id in (
            "roles_split",
            "separation_of_duties",
            "approvals",
            "change_reasons",
            "session_idle_minutes",
            "backup_encryption",
        ):
            assert control_id not in basica, control_id
        # op.acc.6.8 (attempts limited) applies at every level.
        assert basica["lockout_threshold"].clauses[0].id == "op.acc.6.8"

    def test_alta_adds_a_backup_kept_apart(self) -> None:
        alta = frameworks.get("ens-alta").all_requirements()
        media = frameworks.get("ens-media").all_requirements()

        assert alta["backup_offsite"].clauses[0].id == "mp.info.6.r2.1"
        assert "backup_offsite" not in media

    def test_the_clauses_of_ens_are_measures_of_the_annex(self) -> None:
        for name in ("ens-basica", "ens-media", "ens-alta"):
            for required in frameworks.get(name).all_requirements().values():
                for clause in required.clauses:
                    assert clause.id.startswith(("op.", "mp.", "org.")), clause.id


class TestCisContainsItself:
    def test_ig2_asks_everything_ig1_does_and_more(self) -> None:
        ig1 = frameworks.get("cis-ig1").all_requirements()
        ig2 = frameworks.get("cis-ig2").all_requirements()

        assert set(ig1) < set(ig2)
        assert frameworks.get("cis-ig2").includes == "cis-ig1"

    def test_the_numbers_cis_fixes_are_marked_as_the_frameworks(self) -> None:
        ig2 = frameworks.get("cis-ig2").all_requirements()

        assert ig2["session_idle_minutes"].value == 15 and ig2["session_idle_minutes"].prescribed
        assert ig2["stale_account_days"].value == 45 and ig2["stale_account_days"].prescribed
        assert ig2["audit_review_days"].prescribed


class TestWhatTheyAreNot:
    @pytest.mark.parametrize("name", frameworks.FRAMEWORK_IDS)
    def test_no_text_claims_the_server_is_compliant(self, name: str) -> None:
        framework = frameworks.get(name)
        texts = [framework.summary, *framework.organisational, *framework.not_covered]
        for required in framework.all_requirements().values():
            texts.append(required.note)
            texts += [clause.title for clause in required.clauses]

        for text in texts:
            lowered = text.lower()
            assert "is compliant" not in lowered and "are compliant" not in lowered, text
            assert "guarantee" not in lowered, text

    def test_soc2_leaves_backups_to_availability(self) -> None:
        soc2 = frameworks.get("soc2-security").all_requirements()

        assert not {"backup_verify_days", "backup_offsite", "backup_max_age_hours"} & set(soc2)
        assert any(
            "Availability" in text for text in frameworks.get("soc2-security").organisational
        )
