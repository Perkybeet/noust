# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
"Is an application deployed at this domain", for the code that runs as the instance.

The command line, a job and the deployers read the store as root: there is no caller whose teams
could hide an application, so the answer is a plain lookup that says where to look when it fails.
(The console's answer, which is scoped to the caller, is ``noust.web.api.deps.known_app``.)
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.cli.lookup import known_app
from noust.core.app_lookup import require_app
from noust.core.exceptions import DomainError, NoustError
from noust.core.store import App, NoustStore

DOMAIN = "shop.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    instance.create_app(App(domain=DOMAIN, app_type="nodejs", app_path="/srv/shop"))
    yield instance
    NoustStore.reset_instance()


def test_it_returns_the_row_of_an_application_that_exists(store: NoustStore) -> None:
    assert require_app(store, DOMAIN).domain == DOMAIN


def test_it_says_what_to_run_when_there_is_none(store: NoustStore) -> None:
    with pytest.raises(NoustError) as caught:
        require_app(store, "other.example.com")

    assert caught.value.message == "Application not found: other.example.com"
    assert caught.value.details == "Run 'noust list' to see what is deployed."


def test_the_command_line_validates_the_domain_before_it_looks(store: NoustStore) -> None:
    """The operator typed it: ``https://Shop.Example.com/`` is the domain they meant."""
    assert known_app("https://Shop.Example.com/") == DOMAIN


def test_the_command_line_refuses_an_unknown_application_the_same_way(store: NoustStore) -> None:
    with pytest.raises(NoustError) as caught:
        known_app("other.example.com")

    assert caught.value.details == "Run 'noust list' to see what is deployed."


def test_the_command_line_refuses_what_is_not_a_domain(store: NoustStore) -> None:
    with pytest.raises(DomainError):
        known_app("not a domain")
