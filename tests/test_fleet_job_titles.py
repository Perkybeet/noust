# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The title of a fleet job is one of the catalog's sentences (``noust.core.fleet_job_titles``).

A central's bulk action runs as a job whose name is "<title> on N servers", and whatever lists
jobs showed that name as it was: English, in a Spanish console. The name is now the sentence of
the catalog, and what lists jobs asks for its code.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core import fleet_job_titles
from noust.core.fleet_job_titles import coded, title
from noust.core.server_text import CATALOG
from noust.core.store import NoustStore
from noust.fleet.aggregate import set_aggregator
from noust.web import fleet_dns_actions as _dns  # noqa: F401  (registers its action)
from noust.web import fleet_jobs, jobs
from noust.web import fleet_placement_actions as _placement  # noqa: F401  (registers its actions)
from noust.web import fleet_settings_actions as _settings  # noqa: F401  (registers its action)
from noust.web.fleet_jobs import FleetRequest, plan
from tests.support.fleet_actions import ADMIN
from tests.support.fleet_views_support import Central, build_central


def test_the_title_of_the_settings_job_is_the_name_it_always_had() -> None:
    assert title("settings_apply", 2).text == "Apply the fleet's settings on 2 servers"  # type: ignore[union-attr]
    assert title("settings_apply", 1).text == "Apply the fleet's settings on 1 server"  # type: ignore[union-attr]


def test_a_title_is_a_sentence_with_a_code_and_the_count() -> None:
    sentence = title("certs_renew", 3)

    assert sentence is not None
    assert sentence.to_dict() == {"code": "fleet_job.certs_renew.several", "params": {"count": 3}}
    single = title("certs_renew", 1)
    assert single is not None
    assert single.to_dict() == {"code": "fleet_job.certs_renew.single", "params": {}}


def test_every_bulk_action_has_a_title_whose_english_reads_as_its_name_always_did() -> None:
    # The job's name was "<the action's title> on N server(s)": the English did not change for the
    # actions whose title reads as a verb phrase (the three that did not are written out below).
    reworded = {
        "placement_copy": "Copy an application to {n} servers",
        "dns_cluster": "Set up Noust DNS on {n} servers",
        "database_transfer": "Move a database to {n} servers",
    }
    # The actions the product registers: another test of the same process may have added doubles of its own.
    product = {
        name: spec
        for name, spec in fleet_jobs.ACTIONS.items()
        if spec.run.__module__.startswith("noust.")
    }
    assert "settings_apply" in product
    for name, spec in product.items():
        sentence = title(name, 4)
        assert sentence is not None, f"{name} has no title as a sentence"
        expected = reworded.get(name, f"{spec.title} on {{n}} servers").format(n=4)
        assert sentence.text == expected


def test_an_action_nobody_gave_a_sentence_has_none() -> None:
    assert title("something_new", 2) is None


def test_a_name_reads_back_as_the_sentence_that_made_it() -> None:
    for action in fleet_job_titles._TITLES:
        for servers in (1, 2, 17):
            made = title(action, servers)
            assert made is not None
            assert coded(made.text) == made


def test_a_name_that_is_not_a_fleet_title_reads_back_as_nothing() -> None:
    for name in (
        "Deploy shop.example.com",
        "Renew certificates on many servers",
        "Renew certificates on 3 servers, mostly",
        "",
        None,
    ):
        assert coded(name) is None


def test_every_sentence_of_the_family_is_asked_for_by_a_title() -> None:
    family = {code for code in CATALOG if code.startswith("fleet_job.")}
    asked = {code for pair in fleet_job_titles._TITLES.values() for code in pair}

    assert family == asked


@pytest.fixture
def central(tmp_path: Path):
    built = build_central(tmp_path, ("web-1", "web-2"))
    yield built
    set_aggregator(None)
    NoustStore.reset_instance()


@pytest.mark.parametrize("servers", [1, 2])
def test_queuing_the_action_names_the_job_with_its_sentence(
    servers: int, central: Central, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: dict[str, object] = {}

    class Manager:
        def create_job(self, **kwargs: object) -> object:
            created.update(kwargs)
            return type("Queued", (), {"id": "abc12345"})()

    monkeypatch.setattr(jobs, "get_job_manager", lambda: Manager())
    names = ["web-1", "web-2"][:servers]
    the_plan = plan(
        FleetRequest("settings_apply", nodes=names), manager=central.manager, asker=ADMIN
    )

    fleet_jobs.start_job(the_plan, asker=ADMIN, actor="marta", elevated=False)

    suffix = "1 server" if servers == 1 else f"{servers} servers"
    assert created["name"] == f"Apply the fleet's settings on {suffix}"
    assert coded(str(created["name"])) is not None
