"""
Tests for the list of time zones the server offers (item 54).

The zone database is built here, not read from the machine running the tests: a
TZif file with no transitions and a POSIX rule in its footer is exactly what the
tz database ships for a zone that has had the same rules for a long time, and
it makes Europe/Madrid UTC+01:00 in January and UTC+02:00 in July wherever the
tests run, with or without tzdata installed.
"""

from __future__ import annotations

import struct
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.fs import RecordingFileSystem
from noust.core.store import NoustStore
from noust.managers.server.clock import ClockManager, TimezoneInfo, format_offset
from noust.web.api import server as server_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers
from noust.web.api.server.common import set_server_context
from noust.web.permissions.registry import route_map
from tests.server_support import make_host, make_machine, platform_for

#: Midwinter and midsummer in the northern hemisphere, both clear of any change.
JANUARY = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
JULY = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)

#: name -> (standard offset in seconds, standard abbreviation, POSIX rule).
ZONES: dict[str, tuple[int, str, str]] = {
    "Etc/UTC": (0, "UTC", "UTC0"),
    "UTC": (0, "UTC", "UTC0"),
    "Europe/Madrid": (3600, "CET", "CET-1CEST,M3.5.0,M10.5.0/3"),
    "Europe/London": (0, "GMT", "GMT0BST,M3.5.0/1,M10.5.0"),
    "Asia/Kolkata": (19800, "IST", "IST-5:30"),
    "America/St_Johns": (-12600, "NST", "NST3:30NDT,M3.2.0,M11.1.0"),
    "America/Argentina/Buenos_Aires": (-10800, "-03", "<-03>3"),
    "America/New_York": (-18000, "EST", "EST5EDT,M3.2.0,M11.1.0"),
    "Pacific/Kiritimati": (50400, "+14", "<+14>-14"),
    "Etc/GMT+12": (-43200, "-12", "<-12>12"),
}


def tzif(offset: int, abbreviation: str, rule: str) -> bytes:
    """
    Build a TZif version 2 file with one local time type and a POSIX rule.

    Args:
        offset: Standard offset from UTC, in seconds.
        abbreviation: Its abbreviation.
        rule: The POSIX TZ string for the footer, which carries any daylight saving.

    Returns:
        The file's bytes.
    """
    chars = abbreviation.encode() + b"\0"

    def block() -> bytes:
        header = b"TZif2" + b"\0" * 15 + struct.pack(">6l", 0, 0, 0, 0, 1, len(chars))
        return header + struct.pack(">lBB", offset, 0, 0) + chars

    return block() + block() + b"\n" + rule.encode() + b"\n"


def install_zones(host, names: list[str] | None = None) -> None:
    """
    Write the zone database under the fake system root.

    Args:
        host: The fake system paths.
        names: Which of :data:`ZONES` to write; all when omitted.
    """
    for name in names or list(ZONES):
        offset, abbreviation, rule = ZONES[name]
        path = host.at(f"/usr/share/zoneinfo/{name}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(tzif(offset, abbreviation, rule))


@pytest.fixture
def host(tmp_path: Path):
    return make_host(tmp_path / "root")


def manager(host) -> ClockManager:
    return ClockManager(
        runner=None, fs=RecordingFileSystem(), host=host, platform=platform_for("apt")
    )


def known_to_python(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """Make ``zoneinfo`` know exactly these names, as it does on a machine with tzdata."""
    monkeypatch.setattr("zoneinfo.available_timezones", lambda: set(names))


def by_name(zones: list[TimezoneInfo]) -> dict[str, TimezoneInfo]:
    return {zone.name: zone for zone in zones}


class TestWhichZonesAreOffered:
    def test_the_obsolete_copies_and_aliases_are_left_out(
        self, host, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_zones(host)
        known_to_python(
            monkeypatch,
            *ZONES,
            "posix/Europe/Madrid",
            "right/Europe/Madrid",
            "SystemV/AST4",
            "EST5EDT",
            "CET",
            "GMT",
            "MST7MDT",
            "Factory",
            "localtime",
            "posixrules",
            "US/Eastern",
            "Brazil/East",
            "Etc/Zulu",
            "Etc/GMT+0",
            "Etc/GMT-0",
            "Etc/GMT0",
            "Etc/Greenwich",
            "Etc/UCT",
            "Etc/Universal",
        )

        names = {zone.name for zone in manager(host).timezones(now=JANUARY)}

        assert names == set(ZONES)

    def test_a_name_that_could_walk_out_of_the_directory_is_not_offered(
        self, host, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_zones(host, ["Europe/Madrid"])
        known_to_python(monkeypatch, "Europe/Madrid", "Europe/../../etc/passwd", "Europe//Madrid")

        names = [zone.name for zone in manager(host).timezones(now=JANUARY)]

        assert names == ["Europe/Madrid"]

    def test_without_the_python_database_the_systems_directory_is_read(
        self, host, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_zones(host)
        zoneinfo_dir = host.at("/usr/share/zoneinfo")
        # What a real /usr/share/zoneinfo has besides zones.
        (zoneinfo_dir / "tzdata.zi").write_text("# version 2025b\n")
        (zoneinfo_dir / "zone.tab").write_text("# not a zone\n")
        (zoneinfo_dir / "posix/Europe").mkdir(parents=True)
        (zoneinfo_dir / "posix/Europe/Madrid").write_bytes(tzif(3600, "CET", "CET-1CEST"))
        (zoneinfo_dir / "EST5EDT").write_bytes(tzif(-18000, "EST", "EST5EDT"))
        known_to_python(monkeypatch)

        names = {zone.name for zone in manager(host).timezones(now=JANUARY)}

        assert names == set(ZONES)

    def test_a_file_that_is_not_a_zone_is_skipped_and_the_rest_still_answer(
        self, host, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_zones(host, ["Europe/Madrid", "Asia/Kolkata"])
        broken = host.at("/usr/share/zoneinfo/Europe/Paris")
        broken.write_text("this is not a TZif file")
        known_to_python(monkeypatch, "Europe/Madrid", "Asia/Kolkata", "Europe/Paris")

        names = {zone.name for zone in manager(host).timezones(now=JANUARY)}

        assert names == {"Europe/Madrid", "Asia/Kolkata"}


class TestWhatEachZoneSays:
    @pytest.fixture
    def zones(self, host, monkeypatch: pytest.MonkeyPatch):
        install_zones(host)
        known_to_python(monkeypatch, *ZONES)
        return lambda now: by_name(manager(host).timezones(now=now))

    def test_madrid_is_one_hour_ahead_in_winter_and_two_in_summer(self, zones) -> None:
        winter = zones(JANUARY)["Europe/Madrid"]
        summer = zones(JULY)["Europe/Madrid"]

        assert (winter.offset, winter.offset_minutes, winter.abbreviation) == (
            "UTC+01:00",
            60,
            "CET",
        )
        assert (summer.offset, summer.offset_minutes, summer.abbreviation) == (
            "UTC+02:00",
            120,
            "CEST",
        )

    def test_half_hour_zones_keep_their_minutes_and_a_negative_sign(self, zones) -> None:
        found = zones(JANUARY)

        assert found["Asia/Kolkata"].offset == "UTC+05:30"
        assert found["America/St_Johns"].offset == "UTC-03:30"
        assert found["America/St_Johns"].offset_minutes == -210
        assert found["Pacific/Kiritimati"].offset == "UTC+14:00"

    def test_zero_is_plain_utc_and_london_leaves_it_for_summer(self, zones) -> None:
        found = zones(JULY)

        assert found["Etc/UTC"].offset == "UTC"
        assert found["Etc/UTC"].offset_minutes == 0
        assert found["Etc/UTC"].abbreviation == "UTC"
        assert zones(JANUARY)["Europe/London"].offset == "UTC"
        assert found["Europe/London"].offset == "UTC+01:00"
        assert found["Europe/London"].abbreviation == "BST"

    def test_a_zone_the_database_only_numbers_has_no_abbreviation(self, zones) -> None:
        found = zones(JANUARY)

        assert found["America/Argentina/Buenos_Aires"].abbreviation == ""
        assert found["Pacific/Kiritimati"].abbreviation == ""

    def test_region_and_a_readable_city(self, zones) -> None:
        found = zones(JANUARY)

        assert (found["Europe/Madrid"].region, found["Europe/Madrid"].city) == (
            "Europe",
            "Madrid",
        )
        assert found["America/Argentina/Buenos_Aires"].region == "America"
        assert found["America/Argentina/Buenos_Aires"].city == "Argentina / Buenos Aires"
        assert found["America/St_Johns"].city == "St Johns"
        assert (found["Etc/GMT+12"].region, found["Etc/GMT+12"].city) == ("Etc", "GMT+12")
        assert (found["UTC"].region, found["UTC"].city) == ("UTC", "UTC")

    def test_the_offsets_follow_the_instant_given_not_the_one_the_server_has(self, zones) -> None:
        assert zones(JANUARY)["Europe/Madrid"].offset != zones(JULY)["Europe/Madrid"].offset

    def test_a_naive_instant_is_taken_as_utc(self, host, monkeypatch: pytest.MonkeyPatch) -> None:
        install_zones(host, ["Europe/Madrid"])
        known_to_python(monkeypatch, "Europe/Madrid")

        zone = manager(host).timezones(now=datetime(2026, 7, 15, 12, 0))[0]

        assert zone.offset == "UTC+02:00"

    def test_without_an_instant_the_zones_are_read_now(
        self, host, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_zones(host, ["Europe/Madrid"])
        known_to_python(monkeypatch, "Europe/Madrid")

        zone = manager(host).timezones()[0]

        assert zone.offset in ("UTC+01:00", "UTC+02:00")


class TestOrder:
    def test_utc_comes_first_then_by_offset_and_by_name(
        self, host, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_zones(host)
        known_to_python(monkeypatch, *ZONES)

        names = [zone.name for zone in manager(host).timezones(now=JANUARY)]

        assert names[:2] == ["Etc/UTC", "UTC"]
        assert names[2:] == [
            "Etc/GMT+12",  # UTC-12:00
            "America/New_York",  # UTC-05:00
            "America/St_Johns",  # UTC-03:30
            "America/Argentina/Buenos_Aires",  # UTC-03:00
            "Europe/London",  # UTC
            "Europe/Madrid",  # UTC+01:00
            "Asia/Kolkata",  # UTC+05:30
            "Pacific/Kiritimati",  # UTC+14:00
        ]

    def test_zones_at_the_same_offset_are_ordered_by_name(
        self, host, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in ("Europe/Paris", "Europe/Berlin", "Europe/Madrid"):
            path = host.at(f"/usr/share/zoneinfo/{name}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(tzif(3600, "CET", "CET-1CEST,M3.5.0,M10.5.0/3"))
        known_to_python(monkeypatch, "Europe/Paris", "Europe/Berlin", "Europe/Madrid")

        names = [zone.name for zone in manager(host).timezones(now=JANUARY)]

        assert names == ["Europe/Berlin", "Europe/Madrid", "Europe/Paris"]


class TestOffsetLabel:
    @pytest.mark.parametrize(
        ("minutes", "label"),
        [
            (0, "UTC"),
            (60, "UTC+01:00"),
            (120, "UTC+02:00"),
            (330, "UTC+05:30"),
            (345, "UTC+05:45"),
            (-210, "UTC-03:30"),
            (-720, "UTC-12:00"),
            (840, "UTC+14:00"),
        ],
    )
    def test_the_label(self, minutes: int, label: str) -> None:
        assert format_offset(minutes) == label


@pytest.fixture
def store(tmp_path: Path):
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "api.db", fs=RecordingFileSystem())
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store):
    machine = make_machine(tmp_path, monkeypatch, store)
    install_zones(machine.host)
    known_to_python(monkeypatch, *ZONES, "posix/Europe/Madrid", "EST5EDT")
    set_server_context(machine.ctx)
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(server_api.router, prefix="/api/server")
    app.dependency_overrides[get_current_session] = lambda: {"sid": "operator", "type": "master"}
    try:
        yield types.SimpleNamespace(http=TestClient(app, raise_server_exceptions=False), app=app)
    finally:
        set_server_context(None)


class TestTheEndpoint:
    def test_it_lists_the_zones_of_the_managed_server(self, client) -> None:
        response = client.http.get("/api/server/clock/timezones")

        assert response.status_code == 200, response.text
        body = response.json()
        names = [zone["name"] for zone in body["timezones"]]
        assert names[:2] == ["Etc/UTC", "UTC"]
        assert set(names) == set(ZONES)
        assert "posix/Europe/Madrid" not in names
        assert datetime.fromisoformat(body["generated_at"]).tzinfo is not None
        madrid = next(zone for zone in body["timezones"] if zone["name"] == "Europe/Madrid")
        assert set(madrid) == {
            "name",
            "region",
            "city",
            "offset",
            "abbreviation",
            "offset_minutes",
        }
        assert madrid["region"] == "Europe"
        assert madrid["city"] == "Madrid"

    def test_reading_it_is_server_read_and_it_is_in_the_permission_map(self, client) -> None:
        assert route_map()[("GET", "/api/server/clock/timezones")] == "server.read"
        schema = client.app.openapi()
        assert "/api/server/clock/timezones" in schema["paths"]
