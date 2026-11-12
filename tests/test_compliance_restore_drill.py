# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The frameworks that ask for restores that were tried now say so, with the control that holds it (item 174).

``backup_verify_days`` proves that an archive reads. ENS MEDIA (mp.info.6.r1), ISO/IEC 27001 (A.8.13),
NIS2 (article 21(2)(c)) and the GDPR (article 32(1)(c) and (d)) ask for more: that a backup can be
restored. ``restore_drill_days`` is how often a rehearsal of that is held (``backup.drill_days``,
30 by default, 0 turns it off where nothing asks), mapped to those clauses and to no other: each one
is a clause ``docs/COMPLIANCE.md`` already cites for the backup controls.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from noust.core.compliance import check, controls, frameworks, resolve
from noust.core.ens import profile
from noust.core.ens.checks import Facts
from tests.support.ens_checks import NOW, good_facts

#: Which clause of which framework asks for a restore that was tried.
ASKING = {
    "ens-media": ("mp.info.6.r1.1",),
    "ens-alta": ("mp.info.6.r1.1",),
    "iso-27001": ("A.8.13",),
    "nis2": ("Art. 21(2)(c)",),
    "gdpr-32": ("Art. 32(1)(c)", "Art. 32(1)(d)"),
}


class TestTheControl:
    def test_it_is_how_often_a_rehearsal_is_held_and_zero_turns_it_off(self) -> None:
        control = controls.get("restore_drill_days")

        assert control.kind == controls.KIND_MIN
        assert control.standard == 30
        assert control.key == "backup.drill_days"
        assert control.zero_is_off is True
        assert control.group == controls.GROUP_BACKUP
        assert control.unit == "days"
        assert controls.describe("restore_drill_days")

    def test_a_smaller_number_is_stricter(self) -> None:
        control = controls.get("restore_drill_days")

        assert control.stricter(30, 7) == 7
        assert control.stricter(30, None) == 30, "no rehearsal at all is the weakest"


class TestWhoAsksForIt:
    @pytest.mark.parametrize(("name", "clauses"), list(ASKING.items()))
    def test_each_asks_for_a_month_under_the_clauses_that_name_a_restore_that_was_tried(
        self, name: str, clauses: tuple[str, ...]
    ) -> None:
        requirement = frameworks.get(name).all_requirements()["restore_drill_days"]

        assert requirement.value == 30
        assert tuple(clause.id for clause in requirement.clauses) == clauses

    @pytest.mark.parametrize("name", ["ens-basica", "cis-ig1", "cis-ig2", "soc2-security"])
    def test_the_others_do_not(self, name: str) -> None:
        """BÁSICA has no reinforcement r1; CIS and SOC 2 are not asked for a restore by these clauses."""
        assert "restore_drill_days" not in frameworks.get(name).all_requirements()

    def test_the_number_is_marked_as_noust_s_where_the_framework_leaves_it_to_the_organisation(
        self,
    ) -> None:
        for name in ASKING:
            requirement = frameworks.get(name).all_requirements()["restore_drill_days"]
            assert requirement.prescribed is False
            assert "Noust's" in requirement.note

    def test_the_table_is_still_sound(self) -> None:
        assert frameworks.check_table() == []


class TestWhatIsInForce:
    @pytest.fixture
    def config(self) -> Any:
        class Settings:
            def __init__(self) -> None:
                self.values: dict[str, Any] = {}

            def get(self, key: str, default: Any = None) -> Any:
                return self.values.get(key, default)

        return Settings()

    def test_by_default_a_rehearsal_is_held_every_30_days(self, config: Any) -> None:
        assert resolve.value("restore_drill_days", config) == 30

    def test_an_operator_may_turn_it_off_where_nothing_asks(self, config: Any) -> None:
        config.values["backup.drill_days"] = 0

        assert resolve.value("restore_drill_days", config) is None

    def test_an_operator_may_hold_it_more_often(self, config: Any) -> None:
        config.values["backup.drill_days"] = 7

        assert resolve.value("restore_drill_days", config) == 7

    def test_an_ens_server_cannot_turn_it_off_or_hold_it_less_often(self, config: Any) -> None:
        config.values["security.frameworks"] = ["ens-media"]
        config.values["backup.drill_days"] = 0
        assert resolve.value("restore_drill_days", config) == 30

        config.values["backup.drill_days"] = 90
        assert resolve.value("restore_drill_days", config) == 30

        config.values["backup.drill_days"] = 14
        assert resolve.value("restore_drill_days", config) == 14

    def test_the_drill_the_scheduler_runs_follows_it(self, config: Any) -> None:
        from noust.managers.restore_drill import drill_days

        assert drill_days(config) == 30
        config.values["backup.drill_days"] = 0
        assert drill_days(config) == 0
        config.values["security.frameworks"] = ["nis2"]
        assert drill_days(config) == 30
        config.values["backup.drill_days"] = "not a number"
        assert drill_days(config) == 30


class TestTheProfileAgrees:
    def test_the_ens_profile_is_the_standard_one_in_this_number(self) -> None:
        assert profile.ENS_MEDIUM.restore_drill_days == 30
        assert profile.STANDARD.restore_drill_days == 30

    def test_ens_media_is_still_exactly_the_old_profile(self) -> None:
        assert profile.defaults_from_floors(["ens-media"]) == profile.ENS_MEDIUM
        assert profile.defaults_from_floors([]) == profile.STANDARD

    def test_a_stricter_framework_raises_the_profile_to_it(self) -> None:
        floors = profile.defaults_from_floors(["ens-media"])

        assert floors.restore_drill_days == 30


class TestTheGenericCheck:
    """``noust compliance check`` reads the same facts the ENS check does."""

    def facts(self, *drills: dict[str, Any]) -> Facts:
        facts = good_facts()
        facts.restore_drills = list(drills)
        return facts

    def drill(self, days_ago: float, *, domain: str = "shop.example.com") -> dict[str, Any]:
        finished = (NOW - timedelta(days=days_ago)).isoformat()
        return {
            "domain": domain,
            "last_ok_at": finished,
            "duration_s": 252.0,
            "databases": 2,
            "backup_id": "x",
            "last_at": finished,
            "last_ok": True,
            "last_summary": "",
        }

    def measure(
        self, facts: Facts, required: Any = 30
    ) -> tuple[str, str, tuple[str, ...], str] | None:
        return check.MEASURES["restore_drill_days"](facts, required)

    def test_a_restore_tried_in_time_passes(self) -> None:
        status, summary, evidence, _ = self.measure(self.facts(self.drill(3))) or ("", "", (), "")

        assert status == "ok"
        assert "30 days" in summary

    def test_one_not_tried_in_time_warns_and_names_the_command(self) -> None:
        status, summary, evidence, remediation = self.measure(self.facts(self.drill(45))) or (
            "",
            "",
            (),
            "",
        )

        assert status == "warning"
        assert "1 application(s)" in summary
        assert "noust backup drill" in remediation

    def test_facts_that_could_not_be_read_are_no_answer(self) -> None:
        facts = good_facts()
        facts.restore_drills = None

        assert self.measure(facts) is None

    def test_a_server_with_no_application_is_not_judged(self) -> None:
        facts = good_facts()
        facts.backups = []
        facts.restore_drills = []

        status, *_ = self.measure(facts) or ("",)

        assert status == "n/a"
