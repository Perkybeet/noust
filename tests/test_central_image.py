# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the central's container image promises that its scan (Trivy, in security.yml) would otherwise find out.

The base image is a floating tag, rebuilt when its maintainers choose, so the pip and the system
packages it carries can be behind a published fix on the day Noust builds on it. The Dockerfile
brings both up to date itself, before anything of Noust's goes in, and runs as nobody in particular.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOCKERFILE = Path(__file__).resolve().parent.parent / "packaging" / "container" / "Dockerfile"


@pytest.fixture(scope="module")
def instructions() -> list[str]:
    """The Dockerfile's instructions, each joined onto one line and without comments."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    joined = re.sub(r"\\\n\s*", " ", text)
    return [
        line.strip() for line in joined.splitlines() if line.strip() and not line.startswith("#")
    ]


def index_of(instructions: list[str], needle: str) -> int:
    for position, line in enumerate(instructions):
        if needle in line:
            return position
    raise AssertionError(f"no instruction holds {needle!r}")


def test_the_system_packages_are_brought_up_to_date_when_the_image_is_built(
    instructions: list[str],
) -> None:
    assert "apt-get upgrade -y" in instructions[index_of(instructions, "apt-get update")]


def test_pip_is_upgraded_before_noust_is_installed_with_it(instructions: list[str]) -> None:
    text = " ".join(instructions)

    upgraded = text.index("pip install --no-cache-dir --no-compile --upgrade pip")
    installed = text.index('.whl[web]"')

    assert upgraded < installed


def test_the_image_does_not_run_as_root_and_says_whether_it_is_well(
    instructions: list[str],
) -> None:
    users = [line for line in instructions if line.startswith("USER ")]

    assert users and users[-1] != "USER root"
    assert any(line.startswith("HEALTHCHECK ") for line in instructions)
