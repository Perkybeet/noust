# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The public status page of the probes (checklist G-17).

What a visitor may see is defended here as much as what the page looks like:
only the probes marked public, by title and never by address, with no reason of
a failure, no latency and no count of checks; every value escaped; a page that
asks nothing of the browser. Publishing it on a name over TLS is held to the
console exposure's standard: it refuses what is not its to take, and taking it
off removes exactly what publishing made.
"""

from __future__ import annotations

import html
import json
import re
import stat
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.config import Config
from noust.core.exceptions import CertificateError, ConfigError, DomainError, SiteError
from noust.core.notification_state import NotificationState
from noust.core.probe_store import Probe, ProbeState, ProbeStore
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.managers import status_page
from noust.managers.status_page import (
    DATA,
    INDEX,
    StatusPage,
    StatusPageSite,
    bar_class,
    render_html,
    render_json,
    take_snapshot,
)
from noust.managers.webserver import NGINX_BACKEND, WebServerManager
from tests.test_console_exposure import FakeCerts

DAY = 86_400
NOW = 1_800_000_000.0
TODAY = int(NOW) - int(NOW) % DAY
SITE = "status.example.com"


class Clock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def probes(store: NoustStore) -> ProbeStore:
    return ProbeStore(store, clock=Clock())


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Config]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    Config.reset_instance()
    yield Config()
    Config.reset_instance()


def add(
    probes: ProbeStore,
    name: str,
    state: str = "up",
    *,
    public: bool = True,
    title: str = "",
    url: str = "https://secret.example.com/private?key=SECRET",
    enabled: bool = True,
    checks: tuple[int, int] = (0, 0),
) -> Probe:
    saved = probes.save(Probe(name=name, url=url, public=public, title=title, enabled=enabled))
    assert saved.id is not None
    if state != "unknown":
        probes.put_state(
            ProbeState(
                saved.id,
                state,
                NOW - 600,
                failing_since=NOW - 600 if state == "down" else None,
                last_error="HTTP 503 Service Unavailable from the inside",
                last_latency_ms=1234,
            )
        )
    total, good = checks
    for index in range(total):
        probes.record_result(
            saved.id,
            ok=index < good,
            status=200 if index < good else 503,
            latency_ms=1234,
            error=None,
            at=NOW - 3600,
        )
    return saved


class TestWhatTheSnapshotHolds:
    def test_only_enabled_public_probes_are_on_the_page(self, probes: ProbeStore) -> None:
        add(probes, "web", public=True)
        add(probes, "admin", public=False)
        add(probes, "old", public=True, enabled=False)

        found = take_snapshot(probes, "Status", NOW)

        assert [c.name for c in found.components] == ["web"]

    def test_a_component_is_known_by_its_title_and_then_by_its_name(
        self, probes: ProbeStore
    ) -> None:
        add(probes, "web", title="Website")
        add(probes, "api")

        found = take_snapshot(probes, "Status", NOW)

        assert [c.name for c in found.components] == ["api", "Website"]

    @pytest.mark.parametrize(
        ("states", "overall"),
        [
            ((), "unknown"),
            (("unknown",), "unknown"),
            (("up",), "operational"),
            (("up", "unknown"), "operational"),
            (("up", "down"), "degraded"),
            (("down",), "outage"),
            (("down", "down"), "outage"),
        ],
    )
    def test_the_state_of_the_page_follows_the_components(
        self, probes: ProbeStore, states: tuple[str, ...], overall: str
    ) -> None:
        for index, state in enumerate(states):
            add(probes, f"p{index}", state)

        assert take_snapshot(probes, "Status", NOW).overall == overall

    def test_ninety_days_of_bars_and_the_uptime(self, probes: ProbeStore) -> None:
        add(probes, "web", checks=(10, 9))

        (component,) = take_snapshot(probes, "Status", NOW).components

        assert len(component.days) == 90
        assert component.uptime == 90.0
        assert component.days[-1].checks == 10

    def test_the_bar_of_a_day_follows_how_often_it_answered(self) -> None:
        day = status_page.DayBar
        assert bar_class(day(0, 0, 0)) == "none"
        assert bar_class(day(0, 1000, 1000)) == "good"
        assert bar_class(day(0, 1000, 999)) == "good"  # 99.9%
        assert bar_class(day(0, 1000, 990)) == "warn"
        assert bar_class(day(0, 100, 50)) == "bad"


class TestWhatAVisitorSees:
    def page(self, probes: ProbeStore, locale: Any = "en") -> str:
        add(probes, "web", "down", title="Website", checks=(10, 5))
        add(probes, "api", "up", checks=(10, 10))
        probes.open_incident(
            probes.get("web").id, NOW - 600, "HTTP 503 Service Unavailable from the inside"
        )  # type: ignore[union-attr]
        return render_html(take_snapshot(probes, "Acme status", NOW), locale)

    def test_the_page_names_the_state_of_each_thing_in_words_and_a_shape(
        self, probes: ProbeStore
    ) -> None:
        text = self.page(probes)

        assert "Website" in text and "api" in text
        assert "✕ Not answering" in text and "● Operational" in text
        assert "▲ Some systems are not answering" in text

    def test_nothing_of_the_inside_is_on_the_page(self, probes: ProbeStore) -> None:
        text = self.page(probes)

        for secret in (
            "secret.example.com",
            "SECRET",
            "private",
            "1234",
            "Service Unavailable",
            "503",
        ):
            assert secret not in text, secret

    def test_the_incident_says_when_and_for_how_long_and_nothing_more(
        self, probes: ProbeStore
    ) -> None:
        text = self.page(probes)

        assert "Recent incidents" in text
        assert re.search(r"2027-01-\d\d \d\d:\d\d UTC · ongoing", text)

    def test_a_title_with_markup_in_it_is_text(self, probes: ProbeStore) -> None:
        add(probes, "web", title='<img src=x onerror="alert(1)">&"\'')

        text = render_html(take_snapshot(probes, "</title><script>alert(1)</script>", NOW), "en")

        assert "<script" not in text and "<img" not in text
        assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;&amp;&quot;&#x27;" in text

    def test_the_page_asks_nothing_of_the_browser(self, probes: ProbeStore) -> None:
        text = self.page(probes)

        assert "<script" not in text
        assert not re.search(r"(src|href)=", text)
        policy = html.unescape(text)
        assert "default-src 'none'" in policy
        assert "style-src 'unsafe-inline'" in policy
        assert 'name="robots" content="noindex"' in text

    def test_there_is_one_bar_for_each_of_ninety_days(self, probes: ProbeStore) -> None:
        text = self.page(probes)

        assert text.count('<i class="') == 2 * 90

    def test_it_speaks_spanish_when_asked_to(self, probes: ProbeStore) -> None:
        text = self.page(probes, "es")

        assert 'lang="es"' in text
        assert "Sin respuesta" in text and "Operativo" in text
        assert "Incidencias recientes" in text

    def test_an_empty_page_says_so(self, probes: ProbeStore) -> None:
        text = render_html(take_snapshot(probes, "Status", NOW), "en")

        assert "Nothing is published here yet." in text and "No data yet" in text


class TestTheDataFile:
    def test_it_is_the_same_thing_for_a_program(self, probes: ProbeStore) -> None:
        add(probes, "web", "up", title="Website", checks=(4, 4))

        document = json.loads(render_json(take_snapshot(probes, "Acme", NOW)))

        assert document["status"] == "operational" and document["title"] == "Acme"
        (component,) = document["components"]
        assert component["name"] == "Website" and component["status"] == "up"
        assert component["uptime_percent"] == 100.0
        assert len(component["days"]) == 90

    def test_it_holds_no_address_reason_or_latency(self, probes: ProbeStore) -> None:
        add(probes, "web", "down", checks=(3, 1))

        text = render_json(take_snapshot(probes, "Acme", NOW))

        for secret in ("secret.example.com", "SECRET", "503", "1234", "Service Unavailable"):
            assert secret not in text, secret


class TestTheFiles:
    def test_the_page_and_its_data_are_written_where_the_configuration_says(
        self, probes: ProbeStore, config_file: Config, tmp_path: Path
    ) -> None:
        add(probes, "web")
        config_file.set("status_page.directory", str(tmp_path / "public"))
        config_file.set("status_page.title", "Acme status")

        written = StatusPage(probes, config=config_file, clock=Clock()).write()

        assert [p.name for p in written] == [INDEX, DATA]
        assert "<title>Acme status</title>" in (tmp_path / "public" / INDEX).read_text()
        assert json.loads((tmp_path / "public" / DATA).read_text())["title"] == "Acme status"
        assert stat.S_IMODE((tmp_path / "public" / INDEX).stat().st_mode) == 0o644

    def test_the_default_directory_is_the_states(
        self, probes: ProbeStore, config_file: Config
    ) -> None:
        page = StatusPage(probes, config=config_file)

        assert page.directory.name == "status-page"
        assert page.title == "Status" and page.enabled is False

    def test_a_page_is_written_in_the_configured_language(
        self, probes: ProbeStore, config_file: Config, tmp_path: Path
    ) -> None:
        add(probes, "web")
        config_file.set("status_page.directory", str(tmp_path / "public"))
        config_file.set("notifications.language", "es")

        StatusPage(probes, config=config_file, clock=Clock()).write()

        assert 'lang="es"' in (tmp_path / "public" / INDEX).read_text()

    def test_a_link_where_a_file_goes_is_replaced_not_followed(
        self, probes: ProbeStore, config_file: Config, tmp_path: Path
    ) -> None:
        elsewhere = tmp_path / "elsewhere"
        elsewhere.write_text("do not touch")
        public = tmp_path / "public"
        public.mkdir()
        (public / INDEX).symlink_to(elsewhere)
        config_file.set("status_page.directory", str(public))

        StatusPage(probes, config=config_file, clock=Clock()).write()

        assert elsewhere.read_text() == "do not touch"
        assert not (public / INDEX).is_symlink()

    def test_removing_it_leaves_the_directory(
        self, probes: ProbeStore, config_file: Config, tmp_path: Path
    ) -> None:
        config_file.set("status_page.directory", str(tmp_path / "public"))
        page = StatusPage(probes, config=config_file, clock=Clock())
        page.write()

        page.remove()

        assert (tmp_path / "public").is_dir() and not list((tmp_path / "public").iterdir())


class TestTheSettings:
    @pytest.mark.parametrize("value", ["relative/dir", "/var/../etc", "/ok/../../x"])
    def test_a_directory_must_be_absolute_and_not_climb(
        self, config_file: Config, value: str
    ) -> None:
        with pytest.raises(ConfigError, match=r"status_page\.directory"):
            config_file.set("status_page.directory", value)

    def test_an_empty_directory_is_the_default(self, config_file: Config) -> None:
        config_file.set("status_page.directory", "")

        assert config_file.get("status_page.directory") == ""

    def test_a_title_is_bounded(self, config_file: Config) -> None:
        with pytest.raises(ConfigError, match=r"status_page\.title"):
            config_file.set("status_page.title", "x" * 81)


# ---- publishing it on a name over TLS ---------------------------------------


@pytest.fixture
def nginx(tmp_path: Path, store: NoustStore) -> WebServerManager:
    return WebServerManager(
        replace(
            NGINX_BACKEND,
            sites_available=tmp_path / "nginx/sites-available",
            sites_enabled=tmp_path / "nginx/sites-enabled",
        ),
        runner=FakeRunner(),
    )


@pytest.fixture
def certs() -> FakeCerts:
    return FakeCerts()


def points_here(_domain: str) -> SimpleNamespace:
    return SimpleNamespace(
        points_here=True, resolved_addresses=("203.0.113.5",), expected_addresses=()
    )


@pytest.fixture
def site(
    probes: ProbeStore,
    store: NoustStore,
    config_file: Config,
    nginx: WebServerManager,
    certs: FakeCerts,
    tmp_path: Path,
) -> StatusPageSite:
    config_file.set("status_page.directory", str(tmp_path / "public"))
    add(probes, "web")
    return StatusPageSite(
        StatusPage(probes, config=config_file, clock=Clock()),
        state=NotificationState(store),
        nginx=nginx,
        certs=certs,
        dns_check=points_here,
    )


class TestPublishing:
    def test_it_writes_the_files_the_site_and_the_certificate(
        self, site: StatusPageSite, nginx: WebServerManager, certs: FakeCerts, tmp_path: Path
    ) -> None:
        record = site.publish(SITE)

        assert record.domain == SITE and record.site_created and record.certificate_created
        assert (tmp_path / "public" / INDEX).is_file()
        config = nginx.get_site_config(SITE) or ""
        assert f"root {tmp_path / 'public'};" in config
        assert "ssl_certificate" in config
        assert certs.obtained == [SITE]
        assert site.record() == record

    def test_a_name_that_is_an_applications_is_refused_before_anything_is_written(
        self, site: StatusPageSite, store: NoustStore, nginx: WebServerManager
    ) -> None:
        from noust.core.store import App

        store.create_app(App(domain=SITE, app_type="static"))

        with pytest.raises(DomainError, match="belongs to the application"):
            site.publish(SITE)

        assert not nginx.site_exists(SITE)

    def test_a_site_somebody_else_wrote_on_the_name_is_never_touched(
        self, site: StatusPageSite, nginx: WebServerManager
    ) -> None:
        nginx.create_site(
            SITE, template="proxy", context={"port": 3000, "ssl": False, "server_names": SITE}
        )

        with pytest.raises(SiteError, match="Noust did not make"):
            site.publish(SITE)

        assert "proxy_pass" in (nginx.get_site_config(SITE) or "")

    def test_a_name_that_does_not_point_here_is_refused(
        self, site: StatusPageSite, nginx: WebServerManager
    ) -> None:
        site._dns_check = lambda _d: SimpleNamespace(  # type: ignore[assignment]
            points_here=False,
            resolved_addresses=("198.51.100.1",),
            expected_addresses=("203.0.113.5",),
        )

        with pytest.raises(DomainError, match="does not point at this server"):
            site.publish(SITE)

        assert not nginx.site_exists(SITE)

    def test_that_check_can_be_skipped_behind_nat(self, site: StatusPageSite) -> None:
        site._dns_check = lambda _d: SimpleNamespace(
            points_here=False, resolved_addresses=(), expected_addresses=()
        )  # type: ignore[assignment]

        assert site.publish(SITE, skip_dns_check=True).domain == SITE

    def test_a_certificate_that_cannot_be_obtained_leaves_nothing_behind(
        self, site: StatusPageSite, nginx: WebServerManager
    ) -> None:
        site._certs = FakeCerts(fail=True)

        with pytest.raises(CertificateError):
            site.publish(SITE)

        assert not nginx.site_exists(SITE)
        assert site.record() is None

    def test_another_name_cannot_take_the_page_while_it_is_published(
        self, site: StatusPageSite
    ) -> None:
        site.publish(SITE)

        with pytest.raises(SiteError, match="already published"):
            site.publish("other.example.com")

    def test_publishing_again_on_the_same_name_is_fine(
        self, site: StatusPageSite, certs: FakeCerts
    ) -> None:
        site.publish(SITE)

        again = site.publish(SITE)

        assert again.domain == SITE and certs.obtained == [SITE]

    def test_taking_it_off_removes_exactly_what_was_made(
        self, site: StatusPageSite, nginx: WebServerManager, certs: FakeCerts
    ) -> None:
        site.publish(SITE)

        removed = site.unpublish()

        assert removed == [f"nginx site of {SITE}", f"certificate of {SITE}"]
        assert not nginx.site_exists(SITE) and certs.deleted == [SITE]
        assert site.record() is None

    def test_a_certificate_that_was_already_there_is_left(
        self, site: StatusPageSite, nginx: WebServerManager
    ) -> None:
        site._certs = FakeCerts(already=True)
        site._certs.existing = {SITE}

        record = site.publish(SITE)
        removed = site.unpublish()

        assert record.certificate_created is False
        assert removed == [f"nginx site of {SITE}"]
        assert site._certs.deleted == []

    def test_taking_off_what_was_never_published_is_a_no_op(self, site: StatusPageSite) -> None:
        assert site.unpublish() == []
