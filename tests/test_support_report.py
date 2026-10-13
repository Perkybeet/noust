# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The support report (checklist N-33): what an issue needs, with nothing secret in it.

Pinned here: every section is there and says why when it could not be read; the journal,
the units and the diagnoses are the real probes' words, masked twice (the secrets Noust
knows, and what only looks like one); ``hide_domains`` replaces domains, unit-style names
and the host's name consistently through the whole document; and a caller who may not
reveal secrets gets a report without the journal.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pytest

from noust.core.app_state import AppState
from noust.core.exceptions import NoustError
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.managers import self_update, support_report
from noust.managers.diagnose import Check, Diagnosis
from noust.managers.health import HealthCheck, HealthReport
from noust.managers.support_report import SupportReporter

DOMAIN = "shop.example.com"
OTHER = "blog.example.org"
SECRET = "s3cr3t-session-value-0451"


@pytest.fixture
def store(tmp_path: Path) -> Any:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    for domain in (DOMAIN, OTHER):
        instance.create_app(
            App(
                domain=domain,
                app_type="nodejs",
                port=3100,
                app_path=f"/srv/{domain}",
                env_vars={"SESSION_SECRET": SECRET} if domain == DOMAIN else {},
            )
        )
    yield instance
    NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def installed_by_apt(monkeypatch: pytest.MonkeyPatch) -> None:
    """How Noust was installed is detected once per process; the tests say it."""
    monkeypatch.setattr(self_update, "_DETECTED", {"method": "apt", "container": False})


def health() -> HealthReport:
    ok = HealthCheck("Disk Space", "42% used", "ok")
    return HealthReport(
        disk=ok,
        nginx=HealthCheck("Nginx", "running", "ok"),
        apache=HealthCheck("Apache", "not installed", "info"),
        applications=HealthCheck("Applications", "1/2 serving, 1 need attention", "warning"),
        certificates=HealthCheck("Certificates", "all valid", "ok"),
        memory=HealthCheck("Memory", "30% used", "ok"),
        warnings=[f"App '{OTHER}' - Failed"],
    )


def states(apps: list[App]) -> dict[str, AppState]:
    return {
        DOMAIN: AppState("Running", healthy=True),
        OTHER: AppState("Failed", healthy=False, detail="exited with status 1"),
    }


def diagnosed(domain: str) -> Diagnosis:
    return Diagnosis(
        domain=domain,
        verdict="down",
        probable_cause=f"{domain} crashes on start: Missing script: start",
        checks=(
            Check("unit", "fail", "the unit is failed", "\n".join(f"line {i}" for i in range(40))),
            Check("port", "ok", "the port answers"),
        ),
    )


def runner(journal: str = "") -> FakeRunner:
    return (
        FakeRunner()
        .script(["systemctl", "--version"], stdout="systemd 255 (255.4-1ubuntu8)\n+PAM +AUDIT\n")
        .script(
            ["systemctl", "list-units"],
            stdout=(
                "noust-web.service loaded active running Noust console\n"
                "shop-example-com.service loaded failed failed Shop\n"
            ),
        )
        .script(
            ["systemctl", "--failed"], stdout="shop-example-com.service loaded failed failed Shop\n"
        )
        .script(["journalctl"], stdout=journal)
    )


def reporter(store: NoustStore, fake: FakeRunner | None = None, **options: Any) -> SupportReporter:
    return SupportReporter(
        runner=fake or runner(),
        store=store,
        health=health,
        states=states,
        diagnosis=diagnosed,
        **options,
    )


def section(report: support_report.SupportReport, key: str) -> support_report.ReportSection:
    return next(s for s in report.sections if s.key == key)


# -- what is in it


def test_every_section_is_there_in_reading_order(store: NoustStore) -> None:
    report = reporter(store).build()

    assert [s.key for s in report.sections] == [
        "noust",
        "machine",
        "health",
        "applications",
        "units",
        "diagnosis",
        "journal",
    ]
    assert all(s.collected for s in report.sections)


def test_it_says_the_version_and_how_noust_was_installed(store: NoustStore) -> None:
    body = section(reporter(store).build(), "noust").body

    assert f"Noust:        {support_report.__version__}" in body
    assert "Installed by: apt" in body


def test_the_machine_section_says_where_it_stands_against_the_platform_table(
    store: NoustStore,
) -> None:
    body = section(reporter(store).build(), "machine").body

    assert "Platform:" in body
    assert "systemd:  systemd 255" in body


def test_the_health_section_has_the_checks_and_what_decides_the_verdict(store: NoustStore) -> None:
    body = section(reporter(store).build(), "health").body

    assert "Verdict: warning" in body
    assert "[ok] Disk Space: 42% used" in body
    assert f"warning: App '{OTHER}' - Failed" in body


def test_each_application_is_a_line_with_its_state(store: NoustStore) -> None:
    body = section(reporter(store).build(), "applications").body

    assert f"{OTHER}  type=nodejs  layout=inplace  Failed - exited with status 1" in body
    assert f"{DOMAIN}  type=nodejs  layout=inplace  Running" in body


def test_units_come_from_systemd_in_its_own_words(store: NoustStore) -> None:
    body = section(reporter(store).build(), "units").body

    assert "noust-web.service loaded active running Noust console" in body
    assert "Failed units:\n  shop-example-com.service loaded failed failed Shop" in body


def test_only_applications_that_need_attention_are_diagnosed_and_their_evidence_is_cut(
    store: NoustStore,
) -> None:
    body = section(reporter(store).build(), "diagnosis").body

    assert f"{OTHER}: down" in body
    assert "most likely: blog.example.org crashes on start" in body
    assert "[fail] unit: the unit is failed" in body
    assert "the port answers" not in body
    assert "line 11" in body and "line 12" not in body
    assert DOMAIN not in body


def test_a_server_where_everything_serves_has_nothing_to_diagnose(store: NoustStore) -> None:
    fine = SupportReporter(
        runner=runner(),
        store=store,
        health=health,
        states=lambda apps: {a.domain: AppState("Running", healthy=True) for a in apps},
        diagnosis=diagnosed,
    )

    assert section(fine.build(), "diagnosis").body == "Every application is serving."


def test_the_number_of_diagnoses_is_bounded(store: NoustStore) -> None:
    many = SupportReporter(
        runner=runner(),
        store=store,
        health=health,
        states=lambda apps: {a.domain: AppState("Failed", healthy=False) for a in apps},
        diagnosis=diagnosed,
        diagnose_limit=1,
    )

    body = section(many.build(), "diagnosis").body

    assert body.count(": down") == 1
    assert "(1 more need attention: noust diagnose DOMAIN)" in body


# -- the journal


def test_the_journal_of_the_console_and_the_monitor_is_read_with_the_asked_number_of_lines(
    store: NoustStore,
) -> None:
    fake = runner("2026-10-13T10:00:00+0000 web noust-web[1]: started")

    body = section(reporter(store, fake, journal_lines=25).build(), "journal").body

    assert "noust-web[1]: started" in body
    assert (
        "journalctl",
        "--unit",
        "noust-web.service",
        "--unit",
        "noust-monitor.service",
        "--lines",
        "25",
        "--no-pager",
        "--output",
        "short-iso",
    ) in fake.calls


def test_a_caller_who_may_not_read_the_journal_gets_a_report_that_says_so(
    store: NoustStore,
) -> None:
    fake = runner("secret journal line")

    report = reporter(store, fake, journal_left_out="it needs the right to reveal secrets").build()

    journal = section(report, "journal")
    assert not journal.collected
    assert journal.body == "(left out: it needs the right to reveal secrets)"
    assert not any(call[0] == "journalctl" for call in fake.calls)
    assert "secret journal line" not in report.to_markdown()


def test_a_journalctl_that_fails_is_reported_in_its_own_words(store: NoustStore) -> None:
    fake = runner().script(["journalctl"], exit_code=1, stderr="No journal files were found.")

    body = section(reporter(store, fake).build(), "journal").body

    assert body == "(journalctl failed: No journal files were found.)"


# -- nothing secret leaves


def test_a_secret_noust_knows_is_masked_wherever_it_appears(store: NoustStore) -> None:
    fake = runner(f"2026-10-13T10:00:00+0000 web shop[7]: session store opened with {SECRET}")

    markdown = reporter(store, fake).build().to_markdown()

    assert SECRET not in markdown
    assert "session store opened with" in markdown


def test_what_only_looks_like_a_secret_is_masked_too(store: NoustStore) -> None:
    token = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    fake = runner(
        "web other[9]: push failed\n"
        f"web other[9]: Authorization: Bearer {token}\n"
        "web other[9]: DATABASE_URL=postgres://app:hunter2hunter2@db:5432/shop\n"
        "web other[9]: SMTP_PASSWORD=correct-horse-battery\n"
    )

    markdown = reporter(store, fake).build().to_markdown()

    for leaked in (token, "hunter2hunter2", "correct-horse-battery"):
        assert leaked not in markdown
    assert "push failed" in markdown


def test_the_diagnosis_evidence_is_masked_as_well(store: NoustStore) -> None:
    def leaky(domain: str) -> Diagnosis:
        return Diagnosis(
            domain=domain,
            verdict="down",
            probable_cause=None,
            checks=(Check("journal", "fail", "crashed", f"API_TOKEN=abcdef0123456789 {SECRET}"),),
        )

    leak = SupportReporter(
        runner=runner(), store=store, health=health, states=states, diagnosis=leaky
    )

    markdown = leak.build().to_markdown()

    assert "abcdef0123456789" not in markdown
    assert SECRET not in markdown


# -- hiding names


def test_hide_domains_replaces_domains_unit_names_and_the_host_everywhere(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(socket, "gethostname", lambda: "vps-arenna-01")
    fake = runner(
        f"2026-10-13T10:00:00+0000 vps-arenna-01 shop-example-com[3]: listening for {DOMAIN}"
    )

    report = reporter(store, fake, hide_domains=True).build()
    markdown = report.to_markdown()

    for real in (DOMAIN, OTHER, "shop-example-com", "vps-arenna-01"):
        assert real not in markdown
    assert "app-2  type=nodejs" in markdown or "app-1  type=nodejs" in markdown
    assert "host shop" not in markdown
    # One name, one alias: the same application is the same alias in every section.
    assert markdown.count("app-2") >= 3
    assert report.hide_domains
    assert "replaced" in markdown.splitlines()[2]


def test_without_hide_domains_the_report_says_how_to_hide_them(store: NoustStore) -> None:
    markdown = reporter(store).build().to_markdown()

    assert DOMAIN in markdown
    assert "--hide-domains" in markdown


# -- a section that cannot be read


def test_a_collector_that_fails_leaves_the_others_standing_and_says_why(store: NoustStore) -> None:
    def broken() -> HealthReport:
        raise NoustError("the store is locked", details="Another noust is migrating it.")

    partial = SupportReporter(
        runner=runner(), store=store, health=broken, states=states, diagnosis=diagnosed
    )

    report = partial.build()

    failed = section(report, "health")
    assert not failed.collected
    assert failed.body.startswith("(not collected: the store is locked")
    assert "Another noust is migrating it." in failed.body
    assert section(report, "units").collected
    assert section(report, "applications").collected


def test_an_unreadable_store_still_gives_a_report(tmp_path: Path) -> None:
    class Broken:
        def list_apps(self) -> list[App]:
            raise NoustError("cannot open the store")

    blind = SupportReporter(
        runner=runner(),
        store=Broken(),
        health=health,
        states=lambda apps: {},
        diagnosis=diagnosed,  # type: ignore[arg-type]
    )

    report = blind.build()

    assert section(report, "applications").body == "No applications deployed."
    assert section(report, "units").collected


# -- the document


def test_output_is_fenced_with_more_backticks_than_it_contains(store: NoustStore) -> None:
    fake = runner("web x[1]: ran ```npm run build``` ok")

    markdown = reporter(store, fake).build().to_markdown()

    assert "````" in markdown
    assert "ran ```npm run build``` ok" in markdown


def test_the_json_form_carries_the_markdown_and_the_sections(store: NoustStore) -> None:
    data = reporter(store).build().to_dict()

    assert data["markdown"].startswith("# Noust support report")
    assert data["sections"][0]["key"] == "noust"
    assert data["hide_domains"] is False
    assert data["version"] == support_report.__version__
