# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Database exposure and the ``DOCKER-USER`` chain (Noust 3.3, item 72).

``noust db exposure`` told the operator that PostgreSQL on ``0.0.0.0:5435`` and
MariaDB on ``0.0.0.0:3307`` were open to the Internet while ``DOCKER-USER`` on
that server refused exactly those host ports on the public interface, and the
server's own security check agreed they were closed. What is pinned, on the
rules that server really has:

- a published port the chain drops on the public interface is listed apart,
  flagged ``firewalled`` with the rule and the interface, and is not an
  exposure: not in the default list, not in the exit code;
- a port with no rule, a rule on another interface, a rule that is not a
  ``DROP``/``REJECT``, a rule that names the container's port where Docker
  translates the host's, and a port published on one address all stay exposed;
- loopback publications were never exposures and still are not listed;
- an IPv6 publication on a server nothing reaches over IPv6 is closed the way
  the security check says it is, and a chain that cannot be read changes
  nothing and says so;
- every command run is a declared read-only probe, and the reading is the
  security check's own (one parser, rule 3).
"""

from __future__ import annotations

import io
import json

import pytest

from noust.cli.commands import db as db_cli
from noust.core.logger import Logger
from noust.core.runner import FakeRunner, is_read_only
from noust.managers.database.exposure import (
    FIREWALLED_ADVICE,
    ExposedPort,
    find_exposed_database_ports,
)

#: What ``iptables -S DOCKER-USER`` prints on the production server, verbatim.
PRODUCTION_CHAIN = """-N DOCKER-USER
-A DOCKER-USER -i ens6 -p tcp -m conntrack --ctorigdstport 3025 --ctdir ORIGINAL -j DROP
-A DOCKER-USER -i ens6 -p tcp -m conntrack --ctorigdstport 10051 --ctdir ORIGINAL -j DROP
-A DOCKER-USER -i ens6 -p tcp -m conntrack --ctorigdstport 8080 --ctdir ORIGINAL -j DROP
-A DOCKER-USER -i ens6 -p tcp -m conntrack --ctorigdstport 5435 --ctdir ORIGINAL -j DROP
-A DOCKER-USER -i ens6 -p tcp -m conntrack --ctorigdstport 3307 --ctdir ORIGINAL -j DROP
"""

ROUTE_V4 = "default via 203.0.113.1 dev ens6 proto dhcp src 203.0.113.10 metric 100\n"
ROUTE_V6 = "default via fe80::1 dev ens6 proto ra metric 100 pref medium\n"

#: How the production server publishes: every address, both families.
DOCKER_PS = (
    "arenna_postgres\tpostgres:16\t0.0.0.0:5435->5432/tcp, [::]:5435->5432/tcp\n"
    "arenna_mysql\tmariadb:11\t0.0.0.0:3307->3306/tcp, [::]:3307->3306/tcp\n"
    "arenna_cache\tredis:7\t0.0.0.0:6380->6379/tcp, [::]:6380->6379/tcp\n"
)

#: What ss and docker print for "every interface".
ANY = "0.0.0.0"  # noqa: S104


@pytest.fixture
def server(runner: FakeRunner) -> FakeRunner:
    """
    The production server: Docker publishes three databases, the chain drops two.

    Args:
        runner: The fake runner, installed as the process-wide one.

    Returns:
        The runner, scripted.
    """
    runner.script(["ss", "-ltnpH"], stdout="")
    runner.script(["docker", "ps"], stdout=DOCKER_PS)
    runner.script(["iptables", "-S", "DOCKER-USER"], stdout=PRODUCTION_CHAIN)
    runner.script(["ip", "route", "show", "default"], stdout=ROUTE_V4)
    runner.script(["ip", "-6", "route", "show", "default"], stdout=ROUTE_V6)
    return runner


def _key(entries: list[ExposedPort]) -> list[tuple[int, str]]:
    return [(entry.port, entry.address) for entry in entries]


class TestWhatTheChainCloses:
    def test_the_production_rules_close_5435_and_3307(self, server: FakeRunner) -> None:
        exposed = find_exposed_database_ports(server)

        # The Redis nobody filters, and the IPv6 side: this server has an IPv6
        # route and ip6tables has no rule, so nothing proves those closed.
        assert _key(exposed) == [(3307, "::"), (5435, "::"), (6380, ANY), (6380, "::")]

    def test_they_stay_listed_flagged_with_the_rule_and_the_interface(
        self, server: FakeRunner
    ) -> None:
        everything = find_exposed_database_ports(server, include_firewalled=True)

        closed = {(e.port, e.address): e for e in everything if e.firewalled}
        postgres = closed[(5435, ANY)]
        assert postgres.engine == "postgresql" and postgres.container == "arenna_postgres"
        assert postgres.closed_by == "the DOCKER-USER chain on ens6"
        assert postgres.rule == (
            "-A DOCKER-USER -i ens6 -p tcp -m conntrack --ctorigdstport 5435 "
            "--ctdir ORIGINAL -j DROP"
        )
        assert postgres.container_port == 5432
        assert postgres.advice == FIREWALLED_ADVICE.format(host_port=5435, container_port=5432)
        assert closed[(3307, ANY)].engine == "mysql"
        assert closed[(3307, ANY)].container_port == 3306
        assert (6380, ANY) not in closed

    def test_a_port_with_no_rule_stays_exposed(self, server: FakeRunner) -> None:
        exposed = {(e.port, e.address): e for e in find_exposed_database_ports(server)}

        redis = exposed[(6380, ANY)]
        assert not redis.firewalled and redis.closed_by == "" and redis.rule == ""
        assert "127.0.0.1:6380:6379" in redis.advice
        assert (5435, ANY) not in exposed and (3307, ANY) not in exposed

    def test_a_rule_on_another_interface_does_not_close_it(self, server: FakeRunner) -> None:
        server.script(
            ["iptables", "-S", "DOCKER-USER"],
            stdout=PRODUCTION_CHAIN.replace("-i ens6", "-i docker_gwbridge"),
        )

        exposed = find_exposed_database_ports(server)

        assert (5435, ANY) in _key(exposed) and (3307, ANY) in _key(exposed)
        assert not any(entry.firewalled for entry in exposed)

    @pytest.mark.parametrize("verdict", ["ACCEPT", "RETURN", "LOG --log-prefix db"])
    def test_a_rule_that_is_not_a_drop_does_not_close_it(
        self, server: FakeRunner, verdict: str
    ) -> None:
        server.script(
            ["iptables", "-S", "DOCKER-USER"],
            stdout="-N DOCKER-USER\n"
            f"-A DOCKER-USER -i ens6 -p tcp -m conntrack --ctorigdstport 5435 --ctdir ORIGINAL -j {verdict}\n",
        )

        assert (5435, ANY) in _key(find_exposed_database_ports(server))

    def test_a_reject_closes_it_as_a_drop_does(self, server: FakeRunner) -> None:
        server.script(
            ["iptables", "-S", "DOCKER-USER"],
            stdout="-N DOCKER-USER\n"
            "-A DOCKER-USER -i ens6 -p tcp -m conntrack --ctorigdstport 5435 --ctdir ORIGINAL "
            "-j REJECT --reject-with icmp-port-unreachable\n",
        )

        everything = find_exposed_database_ports(server, include_firewalled=True)

        assert {(e.port, e.address): e.firewalled for e in everything}[(5435, ANY)] is True

    def test_dport_sees_the_container_port_the_way_docker_translates_it(
        self, server: FakeRunner
    ) -> None:
        # Docker has rewritten 5435 to 5432 when the chain sees the packet.
        server.script(
            ["iptables", "-S", "DOCKER-USER"],
            stdout="-N DOCKER-USER\n-A DOCKER-USER -i ens6 -p tcp --dport 5435 -j DROP\n"
            "-A DOCKER-USER -i ens6 -p tcp --dport 3306 -j DROP\n",
        )

        state = {
            (e.port, e.address): e.firewalled
            for e in find_exposed_database_ports(server, include_firewalled=True)
        }

        assert state[(5435, ANY)] is False
        assert state[(3307, ANY)] is True

    def test_a_chain_walked_in_order_lets_a_return_through_first(self, server: FakeRunner) -> None:
        server.script(
            ["iptables", "-S", "DOCKER-USER"],
            stdout="-N DOCKER-USER\n-A DOCKER-USER -j RETURN\n" + PRODUCTION_CHAIN,
        )

        assert (5435, ANY) in _key(find_exposed_database_ports(server))

    def test_nftables_alone_closes_it_the_same_way(self, server: FakeRunner) -> None:
        server.only_knows("docker", "nft", "ip", "ss")
        server.script(
            ["nft", "list", "chain", "ip", "filter", "DOCKER-USER"],
            stdout="table ip filter {\n\tchain DOCKER-USER {\n"
            '\t\tiifname "ens6" ct original proto-dst { 3307, 5435 } ct direction original drop\n'
            "\t}\n}\n",
        )

        state = {
            (e.port, e.address): e.firewalled
            for e in find_exposed_database_ports(server, include_firewalled=True)
        }

        assert state[(5435, ANY)] is True and state[(3307, ANY)] is True
        assert state[(6380, ANY)] is False


class TestWhatWasNeverAnExposure:
    def test_loopback_publications_are_not_listed_and_nothing_is_read(
        self, runner: FakeRunner
    ) -> None:
        runner.script(["ss", "-ltnpH"], stdout="")
        runner.script(
            ["docker", "ps"],
            stdout="shop-db\tpostgres:16\t127.0.0.1:5435->5432/tcp, [::1]:5435->5432/tcp\n",
        )

        assert find_exposed_database_ports(runner, include_firewalled=True) == []
        assert not [call for call in runner.calls if call[0] in ("iptables", "ip6tables", "ip")]

    def test_a_port_published_on_one_address_is_not_judged_by_a_rule_for_another(
        self, server: FakeRunner
    ) -> None:
        server.script(
            ["docker", "ps"],
            stdout="shop-db\tpostgres:16\t10.0.0.5:5435->5432/tcp\n",
        )

        [entry] = find_exposed_database_ports(server)

        assert entry.address == "10.0.0.5" and not entry.firewalled

    def test_nothing_published_reads_no_chain(self, runner: FakeRunner) -> None:
        runner.script(["ss", "-ltnpH"], stdout="")
        runner.script(["docker", "ps"], stdout="web\tnginx:1\t0.0.0.0:80->80/tcp\n")

        assert find_exposed_database_ports(runner) == []
        assert not [call for call in runner.calls if call[0] in ("iptables", "ip6tables", "nft")]

    def test_a_listening_engine_is_not_a_docker_publication(self, server: FakeRunner) -> None:
        server.script(
            ["ss", "-ltnpH"],
            stdout='LISTEN 0 244 0.0.0.0:5433 0.0.0.0:* users:(("postgres",pid=1,fd=6))\n',
        )
        # A rule for the same number does not close a socket that is not Docker's.
        server.script(
            ["iptables", "-S", "DOCKER-USER"],
            stdout="-N DOCKER-USER\n-A DOCKER-USER -i ens6 -p tcp --dport 5433 -j DROP\n",
        )

        [engine] = [e for e in find_exposed_database_ports(server) if e.port == 5433]

        assert engine.source == "engine" and not engine.firewalled


class TestIPv6AndUnreadableChains:
    def test_without_an_ipv6_route_the_ipv6_publications_are_closed(
        self, server: FakeRunner
    ) -> None:
        server.script(["ip", "-6", "route", "show", "default"], stdout="")

        everything = find_exposed_database_ports(server, include_firewalled=True)

        v6 = [e for e in everything if e.address == "::"]
        assert v6 and all(e.firewalled for e in v6)
        assert all(e.closed_by.startswith("no IPv6 default route") for e in v6)
        assert all(e.rule == "" for e in v6)
        assert not any(call[0] == "ip6tables" for call in server.calls)
        # The IPv4 side is judged by its own chain and is untouched by this.
        assert [(e.port, e.firewalled) for e in everything if e.address == ANY] == [
            (3307, True),
            (5435, True),
            (6380, False),
        ]

    def test_with_an_ipv6_route_ip6tables_decides_and_nothing_is_assumed(
        self, server: FakeRunner
    ) -> None:
        server.script(
            ["ip6tables", "-S", "DOCKER-USER"],
            stdout="-N DOCKER-USER\n"
            "-A DOCKER-USER -i ens6 -p tcp -m conntrack --ctorigdstport 5435 --ctdir ORIGINAL -j DROP\n",
        )

        state = {
            (e.port, e.address): e.firewalled
            for e in find_exposed_database_ports(server, include_firewalled=True)
        }

        assert state[(5435, "::")] is True
        assert state[(3307, "::")] is False and state[(6380, "::")] is False

    def test_a_chain_that_cannot_be_read_closes_nothing_and_says_why(
        self, server: FakeRunner
    ) -> None:
        server.script(
            ["iptables", "-S", "DOCKER-USER"],
            stderr="iptables v1.8.7 (nf_tables): Could not fetch rule set generation id: "
            "Permission denied (you must be root)\n",
            exit_code=4,
        )

        exposed = {(e.port, e.address): e for e in find_exposed_database_ports(server)}

        assert not any(entry.firewalled for entry in exposed.values())
        assert "Permission denied" in exposed[(5435, ANY)].advice
        assert "could not be read" in exposed[(5435, ANY)].advice

    def test_a_server_without_a_chain_has_no_rule_to_close_anything(
        self, server: FakeRunner
    ) -> None:
        server.script(
            ["iptables", "-S", "DOCKER-USER"],
            stderr="iptables: No chain/target/match by that name.\n",
            exit_code=1,
        )

        exposed = {(e.port, e.address): e for e in find_exposed_database_ports(server)}

        assert (5435, ANY) in exposed
        assert "could not be read" not in exposed[(5435, ANY)].advice


class TestAnImageNoustDoesNotKnow:
    """An unusual image publishing a database's port is still reported; zabbix is not."""

    @pytest.mark.parametrize(
        ("image", "container_port", "engine"),
        [
            ("ghcr.io/acme/postgres:16", 5432, "postgresql"),
            ("supabase/postgres:15.1.0.147", 5432, "postgresql"),
            ("quay.io/team/mysql:8", 3306, "mysql"),
            ("123456789012.dkr.ecr.eu-west-1.amazonaws.com/cache:7", 6379, "redis"),
            ("3f2a9c1d7b8e", 27017, "mongodb"),
            ("mysql/mysql-server:8.0", 33060, "mysql"),
        ],
    )
    def test_the_container_port_names_the_engine(
        self, server: FakeRunner, image: str, container_port: int, engine: str
    ) -> None:
        server.script(
            ["docker", "ps"], stdout=f"odd-db\t{image}\t0.0.0.0:15000->{container_port}/tcp\n"
        )

        [entry] = find_exposed_database_ports(server)

        assert (entry.engine, entry.port, entry.container, entry.image) == (
            engine,
            15000,
            "odd-db",
            image,
        )
        assert not entry.firewalled

    def test_zabbix_web_and_server_are_not_databases(self, server: FakeRunner) -> None:
        server.script(
            ["docker", "ps"],
            stdout=(
                "zabbix-web\tzabbix/zabbix-web-nginx-mysql:alpine-7.0-latest\t"
                "0.0.0.0:8080->8080/tcp, [::]:8080->8080/tcp\n"
                "zabbix-server\tzabbix/zabbix-server-mysql:alpine-7.0-latest\t"
                "0.0.0.0:10051->10051/tcp, [::]:10051->10051/tcp\n"
            ),
        )

        assert find_exposed_database_ports(server, include_firewalled=True) == []


def test_every_command_it_runs_is_a_declared_read_only_probe(server: FakeRunner) -> None:
    find_exposed_database_ports(server, include_firewalled=True)

    asked = {call[0] for call in server.calls}
    assert {"iptables", "ip", "docker", "ss"} <= asked
    assert [call for call in server.calls if not is_read_only(call)] == []


class TestTheCommand:
    @pytest.fixture
    def output(self) -> io.StringIO:
        return io.StringIO()

    def test_closed_ports_are_information_and_the_exit_code_ignores_them(
        self, server: FakeRunner, output: io.StringIO
    ) -> None:
        server.script(
            ["docker", "ps"],
            stdout="arenna_postgres\tpostgres:16\t0.0.0.0:5435->5432/tcp\n",
        )

        code = db_cli._exposure(json_output=False, logger=Logger(stream=output))

        text = output.getvalue()
        assert code == 0
        assert "No database port is open beyond this machine" in text
        assert "postgresql on 0.0.0.0:5435 (container arenna_postgres, postgres:16)" in text
        assert "closed by the DOCKER-USER chain on ens6" in text

    def test_something_really_open_still_fails_and_the_closed_one_is_listed_after_it(
        self, server: FakeRunner, output: io.StringIO
    ) -> None:
        code = db_cli._exposure(json_output=False, logger=Logger(stream=output))

        text = output.getvalue()
        assert code == 1
        assert "No database port is open" not in text
        assert "redis on 0.0.0.0:6380 (container arenna_cache, redis:7)" in text
        assert "closed by the DOCKER-USER chain on ens6" in text
        assert "closed by no IPv6" not in text  # the server has an IPv6 route here

    def test_json_flags_every_finding_and_the_exit_code_counts_the_open_ones(
        self, server: FakeRunner, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = db_cli._exposure(json_output=True, logger=Logger(stream=io.StringIO()))

        entries = json.loads(capsys.readouterr().out)
        assert code == 1
        by_port = {(e["port"], e["address"]): e for e in entries}
        assert by_port[(5435, ANY)]["firewalled"] is True
        assert by_port[(5435, ANY)]["closed_by"] == "the DOCKER-USER chain on ens6"
        assert by_port[(6380, ANY)]["firewalled"] is False

    def test_json_with_only_closed_ports_exits_zero(
        self, server: FakeRunner, capsys: pytest.CaptureFixture[str]
    ) -> None:
        server.script(["docker", "ps"], stdout="arenna_mysql\tmariadb:11\t0.0.0.0:3307->3306/tcp\n")

        code = db_cli._exposure(json_output=True, logger=Logger(stream=io.StringIO()))

        [entry] = json.loads(capsys.readouterr().out)
        assert code == 0 and entry["firewalled"] is True
