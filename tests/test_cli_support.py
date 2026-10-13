# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust support report`` (checklist N-33): the command is a presentation layer over the reporter.

What is pinned: the options reach the reporter, the report is printed (or written owner-only
to a file, never both), ``--json`` prints the structured form, and the command is read-only
so it needs no ``--reason`` under the ENS profile.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.managers.support_report import ReportSection, SupportReport


class FakeReporter:
    built: list[dict[str, Any]] = []

    def __init__(self, **options: Any) -> None:
        self.options = options
        FakeReporter.built.append(options)

    def build(self) -> SupportReport:
        return SupportReport(
            "3.4.0",
            "2026-10-13T10:00:00Z",
            self.options["hide_domains"],
            (ReportSection("noust", "Noust", "Noust: 3.4.0"),),
        )


@pytest.fixture(autouse=True)
def fake_reporter(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeReporter.built = []
    monkeypatch.setattr("noust.cli.commands.support.SupportReporter", FakeReporter)


def run(*args: str) -> Any:
    return CliRunner().invoke(root_cli, ["support", "report", *args])


def test_it_prints_the_report_for_an_issue() -> None:
    result = run()

    assert result.exit_code == 0, result.output
    assert result.output.startswith("# Noust support report")
    assert "Noust: 3.4.0" in result.output


def test_the_options_reach_the_reporter() -> None:
    result = run("--lines", "20", "--diagnose", "5", "--hide-domains")

    assert result.exit_code == 0, result.output
    options = FakeReporter.built[-1]
    assert options["journal_lines"] == 20
    assert options["diagnose_limit"] == 5
    assert options["hide_domains"] is True
    assert options["journal_left_out"] is None


def test_no_lines_leaves_the_journal_out_and_says_why() -> None:
    run("--lines", "0")

    assert FakeReporter.built[-1]["journal_left_out"] == "--lines 0 was given"


def test_json_prints_the_structured_report() -> None:
    result = run("--json")

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["version"] == "3.4.0"
    assert data["sections"][0]["key"] == "noust"
    assert data["markdown"].startswith("# Noust support report")


def test_output_writes_an_owner_only_file_instead_of_printing(tmp_path: Path) -> None:
    target = tmp_path / "report.md"

    result = run("--output", str(target))

    assert result.exit_code == 0, result.output
    assert target.read_text().startswith("# Noust support report")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert "# Noust support report" not in result.output


@pytest.mark.parametrize("lines", ["-1", "2001"])
def test_a_number_of_lines_out_of_range_is_a_usage_error(lines: str) -> None:
    result = run("--lines", lines)

    assert result.exit_code == 2
    assert FakeReporter.built == []


def test_it_is_read_only_so_the_ens_profile_asks_for_no_reason() -> None:
    from noust.cli import audit_policy

    command = root_cli.get_command(None, "support")  # type: ignore[arg-type]
    assert command is not None
    report = command.get_command(None, "report")  # type: ignore[attr-defined,arg-type]

    assert (
        getattr(report, "read_only", False) or "support report" in audit_policy.READ_ONLY_COMMANDS
    )
