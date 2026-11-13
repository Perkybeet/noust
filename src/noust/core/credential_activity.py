# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a credential did, by interventions (item 185).

A credential is any token: of the API, of MCP, of a central, of the built-in assistant. When something went
wrong, or when a person wants to know what their tool has been up to, the question is "what did it do
exactly". The audit trail already holds the answer, with the credential's name on every line; this module
reads it back as the person asks it:

- an **intervention** is what a credential did in a row: its events without a silence of more than thirty
  minutes between one and the next. MCP has no sessions to group by, so the silence is the boundary;
- the built-in assistant is one credential per person, whatever number of tokens it used (each of its
  conversations has a token of its own, and a few hundred of them are not something anybody reads): its
  interventions are its conversations, or the stretches without thirty minutes of silence inside one
  (:class:`CredentialRef`);
- for each, the **timeline** of what it did: the action, the resource, how it ended, the deployment it
  started, and with which client (the name and version the client said of itself, labelled as that:
  :mod:`noust.core.credential_clients`).

Nothing here writes: it is a reading of the audit trail (the chained log, :class:`~noust.core.audit.AuditLog`)
and of the jobs and deployments the store keeps. What a team may see of it is decided by the caller through
``visible``, built from the scoped store: an entry that names an application the caller does not see is not
there, and one that names none is the instance's.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from noust.core.audit.actor import Actor

if TYPE_CHECKING:
    from noust.core.audit import AuditLog
    from noust.core.store import NoustStore

#: The silence that ends an intervention.
GAP = timedelta(minutes=30)
DEFAULT_DAYS = 30
MAX_DAYS = 365
#: The most lines of the log one reading goes through, so that a long log is not read whole for a question.
MAX_SCAN = 400_000
#: The most entries one reading keeps.
MAX_ENTRIES = 20_000
#: How far past an entry the job it queued may have been created, and how far before.
_JOB_WINDOW_AFTER = timedelta(seconds=90)
_JOB_WINDOW_BEFORE = timedelta(seconds=5)

#: Results of an entry.
RESULT_OK = "ok"
RESULT_PROPOSED = "proposed"
RESULT_REFUSED = "refused"
RESULT_FAILED = "failed"

KIND_CHANGE = "change"
KIND_TOOL = "tool"
KIND_READ = "read"
KIND_PROPOSAL = "proposal"
KIND_EVENT = "event"

_API_LINE = re.compile(r"api\.(post|put|patch|delete)")
_APP_PATH = re.compile(r"/api/(?:apps|sites)/([^/]+)")
_ERROR_RESULT = re.compile(r"error:(\d{3})")

_DENIED_ACTIONS = ("http.denied.approval", "http.denied.permission", "auth.token.denied")
#: The events a call's own line says more about: kept as the names of what happened, not as entries.
_STATUS_REFUSED = frozenset({401, 403, 404})


@dataclass(frozen=True)
class CredentialRef:
    """
    Which tokens a reading of activity is about.

    A token is its own credential. The built-in assistant is one credential for each person, made of every
    token it used for them: the reading follows all of them and splits an intervention where the token (the
    conversation) changes, besides where thirty minutes of silence do.

    Attributes:
        label: How the credential is called: the token's name, or ``assistant:<person>``.
        names: The names of the tokens it is made of.
        by_conversation: The tokens are conversations: an intervention ends where the token changes.
    """

    label: str
    names: tuple[str, ...]
    by_conversation: bool = False

    @classmethod
    def of(cls, credential: CredentialRef | str | Collection[str]) -> CredentialRef:
        """
        Args:
            credential: A reference, a token's name, or the names of several tokens.

        Returns:
            The reference.
        """
        if isinstance(credential, CredentialRef):
            return credential
        if isinstance(credential, str):
            return cls(credential, (credential,))
        names = tuple(credential)
        return cls(names[0] if names else "", names)


def assistant_tokens(
    record: Mapping[str, Any], records: Iterable[Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    """
    Find the tokens that make up the built-in assistant of the person a token of it works for.

    Args:
        record: A token's record, whose ``caller`` says it is the assistant's.
        records: Token records to look among: those of that person at least.

    Returns:
        Every token of the assistant that acts for the same account, live or revoked: the one credential
        they make together. Each conversation has a token of its own, which nobody reads one by one.
    """
    owner = record.get("owner_account_id")
    if owner is None:
        return [record]
    return [
        item
        for item in records
        if item.get("caller") == "assistant" and item.get("owner_account_id") == owner
    ] or [record]


def reference_of(
    record: Mapping[str, Any], records: Iterable[Mapping[str, Any]], owner_name: str | None = None
) -> CredentialRef:
    """
    Say what a reading of the activity of a token follows.

    Args:
        record: A token's record.
        records: Token records to look among, for the assistant's other tokens.
        owner_name: The username of the account it acts for.

    Returns:
        The token alone; for a token of the built-in assistant, every token it used for that person, an
        intervention being a conversation (``assistant:<person>``).
    """
    if record.get("caller") != "assistant":
        return CredentialRef.of(str(record["name"]))
    tokens = assistant_tokens(record, records)
    return CredentialRef(
        f"assistant:{owner_name or record.get('owner_account_id')}",
        tuple(str(item["name"]) for item in tokens),
        by_conversation=True,
    )


@dataclass
class Entry:
    """
    One thing a credential did: a call of the API, a tool of MCP, or a proposal waiting for a person.

    Attributes:
        at: When, ISO 8601 as the audit trail wrote it.
        credential: The token that did it (what its conversation is called, for the assistant).
        seq: The sequence number of the first event of the entry in the chain.
        corr: What links the events of one request.
        via: ``api``, ``mcp``, ``assistant`` or ``fleet``.
        kind: ``change``, ``tool``, ``read``, ``proposal`` or ``event``.
        method: The HTTP method, for a call of the API.
        path: The route called, with the application's name in it.
        tool: The tool of MCP.
        domain: The application it names, when it names one.
        result: ``ok``, ``proposed`` (a person had to approve it), ``refused`` or ``failed``.
        status: The HTTP status, when the audit line carries it.
        approval: The approval request, when the call waited for a person or ran with one.
        operator: The person a central acted for, for a fleet token.
        events: The names of the other events the same request recorded.
        deployment: The deployment it started, when it started one.
    """

    at: str
    seq: int | None
    corr: str | None
    via: str
    kind: str
    credential: str = ""
    method: str | None = None
    path: str | None = None
    tool: str | None = None
    domain: str | None = None
    result: str = RESULT_OK
    status: int | None = None
    approval: int | None = None
    operator: str | None = None
    events: list[str] = field(default_factory=list)
    deployment: dict[str, Any] | None = None

    @property
    def moment(self) -> datetime:
        """When it happened, as a moment."""
        return parse_moment(self.at) or datetime.min.replace(tzinfo=timezone.utc)

    @property
    def changes(self) -> bool:
        """Whether it changed something, or tried to."""
        return self.kind in (KIND_CHANGE, KIND_TOOL, KIND_PROPOSAL)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The entry as the API shows it.
        """
        return {
            "at": self.at,
            "seq": self.seq,
            "credential": self.credential,
            "via": self.via,
            "kind": self.kind,
            "method": self.method,
            "path": self.path,
            "tool": self.tool,
            "domain": self.domain,
            "result": self.result,
            "status": self.status,
            "approval": self.approval,
            "operator": self.operator,
            "events": list(self.events),
            "deployment": self.deployment,
        }


@dataclass
class Intervention:
    """
    What a credential did in a row.

    Attributes:
        n: Its number: the sequence number of the first event of the first entry, which names it for as
            long as the log keeps it.
        started_at: When the first entry happened.
        ended_at: When the last one did.
        ongoing: Whether the credential did something less than thirty minutes ago, so that it may go on.
        entries: What it did, oldest first.
        conversation: For the assistant, the token of the conversation this was (its name).
    """

    n: int
    started_at: str
    ended_at: str
    ongoing: bool
    entries: list[Entry]
    conversation: str | None = None

    @property
    def apps(self) -> list[str]:
        """The applications it touched, in the order it met them."""
        return list(dict.fromkeys(e.domain for e in self.entries if e.domain and e.changes))

    def count(self, result: str) -> int:
        """
        Args:
            result: One of the results.

        Returns:
            How many entries that changed something ended so.
        """
        return sum(1 for e in self.entries if e.changes and e.result == result)

    def to_dict(self, *, with_entries: bool = False) -> dict[str, Any]:
        """
        Args:
            with_entries: Include the timeline.

        Returns:
            The intervention as the API shows it.
        """
        shown: dict[str, Any] = {
            "n": self.n,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "ongoing": self.ongoing,
            "calls": len(self.entries),
            "changes": sum(1 for e in self.entries if e.changes and e.result == RESULT_OK),
            "proposed": self.count(RESULT_PROPOSED),
            "refused": self.count(RESULT_REFUSED),
            "failed": self.count(RESULT_FAILED),
            "apps": self.apps,
            "deployments": sum(1 for e in self.entries if e.deployment is not None),
            "conversation": self.conversation,
        }
        if with_entries:
            shown["timeline"] = [e.to_dict() for e in self.entries]
        return shown


def parse_moment(text: Any) -> datetime | None:
    """
    Args:
        text: A timestamp of the audit trail or of the store.

    Returns:
        The moment, aware; a naive one is taken for the machine's local time, as the store writes it.
    """
    if not isinstance(text, str) or not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment.astimezone() if moment.tzinfo is None else moment


def credential_of(entry: dict[str, Any]) -> tuple[str, str | None] | None:
    """
    Name the token an audit event was done by.

    Args:
        entry: One event of the audit trail.

    Returns:
        ``(token name, operator)``: the operator is the person a central acted for, when a fleet token did;
        None for an event no token did.
    """
    who = entry.get("who")
    actor = Actor.from_dict(who) if isinstance(who, dict) else Actor.from_label(entry.get("actor"))
    if actor.kind == "token" and actor.id:
        return actor.id, None
    if actor.kind == "fleet" and actor.id:
        return actor.id, actor.name
    return None


def _status_of(result: Any) -> int | None:
    """
    Args:
        result: The ``result`` of an audit line: ``ok`` or ``error:<status>``.

    Returns:
        The status, when the line carries one.
    """
    found = _ERROR_RESULT.fullmatch(str(result or ""))
    return int(found.group(1)) if found else None


def domain_of_path(path: str | None) -> str | None:
    """
    Args:
        path: A route called, such as ``/api/apps/shop.example.com/update``.

    Returns:
        The application or site it names, when it does.
    """
    found = _APP_PATH.match(path or "")
    return found.group(1).lower() if found else None


def _entry_of(group: list[dict[str, Any]], is_read_tool: Callable[[str], bool]) -> Entry | None:
    """
    Turn the events of one request into the entry a person reads.

    Args:
        group: The events that share a correlation id, oldest first.
        is_read_tool: Whether a tool of MCP only reads.

    Returns:
        The entry; None when the request left nothing a person reads (an event of the machine that a
        call caused is part of its call).
    """
    first = group[0]
    credential = credential_of(first)
    operator = credential[1] if credential else None
    via = "fleet" if operator else "api"
    entry = Entry(
        credential=credential[0] if credential else "",
        at=str(first.get("ts", "")),
        seq=first.get("seq") if isinstance(first.get("seq"), int) else None,
        corr=str(first["corr"]) if first.get("corr") else None,
        via=via,
        kind=KIND_EVENT,
        operator=operator,
    )
    saw_line = False
    for event in group:
        action = str(event.get("action", ""))
        raw_details = event.get("details")
        details: dict[str, Any] = raw_details if isinstance(raw_details, dict) else {}
        if _API_LINE.fullmatch(action):
            saw_line = True
            if entry.kind in (KIND_EVENT, KIND_PROPOSAL):
                entry.kind = KIND_CHANGE
            entry.method = action[4:].upper()
            entry.path = str(event.get("resource") or "")
            entry.status = _status_of(event.get("result"))
            entry.domain = entry.domain or domain_of_path(entry.path)
            if entry.status is not None:
                entry.result = RESULT_REFUSED if entry.status in _STATUS_REFUSED else RESULT_FAILED
        elif action == "mcp.call":
            tool = str(details.get("tool") or "")
            entry.via = "mcp"
            entry.tool = tool
            entry.kind = KIND_READ if is_read_tool(tool) else KIND_TOOL
            entry.domain = entry.domain or (
                str(details["domain"]).lower() if details.get("domain") else None
            )
            outcome = str(event.get("result") or "ok")
            status = details.get("status")
            entry.status = status if isinstance(status, int) else entry.status
            if outcome == "proposed":
                entry.result = RESULT_PROPOSED
            elif outcome == "denied":
                entry.result = RESULT_REFUSED
            elif outcome not in ("ok", "success"):
                entry.result = RESULT_FAILED
            if details.get("approval"):
                entry.approval = _int(details.get("approval"))
        elif action == "approval.request":
            entry.kind = KIND_PROPOSAL if not saw_line and entry.kind == KIND_EVENT else entry.kind
            entry.result = RESULT_PROPOSED
            entry.approval = _int(details.get("approval_id"))
            entry.path = entry.path or str(details.get("path") or "") or None
            entry.method = entry.method or (str(details.get("method") or "") or None)
            entry.domain = entry.domain or (
                str(details["domain"]).lower() if details.get("domain") else None
            )
        elif action == "approval.use":
            entry.approval = _int(details.get("approval_id")) or entry.approval
            entry.events.append(action)
        elif action in _DENIED_ACTIONS:
            entry.result = RESULT_REFUSED
            entry.events.append(action)
        elif not action.startswith("host.") and action not in (
            "approval.approve",
            "approval.reject",
        ):
            entry.events.append(action)
        if entry.via == "api" and isinstance(event.get("who"), dict):
            if event["who"].get("via") == "assistant":
                entry.via = "assistant"
    if entry.kind == KIND_EVENT and not entry.events:
        return None
    if entry.kind == KIND_CHANGE and entry.method in (None, ""):
        return None
    if entry.result == RESULT_PROPOSED and entry.kind == KIND_CHANGE:
        entry.kind = KIND_CHANGE
    entry.events = [name for name in dict.fromkeys(entry.events) if name != "mcp.call"]
    return entry


def _int(value: Any) -> int | None:
    """
    Args:
        value: Whatever an event holds.

    Returns:
        It as a whole number, or None.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class CredentialActivity:
    """
    Read what a token did.

    Args:
        log: The audit trail; the process's own by default.
        store: The store, for the jobs and deployments; the process-wide one by default.
        now: The moment to judge "ongoing" and the reading's window by; the clock by default.
    """

    def __init__(
        self,
        log: AuditLog | None = None,
        store: NoustStore | None = None,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._log = log
        self._store = store
        self._now = now or (lambda: datetime.now(timezone.utc))

    @property
    def log(self) -> AuditLog:
        """The audit trail the reading is made from."""
        if self._log is not None:
            return self._log
        from noust.core.audit import get_log

        return get_log()

    @property
    def store(self) -> NoustStore:
        """The store the jobs and deployments are in."""
        if self._store is not None:
            return self._store
        from noust.core.store import get_store

        return get_store()

    # --------------------------------------------------------------- the events

    def entries(
        self,
        credential: CredentialRef | str,
        *,
        days: int = DEFAULT_DAYS,
        is_read_tool: Callable[[str], bool] = lambda _tool: False,
    ) -> tuple[list[Entry], bool]:
        """
        Read what a credential did, oldest first.

        Args:
            credential: The token's name, or the reference of a credential made of several.
            days: How far back to read.
            is_read_tool: Whether a tool of MCP only reads (the tools' own table, which core cannot read).

        Returns:
            The entries, and whether the reading is complete: False when it stopped at the cap on how much
            of the log one reading goes through, before it reached ``days``.
        """
        names = frozenset(CredentialRef.of(credential).names)
        since = (self._now() - timedelta(days=max(1, min(days, MAX_DAYS)))).isoformat()
        events: list[dict[str, Any]] = []
        scanned = 0
        complete = True
        for event in self.log.iter_newest_first():
            scanned += 1
            if scanned > MAX_SCAN:
                complete = False
                break
            if str(event.get("ts", "")) < since:
                break
            found = credential_of(event)
            if found is not None and found[0] in names:
                events.append(event)
                if len(events) >= MAX_ENTRIES:
                    complete = False
                    break
        events.reverse()
        groups: dict[str, list[dict[str, Any]]] = {}
        order: list[list[dict[str, Any]]] = []
        for event in events:
            key = event.get("corr")
            if isinstance(key, str) and key:
                if key not in groups:
                    groups[key] = []
                    order.append(groups[key])
                groups[key].append(event)
            else:
                order.append([event])
        entries = [e for e in (_entry_of(group, is_read_tool) for group in order) if e is not None]
        entries.sort(key=lambda e: (e.moment, e.seq or 0))
        return entries, complete

    # ---------------------------------------------------------- interventions

    def interventions(
        self,
        credential: CredentialRef | str,
        *,
        days: int = DEFAULT_DAYS,
        visible: Callable[[str | None], bool] | None = None,
        is_read_tool: Callable[[str], bool] = lambda _tool: False,
    ) -> tuple[list[Intervention], bool]:
        """
        Group what a credential did into interventions, newest first.

        Args:
            credential: The token's name, or the reference of a credential made of several (the
                assistant of a person: its interventions are its conversations).
            days: How far back to read.
            visible: Whether the caller sees an application (None for the instance's own, which sees
                everything); an entry that names none is shown only to the instance.
            is_read_tool: Whether a tool of MCP only reads.

        Returns:
            The interventions, with the deployments each started, and whether the reading is complete.
        """
        ref = CredentialRef.of(credential)
        entries, complete = self.entries(ref, days=days, is_read_tool=is_read_tool)
        if visible is not None:
            entries = [e for e in entries if e.domain is not None and visible(e.domain)]
        groups: list[list[Entry]] = []
        for entry in entries:
            previous = groups[-1][-1] if groups else None
            if (
                previous is not None
                and entry.moment - previous.moment <= GAP
                and not (ref.by_conversation and entry.credential != previous.credential)
            ):
                groups[-1].append(entry)
            else:
                groups.append([entry])
        now = self._now()
        found: list[Intervention] = []
        for number, group in enumerate(groups, start=1):
            first, last = group[0], group[-1]
            found.append(
                Intervention(
                    n=first.seq if first.seq is not None else number,
                    started_at=first.at,
                    ended_at=last.at,
                    ongoing=now - last.moment <= GAP,
                    entries=group,
                    conversation=first.credential if ref.by_conversation else None,
                )
            )
        for intervention in found:
            self._attach_deployments(ref, intervention, visible)
        found.reverse()
        return found, complete

    def intervention(
        self,
        credential: CredentialRef | str,
        n: int,
        *,
        days: int = MAX_DAYS,
        visible: Callable[[str | None], bool] | None = None,
        is_read_tool: Callable[[str], bool] = lambda _tool: False,
    ) -> Intervention | None:
        """
        Args:
            credential: The token's name, or the reference of a credential made of several.
            n: The intervention's number.
            days: How far back to read.
            visible: Whether the caller sees an application.
            is_read_tool: Whether a tool of MCP only reads.

        Returns:
            The intervention, or None when the credential has none of that number that the caller sees.
        """
        found, _complete = self.interventions(
            credential, days=days, visible=visible, is_read_tool=is_read_tool
        )
        return next((item for item in found if item.n == n), None)

    # ------------------------------------------------------------- deployments

    def deployments_of(
        self, credential: CredentialRef | str, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        """
        Find the deployments the jobs a credential queued produced.

        Args:
            credential: The token's name, or the reference of a credential made of several.
            start: The first moment of the stretch.
            end: The last.

        Returns:
            One dict for each deployment: its row and the moment its job was queued, oldest first.
        """
        low = (start - _JOB_WINDOW_BEFORE).astimezone().replace(tzinfo=None).isoformat()
        high = (end + _JOB_WINDOW_AFTER).astimezone().replace(tzinfo=None).isoformat()
        names = CredentialRef.of(credential).names
        labels = [f"token:{name}" for name in names]
        # A central acts for a person under its token's name; the assistant's tokens never do.
        likes = [f"{_like_escape(name)} on behalf of %" for name in names[:1]]
        marks = ", ".join("?" for _ in labels) or "NULL"
        try:
            with self.store._transaction() as cursor:
                cursor.execute(
                    "SELECT d.id, d.domain, d.status, d.git_commit, d.release_id, d.started_at, "  # noqa: S608 - marks are question marks
                    "d.finished_at, d.schema_changed, d.job_id, j.created_at AS queued_at "
                    "FROM deployments d JOIN jobs j ON j.id = d.job_id "
                    f"WHERE (j.actor IN ({marks}) OR j.actor LIKE ? ESCAPE '\\') "
                    "AND j.created_at >= ? AND j.created_at <= ? ORDER BY d.id",
                    (*labels, likes[0] if likes else "", low, high),
                )
                rows = cursor.fetchall()
        except sqlite3.OperationalError:
            return []
        return [
            {
                "id": int(r[0]),
                "domain": str(r[1]),
                "status": str(r[2]),
                "commit": r[3],
                "release": r[4],
                "started_at": r[5],
                "finished_at": r[6],
                "schema_changed": bool(r[7]),
                "job": r[8],
                "queued_at": r[9],
            }
            for r in rows
        ]

    def _attach_deployments(
        self,
        credential: CredentialRef | str,
        intervention: Intervention,
        visible: Callable[[str | None], bool] | None,
    ) -> None:
        """
        Link each call of an intervention to the deployment its job produced.

        Args:
            credential: The token's name, or the reference of a credential made of several.
            intervention: The intervention; its entries are updated.
            visible: Whether the caller sees an application.
        """
        start = intervention.entries[0].moment
        end = intervention.entries[-1].moment
        deployments = self.deployments_of(credential, start, end)
        if visible is not None:
            deployments = [d for d in deployments if visible(d["domain"])]
        taken: set[int] = set()
        for entry in intervention.entries:
            if not entry.changes or entry.domain is None or entry.result != RESULT_OK:
                continue
            for deployment in deployments:
                queued = parse_moment(deployment["queued_at"])
                if (
                    deployment["id"] in taken
                    or deployment["domain"] != entry.domain
                    or queued is None
                    or not entry.moment - _JOB_WINDOW_BEFORE
                    <= queued
                    <= entry.moment + _JOB_WINDOW_AFTER
                ):
                    continue
                taken.add(deployment["id"])
                entry.deployment = {k: v for k, v in deployment.items() if k != "queued_at"}
                break

    def deployments(self, intervention: Intervention) -> list[dict[str, Any]]:
        """
        Args:
            intervention: An intervention read with its deployments.

        Returns:
            The deployments its calls started, oldest first.
        """
        return [e.deployment for e in intervention.entries if e.deployment is not None]


def _like_escape(text: str) -> str:
    """
    Args:
        text: A token's name.

    Returns:
        It with the characters ``LIKE`` takes for a pattern made literal.
    """
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def merge_clients(*groups: Iterable[Any]) -> list[Any]:
    """
    Args:
        groups: Lists of clients.

    Returns:
        Them as one list, without repeats of the same name, version and source.
    """
    seen: set[tuple[str, str, str]] = set()
    merged: list[Any] = []
    for group in groups:
        for item in group:
            key = (item.name, item.version, item.source)
            if key not in seen:
                seen.add(key)
                merged.append(item)
    return merged


__all__ = [
    "DEFAULT_DAYS",
    "GAP",
    "KIND_CHANGE",
    "KIND_EVENT",
    "KIND_PROPOSAL",
    "KIND_READ",
    "KIND_TOOL",
    "MAX_DAYS",
    "RESULT_FAILED",
    "RESULT_OK",
    "RESULT_PROPOSED",
    "RESULT_REFUSED",
    "CredentialActivity",
    "CredentialRef",
    "Entry",
    "Intervention",
    "assistant_tokens",
    "credential_of",
    "domain_of_path",
    "parse_moment",
    "reference_of",
]
