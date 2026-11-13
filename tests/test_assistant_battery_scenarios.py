# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The scenarios of the battery of the built-in assistant are well formed, and there are enough of them (item 171).

A scenario is a file read by a closed schema: a key that is not in it, a family that does not exist or a marker
that stands for nothing is an error that names the file, because a scenario whose oracle was misspelt would pass
without checking anything. And the battery is the gate of what is published, so what it covers is pinned here: a
scenario deleted by mistake is a test that fails, not a hole nobody sees.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from tests.support.assistant_battery.scenario import (
    CARRIERS,
    FAMILIES,
    FORMS,
    SCENARIOS_DIR,
    Scenario,
    ScenarioError,
    all_scenarios,
    expand,
    load,
    load_family,
)

#: What the project's pre-push check looks for in what is tracked.
#: Written backwards so that this file does not hold them itself, as the check on the customers' names does.
_VETOED_BACKWARDS = ("edualc", "cipohtrna", "derohtua-oc", "tnega", "eertkrow")
VETOED = re.compile("|".join(word[::-1] for word in _VETOED_BACKWARDS), re.IGNORECASE)

GOOD = """\
id: made-up
family: explain
forms: [explain]
title: A scenario that is well formed
ask:
  message: Why?
script:
  - say: Because.
expect:
  state: done
"""


def written(tmp_path: Path, text: str, *, family: str = "explain", name: str = "made-up") -> Path:
    folder = tmp_path / family
    folder.mkdir(exist_ok=True)
    path = folder / f"{name}.yaml"
    path.write_text(text)
    return path


class TestTheSchemaIsClosed:
    def test_a_well_formed_scenario_is_read(self, tmp_path: Path) -> None:
        scenario = load(written(tmp_path, GOOD))

        assert (scenario.id, scenario.family, scenario.forms) == (
            "made-up",
            "explain",
            ("explain",),
        )
        assert scenario.calls_tools is True

    @pytest.mark.parametrize(
        ("change", "mentions"),
        [
            (("title: A scenario", "colour: red\ntitle: A scenario"), "unknown key"),
            (("forms: [explain]", "forms: [explain, tell]"), "forms"),
            (("forms: [explain]", "forms: []"), "forms"),
            (("family: explain", "family: dream"), "family"),
            (("id: made-up", "id: another"), "not the file's name"),
            (("expect:\n  state: done", "expect:\n  stat: done"), "unknown key"),
            (("  - say: Because.", "  - think: Because."), "unknown key"),
            (
                ("  - say: Because.", "  - say: Because.\n    call: {tool: a, arguments: {}}"),
                "says",
            ),
            (("ask:\n  message: Why?", "ask:\n  question: Why?"), "unknown key"),
            (("  state: done", "  state: done\n  trace: x\nseed:\n  moon: full"), "unknown key"),
            (("  - say: Because.", "  - say: <<canary:nothing>>"), "no canary"),
            (("  - say: Because.", "  - say: <<place:here>>"), "unknown marker"),
            (("  - say: Because.", "  - say: <<deployment:first>>"), "takes a number"),
        ],
    )
    def test_what_is_not_in_the_schema_is_an_error_that_names_the_file(
        self, tmp_path: Path, change: tuple[str, str], mentions: str
    ) -> None:
        text = GOOD.replace(*change)
        # `seed` has to be a top-level key, not under `expect`.
        if "seed:" in change[1]:
            text = (
                GOOD.replace("expect:\n  state: done\n", "")
                + "seed:\n  moon: full\nexpect:\n  state: done\n"
            )

        with pytest.raises(ScenarioError, match=mentions) as caught:
            load(written(tmp_path, text))
        assert "made-up.yaml" in str(caught.value)

    def test_a_scenario_in_the_folder_of_another_family_is_an_error(self, tmp_path: Path) -> None:
        with pytest.raises(ScenarioError, match="folder"):
            load(written(tmp_path, GOOD, family="propose"))

    def test_an_injection_scenario_says_what_the_text_is_and_what_obeying_it_comes_to(
        self, tmp_path: Path
    ) -> None:
        text = GOOD.replace("family: explain", "family: injection")

        with pytest.raises(ScenarioError, match="instruction"):
            load(written(tmp_path, text, family="injection"))

    def test_a_carrier_that_does_not_exist_is_an_error(self, tmp_path: Path) -> None:
        text = (
            GOOD.replace("family: explain", "family: injection")
            + "instruction: x\nobey:\n  - say: y\ncarriers: [carrier-pigeon]\n"
        )

        with pytest.raises(ScenarioError, match="carriers"):
            load(written(tmp_path, text, family="injection"))

    def test_a_policy_cell_has_four_parts(self, tmp_path: Path) -> None:
        text = GOOD + "policy:\n  - [assistant, production, apps.operate]\n"

        with pytest.raises(ScenarioError, match="policy"):
            load(written(tmp_path, text))

    def test_text_that_is_not_yaml_is_an_error_that_names_the_file(self, tmp_path: Path) -> None:
        with pytest.raises(ScenarioError, match=r"made-up\.yaml"):
            load(written(tmp_path, "id: [unclosed\n"))


class TestTheMarkers:
    def test_a_canary_is_put_where_its_marker_is(self) -> None:
        text = expand("password <<canary:env_password>> end", deployments=[])

        assert text.startswith("password ") and text.endswith(" end") and "<<" not in text

    def test_a_marker_that_is_the_whole_value_becomes_the_number(self) -> None:
        assert expand({"id": "<<deployment:2>>"}, deployments=[7, 9]) == {"id": 9}

    def test_a_marker_inside_a_text_becomes_its_digits(self) -> None:
        assert (
            expand("deployment <<deployment:1>> failed", deployments=[7]) == "deployment 7 failed"
        )

    def test_it_goes_into_lists_and_mappings(self) -> None:
        found = expand({"a": ["<<deployment:1>>", {"b": "x"}]}, deployments=[3])

        assert found == {"a": [3, {"b": "x"}]}


class TestWhatTheBatteryCovers:
    def test_every_file_is_a_scenario_of_the_family_of_its_folder(self) -> None:
        found = {p.parent.name for p in SCENARIOS_DIR.rglob("*.yaml")}

        assert found == set(FAMILIES)

    def test_the_names_are_unique_and_are_the_files(self) -> None:
        names = [s.id for s in all_scenarios()]

        assert len(names) == len(set(names))
        assert names == [
            p.stem for family in FAMILIES for p in sorted((SCENARIOS_DIR / family).glob("*.yaml"))
        ]

    def test_what_it_explains_investigates_proposes_and_does_with_a_fleet_is_between_thirty_and_forty(
        self,
    ) -> None:
        core = sum(len(load_family(f)) for f in ("explain", "investigate", "propose", "fleet"))

        assert 30 <= core <= 40, core

    @pytest.mark.parametrize(
        ("family", "at_least"),
        [
            ("explain", 8),
            ("investigate", 11),
            ("propose", 6),
            ("fleet", 3),
            ("injection", 8),
            ("limits", 6),
            ("secrets", 5),
        ],
    )
    def test_each_family_has_its_scenarios(self, family: str, at_least: int) -> None:
        assert len(load_family(family)) >= at_least

    def test_the_failures_the_brief_names_are_explained_and_investigated(self) -> None:
        for family in ("explain", "investigate"):
            names = " ".join(s.id for s in load_family(family))
            for failure in (
                "missing-variable",
                "port-in-use",
                "out-of-memory",
                "dependency",
                "certificate-expired",
                "disk-full",
                "database-refuses",
                "nginx-test-fails",
            ):
                assert failure in names, f"{family} has no {failure}"

    def test_three_of_the_investigations_must_end_in_not_knowing(self) -> None:
        unknown = [s for s in load_family("investigate") if "no-evidence" in s.id]

        assert len(unknown) == 3
        assert all(s.expect.get("no_quotes") and s.expect.get("proposals") == [] for s in unknown)

    def test_what_a_person_asks_to_be_done_is_proposed_with_what_the_machine_gets_after_and_nothing_before(
        self,
    ) -> None:
        asked = " ".join(s.id for s in load_family("propose"))
        for what in ("restart", "limit", "roll-back", "certificate", "backup", "repository"):
            assert what in asked, f"nothing proposes {what}"
        for scenario in load_family("propose"):
            if scenario.id in ("propose-a-secret-is-never-carried",):
                continue
            nothing_before = scenario.expect.get("executed", [])
            if scenario.policy:
                continue
            assert nothing_before == [], scenario.id
            assert scenario.approve, (
                f"{scenario.id} does not say what the machine gets after approval"
            )

    def test_the_fleet_has_a_server_of_the_central_and_one_of_the_previous_version(self) -> None:
        fleet = load_family("fleet")

        assert any(s.seed.get("fleet", {}).get("older") for s in fleet)
        assert any(not s.seed.get("fleet", {}).get("older") for s in fleet)
        assert {form for s in fleet for form in s.forms} == set(FORMS)

    def test_every_carrier_of_a_text_somebody_else_wrote_is_attacked(self) -> None:
        carriers = {c for s in load_family("injection") for c in (s.carriers or CARRIERS)}

        assert carriers == set(CARRIERS)

    @pytest.mark.parametrize(
        "sink",
        ["stop", "variable", "alerts", "ssh-key", "ignore", "domain", "deploy", "delete"],
    )
    def test_every_sink_of_the_brief_is_tried(self, sink: str) -> None:
        assert any(sink in s.id for s in load_family("injection"))

    def test_the_limits_the_brief_names_are_there(self) -> None:
        ids = " ".join(s.id for s in load_family("limits"))

        for limit in ("never-stops", "reads-too-much", "price-table", "no-price", "per-hour"):
            assert limit in ids.replace("changes-per-hour", "per-hour"), limit

    def test_the_three_forms_have_something_to_stand_on(self) -> None:
        for form in FORMS:
            assert [s for s in all_scenarios() if form in s.forms], form


class TestTheFilesAreClean:
    def test_no_scenario_says_what_the_pre_push_check_forbids(self) -> None:
        for path in [
            *SCENARIOS_DIR.rglob("*.yaml"),
            *(SCENARIOS_DIR.parent.parent / "support" / "assistant_battery").rglob("*.py"),
        ]:
            found = VETOED.search(path.read_text())
            assert found is None, f"{path.name} says {found.group(0)!r}" if found else ""

    def test_every_scenario_file_is_plain_yaml(self) -> None:
        for path in SCENARIOS_DIR.rglob("*.yaml"):
            assert isinstance(yaml.safe_load(path.read_text()), dict), path.name

    def test_a_scenario_is_a_value_that_can_be_compared(self) -> None:
        first, second = load_family("explain")[0], load_family("explain")[0]

        assert first == second and isinstance(first, Scenario)
