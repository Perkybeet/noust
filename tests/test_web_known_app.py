# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one answer to "is there an application here, for this caller", for a route that takes the
domain in its path.

Thirty-three routers each carried a copy of it, under three names, and a router written after them
copied the nearest one. The copy that forgets the scope is the one that answers 200 for another
team's application, so there is one, here, and ``tests/test_architecture.py`` fails on a second.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import HTTPException

from noust.core.exceptions import DomainError
from noust.core.store import App, NoustStore
from noust.core.teams import Scope, ScopedStore
from noust.web.api import deps
from noust.web.api.deps import known_app, known_app_row

DOMAIN = "shop.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    instance.create_app(
        App(domain=DOMAIN, app_type="nodejs", port=3100, app_path="/srv/shop", webserver="nginx")
    )
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def seen_by_the_instance(store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "scoped_store", lambda: ScopedStore(Scope.everything(), store))


def test_it_returns_the_validated_domain_of_an_application_that_exists(
    seen_by_the_instance: None,
) -> None:
    assert known_app(DOMAIN) == DOMAIN
    assert known_app("  Shop.Example.com ") == DOMAIN


def test_it_is_a_404_when_nothing_is_deployed_there(seen_by_the_instance: None) -> None:
    with pytest.raises(HTTPException) as caught:
        known_app("other.example.com")

    assert caught.value.status_code == 404
    assert caught.value.detail == "Application not found: other.example.com"


def test_another_teams_application_reads_as_one_that_does_not_exist(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 403 would say it is there; the body is the one of an application nobody has."""
    monkeypatch.setattr(deps, "scoped_store", lambda: ScopedStore(Scope.nothing(), store))

    with pytest.raises(HTTPException) as caught:
        known_app(DOMAIN)
    with pytest.raises(HTTPException) as absent:
        known_app("other.example.com")

    assert caught.value.status_code == 404
    assert caught.value.detail == f"Application not found: {DOMAIN}"
    assert absent.value.detail == "Application not found: other.example.com"


@pytest.mark.parametrize("value", ["shop.example.com/..", "https://shop.example.com", "a b", ""])
def test_a_value_that_is_not_a_bare_domain_is_refused_before_the_lookup(
    seen_by_the_instance: None, value: str
) -> None:
    """``evil/../..`` must not be turned into ``evil`` and looked up as if the client had said so."""
    with pytest.raises(DomainError):
        known_app(value)


def test_the_row_variant_returns_the_application(seen_by_the_instance: None) -> None:
    row = known_app_row(" Shop.Example.com")

    assert row.domain == DOMAIN
    assert row.app_type == "nodejs"


def test_the_row_variant_refuses_the_same_ways(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deps, "scoped_store", lambda: ScopedStore(Scope.nothing(), store))

    with pytest.raises(HTTPException) as caught:
        known_app_row(DOMAIN)
    with pytest.raises(DomainError):
        known_app_row("a/b")

    assert caught.value.status_code == 404
