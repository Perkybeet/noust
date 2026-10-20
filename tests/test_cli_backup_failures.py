# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust backup failures`` reads what a notice linked to (checklist H-10).

The notice's command is ``noust backup failures show ID``; this pins that it prints the output of the
record exactly as it was stored (so it can be piped to a file), that the list says what went wrong
where without printing the output, that ``--json`` is the same data as the API, and that an unknown
id is an error with a nonzero exit.
"""

# The fixtures are imported from the tests of the thing they set up.

from __future__ import annotations

import json
from typing import Any

from click.testing import CliRunner

from noust.cli.app import Context, cli
from noust.core.store import get_store
from noust.managers.backup_failures import BackupFailureLog
from tests.test_backup_safety_22 import _reset_store  # noqa: F401


def invoke(*argv: str) -> Any:
    return CliRunner().invoke(cli, list(argv), obj=Context())


def record(
    kind: str = "backup",
    subject: str = "shop.example.com",
    text: str = "boom",
    destination: str | None = None,
) -> int:
    return BackupFailureLog(get_store()).record(kind, subject, text, destination=destination)


class TestShow:
    def test_it_prints_the_output_as_it_was(self) -> None:
        text = "\n".join(f"pg_dump: line {n}" for n in range(80))
        failure_id = record(text=text)

        result = invoke("backup", "failures", "show", str(failure_id))

        assert result.exit_code == 0, result.output
        assert text in result.output
        assert "shop.example.com" in result.output

    def test_raw_prints_nothing_but_the_output(self) -> None:
        failure_id = record(text="first\nsecond")

        result = invoke("backup", "failures", "show", str(failure_id), "--raw")

        assert result.exit_code == 0
        assert result.output == "first\nsecond\n"

    def test_json_is_what_the_api_says(self) -> None:
        failure_id = record(kind="upload", text="denied", destination="nas")

        result = invoke("backup", "failures", "show", str(failure_id), "--json")

        body = json.loads(result.output)
        assert (body["id"], body["kind"], body["destination"], body["output"]) == (
            failure_id,
            "upload",
            "nas",
            "denied",
        )

    def test_an_unknown_id_is_an_error(self) -> None:
        result = invoke("backup", "failures", "show", "404")

        assert result.exit_code == 1
        assert "No backup failure 404" in result.output

    def test_a_word_is_not_an_id(self) -> None:
        assert invoke("backup", "failures", "show", "latest").exit_code == 2


class TestList:
    def test_it_says_what_failed_where_without_the_output(self) -> None:
        record(text="THE-WHOLE-OUTPUT")
        record(kind="upload", subject="b.example.com", text="x", destination="nas")

        result = invoke("backup", "failures", "list")

        assert result.exit_code == 0, result.output
        assert "b.example.com" in result.output and "nas" in result.output
        assert "THE-WHOLE-OUTPUT" not in result.output

    def test_nothing_failed_says_so(self) -> None:
        result = invoke("backup", "failures", "list")

        assert result.exit_code == 0
        assert "No backup has failed" in result.output

    def test_a_subject_narrows_it(self) -> None:
        record(subject="a.example.com")
        record(subject="b.example.com")

        result = invoke("backup", "failures", "list", "--subject", "b.example.com", "--json")

        assert [entry["subject"] for entry in json.loads(result.output)["failures"]] == [
            "b.example.com"
        ]
