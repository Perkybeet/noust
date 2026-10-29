# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Apply the fleet's settings: the fleet action that brings every server to what the central wants (item 90).

A bulk action like the others (:mod:`noust.web.fleet_jobs`): a plan first, batches, a record per
server, the node's own words when something fails. What it adds is the reconciliation of the
settings of the fleet (:mod:`noust.fleet.settings`), and every step is still a call to a server's
own API (rule 3):

- **It reads what the server says it manages** (``GET /api/managed-settings``, where the server
  has just looked at what it holds, so what its own operator changed shows as ``overridden``),
  works out the difference with what the central wants (:func:`~noust.fleet.settings.reconcile.plan_node`)
  and does it setting by setting.
- **It writes by the server's own typed route** for each kind, never ``PATCH /api/config``:
  the server validates with its own validators. Then it records that it did, so the server can
  tell the setting from the ones it made itself.
- **It signs every write** with the person who approved the change and the change
  (:mod:`noust.fleet.settings.signature`), also when nobody is in front of the central: the person
  pressing "apply", or no person at all because a server came back, is not who approved it.
  The server takes the signature as the confirmation of sudo mode for the fleet's settings and
  for nothing else (:mod:`noust.web.auth.fleet_sync`), and only with its ceiling at ``admin``.
- **It leaves alone** what the server's own operator changed, unless told to put it back, and what
  is the same. What the central no longer wants is let go, never deleted. A setting that is
  already on a server and was not the fleet's is not taken over unless told to.
- **It sends a channel only where its route can match** (:mod:`noust.fleet.settings.reach`): a
  server the route does not name, or that has no application with a domain or a tag the route
  names, is sent nothing, so a credential does not travel to a machine that could never use it.
  The applications are read from the server only when some route depends on them, and when they
  cannot be read nothing is sent and the record says so.
- **A secret is in a request body and nowhere else**: not in the record of the job, not in its
  log, and not in the words a server answers with.

Three things start it: saving a setting of the fleet (:func:`queue_apply`, from the route), a
server answering after it was silent (the watcher, :class:`SettingsWatcher`, which acts on the
note any process left in the store), and a person pressing "apply to the fleet".
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from noust.core.exceptions import NoustError, ValidationError
from noust.core.server_text import ServerText, say
from noust.fleet.aggregate import Asker, Failure, NodeSource
from noust.fleet.settings.desired import DesiredSetting, FleetSettings
from noust.fleet.settings.kinds import KINDS
from noust.fleet.settings.push import find, push
from noust.fleet.settings.reach import explain, needs_apps
from noust.fleet.settings.reconcile import Step, plan_node
from noust.fleet.settings.routes import MANAGED_ITEM, MANAGED_LIST, MANAGED_PREFIX
from noust.fleet.settings.signature import SyncSignature
from noust.web.fleet_jobs import (
    ACTIONS,
    MAX_OUTPUT,
    REQUEST_TIMEOUT,
    ActionFailed,
    ActionSpec,
    FleetRequest,
    NodeRun,
    offers,
    schema_of,
)

if TYPE_CHECKING:
    from noust.core.store import NoustStore
    from noust.web.jobs import Job

logger = logging.getLogger(__name__)

__all__ = [
    "ACTION",
    "ITEM_STATES",
    "REACH_EVERY",
    "RETRY_AFTER",
    "SettingsWatcher",
    "queue_apply",
    "start_watcher",
]

#: The action's name.
ACTION = "settings_apply"

#: The sentence for each thing that can fail to match on a server (:data:`noust.fleet.settings.reach.REASONS`).
_OUT_OF_REACH = {
    "server": "fleet_settings.out_of_reach.server",
    "domain": "fleet_settings.out_of_reach.domain",
    "tag": "fleet_settings.out_of_reach.tag",
}

#: Where a server lists its applications, which decide where a route by domain or tag reaches.
APPLICATIONS_PATH = "/api/apps"

#: What a setting's item can say on one server.
ITEM_STATES = (
    "created",
    "updated",
    "restored",
    "in_sync",
    "overridden",
    "released",
    "out_of_reach",
    "conflict",
    "unsupported",
    "failed",
)

#: Who the nodes' audit logs say read their managed settings when nobody pressed anything.
AUTOMATIC_ACTOR = "noust-fleet-sync"

#: Seconds the watcher waits between looks at what is waiting.
WATCH_EVERY = 30.0

#: Seconds before the watcher tries a server again that it did not manage to get done.
RETRY_AFTER = 300.0

#: Seconds between two looks at which servers a route by domain or tag can match now. An
#: application is deployed on a server without the central being told, so this is how a channel
#: that waited for one reaches it.
REACH_EVERY = 600.0

#: The shortest secret that is worth hiding from a server's words; shorter ones are not secrets.
_MIN_SECRET = 4


# -------------------------------------------------------------------------- the options


def _check_options(options: Mapping[str, Any]) -> dict[str, Any]:
    """
    Check the options of the action.

    Args:
        options: ``adopt`` (take over a setting a server already has that is not the fleet's),
            ``restore`` (a list of ``kind/name`` to put back where a server's operator changed
            them) and ``restore_all``.

    Returns:
        The options, complete.

    Raises:
        ValidationError: An unknown option, or one of the wrong kind.
    """
    allowed = {"adopt", "restore", "restore_all"}
    unknown = sorted(set(options) - allowed)
    if unknown:
        raise ValidationError(
            f"Unknown option(s): {', '.join(unknown)}",
            details=f"This action takes: {', '.join(sorted(allowed))}.",
            field="options",
        )
    checked: dict[str, Any] = {}
    for flag in ("adopt", "restore_all"):
        value = options.get(flag, False)
        if not isinstance(value, bool):
            raise ValidationError(f"{flag} must be bool", field="options")
        checked[flag] = value
    restore = options.get("restore") or []
    if not isinstance(restore, list) or not all(
        isinstance(entry, str) and entry.count("/") == 1 for entry in restore
    ):
        raise ValidationError(
            "restore is a list of 'kind/name'",
            details="Such as ['channel/ops', 'variable/REGION'].",
            field="options",
        )
    checked["restore"] = sorted(set(restore))
    return checked


# ---------------------------------------------------------------------------- one server


class _Signed:
    """A server, spoken to as a push of one change: what :func:`~noust.fleet.settings.push.push` writes through."""

    def __init__(self, run: NodeRun, signature: SyncSignature) -> None:
        source = run.source
        self._run = run
        self.source = NodeSource(
            source.manager,
            source.record,
            Asker(actor=signature.actor, sync=signature),
            REQUEST_TIMEOUT,
        )

    def call(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        """
        Send one signed request and read its JSON.

        Args:
            method: The method.
            path: The server's path.
            body: The JSON body.
            params: Query parameters.

        Returns:
            The decoded answer.

        Raises:
            Failure: The server refused, or did not answer.
        """
        return self._run.call(method, path, body=body, params=params, on=self.source)


def _scrub(text: str | None, secrets: Iterable[str]) -> str | None:
    """
    Take the secrets out of a server's words.

    A server answering "invalid token X" says what it was sent; the words are kept verbatim for
    the operator and the secret is not in them.

    Args:
        text: What the server said.
        secrets: The values that were sent as credentials.

    Returns:
        The text with each secret replaced by ``***``.
    """
    if text is None:
        return None
    for secret in sorted((s for s in secrets if len(s) >= _MIN_SECRET), key=len, reverse=True):
        text = text.replace(secret, "***")
    return text


def _item(step: Step, state: str, **extra: Any) -> dict[str, Any]:
    """
    Args:
        step: What was done about the setting.
        state: One of :data:`ITEM_STATES`.
        **extra: More fields.

    Returns:
        The item the server's record carries for the setting.
    """
    item: dict[str, Any] = {
        "resource": step.resource,
        "name": step.name,
        "state": state,
        "reason": step.reason,
        "version": step.setting.version if step.setting is not None else None,
        "change": step.setting.change_id if step.setting is not None else None,
    }
    item.update(extra)
    return item


def _told(text: ServerText) -> dict[str, Any]:
    """
    Args:
        text: A sentence of the catalog.

    Returns:
        The fields an item carries for it.
    """
    return {"message": text.text, "hint": text.hint, "coded": text.to_dict()}


def _unsupported(run: NodeRun, resource: str) -> ServerText | None:
    """
    Say why a server cannot take a kind of setting, when it cannot.

    Args:
        run: The server.
        resource: The kind.

    Returns:
        The sentence, or None when it offers every route the kind needs.
    """
    schema = schema_of(run.source)
    for method, template in KINDS[resource].operations:
        if not offers(schema, method, template)[0]:
            return say(
                "fleet_settings.item_unsupported",
                kind=resource,
                operation=f"{method} {template}",
            )
    return None


def _applications(run: NodeRun) -> list[dict[str, Any]] | None:
    """
    Read the applications of a server, for the routes that depend on them.

    Args:
        run: The server.

    Returns:
        Each application's listing (``domain`` and ``tags``); None when the server did not give
        them, which a route that depends on them takes as not knowing.
    """
    try:
        answer = run.call("GET", APPLICATIONS_PATH)
    except Failure as exc:
        run.log(f"Could not read the applications: {exc.message}")
        return None
    listed = answer.get("apps") if isinstance(answer, dict) else None
    if not isinstance(listed, list):
        run.log("The applications came in a shape that is not a list")
        return None
    return [app for app in listed if isinstance(app, dict)]


def _reach(
    run: NodeRun, wanted: Sequence[DesiredSetting]
) -> tuple[dict[tuple[str, str], str], set[tuple[str, str]]]:
    """
    Say which settings cannot match anything on a server.

    Args:
        run: The server.
        wanted: What the central wants of it.

    Returns:
        ``(kind, name)`` to what cannot match, and the settings whose reach is not known because
        the applications that decide it could not be read.
    """
    apps: list[dict[str, Any]] | None = None
    if any(needs_apps(setting.resource, setting.value) for setting in wanted):
        apps = _applications(run)
    out: dict[tuple[str, str], str] = {}
    unknown: set[tuple[str, str]] = set()
    for setting in wanted:
        verdict = explain(setting.resource, setting.value, run.name, apps)
        key = (setting.resource, setting.name)
        if verdict.reaches is False and verdict.why is not None:
            out[key] = verdict.why
        elif verdict.reaches is None:
            unknown.add(key)
    return out, unknown


def _held_back(step: Step) -> dict[str, Any]:
    """
    Args:
        step: A setting kept because its route cannot be matched here, or not known to.

    Returns:
        Its item: what could not match, in the catalog's words.
    """
    if step.reason == "reach_unknown":
        text = say("fleet_settings.reach_unknown", kind=step.resource, name=step.name)
        return _item(step, "failed", code=text.code, **_told(text))
    text = say(_OUT_OF_REACH[step.why or "server"], kind=step.resource, name=step.name)
    return _item(step, "out_of_reach", why=step.why, **_told(text))


def _apply(
    run: NodeRun, store: FleetSettings, step: Step, options: Mapping[str, Any]
) -> dict[str, Any]:
    """
    Write one setting on a server and record that it is the fleet's.

    Args:
        run: The server.
        store: What the central wants.
        step: The step.
        options: The action's options.

    Returns:
        The setting's item.
    """
    setting = step.setting
    if setting is None:  # pragma: no cover - an apply step always has its setting
        raise RuntimeError("an apply step without a setting")
    problem = _unsupported(run, setting.resource)
    if problem is not None:
        return _item(step, "unsupported", **_told(problem))
    signed = _Signed(run, SyncSignature(person=setting.changed_by, change=setting.change_id))
    secrets = store.secrets_of(setting).values()
    try:
        found = find(signed, setting.resource, setting.name)
        if step.row is None and found is not None and not options.get("adopt"):
            return _item(
                step,
                "conflict",
                **_told(
                    say("fleet_settings.item_conflict", kind=setting.resource, name=setting.name)
                ),
            )
        value = KINDS[setting.resource].for_node(store.whole_value(setting), run.name)
        how = push(signed, setting.resource, setting.name, value, found)
        signed.call(
            "PUT",
            MANAGED_ITEM.format(resource=setting.resource, name=quote(setting.name, safe="")),
            body={"version": setting.version},
        )
    except Failure as exc:
        return _item(
            step,
            "failed",
            message=_scrub(exc.message, secrets),
            hint=_scrub(exc.hint, secrets),
            code=exc.code,
            output=_scrub(exc.output, secrets),
        )
    except NoustError as exc:
        return _item(
            step,
            "failed",
            message=_scrub(exc.message, secrets),
            hint=_scrub(exc.details or None, secrets),
            code=exc.coded.code if exc.coded else type(exc).__name__.lower(),
        )
    return _item(step, "restored" if step.reason == "restore" else how)


def _release(run: NodeRun, step: Step) -> dict[str, Any]:
    """
    Let a server go of a setting that is no longer the fleet's. It keeps the setting.

    Args:
        run: The server.
        step: The step.

    Returns:
        The setting's item.
    """
    row = step.row or {}
    # The setting is gone from the central, so is who approved its removal: the release is signed
    # with the change the server last applied, which is whose setting it was.
    try:
        signature = SyncSignature(
            person=str(row.get("applied_by", "")), change=str(row.get("change", ""))
        )
        _Signed(run, signature).call(
            "DELETE", MANAGED_ITEM.format(resource=step.resource, name=quote(step.name, safe=""))
        )
    except Failure as exc:
        return _item(
            step, "failed", message=exc.message, hint=exc.hint, code=exc.code, output=exc.output
        )
    except NoustError as exc:
        return _item(step, "failed", message=exc.message, hint=exc.details or None)
    return _item(step, "released")


def _settings_apply(run: NodeRun) -> None:
    """
    Bring one server to the settings the central wants.

    Args:
        run: The server, asked as whoever started the job.

    Raises:
        ActionFailed: Some setting could not be applied; the items say which and why.
    """
    manager = run.source.manager
    store = FleetSettings(manager.store, manager.secrets)
    node = run.name
    run.step("Reading what it manages")
    me = run.call("GET", "/api/auth/fleet/self")
    managed = run.call("GET", MANAGED_PREFIX)
    rows = managed.get("items") if isinstance(managed, dict) else None
    wanted = store.effective_for(node)
    out_of_reach, reach_unknown = _reach(run, wanted)
    steps = plan_node(
        wanted,
        [row for row in rows or [] if isinstance(row, dict)],
        restore=set(run.options.get("restore") or []),
        restore_all=bool(run.options.get("restore_all")),
        independent=store.mode(node) == "independent",
        central=str(me.get("central")) if isinstance(me, dict) and me.get("central") else None,
        out_of_reach=out_of_reach,
        reach_unknown=reach_unknown,
    )
    failures: list[str] = []
    for position, step in enumerate(steps, start=1):
        run.step(f"{step.resource} {step.name} ({position} of {len(steps)})")
        if step.action == "keep" and step.reason in ("out_of_reach", "reach_unknown"):
            item = _held_back(step)
        elif step.action == "keep":
            item = _item(step, step.reason)
        elif step.action == "release":
            item = _release(run, step)
        else:
            item = _apply(run, store, step, run.options)
        run.items.append(item)
        if item["state"] == "failed":
            failures.append(
                f"{step.resource} {step.name}: {item.get('message') or ''}".rstrip(": ")
            )
            run.log(failures[-1])
    if failures:
        raise ActionFailed(
            f"{len(failures)} of {len(steps)} settings could not be applied on {node}",
            details="The server's own words are in each setting's record; apply again once fixed.",
            output="\n".join(failures)[:MAX_OUTPUT],
        )
    store.clear_resync(node)


ACTIONS[ACTION] = ActionSpec(
    ACTION,
    "Apply the fleet's settings",
    (
        ("GET", "/api/auth/fleet/self"),
        ("GET", APPLICATIONS_PATH),
        MANAGED_LIST,
        ("PUT", MANAGED_ITEM),
        ("DELETE", MANAGED_ITEM),
    ),
    serial=4,
    # A server that refuses one setting is that server's problem: it must not stop the rest.
    max_failures=None,
    run=_settings_apply,
    check_options=_check_options,
)


# --------------------------------------------------------------------------- starting it


def queue_apply(
    nodes: Sequence[str] | None = None,
    *,
    options: Mapping[str, Any] | None = None,
    asker: Asker | None = None,
    actor: str = AUTOMATIC_ACTOR,
    store: NoustStore | None = None,
) -> Job | None:
    """
    Queue the reconciliation of some servers, or of all of them, as a console job.

    Used where nobody is in front of the central: a setting was just saved, or a server came back.
    Reading what a server manages needs only to read, so the job asks as a reader; every write is
    signed with the person who approved the change.

    Args:
        nodes: The servers; every one when None.
        options: The action's options.
        asker: Who reads; the automatic reader by default.
        actor: Who the job says started it.
        store: The central's store (tests).

    Returns:
        The job; None when there is no server to apply to.
    """
    from noust.fleet.nodes import NodeManager
    from noust.web.fleet_jobs import plan, start_job

    manager = NodeManager(store=store)
    names = list(nodes) if nodes is not None else [record.name for record in manager.list()]
    if not names:
        return None
    reader = asker or Asker(actor=AUTOMATIC_ACTOR, scope="read")
    request = FleetRequest(ACTION, nodes=names, options=dict(options or {}))
    the_plan = plan(request, manager=manager, asker=reader, probe=False)
    job, _described = start_job(the_plan, asker=reader, actor=actor, elevated=False)
    return job


class SettingsWatcher:
    """
    Applies the fleet's settings to the servers that came back, or joined, since it last looked.

    Whichever process saw a server answer after a silence left a note in the store
    (:meth:`FleetSettings.request_resync`); this is the one thing that acts on it, in the console's
    process, where the jobs are. A server that was not got done is tried again after
    :data:`RETRY_AFTER` seconds, not at every look.

    It also looks, every :data:`REACH_EVERY` seconds and only when some route depends on the
    applications of a server, for the servers a channel can match on now and has not been sent to
    (:meth:`look_reach`).

    Args:
        queue: Starts the job; :func:`queue_apply`.
        clock: Monotonic clock (tests move it).
        settings: What the central wants.
        read: Says where each server stands (:func:`noust.fleet.settings.status.read`), as the
            automatic reader.
    """

    def __init__(
        self,
        queue: Callable[..., Job | None] = queue_apply,
        clock: Callable[[], float] = time.monotonic,
        settings: Callable[[], FleetSettings] = FleetSettings,
        read: Callable[[FleetSettings], dict[str, Any]] | None = None,
    ) -> None:
        self._queue = queue
        self._clock = clock
        self._settings = settings
        self._read = read or _read_standing
        self._reach_looked = -REACH_EVERY
        self._tried: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def look(self) -> list[str]:
        """
        Queue the servers that are waiting.

        Returns:
            The servers a job was queued for.
        """
        now = self._clock()
        waiting = [
            node
            for node in self._settings().pending_resync()
            if now - self._tried.get(node, -RETRY_AFTER) >= RETRY_AFTER
        ]
        if not waiting:
            return []
        for node in waiting:
            self._tried[node] = now
        self._queue(waiting)
        return waiting

    def look_reach(self) -> list[str]:
        """
        Queue the servers a channel can be sent to now and has not been.

        Returns:
            The servers a job was queued for; none before :data:`REACH_EVERY` seconds have passed
            since the last look, and none when no route depends on the servers' applications.

        Raises:
            SealError: This central is locked and no server can be asked.
        """
        now = self._clock()
        if now - self._reach_looked < REACH_EVERY:
            return []
        self._reach_looked = now
        settings = self._settings()
        if not any(needs_apps(s.resource, s.value) for s in settings.list()):
            return []
        standing = self._read(settings)
        behind = {
            str(cell["node"])
            for entry in standing.get("settings", [])
            for cell in entry.get("servers", [])
            if cell.get("sync") == "pending" and cell.get("reach", {}).get("reaches") is True
        }
        waiting = [
            node
            for node in sorted(behind)
            if now - self._tried.get(node, -RETRY_AFTER) >= RETRY_AFTER
        ]
        if not waiting:
            return []
        for node in waiting:
            self._tried[node] = now
        self._queue(waiting)
        return waiting

    def start(self) -> None:
        """Start looking in the background. Starting twice is a no-op."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="noust-fleet-settings", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop looking."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        self._thread = None

    def _loop(self) -> None:
        """Look every :data:`WATCH_EVERY` seconds until stopped. The thread's error boundary."""
        while not self._stop.wait(WATCH_EVERY):
            try:
                self.look()
                self.look_reach()
            # The boundary of a background thread: what a look can fail with (the store, the
            # registry of servers, the job manager, a sealed central) must not end the thread, and
            # is logged with its traceback; anything else is a bug and is let through.
            except (NoustError, OSError, sqlite3.Error, RuntimeError):
                logger.exception("The fleet settings watcher failed")


def _read_standing(settings: FleetSettings) -> dict[str, Any]:
    """
    Args:
        settings: What the central wants.

    Returns:
        Where each server stands on each setting, asked as the automatic reader.
    """
    from noust.fleet.settings.status import read

    return read(settings, asker=Asker(actor=AUTOMATIC_ACTOR, scope="read"), refresh=True)


def start_watcher() -> SettingsWatcher:
    """
    Start the console's watcher of the servers that came back.

    Returns:
        The running watcher, for stopping it.
    """
    watcher = SettingsWatcher()
    watcher.start()
    return watcher
