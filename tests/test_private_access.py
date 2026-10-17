# ruff: noqa: F811
# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An application that answers a private network only: a tailnet, a WireGuard interface.

Two halves. What the machine is on (``ip -j`` and ``tailscale status --json``, only asked) and what
an application does about it, which is the site's own list of allowed addresses set by name. The
second is exercised through the real policy machinery on the domains harness, so what is pinned is
the site that comes out and the refusals that matter (a public range, the operator's own address),
not a mock of them. Tailscale's certificate for the server's name is kept as an uploaded one; what
is pinned there is when it is refused, the exact command, and that the upkeep asks again.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.certificate_store import CertificateStore
from noust.core.exceptions import CertificateError, ValidationError
from noust.core.runner import FakeRunner
from noust.core.teams.model import ResourceNotFoundError
from noust.deployers import access, certificates, private_access
from noust.managers.private_networks import (
    TAILSCALE_CIDRS,
    PrivateNetworks,
    checked_cidrs,
    is_private_range,
)
from tests.test_domains import (  # noqa: F401 - fixtures of the domains harness
    Machine,
    certs,
    machine,
    store,
    web,
)

TAILNET = "web.tail1234.ts.net"
KEY = "-----BEGIN PRIVATE KEY-----\nMIIB\n-----END PRIVATE KEY-----\n"
CERT = "-----BEGIN CERTIFICATE-----\nMIIC\n-----END CERTIFICATE-----\n"


def tailscale_status(**changes: Any) -> str:
    document: dict[str, Any] = {
        "BackendState": "Running",
        "Self": {
            "DNSName": f"{TAILNET}.",
            "TailscaleIPs": ["100.101.102.103", "fd7a:115c:a1e0::1"],
        },
        "CurrentTailnet": {"Name": "example.org", "MagicDNSSuffix": "tail1234.ts.net"},
        "CertDomains": [TAILNET],
    }
    document.update(changes)
    return json.dumps(document)


def addresses(*interfaces: tuple[str, str]) -> str:
    return json.dumps(
        [
            {
                "ifname": name,
                "addr_info": [
                    {
                        "family": "inet6" if ":" in cidr else "inet",
                        "local": cidr.split("/")[0],
                        "prefixlen": int(cidr.split("/")[1]),
                        "scope": "global",
                    }
                ],
            }
            for name, cidr in interfaces
        ]
    )


def links(*names: str) -> str:
    return json.dumps([{"ifname": name, "link_type": "none"} for name in names])


def script_machine(
    runner: FakeRunner,
    *,
    tailscale: str | None = None,
    wireguard: tuple[str, ...] = (),
    interfaces: tuple[tuple[str, str], ...] = (),
) -> None:
    if tailscale is None:
        runner.only_knows("ip", "nginx", "openssl", "certbot")
    else:
        runner.script(["tailscale", "status", "--json"], stdout=tailscale)
    runner.script(["ip", "-j", "link", "show", "type", "wireguard"], stdout=links(*wireguard))
    runner.script(["ip", "-j", "address", "show"], stdout=addresses(*interfaces))


# -- what the machine is on --------------------------------------------------------


def test_a_running_tailnet_is_found_with_its_name_and_the_names_it_issues_certificates_for(
    runner: FakeRunner,
) -> None:
    script_machine(
        runner, tailscale=tailscale_status(), interfaces=(("tailscale0", "100.101.102.103/32"),)
    )

    (tailnet,) = PrivateNetworks(runner).discover()

    assert (tailnet.name, tailnet.kind, tailnet.up) == ("tailscale", "tailscale", True)
    assert tailnet.dns_name == TAILNET and tailnet.cert_domains == (TAILNET,)
    assert tailnet.tailnet == "example.org" and tailnet.interface == "tailscale0"
    assert tailnet.cidrs == TAILSCALE_CIDRS
    assert tailnet.addresses == ("100.101.102.103", "fd7a:115c:a1e0::1")


def test_a_stopped_tailnet_is_listed_as_down_with_the_reason(runner: FakeRunner) -> None:
    script_machine(runner, tailscale=tailscale_status(BackendState="Stopped"))

    (tailnet,) = PrivateNetworks(runner).discover()

    assert not tailnet.up and "Stopped" in tailnet.problem


def test_tailscale_that_answers_nothing_useful_is_a_network_that_is_down(
    runner: FakeRunner,
) -> None:
    script_machine(runner, tailscale="Failed to connect to local tailscaled")

    (tailnet,) = PrivateNetworks(runner).discover()

    assert not tailnet.up and "tailscaled" in tailnet.problem


def test_a_server_without_tailscale_is_on_no_tailnet_and_is_not_asked(runner: FakeRunner) -> None:
    script_machine(runner)

    assert PrivateNetworks(runner).discover() == []
    assert not any(call[0] == "tailscale" for call in runner.calls)


def test_a_wireguard_interface_is_the_network_its_own_address_is_in(runner: FakeRunner) -> None:
    script_machine(
        runner,
        wireguard=("wg0",),
        interfaces=(("wg0", "10.8.0.1/24"), ("eth0", "203.0.113.5/24")),
    )

    (wg,) = PrivateNetworks(runner).discover()

    assert (wg.name, wg.kind, wg.up) == ("wg0", "wireguard", True)
    assert wg.cidrs == ("10.8.0.0/24",) and wg.addresses == ("10.8.0.1",)


def test_a_wireguard_interface_without_an_address_is_down_and_a_strange_name_is_dropped(
    runner: FakeRunner,
) -> None:
    script_machine(runner, wireguard=("wg1", "wg;rm -rf"), interfaces=())

    (wg,) = PrivateNetworks(runner).discover()

    assert wg.name == "wg1" and not wg.up and "no address" in wg.problem


def test_an_ip_command_that_fails_means_no_networks_not_a_crash(runner: FakeRunner) -> None:
    runner.only_knows("ip")
    runner.script(["ip"], exit_code=1, stderr="Cannot open netlink socket")

    assert PrivateNetworks(runner).discover() == []


def test_a_network_that_is_not_there_is_a_404(runner: FakeRunner) -> None:
    script_machine(runner)

    with pytest.raises(ResourceNotFoundError):
        PrivateNetworks(runner).get("wg9")


@pytest.mark.parametrize(
    ("cidr", "private"),
    [
        ("10.0.0.0/8", True),
        ("172.16.5.0/24", True),
        ("192.168.1.10", True),
        ("100.64.0.0/10", True),
        ("100.101.0.0/16", True),
        ("fd7a:115c:a1e0::/48", True),
        ("fc00::/7", True),
        ("127.0.0.1", True),
        ("169.254.1.1", True),
        ("203.0.113.0/24", False),
        ("8.8.8.8", False),
        ("0.0.0.0/0", False),
        ("::/0", False),
        ("100.0.0.0/8", False),
        ("2001:4860::/32", False),
        ("not a network", False),
    ],
)
def test_which_ranges_are_private(cidr: str, private: bool) -> None:
    assert is_private_range(cidr) is private


def test_ranges_an_operator_names_are_checked_and_deduplicated() -> None:
    assert checked_cidrs(["10.8.0.5/24", "10.8.0.0/24", "192.168.1.1"]) == [
        "10.8.0.0/24",
        "192.168.1.1/32",
    ]
    with pytest.raises(ValidationError) as public:
        checked_cidrs(["10.0.0.0/8", "203.0.113.0/24"])
    assert public.value.field == "cidrs" and "public network" in public.value.message
    with pytest.raises(ValidationError):
        checked_cidrs(["banana"])
    with pytest.raises(ValidationError):
        checked_cidrs([f"10.0.{n}.0/24" for n in range(21)])


# -- an application ----------------------------------------------------------------


@pytest.fixture
def served(machine: Machine, runner: FakeRunner) -> Machine:
    machine.deploy(app_type="nodejs", tls=True)
    return machine


def test_an_application_answers_everybody_until_it_is_made_private(
    served: Machine, runner: FakeRunner
) -> None:
    script_machine(runner, tailscale=tailscale_status())

    status = private_access.status("example.com")

    assert not status.private and status.allow_ips == () and status.networks == ()
    assert [network.name for network in status.available] == ["tailscale"]
    assert status.tailnet_name == TAILNET


def test_making_it_private_allows_the_network_and_nothing_else_reaches_the_site(
    served: Machine, runner: FakeRunner
) -> None:
    script_machine(runner, tailscale=tailscale_status())

    status = private_access.enable("example.com", network="tailscale")

    assert status.private and status.networks == ("tailscale",)
    assert status.allow_ips == TAILSCALE_CIDRS
    config = served.config()
    assert "allow 100.64.0.0/10;" in config and "allow fd7a:115c:a1e0::/48;" in config
    assert "deny all;" in config
    # The certificate authority must still be able to reach the challenge on port 80.
    acme = config.split("location /.well-known/acme-challenge/ {")[1].split("}")[0]
    assert "allow all;" in acme


def test_the_public_ranges_a_list_had_go_and_its_private_ones_stay(
    served: Machine, runner: FakeRunner
) -> None:
    script_machine(runner, wireguard=("wg0",), interfaces=(("wg0", "10.8.0.1/24"),))
    access.set_policy("example.com", {"allow_ips": ["203.0.113.5", "192.168.0.0/16"]})

    status = private_access.enable("example.com", network="wg0", cidrs=["10.9.0.0/24"])

    assert status.allow_ips == ("10.8.0.0/24", "10.9.0.0/24", "192.168.0.0/16")
    assert status.private and status.networks == ("wg0",)


def test_a_site_with_a_public_range_among_the_allowed_is_not_private(
    served: Machine, runner: FakeRunner
) -> None:
    script_machine(runner, tailscale=tailscale_status())
    access.set_policy("example.com", {"allow_ips": ["10.0.0.0/8", "203.0.113.0/24"]})

    status = private_access.status("example.com")

    assert not status.private and status.public_ranges == ("203.0.113.0/24",)


def test_the_answer_follows_a_list_edited_by_hand(served: Machine, runner: FakeRunner) -> None:
    script_machine(runner, tailscale=tailscale_status())
    private_access.enable("example.com", network="tailscale")

    access.set_policy("example.com", {"allow_ips": ["100.64.0.0/10", "198.51.100.1"]})

    assert not private_access.status("example.com").private


def test_a_public_range_is_refused_before_anything_is_written(
    served: Machine, runner: FakeRunner
) -> None:
    script_machine(runner, tailscale=tailscale_status())

    with pytest.raises(ValidationError) as raised:
        private_access.enable("example.com", cidrs=["203.0.113.0/24"])

    assert raised.value.field == "cidrs"
    config = served.config()
    assert "allow 203.0.113" not in config and "deny all;" not in config


def test_nothing_named_is_a_question(served: Machine, runner: FakeRunner) -> None:
    script_machine(runner)

    with pytest.raises(ValidationError) as raised:
        private_access.enable("example.com")

    assert "Name a network or a range" in raised.value.message


def test_a_network_that_is_down_is_not_published_to(served: Machine, runner: FakeRunner) -> None:
    script_machine(runner, tailscale=tailscale_status(BackendState="Stopped"))

    with pytest.raises(ValidationError) as raised:
        private_access.enable("example.com", network="tailscale")

    assert raised.value.field == "network" and "Stopped" in raised.value.message


def test_an_operator_who_would_be_refused_is_asked_first(
    served: Machine, runner: FakeRunner
) -> None:
    script_machine(runner, tailscale=tailscale_status())

    with pytest.raises(ValidationError) as raised:
        private_access.enable(
            "example.com", network="tailscale", operator_addresses=["203.0.113.9"]
        )
    assert raised.value.field == "allow_ips"
    assert "your own address, 203.0.113.9" in raised.value.message
    assert not private_access.status("example.com").private

    status = private_access.enable(
        "example.com",
        network="tailscale",
        operator_addresses=["203.0.113.9"],
        accept_lockout=True,
    )
    assert status.private


def test_an_operator_on_the_tailnet_is_not_refused(served: Machine, runner: FakeRunner) -> None:
    script_machine(runner, tailscale=tailscale_status())

    status = private_access.enable(
        "example.com", network="tailscale", operator_addresses=["100.101.102.50"]
    )

    assert status.private


def test_making_it_public_again_takes_the_private_ranges_off_and_leaves_a_public_one(
    served: Machine, runner: FakeRunner
) -> None:
    script_machine(runner, tailscale=tailscale_status())
    private_access.enable("example.com", network="tailscale")
    access.set_policy("example.com", {"allow_ips": [*TAILSCALE_CIDRS, "198.51.100.1"]})

    status = private_access.disable("example.com", accept_lockout=True)

    assert status.allow_ips == ("198.51.100.1",)
    assert not status.private


def test_making_it_public_again_when_it_was_not_private_changes_nothing(
    served: Machine, runner: FakeRunner
) -> None:
    script_machine(runner, tailscale=tailscale_status())
    before = served.config()

    private_access.disable("example.com")

    assert served.config() == before


# -- the tailnet's own certificate -------------------------------------------------


class Uploads:
    """Stands in for the store of uploaded certificates and for serving one."""

    def __init__(self) -> None:
        self.uploaded: list[tuple[str, str, str]] = []
        self.served: list[tuple[str, str]] = []
        self.records: list[SimpleNamespace] = []
        self.users: dict[str, list[str]] = {}


@pytest.fixture
def uploads(monkeypatch: pytest.MonkeyPatch, store: Any) -> Uploads:
    fake = Uploads()

    def upload(self: Any, name: str, certificate: str, key: str, *args: Any, **kwargs: Any) -> Any:
        fake.uploaded.append((name, certificate, key))
        return SimpleNamespace(name=name)

    def domains_using(self: Any, kind: str, name: str) -> list[str]:
        return fake.users.get(name, [])

    monkeypatch.setattr(private_access.CustomCertificates, "upload", upload)
    monkeypatch.setattr(private_access.CustomCertificates, "uploaded", lambda self: fake.records)
    monkeypatch.setattr(
        private_access.CustomCertificates,
        "get",
        lambda self, name: SimpleNamespace(name=name, not_after="2027-01-01T00:00:00+00:00"),
    )
    monkeypatch.setattr(CertificateStore, "domains_using", domains_using)
    monkeypatch.setattr(
        certificates,
        "use_uploaded",
        lambda app, name, verbose=False: fake.served.append((app, name)),
    )
    return fake


def issue(runner: FakeRunner, **kwargs: Any) -> None:
    runner.script(["tailscale", "cert"], stdout=CERT + KEY, **kwargs)


def test_the_tailnets_certificate_is_asked_for_by_name_kept_and_served(
    machine: Machine, runner: FakeRunner, uploads: Uploads
) -> None:
    machine.deploy(TAILNET, app_type="nodejs", tls=True)
    script_machine(runner, tailscale=tailscale_status())
    issue(runner)

    issued = private_access.tailnet_certificate(TAILNET)

    assert tuple(runner.calls[-1][:2]) == ("tailscale", "cert")
    argv = runner.calls[-1]
    assert argv[argv.index("--cert-file") + 1] == "-" and argv[argv.index("--key-file") + 1] == "-"
    assert argv[-1] == TAILNET and "--min-validity" in argv
    assert uploads.uploaded == [(f"tailnet-{TAILNET}", CERT, KEY)]
    assert uploads.served == [(TAILNET, f"tailnet-{TAILNET}")]
    assert issued.name == f"tailnet-{TAILNET}" and issued.hostname == TAILNET


def test_an_application_that_also_answers_on_a_public_name_is_refused_before_tailscale_is_asked(
    machine: Machine, runner: FakeRunner, uploads: Uploads
) -> None:
    machine.deploy("example.com", app_type="nodejs", tls=True)
    script_machine(runner, tailscale=tailscale_status())

    with pytest.raises(ValidationError) as raised:
        private_access.tailnet_certificate("example.com")

    assert "also answers on example.com" in raised.value.message
    assert not any(call[:2] == ("tailscale", "cert") for call in runner.calls)
    assert uploads.uploaded == []


def test_a_tailnet_that_does_not_issue_certificates_says_where_to_turn_them_on(
    machine: Machine, runner: FakeRunner, uploads: Uploads
) -> None:
    machine.deploy(TAILNET, app_type="nodejs", tls=True)
    script_machine(runner, tailscale=tailscale_status(CertDomains=[]))

    with pytest.raises(ValidationError) as raised:
        private_access.tailnet_certificate(TAILNET)

    assert "HTTPS" in raised.value.details


def test_a_server_that_is_on_no_tailnet_is_told_to_join_one(
    machine: Machine, runner: FakeRunner, uploads: Uploads
) -> None:
    machine.deploy(TAILNET, app_type="nodejs", tls=True)
    script_machine(runner)

    with pytest.raises(ValidationError) as raised:
        private_access.tailnet_certificate(TAILNET)

    assert "tailscale up" in raised.value.details


def test_tailscale_refusing_is_one_error_with_its_words(
    machine: Machine, runner: FakeRunner, uploads: Uploads
) -> None:
    machine.deploy(TAILNET, app_type="nodejs", tls=True)
    script_machine(runner, tailscale=tailscale_status())
    runner.script(["tailscale", "cert"], exit_code=1, stderr="500 Internal Server Error: no ACME")

    with pytest.raises(CertificateError) as raised:
        private_access.tailnet_certificate(TAILNET)

    assert "no ACME" in (raised.value.output or "")
    assert uploads.uploaded == [] and uploads.served == []


def test_an_answer_with_no_key_is_not_kept(
    machine: Machine, runner: FakeRunner, uploads: Uploads
) -> None:
    machine.deploy(TAILNET, app_type="nodejs", tls=True)
    script_machine(runner, tailscale=tailscale_status())
    runner.script(["tailscale", "cert"], stdout=CERT)

    with pytest.raises(CertificateError):
        private_access.tailnet_certificate(TAILNET)

    assert uploads.uploaded == []


def record(name: str, days: int) -> SimpleNamespace:
    expires = datetime.now(timezone.utc) + timedelta(days=days)
    return SimpleNamespace(name=name, not_after=expires.isoformat())


def test_the_upkeep_asks_again_only_for_a_tailnet_certificate_that_is_about_to_expire(
    machine: Machine, runner: FakeRunner, uploads: Uploads
) -> None:
    machine.deploy(TAILNET, app_type="nodejs", tls=True)
    uploads.records = [
        record(f"tailnet-{TAILNET}", 10),
        record("tailnet-other.tail1234.ts.net", 80),
        record("origin", 3),
    ]
    uploads.users = {f"tailnet-{TAILNET}": [TAILNET]}
    issue(runner)

    assert private_access.renewal_due()
    renewed = private_access.renew_tailnet_certificates()

    assert renewed == [f"tailnet-{TAILNET}"]
    assert [name for name, *_ in uploads.uploaded] == [f"tailnet-{TAILNET}"]
    asked = [call for call in runner.calls if call[:2] == ("tailscale", "cert")]
    assert len(asked) == 1 and asked[0][-1] == TAILNET


def test_nothing_is_due_while_every_certificate_has_time(
    runner: FakeRunner, uploads: Uploads
) -> None:
    uploads.records = [record(f"tailnet-{TAILNET}", 60), record("origin", 2)]

    assert not private_access.renewal_due()
    assert private_access.renew_tailnet_certificates() == []
    assert not any(call[:2] == ("tailscale", "cert") for call in runner.calls)
