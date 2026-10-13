"""The Hetzner, DigitalOcean and Vultr adapters, against fake APIs: no real call is ever made."""

from __future__ import annotations

import base64
from typing import Any

import pytest

from noust.core.exceptions import CloudProviderError, ValidationError
from noust.core.runner import DryRunRunner, FakeRunner, set_runner
from noust.fleet.cloud.http import CloudHttp
from noust.fleet.cloud.models import CreateSpec
from noust.fleet.cloud.providers import (
    PROVIDERS,
    DigitalOcean,
    HetznerCloud,
    Vultr,
    describe_providers,
    provider_for,
)
from tests.cloud_support import FakeApi

TOKEN = "tok" + "-" + "S" * 24


def _no_sleep(_seconds: float) -> None:
    """Never wait."""


def spec(**changes: Any) -> CreateSpec:
    """Build a request for a machine, with the fields a test varies."""
    values: dict[str, Any] = {
        "name": "web-2",
        "location": "fsn1",
        "plan": "cx22",
        "image": "ubuntu-24.04",
        "user_data": "#cloud-config\n",
    }
    values.update(changes)
    return CreateSpec(**values)


class TestRegistry:
    def test_the_three_providers_are_registered_by_key(self):
        assert set(PROVIDERS) == {"hetzner", "digitalocean", "vultr"}

    def test_an_unknown_provider_is_refused_with_the_list(self):
        with pytest.raises(ValidationError) as caught:
            provider_for("aws", TOKEN)
        assert "hetzner" in (caught.value.details or "")

    def test_every_provider_says_where_its_token_is_made(self):
        for entry in describe_providers():
            assert entry["token_url"].startswith("https://")
            assert entry["token_help"]

    def test_the_client_refuses_a_host_that_is_not_https(self):
        with pytest.raises(CloudProviderError):
            CloudHttp("X", "http://api.example", TOKEN)

    def test_the_client_does_not_show_its_token(self):
        client = CloudHttp("X", "https://api.example", TOKEN)
        assert TOKEN not in repr(client)
        assert TOKEN not in str(client)


@pytest.fixture
def hetzner() -> tuple[HetznerCloud, FakeApi]:
    api = FakeApi(prefix="/v1")
    return HetznerCloud(TOKEN, opener=api, sleep=_no_sleep), api


class TestHetzner:
    def test_it_authenticates_with_the_bearer_token_on_every_call(self, hetzner):
        provider, api = hetzner
        api.on("GET", "/locations", (200, {"locations": []}))

        provider.verify()

        assert api.calls[0].headers["authorization"] == f"Bearer {TOKEN}"
        assert api.calls[0].headers["user-agent"].startswith("noust/")

    def test_a_list_is_read_across_every_page(self, hetzner):
        provider, api = hetzner
        page_two = {
            "ssh_keys": [{"id": 2, "name": "b", "fingerprint": "bb"}],
            "meta": {"pagination": {"next_page": None}},
        }
        api.on(
            "GET",
            "/ssh_keys",
            lambda call: (
                200,
                {
                    "ssh_keys": [{"id": 1, "name": "a", "fingerprint": "aa"}],
                    "meta": {"pagination": {"next_page": 2}},
                }
                if call.q("page") == "1"
                else page_two,
            ),
        )

        keys = provider.ssh_keys()

        assert [(k.id, k.name) for k in keys] == [("1", "a"), ("2", "b")]
        assert len(api.calls) == 2

    def test_a_catalog_has_locations_plans_images_and_what_to_attach(self, hetzner):
        provider, api = hetzner
        api.on(
            "GET",
            "/locations",
            (
                200,
                {
                    "locations": [
                        {
                            "name": "fsn1",
                            "city": "Falkenstein",
                            "country": "DE",
                            "description": "Falkenstein DC Park 1",
                            "network_zone": "eu-central",
                        }
                    ]
                },
            ),
        )
        api.on(
            "GET",
            "/server_types",
            (
                200,
                {
                    "server_types": [
                        {
                            "name": "cx22",
                            "cores": 2,
                            "memory": 4,
                            "disk": 40,
                            "architecture": "x86",
                            "prices": [
                                {"location": "fsn1", "price_monthly": {"gross": "4.5900"}},
                                {"location": "nbg1", "price_monthly": {"gross": "4.5900"}},
                            ],
                        },
                        {"name": "cx11", "deprecated": True, "cores": 1, "memory": 2, "disk": 20},
                    ]
                },
            ),
        )
        api.on(
            "GET",
            "/images",
            (
                200,
                {
                    "images": [
                        {
                            "name": "ubuntu-24.04",
                            "description": "Ubuntu 24.04",
                            "os_flavor": "ubuntu",
                            "os_version": "24.04",
                            "architecture": "x86",
                        },
                        {
                            "name": "ubuntu-24.04",
                            "description": "Ubuntu 24.04",
                            "os_flavor": "ubuntu",
                            "os_version": "24.04",
                            "architecture": "arm",
                        },
                        {"name": "old", "deprecated": "2024-01-01T00:00:00+00:00"},
                    ]
                },
            ),
        )
        api.on("GET", "/ssh_keys", (200, {"ssh_keys": []}))
        api.on("GET", "/firewalls", (200, {"firewalls": [{"id": 7, "name": "web"}]}))
        api.on(
            "GET",
            "/networks",
            (200, {"networks": [{"id": 9, "name": "lan", "ip_range": "10.0.0.0/16"}]}),
        )

        catalog = provider.catalog().to_dict()

        assert catalog["locations"][0]["id"] == "fsn1"
        assert catalog["locations"][0]["name"] == "Falkenstein, DE (fsn1)"
        plan = catalog["plans"][0]
        assert [p["id"] for p in catalog["plans"]] == ["cx22"]
        assert plan["detail"] == "2 vCPU, 4 GB RAM, 40 GB disk"
        assert plan["locations"] == ["fsn1", "nbg1"]
        assert plan["monthly"] == 4.59
        assert plan["arch"] == "x86"
        assert [i["id"] for i in catalog["images"]] == ["ubuntu-24.04"]
        assert catalog["images"][0]["archs"] == ["x86", "arm"]
        assert catalog["firewalls"] == [{"id": "7", "name": "web", "detail": ""}]
        assert catalog["networks"][0]["detail"] == "10.0.0.0/16"

    def test_a_server_is_created_with_everything_asked_and_a_managed_label(self, hetzner):
        provider, api = hetzner
        api.on(
            "POST",
            "/servers",
            (
                201,
                {
                    "server": {
                        "id": 42,
                        "name": "web-2",
                        "status": "initializing",
                        "public_net": {
                            "ipv4": {"ip": "203.0.113.9"},
                            "ipv6": {"ip": "2001:db8:1::/64"},
                        },
                    },
                    "root_password": "never-read",
                },
            ),
        )

        server = provider.create_server(
            spec(ssh_keys=["11", "mine"], firewalls=["7"], networks=["9"], ipv4=True, ipv6=False)
        )

        body = api.calls[0].body
        assert body == {
            "name": "web-2",
            "server_type": "cx22",
            "image": "ubuntu-24.04",
            "location": "fsn1",
            "start_after_create": True,
            "public_net": {"enable_ipv4": True, "enable_ipv6": False},
            "labels": {"managed-by": "noust"},
            "user_data": "#cloud-config\n",
            "ssh_keys": [11, "mine"],
            "firewalls": [{"firewall": 7}],
            "networks": [9],
        }
        assert (server.id, server.state, server.ipv4) == ("42", "creating", "203.0.113.9")
        assert server.ipv6 == "2001:db8:1::1"
        assert "never-read" not in repr(server)

    def test_backups_are_turned_on_after_the_server_exists(self, hetzner):
        provider, api = hetzner
        api.on("POST", "/servers/42/actions/enable_backup", (201, {"action": {}}))

        provider.after_create(
            provider._server({"id": 42, "name": "x", "status": "initializing"}), spec(backups=True)
        )

        assert len(api.sent("POST", "/servers/42/actions/enable_backup")) == 1

    @pytest.mark.parametrize(
        ("raw", "state"),
        [
            ("initializing", "creating"),
            ("starting", "creating"),
            ("running", "running"),
            ("off", "stopped"),
            ("deleting", "deleting"),
            ("mystery", "unknown"),
        ],
    )
    def test_states_are_translated(self, hetzner, raw, state):
        provider, api = hetzner
        api.on("GET", "/servers/5", (200, {"server": {"id": 5, "name": "n", "status": raw}}))

        assert provider.get_server("5").state == state

    def test_a_server_without_a_public_address_has_none(self, hetzner):
        provider, api = hetzner
        api.on(
            "GET",
            "/servers/5",
            (
                200,
                {
                    "server": {
                        "id": 5,
                        "status": "running",
                        "public_net": {"ipv4": None, "ipv6": None},
                    }
                },
            ),
        )

        assert provider.get_server("5").address is None

    def test_delete_and_power_on_use_their_endpoints(self, hetzner):
        provider, api = hetzner
        api.on("DELETE", "/servers/5", (200, {"action": {}}))
        api.on("POST", "/servers/5/actions/poweron", (201, {"action": {}}))
        api.on("DELETE", "/ssh_keys/3", (200, None))

        provider.delete_server("5")
        provider.power_on("5")
        provider.delete_ssh_key("3")

        assert [c.method for c in api.calls] == ["DELETE", "POST", "DELETE"]

    def test_a_key_is_uploaded_and_its_id_returned(self, hetzner):
        provider, api = hetzner
        api.on("POST", "/ssh_keys", (201, {"ssh_key": {"id": 77}}))

        assert provider.upload_ssh_key("noust-web-2", "ssh-ed25519 AAAA") == "77"
        assert api.calls[0].body == {"name": "noust-web-2", "public_key": "ssh-ed25519 AAAA"}

    def test_the_providers_sentence_is_kept_verbatim_and_the_token_is_not_in_it(self, hetzner):
        provider, api = hetzner
        api.on(
            "POST",
            "/servers",
            (
                422,
                {"error": {"code": "invalid_input", "message": f"server type unavailable {TOKEN}"}},
            ),
        )

        with pytest.raises(CloudProviderError) as caught:
            provider.create_server(spec())

        assert "server type unavailable" in (caught.value.output or "")
        assert TOKEN not in (caught.value.output or "")
        assert TOKEN not in str(caught.value)
        assert caught.value.status == 422

    def test_a_create_that_hit_a_server_error_is_not_sent_again(self, hetzner):
        provider, api = hetzner
        api.on("POST", "/servers", (503, {"error": {"message": "overloaded"}}))

        with pytest.raises(CloudProviderError):
            provider.create_server(spec())

        assert len(api.sent("POST", "/servers")) == 1

    def test_a_read_that_hit_a_server_error_is_retried(self, hetzner):
        provider, api = hetzner
        answers = iter(
            [(503, {"error": {"message": "x"}}), (200, {"server": {"id": 5, "status": "running"}})]
        )
        api.on("GET", "/servers/5", lambda call: next(answers))

        assert provider.get_server("5").state == "running"
        assert len(api.calls) == 2

    def test_a_rate_limit_is_waited_out_even_for_a_create(self, hetzner):
        provider, api = hetzner
        waits: list[float] = []
        provider.http._sleep = waits.append
        answers = iter(
            [
                (429, {"error": {"message": "slow down"}}, {"Retry-After": "7"}),
                (201, {"server": {"id": 1, "status": "initializing"}}),
            ]
        )
        api.on("POST", "/servers", lambda call: next(answers))

        provider.create_server(spec())

        assert waits == [7.0]
        assert len(api.sent("POST", "/servers")) == 2

    def test_a_rate_limit_that_keeps_coming_is_given_up(self, hetzner):
        provider, api = hetzner
        api.on("GET", "/servers/5", (429, {"error": {"message": "slow"}}, {"Retry-After": "1"}))

        with pytest.raises(CloudProviderError) as caught:
            provider.get_server("5")

        assert caught.value.status == 429
        assert caught.value.retry_after == 1
        assert len(api.calls) == 3

    def test_an_unauthorized_token_says_to_make_a_new_one(self, hetzner):
        provider, api = hetzner
        api.on("GET", "/locations", (401, {"error": {"message": "unable to authenticate"}}))

        with pytest.raises(CloudProviderError) as caught:
            provider.verify()

        assert "new one" in caught.value.details
        assert caught.value.output == "unable to authenticate"

    def test_a_dry_run_sends_nothing_that_changes_anything(self, hetzner):
        provider, api = hetzner
        set_runner(DryRunRunner(FakeRunner()))
        try:
            with pytest.raises(CloudProviderError) as caught:
                provider.create_server(spec())
            api.on("GET", "/locations", (200, {"locations": []}))
            provider.verify()
        finally:
            set_runner(None)

        assert "dry run" in caught.value.message.lower()
        assert [c.method for c in api.calls] == ["GET"]

    def test_a_request_that_asks_for_no_address_at_all_is_refused_before_it_is_sent(self, hetzner):
        provider, api = hetzner

        with pytest.raises(ValidationError):
            provider.create_server(spec(ipv4=False, ipv6=False))

        assert api.calls == []

    def test_a_document_larger_than_the_limit_is_refused_before_it_is_sent(self, hetzner):
        provider, api = hetzner

        with pytest.raises(ValidationError) as caught:
            provider.create_server(spec(user_data="#" * (provider.user_data_limit + 1)))

        assert "bytes" in caught.value.message
        assert api.calls == []


@pytest.fixture
def digitalocean() -> tuple[DigitalOcean, FakeApi]:
    api = FakeApi(prefix="/v2")
    return DigitalOcean(TOKEN, opener=api, sleep=_no_sleep), api


class TestDigitalOcean:
    def test_verify_returns_the_account_email(self, digitalocean):
        provider, api = digitalocean
        api.on("GET", "/account", (200, {"account": {"email": "ops@example.com"}}))

        assert provider.verify() == "ops@example.com"

    def test_pages_follow_the_next_link(self, digitalocean):
        provider, api = digitalocean
        api.on(
            "GET",
            "/regions",
            lambda call: (
                200,
                {
                    "regions": [{"slug": "ams3", "name": "Amsterdam 3", "available": True}],
                    "links": {"pages": {"next": "https://x/?page=2"}},
                }
                if call.q("page") == "1"
                else {
                    "regions": [
                        {"slug": "nyc1", "name": "New York 1", "available": True},
                        {"slug": "old", "name": "Old", "available": False},
                    ],
                    "links": {},
                },
            ),
        )

        assert [r.id for r in provider.locations()] == ["ams3", "nyc1"]

    def test_sizes_keep_the_regions_they_are_sold_in(self, digitalocean):
        provider, api = digitalocean
        api.on(
            "GET",
            "/sizes",
            (
                200,
                {
                    "sizes": [
                        {
                            "slug": "s-2vcpu-4gb",
                            "description": "Basic",
                            "vcpus": 2,
                            "memory": 4096,
                            "disk": 80,
                            "price_monthly": 24,
                            "regions": ["ams3", "nyc1"],
                            "available": True,
                        },
                        {"slug": "gone", "available": False},
                    ]
                },
            ),
        )

        (plan,) = provider.plans()

        assert plan.detail == "2 vCPU, 4 GB RAM, 80 GB disk"
        assert plan.extra["locations"] == ["ams3", "nyc1"]
        assert plan.extra["monthly"] == 24

    def test_only_public_distribution_images_are_listed(self, digitalocean):
        provider, api = digitalocean
        api.on(
            "GET",
            "/images",
            (
                200,
                {
                    "images": [
                        {
                            "slug": "ubuntu-24-04-x64",
                            "distribution": "Ubuntu",
                            "name": "24.04 (LTS) x64",
                            "public": True,
                        },
                        {"id": 5, "distribution": "Mine", "name": "private", "public": False},
                    ]
                },
            ),
        )

        (image,) = provider.images()

        assert image.id == "ubuntu-24-04-x64"
        assert image.name == "Ubuntu 24.04 (LTS) x64"
        assert api.calls[0].query["type"] == ["distribution"]

    def test_a_droplet_is_created_with_the_key_vpc_tag_and_user_data(self, digitalocean):
        provider, api = digitalocean
        api.on(
            "POST",
            "/droplets",
            (
                202,
                {
                    "droplet": {
                        "id": 99,
                        "name": "web-2",
                        "status": "new",
                        "networks": {"v4": [], "v6": []},
                    }
                },
            ),
        )

        droplet = provider.create_server(
            spec(
                location="ams3",
                plan="s-1vcpu-1gb",
                image="ubuntu-24-04-x64",
                ssh_keys=["5"],
                networks=["vpc-1"],
                backups=True,
            )
        )

        assert api.calls[0].body == {
            "name": "web-2",
            "region": "ams3",
            "size": "s-1vcpu-1gb",
            "image": "ubuntu-24-04-x64",
            "ipv6": True,
            "backups": True,
            "tags": ["noust"],
            "user_data": "#cloud-config\n",
            "ssh_keys": [5],
            "vpc_uuid": "vpc-1",
        }
        assert droplet.state == "creating"
        assert droplet.address is None

    def test_firewalls_are_attached_from_the_firewalls_side_after_creation(self, digitalocean):
        provider, api = digitalocean
        api.on("POST", "/firewalls/fw-1/droplets", (204, None))
        api.on("POST", "/firewalls/fw-2/droplets", (204, None))

        provider.after_create(
            provider._server({"id": 99, "status": "new"}), spec(firewalls=["fw-1", "fw-2"])
        )

        assert [c.body for c in api.calls] == [{"droplet_ids": [99]}, {"droplet_ids": [99]}]

    def test_the_public_addresses_are_read_and_the_private_one_is_not(self, digitalocean):
        provider, api = digitalocean
        api.on(
            "GET",
            "/droplets/99",
            (
                200,
                {
                    "droplet": {
                        "id": 99,
                        "name": "n",
                        "status": "active",
                        "networks": {
                            "v4": [
                                {"type": "private", "ip_address": "10.0.0.2"},
                                {"type": "public", "ip_address": "198.51.100.4"},
                            ],
                            "v6": [{"type": "public", "ip_address": "2001:db8::4"}],
                        },
                    }
                },
            ),
        )

        droplet = provider.get_server("99")

        assert (droplet.state, droplet.ipv4, droplet.ipv6) == (
            "running",
            "198.51.100.4",
            "2001:db8::4",
        )

    def test_the_providers_message_is_read_from_message(self, digitalocean):
        provider, api = digitalocean
        api.on(
            "POST",
            "/droplets",
            (422, {"id": "unprocessable_entity", "message": "Size is not available"}),
        )

        with pytest.raises(CloudProviderError) as caught:
            provider.create_server(spec(plan="s-1vcpu-1gb"))

        assert caught.value.output == "Size is not available"

    def test_it_cannot_be_asked_for_no_ipv4_or_two_networks(self, digitalocean):
        provider, _api = digitalocean

        with pytest.raises(ValidationError):
            provider.create_server(spec(ipv4=False))
        with pytest.raises(ValidationError):
            provider.create_server(spec(networks=["a", "b"]))

    def test_keys_delete_and_power_on(self, digitalocean):
        provider, api = digitalocean
        api.on("POST", "/account/keys", (201, {"ssh_key": {"id": 12}}))
        api.on("DELETE", "/account/keys/12", (204, None))
        api.on("DELETE", "/droplets/99", (204, None))
        api.on("POST", "/droplets/99/actions", (201, {"action": {}}))

        assert provider.upload_ssh_key("k", "ssh-ed25519 AAAA") == "12"
        provider.delete_ssh_key("12")
        provider.delete_server("99")
        provider.power_on("99")

        assert api.calls[-1].body == {"type": "power_on"}


@pytest.fixture
def vultr() -> tuple[Vultr, FakeApi]:
    api = FakeApi(prefix="/v2")
    return Vultr(TOKEN, opener=api, sleep=_no_sleep), api


class TestVultr:
    def test_pages_follow_the_cursor(self, vultr):
        provider, api = vultr
        api.on(
            "GET",
            "/regions",
            lambda call: (
                200,
                {
                    "regions": [{"id": "ams", "city": "Amsterdam", "country": "NL"}],
                    "meta": {
                        "links": {
                            "next": "https://api.vultr.com/v2/regions?cursor=abc&per_page=100"
                        }
                    },
                }
                if not call.q("cursor")
                else {
                    "regions": [{"id": "fra", "city": "Frankfurt", "country": "DE"}],
                    "meta": {"links": {"next": ""}},
                },
            ),
        )

        assert [r.id for r in provider.locations()] == ["ams", "fra"]
        assert api.calls[1].q("cursor") == "abc"

    def test_operating_systems_leave_out_isos_and_snapshots(self, vultr):
        provider, api = vultr
        api.on(
            "GET",
            "/os",
            (
                200,
                {
                    "os": [
                        {
                            "id": 2284,
                            "name": "Ubuntu 24.04 LTS x64",
                            "arch": "x64",
                            "family": "ubuntu",
                        },
                        {"id": 159, "name": "Custom", "arch": "x64", "family": "iso"},
                        {"id": 164, "name": "Snapshot", "arch": "x64", "family": "snapshot"},
                    ]
                },
            ),
        )

        assert [i.id for i in provider.images()] == ["2284"]

    def test_plans_keep_their_locations_and_price(self, vultr):
        provider, api = vultr
        api.on(
            "GET",
            "/plans",
            (
                200,
                {
                    "plans": [
                        {
                            "id": "vc2-1c-1gb",
                            "vcpu_count": 1,
                            "ram": 1024,
                            "disk": 25,
                            "monthly_cost": 5,
                            "locations": ["ams", "fra"],
                        }
                    ]
                },
            ),
        )

        (plan,) = provider.plans()

        assert plan.detail == "1 vCPU, 1 GB RAM, 25 GB disk"
        assert plan.extra["locations"] == ["ams", "fra"]
        assert api.calls[0].q("type") == "all"

    def test_an_instance_is_created_with_base64_user_data(self, vultr):
        provider, api = vultr
        api.on(
            "POST",
            "/instances",
            (
                202,
                {
                    "instance": {
                        "id": "abc-123",
                        "label": "web-2",
                        "status": "pending",
                        "power_status": "running",
                        "server_status": "none",
                        "main_ip": "0.0.0.0",  # noqa: S104 - the placeholder Vultr reports
                        "v6_main_ip": "",
                        "default_password": "never-read",
                    }
                },
            ),
        )

        instance = provider.create_server(
            spec(
                location="ams",
                plan="vc2-1c-1gb",
                image="2284",
                ssh_keys=["k1"],
                firewalls=["fw"],
                networks=["n1", "n2"],
                ipv6=False,
            )
        )

        body = api.calls[0].body
        assert base64.b64decode(body.pop("user_data")).decode() == "#cloud-config\n"
        assert body == {
            "region": "ams",
            "plan": "vc2-1c-1gb",
            "os_id": 2284,
            "label": "web-2",
            "hostname": "web-2",
            "enable_ipv6": False,
            "disable_public_ipv4": False,
            "backups": "disabled",
            "tags": ["noust"],
            "sshkey_id": ["k1"],
            "firewall_group_id": "fw",
            "attach_vpc": ["n1", "n2"],
        }
        assert (instance.state, instance.address) == ("creating", None)
        assert "never-read" not in repr(instance)

    @pytest.mark.parametrize(
        ("status", "power", "server_status", "state"),
        [
            ("pending", "running", "none", "creating"),
            ("active", "running", "installingbooting", "creating"),
            ("active", "running", "ok", "running"),
            ("active", "stopped", "ok", "stopped"),
            ("suspended", "stopped", "ok", "stopped"),
        ],
    )
    def test_states_are_translated(self, vultr, status, power, server_status, state):
        provider, api = vultr
        api.on(
            "GET",
            "/instances/i1",
            (
                200,
                {
                    "instance": {
                        "id": "i1",
                        "status": status,
                        "power_status": power,
                        "server_status": server_status,
                        "main_ip": "192.0.2.5",
                    }
                },
            ),
        )

        found = provider.get_server("i1")

        assert found.state == state
        assert found.ipv4 == "192.0.2.5"

    def test_two_firewalls_are_refused_because_vultr_takes_one_group(self, vultr):
        provider, _api = vultr

        with pytest.raises(ValidationError):
            provider.create_server(spec(firewalls=["a", "b"]))

    def test_the_providers_message_is_read_from_error(self, vultr):
        provider, api = vultr
        api.on("GET", "/account", (401, {"error": "Invalid API token.", "status": 401}))

        with pytest.raises(CloudProviderError) as caught:
            provider.verify()

        assert caught.value.output == "Invalid API token."

    def test_keys_delete_and_start(self, vultr):
        provider, api = vultr
        api.on("POST", "/ssh-keys", (201, {"ssh_key": {"id": "k9"}}))
        api.on("DELETE", "/ssh-keys/k9", (204, None))
        api.on("DELETE", "/instances/i1", (204, None))
        api.on("POST", "/instances/i1/start", (204, None))

        assert provider.upload_ssh_key("k", "ssh-ed25519 AAAA") == "k9"
        assert api.calls[0].body == {"name": "k", "ssh_key": "ssh-ed25519 AAAA"}
        provider.delete_ssh_key("k9")
        provider.delete_server("i1")
        provider.power_on("i1")


class TestACallThatLosesItsAnswer:
    def test_a_create_whose_connection_drops_tells_the_operator_to_look_before_retrying(
        self, hetzner
    ):
        provider, _api = hetzner

        def dropped(request: Any, timeout: float | None = None) -> Any:
            raise TimeoutError("timed out")

        provider.http._opener = dropped

        with pytest.raises(CloudProviderError) as caught:
            provider.create_server(spec())

        assert "console" in caught.value.details

    def test_a_read_whose_connection_drops_is_retried_and_then_says_so(self, hetzner):
        provider, _api = hetzner
        attempts = []

        def dropped(request: Any, timeout: float | None = None) -> Any:
            attempts.append(1)
            raise TimeoutError("timed out")

        provider.http._opener = dropped

        with pytest.raises(CloudProviderError) as caught:
            provider.get_server("1")

        assert len(attempts) == 3
        assert "could not be reached" in caught.value.message
