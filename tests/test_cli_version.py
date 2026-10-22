# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What ``noust --version`` prints.

The first line is read by machines (a central's enrollment probe, the installer), so it
stays "Noust X.Y.Z"; the lines after it are the legal notices an interactive program
shows under the AGPL: who holds the copyright, the licence and that there is no warranty.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from noust import __copyright__, __version__
from noust.cli.app import cli


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_the_first_line_is_the_name_and_the_version(flag: str) -> None:
    result = CliRunner().invoke(cli, [flag])

    assert result.exit_code == 0
    assert result.output.splitlines()[0] == f"Noust {__version__}"


def test_the_author_the_licence_and_the_absence_of_warranty_follow() -> None:
    output = CliRunner().invoke(cli, ["--version"]).output

    assert __copyright__ == "Copyright (c) 2024-2026 Yago López Prado"
    assert __copyright__ in output
    assert "AGPL-3.0-or-later" in output
    assert "NO WARRANTY" in output
    assert "https://github.com/Perkybeet/noust" in output
