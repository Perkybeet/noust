# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Asking a registry when a domain's registration expires, once, and only what was meant (item 173).

The request is the one new thing that leaves the server in 4.3 that nobody typed an address for, so
what is pinned is as much what it does not do as what it does: it goes to the registry the bundled
table names and to no other, over https, never to an internal address, never follows a redirect,
sends nothing but the domain name and the version of the program, reads a bounded answer, and asks
a name once a day (a week when the registry has no answer to give, never every day).
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from noust import __version__
from noust.core import outbound
from noust.core.domain_registrations import (
    ERROR,
    NO_EXPIRY,
    NO_RDAP,
    NOT_FOUND,
    OK,
    RegistrationStore,
)
from noust.core.store import NoustStore
from noust.managers.domain_registration import DomainRegistrations

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
COM = "https://rdap.verisign.com/com/v1/"
UK = "https://rdap.nominet.uk/uk/"


def domain_object(expires: str | None = "2027-08-13T04:00:00Z") -> bytes:
    events = [{"eventAction": "registration", "eventDate": "1995-08-14T04:00:00Z"}]
    if expires:
        events.append({"eventAction": "expiration", "eventDate": expires})
    return json.dumps(
        {"objectClassName": "domain", "ldhName": "example.com", "events": events}
    ).encode()


class Answer:
    """What a registry sends back: a status, a body, and nothing else."""

    def __init__(self, status: int = 200, body: bytes = b"") -> None:
        self.status = status
        self.body = body
        self.read_sizes: list[int] = []

    def __enter__(self) -> Answer:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return self.body if size < 0 else self.body[:size]


class Registry:
    """The registries of a test: a table of address to answer, and a record of what was asked."""

    def __init__(self, **answers: Answer | Exception) -> None:
        self.answers: dict[str, Answer | Exception] = {}
        for key, answer in answers.items():
            self.answers[key] = answer
        self.asked: list[urllib.request.Request] = []
        self.timeouts: list[float] = []

    def at(self, url: str, answer: Answer | Exception) -> Registry:
        self.answers[url] = answer
        return self

    def __call__(self, request: urllib.request.Request, timeout: float) -> Answer:
        self.asked.append(request)
        self.timeouts.append(timeout)
        found = self.answers.get(request.full_url)
        if found is None:
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b"{}"))  # type: ignore[arg-type]
        if isinstance(found, Exception):
            raise found
        return found

    def urls(self) -> list[str]:
        return [request.full_url for request in self.asked]


@pytest.fixture(autouse=True)
def public_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every registry resolves to a public address; no test resolves a real name."""
    monkeypatch.setattr(outbound, "resolve_host", lambda host: ("93.184.216.34",))


@pytest.fixture
def store(tmp_path: Path) -> Any:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


def manager(store: NoustStore, registry: Callable[..., Any]) -> DomainRegistrations:
    return DomainRegistrations(store, opener=registry, clock=lambda: NOW)


class TestFindingTheRegistrableDomain:
    def test_a_subdomain_is_climbed_until_the_registry_knows_the_name(
        self, store: NoustStore
    ) -> None:
        registry = Registry().at(COM + "domain/example.com", Answer(200, domain_object()))

        found = manager(store, registry).check("shop.example.com")

        assert found is not None and found.domain == "example.com"
        assert registry.urls() == [COM + "domain/shop.example.com", COM + "domain/example.com"]
        assert RegistrationStore(store).registrable_of("shop.example.com") == "example.com"

    def test_a_two_label_suffix_is_found_by_the_registry_and_not_by_a_list(
        self, store: NoustStore
    ) -> None:
        registry = Registry().at(UK + "domain/example.co.uk", Answer(200, domain_object()))

        found = manager(store, registry).check("shop.example.co.uk")

        assert found is not None and found.domain == "example.co.uk"
        assert registry.urls() == [UK + "domain/shop.example.co.uk", UK + "domain/example.co.uk"]

    def test_once_it_is_found_only_that_name_is_asked_about_again(self, store: NoustStore) -> None:
        registry = Registry().at(COM + "domain/example.com", Answer(200, domain_object()))
        both = manager(store, registry)
        both.check("shop.example.com")
        registry.asked.clear()

        both.check("shop.example.com", force=True)

        assert registry.urls() == [COM + "domain/example.com"]

    def test_the_name_itself_is_asked_first_so_a_registrable_name_costs_one_question(
        self, store: NoustStore
    ) -> None:
        registry = Registry().at(COM + "domain/example.com", Answer(200, domain_object()))

        manager(store, registry).check("example.com")

        assert registry.urls() == [COM + "domain/example.com"]

    def test_a_name_the_registry_does_not_know_at_any_label_is_not_found(
        self, store: NoustStore
    ) -> None:
        found = manager(store, Registry()).check("shop.nobody.com")

        assert found is not None and found.status == NOT_FOUND
        assert found.expires_at is None
        assert "does not know" in found.detail

    def test_a_wildcard_is_asked_about_as_the_name_it_covers(self, store: NoustStore) -> None:
        registry = Registry().at(COM + "domain/example.com", Answer(200, domain_object()))

        found = manager(store, registry).check("*.example.com")

        assert found is not None and found.domain == "example.com"


class TestWhatTheRegistrySays:
    def test_the_expiry_is_kept_with_the_registry_that_said_it(self, store: NoustStore) -> None:
        registry = Registry().at(COM + "domain/example.com", Answer(200, domain_object()))

        found = manager(store, registry).check("example.com")

        assert found is not None
        assert (found.status, found.expires_at, found.rdap_base) == (
            OK,
            "2027-08-13T04:00:00+00:00",
            COM,
        )
        assert RegistrationStore(store).get("example.com") == found

    def test_a_registry_that_answers_with_no_expiry_says_it_does_not_publish_it(
        self, store: NoustStore
    ) -> None:
        registry = Registry().at(COM + "domain/example.com", Answer(200, domain_object(None)))

        found = manager(store, registry).check("example.com")

        assert found is not None and found.status == NO_EXPIRY
        assert "does not publish" in found.detail

    def test_a_top_level_domain_with_no_rdap_is_said_once_and_asked_nothing(
        self, store: NoustStore
    ) -> None:
        registry = Registry()

        found = manager(store, registry).check("shop.example.es")

        assert found is not None
        assert (found.status, found.domain) == (NO_RDAP, "example.es")
        assert "this registry does not publish the expiry" in found.detail.lower()
        assert registry.asked == []

    def test_a_registry_that_is_down_is_an_error_to_look_at_again_soon(
        self, store: NoustStore
    ) -> None:
        registry = Registry().at(COM + "domain/example.com", urllib.error.URLError("timed out"))

        found = manager(store, registry).check("example.com")

        assert found is not None and found.status == ERROR
        assert "timed out" in found.detail
        assert datetime.fromisoformat(found.next_check_at) - NOW == timedelta(hours=6)

    @pytest.mark.parametrize("status", [429, 500, 503])
    def test_a_registry_that_refuses_or_fails_is_an_error(
        self, store: NoustStore, status: int
    ) -> None:
        error = urllib.error.HTTPError(COM, status, "x", {}, io.BytesIO(b"{}"))  # type: ignore[arg-type]
        registry = Registry().at(COM + "domain/example.com", error)

        found = manager(store, registry).check("example.com")

        assert found is not None and found.status == ERROR
        assert str(status) in found.detail

    def test_a_redirect_is_not_followed(self, store: NoustStore) -> None:
        redirect = urllib.error.HTTPError(
            COM,
            302,
            "Found",
            {"Location": "http://169.254.169.254/"},
            io.BytesIO(b""),  # type: ignore[arg-type]
        )
        registry = Registry().at(COM + "domain/example.com", redirect)

        found = manager(store, registry).check("example.com")

        assert found is not None and found.status == ERROR
        assert "redirect" in found.detail
        assert registry.urls() == [COM + "domain/example.com"]

    @pytest.mark.parametrize("body", [b"not json", b"[1, 2]", b"", b"\xff\xfe"])
    def test_an_answer_that_is_not_an_rdap_object_is_an_error_and_raises_nothing(
        self, store: NoustStore, body: bytes
    ) -> None:
        registry = Registry().at(COM + "domain/example.com", Answer(200, body))

        found = manager(store, registry).check("example.com")

        assert found is not None and found.status == ERROR

    def test_what_is_read_is_bounded(self, store: NoustStore) -> None:
        huge = Answer(200, b" " * (3 * 1024 * 1024))
        registry = Registry().at(COM + "domain/example.com", huge)

        found = manager(store, registry).check("example.com")

        assert found is not None and found.status == ERROR
        assert huge.read_sizes and max(huge.read_sizes) <= 256 * 1024 + 1


class TestWhatIsSent:
    def test_the_request_names_the_domain_and_the_version_and_nothing_else(
        self, store: NoustStore
    ) -> None:
        registry = Registry().at(COM + "domain/example.com", Answer(200, domain_object()))

        manager(store, registry).check("example.com")

        [request] = registry.asked
        assert dict(request.header_items()) == {
            "Accept": "application/rdap+json",
            "User-agent": f"noust/{__version__}",
        }
        assert request.get_method() == "GET"
        assert request.data is None
        assert request.full_url.startswith("https://")

    def test_the_request_has_a_timeout(self, store: NoustStore) -> None:
        registry = Registry().at(COM + "domain/example.com", Answer(200, domain_object()))

        manager(store, registry).check("example.com")

        assert registry.timeouts and all(0 < t <= 30 for t in registry.timeouts)

    def test_a_registry_that_resolves_to_an_internal_address_is_not_asked(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(outbound, "resolve_host", lambda host: ("10.0.0.7",))
        registry = Registry().at(COM + "domain/example.com", Answer(200, domain_object()))

        found = manager(store, registry).check("example.com")

        assert registry.asked == []
        assert found is not None and found.status == ERROR
        assert "internal" in found.detail

    def test_a_name_that_is_not_a_domain_is_never_sent(self, store: NoustStore) -> None:
        registry = Registry()
        asked = manager(store, registry)

        for name in ("localhost", "10.0.0.1", "../etc", "", "bücher.example"):
            assert asked.check(name) is None

        assert registry.asked == []


class TestOnceADay:
    def test_a_domain_looked_at_today_is_not_asked_again_until_tomorrow(
        self, store: NoustStore
    ) -> None:
        registry = Registry().at(COM + "domain/example.com", Answer(200, domain_object()))
        both = manager(store, registry)
        both.check_names(["shop.example.com"])
        asked = len(registry.asked)

        both.check_names(["shop.example.com"])

        assert len(registry.asked) == asked

    def test_it_is_asked_again_when_its_day_has_come(self, store: NoustStore) -> None:
        registry = Registry().at(COM + "domain/example.com", Answer(200, domain_object()))
        manager(store, registry).check_names(["example.com"])
        later = DomainRegistrations(store, opener=registry, clock=lambda: NOW + timedelta(hours=25))

        later.check_names(["example.com"])

        assert len(registry.asked) == 2

    def test_force_asks_now(self, store: NoustStore) -> None:
        registry = Registry().at(COM + "domain/example.com", Answer(200, domain_object()))
        both = manager(store, registry)
        both.check_names(["example.com"])

        both.check_names(["example.com"], force=True)

        assert len(registry.asked) == 2

    def test_several_names_of_one_domain_ask_the_registry_once(self, store: NoustStore) -> None:
        registry = Registry().at(COM + "domain/example.com", Answer(200, domain_object()))

        manager(store, registry).check_names(["example.com", "www.example.com", "shop.example.com"])

        assert registry.urls().count(COM + "domain/example.com") == 1

    def test_a_registry_with_no_answer_to_give_is_not_asked_every_day(
        self, store: NoustStore
    ) -> None:
        registry = Registry()
        both = manager(store, registry)
        first = both.check("shop.example.es")
        assert first is not None
        assert datetime.fromisoformat(first.next_check_at) - NOW >= timedelta(days=7)

    def test_a_name_that_is_gone_takes_its_registration_with_it(self, store: NoustStore) -> None:
        registry = Registry().at(COM + "domain/example.com", Answer(200, domain_object()))
        both = manager(store, registry)
        both.check_names(["example.com"])
        assert len(RegistrationStore(store).all()) == 1

        both.forget_unserved(["other.example.org"])

        assert RegistrationStore(store).all() == []

    def test_one_that_fails_does_not_stop_the_rest(self, store: NoustStore) -> None:
        registry = (
            Registry()
            .at(COM + "domain/bad.com", urllib.error.URLError("down"))
            .at(COM + "domain/good.com", Answer(200, domain_object()))
        )

        done = manager(store, registry).check_names(["bad.com", "good.com"])

        assert {r.domain: r.status for r in done} == {"bad.com": ERROR, "good.com": OK}


class TestWhichNamesAreServed:
    def test_the_domains_of_every_application_that_is_not_a_preview_are_listed_once(
        self, store: NoustStore
    ) -> None:
        from noust.core.store import App
        from noust.managers.domain_registration import served_names

        store.create_app(App(domain="shop.example.com", app_type="nodejs", port=3000))
        store.add_domain("shop.example.com", "www.shop.example.com", "alias")
        store.add_domain("shop.example.com", "off.shop.example.com", "alias")
        store.create_app(
            App(
                domain="pr-1.example.com",
                app_type="nodejs",
                port=3001,
                preview_parent="shop.example.com",
            )
        )
        store.create_app(App(domain="blog.example.org", app_type="nodejs", port=3002))
        store.set_domain_enabled("shop.example.com", "off.shop.example.com", False)

        assert served_names(store) == [
            "blog.example.org",
            "shop.example.com",
            "www.shop.example.com",
        ]
