# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The fleet at once: one resource, asked of every server a central manages.

The one aggregator (rule 3) behind ``/api/fleet/*``, ``noust fleet status`` and
the ``noust fleet apps|certs|backups|updates|activity`` views. It never knows
what an application or a certificate is: it asks each server's own API for the
same endpoints that server's console reads, through the same tunnel and with
the same operator identity the proxy forwards, and labels every row with the
server it came from. A node therefore applies its own permissions, its fleet
ceiling and its own idea of the data, and nothing here can disagree with it.

What it adds is the fleet's shape:

- **In parallel, with a deadline.** Every server is asked at once, each with
  its own timeout; the answer is sent when every server answered or the
  deadline passed, whichever comes first. A server still being asked is
  reported as such, and its answer keeps coming in the background and warms
  the cache for the next look.
- **Partial, never failed.** Each server has an outcome (:class:`NodeOutcome`):
  ``ok``, ``stale`` (it failed now, and this is its last good answer, with its
  age), ``unreachable``, ``unsupported`` (it answered but does not offer what
  was asked: a 3.0 node), ``forbidden`` (it refused this operator) or
  ``error``, each with the node's or ssh's own words verbatim. A server that
  is down never turns the whole answer into an error.
- **Cached and single-flight.** One answer per server, resource and audience
  (the role and scope the node judged), reused for a few seconds, so ten open
  consoles cost the node what one does. Stale data is served only for a
  failure to reach a node, never for a refusal: a permission taken away must
  not keep answering from the cache.

A central whose role is ``server`` is one of its own servers: the caller hands
it in as a local :class:`Source` that answers through the central's own API.
"""

from __future__ import annotations

import builtins
import json
import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as wait_futures
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import quote

from noust.core.exceptions import NodeError, NodeRefusedError, NodeUnreachableError
from noust.core.store import NodeRecord

if TYPE_CHECKING:
    import httpx

    from noust.fleet.nodes import NodeManager

logger = logging.getLogger(__name__)

#: What a node's outcome can be. The console shows each with a shape, a colour
#: and a word; ``code`` says precisely why.
OUTCOME_STATUSES = ("ok", "stale", "unreachable", "unsupported", "forbidden", "error")

#: Seconds each request to a node may take in an interactive view.
NODE_TIMEOUT = 8.0

#: Seconds a whole interactive view waits before answering with what it has.
DEADLINE = 10.0

#: Nodes asked at once, across every view and every console.
MAX_WORKERS = 16

#: How long a node must stay unreachable before its operator is told: a blip
#: of the tunnel is not an outage.
UNREACHABLE_GRACE_SECONDS = 120.0

#: How often a reachable node's ``last_seen`` is written, at most: every poll
#: of every console would otherwise be a write to the store.
LAST_SEEN_EVERY_SECONDS = 60.0

#: Most of a node's answer kept as its words in an outcome.
MAX_VERBATIM = 4000

VERSION_PATH = "/api/system/version"
MACHINE_PATH = "/api/system/machine"
CERTS_PATH = "/api/certs"

#: A certificate this close to expiry is counted as expiring: the threshold of
#: ``noust health``'s warning, so both say the same thing.
CERT_EXPIRING_DAYS = 30

#: What a node filter names to mean this central itself, whatever its name.
LOCAL_SELECTOR = "@central"


# ----------------------------------------------------------------- outcomes


def _now_iso() -> str:
    """Returns: The current time, ISO 8601 UTC."""
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class NodeOutcome:
    """
    How one server answered one fleet view.

    Attributes:
        name: The server's name on this central (this central's own name for
            its own row).
        status: One of :data:`OUTCOME_STATUSES`.
        age_seconds: How old the data shown for it is; 0 for a fresh answer,
            None when nothing is shown.
        error_verbatim: The node's answer, or ssh's, exactly as it came.
        version: The Noust version it runs, when known.
        code: Why, for a machine: ``node_unreachable``, ``node_refused``,
            ``timeout``, ``not_offered``, ``permission_denied``, ``node_error``,
            or the node's own error code.
        message: The central's one sentence about it.
        hint: What to do about it.
        elapsed_ms: How long its first answer took.
        fetched_at: When the data shown was read, ISO 8601 UTC.
        local: This row is the central itself.
        missing: The node's endpoints this view needed and it does not offer.
        warnings: Parts of the view that could not be read, one sentence each.
    """

    name: str
    status: str
    age_seconds: float | None = None
    error_verbatim: str | None = None
    version: str | None = None
    code: str | None = None
    message: str | None = None
    hint: str | None = None
    elapsed_ms: float | None = None
    fetched_at: str | None = None
    local: bool = False
    missing: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the outcome for JSON.

        Returns:
            Every attribute, ``missing`` and ``warnings`` as lists.
        """
        return {
            "name": self.name,
            "local": self.local,
            "status": self.status,
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "error_verbatim": self.error_verbatim,
            "age_seconds": self.age_seconds,
            "fetched_at": self.fetched_at,
            "elapsed_ms": self.elapsed_ms,
            "version": self.version,
            "missing": list(self.missing),
            "warnings": list(self.warnings),
        }


@dataclass
class FleetResult:
    """
    One fleet view: how each server answered, and the rows they gave.

    Attributes:
        resource: The view, such as ``apps``.
        generated_at: When it was put together, ISO 8601 UTC.
        nodes: One outcome per server asked, in order.
        items: The rows, each with ``node``, ``local``, ``page`` (the path of
            its page on that server's own console) and ``href`` (the same page
            through this central's console).
    """

    resource: str
    generated_at: str
    nodes: list[NodeOutcome]
    items: list[dict[str, Any]]

    @property
    def partial(self) -> bool:
        """True when any server's answer is not a fresh, complete one."""
        return any(outcome.status != "ok" for outcome in self.nodes)

    def outcome(self, name: str) -> NodeOutcome | None:
        """
        Find one server's outcome.

        Args:
            name: The server's name.

        Returns:
            Its outcome, or None when it was not asked.
        """
        return next((outcome for outcome in self.nodes if outcome.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the view for JSON: the envelope every ``/api/fleet`` view answers.

        Returns:
            ``resource``, ``generated_at``, ``partial``, ``nodes`` and ``items``.
        """
        return {
            "resource": self.resource,
            "generated_at": self.generated_at,
            "partial": self.partial,
            "nodes": [outcome.to_dict() for outcome in self.nodes],
            "items": self.items,
        }


# ------------------------------------------------------------------ sources


class Failure(Exception):
    """
    One request to a server that did not give what was asked.

    Attributes:
        kind: ``unreachable``, ``refused``, ``timeout``, ``forbidden``,
            ``unsupported`` or ``error``.
        message: The central's sentence.
        hint: What to do about it.
        output: The other side's words, verbatim.
        code: The node's own error code, when its answer carried one.
    """

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        hint: str | None = None,
        output: str | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.hint = hint or None
        self.output = (output or None) and output[:MAX_VERBATIM]
        self.code = code


@dataclass(frozen=True)
class Asker:
    """
    Who is asking, as every call to a node says it.

    The same four facts the proxy forwards
    (:func:`noust.web.api.node_proxy.forwarded_identity`): the node applies its
    own permissions for them, so two askers the node would treat differently
    never share a cached answer.

    Attributes:
        actor: The operator on the central, as an actor label.
        scope: ``read``, ``deploy`` or ``admin``.
        role: The operator's account role on the central, when an account.
        elevated: Whether the central vouches for the operator's sudo mode.
    """

    actor: str
    scope: str = "read"
    role: str | None = None
    elevated: bool = False

    @property
    def audience(self) -> str:
        """What a node's answer depends on besides the node: role and scope."""
        return f"{self.role or '-'}/{self.scope}"

    def headers(self) -> dict[str, Any]:
        """
        Returns:
            The keyword arguments of :meth:`noust.fleet.client.NodeClient.request`
            that say who asks.
        """
        return {
            "actor": self.actor,
            "actor_scope": self.scope,
            "actor_role": self.role,
            "elevated": self.elevated,
        }


class Source(Protocol):
    """One server a view asks: a node through its tunnel, or this central itself."""

    @property
    def name(self) -> str:
        """The server's name."""

    @property
    def local(self) -> bool:
        """Whether this is the central itself."""

    @property
    def cache_key(self) -> str:
        """What its answers are cached under, with the audience."""

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        """
        GET one path of the server's API.

        Args:
            path: Such as ``/api/apps``.
            params: Query parameters.

        Returns:
            The decoded JSON.

        Raises:
            Failure: The server did not answer it.
        """


def _body_text(response: httpx.Response) -> str:
    """
    Read an answer's body as text, for an error, verbatim but bounded.

    Args:
        response: The response.

    Returns:
        At most :data:`MAX_VERBATIM` characters.
    """
    try:
        return response.text[:MAX_VERBATIM]
    except UnicodeDecodeError:
        return ""


def _error_fields(text: str | None) -> tuple[str | None, str | None, str | None]:
    """
    Read the API error contract out of a node's answer, when it follows it.

    Args:
        text: The body.

    Returns:
        ``error``, ``detail`` and ``hint``, each None when absent.
    """
    if not text:
        return None, None, None
    try:
        body = json.loads(text)
    except ValueError:
        return None, None, None
    if not isinstance(body, dict):
        return None, None, None
    detail = body.get("detail")
    if isinstance(detail, dict):
        body = detail
        detail = body.get("detail")
    error = body.get("error")
    hint = body.get("hint")
    return (
        error if isinstance(error, str) else None,
        detail if isinstance(detail, str) else None,
        hint if isinstance(hint, str) else None,
    )


def decode_answer(name: str, method: str, path: str, response: httpx.Response) -> Any:
    """
    Turn a server's answer into JSON, or the failure it is.

    404 and 405 on the fixed paths a view asks mean the server does not offer
    the endpoint (a Noust older than the view), never a missing record.

    Args:
        name: The server, for the message.
        method: The method asked.
        path: The path asked.
        response: The answer.

    Returns:
        The decoded body.

    Raises:
        Failure: ``unsupported``, ``forbidden`` or ``error``.
    """
    status = response.status_code
    if status in (404, 405):
        raise Failure(
            "unsupported",
            f"{name} does not offer {method} {path}",
            hint="It runs an older Noust. Update it to see this here.",
            output=_body_text(response),
            code="not_offered",
        )
    if status == 403:
        text = _body_text(response)
        code, detail, hint = _error_fields(text)
        raise Failure(
            "forbidden",
            detail or f"{name} refused {method} {path} to this operator",
            hint=hint,
            output=text,
            code=code or "permission_denied",
        )
    if status >= 400:
        text = _body_text(response)
        code, detail, hint = _error_fields(text)
        raise Failure(
            "error",
            f"{name} answered {status} to {method} {path}" + (f": {detail}" if detail else ""),
            hint=hint,
            output=text,
            code=code or "node_error",
        )
    try:
        return response.json()
    except ValueError as exc:
        raise Failure(
            "error",
            f"{name} answered {method} {path} with something that is not JSON",
            output=_body_text(response),
            code="node_error",
        ) from exc


class NodeSource:
    """
    A node, asked through its tunnel with the fleet token.

    Args:
        manager: The node registry.
        record: The node.
        asker: Who asks.
        timeout: Seconds per request.
    """

    def __init__(
        self, manager: NodeManager, record: NodeRecord, asker: Asker, timeout: float
    ) -> None:
        self.manager = manager
        self.record = record
        self.asker = asker
        self.timeout = timeout

    @property
    def name(self) -> str:
        """The node's name."""
        return self.record.name

    @property
    def local(self) -> bool:
        """A node is never the central itself."""
        return False

    @property
    def cache_key(self) -> str:
        """The node and the audience."""
        return f"node:{self.record.name}:{self.asker.audience}"

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """
        Send one request to the node, as the asker.

        Args:
            method: HTTP method.
            path: Path on the node.
            params: Query parameters.
            json_body: Body, sent as JSON.
            timeout: Seconds, instead of the source's.

        Returns:
            The node's answer, whatever its status except 401 and 403.

        Raises:
            Failure: ``unreachable``, ``refused`` or ``forbidden``.
            SealError: This central is locked; no node can be reached.
        """
        client = self.manager.client(self.record.name, timeout=timeout or self.timeout)
        try:
            return client.request(
                method,
                path,
                params=dict(params) if params else None,
                json=json_body,
                **self.asker.headers(),
            )
        except NodeRefusedError as exc:
            if exc.status_code == 403:
                code, detail, hint = _error_fields(exc.output)
                raise Failure(
                    "forbidden",
                    detail or f"{self.name} refused {method} {path} to this operator",
                    hint=hint,
                    output=exc.output,
                    code=code or "permission_denied",
                ) from exc
            raise Failure(
                "refused",
                exc.message,
                hint=exc.details,
                output=exc.output or exc.details,
                code="node_refused",
            ) from exc
        except NodeUnreachableError as exc:
            raise Failure(
                "unreachable",
                exc.message,
                hint=f"Check it with 'noust node test {self.name}'.",
                output=exc.details or exc.output,
                code="node_unreachable",
            ) from exc
        except NodeError as exc:
            raise Failure(
                "error", exc.message, hint=exc.details, output=exc.output, code="node_error"
            ) from exc

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        """
        GET one path of the node's API.

        Args:
            path: Such as ``/api/apps``.
            params: Query parameters.

        Returns:
            The decoded JSON.

        Raises:
            Failure: The node did not answer it.
            SealError: This central is locked.
        """
        return decode_answer(self.name, "GET", path, self.request("GET", path, params=params))


# ---------------------------------------------------------------- resources


@dataclass(frozen=True)
class Part:
    """
    One endpoint a view asks each server.

    Attributes:
        key: What its answer is called in the builder.
        path: The server's path.
        params: Query parameters, from the view's own parameters.
        required: Without it the view has nothing to show for the server. An
            optional part that fails leaves a warning; one the server does not
            offer makes the outcome ``unsupported``, the rest still shown.
        fallback_for: Only asked when the server does not offer this other
            part (an older node's way to the same figure).
        title: What it is, for a warning when it cannot be read.
    """

    key: str
    path: str
    params: Callable[[Mapping[str, Any]], dict[str, Any]] | None = None
    required: bool = True
    fallback_for: str | None = None
    title: str = ""


@dataclass
class NodeView:
    """
    What a builder knows about one server.

    Attributes:
        name: The server's name.
        local: It is this central.
        parts: Each part's answer that was read.
        record: The node's registry row; None for the central itself.
        labels: The node's labels.
        outcome: How it answered.
        latency_ms: How long its first answer took.
        expected: The outage the central expects of it (a reboot it was
            asked for), as :meth:`~noust.fleet.expected.ExpectedOutage.to_dict`.
    """

    name: str
    local: bool
    parts: dict[str, Any]
    record: NodeRecord | None
    labels: dict[str, str]
    outcome: NodeOutcome
    latency_ms: float | None = None
    expected: dict[str, Any] | None = None


@dataclass(frozen=True)
class Resource:
    """
    One fleet view.

    Attributes:
        name: Its name, the last segment of ``/api/fleet/<name>``.
        parts: What each server is asked, in order; the first is the probe.
        ttl: Seconds an answer is reused without asking again.
        max_stale: Seconds a server's last good answer is shown when it fails.
        build: Rows for one server.
        per_node: One row per server whatever it answered (summary, servers):
            the builder is called for a server that failed too.
        sort: Orders the rows of every server together.
        params: Checks and completes the view's parameters.
    """

    name: str
    parts: tuple[Part, ...]
    ttl: float
    max_stale: float
    build: Callable[[NodeView], builtins.list[dict[str, Any]]]
    per_node: bool = False
    sort: Callable[[builtins.list[dict[str, Any]]], builtins.list[dict[str, Any]]] | None = None
    params: Callable[[Mapping[str, Any]], dict[str, Any]] = lambda params: {}


def href(name: str, local: bool, page: str) -> str:
    """
    Where a server's page is in this central's console.

    Args:
        name: The server.
        local: It is this central.
        page: The page on the server's own console, starting with ``/``.

    Returns:
        ``page`` for the central itself; ``/n/<server><page>`` for a node, and
        ``/n/<server>`` for its home page.
    """
    if local:
        return page
    base = f"/n/{quote(name, safe='')}"
    return base if page == "/" else base + page


def _row(view: NodeView, page: str, fields: Mapping[str, Any]) -> dict[str, Any]:
    """
    Label one row with its server and its page.

    Args:
        view: The server.
        page: The row's page on that server's console.
        fields: The row itself; its own keys win nothing over the labels.

    Returns:
        ``fields`` with ``node``, ``local``, ``page`` and ``href``.
    """
    return {
        **fields,
        "node": view.name,
        "local": view.local,
        "page": page,
        "href": href(view.name, view.local, page),
    }


def _counts(
    value: Any, keys: tuple[str, ...], optional: tuple[str, ...] = ()
) -> dict[str, int] | None:
    """
    Read a block of counters from a server's answer.

    Args:
        value: The block, such as ``{"running": 3, "failed": 0, ...}``.
        keys: The counters to keep.
        optional: Counters a server of an older version does not send,
            read as 0 when absent.

    Returns:
        The counters, or None when the block is not what a server sends.
    """
    if not isinstance(value, dict):
        return None
    counts: dict[str, int] = {}
    for key in (*keys, *optional):
        number = value.get(key, 0 if key in optional else None)
        if not isinstance(number, int) or isinstance(number, bool):
            return None
        counts[key] = number
    return counts


#: The application counters every server sends, and the one 3.3 added.
_APP_COUNTS = ("running", "failed", "stopped", "static")
_APP_COUNTS_SINCE_3_3 = ("unmanaged",)


def _dict(value: Any) -> dict[str, Any] | None:
    """Returns: ``value`` when it is a JSON object, else None."""
    return value if isinstance(value, dict) else None


def _list(value: Any, key: str) -> builtins.list[dict[str, Any]]:
    """
    Read a list of objects out of a server's answer.

    Args:
        value: The answer, such as ``{"apps": [...]}``.
        key: The list's key.

    Returns:
        The objects; an answer of another shape reads as none.
    """
    items = value.get(key) if isinstance(value, dict) else None
    return (
        [item for item in items or [] if isinstance(item, dict)] if isinstance(items, list) else []
    )


def _access(record: NodeRecord | None) -> dict[str, Any] | None:
    """
    The ceiling a node last published, as the rows show it.

    Args:
        record: The node; None for the central itself.

    Returns:
        ``level`` and ``host_access``, or None when unknown.
    """
    if record is None or record.access_level is None:
        return None
    return {"level": record.access_level, "host_access": bool(record.host_access)}


def _noust(version: Any) -> dict[str, Any] | None:
    """
    What a server says about its own Noust (``GET /api/system/version``).

    Args:
        version: Its answer.

    Returns:
        The installed and available versions and how to update, or None.
    """
    body = _dict(version)
    if body is None:
        return None
    return {
        key: body.get(key)
        for key in (
            "current_version",
            "latest_version",
            "published_version",
            "update_state",
            "update_command",
            "status",
        )
    }


def _server_row(view: NodeView) -> dict[str, Any]:
    """
    The fields every per-server row shares.

    Args:
        view: The server.

    Returns:
        Reachability, version, latency, access, labels, the node's SSH address
        and last contact when it is a node, and the outage it is expected to
        have. A node that is down while it reboots on request is ``rebooting``,
        not ``unreachable``.
    """
    record = view.record
    version_body = _dict(view.parts.get("version"))
    version = version_body.get("current_version") if version_body else None
    if not isinstance(version, str):
        version = view.outcome.version
    reachability = "reachable" if view.local else (record.status if record else "unknown")
    if view.expected is not None and reachability == "unreachable":
        reachability = "rebooting"
    return {
        "name": view.name,
        "reachability": reachability,
        "expected_outage": view.expected,
        "version": version,
        "latency_ms": view.latency_ms,
        "last_seen": None if record is None else record.last_seen,
        "ssh": None if record is None else f"{record.ssh_user}@{record.ssh_host}:{record.ssh_port}",
        "access": _access(record),
        "labels": dict(view.labels),
        "noust": _noust(version_body),
    }


def _certificates_expiring(view: NodeView) -> int | None:
    """
    Count a server's certificates that expire soon, from what it said.

    Args:
        view: The server.

    Returns:
        Its overview's own count when it has one, else the count from its
        certificate list, else None.
    """
    overview = _dict(view.parts.get("overview"))
    figure = _dict(overview.get("certificates")) if overview else None
    expiring = figure.get("expiring") if figure is not None else None
    if isinstance(expiring, int):
        expired = figure.get("expired") if figure is not None else None
        return expiring + (expired if isinstance(expired, int) else 0)
    certs = _list(view.parts.get("certs"), "certificates")
    if view.parts.get("certs") is None:
        return None
    return sum(
        1
        for cert in certs
        if isinstance(cert.get("days_remaining"), int)
        and cert["days_remaining"] < CERT_EXPIRING_DAYS
    )


def _build_summary(view: NodeView) -> builtins.list[dict[str, Any]]:
    """
    One row per server: how it is, from its own overview and machine snapshot.

    Args:
        view: The server.

    Returns:
        The row.
    """
    machine = _dict(view.parts.get("machine"))
    overview = _dict(view.parts.get("overview"))
    apps = _counts(machine.get("apps"), _APP_COUNTS, _APP_COUNTS_SINCE_3_3) if machine else None
    if apps is None and overview is not None:
        apps = _counts(overview.get("apps"), _APP_COUNTS, _APP_COUNTS_SINCE_3_3)
    return [
        _row(
            view,
            "/",
            {
                **_server_row(view),
                "apps": apps,
                "units": _counts(machine.get("units"), ("running", "failed", "stopped"))
                if machine
                else None,
                "certificates_expiring": _certificates_expiring(view),
                "machine": machine,
                "overview": overview,
                "server": _dict(view.parts.get("server")),
            },
        )
    ]


def _build_servers(view: NodeView) -> builtins.list[dict[str, Any]]:
    """
    One row per server: who it is and whether it answers.

    Args:
        view: The server.

    Returns:
        The row.
    """
    return [_row(view, "/", _server_row(view))]


def _build_apps(view: NodeView) -> builtins.list[dict[str, Any]]:
    """
    One row per application, as its server lists it.

    Args:
        view: The server.

    Returns:
        The rows, with the application's page on its server.
    """
    rows = []
    for app in _list(view.parts.get("apps"), "apps"):
        domain = str(app.get("domain") or app.get("name") or "")
        rows.append(_row(view, f"/apps/{quote(domain, safe='')}" if domain else "/apps", app))
    return rows


def _build_certificates(view: NodeView) -> builtins.list[dict[str, Any]]:
    """
    One row per certificate, as its server's certbot reports it.

    Args:
        view: The server.

    Returns:
        The rows.
    """
    return [_row(view, "/domains", cert) for cert in _list(view.parts.get("certs"), "certificates")]


def _sort_certificates(rows: builtins.list[dict[str, Any]]) -> builtins.list[dict[str, Any]]:
    """Returns: The certificates, the soonest to expire first; unknown expiry last."""
    return sorted(
        rows,
        key=lambda row: (
            not isinstance(row.get("days_remaining"), int),
            row.get("days_remaining") if isinstance(row.get("days_remaining"), int) else 0,
            str(row.get("node")),
            str(row.get("domain")),
        ),
    )


def _build_backups(view: NodeView) -> builtins.list[dict[str, Any]]:
    """
    One row per application: its newest backup, whether it was verified, its schedule.

    A fleet view of backups is about gaps (an application without one, with an
    old one, without a schedule), so the row is the application, not the
    archive; the archives are on its server's own page.

    Args:
        view: The server.

    Returns:
        The rows.
    """
    backups = _list(view.parts.get("backups"), "backups")
    schedules = {
        str(schedule.get("domain")): schedule
        for schedule in _list(view.parts.get("schedules"), "schedules")
    }
    by_domain: dict[str, builtins.list[dict[str, Any]]] = {}
    for backup in backups:
        by_domain.setdefault(str(backup.get("domain")), []).append(backup)
    domains = [str(app.get("domain")) for app in _list(view.parts.get("apps"), "apps")]
    rows = []
    for domain in sorted(set(domains)):
        archives = sorted(
            by_domain.get(domain, []), key=lambda b: str(b.get("timestamp") or ""), reverse=True
        )
        newest = archives[0] if archives else None
        verified = None if newest is None else newest.get("verified_ok")
        rows.append(
            _row(
                view,
                f"/backups?domain={quote(domain, safe='')}",
                {
                    "domain": domain,
                    "backups": len(archives),
                    "size": sum(
                        b.get("size") or 0 for b in archives if isinstance(b.get("size"), int)
                    ),
                    "last_backup": newest,
                    "verified": "never" if verified is None else ("ok" if verified else "failed"),
                    "last_verified_at": None if newest is None else newest.get("last_verified_at"),
                    "schedule": schedules.get(domain),
                    "scheduled": domain in schedules,
                },
            )
        )
    return rows


def _sort_backups(rows: builtins.list[dict[str, Any]]) -> builtins.list[dict[str, Any]]:
    """Returns: Applications without a backup first, then the oldest backups."""
    return sorted(
        rows,
        key=lambda row: (
            row.get("last_backup") is not None,
            str((row.get("last_backup") or {}).get("timestamp") or ""),
            str(row.get("node")),
            str(row.get("domain")),
        ),
    )


def _build_updates(view: NodeView) -> builtins.list[dict[str, Any]]:
    """
    One row per server: its Noust and its operating system's pending updates.

    Args:
        view: The server.

    Returns:
        The row.
    """
    update = _dict(view.parts.get("update"))
    server = _dict(view.parts.get("server"))
    noust = _noust(view.parts.get("version")) or {}
    if update is not None:
        noust = {**noust, "method": update.get("method"), "last_run": update.get("last_run")}
    return [
        _row(
            view,
            "/server/updates",
            {
                **_server_row(view),
                "noust": noust or None,
                "os": None
                if server is None
                else {
                    "updates": server.get("updates"),
                    "reboot": server.get("reboot"),
                    "auto_updates": server.get("auto_updates"),
                    "os": server.get("os"),
                },
            },
        )
    ]


def _build_activity(view: NodeView) -> builtins.list[dict[str, Any]]:
    """
    One row per audit event, as its server recorded it.

    Args:
        view: The server.

    Returns:
        The rows.
    """
    return [_row(view, "/activity", entry) for entry in _list(view.parts.get("audit"), "items")]


def _sort_activity(rows: builtins.list[dict[str, Any]]) -> builtins.list[dict[str, Any]]:
    """Returns: The events, newest first, across every server."""
    return sorted(rows, key=lambda row: str(row.get("timestamp") or ""), reverse=True)


def _activity_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """
    Check the activity view's parameters.

    Args:
        params: ``limit`` (per server, 1 to 200, 50 by default).

    Returns:
        The checked parameters.
    """
    raw = params.get("limit", 50)
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        limit = 50
    return {"limit": max(1, min(limit, 200))}


def _by_node_then(
    key: str,
) -> Callable[[builtins.list[dict[str, Any]]], builtins.list[dict[str, Any]]]:
    """
    Order rows by server, then by one of their fields.

    Args:
        key: The field.

    Returns:
        The sort function.
    """
    return lambda rows: sorted(rows, key=lambda row: (str(row.get("node")), str(row.get(key))))


#: Every fleet view, by name.
RESOURCES: dict[str, Resource] = {
    resource.name: resource
    for resource in (
        Resource(
            "summary",
            (
                Part("version", VERSION_PATH),
                Part("machine", MACHINE_PATH, required=False, title="Machine snapshot"),
                Part("overview", "/api/overview", required=False, title="Overview"),
                Part(
                    "certs",
                    CERTS_PATH,
                    required=False,
                    fallback_for="overview",
                    title="Certificates",
                ),
                Part("server", "/api/server/summary", required=False, title="Server summary"),
            ),
            ttl=10.0,
            max_stale=3600.0,
            build=_build_summary,
            per_node=True,
        ),
        Resource(
            "servers",
            (Part("version", VERSION_PATH),),
            ttl=10.0,
            max_stale=3600.0,
            build=_build_servers,
            per_node=True,
        ),
        Resource(
            "apps",
            (Part("apps", "/api/apps"),),
            ttl=15.0,
            max_stale=3600.0,
            build=_build_apps,
            sort=_by_node_then("domain"),
        ),
        Resource(
            "certificates",
            (Part("certs", CERTS_PATH),),
            ttl=300.0,
            max_stale=86400.0,
            build=_build_certificates,
            sort=_sort_certificates,
        ),
        Resource(
            "backups",
            (
                Part("apps", "/api/apps"),
                Part("backups", "/api/backups", params=lambda _: {"limit": 1000}),
                Part(
                    "schedules", "/api/backup-schedules", required=False, title="Backup schedules"
                ),
            ),
            ttl=60.0,
            max_stale=3600.0,
            build=_build_backups,
            sort=_sort_backups,
        ),
        Resource(
            "updates",
            (
                Part("version", VERSION_PATH),
                Part("update", "/api/system/update", required=False, title="Noust update"),
                Part("server", "/api/server/summary", required=False, title="Server summary"),
            ),
            ttl=300.0,
            max_stale=86400.0,
            build=_build_updates,
            per_node=True,
        ),
        Resource(
            "activity",
            (Part("audit", "/api/audit", params=lambda p: {"limit": p["limit"]}),),
            ttl=15.0,
            max_stale=3600.0,
            build=_build_activity,
            sort=_sort_activity,
            params=_activity_params,
        ),
    )
}


# -------------------------------------------------------------- the engine


@dataclass
class Fetched:
    """
    What asking one server for one view gave.

    Attributes:
        parts: Each part's answer that was read.
        failure: Why a required part could not be read, if one could not.
        missing: The paths the server does not offer.
        warnings: One sentence per optional part that failed.
        optional_failure: The first of those failures, for its words.
        version: The version the server reported, when a part said it.
        latency_ms: How long its first answer took.
        fetched_at: When, ISO 8601 UTC.
        fetched_mono: When, on the monotonic clock.
    """

    parts: dict[str, Any] = field(default_factory=dict)
    failure: Failure | None = None
    missing: builtins.list[str] = field(default_factory=list)
    warnings: builtins.list[str] = field(default_factory=list)
    optional_failure: Failure | None = None
    version: str | None = None
    latency_ms: float | None = None
    fetched_at: str = ""
    fetched_mono: float = 0.0


def _params_key(params: Mapping[str, Any]) -> str:
    """Returns: The view's parameters, as a stable cache key."""
    return json.dumps(dict(params), sort_keys=True, default=str)


class Aggregator:
    """
    Asks every server, caches their answers, and remembers what was learnt.

    One per process (:func:`get_aggregator`), so its cache and its in-flight
    requests are shared by every console and every view.

    Args:
        clock: Monotonic clock (tests move it).
        max_workers: Servers asked at once.
    """

    def __init__(
        self, *, clock: Callable[[], float] = time.monotonic, max_workers: int = MAX_WORKERS
    ) -> None:
        self._clock = clock
        self._max_workers = max_workers
        self._executor: ThreadPoolExecutor | None = None
        self._lock = threading.Lock()
        self._cache: dict[tuple[str, str, str], Fetched] = {}
        self._inflight: dict[tuple[str, str, str], Future[Fetched]] = {}
        # Node name to (monotonic time it was first seen down, notified yet).
        self._outages: dict[str, tuple[float, bool, str | None]] = {}

    def _pool(self) -> ThreadPoolExecutor:
        """Returns: The shared pool, created on first use."""
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=self._max_workers, thread_name_prefix="noust-fleet"
                )
            return self._executor

    def forget(self, name: str | None = None) -> None:
        """
        Drop cached answers: one server's, or every one.

        Args:
            name: The server, or None for all of them.
        """
        with self._lock:
            if name is None:
                self._cache.clear()
                return
            for key in [key for key in self._cache if key[0].split(":")[1] == name]:
                del self._cache[key]

    # ------------------------------------------------------------ fetching

    def _fetch(self, source: Source, resource: Resource, params: Mapping[str, Any]) -> Fetched:
        """
        Ask one server every part of a view. Runs on the pool.

        Args:
            source: The server.
            resource: The view.
            params: The view's checked parameters.

        Returns:
            What it answered.

        Raises:
            SealError: This central is locked.
        """
        fetched = Fetched()
        started = self._clock()
        for part in resource.parts:
            if part.fallback_for is not None and part.fallback_for not in {
                missing.partition(" ")[0] for missing in fetched.missing
            }:
                continue
            try:
                answer = source.get(part.path, part.params(params) if part.params else None)
            except Failure as failure:
                if fetched.latency_ms is None and failure.kind != "unsupported":
                    fetched.latency_ms = round((self._clock() - started) * 1000, 1)
                if part.required:
                    fetched.failure = failure
                    if failure.kind == "unsupported":
                        fetched.missing.append(f"{part.key} {part.path}")
                    break
                if failure.kind == "unsupported":
                    fetched.missing.append(f"{part.key} {part.path}")
                    continue
                fetched.warnings.append(f"{part.title or part.path}: {failure.message}")
                fetched.optional_failure = fetched.optional_failure or failure
                if failure.kind in ("unreachable", "refused"):
                    break
                continue
            if fetched.latency_ms is None:
                fetched.latency_ms = round((self._clock() - started) * 1000, 1)
            fetched.parts[part.key] = answer
            if part.path == VERSION_PATH and isinstance(answer, dict):
                version = answer.get("current_version")
                fetched.version = version if isinstance(version, str) else None
        fetched.fetched_at = _now_iso()
        fetched.fetched_mono = self._clock()
        if isinstance(source, NodeSource):
            self._record_reachability(source, fetched)
        return fetched

    def _run(
        self,
        key: tuple[str, str, str],
        source: Source,
        resource: Resource,
        params: Mapping[str, Any],
    ) -> Fetched:
        """
        Fetch, keep a good answer, and let the next asker in. Runs on the pool.

        Args:
            key: The cache key.
            source: The server.
            resource: The view.
            params: Its parameters.

        Returns:
            What the server answered.
        """
        try:
            fetched = self._fetch(source, resource, params)
            if fetched.failure is None:
                with self._lock:
                    self._cache[key] = fetched
            return fetched
        finally:
            with self._lock:
                self._inflight.pop(key, None)

    def _submit(
        self,
        pool: ThreadPoolExecutor,
        key: tuple[str, str, str],
        source: Source,
        resource: Resource,
        params: Mapping[str, Any],
    ) -> Future[Fetched]:
        """
        Ask a server, or join the request already asking it the same thing.

        The caller holds ``self._lock``, so that its look at the cache and this
        one at the requests in flight are one.

        Args:
            pool: Where requests run (taken before the lock: it takes it too).
            key: The cache key.
            source: The server.
            resource: The view.
            params: Its parameters.

        Returns:
            The request's future.
        """
        running = self._inflight.get(key)
        if running is not None:
            return running
        future = pool.submit(self._run, key, source, resource, params)
        self._inflight[key] = future
        return future

    # --------------------------------------------------------- reachability

    def _record_reachability(self, source: NodeSource, fetched: Fetched) -> None:
        """
        Write down what this read learnt about a node, and tell the operator of an outage.

        The registry's status is what the server selector shows; a read of any
        view is as good a probe as ``noust node test``. A refusal is recorded
        by the client itself, at the point of failure.

        Args:
            source: The node.
            fetched: What it answered.
        """
        from noust.fleet import expected

        name = source.name
        failure = fetched.failure or fetched.optional_failure
        down = failure is not None and failure.kind == "unreachable" and not fetched.parts
        store = source.manager.store
        record = store.get_node(name)
        if record is None:
            return
        if down:
            if record.status != "unreachable":
                store.set_node_status(name, "unreachable")
            if expected.current(name, store=store) is not None:
                # Asked to reboot through this central: its silence is not an
                # outage until the expectation runs out.
                with self._lock:
                    self._outages.pop(name, None)
                return
            self._outage_started(source, record, failure)
            return
        if not fetched.parts:
            return
        expected.answered(name, store=store)
        stale_seen = True
        if record.last_seen:
            try:
                seen = datetime.fromisoformat(record.last_seen)
                if seen.tzinfo is None:
                    seen = seen.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - seen).total_seconds()
                stale_seen = age >= LAST_SEEN_EVERY_SECONDS
            except ValueError:
                stale_seen = True
        changed = record.status != "reachable" or (
            fetched.version is not None and fetched.version != record.version
        )
        if changed or stale_seen:
            store.set_node_status(name, "reachable", version=fetched.version)
        self._outage_ended(name)

    def _outage_started(
        self, source: NodeSource, record: NodeRecord, failure: Failure | None
    ) -> None:
        """
        Count an unreachable read towards an outage, and announce it once it lasts.

        Args:
            source: The node.
            record: Its registry row.
            failure: Why it could not be reached.
        """
        now = self._clock()
        with self._lock:
            since, notified, last_seen = self._outages.get(
                record.name, (now, False, record.last_seen)
            )
            self._outages[record.name] = (since, notified, last_seen)
            due = not notified and now - since >= UNREACHABLE_GRACE_SECONDS
            if due:
                self._outages[record.name] = (since, True, last_seen)
        if not due:
            return
        from noust.core.notifications.fleet import notify_node_unreachable

        seen: datetime | None = None
        if last_seen:
            try:
                seen = datetime.fromisoformat(last_seen)
            except ValueError:
                seen = None
        notify_node_unreachable(
            record.name,
            reason=(failure.output or failure.message) if failure else None,
            address=f"{record.ssh_user}@{record.ssh_host}:{record.ssh_port}",
            since=seen,
        )

    def _outage_ended(self, name: str) -> None:
        """
        Close a node's outage, announcing the recovery if the outage was announced.

        Args:
            name: The node.
        """
        with self._lock:
            outage = self._outages.pop(name, None)
        if outage is None or not outage[1]:
            return
        from noust.core.notifications.fleet import notify_node_recovered

        notify_node_recovered(name, down_for_s=max(0.0, self._clock() - outage[0]))

    # ------------------------------------------------------------ gathering

    def gather(
        self,
        resource_name: str,
        params: Mapping[str, Any] | None,
        sources: Sequence[Source],
        *,
        refresh: bool = False,
        deadline: float | None = DEADLINE,
        labels: Mapping[str, Mapping[str, str]] | None = None,
    ) -> FleetResult:
        """
        Ask every server for one view, and put the answers together.

        Args:
            resource_name: The view, a key of :data:`RESOURCES`.
            params: Its parameters.
            sources: The servers, in the order to show them.
            refresh: Ask even a server whose answer is fresh in the cache.
            deadline: Seconds to wait before answering with what arrived; None
                waits for every server (the CLI).
            labels: Each node's labels.

        Returns:
            The view.

        Raises:
            KeyError: For a view that does not exist.
            SealError: This central is locked; no node can be reached.
        """
        resource = RESOURCES[resource_name]
        checked = resource.params(params or {})
        params_key = _params_key(checked)
        now = self._clock()
        fetched: dict[int, Fetched] = {}
        futures: dict[int, Future[Fetched]] = {}
        keys: dict[int, tuple[str, str, str]] = {}
        pool = self._pool()
        for index, source in enumerate(sources):
            key = (source.cache_key, resource.name, params_key)
            keys[index] = key
            # One look at the cache and the requests in flight, under one lock:
            # a request that finished between two separate looks had left the
            # cache warm and itself gone, and a second request went out.
            with self._lock:
                cached = self._cache.get(key)
                if cached is not None and not refresh and now - cached.fetched_mono < resource.ttl:
                    fetched[index] = cached
                else:
                    futures[index] = self._submit(pool, key, source, resource, checked)
        if futures:
            wait_futures(list(futures.values()), timeout=deadline)
        for index, future in futures.items():
            if future.done():
                fetched[index] = future.result()
            else:
                fetched[index] = Fetched(
                    failure=Failure(
                        "timeout",
                        f"{sources[index].name} did not answer within {deadline:.0f}s",
                        hint="Its answer keeps coming in the background: look again shortly.",
                        code="timeout",
                    )
                )

        outcomes: list[NodeOutcome] = []
        items: list[dict[str, Any]] = []
        for index, source in enumerate(sources):
            outcome, shown = self._outcome(source, resource, fetched[index], keys[index])
            outcomes.append(outcome)
            expectation = (
                expected_outage(source.manager.store, source.name)
                if isinstance(source, NodeSource)
                else None
            )
            view = NodeView(
                name=source.name,
                local=source.local,
                parts=shown.parts if shown else {},
                # Read again: this very read may just have changed the status.
                record=(source.manager.store.get_node(source.name) or source.record)
                if isinstance(source, NodeSource)
                else None,
                labels=dict((labels or {}).get(source.name, {})) if not source.local else {},
                outcome=outcome,
                latency_ms=shown.latency_ms if shown else None,
                expected=expectation,
            )
            if shown is None and not resource.per_node:
                continue
            try:
                items.extend(resource.build(view))
            except (TypeError, ValueError, KeyError, AttributeError) as exc:
                # A node's answer of a shape this central does not expect: one
                # server's rows are missing, never the whole view.
                logger.warning(
                    "Fleet view %s: rows from %s could not be read: %s",
                    resource.name,
                    source.name,
                    exc,
                )
                outcomes[-1] = NodeOutcome(
                    name=source.name,
                    local=source.local,
                    status="error",
                    code="node_error",
                    message=f"{source.name}'s answer could not be read by this central",
                    error_verbatim=str(exc),
                    version=outcome.version,
                )
        if resource.sort is not None:
            items = resource.sort(items)
        return FleetResult(
            resource=resource.name, generated_at=_now_iso(), nodes=outcomes, items=items
        )

    def _outcome(
        self,
        source: Source,
        resource: Resource,
        fetched: Fetched,
        key: tuple[str, str, str],
    ) -> tuple[NodeOutcome, Fetched | None]:
        """
        Decide how a server answered, and what is shown for it.

        Args:
            source: The server.
            resource: The view.
            fetched: What it answered (possibly from the cache).
            key: Its cache key, for its last good answer.

        Returns:
            The outcome, and the answer whose rows are shown (None when none is).
        """
        now = self._clock()
        record = source.record if isinstance(source, NodeSource) else None
        version = fetched.version or (record.version if record else None)
        missing = tuple(entry.partition(" ")[2] for entry in fetched.missing)
        failure = fetched.failure
        if failure is None:
            status = "ok"
            code = message = hint = verbatim = None
            # A part that failed says more than a part that is not offered: the
            # first is something to look at, the second only a version.
            if fetched.optional_failure is not None:
                status = "error"
                code = fetched.optional_failure.code
                message = fetched.optional_failure.message
                hint = fetched.optional_failure.hint
                verbatim = fetched.optional_failure.output
            elif fetched.missing:
                status = "unsupported"
                code = "not_offered"
                message = f"{source.name} does not offer {', '.join(missing)}"
                hint = "It runs an older Noust. Update it to see everything here."
            return (
                NodeOutcome(
                    name=source.name,
                    local=source.local,
                    status=status,
                    age_seconds=round(max(0.0, now - fetched.fetched_mono), 1),
                    version=version,
                    code=code,
                    message=message,
                    hint=hint,
                    error_verbatim=verbatim,
                    elapsed_ms=fetched.latency_ms,
                    fetched_at=fetched.fetched_at,
                    missing=missing,
                    warnings=tuple(fetched.warnings),
                ),
                fetched,
            )

        status = {
            "unreachable": "unreachable",
            "timeout": "unreachable",
            "refused": "error",
            "forbidden": "forbidden",
            "unsupported": "unsupported",
        }.get(failure.kind, "error")
        shown: Fetched | None = None
        if failure.kind in ("unreachable", "timeout", "refused", "error"):
            with self._lock:
                last = self._cache.get(key)
            if last is not None and now - last.fetched_mono <= resource.max_stale:
                shown = last
        return (
            NodeOutcome(
                name=source.name,
                local=source.local,
                status="stale" if shown else status,
                age_seconds=round(now - shown.fetched_mono, 1) if shown else None,
                error_verbatim=failure.output,
                version=version,
                code=failure.code,
                message=failure.message,
                hint=failure.hint,
                elapsed_ms=fetched.latency_ms,
                fetched_at=shown.fetched_at if shown else None,
                missing=tuple(e.partition(" ")[2] for e in shown.missing) if shown else missing,
            ),
            shown,
        )


def expected_outage(store: Any, name: str) -> dict[str, Any] | None:
    """
    Read the outage a node is expected to have, for its rows.

    Args:
        store: The central's store.
        name: The node.

    Returns:
        The expectation as plain data, or None (also when the store cannot
        say: a view never fails for it).
    """
    import sqlite3

    from noust.fleet import expected

    try:
        outage = expected.current(name, store=store)
    except sqlite3.Error as exc:
        logger.warning("The expected outage of %s could not be read: %s", name, exc)
        return None
    return outage.to_dict() if outage is not None else None


_aggregator: Aggregator | None = None
_aggregator_lock = threading.Lock()


def get_aggregator() -> Aggregator:
    """
    The process-wide aggregator.

    Returns:
        It, created on first use.
    """
    global _aggregator
    with _aggregator_lock:
        if _aggregator is None:
            _aggregator = Aggregator()
        return _aggregator


def set_aggregator(aggregator: Aggregator | None) -> None:
    """
    Replace the process-wide aggregator (tests; None builds a new one on next use).

    Args:
        aggregator: The aggregator.
    """
    global _aggregator
    with _aggregator_lock:
        _aggregator = aggregator


def cli_asker() -> Asker:
    """
    Who asks when the central's own terminal does.

    Returns:
        The operator at this terminal, with the central's full authority: the
        CLI already runs as root here, and the node still applies its ceiling.
    """
    from noust.fleet.nodes import cli_actor

    return Asker(actor=cli_actor(), scope="admin")


def includes_central() -> bool:
    """
    Report whether this central is one of its own servers in fleet views.

    Returns:
        True when its role is ``server``: it runs applications of its own. A
        ``hub`` runs none, so it has no rows of its own to show.
    """
    from noust.central import ROLE_SERVER, role

    return role() == ROLE_SERVER


def selected(names: Sequence[str] | None, name: str, *, local: bool = False) -> bool:
    """
    Report whether a node filter selects a server.

    Args:
        names: The filter; None or empty selects every server.
        name: The server's name.
        local: It is this central, also selected by :data:`LOCAL_SELECTOR`.

    Returns:
        True when the server is to be asked.
    """
    if not names:
        return True
    return name in names or (local and LOCAL_SELECTOR in names)


def gather(
    resource: str,
    params: Mapping[str, Any] | None = None,
    *,
    asker: Asker | None = None,
    nodes: Sequence[str] | None = None,
    local: Source | None = None,
    manager: NodeManager | None = None,
    refresh: bool = False,
    node_timeout: float = NODE_TIMEOUT,
    deadline: float | None = DEADLINE,
) -> FleetResult:
    """
    Ask every server this central manages for one view.

    Args:
        resource: The view: ``summary``, ``servers``, ``apps``,
            ``certificates``, ``backups``, ``updates`` or ``activity``.
        params: The view's parameters (``limit`` for ``activity``).
        asker: Who asks; the terminal's operator (:func:`cli_asker`) when None.
        nodes: Only these servers; :data:`LOCAL_SELECTOR` names this central.
        local: This central as a source of its own rows; asked only when its
            role is ``server`` (:func:`includes_central`).
        manager: The node registry; a default one when None.
        refresh: Ask even the servers whose answer is fresh in the cache.
        node_timeout: Seconds each request to a node may take.
        deadline: Seconds to wait for the slowest; None waits for all.

    Returns:
        The view: every server's outcome, and the rows.

    Raises:
        KeyError: For a view that does not exist.
        SealError: This central is locked; no node can be reached.
    """
    from noust.fleet.labels import NodeLabels
    from noust.fleet.nodes import NodeManager

    manager = manager or NodeManager()
    asker = asker or cli_asker()
    sources: list[Source] = []
    if local is not None and includes_central() and selected(nodes, local.name, local=True):
        sources.append(local)
    for record in manager.list():
        if selected(nodes, record.name):
            sources.append(NodeSource(manager, record, asker, node_timeout))
    return get_aggregator().gather(
        resource,
        params,
        sources,
        refresh=refresh,
        deadline=deadline,
        labels=NodeLabels(manager.store).all(),
    )


__all__ = [
    "DEADLINE",
    "LOCAL_SELECTOR",
    "NODE_TIMEOUT",
    "OUTCOME_STATUSES",
    "RESOURCES",
    "Aggregator",
    "Asker",
    "Failure",
    "FleetResult",
    "NodeOutcome",
    "NodeSource",
    "Source",
    "cli_asker",
    "decode_answer",
    "gather",
    "get_aggregator",
    "href",
    "includes_central",
    "selected",
    "set_aggregator",
]
