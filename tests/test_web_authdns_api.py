# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for ``/api/dns/noust``.

The managers decide (``tests/test_authdns_server.py``, ``test_authdns_zones.py``,
``test_authdns_delegation.py``). What this module owns: that what changes the host asks for sudo
mode and what only reads does not, that setting up is a job and the key of a secondary travels in
the body and in no other place a job keeps, and the shapes the console reads.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.runner import FakeRunner
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.managers.authdns.delegation import DelegationGuide
from noust.managers.authdns.server import TransferKey
from noust.managers.authdns.zones import ZoneEditor
from noust.web.api import authdns as api
from noust.web.api.deps import install_error_handlers
from noust.web.auth import require_auth
from tests.support.authdns_support import PUBLIC, SECRET, World, make_world
from tests.support.dns_packets import FakeNetwork, Reply, ns_record, soa_record

pytestmark = pytest.mark.real_listeners

SECOND = "198.51.100.7"
BEARER = {"sid": "token", "scope": "admin", "source": "bearer", "type": "api_token"}
COOKIE = {"sid": "s1", "scope": "admin", "source": "cookie", "type": "session"}
DS_LINE = (
    "example.com. DS 9431 13 2 EBE5D6007FDF87E3B70FBC2E1AFB0CFF87E66A0D8F70CAE380D4352472E83665\n"
)


class FakeJob:
    def __init__(self, job_id: str) -> None:
        self.id = job_id
        self.status = type("Status", (), {"value": "pending"})()

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id}


class FakeContext:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.secrets: dict[str, str] = {}

    def log(self, line: str, *args: Any, **kwargs: Any) -> None:
        self.lines.append(line)

    def update(self, *args: Any, **kwargs: Any) -> None:
        pass

    def add_secret_env(self, values: dict[str, str]) -> None:
        self.secrets.update(values)


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    queued: list[dict[str, Any]] = []

    class Manager:
        def create_job(self, **kwargs: Any) -> FakeJob:
            queued.append(kwargs)
            return FakeJob(f"job-{len(queued)}")

    monkeypatch.setattr(api, "get_job_manager", Manager)
    return queued


@pytest.fixture
def world(
    tmp_path: Path,
    store: NoustStore,
    secrets: SecretStore,
    runner: FakeRunner,
    fs: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> World:
    built = make_world(tmp_path, store, secrets, runner, fs, monkeypatch)
    editor = ZoneEditor(store, built.servers, clock=lambda: 1793266825, sleep=lambda _: None)
    network = FakeNetwork(
        {
            **{
                (a, "example.com", "SOA"): Reply([soa_record(7)], authoritative=True)
                for a in (PUBLIC, SECOND)
            },
            **{(a, "recursion-check.example.org", "A"): Reply(rcode=5) for a in (PUBLIC, SECOND)},
            ("1.1.1.1", "example.com", "NS"): Reply(
                [ns_record("ns1.example.net"), ns_record("ns2.example.net")],
                recursion_available=True,
            ),
            ("1.1.1.1", "example.com", "DS"): Reply([], recursion_available=True),
        }
    )
    guide = DelegationGuide(
        editor, resolvers=["1.1.1.1"], transport=network, resolve=lambda n: ([], None)
    )
    monkeypatch.setattr(api, "AuthDnsServers", lambda: built.servers)
    monkeypatch.setattr(api, "ZoneEditor", lambda: editor)
    monkeypatch.setattr(api, "DelegationGuide", lambda: guide)
    return built


def client_for(session: dict[str, Any]) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(api.router, prefix="/api/dns/noust")
    app.dependency_overrides[require_auth] = lambda: session
    return TestClient(app)


@pytest.fixture
def client(world: World) -> TestClient:
    return client_for(BEARER)


@pytest.fixture
def primary(world: World, client: TestClient) -> World:
    world.servers.setup("primary", hostname="ns1.example.net")
    world.servers.add_secondary(SECOND, "ns2.example.net")
    world.runner.script(["systemctl", "is-active"], stdout="active\n")
    return world


class TestTheServer:
    def test_the_state_before_a_setup_lists_the_costs_as_codes(self, client: TestClient) -> None:
        response = client.get("/api/dns/noust/server")

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["configured"], body["role"], body["installed"]) == (False, None, True)
        assert body["candidates"] == [PUBLIC]
        assert body["warnings"] == [
            "authdns.warning.availability",
            "authdns.warning.amplification",
        ]

    def test_setting_up_is_a_job_and_the_key_of_a_secondary_is_not_in_what_the_job_keeps(
        self, client: TestClient, jobs: list[dict[str, Any]]
    ) -> None:
        response = client.post(
            "/api/dns/noust/server",
            json={
                "role": "secondary",
                "hostname": "ns2.example.net",
                "primary_address": "198.51.100.9",
                "transfer_key": {"secret": SECRET},
            },
        )

        assert response.status_code == 202, response.text
        assert response.json()["job_id"] == "job-1"
        (queued,) = jobs
        assert queued["name"] == "Set up Noust DNS as a secondary"
        assert queued["kwargs"]["request"] == {
            "role": "secondary",
            "hostname": "ns2.example.net",
            "listen": None,
            "primary_address": "198.51.100.9",
            "epel": False,
        }
        assert queued["kwargs"]["transfer_secret"] == SECRET
        assert SECRET not in repr({k: v for k, v in queued.items() if k != "kwargs"})

    def test_the_job_registers_the_key_as_a_secret_and_sets_the_server_up(
        self, client: TestClient, jobs: list[dict[str, Any]], world: World
    ) -> None:
        client.post(
            "/api/dns/noust/server",
            json={
                "role": "secondary",
                "hostname": "ns2.example.net",
                "primary_address": "198.51.100.9",
                "transfer_key": {"secret": SECRET},
            },
        )
        (queued,) = jobs
        context = FakeContext()

        result = queued["func"](**queued["kwargs"], job_context=context)

        assert context.secrets == {"DNS_TRANSFER_KEY": SECRET}
        assert result["role"] == "secondary" and SECRET not in repr(result)
        assert world.servers.transfer_key().secret == SECRET

    def test_a_primary_job_needs_no_key(
        self, client: TestClient, jobs: list[dict[str, Any]], world: World
    ) -> None:
        client.post(
            "/api/dns/noust/server", json={"role": "primary", "hostname": "ns1.example.net"}
        )
        (queued,) = jobs
        context = FakeContext()

        result = queued["func"](**queued["kwargs"], job_context=context)

        assert context.secrets == {} and result["configured"] is True

    def test_installing_is_a_job_too(self, client: TestClient, jobs: list[dict[str, Any]]) -> None:
        response = client.post("/api/dns/noust/server/install", json={"epel": True})

        assert response.status_code == 202
        (queued,) = jobs
        assert queued["kwargs"]["epel"] is True
        result = queued["func"](**queued["kwargs"], job_context=FakeContext())
        assert result["installed"] is True

    @pytest.mark.parametrize(
        "body",
        [
            {"role": "boss"},
            {"role": "primary", "listen": ["a"] * 17},
            {"role": "secondary", "transfer_key": {"secret": "short"}},
            {"role": 5},
            {},
        ],
    )
    def test_what_cannot_be_a_setup_is_refused_before_a_job_is_queued(
        self, client: TestClient, jobs: list[dict[str, Any]], body: dict[str, Any]
    ) -> None:
        response = client.post("/api/dns/noust/server", json=body)

        # 422 where the body is not the shape of a request, 400 where it is and the value is not
        # usable (an address that is not one): either way before a job is queued.
        assert response.status_code in (400, 422)
        assert jobs == []

    def test_the_key_is_read_by_the_one_call_that_asks_for_it(
        self, client: TestClient, primary: World
    ) -> None:
        response = client.get("/api/dns/noust/server/key")

        assert response.status_code == 200
        assert response.json() == {
            "name": "noust-xfr",
            "algorithm": "hmac-sha256",
            "secret": primary.servers.transfer_key().secret,
        }
        status = client.get("/api/dns/noust/server").text
        assert primary.servers.transfer_key().secret not in status

    def test_a_server_that_is_not_set_up_has_no_key(self, client: TestClient) -> None:
        response = client.get("/api/dns/noust/server/key")

        assert response.status_code == 400
        body = response.json()
        assert body["error"] == "validationerror" and list(body["fields"]) == ["transfer_key"]
        assert body["hint"] == "Set Noust DNS up first: noust dns server setup"

    def test_secondaries_are_added_and_removed(self, client: TestClient, primary: World) -> None:
        added = client.post(
            "/api/dns/noust/server/secondaries",
            json={"address": "198.51.100.8", "hostname": "ns3.example.net"},
        )
        removed = client.delete("/api/dns/noust/server/secondaries/198.51.100.8")
        again = client.delete("/api/dns/noust/server/secondaries/198.51.100.8")

        assert added.status_code == 201 and added.json()["hostname"] == "ns3.example.net"
        assert removed.json() == {"removed": True} and again.json() == {"removed": False}

    def test_removing_the_server_asks_for_the_flag_when_it_serves_zones(
        self, client: TestClient, primary: World
    ) -> None:
        client.post("/api/dns/noust/zones", json={"name": "example.com"})

        refused = client.delete("/api/dns/noust/server")
        done = client.delete("/api/dns/noust/server", params={"delete_zones": "true"})

        assert refused.status_code == 400 and "delete-zones" in refused.text
        assert done.json() == {"removed": True}


class TestAnInstallationThatNeedsAYes:
    @pytest.fixture(autouse=True)
    def _rhel(self, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
        from types import SimpleNamespace

        from noust.managers.server.host import PackageFamily

        monkeypatch.setattr(
            "noust.managers.authdns.engine.detect_platform",
            lambda _runner: SimpleNamespace(
                family=PackageFamily.DNF,
                os=SimpleNamespace(id="almalinux", version_id="9.4", id_like="rhel centos fedora"),
                why_updates_unsupported=lambda: "",
            ),
        )
        world.runner._known_programs = {"dnf"}

    def test_epel_is_asked_for_before_a_job_is_queued(
        self, client: TestClient, jobs: list[dict[str, Any]]
    ) -> None:
        for path, body in (
            ("/api/dns/noust/server/install", {}),
            ("/api/dns/noust/server", {"role": "primary", "hostname": "ns1.example.net"}),
        ):
            response = client.post(path, json=body)

            assert response.status_code == 409, response.text
            assert response.json()["error"] == "confirmation_required"
            assert response.json()["required"] == {
                "epel": True,
                "packages": ["epel-release", "knot"],
            }
        assert jobs == []

    def test_with_the_yes_it_is_queued(
        self, client: TestClient, jobs: list[dict[str, Any]]
    ) -> None:
        response = client.post("/api/dns/noust/server/install", json={"epel": True})

        assert response.status_code == 202 and len(jobs) == 1


class TestSudoMode:
    @pytest.mark.parametrize(
        ("method", "path", "body"),
        [
            ("POST", "/api/dns/noust/server/install", {}),
            ("POST", "/api/dns/noust/server", {"role": "primary"}),
            ("DELETE", "/api/dns/noust/server", None),
            ("GET", "/api/dns/noust/server/key", None),
            ("DELETE", "/api/dns/noust/zones/example.com", None),
            ("PUT", "/api/dns/noust/zones/example.com/dnssec", {"enabled": True}),
        ],
    )
    def test_what_changes_the_host_or_reveals_the_key_asks_for_it(
        self, world: World, jobs: list[dict[str, Any]], method: str, path: str, body: Any
    ) -> None:
        response = client_for(COOKIE).request(method, path, json=body)

        assert response.status_code == 403, response.text
        assert response.json()["error"] == "elevation_required"
        assert jobs == []

    @pytest.mark.parametrize(
        "path",
        [
            "/api/dns/noust/server",
            "/api/dns/noust/zones",
        ],
    )
    def test_reading_does_not(self, primary: World, path: str) -> None:
        assert client_for(COOKIE).get(path).status_code == 200


class TestZones:
    def test_a_zone_is_made_listed_read_changed_and_deleted(
        self, client: TestClient, primary: World
    ) -> None:
        made = client.post("/api/dns/noust/zones", json={"name": "Example.COM"})
        listed = client.get("/api/dns/noust/zones")
        read = client.get("/api/dns/noust/zones/example.com")
        changed = client.patch("/api/dns/noust/zones/example.com", json={"default_ttl": 600})
        deleted = client.delete("/api/dns/noust/zones/example.com")
        again = client.delete("/api/dns/noust/zones/example.com")

        assert made.status_code == 201, made.text
        assert made.json()["nameservers"] == ["ns1.example.net", "ns2.example.net"]
        assert listed.json()["zones"][0]["name"] == "example.com"
        assert read.json()["contact"] == "hostmaster@example.com"
        assert changed.json()["default_ttl"] == 600
        assert deleted.json() == {"removed": True} and again.json() == {"removed": False}

    def test_a_zone_that_exists_or_cannot_be_is_a_400_with_a_way_out(
        self, client: TestClient, primary: World
    ) -> None:
        client.post("/api/dns/noust/zones", json={"name": "example.com"})

        twice = client.post("/api/dns/noust/zones", json={"name": "example.com"})
        bad = client.post("/api/dns/noust/zones", json={"name": "not a zone"})
        glue = client.post(
            "/api/dns/noust/zones",
            json={"name": "example.org", "nameservers": ["ns9.example.org"]},
        )

        assert twice.status_code == bad.status_code == glue.status_code == 400
        assert "already a zone" in twice.text
        assert "glue" in glue.text

    def test_records_are_added_changed_and_removed(
        self, client: TestClient, primary: World
    ) -> None:
        client.post("/api/dns/noust/zones", json={"name": "example.com"})

        added = client.post(
            "/api/dns/noust/zones/example.com/records",
            json={"name": "www", "type": "a", "content": "192.0.2.1", "ttl": 300},
        )
        record = added.json()
        changed = client.patch(
            f"/api/dns/noust/zones/example.com/records/{record['id']}",
            json={"content": "192.0.2.9"},
        )
        shown = client.get("/api/dns/noust/zones/example.com").json()["records"]
        removed = client.delete(f"/api/dns/noust/zones/example.com/records/{record['id']}")

        assert added.status_code == 201 and (record["type"], record["ttl"]) == ("A", 300)
        assert changed.json()["content"] == "192.0.2.9" and changed.json()["ttl"] == 300
        assert [r["content"] for r in shown] == ["192.0.2.9"]
        assert removed.json() == {"removed": True}

    def test_a_record_that_does_not_fit_says_why(self, client: TestClient, primary: World) -> None:
        client.post("/api/dns/noust/zones", json={"name": "example.com"})
        client.post(
            "/api/dns/noust/zones/example.com/records",
            json={"name": "go", "type": "CNAME", "content": "target.example.net"},
        )

        response = client.post(
            "/api/dns/noust/zones/example.com/records",
            json={"name": "go", "type": "TXT", "content": "hi"},
        )

        assert response.status_code == 400 and "a CNAME stands alone" in response.text

    def test_the_zones_of_a_secondary_are_listed_and_not_edited(
        self, client: TestClient, world: World
    ) -> None:
        world.servers.setup(
            "secondary",
            hostname="ns2.example.net",
            primary_address="198.51.100.9",
            transfer_key=TransferKey("noust-xfr", "hmac-sha256", SECRET),
        )
        world.runner.script(["systemctl", "is-active"], stdout="active\n")

        assert client.get("/api/dns/noust/zones").status_code == 200
        assert client.post("/api/dns/noust/zones", json={"name": "example.com"}).status_code == 400


class TestDnssecAndDelegation:
    @pytest.fixture(autouse=True)
    def _zone(self, client: TestClient, primary: World) -> None:
        client.post("/api/dns/noust/zones", json={"name": "example.com"})
        primary.runner.script(["keymgr"], stdout=DS_LINE)

    def test_signing_returns_the_ds_and_unsigning_needs_it_gone(self, client: TestClient) -> None:
        signed = client.put("/api/dns/noust/zones/example.com/dnssec", json={"enabled": True})
        refused = client.put("/api/dns/noust/zones/example.com/dnssec", json={"enabled": False})
        undone = client.put(
            "/api/dns/noust/zones/example.com/dnssec",
            json={"enabled": False, "ds_removed": True},
        )

        assert signed.status_code == 200
        assert signed.json()["ds"][0]["text"].startswith("9431 13 2 ebe5d600")
        assert refused.status_code == 400
        assert list(refused.json()["fields"]) == ["ds_removed"]
        assert "validating resolver refuses the domain" in refused.json()["hint"]
        assert undone.json()["enabled"] is False

    def test_the_server_is_told_the_registrar_has_the_ds(
        self, client: TestClient, primary: World
    ) -> None:
        client.put("/api/dns/noust/zones/example.com/dnssec", json={"enabled": True})

        response = client.post("/api/dns/noust/zones/example.com/dnssec/ds-published")

        assert response.status_code == 200
        assert [
            "knotc",
            "-c",
            str(primary.config),
            "zone-ksk-submitted",
            "example.com.",
        ] in primary.commands()

    def test_the_delegation_is_steps_with_a_code_and_a_check_that_says_what_is_missing(
        self, client: TestClient
    ) -> None:
        plan = client.get("/api/dns/noust/zones/example.com/delegation").json()
        check = client.get("/api/dns/noust/zones/example.com/delegation/check").json()

        assert [s["code"] for s in plan["steps"]] == [
            "authdns.delegation.nameservers",
            "authdns.delegation.check",
        ]
        assert plan["steps"][0]["text"].startswith("At your registrar, set the name servers")
        assert plan["nameservers"][0] == {"name": "ns1.example.net", "addresses": [], "glue": False}
        assert check["ready"] is True and check["delegated"] is True
        assert [s["authoritative"] for s in check["servers"]] == [True, True]

    def test_a_zone_that_is_not_there_has_no_plan(self, client: TestClient) -> None:
        assert client.get("/api/dns/noust/zones/nope.example/delegation").status_code == 400


class TestWhenTheNameServerSaysNo:
    def test_a_change_knot_does_not_take_is_a_502_with_its_own_words_and_the_zone_as_it_was(
        self, client: TestClient, primary: World
    ) -> None:
        client.post("/api/dns/noust/zones", json={"name": "example.com"})
        primary.runner.script(
            ["knotc", "-c", str(primary.config), "-b", "zone-reload", "example.com."],
            stderr="error: [example.com.] (failed)",
            exit_code=1,
        )

        response = client.post(
            "/api/dns/noust/zones/example.com/records",
            json={"name": "www", "type": "A", "content": "192.0.2.1"},
        )

        assert response.status_code == 502, response.text
        body = response.json()
        assert body["detail"] == "Knot did not take the file of example.com"
        assert "(failed)" in body["output"]
        assert client.get("/api/dns/noust/zones/example.com").json()["records"] == []
