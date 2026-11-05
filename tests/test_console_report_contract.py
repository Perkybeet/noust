# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The console's "Report a problem" opens the console report form, and says what the form says.

``panel/src/features/report`` writes the address of ``.github/ISSUE_TEMPLATE/console_report.yml`` filled in:
the form's file name, a parameter for each field by its id, and an ``area`` that is one of the
dropdown's own options (GitHub drops a value that is not). The console is TypeScript and the form is
data this repository owns, so the check is made here, where both can be read: the console's list of
areas and its field ids are held to the form, option by option, and a change to either side breaks
this test instead of an empty box in somebody's browser.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
FORM = ROOT / ".github" / "ISSUE_TEMPLATE" / "console_report.yml"
REPORT = ROOT / "panel" / "src" / "features" / "report"


def _form() -> dict[str, Any]:
    data = yaml.safe_load(FORM.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _fields() -> dict[str, dict[str, Any]]:
    return {
        entry["id"]: entry for entry in _form()["body"] if isinstance(entry, dict) and "id" in entry
    }


def _source(name: str) -> str:
    return (REPORT / name).read_text(encoding="utf-8")


def test_the_console_offers_the_options_of_the_forms_area_dropdown_exactly_and_in_order() -> None:
    options = _fields()["area"]["attributes"]["options"]
    offered = re.findall(r'\{ value: "([^"]+)", key: "report\.areas\.\w+" \}', _source("areas.ts"))

    assert offered == options


def test_the_console_names_the_template_the_title_and_the_fields_the_form_has() -> None:
    source = _source("githubUrl.ts")
    ids = set(_fields())

    template = re.search(r'REPORT_TEMPLATE = "([^"]+)"', source)
    assert template is not None and template.group(1) == FORM.name
    prefix = re.search(r'TITLE_PREFIX = "([^"]*)"', source)
    assert prefix is not None and prefix.group(1) == _form()["title"]
    block = re.search(r"FIELD_IDS = \{([^}]*)\}", source)
    assert block is not None
    sent = set(re.findall(r'"([^"]+)"', block.group(1)))
    assert sent <= ids
    # The one field it leaves for the person is the one that takes the file.
    assert ids - sent == {"attachment"}


def test_the_dropdown_and_the_fields_the_console_fills_in_are_required_by_the_form() -> None:
    fields = _fields()

    for needed in ("version", "area", "what-happened", "attachment"):
        assert fields[needed].get("validations", {}).get("required") is True, needed
    assert not fields["reproduce"].get("validations", {}).get("required")


def test_the_attachment_box_takes_the_kind_of_file_the_console_makes() -> None:
    description = _fields()["attachment"]["attributes"]["description"]

    assert ".zip" in description
    assert "25 MB" in description


def test_the_labels_the_console_quotes_are_the_ones_the_form_writes() -> None:
    fields = _fields()
    block = re.search(r"FORM_LABELS = \{([^}]*)\}", _source("githubUrl.ts"))
    assert block is not None
    labels = dict(re.findall(r'(\w+): "([^"]+)"', block.group(1)))

    assert labels["version"] == fields["version"]["attributes"]["label"]
    assert labels["area"] == fields["area"]["attributes"]["label"]
    assert labels["whatHappened"] == fields["what-happened"]["attributes"]["label"]
    assert labels["reproduce"] == fields["reproduce"]["attributes"]["label"]
    # The box the file is dragged into is named the way the instructions of the last step name it.
    box = re.search(r'GITHUB_FILE_BOX = "([^"]+)"', _source("StepDone.tsx"))
    assert box is not None and box.group(1) == fields["attachment"]["attributes"]["label"]
