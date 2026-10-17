# Copyright (c) 2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Putting an application on another server from the export of one that has it (checklist F-18).

The servers do the work (the export on one, the import on the other, each through its own API and
its own permissions); the central carries the document between them and brings the copy to the commit
the source serves. The document may hold secret values: they pass through the central's memory, and
none of the places a job writes (its result, its log, the record of a server) may ever hold one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore
from noust.fleet.aggregate import Asker, set_aggregator
from noust.web import fleet_jobs
from noust.web.fleet_jobs import FleetJobs, FleetRequest, Runner, plan
from tests.fleet_views_support import SCHEMA_30, Central, build_central
from tests.placement_support import DOMAIN, SECRET_VALUE, Shop, Upstream

ADMIN = Asker(actor="ana", scope="admin", role="admin", elevated=True)
NAMES = ("web-1", "web-2", "web-3")


@pytest.fixture
def central(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")
    built = build_central(tmp_path, NAMES)
    yield built
    set_aggregator(None)
    NoustStore.reset_instance()


@pytest.fixture
def upstream() -> Upstream:
    return Upstream(head="aaa1111")


@pytest.fixture
def source(central: Central, upstream: Upstream) -> Shop:
    return Shop(central.nodes["web-1"], upstream, commit="aaa1111")


class Recorder:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []
        self.snapshots: list[dict[str, Any]] = []

    def log(self, message: str, level: str = "info") -> None:
        self.lines.append((level, message))

    def publish(self, snapshot: dict[str, Any], done: int, step: str) -> None:
        self.snapshots.append(snapshot)


def request(**overrides: Any) -> FleetRequest:
    options = {"domain": DOMAIN, "from_node": "web-1", **overrides.pop("options", {})}
    values: dict[str, Any] = {"action": "placement_copy", "nodes": ["web-2"], "options": options}
    values.update(overrides)
    return FleetRequest(**values)


def run(central: Central, fleet_request: FleetRequest):
    the_plan = plan(fleet_request, manager=central.manager, asker=ADMIN)
    recorder = Recorder()
    jobs = FleetJobs(central.store)
    job_id = fleet_jobs.new_job_id()
    jobs.create(job_id, the_plan, created_by="ana")
    snapshot = Runner(
        plan=the_plan,
        job_id=job_id,
        manager=central.manager,
        asker=ADMIN,
        reporter=recorder,
        elevated_until=float("inf"),
        jobs=jobs,
        poll=0,
        sleep=lambda seconds: None,
    ).run()
    return snapshot, recorder, job_id


def states(snapshot: dict[str, Any]) -> dict[str, str]:
    return {node["node"]: node["state"] for node in snapshot["nodes"]}


def test_the_application_is_exported_from_the_source_and_imported_on_the_destination(
    central, source, upstream
) -> None:
    created: list[Shop] = []
    Shop.accept_imports(central.nodes["web-2"], upstream, imported=created)

    snapshot, _, _ = run(central, request())

    assert snapshot["status"] == "succeeded", snapshot
    assert source.exports == [{"with_secrets": "True"}]
    assert len(created) == 1 and created[0].domain == DOMAIN
    body = created[0].imports[0]
    assert body["document"]["app"]["domain"] == DOMAIN
    assert body["document"]["env"]["API_KEY"]["value"] == SECRET_VALUE
    assert "domain" not in body, "same domain: the import takes it from the document"


def test_the_copy_is_brought_to_the_commit_the_source_serves(central, source, upstream) -> None:
    created: list[Shop] = []
    Shop.accept_imports(central.nodes["web-2"], upstream, imported=created)
    source.commit = "old0001"
    source.node.apps = [source.row()]
    upstream.head = "new0002"

    snapshot, _, _ = run(central, request())

    assert snapshot["status"] == "succeeded", snapshot
    assert created[0].deploys == [{"commit": "old0001"}], (
        "the copy's import built the head; it is moved"
    )
    assert created[0].commit == "old0001"


def test_a_copy_that_is_already_on_the_commit_is_not_built_again(central, source, upstream) -> None:
    created: list[Shop] = []
    Shop.accept_imports(central.nodes["web-2"], upstream, imported=created)

    run(central, request())

    assert created[0].deploys == []


def test_the_secrets_are_asked_for_only_when_wanted(central, source, upstream) -> None:
    created: list[Shop] = []
    Shop.accept_imports(central.nodes["web-2"], upstream, imported=created)

    run(central, request(options={"secrets": False}))

    assert source.exports == [{"with_secrets": "False"}]
    assert created[0].imports[0]["document"]["env"]["API_KEY"]["value"] is None


def test_no_secret_value_is_ever_kept_by_the_job(central, source, upstream) -> None:
    created: list[Shop] = []
    Shop.accept_imports(central.nodes["web-2"], upstream, imported=created)

    snapshot, recorder, job_id = run(central, request())

    everything = (
        json.dumps(snapshot)
        + json.dumps(recorder.lines)
        + json.dumps(FleetJobs(central.store).get(job_id))
    )
    assert SECRET_VALUE not in everything
    rows = central.store._get_connection().execute("SELECT * FROM fleet_job_nodes").fetchall()
    assert SECRET_VALUE not in json.dumps([dict(r) for r in rows])


def test_a_server_that_already_has_it_is_left_alone(central, source, upstream) -> None:
    other = Shop(central.nodes["web-2"], upstream)

    snapshot, _, _ = run(central, request())

    assert states(snapshot) == {"web-2": "skipped"}
    assert source.exports == [] or other.imports == []
    assert other.imports == []


def test_a_new_domain_makes_a_second_copy(central, source, upstream) -> None:
    created: list[Shop] = []
    Shop.accept_imports(central.nodes["web-2"], upstream, imported=created)

    snapshot, _, _ = run(central, request(options={"new_domain": "staging.example.com"}))

    assert snapshot["status"] == "succeeded", snapshot
    assert created[0].domain == "staging.example.com"
    assert created[0].imports[0]["domain"] == "staging.example.com"


def test_the_source_must_have_the_application(central, upstream) -> None:
    created: list[Shop] = []
    Shop.accept_imports(central.nodes["web-2"], upstream, imported=created)
    central.nodes["web-1"].apps = []

    snapshot, _, _ = run(central, request())

    assert states(snapshot) == {"web-2": "failed"}
    node = snapshot["nodes"][0]
    assert "web-1" in node["error"]["message"]
    assert created == []


def test_an_import_that_fails_keeps_the_servers_words(central, source, upstream) -> None:
    Shop.accept_imports(
        central.nodes["web-2"], upstream, imported=[], fail="API_KEY is a secret and has no value"
    )

    snapshot, _, _ = run(central, request())

    assert states(snapshot) == {"web-2": "failed"}
    assert "API_KEY is a secret and has no value" in (snapshot["nodes"][0]["output"] or "")


def test_one_server_failing_does_not_stop_the_others(central, source, upstream) -> None:
    created: list[Shop] = []
    Shop.accept_imports(central.nodes["web-2"], upstream, imported=[], fail="disk full")
    Shop.accept_imports(central.nodes["web-3"], upstream, imported=created)

    snapshot, _, _ = run(central, request(nodes=["web-2", "web-3"]))

    assert states(snapshot) == {"web-2": "failed", "web-3": "succeeded"}
    assert len(created) == 1


def test_the_source_cannot_also_be_a_destination(central, source) -> None:
    with pytest.raises(ValidationError) as caught:
        plan(request(nodes=["web-1", "web-2"]), manager=central.manager, asker=ADMIN)

    assert "web-1" in caught.value.message


def test_the_source_must_be_a_server_of_this_central(central, source) -> None:
    with pytest.raises(Exception, match="No node named ghost"):
        plan(request(options={"from_node": "ghost"}), manager=central.manager, asker=ADMIN)


def test_the_plan_needs_sudo_mode_because_the_import_does(central, source) -> None:
    described = plan(request(), manager=central.manager, asker=ADMIN).to_dict()

    assert described["requires_elevation"] is True
    assert any("secret" in note for note in described["notes"])


def test_a_server_that_cannot_import_is_skipped_as_too_old(central, source) -> None:
    central.nodes["web-2"].schema = dict(SCHEMA_30)

    described = plan(request(), manager=central.manager, asker=ADMIN).to_dict()

    assert described["nodes"][0]["reason"] == "unsupported"


@pytest.mark.parametrize(
    "options",
    [
        {"domain": DOMAIN},
        {"domain": DOMAIN, "from_node": ""},
        {"domain": "https://x.example.com", "from_node": "web-1"},
        {"domain": DOMAIN, "from_node": "web-1", "new_domain": "bad domain"},
        {"domain": DOMAIN, "from_node": "web-1", "secrets": "yes"},
        {"domain": DOMAIN, "from_node": "web-1", "extra": 1},
    ],
)
def test_options_that_are_not_valid_are_refused(central, source, options) -> None:
    with pytest.raises(ValidationError):
        plan(
            FleetRequest("placement_copy", nodes=["web-2"], options=options),
            manager=central.manager,
            asker=ADMIN,
        )
