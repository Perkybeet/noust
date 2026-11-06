# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Every background job says what it is as one sentence of the catalog.

A job's ``name`` is English the call site wrote; the Activity page, the notices and the console's
tray each kept a table of their own to say it in words, and 19 of the 35 types had no entry
anywhere. ``noust.core.job_titles.title_for`` is the one place that decides, from what a job is
(its type and what it knows of its subject), the sentence of :mod:`noust.core.server_text` that
names it. The console says it in its own language from the code, and every client gets the same
words. A job that is stored keeps its type and its application, so the same function says it for
history as for what is running.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from noust.core import job_titles, server_text
from noust.core.server_text import CATALOG, ServerText
from noust.web.jobs import Job, JobType
from tests.support.console_catalog import CONSOLE_CATALOGS, sentences_of


def title(job_type: str, metadata: dict[str, Any] | None = None, name: str = "") -> ServerText:
    return job_titles.title_for(job_type, metadata or {}, name)


class TestEveryTypeHasATitle:
    @pytest.mark.parametrize("job_type", [t.value for t in JobType])
    def test_a_job_of_the_type_is_named_even_when_it_knows_nothing_of_its_subject(
        self, job_type: str
    ) -> None:
        sentence = title(job_type)

        assert sentence.code in CATALOG
        assert sentence.code.startswith("jobs.title.")
        assert sentence.params == {}

    def test_the_table_has_no_type_that_is_not_a_job_type(self) -> None:
        assert set(job_titles.TITLES) == {t.value for t in JobType}

    def test_a_type_nobody_knows_is_a_task_and_not_an_error(self) -> None:
        # A stored job of a newer Noust, read by an older one.
        assert title("from_the_future").code == "jobs.title.custom"

    def test_the_last_way_of_every_type_needs_nothing_so_something_is_always_said(self) -> None:
        for job_type, ways in job_titles.TITLES.items():
            assert ways[-1].takes == () and ways[-1].when == (), job_type

    def test_every_way_is_a_sentence_of_the_catalog_with_the_values_it_takes(self) -> None:
        for job_type, ways in job_titles.TITLES.items():
            for way in ways:
                assert way.code in CATALOG, (job_type, way.code)
                assert server_text.values_of(way.code) == {name for name, _ in way.takes}, way.code

    def test_no_sentence_of_a_title_is_the_beginning_of_another_in_a_tree(self) -> None:
        codes = sorted({way.code for ways in job_titles.TITLES.values() for way in ways})
        assert [c for c in codes if any(o.startswith(c + ".") for o in codes)] == []

    def test_the_catalog_holds_no_title_that_no_type_says(self) -> None:
        said = {way.code for ways in job_titles.TITLES.values() for way in ways}
        held = {code for code in CATALOG if code.startswith("jobs.title.")}

        assert held == said


class TestWhatASubjectChanges:
    def test_the_application_a_job_is_about_is_in_its_sentence(self) -> None:
        sentence = title("deploy", {"domain": "shop.example.com"})

        assert sentence.code == "jobs.title.deploy"
        assert sentence.params == {"domain": "shop.example.com"}
        assert sentence.text == "Deploy shop.example.com"

    @pytest.mark.parametrize(
        ("job_type", "expected"),
        [
            ("update", "jobs.title.update"),
            ("backup", "jobs.title.backup"),
            ("restore", "jobs.title.restore"),
            ("rollback", "jobs.title.rollback"),
            ("delete", "jobs.title.delete"),
            ("migrate", "jobs.title.migrate"),
            ("zero_downtime", "jobs.title.zero_downtime"),
            ("sandbox_test", "jobs.title.sandbox_test"),
            ("identity_migrate", "jobs.title.identity_migrate"),
            ("hardening", "jobs.title.hardening"),
            ("tuning", "jobs.title.tuning"),
            ("disk", "jobs.title.disk"),
            ("replicas", "jobs.title.replicas"),
            ("app_exec", "jobs.title.app_exec"),
            ("cert_create", "jobs.title.cert_create"),
            ("cert_renew", "jobs.title.cert_renew"),
        ],
    )
    def test_a_type_about_an_application_names_it(self, job_type: str, expected: str) -> None:
        sentence = title(job_type, {"domain": "shop.example.com"})

        assert sentence.code == expected
        assert sentence.params == {"domain": "shop.example.com"}
        assert "shop.example.com" in sentence.text

    def test_a_subject_that_is_not_text_is_not_put_in_a_sentence(self) -> None:
        for bad in (None, "", ["a"], {"a": 1}, b"x"):
            assert title("deploy", {"domain": bad}).code == "jobs.title.deploy_any"

    def test_the_two_names_that_are_not_domains_are_told_apart(self) -> None:
        # ``all`` renews every certificate and ``acme`` registers the account: neither is a domain.
        assert title("cert_renew", {"domain": "all"}).code == "jobs.title.cert_renew_all"
        assert title("cert_create", {"domain": "acme"}).code == "jobs.title.cert_acme_account"
        assert title("cert_create", {"domain": "shop.example.com"}).code == "jobs.title.cert_create"

    def test_where_a_backup_goes_and_what_a_site_does_are_said_when_known(self) -> None:
        assert title("push", {"destination": "offsite"}).params == {"destination": "offsite"}
        assert title("push").code == "jobs.title.push_any"
        assert (
            title("site_action", {"kind": "console_expose", "console_domain": "c.example.com"}).code
            == "jobs.title.site_console_expose"
        )
        assert title("site_action", {"webserver": "apache", "domains": ["a"]}).params == {
            "webserver": "apache"
        }
        assert title("swap", {"action": "create"}).code == "jobs.title.swap_create"
        assert title("swap", {"action": "remove"}).code == "jobs.title.swap_remove"
        assert title("swap", {"action": "other"}).code == "jobs.title.swap"
        assert (
            title("server_action", {"kind": "dns_server_setup", "role": "primary"}).text
            == "Set up Noust DNS as a primary"
        )

    def test_an_install_and_a_removal_from_the_software_page_name_their_component(self) -> None:
        install = title("server_action", {"component": "nginx_stream_module", "action": "install"})
        remove = title("server_action", {"component": "fail2ban", "action": "remove"})

        assert (install.code, install.params) == (
            "jobs.title.server_action_software_install",
            {"component": "nginx_stream_module"},
        )
        assert remove.text == "Remove fail2ban"
        # A server action that is neither, or that does not know its component, stays a server task.
        assert title("server_action", {"component": "fail2ban"}).code == "jobs.title.server_action"
        assert title("server_action", {"action": "install"}).code == "jobs.title.server_action"

    def test_a_fleet_job_keeps_the_sentence_its_name_already_is(self) -> None:
        from noust.core.fleet_job_titles import title as fleet_title

        named = fleet_title("certs_renew", 3)
        assert named is not None

        sentence = title("fleet", {"action": "certs_renew"}, named.text)

        assert sentence.code == named.code and sentence.params == named.params
        assert (
            title("fleet", {"action": "certs_renew"}, "Something else").code == "jobs.title.fleet"
        )


class TestWhereATitleTravels:
    def test_a_job_carries_its_title_beside_its_english_name(self) -> None:
        job = Job(
            id="abc12345",
            type=JobType.BACKUP,
            name="Backup shop.example.com",
            description="",
            metadata={"domain": "shop.example.com"},
        )

        data = job.to_dict()

        assert data["name"] == "Backup shop.example.com"
        assert data["title"] == {
            "code": "jobs.title.backup",
            "params": {"domain": "shop.example.com"},
        }

    def test_a_job_that_is_stored_is_titled_from_what_the_store_keeps(self) -> None:
        from noust.core.store import JobRecord
        from noust.web.api.jobs import _from_record

        record = JobRecord(
            id="job-1",
            type="rollback",
            name="Rollback shop.example.com",
            status="completed",
            domain="shop.example.com",
        )

        response = _from_record(record)

        assert response.title is not None
        assert response.title.code == "jobs.title.rollback"
        assert response.title.params == {"domain": "shop.example.com"}


class TestTheConsoleSaysEachTitle:
    @pytest.mark.parametrize("language", sorted(CONSOLE_CATALOGS))
    def test_every_title_has_a_text_in_the_console_with_the_values_the_server_sends(
        self, language: str
    ) -> None:
        catalog = sentences_of(CONSOLE_CATALOGS[language])
        codes = sorted(code for code in CATALOG if code.startswith("jobs.title."))
        assert codes

        missing = [code for code in codes if code not in catalog]
        wrong = [
            code
            for code in codes
            if code in catalog
            and set(re.findall(r"\{(\w+)\}", catalog[code])) != server_text.values_of(code)
        ]

        assert missing == [], f"{language} has no text for {missing}"
        assert wrong == [], (
            f"{language} says these with other values than the server sends: {wrong}"
        )

    def test_the_reader_finds_a_sentence_it_knows_is_there(self) -> None:
        found = sentences_of(CONSOLE_CATALOGS["English"])

        assert found["actors.through_central"] == "{person} (from the central {central})"
        assert found["tunnels.store_sealed"] == "The secret store is sealed"
