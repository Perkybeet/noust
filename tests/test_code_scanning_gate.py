# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
No alert of high or critical severity stays open without a decision (item 87).

A finding of the code scanning is either fixed, or dismissed in GitHub with the reason written
down. ``scripts/code_scanning_gate.py`` is the job of ``security.yml`` that reads the open alerts
of the branch and fails while any of that severity is left, so a new one cannot be forgotten
under the hundreds that came before it.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "code_scanning_gate.py"


@pytest.fixture(scope="module")
def gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location("code_scanning_gate", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def alert(
    number: int,
    rule: str = "py/path-injection",
    severity: str | None = "high",
    *,
    path: str = "src/noust/x.py",
    line: int = 3,
    state: str = "open",
    level: str = "error",
) -> dict[str, Any]:
    return {
        "number": number,
        "state": state,
        "html_url": f"https://github.com/Perkybeet/noust/security/code-scanning/{number}",
        "rule": {"id": rule, "severity": level, "security_severity_level": severity},
        "tool": {"name": "CodeQL"},
        "most_recent_instance": {"location": {"path": path, "start_line": line}},
    }


class TestReadingWhatGhPrints:
    def test_one_array(self, gate: ModuleType) -> None:
        assert gate.load_alerts(json.dumps([alert(1), alert(2)])) == [alert(1), alert(2)]

    def test_arrays_one_after_the_other_as_older_gh_prints_pages(self, gate: ModuleType) -> None:
        text = json.dumps([alert(1)]) + json.dumps([alert(2)]) + "\n"

        assert [a["number"] for a in gate.load_alerts(text)] == [1, 2]

    def test_nothing_is_no_alerts(self, gate: ModuleType) -> None:
        assert gate.load_alerts("") == []
        assert gate.load_alerts("[]") == []

    def test_something_that_is_not_a_list_of_alerts_is_an_error_not_a_pass(
        self, gate: ModuleType
    ) -> None:
        with pytest.raises(ValueError, match="alerts"):
            gate.load_alerts('{"message": "Bad credentials"}')


class TestWhatBlocks:
    @pytest.mark.parametrize("severity", ["critical", "high"])
    def test_an_open_alert_of_high_or_critical_severity_blocks(
        self, gate: ModuleType, severity: str
    ) -> None:
        assert gate.blocking([alert(1, severity=severity)]) == [alert(1, severity=severity)]

    @pytest.mark.parametrize("severity", ["medium", "low"])
    def test_a_lesser_one_does_not(self, gate: ModuleType, severity: str) -> None:
        assert gate.blocking([alert(1, severity=severity)]) == []

    def test_an_alert_a_decision_closed_does_not(self, gate: ModuleType) -> None:
        closed = [alert(1, state="dismissed"), alert(2, state="fixed")]

        assert gate.blocking(closed) == []

    def test_a_rule_with_no_security_severity_is_judged_by_its_error_level(
        self, gate: ModuleType
    ) -> None:
        assert gate.blocking([alert(1, severity=None, level="error")]) != []
        assert gate.blocking([alert(2, severity=None, level="warning")]) == []
        assert gate.blocking([alert(3, severity=None, level="note")]) == []


class TestTheReport:
    def test_it_names_each_alert_where_it_is_and_where_to_decide_it(self, gate: ModuleType) -> None:
        text = gate.report([alert(7, "py/partial-ssrf", "critical", path="a/b.py", line=12)])

        assert "#7" in text and "critical" in text and "py/partial-ssrf" in text
        assert "a/b.py:12" in text
        assert "https://github.com/Perkybeet/noust/security/code-scanning/7" in text

    def test_the_worst_come_first(self, gate: ModuleType) -> None:
        text = gate.report([alert(1, severity="high"), alert(2, severity="critical")])

        assert text.index("#2") < text.index("#1")


class TestTheCommand:
    def test_it_passes_with_nothing_to_decide(
        self, gate: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        file = tmp_path / "alerts.json"
        file.write_text(json.dumps([alert(1, severity="low")]))

        assert gate.main([str(file)]) == 0
        assert "No open alert" in capsys.readouterr().out

    def test_it_fails_and_says_which_while_one_is_open(
        self, gate: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        file = tmp_path / "alerts.json"
        file.write_text(json.dumps([alert(5, severity="critical")]))

        assert gate.main([str(file)]) == 1
        assert "#5" in capsys.readouterr().out

    def test_an_unreadable_answer_fails_instead_of_passing(
        self, gate: ModuleType, tmp_path: Path
    ) -> None:
        file = tmp_path / "alerts.json"
        file.write_text('{"message": "Resource not accessible by integration"}')

        assert gate.main([str(file)]) == 2
