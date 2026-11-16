# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
When the console tells a browser to remember that a name is HTTPS only (item 198).

``Strict-Transport-Security`` binds a *name* in every port, so a central in a container on a NAS
that sent it on ``nas.example.net:8443`` made the browser refuse the NAS's own portal on the same
name (a certificate it cannot trust and no way to continue) and lifted the NAS's http pages to
https. The console now says it only where it is the owner of the name: the one ``noust console
expose`` published, or what ``web.hsts`` asks for in so many words. ``includeSubDomains`` is never
part of it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from noust.core import hsts
from noust.core.config import Config
from noust.core.console_exposure_store import (
    STATE_ACTIVE,
    STATE_PENDING,
    ConsoleExposureStore,
    ExposureState,
)
from noust.core.exceptions import ConfigError
from noust.core.store import NoustStore, StoreError
from noust.managers import webserver
from noust.web import console_hsts
from noust.web.server import create_app
from tests.support.web_auth import make_config

NAME = "console.example.com"


class TestTheDecision:
    def test_the_value_is_a_year_with_no_subdomains_and_no_preload(self) -> None:
        assert hsts.VALUE == "max-age=31536000"

    def test_it_is_the_value_the_sites_noust_writes_send(self) -> None:
        assert webserver.HSTS_VALUE == hsts.VALUE

    @pytest.mark.parametrize("mode", hsts.MODES)
    def test_cleartext_never_carries_it(self, mode: str) -> None:
        assert hsts.header_for(mode, secure=False, host=NAME, published=NAME) is None

    def test_automatic_sends_it_on_the_name_the_console_was_published_on(self) -> None:
        assert hsts.header_for("auto", secure=True, host=NAME, published=NAME) == hsts.VALUE

    @pytest.mark.parametrize("host", ["nas.example.net", "192.0.2.10", "localhost", "", None])
    def test_automatic_says_nothing_on_any_other_name(self, host: str | None) -> None:
        assert hsts.header_for("auto", secure=True, host=host, published=NAME) is None

    def test_automatic_says_nothing_when_nothing_was_published(self) -> None:
        assert hsts.header_for("auto", secure=True, host=NAME, published=None) is None

    def test_a_name_is_compared_as_a_name_not_as_text(self) -> None:
        assert (
            hsts.header_for("auto", secure=True, host="Console.Example.COM", published=NAME)
            == hsts.VALUE
        )

    def test_a_name_that_only_ends_the_same_is_another_name(self) -> None:
        assert hsts.header_for("auto", secure=True, host="evil" + NAME, published=NAME) is None

    def test_always_sends_it_over_tls_on_any_name(self) -> None:
        assert hsts.header_for("always", secure=True, host="nas.example.net", published=None) == (
            hsts.VALUE
        )

    def test_never_sends_it_even_where_the_console_was_published(self) -> None:
        assert hsts.header_for("never", secure=True, host=NAME, published=NAME) is None

    @pytest.mark.parametrize("junk", ["", "yes", "ALWAYS ", None, 7])
    def test_a_value_nobody_validated_is_taken_as_automatic(self, junk: object) -> None:
        assert hsts.header_for(junk, secure=True, host="nas.example.net", published=None) is None
        assert hsts.header_for(junk, secure=True, host=NAME, published=NAME) == hsts.VALUE


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture
def config(real_config: Config, store: NoustStore) -> Config:
    return real_config


def publish(store: NoustStore, name: str = NAME, state: str = STATE_ACTIVE) -> None:
    ConsoleExposureStore(store).save(ExposureState(domain=name, port=8443, state=state))


def over_tls(sandbox: Path, host: str = NAME) -> TestClient:
    return TestClient(
        create_app(make_config(sandbox)),
        base_url=f"https://{host}",
        client=("203.0.113.9", 50000),
    )


class TestWhatTheConsoleSends:
    def test_a_console_nobody_published_sends_nothing(
        self, sandbox: Path, config: Config, store: NoustStore
    ) -> None:
        response = over_tls(sandbox, "nas.example.net").get("/health")

        assert "Strict-Transport-Security" not in response.headers

    def test_a_published_console_sends_it_on_its_own_name_and_only_there(
        self, sandbox: Path, config: Config, store: NoustStore
    ) -> None:
        publish(store)

        on_the_name = over_tls(sandbox, NAME).get("/health")
        on_the_address = over_tls(sandbox, "192.0.2.10").get("/health")

        assert on_the_name.headers["Strict-Transport-Security"] == "max-age=31536000"
        assert "Strict-Transport-Security" not in on_the_address.headers

    def test_a_published_console_behind_its_proxy_sends_it_too(
        self, sandbox: Path, config: Config, store: NoustStore
    ) -> None:
        publish(store)
        client = TestClient(
            create_app(make_config(sandbox, trusted_proxies=["127.0.0.1"])),
            client=("127.0.0.1", 50000),
        )

        response = client.get("/health", headers={"Host": NAME, "X-Forwarded-Proto": "https"})

        assert response.headers["Strict-Transport-Security"] == "max-age=31536000"

    def test_an_exposure_that_did_not_finish_is_not_a_published_console(
        self, sandbox: Path, config: Config, store: NoustStore
    ) -> None:
        publish(store, state=STATE_PENDING)

        assert "Strict-Transport-Security" not in over_tls(sandbox).get("/health").headers

    def test_unexposing_takes_effect_without_a_restart(
        self, sandbox: Path, config: Config, store: NoustStore
    ) -> None:
        publish(store)
        client = over_tls(sandbox)
        assert "Strict-Transport-Security" in client.get("/health").headers

        ConsoleExposureStore(store).clear()

        assert "Strict-Transport-Security" not in client.get("/health").headers

    def test_forced_it_is_sent_on_any_name_over_tls(
        self, sandbox: Path, config: Config, store: NoustStore
    ) -> None:
        config.set("web.hsts", "always")

        response = over_tls(sandbox, "nas.example.net").get("/health")

        assert response.headers["Strict-Transport-Security"] == "max-age=31536000"

    def test_forced_it_is_still_never_sent_in_cleartext(
        self, sandbox: Path, config: Config, store: NoustStore
    ) -> None:
        config.set("web.hsts", "always")
        client = TestClient(create_app(make_config(sandbox)), client=("203.0.113.9", 50000))

        assert "Strict-Transport-Security" not in client.get("/health").headers

    def test_turned_off_it_is_not_sent_even_on_a_published_name(
        self, sandbox: Path, config: Config, store: NoustStore
    ) -> None:
        publish(store)
        config.set("web.hsts", "never")

        assert "Strict-Transport-Security" not in over_tls(sandbox).get("/health").headers

    @pytest.mark.parametrize("mode", ["auto", "always"])
    def test_no_case_ever_binds_the_subdomains(
        self, sandbox: Path, config: Config, store: NoustStore, mode: str
    ) -> None:
        publish(store)
        config.set("web.hsts", mode)

        sent = over_tls(sandbox).get("/health").headers["Strict-Transport-Security"]

        assert "includeSubDomains" not in sent
        assert "preload" not in sent

    def test_a_record_that_cannot_be_read_sends_nothing_and_does_not_break_the_answer(
        self, sandbox: Path, config: Config, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(self: ConsoleExposureStore) -> ExposureState | None:
            raise StoreError("the store cannot be read")

        monkeypatch.setattr(ConsoleExposureStore, "get", broken)

        response = over_tls(sandbox).get("/health")

        assert response.status_code == 200
        assert "Strict-Transport-Security" not in response.headers

    def test_the_answer_of_a_refusal_follows_the_same_rule(
        self, sandbox: Path, config: Config, store: NoustStore
    ) -> None:
        publish(store)
        client = TestClient(
            create_app(make_config(sandbox, ip_whitelist=["10.9.9.9"])),
            base_url=f"https://{NAME}",
            client=("203.0.113.9", 50000),
        )

        response = client.get("/health")

        assert response.status_code == 403
        assert response.headers["Strict-Transport-Security"] == "max-age=31536000"


class TestTheSetting:
    def test_it_is_automatic_unless_somebody_chose(self, config: Config) -> None:
        assert config.get("web.hsts") == "auto"

    @pytest.mark.parametrize("word", ["auto", "always", "never"])
    def test_the_three_words_are_accepted(self, config: Config, word: str) -> None:
        config.set("web.hsts", word)

        assert config.get("web.hsts") == word

    @pytest.mark.parametrize("word", ["", "yes", "true", "on", "includeSubDomains", "max-age=0"])
    def test_anything_else_is_refused_with_what_to_write(self, config: Config, word: str) -> None:
        with pytest.raises(ConfigError, match=r"web\.hsts"):
            config.set("web.hsts", word)

    def test_the_console_reads_it_where_it_decides(self, config: Config) -> None:
        config.set("web.hsts", "never")

        assert console_hsts.mode() == "never"
