"""
Servers created at a cloud provider, over HTTP: ``/api/fleet/cloud``.

The managers are tested on their own (``tests/test_cloud_*.py``); what is pinned here is
what the HTTP layer owes: an administrator's permission everywhere, sudo mode for what
spends or destroys, a token that is in nothing it leaves, checks before a job exists, and
a job whose record and log never hold a key.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core.accounts.approvals import one_time_fields, rule_for
from noust.core.config import Config
from noust.core.exceptions import NodeError
from noust.core.runner import FakeRunner
from noust.core.secrets import SecretStore
from noust.core.store import NodeRecord, NoustStore, get_store
from noust.fleet.cloud.accounts import CloudAccounts
from noust.fleet.cloud.servers import CloudServers
from noust.web.api import cloud as api
from noust.web.auth import CSRF_HEADER_NAME
from noust.web.jobs import JobStatus, JobType, get_job_manager
from noust.web.permissions import Permission
from noust.web.permissions.registry import permission_for_route
from noust.web.server import create_app, get_token_manager
from tests.cloud_support import FakeApi
from tests.test_web_auth import make_config

TOKEN = "hc" + "_" + "w" * 20 + "E4r8"


class FakeProvisioner:
    """What the routes ask of the provisioner."""

    def __init__(self, servers: CloudServers) -> None:
        self.servers = servers
        self.validated: list[Any] = []
        self.requests: list[Any] = []
        self.refuse: NodeError | None = None
        self.deleted: list[tuple[str, bool]] = []
        self.powered: list[str] = []

    def validate(self, request: Any) -> Any:
        self.validated.append(request)
        if self.refuse is not None:
            raise self.refuse
        return SimpleNamespace(kind="hetzner", display_name="Hetzner Cloud"), []

    def provision(self, request: Any, *, actor: str | None, job_id: str | None, log: Any) -> Any:
        self.requests.append((request, actor, job_id))
        self.servers.record(
            node=request.name,
            account=request.account,
            provider="hetzner",
            provider_id="42",
            location=request.location,
            plan=request.plan,
            image=request.image,
            job_id=job_id,
        )
        log("Hetzner Cloud created 42", "success")
        return SimpleNamespace(to_dict=lambda: {"name": request.name, "status": "reachable"})

    def machine(self, node: str, *, live: bool = False) -> dict[str, Any]:
        data = self.servers.get(node).to_dict()
        if live:
            data["remote"] = {"id": "42", "state": "running"}
        return data

    def power_on(self, node: str, *, actor: str | None = None) -> None:
        self.powered.append(node)

    def delete_machine(
        self, node: str, *, remove_node: bool = False, actor: str | None = None
    ) -> list[str]:
        self.deleted.append((node, remove_node))
        return ["a warning"]


@pytest.fixture(autouse=True)
def fresh_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    Config.reset_instance()
    NoustStore.reset_instance()
    yield
    Config.reset_instance()
    NoustStore.reset_instance()


@pytest.fixture
def provider_api() -> FakeApi:
    fake = FakeApi(prefix="/v1")
    fake.on(
        "GET",
        "/locations",
        (200, {"locations": [{"name": "fsn1", "city": "Falkenstein", "country": "DE"}]}),
    )
    fake.on(
        "GET",
        "/server_types",
        (200, {"server_types": [{"name": "cx22", "cores": 2, "memory": 4, "disk": 40}]}),
    )
    fake.on(
        "GET",
        "/images",
        (200, {"images": [{"name": "ubuntu-24.04", "description": "Ubuntu 24.04"}]}),
    )
    fake.on("GET", "/ssh_keys", (200, {"ssh_keys": []}))
    fake.on("GET", "/firewalls", (200, {"firewalls": []}))
    fake.on("GET", "/networks", (200, {"networks": []}))
    return fake


@pytest.fixture(autouse=True)
def managers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider_api: FakeApi
) -> FakeProvisioner:
    secrets = SecretStore(root=tmp_path / "secrets")
    options = {"opener": provider_api, "sleep": lambda _s: None}
    monkeypatch.setattr(
        api, "_accounts", lambda: CloudAccounts(secrets=secrets, provider_options=options)
    )
    provisioner = FakeProvisioner(CloudServers())
    monkeypatch.setattr(api, "_provisioner", lambda: provisioner)
    return provisioner


@pytest.fixture
def client(sandbox: Path, runner: FakeRunner) -> TestClient:
    return TestClient(create_app(make_config(sandbox)), client=("testclient", 50000))


def sign_in(client: TestClient) -> str:
    master = get_token_manager().generate_master_token()
    response = client.post("/api/auth/login", json={"token": master})
    assert response.status_code == 200, response.text
    client.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return master


def elevate(client: TestClient, master: str) -> None:
    response = client.post("/api/auth/elevate", json={"token": master})
    assert response.status_code == 200, response.text


def wait_for(job_id: str) -> Any:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = get_job_manager().get_job(job_id)
        if job is not None and job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
            return job
        time.sleep(0.05)
    raise AssertionError("the job never finished")


def admin(client: TestClient) -> None:
    elevate(client, sign_in(client))


ACCOUNT = {"name": "hz", "provider": "hetzner", "token": TOKEN, "note": "web project"}
SERVER = {
    "name": "web-2",
    "account": "hz",
    "location": "fsn1",
    "plan": "cx22",
    "image": "ubuntu-24.04",
    "scripts": ["swap-2g"],
}


class TestWhoMayCallIt:
    def test_nobody_anonymous_calls_any_of_it(self, client: TestClient) -> None:
        for method, path in (
            ("get", "/api/fleet/cloud/providers"),
            ("get", "/api/fleet/cloud/accounts"),
            ("post", "/api/fleet/cloud/accounts"),
            ("get", "/api/fleet/cloud/accounts/hz/catalog"),
            ("get", "/api/fleet/cloud/scripts"),
            ("get", "/api/fleet/cloud/servers"),
            ("post", "/api/fleet/cloud/servers"),
            ("delete", "/api/fleet/cloud/servers/web-2"),
        ):
            assert getattr(client, method)(path).status_code == 401, (method, path)

    def test_every_route_is_an_administrators(self) -> None:
        from noust.web.permissions.routes_cloud import ROUTES

        assert set(ROUTES.values()) == {Permission.FLEET_MANAGE}
        for (method, template), permission in ROUTES.items():
            assert permission_for_route(method, template) == permission

    @pytest.mark.parametrize(
        ("method", "path", "body"),
        [
            ("post", "/api/fleet/cloud/accounts", ACCOUNT),
            ("put", "/api/fleet/cloud/accounts/hz/token", {"token": TOKEN}),
            ("delete", "/api/fleet/cloud/accounts/hz", None),
            (
                "put",
                "/api/fleet/cloud/scripts/mine",
                {"kind": "script", "content": "#!/bin/sh\n:\n"},
            ),
            ("delete", "/api/fleet/cloud/scripts/mine", None),
            ("post", "/api/fleet/cloud/servers", SERVER),
            ("post", "/api/fleet/cloud/servers/web-2/power-on", None),
            ("delete", "/api/fleet/cloud/servers/web-2?confirm=web-2", None),
        ],
    )
    def test_what_spends_or_destroys_asks_for_sudo_mode(
        self, client: TestClient, method: str, path: str, body: Any
    ) -> None:
        sign_in(client)

        response = getattr(client, method)(path, **({"json": body} if body is not None else {}))

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"

    def test_the_token_and_the_new_servers_need_a_second_person_when_approvals_are_on(self) -> None:
        for method, template in (
            ("POST", "/api/fleet/cloud/accounts"),
            ("PUT", "/api/fleet/cloud/accounts/{name}/token"),
            ("POST", "/api/fleet/cloud/servers"),
            ("DELETE", "/api/fleet/cloud/servers/{node}"),
            ("PUT", "/api/fleet/cloud/scripts/{name}"),
        ):
            assert rule_for(method, template, ["fleet.manage"], {}, {}) is not None, (
                method,
                template,
            )
        assert one_time_fields("POST", "/api/fleet/cloud/accounts") == ("token",)
        assert one_time_fields("PUT", "/api/fleet/cloud/accounts/{name}/token") == ("token",)


class TestAccounts:
    def test_the_providers_are_listed_with_where_their_token_is_made(
        self, client: TestClient
    ) -> None:
        admin(client)

        response = client.get("/api/fleet/cloud/providers")

        assert response.status_code == 200
        kinds = {p["kind"]: p for p in response.json()}
        assert set(kinds) == {"hetzner", "digitalocean", "vultr"}
        assert kinds["hetzner"]["token_url"].startswith("https://")
        assert kinds["hetzner"]["capabilities"]["firewalls"] is True

    def test_an_account_is_added_and_the_token_is_in_nothing_that_comes_back(
        self, client: TestClient
    ) -> None:
        admin(client)

        added = client.post("/api/fleet/cloud/accounts", json=ACCOUNT)
        listed = client.get("/api/fleet/cloud/accounts")

        assert added.status_code == 201, added.text
        assert added.json()["token_hint"] == "...E4r8"
        assert added.json()["verified_at"] is not None
        assert listed.json()[0]["name"] == "hz"
        assert TOKEN not in added.text and TOKEN not in listed.text

    def test_a_token_the_provider_refuses_is_not_kept(
        self, client: TestClient, provider_api: FakeApi
    ) -> None:
        provider_api._routes.clear()
        provider_api.on(
            "GET", "/locations", (401, {"error": {"message": "unable to authenticate"}})
        )
        admin(client)

        response = client.post("/api/fleet/cloud/accounts", json=ACCOUNT)

        assert response.status_code == 502
        assert "unable to authenticate" in response.text
        assert TOKEN not in response.text
        assert client.get("/api/fleet/cloud/accounts").json() == []

    def test_a_malformed_token_is_a_validation_error_not_a_request_to_the_provider(
        self, client: TestClient, provider_api: FakeApi
    ) -> None:
        admin(client)

        response = client.post("/api/fleet/cloud/accounts", json={**ACCOUNT, "token": "short"})

        assert response.status_code == 400
        assert provider_api.calls == []

    def test_a_token_is_replaced_verified_and_the_account_removed(self, client: TestClient) -> None:
        admin(client)
        client.post("/api/fleet/cloud/accounts", json=ACCOUNT)

        replaced = client.put(
            "/api/fleet/cloud/accounts/hz/token", json={"token": TOKEN[:-4] + "Zz99"}
        )
        verified = client.post("/api/fleet/cloud/accounts/hz/verify")
        removed = client.delete("/api/fleet/cloud/accounts/hz")

        assert replaced.json()["token_hint"] == "...Zz99"
        assert verified.status_code == 200
        assert removed.status_code == 204
        assert client.get("/api/fleet/cloud/accounts").json() == []

    def test_the_catalog_lists_what_the_account_can_create(self, client: TestClient) -> None:
        admin(client)
        client.post("/api/fleet/cloud/accounts", json=ACCOUNT)

        response = client.get("/api/fleet/cloud/accounts/hz/catalog")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["provider"] == "hetzner"
        assert body["locations"][0]["id"] == "fsn1"
        assert body["plans"][0]["id"] == "cx22"
        assert body["images"][0]["id"] == "ubuntu-24.04"
        assert body["capabilities"]["backups"] is True

    def test_an_unknown_account_is_an_error_that_names_the_command_that_lists_them(
        self, client: TestClient
    ) -> None:
        admin(client)

        response = client.get("/api/fleet/cloud/accounts/nope/catalog")

        assert response.status_code == 400
        assert "account list" in response.text


class TestTheLibrary:
    def test_the_built_ins_are_listed_without_their_text(self, client: TestClient) -> None:
        admin(client)

        response = client.get("/api/fleet/cloud/scripts")

        assert response.status_code == 200
        names = {entry["name"] for entry in response.json()}
        assert {"swap-2g", "base-packages"} <= names
        assert all("content" not in entry for entry in response.json())

    def test_an_entry_is_saved_read_and_deleted(self, client: TestClient) -> None:
        admin(client)
        body = {"kind": "script", "content": "#!/bin/sh\nufw allow 22\n", "description": "ssh"}

        saved = client.put("/api/fleet/cloud/scripts/ssh-only", json=body)
        read = client.get("/api/fleet/cloud/scripts/ssh-only")
        gone = client.delete("/api/fleet/cloud/scripts/ssh-only")

        assert saved.status_code == 200, saved.text
        assert read.json()["content"] == "#!/bin/sh\nufw allow 22\n"
        assert gone.status_code == 204
        assert client.get("/api/fleet/cloud/scripts/ssh-only").status_code == 400

    def test_an_entry_that_sets_what_noust_owns_is_refused_with_the_reason(
        self, client: TestClient
    ) -> None:
        admin(client)

        response = client.put(
            "/api/fleet/cloud/scripts/evil",
            json={"kind": "cloud-config", "content": "#cloud-config\nssh_keys: {}\n"},
        )

        assert response.status_code == 400
        assert "ssh_keys" in response.text

    def test_a_built_in_cannot_be_changed(self, client: TestClient) -> None:
        admin(client)

        response = client.put(
            "/api/fleet/cloud/scripts/swap-2g", json={"kind": "script", "content": "#!/bin/sh\n:\n"}
        )

        assert response.status_code == 400

    def test_the_preview_shows_the_document_without_the_host_key(self, client: TestClient) -> None:
        admin(client)

        response = client.post("/api/fleet/cloud/scripts/preview", json={"scripts": ["swap-2g"]})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["document"].startswith("#cloud-config\n")
        assert "01-swap-2g.sh" in body["document"]
        assert "BEGIN OPENSSH" not in body["document"]
        assert body["bytes"] > len(body["document"])

    def test_a_preview_of_an_unknown_script_is_an_error(self, client: TestClient) -> None:
        admin(client)

        assert (
            client.post("/api/fleet/cloud/scripts/preview", json={"scripts": ["nope"]}).status_code
            == 400
        )


class TestMachines:
    def test_the_checks_that_need_no_connection_run_before_a_job_exists(
        self, client: TestClient, managers: FakeProvisioner
    ) -> None:
        managers.refuse = NodeError(
            "This central may not register servers yet",
            details="Two-factor sign-in is not enabled.",
        )
        admin(client)

        response = client.post("/api/fleet/cloud/servers", json=SERVER)

        assert response.status_code == 400, response.text
        assert "Two-factor" in response.text
        assert managers.requests == []

    def test_creating_a_server_is_a_job_that_streams_what_happens(
        self, client: TestClient, managers: FakeProvisioner
    ) -> None:
        admin(client)

        response = client.post(
            "/api/fleet/cloud/servers", json={**SERVER, "allow_host_access": True}
        )

        assert response.status_code == 202, response.text
        job = wait_for(response.json()["job_id"])
        assert job.status == JobStatus.COMPLETED
        assert job.type == JobType.NODE_PROVISION
        request, actor, job_id = managers.requests[0]
        assert (request.name, request.account, request.plan) == ("web-2", "hz", "cx22")
        assert request.host_access is True and request.scripts == ["swap-2g"]
        assert job_id == job.id
        assert any("created 42" in entry.message for entry in job.logs)
        assert job.result["node"]["name"] == "web-2"

    def test_a_list_of_the_machines_and_one_with_the_providers_live_view(
        self, client: TestClient, managers: FakeProvisioner
    ) -> None:
        managers.servers.record(
            node="web-2",
            account="hz",
            provider="hetzner",
            provider_id="42",
            location="fsn1",
            plan="cx22",
            image="u",
        )
        admin(client)

        listed = client.get("/api/fleet/cloud/servers")
        one = client.get("/api/fleet/cloud/servers/web-2?live=true")

        assert [m["node"] for m in listed.json()] == ["web-2"]
        assert one.json()["remote"]["state"] == "running"

    def test_powering_on_asks_the_provisioner(
        self, client: TestClient, managers: FakeProvisioner
    ) -> None:
        managers.servers.record(
            node="web-2",
            account="hz",
            provider="hetzner",
            provider_id="42",
            location=None,
            plan=None,
            image=None,
        )
        admin(client)

        response = client.post("/api/fleet/cloud/servers/web-2/power-on")

        assert response.status_code == 200, response.text
        assert managers.powered == ["web-2"]

    def test_deleting_a_machine_needs_its_name_typed(
        self, client: TestClient, managers: FakeProvisioner
    ) -> None:
        admin(client)

        without = client.delete("/api/fleet/cloud/servers/web-2")
        wrong = client.delete("/api/fleet/cloud/servers/web-2?confirm=web-3")
        right = client.delete("/api/fleet/cloud/servers/web-2?confirm=web-2&remove_node=true")

        assert without.status_code == 400 and wrong.status_code == 400
        assert right.status_code == 200, right.text
        assert right.json() == {"node": "web-2", "warnings": ["a warning"]}
        assert managers.deleted == [("web-2", True)]

    def test_no_secret_is_in_the_job_the_log_or_what_the_store_holds(
        self, client: TestClient, managers: FakeProvisioner
    ) -> None:
        admin(client)
        client.post("/api/fleet/cloud/accounts", json=ACCOUNT)

        response = client.post("/api/fleet/cloud/servers", json=SERVER)
        job = wait_for(response.json()["job_id"])

        assert TOKEN not in response.text
        assert TOKEN not in str(job.to_dict())
        assert TOKEN.encode() not in get_store().db_path.read_bytes()


def test_the_node_record_a_real_provision_returns_has_nothing_secret(tmp_path: Path) -> None:
    record = NodeRecord(
        name="web-2",
        ssh_host="203.0.113.9",
        ssh_port=22,
        ssh_user="noust-tunnel",
        host_key="k",
        console_port=8080,
    )

    assert "token" not in str(record.to_dict()).lower()
