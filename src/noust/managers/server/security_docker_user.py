# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Docker's ``DOCKER-USER`` chain: where an operator filters what Docker publishes.

A port Docker publishes goes through the ``nat`` table and the ``FORWARD``
chain, never through the ``INPUT`` chain ufw and firewalld filter, so a firewall
that is on says nothing about it. What Docker leaves to the operator is the
``DOCKER-USER`` chain, which ``FORWARD`` jumps to before any rule of Docker's:
a ``DROP`` there, such as::

    -A DOCKER-USER -i ens6 -p tcp -m multiport --dports 3307,5435 -j DROP

closes those ports to the Internet. This module reads the chain (``iptables
-S DOCKER-USER``, ``ip6tables`` for IPv6, ``nft list chain`` when nftables is
the only tool) and decides, for one published port, whether a rule refuses it
on the public interface: the interface of the default route, or every interface.
:class:`DockerUserReader` is that reading, shared: the server's security check
and the databases' exposure report both ask it, so a port the operator closed is
closed in both and there is one parser to keep right.

The chain is walked in order, the way the kernel does, and only what can be
proven counts as filtering: a ``DROP`` or ``REJECT`` that applies to every source
and to the port on that interface. Docker has already rewritten the destination
when a packet reaches the chain, so ``--dport`` sees the container's port and
only ``-m conntrack --ctorigdstport`` sees the host's: a rule that names the
host port with ``--dport`` refuses nothing where the two differ (the owner's
central had one for ``3307->3306``, with a firewall upstream as the only thing
closing it). A
``RETURN`` or ``ACCEPT`` that lets the port through first ends the walk, and so
does a jump or goto to another chain (or a queue), which this module does not
follow and which may accept it: past one, a ``DROP`` proves nothing;
``RELATED,ESTABLISHED`` rules never decide a new connection and are passed
over; a rule with a condition this module does not read (a source, a mark, a
set) never counts as filtering, and never lets the walk go on past an
``ACCEPT`` either.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from noust.core.runner import CommandRunner
from noust.managers.server.host import PROBE_TIMEOUT

#: The chain Docker leaves to the operator.
CHAIN = "DOCKER-USER"

#: Environment of every firewall command: the tools' own words, untranslated, so
#: what they say can be recognised.
FIREWALL_ENV = {"LC_ALL": "C", "TERM": "dumb"}

#: What a terminating rule does, as both tools spell it.
_VERDICTS = {
    "DROP": "drop",
    "REJECT": "reject",
    "ACCEPT": "accept",
    "RETURN": "return",
    "drop": "drop",
    "reject": "reject",
    "accept": "accept",
    "return": "return",
}

#: iptables targets that change, count or log a packet and let it go on down
#: the chain. Any other target that is not a verdict sends it somewhere this
#: module does not read (a jump or goto to another chain, a queue to a
#: program), where it may be accepted: such a rule ends the walk unproven.
_NON_TERMINATING = frozenset(
    {
        "LOG",
        "NFLOG",
        "ULOG",
        "MARK",
        "CONNMARK",
        "NOTRACK",
        "CT",
        "TRACE",
        "AUDIT",
        "CLASSIFY",
        "DSCP",
        "TOS",
        "TTL",
        "HL",
        "SECMARK",
        "CONNSECMARK",
        "TCPMSS",
        "TCPOPTSTRIP",
        "IDLETIMER",
        "LED",
        "RATEEST",
        "CHECKSUM",
    }
)

#: Connection states that never start a connection: a rule limited to them
#: decides nothing about a stranger connecting.
_NOT_NEW = frozenset({"ESTABLISHED", "RELATED", "INVALID", "UNTRACKED"})

#: iptables options this module reads (with their one argument).
_IPTABLES_ARGUMENTS = frozenset(
    {
        "-i",
        "--in-interface",
        "-p",
        "--protocol",
        "--dport",
        "--destination-port",
        "--dports",
        "--destination-ports",
        "--ctorigdstport",
        "--ctdir",
        "--ctstate",
        "--state",
        "-j",
        "--jump",
        "-g",
        "--goto",
    }
)

#: iptables options that restrict who the rule applies to.
_IPTABLES_SOURCES = frozenset({"-s", "--source", "--src-range", "--match-set"})

#: iptables options that never change which packets a rule matches.
_IPTABLES_IGNORED = frozenset({"--comment", "--reject-with"})


def port_ranges(text: str) -> tuple[tuple[int, int], ...] | None:
    """
    Read ``22``, ``80,443``, ``6000:6007`` or ``6000-6007``.

    Args:
        text: The ports.

    Returns:
        Inclusive ranges, or None when this is not a port list.
    """
    ranges: list[tuple[int, int]] = []
    for part in text.split(","):
        low, sep, high = part.strip().replace("-", ":").partition(":")
        if not low.isdigit() or (sep and not high.isdigit()):
            return None
        ranges.append((int(low), int(high) if sep else int(low)))
    return tuple(ranges)


@dataclass(frozen=True)
class FilterRule:
    """
    One rule of ``DOCKER-USER``, as far as it decides a published port.

    Attributes:
        text: The rule, verbatim.
        verdict: ``drop``, ``reject``, ``accept``, ``return``; ``jump`` for
            a target that hands the packet to something this module does not
            read (another chain, by jump or goto, or a queue), which may
            accept it; empty for a target that lets it go on (a log, a mark).
        proto: ``tcp``, ``udp``, or None for every protocol.
        ports: Destination ports as the chain sees them (after Docker's
            translation: the container's), or None for every port.
        original_ports: Destination ports as the client asked for them
            (``--ctorigdstport``: the host's), or None when not matched on.
        interface: The incoming interface it is limited to (``+`` ends a
            prefix), or None for every interface.
        interface_negated: It applies to every interface but that one.
        sources: It is limited to some sources: it filters or admits only them.
        unknown: It carries a condition this module does not read.
        established: It is limited to connections already made.
    """

    text: str
    verdict: str = ""
    proto: str | None = None
    ports: tuple[tuple[int, int], ...] | None = None
    original_ports: tuple[tuple[int, int], ...] | None = None
    interface: str | None = None
    interface_negated: bool = False
    sources: bool = False
    unknown: bool = False
    established: bool = False

    def on_interface(self, interface: str | None) -> bool | None:
        """
        Report whether the rule applies to packets coming in on an interface.

        Args:
            interface: The interface, or None when the public one is unknown.

        Returns:
            True when it does, False when it does not, None when that depends
            on an interface nobody named.
        """
        if self.interface is None:
            return True
        if interface is None:
            return None
        if self.interface.endswith("+"):
            named = interface.startswith(self.interface[:-1])
        else:
            named = interface == self.interface
        return named != self.interface_negated

    def matched_port(self, host_port: int, container_port: int, proto: str) -> str | None:
        """
        Say which port of a publication the rule applies to.

        Args:
            host_port: The port on the host.
            container_port: The port inside the container.
            proto: ``tcp`` or ``udp``.

        Returns:
            ``host port N``, ``container port N`` or ``every port``; None when
            the rule does not apply to it.
        """
        if self.proto is not None and self.proto != proto:
            return None

        def within(ranges: tuple[tuple[int, int], ...], port: int) -> bool:
            return any(low <= port <= high for low, high in ranges)

        if self.original_ports is not None:
            if not within(self.original_ports, host_port):
                return None
            if self.ports is None:
                return f"host port {host_port}"
        if self.ports is None:
            return "every port"
        # The chain sees the translated destination: the host port a --dport
        # names is not the one it compares against.
        if within(self.ports, container_port):
            if container_port == host_port:
                return f"port {host_port}"
            return f"container port {container_port}"
        return None


def _iptables_rule(line: str) -> FilterRule | None:
    """
    Read one line of ``iptables -S``.

    Args:
        line: The line.

    Returns:
        The rule, or None for a line that is not a rule of the chain.
    """
    try:
        tokens = shlex.split(line)
    except ValueError:
        return FilterRule(text=line, unknown=True)
    if tokens[:2] != ["-A", CHAIN]:
        return None
    values: dict[str, str] = {}
    negated: set[str] = set()
    sources = unknown = False
    index = 2
    while index < len(tokens):
        negate = tokens[index] == "!"
        if negate:
            index += 1
            if index >= len(tokens):
                unknown = True
                break
        option = tokens[index]
        index += 1
        if option == "-m":
            index += 1
            continue
        arguments: list[str] = []
        while index < len(tokens) and not tokens[index].startswith("-") and tokens[index] != "!":
            arguments.append(tokens[index])
            index += 1
        if option in _IPTABLES_IGNORED:
            continue
        if option in _IPTABLES_SOURCES:
            sources = True
            unknown = unknown or negate
            continue
        if option not in _IPTABLES_ARGUMENTS or len(arguments) != 1:
            unknown = True
            continue
        values[option] = arguments[0]
        if negate:
            negated.add(option)
    return _rule_from(line, values, negated, sources=sources, unknown=unknown)


def _rule_from(
    text: str,
    values: dict[str, str],
    negated: set[str],
    *,
    sources: bool,
    unknown: bool,
) -> FilterRule:
    """
    Build a rule from the options of one ``iptables -S`` line.

    Args:
        text: The line.
        values: Option to its argument.
        negated: Options given with ``!``.
        sources: It is limited to some sources.
        unknown: It carries a condition this module does not read.

    Returns:
        The rule.
    """

    def value(*names: str) -> str | None:
        for name in names:
            if name in values:
                if name in negated:
                    return None
                return values[name]
        return None

    port_options = ("--dport", "--destination-port", "--dports", "--destination-ports")
    if negated & {
        "-p",
        "--protocol",
        "--ctorigdstport",
        "--ctdir",
        "--ctstate",
        "--state",
        *port_options,
    }:
        unknown = True
    ports: tuple[tuple[int, int], ...] | None = None
    port_text = value(*port_options)
    if port_text is not None:
        ports = port_ranges(port_text)
        unknown = unknown or ports is None
    original: tuple[tuple[int, int], ...] | None = None
    original_text = value("--ctorigdstport")
    if original_text is not None:
        original = port_ranges(original_text)
        unknown = unknown or original is None
    proto = value("-p", "--protocol")
    if proto is not None and proto.lower() not in ("tcp", "udp"):
        unknown = unknown or proto.lower() not in ("all", "0")
        proto = None
    states = value("--ctstate", "--state")
    established = False
    if states is not None:
        named = {state.strip().upper() for state in states.split(",")}
        established = named <= _NOT_NEW
    # A rule for the reply direction never sees a stranger's first packet.
    direction = value("--ctdir")
    if direction is not None and direction.upper() == "REPLY":
        established = True
    elif direction is not None and direction.upper() != "ORIGINAL":
        unknown = True
    target = value("-j", "--jump") or ""
    if value("-g", "--goto") is not None:
        verdict = "jump"
    elif target in _VERDICTS:
        verdict = _VERDICTS[target]
    elif target and target.upper() not in _NON_TERMINATING:
        verdict = "jump"
    else:
        verdict = ""
    interface = values.get("-i") or values.get("--in-interface")
    return FilterRule(
        text=text,
        verdict=verdict,
        proto=proto.lower() if proto else None,
        ports=ports,
        original_ports=original,
        interface=interface,
        interface_negated=bool(negated & {"-i", "--in-interface"}),
        sources=sources,
        unknown=unknown,
        established=established,
    )


def parse_iptables_chain(text: str) -> list[FilterRule]:
    """
    Read ``iptables -S DOCKER-USER`` (or ``ip6tables``).

    Args:
        text: What it printed.

    Returns:
        The chain's rules, in order.
    """
    rules = [_iptables_rule(line) for line in text.splitlines() if line.strip()]
    return [rule for rule in rules if rule is not None]


def _nft_set(tokens: Sequence[str], index: int) -> tuple[str, int]:
    """
    Read one nft value: a word, or a ``{ a, b }`` set, as ``a,b``.

    Args:
        tokens: The rule's words.
        index: Where the value starts.

    Returns:
        The value, and the index after it.
    """
    if index >= len(tokens):
        return "", index
    if tokens[index] != "{":
        return tokens[index], index + 1
    parts: list[str] = []
    index += 1
    while index < len(tokens) and tokens[index] != "}":
        parts.extend(part for part in tokens[index].split(",") if part)
        index += 1
    return ",".join(parts), index + 1


def _nft_rule(line: str) -> FilterRule:
    """
    Read one rule of ``nft list chain``.

    Args:
        line: The rule.

    Returns:
        The rule.
    """
    try:
        tokens = shlex.split(line.replace("{", " { ").replace("}", " } "))
    except ValueError:
        return FilterRule(text=line, unknown=True)
    fields: dict[str, str] = {}
    negated: set[str] = set()
    verdict = ""
    sources = unknown = False
    index = 0
    while index < len(tokens):
        word = tokens[index]
        index += 1
        if word in ("counter", "log", "limit"):
            # Statements of their own: a counter's "packets N bytes M".
            while index < len(tokens) and tokens[index] in ("packets", "bytes"):
                index += 2
            continue
        if word == "comment":
            index += 1
            continue
        if word in _VERDICTS:
            verdict = _VERDICTS[word]
            # "reject with icmp type port-unreachable": the rest is the reply.
            break
        if word in ("jump", "goto", "queue"):
            # Handed to another chain or a program, which may accept it.
            fields["-g"] = tokens[index] if index < len(tokens) else word
            break
        negate = index < len(tokens) and tokens[index] == "!="
        if negate:
            index += 1
        if word in ("iifname", "iif"):
            value, index = _nft_set(tokens, index)
            fields["-i"] = value
        elif word in ("meta", "ip", "ip6") and index < len(tokens):
            key = tokens[index]
            index += 1
            negate = index < len(tokens) and tokens[index] == "!="
            if negate:
                index += 1
            value, index = _nft_set(tokens, index)
            if key in ("l4proto", "protocol", "nexthdr"):
                fields["-p"] = value
                word = "-p"
            elif key == "saddr":
                sources = True
                unknown = unknown or negate
                continue
            else:
                unknown = True
                continue
        elif word in ("tcp", "udp", "th") and index < len(tokens) and tokens[index] == "dport":
            index += 1
            negate = index < len(tokens) and tokens[index] == "!="
            if negate:
                index += 1
            value, index = _nft_set(tokens, index)
            fields["--dports"] = value
            if word != "th":
                fields.setdefault("-p", word)
            word = "--dports"
        elif word == "ct" and index < len(tokens):
            key = tokens[index]
            index += 1
            negate = index < len(tokens) and tokens[index] == "!="
            if negate:
                index += 1
            value, index = _nft_set(tokens, index)
            if key == "state":
                fields["--ctstate"] = value
                word = "--ctstate"
            elif key == "direction":
                fields["--ctdir"] = value
                word = "--ctdir"
            elif key == "original" and value in ("proto-dst", "dport"):
                ports, index = _nft_set(tokens, index)
                fields["--ctorigdstport"] = ports
                word = "--ctorigdstport"
            else:
                unknown = True
                continue
        else:
            unknown = True
            continue
        if negate:
            negated.add("-i" if word in ("iifname", "iif") else word)
    if "-p" in fields and "," in fields["-p"]:
        fields.pop("-p")
    fields["-j"] = verdict.upper() if verdict else ""
    return _rule_from(line.strip(), fields, negated, sources=sources, unknown=unknown)


def parse_nft_chain(text: str) -> list[FilterRule]:
    """
    Read ``nft list chain ip filter DOCKER-USER`` (or ``ip6``).

    Args:
        text: What it printed.

    Returns:
        The chain's rules, in order.
    """
    rules: list[FilterRule] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line == "}" or line.startswith(("table ", "chain ", "type ", "policy ")):
            continue
        rules.append(_nft_rule(line))
    return rules


def parse_default_interfaces(text: str) -> tuple[str, ...]:
    """
    Read the interfaces of ``ip route show default``.

    Args:
        text: What it printed.

    Returns:
        Each default route's ``dev``, once, in order.
    """
    found: list[str] = []
    for line in text.splitlines():
        words = line.split()
        if "dev" in words[:-1]:
            device = words[words.index("dev") + 1]
            if device not in found:
                found.append(device)
    return tuple(found)


@dataclass(frozen=True)
class Coverage:
    """
    Why a published port is filtered.

    Attributes:
        rule: The rule that refuses it, verbatim.
        port: Which of its ports the rule names (``host port 3307``).
        interfaces: Where: the public interfaces, or ``every interface``.
    """

    rule: str
    port: str
    interfaces: str

    def describe(self) -> str:
        """
        One line for the evidence.

        Returns:
            Such as ``DOCKER-USER '-A ... -j DROP' (host port 3307, on ens6)``.
        """
        return f"filtered by {CHAIN} '{self.rule}' ({self.port}, on {self.interfaces})"


def _decides(
    rules: Iterable[FilterRule],
    host_port: int,
    container_port: int,
    proto: str,
    interface: str | None,
) -> tuple[FilterRule, str] | None:
    """
    Walk the chain for a new connection to a port, as the kernel does.

    Args:
        rules: The chain, in order.
        host_port: The port on the host.
        container_port: The port inside the container.
        proto: ``tcp`` or ``udp``.
        interface: The interface it comes in on; None when the public one is
            unknown, so only rules on every interface can refuse it.

    Returns:
        The rule that refuses it and which port it named, or None when the
        connection gets through (or nothing provably refuses it).
    """
    for rule in rules:
        applies = rule.on_interface(interface)
        if rule.established or not rule.verdict or applies is False:
            continue
        matched = rule.matched_port(host_port, container_port, proto)
        if matched is None:
            continue
        if rule.verdict in ("drop", "reject"):
            if rule.sources or rule.unknown or applies is None:
                continue
            return rule, matched
        # accept, return, or a jump to a chain not read here (which may accept
        # it): some sources let through before a refusal still leave it
        # refusing everyone else; anything else ends the walk unproven.
        if rule.sources and not rule.unknown:
            continue
        return None
    return None


@dataclass(frozen=True)
class DockerUserChain:
    """
    ``DOCKER-USER`` for one address family, and where the Internet comes in.

    Attributes:
        rules: The chain, in order.
        interfaces: The public interfaces: those of the default routes.
        source: The command that listed the chain, for the evidence.
        error: Why the chain could not be read, verbatim.
    """

    rules: tuple[FilterRule, ...] = ()
    interfaces: tuple[str, ...] = ()
    source: str = ""
    error: str = ""

    def covering(self, host_port: int, container_port: int, proto: str) -> Coverage | None:
        """
        Find the rule that closes a published port to the Internet.

        A rule limited to an interface counts when it is the public one, on
        every public interface there is; a rule on every interface always does.

        Args:
            host_port: The port on the host.
            container_port: The port inside the container.
            proto: ``tcp`` or ``udp``.

        Returns:
            Why it is filtered, or None when it is not.
        """
        found: list[tuple[FilterRule, str]] = []
        for interface in self.interfaces or (None,):
            decided = _decides(self.rules, host_port, container_port, proto, interface)
            if decided is None:
                return None
            found.append(decided)
        rule, matched = found[0]
        where = (
            "every interface"
            if all(each.interface is None for each, _ in found)
            else ", ".join(self.interfaces)
        )
        return Coverage(rule.text, matched, where)


def _missing_chain(output: str) -> bool:
    """
    Tell "the chain does not exist" from a failure to read it.

    Args:
        output: What iptables or nft said.

    Returns:
        True when the tool said there is no such chain (Docker not running, or
        not managing this family's rules): no rule, and nothing wrong.
    """
    text = output.lower()
    return "no chain/target/match" in text or "no such file or directory" in text


def _family_of(host_address: str) -> str:
    """
    Tell which chain a publication goes through.

    Args:
        host_address: The address the port is published on.

    Returns:
        ``ipv6`` for an IPv6 address, ``ipv4`` otherwise.
    """
    return "ipv6" if ":" in host_address else "ipv4"


class DockerUserReader:
    """
    Reads ``DOCKER-USER`` and the default routes through a runner, once each.

    The one place that answers "is this published port refused on the public
    interface" (rule 3): the server's security checks and the databases'
    exposure report both go through it, so the two cannot disagree about a port
    the operator closed. Every command it runs is a declared read-only probe
    (``managers/server/probes.py``).
    """

    def __init__(self, runner: CommandRunner) -> None:
        """
        Args:
            runner: The runner every command goes through.
        """
        self.runner = runner
        self._chains: dict[str, DockerUserChain] = {}
        self._routes: dict[str, tuple[str, ...]] = {}
        self._interfaces: tuple[str, ...] | None = None

    def default_routes(self, family: str) -> tuple[str, ...]:
        """
        The interfaces of one address family's default routes.

        Args:
            family: ``ipv4`` or ``ipv6``.

        Returns:
            Their names; empty when ``ip`` is missing or says none.
        """
        if family not in self._routes:
            found: tuple[str, ...] = ()
            if self.runner.exists("ip"):
                argv = ["ip", "route", "show", "default"]
                if family == "ipv6":
                    argv.insert(1, "-6")
                result = self.runner.run(argv, timeout=PROBE_TIMEOUT, env=FIREWALL_ENV)
                if result.success:
                    found = parse_default_interfaces(result.stdout)
            self._routes[family] = found
        return self._routes[family]

    def public_interfaces(self) -> tuple[str, ...]:
        """
        The interfaces the Internet comes in on: those of the default routes.

        Returns:
            Their names, IPv4's first; empty when ``ip`` is missing or says none.
        """
        if self._interfaces is None:
            found: list[str] = []
            for family in ("ipv4", "ipv6"):
                found += [name for name in self.default_routes(family) if name not in found]
            self._interfaces = tuple(found)
        return self._interfaces

    def chain(self, family: str) -> DockerUserChain:
        """
        Read Docker's ``DOCKER-USER`` chain for one address family.

        ``iptables -S`` reads it on both of iptables' backends; ``nft list
        chain`` when nftables is the only tool there is.

        Args:
            family: ``ipv4`` or ``ipv6``.

        Returns:
            The chain; without rules when it does not exist, with the tool's
            own words in ``error`` when it could not be read.
        """
        if family in self._chains:
            return self._chains[family]
        ipv6 = family == "ipv6"
        tool = "ip6tables" if ipv6 else "iptables"
        if self.runner.exists(tool):
            argv = [tool, "-S", CHAIN]
            parse = parse_iptables_chain
        elif self.runner.exists("nft"):
            argv = ["nft", "list", "chain", "ip6" if ipv6 else "ip", "filter", CHAIN]
            parse = parse_nft_chain
        else:
            self._chains[family] = DockerUserChain()
            return self._chains[family]
        result = self.runner.run(argv, timeout=PROBE_TIMEOUT, env=FIREWALL_ENV)
        output = (result.stderr or result.stdout).strip()
        source = " ".join(argv)
        if result.success:
            chain = DockerUserChain(tuple(parse(result.stdout)), self.public_interfaces(), source)
        elif _missing_chain(output):
            chain = DockerUserChain(source=source)
        else:
            chain = DockerUserChain(
                source=source, error=f"{source}: {output or f'exit {result.exit_code}'}"
            )
        self._chains[family] = chain
        return chain

    def errors(self) -> list[str]:
        """
        Why a ``DOCKER-USER`` chain that was needed could not be read.

        Returns:
            The tools' own words, one line per chain; empty when every chain
            read was read.
        """
        return [chain.error for chain in self._chains.values() if chain.error]

    def unrouted(self, host_address: str) -> str:
        """
        Say why the Internet cannot reach an IPv6 publication at all.

        Docker publishes on ``[::]`` as well as ``0.0.0.0`` whether or not the
        server has IPv6; without an IPv6 default route nothing from outside can
        answer back, so that publication is not an exposure. The owner's central
        was told its six IPv6 publications were open when it has no IPv6 address.

        Args:
            host_address: The address the port is published on.

        Returns:
            The reason, or empty when it is reachable (or IPv4).
        """
        if ":" not in host_address or not self.runner.exists("ip"):
            return ""
        if self.default_routes("ipv6"):
            return ""
        return "no IPv6 default route: nothing outside reaches it over IPv6"

    def covering(
        self, host_address: str, host_port: int, container_port: int, proto: str
    ) -> Coverage | None:
        """
        Find the rule that closes a publication to the Internet.

        Args:
            host_address: The address the port is published on; its family
                picks the chain (``iptables`` or ``ip6tables``).
            host_port: The port on the host.
            container_port: The port inside the container.
            proto: ``tcp`` or ``udp``.

        Returns:
            Why it is filtered, or None when nothing provably refuses it.
        """
        return self.chain(_family_of(host_address)).covering(host_port, container_port, proto)

    def read_error(self, host_address: str) -> str:
        """
        Say why the chain that judges a publication could not be read.

        Args:
            host_address: The address the port is published on.

        Returns:
            The tools' own words, or empty when the chain was read (or is
            not there to read).
        """
        return self.chain(_family_of(host_address)).error
