# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The firewall against the sockets that really listen, and changes that cannot lock anyone out.

What a firewall says it allows and what a stranger can reach are two
different things, and the gap is where real exposures live:

- **The sockets are the doors.** ``ss -Hltnup`` says what listens and on
  which address; the firewall's rules only say which of those doors are
  closed. Each listening port gets a verdict: local only, blocked, open to
  everyone, open to some addresses, or no firewall at all.
- **Docker publishes around the firewall.** A port published by Docker goes
  through the ``nat`` table before the ``INPUT`` chain ufw uses, so "ufw is
  active" says nothing about ``0.0.0.0:5435->5432``. Published ports come from
  ``docker ps`` (with the userland proxy off there is no socket for ``ss`` to
  see) and are reported apart, with the fix left guided: publishing on
  ``127.0.0.1:`` in the Compose file. What the operator refuses in Docker's
  ``DOCKER-USER`` chain on the public interface is filtered all the same
  (:mod:`~noust.managers.server.security_docker_user`), and says which rule.

:class:`Firewall` is the only code that changes rules (rule 4), for ufw and
firewalld; nftables and iptables on their own are only read. Every change goes
through :meth:`Firewall._apply`, which arms the revert first
(:class:`~noust.managers.server.security_pending.ChangeLedger`), and the
anti-lockout guard refuses, before anything runs:

- denying, rejecting or rate-limiting a port SSH or a public console listens
  on (``limit`` counts: a central's tunnel reconnects, and six connections in
  thirty seconds would get it banned);
- deleting the last rule that lets everyone reach one of those ports;
- enabling a default-deny firewall: the SSH and console ports are allowed
  first, in the same change, so there is no such thing as enabling it without.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import shlex
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from noust.core import paths
from noust.core.exceptions import SecurityError, ValidationError
from noust.core.runner import EXIT_NOT_FOUND, CommandResult
from noust.managers.server.host import PROBE_TIMEOUT, read_text
from noust.managers.server.security_access import AccessGuardError
from noust.managers.server.security_docker_user import (
    FIREWALL_ENV,
    Coverage,
    DockerUserReader,
)
from noust.managers.server.security_docker_user import port_ranges as _port_ranges
from noust.managers.server.security_pending import (
    CONFIRM_WINDOW,
    ChangeLedger,
    PendingChange,
    audit,
    new_change_id,
)
from noust.managers.server.security_probe import SecurityProbe
from noust.managers.server.security_sockets import ANY_ADDRESSES, Listener

#: What firewall-cmd exits with when firewalld is not running.
FIREWALLD_NOT_RUNNING = 252

#: Ports whose service should never face the internet, and what usually holds them.
RISKY_PORTS: dict[int, str] = {
    2375: "Docker API without TLS",
    2376: "Docker API",
    3306: "MySQL or MariaDB",
    5432: "PostgreSQL",
    5672: "RabbitMQ",
    5984: "CouchDB",
    6379: "Redis or Valkey",
    8086: "InfluxDB",
    9090: "Cockpit",
    9200: "Elasticsearch",
    11211: "Memcached",
    15672: "RabbitMQ management",
    27017: "MongoDB",
}

#: Ports every web server is expected to show the world.
WEB_PORTS = frozenset({80, 443})

#: What ufw's application profiles and firewalld's services open, for the ones
#: that matter to the guard. An unknown profile covers nothing as far as the
#: guard knows, which errs towards refusing.
KNOWN_SERVICES: dict[str, tuple[tuple[int, str], ...]] = {
    "openssh": ((22, "tcp"),),
    "ssh": ((22, "tcp"),),
    "http": ((80, "tcp"),),
    "https": ((443, "tcp"),),
    "nginx http": ((80, "tcp"),),
    "nginx https": ((443, "tcp"),),
    "nginx full": ((80, "tcp"), (443, "tcp")),
    "apache": ((80, "tcp"),),
    "apache secure": ((443, "tcp"),),
    "apache full": ((80, "tcp"), (443, "tcp")),
    "www": ((80, "tcp"),),
    "www secure": ((443, "tcp"),),
    "www full": ((80, "tcp"), (443, "tcp")),
    "cockpit": ((9090, "tcp"),),
}

#: The prefix of the comment on every ufw rule Noust adds.
NOUST_COMMENT = "noust:"


@dataclass(frozen=True)
class FirewallRule:
    """
    One rule, as the console lists it and as a deletion names it.

    Attributes:
        id: Stable identifier: a hash of the backend and the rule's own spelling.
        backend: ``ufw`` or ``firewalld``.
        action: ``allow``, ``deny``, ``reject`` or ``limit``.
        ports: Inclusive port ranges; empty for every port.
        proto: ``tcp``, ``udp`` or ``any``.
        source: ``any``, or the address or network it applies to.
        spec: The rule as its tool spells it (ufw's ``show added`` without the
            leading ``ufw``; firewalld's ``port 8080/tcp``, ``service ssh``,
            ``rich rule ...``).
        comment: ufw's comment.
        noust: Noust added it (its comment starts with ``noust:``).
        service: The ufw application profile or firewalld service it names.
        known: Its ports are known (a service Noust does not know is not).
    """

    id: str
    backend: str
    action: str
    ports: tuple[tuple[int, int], ...]
    proto: str
    source: str
    spec: str
    comment: str = ""
    noust: bool = False
    service: str | None = None
    known: bool = True

    def covers(self, port: int, proto: str = "tcp") -> bool:
        """
        Report whether the rule applies to a port.

        Args:
            port: The port.
            proto: ``tcp`` or ``udp``.

        Returns:
            True when it names the port (or every port) and the protocol.
        """
        if not self.known:
            return False
        if self.proto not in ("any", proto):
            return False
        return not self.ports or any(low <= port <= high for low, high in self.ports)

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the rule for the API and ``--json``.

        Returns:
            Every field, the ranges as ``[low, high]`` pairs.
        """
        data = asdict(self)
        data["ports"] = [list(pair) for pair in self.ports]
        return data


def _rule_id(backend: str, spec: str) -> str:
    """
    Identify a rule by what it says.

    Args:
        backend: ``ufw`` or ``firewalld``.
        spec: The rule without its comment.

    Returns:
        Twelve hexadecimal characters.
    """
    return hashlib.sha256(f"{backend}:{spec}".encode()).hexdigest()[:12]


@dataclass
class FirewallState:
    """
    The firewall as it is now.

    Attributes:
        backend: ``ufw``, ``firewalld``, ``nftables`` or ``none``.
        active: It filters traffic now.
        default_incoming: What happens to a connection no rule matches:
            ``deny``, ``reject``, ``drop``, ``allow``, or empty when unknown.
        rules: Its rules; nftables' are not listed.
        zone: firewalld's default zone.
        installed: The backend's tool is installed (ufw can be installed and off).
        status: What the tool printed, verbatim.
        others: Other firewalls that are also active: a conflict.
        warnings: Sentences the console shows above the rules.
        error: Why the state could not be read, verbatim.
    """

    backend: str
    active: bool = False
    default_incoming: str = ""
    rules: list[FirewallRule] = field(default_factory=list)
    zone: str | None = None
    installed: bool = False
    status: str = ""
    others: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def denies_by_default(self) -> bool:
        """Whether a port no rule opens is closed."""
        return self.active and self.default_incoming in ("deny", "reject", "drop")

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the state for the API and ``--json``.

        Returns:
            Every field, the rules described.
        """
        data = asdict(self)
        data["rules"] = [rule.to_dict() for rule in self.rules]
        return data


@dataclass(frozen=True)
class DockerPort:
    """
    A port Docker publishes on the host.

    Attributes:
        container: The container's name.
        project: Its Compose project, when it has one.
        host_address: The address it is published on.
        host_port: The port on the host.
        container_port: The port inside the container.
        proto: ``tcp`` or ``udp``.
    """

    container: str
    project: str | None
    host_address: str
    host_port: int
    container_port: int
    proto: str

    @property
    def public(self) -> bool:
        """Published on every interface (Docker's default)."""
        return self.host_address in ANY_ADDRESSES


@dataclass(frozen=True)
class ConsoleSockets:
    """
    Where Noust's console listens, and how that was established.

    Attributes:
        listeners: Its listening sockets.
        by_process: They were found by the console's own processes (the
            service's cgroup, or its main process); False when by the
            configured port, which is only a claim about where it listens.
        port: The configured ``web.port``.
        note: Why the configured port was used, for the evidence; empty
            when the processes decided.
    """

    listeners: tuple[Listener, ...]
    by_process: bool
    port: int
    note: str = ""

    def holds(self, proto: str, port: int, address: str) -> bool:
        """
        Report whether a socket is the console's.

        Args:
            proto: ``tcp`` or ``udp``.
            port: The port.
            address: What it is bound to.

        Returns:
            True for one of its sockets.
        """
        return any(
            (listener.proto, listener.port, listener.address) == (proto, port, address)
            for listener in self.listeners
        )


#: Programs that hold a port for someone else: never the console, whatever port
#: the configuration names.
_PROXIES = frozenset({"docker-proxy", "rootlesskit"})


@dataclass(frozen=True)
class PortExposure:
    """
    One port that answers, and what the firewall does about it.

    Attributes:
        proto: ``tcp`` or ``udp``.
        port: The port.
        address: What it is bound to.
        process: Who holds it.
        verdict: ``local``, ``blocked``, ``open``, ``open_to``,
            ``no_firewall`` or ``docker_bypass``.
        sources: For ``open_to``, the addresses allowed.
        risky: What usually holds this port, when it is one that should never
            face the internet (a database, Redis, the Docker API).
        baseline: SSH, the web ports or the public console: expected to answer.
        docker: The Docker publication, for ``docker_bypass`` (and for
            ``blocked`` when ``DOCKER-USER`` refuses it).
        filtered_by: For a Docker publication ``DOCKER-USER`` refuses, the
            rule and the port it names, for the evidence.
    """

    proto: str
    port: int
    address: str
    process: str | None
    verdict: str
    sources: tuple[str, ...] = ()
    risky: str = ""
    baseline: bool = False
    docker: DockerPort | None = None
    filtered_by: str = ""

    @property
    def reachable(self) -> bool:
        """Whether a stranger on the internet can connect to it."""
        return self.verdict in ("open", "no_firewall", "docker_bypass")

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the port for the API and ``--json``.

        Returns:
            Every field, the Docker publication as a mapping.
        """
        data = asdict(self)
        data["reachable"] = self.reachable
        return data


@dataclass(frozen=True)
class RuleRequest:
    """
    A rule an operator asks for. Every field is validated; none reaches argv as free text.

    Attributes:
        action: ``allow`` or ``deny``.
        port: The port, 1 to 65535.
        proto: ``tcp``, ``udp`` or ``any``.
        source: ``any``, or an address or network.
        comment: A short note, reduced to safe characters; ``noust:`` is added.
    """

    action: str
    port: int
    proto: str = "tcp"
    source: str = "any"
    comment: str = ""

    def validated(self) -> RuleRequest:
        """
        Check every field.

        Returns:
            The request, with the source normalised.

        Raises:
            ValidationError: A field is not what a rule may carry.
        """
        if self.action not in ("allow", "deny"):
            raise ValidationError("A rule's action is allow or deny", field="action")
        if not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            raise ValidationError("A port is a number from 1 to 65535", field="port")
        if self.proto not in ("tcp", "udp", "any"):
            raise ValidationError("A protocol is tcp, udp or any", field="proto")
        source = (self.source or "any").strip()
        if source != "any":
            try:
                source = str(ipaddress.ip_network(source, strict=False))
            except ValueError as exc:
                raise ValidationError(
                    f"{self.source!r} is not an address or a network", field="source"
                ) from exc
        comment = "".join(c for c in self.comment if c.isalnum() or c in " ._-")[:48].strip()
        return RuleRequest(self.action, self.port, self.proto, source, comment)


@dataclass(frozen=True)
class FirewallPlan:
    """
    A firewall change, worked out and guarded, not yet made.

    Attributes:
        title: What it does, in a sentence.
        apply: The commands that make it, in order.
        undo: The commands that undo it, in order.
        commit: The commands that make it permanent once confirmed (firewalld).
    """

    title: str
    apply: list[list[str]]
    undo: list[list[str]]
    commit: list[list[str]]


@dataclass(frozen=True)
class Protected:
    """
    What the anti-lockout guard keeps open.

    Attributes:
        ports: Port to why it is protected (``SSH``, ``console``).
        sources: Addresses of the SSH sessions open now.
    """

    ports: dict[int, str]
    sources: frozenset[str]


# Parsers ------------------------------------------------------------------------


def parse_ufw_status(text: str) -> tuple[bool, str]:
    """
    Read ``ufw status verbose``.

    Args:
        text: Its output.

    Returns:
        Whether ufw is active, and its default for incoming connections.
    """
    active = False
    incoming = ""
    for line in text.splitlines():
        if line.startswith("Status:"):
            active = line.split(":", 1)[1].strip() == "active"
        elif line.startswith("Default:"):
            for part in line.split(":", 1)[1].split(","):
                words = part.strip().split()
                if len(words) >= 2 and words[1] == "(incoming)":
                    incoming = words[0]
    return active, incoming


def parse_ufw_added(text: str) -> list[FirewallRule]:
    """
    Read ``ufw show added``: the rules as the commands that made them.

    Args:
        text: Its output.

    Returns:
        The inbound rules; ``route`` and ``out`` rules are left out, since they
        do not decide who reaches this server.
    """
    rules: list[FirewallRule] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("ufw "):
            continue
        try:
            tokens = shlex.split(line)[1:]
        except ValueError:
            continue
        rule = _ufw_rule(tokens)
        if rule is not None:
            rules.append(rule)
    return rules


def _ufw_rule(tokens: list[str]) -> FirewallRule | None:
    """
    Read one ufw rule from its tokens.

    Args:
        tokens: The command after ``ufw``.

    Returns:
        The rule, or None for one that is not inbound.
    """
    comment = ""
    if "comment" in tokens:
        at = tokens.index("comment")
        comment = " ".join(tokens[at + 1 :])
        tokens = tokens[:at]
    spec = " ".join(shlex.quote(token) for token in tokens)
    if not tokens or tokens[0] == "route":
        return None
    action = tokens[0]
    rest = tokens[1:]
    if rest[:1] == ["out"]:
        return None
    if rest[:1] == ["in"]:
        rest = rest[1:]
    if rest[:1] == ["on"]:
        rest = rest[2:]
    rest = [token for token in rest if token not in ("log", "log-all")]
    ports: tuple[tuple[int, int], ...] = ()
    proto = "any"
    source = "any"
    service: str | None = None
    known = True
    if rest and rest[0] in ("from", "to", "proto", "port", "app"):
        index = 0
        side = ""
        while index < len(rest):
            word = rest[index]
            value = rest[index + 1] if index + 1 < len(rest) else ""
            if word in ("from", "to"):
                side = word
                if word == "from":
                    source = value
                index += 2
            elif word == "port":
                if side == "to":
                    ports = _port_ranges(value) or ()
                index += 2
            elif word == "proto":
                proto = value
                index += 2
            elif word == "app":
                if side == "to":
                    service = value
                index += 2
            else:
                index += 1
    elif rest:
        target, _, protocol = rest[0].partition("/")
        parsed = _port_ranges(target)
        if parsed is None:
            service = rest[0]
        else:
            ports = parsed
            proto = protocol or "any"
    if service is not None:
        entries = KNOWN_SERVICES.get(service.lower())
        known = entries is not None
        if entries:
            ports = tuple((port, port) for port, _proto in entries)
            proto = entries[0][1]
    return FirewallRule(
        id=_rule_id("ufw", spec),
        backend="ufw",
        action=action,
        ports=ports,
        proto=proto,
        source="any" if source in ("any", "0.0.0.0/0", "::/0") else source,
        spec=spec,
        comment=comment,
        noust=comment.startswith(NOUST_COMMENT),
        service=service,
        known=known,
    )


def parse_firewalld_zone(text: str) -> dict[str, str]:
    """
    Read ``firewall-cmd --list-all``.

    Args:
        text: Its output: a zone header, then ``key: value`` lines, and rich
            rules one per line after ``rich rules:``.

    Returns:
        Key to value; ``rich rules`` holds the rules one per line.
    """
    fields: dict[str, str] = {}
    rich: list[str] = []
    in_rich = False
    for raw in text.splitlines()[1:]:
        line = raw.strip()
        if in_rich and line.startswith("rule "):
            rich.append(line)
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        in_rich = key == "rich rules"
        fields[key] = value.strip()
    fields["rich rules"] = "\n".join(rich)
    return fields


def firewalld_rules(fields: dict[str, str]) -> list[FirewallRule]:
    """
    Turn a firewalld zone into rules.

    Args:
        fields: :func:`parse_firewalld_zone`'s output.

    Returns:
        One rule per service, port and rich rule.
    """
    rules: list[FirewallRule] = []
    for service in fields.get("services", "").split():
        entries = KNOWN_SERVICES.get(service.lower())
        spec = f"service {service}"
        rules.append(
            FirewallRule(
                id=_rule_id("firewalld", spec),
                backend="firewalld",
                action="allow",
                ports=tuple((port, port) for port, _ in entries or ()),
                proto=entries[0][1] if entries else "any",
                source="any",
                spec=spec,
                service=service,
                known=entries is not None,
            )
        )
    for entry in fields.get("ports", "").split():
        target, _, proto = entry.partition("/")
        ranges = _port_ranges(target)
        if ranges is None:
            continue
        spec = f"port {entry}"
        rules.append(
            FirewallRule(
                id=_rule_id("firewalld", spec),
                backend="firewalld",
                action="allow",
                ports=ranges,
                proto=proto or "any",
                source="any",
                spec=spec,
            )
        )
    for rich in fields.get("rich rules", "").splitlines():
        rule = _rich_rule(rich)
        if rule is not None:
            rules.append(rule)
    return rules


def _rich_rule(text: str) -> FirewallRule | None:
    """
    Read one firewalld rich rule.

    Args:
        text: ``rule family="ipv4" source address="10.0.0.0/8" port port="5432" protocol="tcp" accept``.

    Returns:
        The rule, or None for one this does not understand.
    """
    try:
        tokens = shlex.split(text)
    except ValueError:
        return None
    values: dict[str, str] = {}
    for token in tokens:
        key, sep, value = token.partition("=")
        if sep:
            values.setdefault(key, value)
    action = next(
        (
            {"accept": "allow", "reject": "reject", "drop": "deny"}[word]
            for word in tokens
            if word in ("accept", "reject", "drop")
        ),
        None,
    )
    if action is None:
        return None
    ranges = _port_ranges(values.get("port", "")) if "port" in values else ()
    spec = f"rich rule {text}"
    return FirewallRule(
        id=_rule_id("firewalld", spec),
        backend="firewalld",
        action=action,
        ports=ranges or (),
        proto=values.get("protocol", "any"),
        source=values.get("address", "any"),
        spec=spec,
    )


def parse_docker_ps(text: str) -> list[DockerPort]:
    """
    Read ``docker ps --format '{{json .}}'``.

    Args:
        text: One JSON object per container.

    Returns:
        Every port published on the host.
    """
    found: list[DockerPort] = []
    for raw in text.splitlines():
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        labels = dict(
            item.split("=", 1) for item in str(entry.get("Labels", "")).split(",") if "=" in item
        )
        project = labels.get("com.docker.compose.project")
        for published in str(entry.get("Ports", "")).split(","):
            mapping = published.strip()
            if "->" not in mapping:
                continue
            host_side, _, container_side = mapping.partition("->")
            address, _, host_port = host_side.rpartition(":")
            container_port, _, proto = container_side.partition("/")
            host_range = _port_ranges(host_port)
            container_range = _port_ranges(container_port)
            if not host_range or not container_range:
                continue
            for offset, port in enumerate(range(host_range[0][0], host_range[0][1] + 1)):
                found.append(
                    DockerPort(
                        container=str(entry.get("Names", "")),
                        project=project,
                        host_address=address.strip("[]"),
                        host_port=port,
                        container_port=container_range[0][0] + offset,
                        proto=proto or "tcp",
                    )
                )
    return found


# The manager ----------------------------------------------------------------------


class Firewall:
    """
    Read the firewall, and change it without locking anyone out.

    Args:
        probe: This pass's look at the machine.
        ledger: Where changes wait for confirmation.
        actor: Who is asking.
        on_output: Receives every command and its output, verbatim.
        console_port: The console's port, when known; read from the
            configuration otherwise.
    """

    def __init__(
        self,
        probe: SecurityProbe,
        ledger: ChangeLedger | None = None,
        *,
        actor: str = "noust",
        on_output: Callable[[str], None] | None = None,
        console_port: int | None = None,
    ) -> None:
        self.probe = probe
        self.ledger = ledger
        self.actor = actor
        self.on_output = on_output
        self._console_port = console_port
        self._console: ConsoleSockets | None = None
        self._state: FirewallState | None = None
        # The one reading of DOCKER-USER and the default routes, shared with the
        # databases' exposure report (rule 3).
        self._docker = DockerUserReader(probe.runner)

    def _run(self, argv: list[str]) -> CommandResult:
        """
        Run a read-only firewall probe.

        Args:
            argv: The command.

        Returns:
            Its result.
        """
        return self.probe.runner.run(argv, timeout=PROBE_TIMEOUT, env=FIREWALL_ENV)

    # Reading ------------------------------------------------------------------

    def state(self) -> FirewallState:
        """
        Read the firewall: ufw first, then firewalld, then nftables alone.

        Returns:
            The state; two active firewalls are reported as a conflict.
        """
        if self._state is not None:
            return self._state
        runner = self.probe.runner
        candidates: list[FirewallState] = []
        if runner.exists("ufw"):
            candidates.append(self._ufw_state())
        if runner.exists("firewall-cmd"):
            candidates.append(self._firewalld_state())
        active = [candidate for candidate in candidates if candidate.active]
        if active:
            state = active[0]
            state.others = [other.backend for other in active[1:]]
            if state.others:
                state.warnings.append(
                    f"{state.backend} and {', '.join(state.others)} are both active; they "
                    "filter the same traffic twice and their rules can contradict each other."
                )
        elif candidates:
            state = candidates[0]
        else:
            state = self._nft_state()
        self._state = state
        return state

    def _ufw_state(self) -> FirewallState:
        status = self._run(["ufw", "status", "verbose"])
        if not status.success:
            return FirewallState(
                backend="ufw",
                installed=True,
                error=(status.stderr or status.stdout).strip() or f"ufw exited {status.exit_code}",
            )
        active, incoming = parse_ufw_status(status.stdout)
        added = self._run(["ufw", "show", "added"])
        state = FirewallState(
            backend="ufw",
            active=active,
            default_incoming=incoming,
            rules=parse_ufw_added(added.stdout) if added.success else [],
            installed=True,
            status=status.stdout.strip(),
        )
        defaults = read_text(self.probe.host.at("/etc/default/ufw")) or ""
        if any(line.strip() == "IPV6=no" for line in defaults.splitlines()):
            state.warnings.append(
                "ufw is set to IPV6=no in /etc/default/ufw: it does not filter IPv6 at all."
            )
        return state

    def _firewalld_state(self) -> FirewallState:
        running = self._run(["firewall-cmd", "--state"])
        if running.exit_code == FIREWALLD_NOT_RUNNING or not running.success:
            return FirewallState(
                backend="firewalld",
                installed=True,
                status=(running.stdout or running.stderr).strip(),
            )
        zone = self._run(["firewall-cmd", "--get-default-zone"]).stdout.strip() or "public"
        runtime = self._run(["firewall-cmd", f"--zone={zone}", "--list-all"])
        permanent = self._run(["firewall-cmd", "--permanent", f"--zone={zone}", "--list-all"])
        fields = parse_firewalld_zone(runtime.stdout)
        target = fields.get("target", "default").lower()
        state = FirewallState(
            backend="firewalld",
            active=True,
            default_incoming={"accept": "allow", "drop": "drop"}.get(target, "reject"),
            rules=firewalld_rules(fields),
            zone=zone,
            installed=True,
            status=runtime.stdout.strip(),
        )
        if permanent.success and firewalld_rules(parse_firewalld_zone(permanent.stdout)) != (
            state.rules
        ):
            state.warnings.append(
                f"The running rules of zone {zone} differ from the saved ones: what differs "
                "is lost at the next reload or reboot."
            )
        return state

    def _nft_state(self) -> FirewallState:
        result = self._run(["nft", "-j", "list", "ruleset"])
        if result.exit_code == EXIT_NOT_FOUND or not result.success:
            return FirewallState(backend="none")
        try:
            ruleset = json.loads(result.stdout).get("nftables", [])
        except (json.JSONDecodeError, AttributeError):
            return FirewallState(backend="none")
        policy = ""
        for item in ruleset:
            chain = item.get("chain") if isinstance(item, dict) else None
            if isinstance(chain, dict) and chain.get("hook") == "input":
                policy = str(chain.get("policy", "accept"))
        if not policy:
            return FirewallState(backend="none")
        return FirewallState(
            backend="nftables",
            active=True,
            default_incoming="allow" if policy == "accept" else "drop",
            installed=True,
            warnings=[
                "nftables rules are in place without ufw or firewalld; Noust reads their "
                "default but does not list or change them."
            ],
        )

    def docker_ports(self) -> tuple[list[DockerPort], str]:
        """
        What Docker publishes on the host.

        Returns:
            The ports, and why they could not be read (empty when Docker is
            absent or answered).
        """
        if not self.probe.runner.exists("docker"):
            return [], ""
        result = self._run(["docker", "ps", "--format", "{{json .}}"])
        if not result.success:
            return [], (result.stderr or result.stdout).strip()
        return parse_docker_ps(result.stdout), ""

    def unrouted(self, port: DockerPort) -> str:
        """
        Say why the Internet cannot reach an IPv6 publication at all.

        Args:
            port: The publication.

        Returns:
            The reason, or empty when it is reachable (or IPv4).
        """
        return self._docker.unrouted(port.host_address)

    def docker_user_errors(self) -> list[str]:
        """
        Why a ``DOCKER-USER`` chain that was needed could not be read.

        Returns:
            The tools' own words, one line per chain; empty when every chain
            read was read.
        """
        return self._docker.errors()

    def docker_filter(self, port: DockerPort) -> Coverage | None:
        """
        Find the ``DOCKER-USER`` rule that closes a publication to the Internet.

        Args:
            port: The publication.

        Returns:
            Why it is filtered, or None when nothing provably refuses it.
        """
        return self._docker.covering(
            port.host_address, port.host_port, port.container_port, port.proto
        )

    def console_port(self) -> int:
        """
        The console's port.

        Returns:
            The configured ``web.port``.
        """
        if self._console_port is None:
            from noust.core.config import Config

            self._console_port = int(Config().get("web.port", 8080) or 8080)
        return self._console_port

    def _console_pids(self) -> frozenset[int]:
        """
        The processes of ``noust-web.service``: its cgroup, else its main process.

        Returns:
            Their ids; empty when the service does not run (or systemd did not say).
        """
        # One reading of a unit's processes (rule 3): the zero-downtime switch
        # asks the same question of an application's unit.
        from noust.deployers.bluegreen import CGROUP_ROOTS, unit_facts_of

        runner = self.probe.runner
        cgroups = tuple(self.probe.host.at(str(root)) for root in CGROUP_ROOTS)
        facts = unit_facts_of(paths.WEB_UNIT, runner=runner, cgroups=cgroups)
        if facts.pids:
            return facts.pids
        # A host with only the cgroup v1 hierarchy: the main process, which
        # holds the socket unless the console runs workers.
        result = runner.run(
            ["systemctl", "show", "-p", "MainPID", "--value", f"{paths.WEB_UNIT}.service"],
            timeout=PROBE_TIMEOUT,
        )
        value = result.stdout.strip() if result.success else ""
        return frozenset({int(value)}) if value.isdigit() and int(value) > 0 else frozenset()

    def console_sockets(self) -> ConsoleSockets:
        """
        Find the sockets Noust's console listens on.

        By its processes first: the configuration says where the console was
        meant to listen, and on one server it named the port Docker held while
        the console ran on another, so the console was blamed for Docker's
        socket. Only when no console process is found (a console started by
        hand, a host where systemd does not say) does the configured port
        decide, and the result says so.

        Returns:
            The sockets, and how they were found.
        """
        if self._console is not None:
            return self._console
        listeners, _error = self.probe.listeners()
        port = self.console_port()
        unit = f"{paths.WEB_UNIT}.service"
        pids = self._console_pids()
        if pids:
            owned = tuple(listener for listener in listeners if pids.intersection(listener.pids))
            if owned:
                self._console = ConsoleSockets(owned, True, port)
                return self._console
            note = (
                f"No socket of {unit} was in ss's list: the console was looked for on the "
                f"configured port, web.port {port}."
            )
        else:
            note = (
                f"{unit} is not running: the console was looked for on the configured port, "
                f"web.port {port}."
            )
        configured = tuple(
            listener
            for listener in listeners
            if listener.port == port and listener.process not in _PROXIES
        )
        self._console = ConsoleSockets(configured, False, port, note)
        return self._console

    def exposures(self) -> tuple[list[PortExposure], str]:
        """
        Every port that answers, with the firewall's verdict on it.

        Returns:
            The ports - sockets first, then Docker's publications - and why the
            sockets could not be read, when they could not.
        """
        state = self.state()
        listeners, error = self.probe.listeners()
        ssh_ports = self.probe.ssh_ports()
        console = self.console_sockets()
        found: list[PortExposure] = []
        seen: set[tuple[str, int, str]] = set()
        for listener in listeners:
            key = (listener.proto, listener.port, listener.address)
            if key in seen:
                continue
            seen.add(key)
            verdict, sources = self._verdict(
                state, listener.port, listener.proto, listener.exposure
            )
            baseline = (
                listener.port in ssh_ports
                or listener.port in WEB_PORTS
                or (
                    console.holds(listener.proto, listener.port, listener.address)
                    and listener.exposure != "local"
                )
            )
            found.append(
                PortExposure(
                    proto=listener.proto,
                    port=listener.port,
                    address=listener.address,
                    process=listener.process,
                    verdict=verdict,
                    sources=sources,
                    risky=RISKY_PORTS.get(listener.port, "") if not baseline else "",
                    baseline=baseline,
                )
            )
        published, _docker_error = self.docker_ports()
        for port in published:
            if not port.public:
                continue
            unrouted = self.unrouted(port)
            coverage = None if unrouted else self.docker_filter(port)
            if unrouted or coverage is not None:
                verdict = "blocked"
            else:
                verdict = "docker_bypass" if state.active else "no_firewall"
            found.append(
                PortExposure(
                    proto=port.proto,
                    port=port.host_port,
                    address=port.host_address,
                    process=f"docker: {port.container}",
                    verdict=verdict,
                    risky=RISKY_PORTS.get(port.container_port, "")
                    or RISKY_PORTS.get(port.host_port, ""),
                    docker=port,
                    filtered_by=coverage.describe() if coverage is not None else unrouted,
                )
            )
        return found, error

    @staticmethod
    def _verdict(
        state: FirewallState, port: int, proto: str, exposure: str
    ) -> tuple[str, tuple[str, ...]]:
        """
        Decide what the firewall does with one listening port.

        Args:
            state: The firewall.
            port: The port.
            proto: ``tcp`` or ``udp``.
            exposure: The socket's ``local``, ``all`` or ``interface``.

        Returns:
            The verdict, and the sources for ``open_to``.
        """
        if exposure == "local":
            return "local", ()
        if not state.active:
            return "no_firewall", ()
        allows = [
            rule
            for rule in state.rules
            if rule.action in ("allow", "limit") and rule.covers(port, proto)
        ]
        if any(rule.source == "any" for rule in allows):
            return "open", ()
        if allows:
            return "open_to", tuple(sorted({rule.source for rule in allows}))
        if state.default_incoming == "allow":
            return "open", ()
        return "blocked", ()

    # The guard ------------------------------------------------------------------

    def protected(self) -> Protected:
        """
        What must stay reachable: the SSH ports, a public console, the sessions open now.

        Returns:
            The protected ports and sources.
        """
        ports = dict.fromkeys(self.probe.ssh_ports(), "SSH")
        for listener in self.console_sockets().listeners:
            if listener.exposure != "local":
                ports.setdefault(listener.port, "the console")
        sources = frozenset(session.connection.peer_address for session in self.probe.sessions())
        return Protected(ports=ports, sources=sources)

    def _guard_rule(self, request: RuleRequest, protected: Protected) -> None:
        """
        Refuse a rule that closes a protected port.

        Args:
            request: The validated rule.
            protected: What must stay reachable.

        Raises:
            AccessGuardError: It would.
        """
        if request.action == "allow":
            return
        what = protected.ports.get(request.port)
        if what is None or request.proto == "udp":
            return
        if request.source != "any":
            network = ipaddress.ip_network(request.source, strict=False)
            hit = [src for src in protected.sources if ipaddress.ip_address(src) in network]
            if not hit:
                return
        audit(
            "server.firewall",
            "rules",
            outcome="denied",
            action=f"{request.action} port {request.port}",
            reason=f"{what} listens there",
        )
        raise AccessGuardError(
            f"Refusing to {request.action} port {request.port}: {what} listens there",
            details="Closing it could cut off the SSH sessions open now and a central's "
            "tunnel. To restrict who reaches it, allow the addresses you use and let the "
            "default policy close the rest.",
        )

    def _guard_delete(self, rule: FirewallRule, state: FirewallState, protected: Protected) -> None:
        """
        Refuse to delete the last rule that opens a protected port to everyone.

        Args:
            rule: The rule to delete.
            state: The firewall.
            protected: What must stay reachable.

        Raises:
            AccessGuardError: It is that rule.
        """
        if rule.action not in ("allow", "limit") or not state.denies_by_default:
            return
        for port, what in protected.ports.items():
            if not rule.covers(port, "tcp"):
                continue
            others = [
                other
                for other in state.rules
                if other.id != rule.id
                and other.action in ("allow", "limit")
                and other.source == "any"
                and other.covers(port, "tcp")
            ]
            if not others:
                audit(
                    "server.firewall",
                    "rules",
                    outcome="denied",
                    action=f"delete '{rule.spec}'",
                    reason=f"last rule opening port {port} ({what})",
                )
                raise AccessGuardError(
                    f"Refusing to delete the rule that lets everyone reach port {port} ({what})",
                    details="With the default policy denying, nobody could open a new SSH "
                    "session, the central's tunnel included. Add the rule you want instead "
                    "first, then delete this one.",
                )

    # Changes --------------------------------------------------------------------

    def _require_ledger(self) -> ChangeLedger:
        if self.ledger is None:
            raise SecurityError("This firewall was opened for reading only")
        return self.ledger

    def _editable(self) -> FirewallState:
        state = self.state()
        if state.backend not in ("ufw", "firewalld") or not state.installed:
            raise SecurityError(
                "Noust changes ufw and firewalld rules, and neither is in use here",
                details="Install ufw (apt-get install ufw) or firewalld (dnf install firewalld); "
                "nftables and iptables rules are only read.",
            )
        if state.error:
            raise SecurityError("The firewall's state could not be read", output=state.error)
        return state

    def plan_add(self, request: RuleRequest) -> FirewallPlan:
        """
        Work out adding a rule, guard included, without changing anything.

        Args:
            request: The rule.

        Returns:
            The commands that make it, undo it and make it permanent.

        Raises:
            AccessGuardError: It would close SSH or the console.
            ValidationError: A field is invalid.
        """
        rule = request.validated()
        state = self._editable()
        self._guard_rule(rule, self.protected())
        note = f"{NOUST_COMMENT} {rule.comment}".strip()
        title = f"{rule.action.capitalize()} {rule.proto} port {rule.port} from {rule.source}"
        if state.backend == "ufw":
            spec = ["from", rule.source, "to", "any", "port", str(rule.port)]
            if rule.proto != "any":
                spec += ["proto", rule.proto]
            apply = [["ufw", rule.action, *spec, "comment", note]]
            undo = [["ufw", "delete", rule.action, *spec]]
            return FirewallPlan(title, apply, undo, [])
        zone = state.zone or "public"
        protos = ["tcp", "udp"] if rule.proto == "any" else [rule.proto]
        apply, undo, commit = [], [], []
        for proto in protos:
            if rule.source == "any" and rule.action == "allow":
                option = f"port={rule.port}/{proto}"
                apply.append(["firewall-cmd", f"--zone={zone}", f"--add-{option}"])
                commit.append(["firewall-cmd", "--permanent", f"--zone={zone}", f"--add-{option}"])
                undo.append(["firewall-cmd", f"--zone={zone}", f"--remove-{option}"])
                continue
            text = _rich_text(rule, proto)
            apply.append(["firewall-cmd", f"--zone={zone}", f"--add-rich-rule={text}"])
            commit.append(
                ["firewall-cmd", "--permanent", f"--zone={zone}", f"--add-rich-rule={text}"]
            )
            undo.append(["firewall-cmd", f"--zone={zone}", f"--remove-rich-rule={text}"])
        return FirewallPlan(title, apply, undo, commit)

    def plan_delete(self, rule_id: str) -> FirewallPlan:
        """
        Work out deleting a rule, guard included, without changing anything.

        Args:
            rule_id: :attr:`FirewallRule.id`.

        Returns:
            The commands that make it, undo it and make it permanent.

        Raises:
            AccessGuardError: It is the last rule opening SSH or the console.
            SecurityError: There is no such rule.
        """
        state = self._editable()
        rule = next((item for item in state.rules if item.id == rule_id), None)
        if rule is None:
            raise SecurityError(
                f"There is no firewall rule {rule_id}",
                details="List the rules with 'noust server security firewall status'.",
            )
        self._guard_delete(rule, state, self.protected())
        title = f"Delete the firewall rule '{rule.spec}'"
        if rule.backend == "ufw":
            tokens = shlex.split(rule.spec)
            readd = [*tokens, "comment", rule.comment] if rule.comment else tokens
            return FirewallPlan(title, [["ufw", "delete", *tokens]], [["ufw", *readd]], [])
        zone = state.zone or "public"
        if rule.spec.startswith("rich rule "):
            option = "rich-rule=" + rule.spec.removeprefix("rich rule ")
        else:
            kind, _, value = rule.spec.partition(" ")
            option = f"{kind}={value}"
        return FirewallPlan(
            title,
            [["firewall-cmd", f"--zone={zone}", f"--remove-{option}"]],
            [["firewall-cmd", f"--zone={zone}", f"--add-{option}"]],
            [["firewall-cmd", "--permanent", f"--zone={zone}", f"--remove-{option}"]],
        )

    def plan_enable(self) -> FirewallPlan:
        """
        Work out turning ufw on with a default-deny policy, SSH and a public console allowed first.

        Returns:
            The commands that make it, undo it and make it permanent.

        Raises:
            SecurityError: This is not ufw, or ufw is already active.
        """
        state = self._editable()
        if state.backend != "ufw":
            raise SecurityError(
                "Noust turns on ufw only",
                details="For firewalld, allow SSH in the default zone with "
                "'firewall-offline-cmd --add-service=ssh', then 'systemctl enable --now "
                "firewalld', keeping this session open.",
            )
        if state.active:
            raise SecurityError("ufw is already active")
        protected = self.protected()
        apply: list[list[str]] = []
        undo: list[list[str]] = [["ufw", "disable"]]
        for port, what in sorted(protected.ports.items()):
            if any(
                rule.action in ("allow", "limit") and rule.source == "any" and rule.covers(port)
                for rule in state.rules
            ):
                continue
            spec = ["from", "any", "to", "any", "port", str(port), "proto", "tcp"]
            apply.append(["ufw", "allow", *spec, "comment", f"{NOUST_COMMENT} {what}"])
            undo.append(["ufw", "delete", "allow", *spec])
        previous = state.default_incoming or "allow"
        apply += [
            ["ufw", "default", "deny", "incoming"],
            ["ufw", "default", "allow", "outgoing"],
            ["ufw", "--force", "enable"],
        ]
        undo.insert(1, ["ufw", "default", previous, "incoming"])
        return FirewallPlan("Turn on ufw, denying incoming by default", apply, undo, [])

    def plan_disable(self) -> FirewallPlan:
        """
        Work out turning the firewall off.

        Returns:
            The commands that make it, undo it and make it permanent.

        Raises:
            SecurityError: It is not active.
        """
        state = self._editable()
        if not state.active:
            raise SecurityError(f"{state.backend} is not active")
        if state.backend == "ufw":
            return FirewallPlan(
                "Turn off ufw", [["ufw", "disable"]], [["ufw", "--force", "enable"]], []
            )
        return FirewallPlan(
            "Stop firewalld",
            [["systemctl", "stop", "firewalld"]],
            [["systemctl", "start", "firewalld"]],
            [["systemctl", "disable", "firewalld"]],
        )

    def add_rule(self, request: RuleRequest) -> PendingChange:
        """
        Add a rule, pending confirmation.

        Args:
            request: The rule.

        Returns:
            The change.
        """
        return self._apply(self.plan_add(request))

    def delete_rule(self, rule_id: str) -> PendingChange:
        """
        Delete a rule, pending confirmation.

        Args:
            rule_id: :attr:`FirewallRule.id`.

        Returns:
            The change.
        """
        return self._apply(self.plan_delete(rule_id))

    def enable(self) -> PendingChange:
        """
        Turn ufw on, SSH and a public console allowed first, pending confirmation.

        Returns:
            The change.
        """
        return self._apply(self.plan_enable())

    def disable(self) -> PendingChange:
        """
        Turn the firewall off, pending confirmation.

        Returns:
            The change.
        """
        return self._apply(self.plan_disable())

    def _apply(self, plan: FirewallPlan) -> PendingChange:
        """
        Make a firewall change: the one place rules are changed.

        The revert is armed before the first command runs; a command that
        fails undoes the whole change at once.

        Args:
            plan: What to run, what undoes it and what makes it permanent.

        Returns:
            The change, pending.

        Raises:
            SecurityError: Another change is pending, the revert could not be
                armed, or a command failed (and the change was undone).
        """
        ledger = self._require_ledger()
        with ledger.locked():
            waiting = ledger.pending()
            if waiting:
                raise SecurityError(
                    f"Another change is waiting for confirmation: {waiting[0].title} ({waiting[0].id})",
                    details="Confirm it or revert it first: one change at a time can be undone safely.",
                )
            now = self.probe.now()
            state = self.state()
            change = PendingChange(
                id=new_change_id(),
                kind="firewall",
                title=plan.title,
                actor=self.actor,
                applied_at=now,
                expires_at=now + CONFIRM_WINDOW,
                undo=plan.undo,
                commit=plan.commit,
                before={"active": str(state.active).lower(), "default": state.default_incoming},
                proof="any",
            )
            ledger.open(change)
            self._say(f"Armed {change.unit}: it undoes this change in {CONFIRM_WINDOW} s")
            for argv in plan.apply:
                result = self.probe.runner.run(argv, timeout=PROBE_TIMEOUT, env=FIREWALL_ENV)
                self._say("$ " + " ".join(argv))
                for line in (result.stdout + result.stderr).splitlines():
                    self._say(line)
                if not result.success:
                    ledger.revert(change.id, by=self.actor, on_output=self.on_output)
                    audit(
                        "server.firewall",
                        state.backend,
                        outcome="failure",
                        action=plan.title,
                        reason=(result.stderr or result.stdout).strip(),
                    )
                    raise SecurityError(
                        f"The firewall refused '{' '.join(argv)}', so the change was undone",
                        output=(result.stderr or result.stdout).strip() or None,
                    )
            self._state = None
            after = self.state()
            change.after = {"active": str(after.active).lower(), "default": after.default_incoming}
            ledger.save(change)
            audit("server.firewall", state.backend, action=plan.title, change=change.id)
            return change

    def _say(self, line: str) -> None:
        if self.on_output:
            self.on_output(line)


def _rich_text(rule: RuleRequest, proto: str) -> str:
    """
    Spell a firewalld rich rule from validated fields.

    Args:
        rule: The validated request.
        proto: ``tcp`` or ``udp``.

    Returns:
        The rule text.
    """
    parts = ["rule"]
    if rule.source != "any":
        family = "ipv6" if ipaddress.ip_network(rule.source).version == 6 else "ipv4"
        parts += [f'family="{family}"', f'source address="{rule.source}"']
    parts += [f'port port="{rule.port}" protocol="{proto}"']
    parts.append("accept" if rule.action == "allow" else "reject")
    return " ".join(parts)
