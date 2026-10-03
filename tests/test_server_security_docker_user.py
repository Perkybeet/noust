# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Docker's ``DOCKER-USER`` chain in the firewall's verdicts and in ``fw.docker_bypass``.

On the owner's central the check said "Docker publishes N port(s) on every
interface; ufw ... does not filter them" as critical, while
``iptables -S DOCKER-USER`` showed the operator's own ``DROP`` for exactly
those ports on the public interface. What is pinned, on real ``iptables -S``
and ``nft list chain`` outputs:

- a ``DROP``/``REJECT`` naming a published port (the container's with
  ``--dport``, the host's with ``--ctorigdstport``; lists, ranges) on the public interface
  (the default route's) or on every interface filters it, and the evidence
  names the rule;
- the chain is walked in order: a ``RETURN`` first lets the port through, a
  ``RELATED,ESTABLISHED`` rule decides nothing, a rule limited to some sources
  or to another interface does not filter it;
- a ``--dport`` naming only the host port covers nothing where Docker
  translates it (the central's ``5435->5432`` looked filtered and was not);
- an IPv6 publication on a server with no IPv6 default route is not exposed;
- what is not covered is still reported, apart from what is;
- every command it runs is a declared read-only probe.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.runner import is_read_only
from noust.managers.server import host as host_module
from noust.managers.server import security_checks
from noust.managers.server.security_checks import HardeningChecks
from noust.managers.server.security_docker_user import (
    DockerUserChain,
    parse_default_interfaces,
    parse_iptables_chain,
    parse_nft_chain,
)
from noust.managers.server.security_firewall import Firewall
from noust.managers.server.security_probe import SecurityProbe
from tests.server_security_support import NOW, FakeHost, FakeSshd

#: The owner's central, verbatim: the rule a systemd unit puts back at boot.
CENTRAL_DOCKER_USER = """-N DOCKER-USER
-A DOCKER-USER -i ens6 -p tcp -m multiport --dports 3307,3308,5435,8080,10051,3025 -j DROP
-A DOCKER-USER -j RETURN
"""

#: What Docker leaves when nobody touched the chain (Docker 27 and older).
UNTOUCHED = "-N DOCKER-USER\n-A DOCKER-USER -j RETURN\n"

#: A chain written the way Docker's documentation suggests.
DOCUMENTED = """-N DOCKER-USER
-A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
-A DOCKER-USER -s 10.8.0.0/24 -m comment --comment "office VPN" -j RETURN
-A DOCKER-USER -i eth0 -p tcp -m tcp --dport 5432 -j DROP
-A DOCKER-USER -i eth0 -p tcp -m conntrack --ctorigdstport 6000:6010 -j REJECT --reject-with icmp-port-unreachable
-A DOCKER-USER -p udp -m udp --dport 53 -j DROP
-A DOCKER-USER -j RETURN
"""

ROUTE_V4 = "default via 203.0.113.1 dev ens6 proto dhcp src 203.0.113.10 metric 100\n"
ROUTE_V6 = "default via fe80::1 dev ens6 proto ra metric 100 pref medium\n"

#: The central's rule as rewritten after 3.1.4: the host ports, as the client
#: asked for them, in the direction a connection starts.
BY_ORIGINAL_PORT = "".join(
    f"-A DOCKER-USER -i ens6 -p tcp -m conntrack --ctorigdstport {port} --ctdir ORIGINAL -j DROP\n"
    for port in (3307, 3308, 5435, 8080, 10051, 3025)
)

#: Published the way the central publishes them: every interface, both families.
CENTRAL_DOCKER_PS = (
    '{"ID":"a1","Labels":"com.docker.compose.project=zabbix","Names":"zabbix-server",'
    '"Ports":"0.0.0.0:10051->10051/tcp, [::]:10051->10051/tcp","State":"running"}\n'
    '{"ID":"b2","Labels":"com.docker.compose.project=erp","Names":"erp-db",'
    '"Ports":"0.0.0.0:3307->3307/tcp","State":"running"}\n'
    '{"ID":"c3","Labels":"com.docker.compose.project=crm","Names":"crm-postgres",'
    '"Ports":"0.0.0.0:5435->5432/tcp","State":"running"}\n'
    '{"ID":"d4","Labels":"","Names":"cache","Ports":"0.0.0.0:6380->6379/tcp","State":"running"}\n'
)

UFW_ACTIVE = """Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), disabled (routed)
New profiles: skip

To                         Action      From
--                         ------      ----
22/tcp                     ALLOW IN    Anywhere
"""

#: What ss and docker print for "every interface".
ANY = "0.0.0.0"  # noqa: S104


def _chain(text: str, interfaces: tuple[str, ...] = ("ens6",)) -> DockerUserChain:
    return DockerUserChain(tuple(parse_iptables_chain(text)), interfaces)


class TestReadingTheChain:
    def test_the_centrals_rule_is_read_whole(self) -> None:
        drop, back = parse_iptables_chain(CENTRAL_DOCKER_USER)

        assert drop.verdict == "drop" and drop.interface == "ens6" and drop.proto == "tcp"
        assert drop.ports == ((3307, 3307), (3308, 3308), (5435, 5435), (8080, 8080),
                              (10051, 10051), (3025, 3025))  # fmt: skip
        assert not drop.unknown and not drop.sources
        assert back.verdict == "return" and back.ports is None and back.interface is None

    def test_states_sources_ranges_and_the_original_port(self) -> None:
        established, office, single, original, udp, _ = parse_iptables_chain(DOCUMENTED)

        assert established.established and established.verdict == "return"
        assert office.sources and not office.unknown
        assert single.ports == ((5432, 5432),)
        assert original.original_ports == ((6000, 6010),) and original.verdict == "reject"
        assert udp.proto == "udp"

    def test_what_it_cannot_read_is_marked_so(self) -> None:
        [negated, other_out, jump] = parse_iptables_chain(
            "-A DOCKER-USER -p tcp ! --dport 22 -j DROP\n"
            "-A DOCKER-USER -o docker0 -j DROP\n"
            "-A DOCKER-USER -j LOG --log-prefix drop\n"
        )

        assert negated.unknown and other_out.unknown
        assert jump.verdict == ""

    def test_nftables_spelling(self) -> None:
        rules = parse_nft_chain(
            "table ip filter {\n"
            "\tchain DOCKER-USER {\n"
            '\t\tiifname "ens6" meta l4proto tcp tcp dport { 3025, 3307, 3308, 5435 } '
            "counter packets 0 bytes 0 drop\n"
            "\t\tct state established,related counter packets 9 bytes 900 return\n"
            "\t\ttcp dport 6000-6010 reject with icmp type port-unreachable\n"
            "\t\tcounter packets 1742 bytes 104520 return\n"
            "\t}\n"
            "}\n"
        )

        drop, established, ranged, back = rules
        assert drop.verdict == "drop" and drop.interface == "ens6" and drop.proto == "tcp"
        assert drop.ports == ((3025, 3025), (3307, 3307), (3308, 3308), (5435, 5435))
        assert not drop.unknown
        assert established.established
        assert ranged.verdict == "reject" and ranged.ports == ((6000, 6010),)
        assert back.verdict == "return" and back.ports is None and not back.unknown

    def test_the_default_routes_name_the_public_interfaces(self) -> None:
        assert parse_default_interfaces(ROUTE_V4) == ("ens6",)
        assert parse_default_interfaces(
            "default via 203.0.113.1 dev ens6 metric 100\n"
            "default via 198.51.100.1 dev ens7 metric 200\n"
        ) == ("ens6", "ens7")
        assert parse_default_interfaces("") == ()


class TestWhatTheChainCovers:
    def test_the_centrals_ports_are_filtered_on_the_public_interface(self) -> None:
        chain = _chain(CENTRAL_DOCKER_USER)

        for port in (3307, 3308, 5435, 8080, 10051, 3025):
            coverage = chain.covering(port, port, "tcp")
            assert coverage is not None, port
            assert coverage.interfaces == "ens6"
            assert coverage.rule.startswith("-A DOCKER-USER -i ens6")
        assert chain.covering(6380, 6379, "tcp") is None
        assert chain.covering(3307, 3307, "udp") is None

    def test_dport_sees_the_container_port_and_ctorigdstport_the_host_port(self) -> None:
        chain = _chain(CENTRAL_DOCKER_USER)

        # Docker has rewritten 5435 to 5432 before the chain: the rule never sees 5435.
        assert chain.covering(5435, 5432, "tcp") is None
        by_container = chain.covering(15435, 3307, "tcp")
        assert by_container is not None and by_container.port == "container port 3307"

        original = _chain(BY_ORIGINAL_PORT).covering(5435, 5432, "tcp")
        assert original is not None and original.port == "host port 5435"
        assert "--ctdir ORIGINAL" in original.describe()

    def test_a_rule_for_replies_decides_no_new_connection(self) -> None:
        [reply, other] = parse_iptables_chain(
            "-A DOCKER-USER -p tcp -m conntrack --ctorigdstport 5435 --ctdir REPLY -j DROP\n"
            "-A DOCKER-USER -p tcp -m conntrack --ctorigdstport 5435 --ctdir SIDEWAYS -j DROP\n"
        )

        assert reply.established and not reply.unknown
        assert other.unknown
        assert DockerUserChain((reply,), ("ens6",)).covering(5435, 5432, "tcp") is None

    def test_nftables_direction_is_read(self) -> None:
        [rule] = parse_nft_chain(
            'iifname "ens6" ct original proto-dst { 3307, 5435 } ct direction original drop\n'
        )

        assert not rule.unknown and not rule.established
        assert rule.original_ports == ((3307, 3307), (5435, 5435))

    def test_a_rule_on_another_interface_does_not_cover_the_public_one(self) -> None:
        chain = _chain(CENTRAL_DOCKER_USER, interfaces=("eth0",))

        assert chain.covering(3307, 3307, "tcp") is None

    def test_a_rule_on_every_interface_covers_whatever_the_public_one_is(self) -> None:
        chain = _chain("-A DOCKER-USER -p tcp --dport 5432 -j DROP\n", interfaces=())

        coverage = chain.covering(5432, 5432, "tcp")

        assert coverage is not None and coverage.interfaces == "every interface"

    def test_with_no_known_public_interface_an_interface_rule_proves_nothing(self) -> None:
        assert _chain(CENTRAL_DOCKER_USER, interfaces=()).covering(3307, 3307, "tcp") is None

    def test_the_chain_is_walked_in_order(self) -> None:
        chain = _chain(DOCUMENTED, interfaces=("eth0",))

        # Established traffic and the office network go first; the rest is refused.
        assert chain.covering(5432, 5432, "tcp") is not None
        assert chain.covering(6005, 80, "tcp") is not None
        assert chain.covering(53, 53, "udp") is not None
        assert chain.covering(8080, 80, "tcp") is None

    def test_a_return_before_the_drop_lets_the_port_through(self) -> None:
        chain = _chain(UNTOUCHED + "-A DOCKER-USER -i ens6 -p tcp --dport 3307 -j DROP\n")

        assert chain.covering(3307, 3307, "tcp") is None

    def test_a_drop_for_some_sources_only_is_not_a_filter(self) -> None:
        chain = _chain("-A DOCKER-USER -s 192.0.2.0/24 -p tcp --dport 3307 -j DROP\n")

        assert chain.covering(3307, 3307, "tcp") is None

    def test_an_accept_nobody_can_read_ends_the_walk(self) -> None:
        chain = _chain(
            "-A DOCKER-USER -m mark --mark 0x1 -j ACCEPT\n"
            "-A DOCKER-USER -p tcp --dport 3307 -j DROP\n"
        )

        assert chain.covering(3307, 3307, "tcp") is None

    @pytest.mark.parametrize(
        "handed_over",
        [
            "-A DOCKER-USER -j OFFICE",
            "-A DOCKER-USER -p tcp --dport 3307 -j OFFICE",
            "-A DOCKER-USER -g OFFICE",
            "-A DOCKER-USER --goto OFFICE",
            "-A DOCKER-USER -j NFQUEUE --queue-num 0",
        ],
    )
    def test_a_jump_to_a_chain_not_read_before_the_drop_proves_nothing(
        self, handed_over: str
    ) -> None:
        # OFFICE may accept 3307 for everyone: the DROP after it is not proof.
        chain = _chain(f"{handed_over}\n-A DOCKER-USER -i ens6 -p tcp --dport 3307 -j DROP\n")

        assert chain.rules[0].verdict == "jump"
        assert chain.covering(3307, 3307, "tcp") is None

    def test_a_jump_for_another_port_or_a_log_lets_the_walk_go_on(self) -> None:
        chain = _chain(
            "-A DOCKER-USER -p tcp --dport 80 -j OFFICE\n"
            "-A DOCKER-USER -j LOG --log-prefix docker-user\n"
            "-A DOCKER-USER -j MARK --set-mark 0x1\n"
            "-A DOCKER-USER -i ens6 -p tcp --dport 3307 -j DROP\n"
        )

        assert chain.covering(3307, 3307, "tcp") is not None

    @pytest.mark.parametrize("word", ["jump", "goto"])
    def test_an_nftables_jump_before_the_drop_proves_nothing(self, word: str) -> None:
        rules = parse_nft_chain(
            f"\t\tcounter packets 3 bytes 180 {word} office\n"
            '\t\tiifname "ens6" tcp dport 3307 drop\n'
        )

        assert rules[0].verdict == "jump"
        assert DockerUserChain(tuple(rules), ("ens6",)).covering(3307, 3307, "tcp") is None


# -- through the firewall and the check ----------------------------------------------


@pytest.fixture(autouse=True)
def fresh_platform() -> None:
    host_module.reset_platform_cache()
    security_checks.forget_report()
    yield
    host_module.reset_platform_cache()
    security_checks.forget_report()


@pytest.fixture
def host(tmp_path: Path) -> FakeHost:
    return FakeHost(tmp_path / "root")


@pytest.fixture
def runner(host: FakeHost) -> FakeSshd:
    fake = FakeSshd(host)
    fake.only_knows("ufw", "docker", "iptables", "ip", "systemctl", "sshd")
    fake.script(["ufw", "status", "verbose"], stdout=UFW_ACTIVE)
    fake.script(["ufw", "show", "added"], stdout="ufw allow 22/tcp\n")
    fake.script(["docker", "ps"], stdout=CENTRAL_DOCKER_PS)
    fake.script(["iptables", "-S", "DOCKER-USER"], stdout=CENTRAL_DOCKER_USER)
    fake.script(["ip", "route", "show", "default"], stdout=ROUTE_V4)
    fake.script(["ip", "-6", "route", "show", "default"], stdout="")
    return fake


def _firewall(runner: FakeSshd, host: FakeHost) -> Firewall:
    probe = SecurityProbe(runner=runner, host=host.paths, clock=lambda: NOW)
    return Firewall(probe, console_port=8080)


def _bypass(runner: FakeSshd, host: FakeHost):
    probe = SecurityProbe(runner=runner, host=host.paths, clock=lambda: NOW)
    found, _ = HardeningChecks(probe, console_port=8080)._firewall_checks()
    return next(check for check in found if check.id == "fw.docker_bypass")


class TestTheFirewallAndTheCheck:
    def test_published_ports_the_chain_drops_are_blocked(self, runner, host) -> None:
        exposures, _ = _firewall(runner, host).exposures()

        docker = {(e.address, e.port): e for e in exposures if e.docker is not None}
        assert docker[(ANY, 10051)].verdict == "blocked"
        assert docker[(ANY, 3307)].verdict == "blocked"
        # 5435 is published to 5432, which the host-port rule does not name.
        assert docker[(ANY, 5435)].verdict == "docker_bypass"
        assert docker[(ANY, 6380)].verdict == "docker_bypass"
        assert "DOCKER-USER" in docker[(ANY, 3307)].filtered_by
        assert not docker[(ANY, 3307)].reachable

    def test_ipv6_publications_are_judged_by_ip6tables(self, runner, host) -> None:
        runner.only_knows("ufw", "docker", "iptables", "ip6tables", "ip")
        runner.script(["ip", "-6", "route", "show", "default"], stdout=ROUTE_V6)
        runner.script(
            ["ip6tables", "-S", "DOCKER-USER"],
            stdout="-N DOCKER-USER\n-A DOCKER-USER -p tcp --dport 10051 -j DROP\n",
        )

        exposures, _ = _firewall(runner, host).exposures()

        assert {(e.address, e.port): e.verdict for e in exposures}[("::", 10051)] == "blocked"

    def test_without_ip6tables_an_ipv6_publication_is_still_reported(self, runner, host) -> None:
        runner.script(["ip", "-6", "route", "show", "default"], stdout=ROUTE_V6)

        exposures, _ = _firewall(runner, host).exposures()

        assert {(e.address, e.port): e.verdict for e in exposures}[("::", 10051)] == (
            "docker_bypass"
        )

    def test_without_an_ipv6_route_an_ipv6_publication_is_not_exposed(self, runner, host) -> None:
        """The central has no IPv6 address; its [::] publications were counted open."""
        exposures, _ = _firewall(runner, host).exposures()

        [v6] = [e for e in exposures if (e.address, e.port) == ("::", 10051)]
        assert v6.verdict == "blocked"
        assert "no IPv6 default route" in v6.filtered_by
        assert not runner.ran("ip6tables")

    def test_nftables_alone_is_read_with_nft(self, runner, host) -> None:
        runner.only_knows("ufw", "docker", "nft", "ip")
        runner.script(
            ["nft", "list", "chain", "ip", "filter", "DOCKER-USER"],
            stdout="table ip filter {\n\tchain DOCKER-USER {\n"
            '\t\tiifname "ens6" tcp dport { 3307, 5435, 10051 } drop\n\t}\n}\n',
        )

        exposures, _ = _firewall(runner, host).exposures()

        verdicts = {(e.address, e.port): e.verdict for e in exposures}
        assert verdicts[(ANY, 3307)] == verdicts[(ANY, 10051)] == "blocked"
        assert verdicts[(ANY, 6380)] == "docker_bypass"
        assert not runner.ran("iptables")

    def test_the_check_reports_only_what_is_not_covered_and_says_what_is(
        self, runner, host
    ) -> None:
        check = _bypass(runner, host)

        # The Redis nobody filters is still the critical finding it was, and
        # 5435, which the host-port rule only seemed to cover.
        assert check.status == "fail" and check.severity == "critical"
        assert "2 port(s)" in check.reason
        assert "2 other publication(s) it does refuse" in check.reason
        assert "1 IPv6 with no IPv6 route" in check.reason
        uncovered = [line for line in check.evidence if "around the firewall" in line]
        assert {line.split("/tcp")[0].rsplit(":", 1)[1] for line in uncovered} == {
            "6380",
            "5435",
        }
        covered = [line for line in check.evidence if "filtered by DOCKER-USER" in line]
        assert {line.split("/tcp")[0].rsplit(":", 1)[1] for line in covered} == {
            "10051",
            "3307",
        }
        assert all("on ens6" in line for line in covered)

    def test_every_port_covered_passes_with_the_rules_as_evidence(self, runner, host) -> None:
        runner.only_knows("ufw", "docker", "iptables", "ip6tables", "ip")
        runner.script(["ip", "-6", "route", "show", "default"], stdout=ROUTE_V6)
        runner.script(
            ["iptables", "-S", "DOCKER-USER"],
            stdout=BY_ORIGINAL_PORT.replace("3025", "3025:6380"),
        )
        runner.script(
            ["ip6tables", "-S", "DOCKER-USER"],
            stdout="-N DOCKER-USER\n-A DOCKER-USER -i ens6 -p tcp --dport 10051 -j DROP\n",
        )

        check = _bypass(runner, host)

        assert check.status == "pass", check.reason
        assert "DOCKER-USER" in check.reason
        assert len([line for line in check.evidence if "filtered by DOCKER-USER" in line]) == 5

    def test_the_rewritten_central_rule_and_no_ipv6_route_pass(self, runner, host) -> None:
        runner.script(
            ["iptables", "-S", "DOCKER-USER"],
            stdout=BY_ORIGINAL_PORT + "-A DOCKER-USER -i ens6 -p tcp --dport 6379 -j DROP\n",
        )

        check = _bypass(runner, host)

        assert check.status == "pass", check.reason
        assert "refuses 4 on the public interface" in check.reason
        assert "1 IPv6 with no IPv6 route" in check.reason

    def test_an_untouched_chain_changes_nothing(self, runner, host) -> None:
        runner.script(["iptables", "-S", "DOCKER-USER"], stdout=UNTOUCHED)

        check = _bypass(runner, host)

        assert check.status == "fail"
        assert check.severity == "critical"

    def test_a_chain_that_cannot_be_read_is_said_in_the_evidence(self, runner, host) -> None:
        runner.script(
            ["iptables", "-S", "DOCKER-USER"],
            stderr="iptables v1.8.7 (nf_tables): Could not fetch rule set generation id: "
            "Permission denied (you must be root)\n",
            exit_code=4,
        )

        check = _bypass(runner, host)

        assert check.status == "fail"
        assert any("Permission denied" in line for line in check.evidence)

    def test_a_missing_chain_is_no_rule_and_no_error(self, runner, host) -> None:
        runner.script(
            ["iptables", "-S", "DOCKER-USER"],
            stderr="iptables: No chain/target/match by that name.\n",
            exit_code=1,
        )

        check = _bypass(runner, host)

        assert check.status == "fail"
        assert not any("No chain" in line for line in check.evidence)

    def test_every_command_it_runs_is_a_declared_read_only_probe(self, runner, host) -> None:
        runner.script(["ip", "-6", "route", "show", "default"], stdout=ROUTE_V6)
        runner.only_knows("ufw", "docker", "iptables", "ip6tables", "nft", "ip")
        _firewall(runner, host).exposures()
        runner.only_knows("ufw", "docker", "nft", "ip")
        _firewall(runner, host).exposures()

        ran = [call for call in runner.calls if call[0] in ("iptables", "ip6tables", "nft", "ip")]
        assert {call[0] for call in ran} == {"iptables", "ip6tables", "nft", "ip"}
        assert [call for call in ran if not is_read_only(call)] == []
