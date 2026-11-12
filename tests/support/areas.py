# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The areas of the suite: a test file belongs to the one its name begins with.

The tests sit in one directory and their names are the only map (``test_web_*``, ``test_cli_*``,
``test_database_*``). ``conftest.py`` marks every test with the area of its file, so the slice
that matters to a change runs on its own (``pytest -m web``) and a machine can take one slice of
the suite and not another. A word has an area once ten files begin with it
(``tests/test_tests_layout.py`` says which are missing).
"""

from __future__ import annotations

import functools
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

import pytest

_TESTS_DIRECTORY = Path(__file__).resolve().parent.parent

#: The first word of a test file's name, and what the files that begin with it test.
AREAS: dict[str, str] = {
    "web": "the console's API: sign-in, routes, headers and the pages it serves",
    "cli": "the command line: its tree, its output and each command",
    "database": "database engines, their instances, users, dumps and restores",
    "app": "an application: its settings, files, environment and traffic",
    "fleet": "a central and the servers it drives",
    "backup": "backups of applications, volumes and the server, and getting back from them",
    "server": "the server itself: packages, storage, jobs, power and the checks on it",
    "compose": "Docker Compose stacks: documents, ports, relays and releases",
    "notification": "notifications: events, channels, routing and what they say",
    "delivery": "how a deployment is queued, cancelled and limited",
    "source": "where an application's code comes from",
    "security": "the guards over the product's own files and configuration",
    "container": "applications that run as a container",
    "docker": "the gate to Docker (the engine's doctor, the owner of each object, what may be done to it) and the Docker Compose deployer",
    "ens": "the ENS profile and its evidence",
    "unit": "the systemd units of an application",
    "deploy": "a deployment: its gates, keys, notifications and target",
    "monitor": "the monitor: samples, watches and thresholds",
    "console": "the console as a program: the machine it is developed on, how it is published, its headers and its proxies",
}


class Markable(Protocol):
    """A collected test, as far as marking it by area goes."""

    path: Path

    def get_closest_marker(self, name: str) -> object | None:
        """The nearest marker of that name, or None."""

    def add_marker(self, marker: pytest.MarkDecorator) -> None:
        """Mark the test."""


def area_of(path: Path) -> str | None:
    """
    The area of a test file.

    Only a ``test_*.py`` file beside this module's package belongs to one: the files of the
    directories below are named after what they hold.

    Args:
        path: The test file.

    Returns:
        The name of the area, or None when the first word of the file's name is not one.
    """
    if not path.name.startswith("test_") or not _is_the_tests_directory(path.parent):
        return None
    word = path.stem.removeprefix("test_").split("_")[0]
    return word if word in AREAS else None


@functools.cache
def _is_the_tests_directory(directory: Path) -> bool:
    """
    Whether a directory is ``tests/``, however a checkout reached it.

    A checkout opened through a link names its files by the link, and a test would then be in no
    area without anyone being told. The answer is kept: every test of a file asks.

    Args:
        directory: The directory a test file is in.

    Returns:
        True when it is the directory that holds this package.
    """
    return directory.resolve() == _TESTS_DIRECTORY


def mark_by_area(items: Iterable[Markable]) -> None:
    """
    Mark every test with the area of its file.

    Args:
        items: The collected tests; the ones in no area are left as they are.
    """
    for item in items:
        area = area_of(item.path)
        if area is not None and item.get_closest_marker(area) is None:
            item.add_marker(getattr(pytest.mark, area))
