# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A pass over one server: what it has, what to take, taking it, and telling it what the central holds (item 195).

One pass, for one server, at a time (a second asked for while one runs is told so and does nothing),
over the central's connection for transfers (:data:`~noust.fleet.tunnels.TRANSFER`), one copy after
another, newest first. What a pass does, in order:

1. **Ask what the server has.** A server before 4.3 has no such list: its database dumps are taken
   by the routes they always had and the rest is said not to be supported until it is updated.
   A server whose list has not changed since the last whole pass (it says so, by the fingerprint)
   costs one small request.
2. **Judge it.** Every name, size and digest the server says is checked before it reaches a path
   (:mod:`~noust.fleet.collect.names`); what is wrong is not taken and is said.
3. **Take what the central lacks.** A copy at a time, its files in pieces (:mod:`~noust.fleet.collect.fetch`),
   paced and with room asked for first, every file checked against the digest the server recorded.
   The copy is recorded only when all its files are in their places. A copy whose digest does not
   match is discarded and said, and the pass goes on with the next; a disk with no room ends the pass
   with that said, and **nothing is deleted to make room**.
4. **Say what is held.** The server is told which copies the central holds, so they count as kept
   elsewhere and its own retention waits for the others (:mod:`noust.core.backup_hold`).
5. **Let go by the central's own retention** what its settings say, and nothing else: never because
   a server deleted a copy, listed fewer, or said anything.
6. **Write down how it went**, and tell the operator once when it keeps failing, when the disk is
   full, or when a server sent something that did not match what it said it was.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from noust.core import audit
from noust.core.exceptions import NodeError, NodeRefusedError, NodeUnreachableError
from noust.core.fs import FileSystem, get_fs
from noust.core.store import NoustStore, get_store
from noust.core.timeutil import utc_now_iso
from noust.fleet.collect.api import (
    ClientApi,
    LegacyApi,
    NodeApi,
    NodeUnsupported,
    client_api_for,
)
from noust.fleet.collect.board import Board, get_board
from noust.fleet.collect.fetch import (
    DigestMismatch,
    Fetcher,
    Received,
    TooManyFailures,
    TransferError,
)
from noust.fleet.collect.layout import Layout
from noust.fleet.collect.names import RemoteCatalogue, RemoteItem
from noust.fleet.collect.notify import Notify, notify_failure
from noust.fleet.collect.plan import expired, make_plan
from noust.fleet.collect.records import (
    HELD,
    Collected,
    CollectedFile,
    CollectedStore,
    NodeStates,
)
from noust.fleet.collect.settings import Follows, SettingsStore
from noust.fleet.collect.space import DiskFullError, Guard, Space, space_of
from noust.fleet.collect.throttle import Throttle

__all__ = ["NOTIFY_AFTER", "NodeCollector", "PassResult"]

_log = logging.getLogger(__name__)

#: Passes in a row that fail before the operator is told (once, until it works again).
NOTIFY_AFTER = 3

#: What a pass may fail with at the edge of the store.
_STORE_ERRORS = (sqlite3.Error, OSError)

#: Codes that are said the first time they happen: waiting for the third time would be waiting
#: for a copy that is not being kept.
_IMMEDIATE = frozenset({"disk_full", "mismatch", "ceiling"})


@dataclass
class PassResult:
    """
    How a pass over a server went.

    Attributes:
        node: The server.
        state: ``ok`` (nothing left to take, nothing failed), ``unchanged`` (the server said its
            list was the one the central had), ``partial`` (some taken, something failed or was not
            taken), ``failed``, ``skipped`` (not followed, or already running).
        fetched: Copies taken.
        bytes: Bytes taken.
        held: Copies the central already held of what the server lists.
        left: Copies the server lists that were not taken, with why.
        rejected: What the server listed that the central did not accept, with why.
        problems: Kinds the server could not list.
        failures: What failed, for each, in words.
        code: A stable word for the worst thing that happened.
        stopped: ``disk_full`` when the disk ended the pass.
        let_go: Copies the central's retention let go.
        legacy: The server is before 4.3: only its database dumps were looked at.
        notes: Anything else worth saying.
    """

    node: str
    state: str = "ok"
    fetched: int = 0
    bytes: int = 0
    held: int = 0
    left: list[dict[str, str]] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)
    problems: list[dict[str, str]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    code: str | None = None
    stopped: str | None = None
    let_go: int = 0
    legacy: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The result as plain data.
        """
        return dict(self.__dict__)


class NodeCollector:
    """
    Collect the copies of the servers.

    Args:
        store: The store; the process-wide one by default.
        api_for: Makes the API of a server's transfer connection.
        legacy_for: Makes the API of a server before 4.3 (given the 4.3 one's client).
        layout: Where the copies go; the central's backup directory by default.
        notify: Tells the operator; the central's channels by default.
        sleep: Waits between attempts of a piece (tests record it).
        should_stop: Whether the console is stopping.
        board: What is moving, for the console.
        fs: The filesystem changes go through.
        clock: Now, in UTC (tests move it).
        measure: Measures the disk the copies are on (tests replace it).
        throttle: Makes the speed limit of a pass from bytes a second (tests record its waits).
    """

    def __init__(
        self,
        *,
        store: NoustStore | None = None,
        api_for: Callable[[str], NodeApi] = client_api_for,
        legacy_for: Callable[[NodeApi], NodeApi] | None = None,
        layout: Layout | None = None,
        notify: Notify = notify_failure,
        sleep: Callable[[float], None] | None = None,
        should_stop: Callable[[], bool] = lambda: False,
        board: Board | None = None,
        fs: FileSystem | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        measure: Callable[..., Space] = space_of,
        throttle: Callable[[float | None], Throttle] = Throttle,
    ) -> None:
        self._measure = measure
        self._throttle = throttle
        self._store = store
        self._api_for = api_for
        self._legacy_for = legacy_for or self._default_legacy
        self._layout = layout
        self._notify = notify
        self._sleep = sleep
        self._should_stop = should_stop
        self._board = board
        self._fs = fs
        self._clock = clock
        self._locks: dict[str, threading.Lock] = {}
        self._guard_lock = threading.Lock()
        #: What a server sent that did not match, by copy: not asked for again while it is the same.
        self._quarantine: dict[tuple[str, str, str], tuple[int, str | None]] = {}

    # ------------------------------------------------------------------ plumbing

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    @property
    def layout(self) -> Layout:
        """Where the copies go."""
        if self._layout is None:
            self._layout = Layout.default()
        return self._layout

    @property
    def board(self) -> Board:
        """What is moving."""
        return self._board or get_board()

    @property
    def fs(self) -> FileSystem:
        """The filesystem changes go through."""
        return self._fs or get_fs()

    @staticmethod
    def _default_legacy(api: NodeApi) -> NodeApi:
        """
        Make the API of a server before 4.3 from the one that found it is.

        Args:
            api: The 4.3 API, whose client is reused.

        Returns:
            The API for the routes a 4.2 server has.
        """
        if isinstance(api, ClientApi):
            return LegacyApi(api.client)
        raise NodeUnsupported("A server before 4.3 can only be reached through its client")

    def _lock_of(self, node: str) -> threading.Lock:
        """
        Args:
            node: The server.

        Returns:
            The lock that keeps two passes over it from running at once.
        """
        with self._guard_lock:
            return self._locks.setdefault(node, threading.Lock())

    # ---------------------------------------------------------------------- pass

    def collect(self, node: str, *, force: bool = False) -> PassResult:
        """
        Make a pass over one server.

        Args:
            node: The server.
            force: Read the whole list even when the server says nothing changed.

        Returns:
            How it went. Never raises for what a server did: that is the result.
        """
        lock = self._lock_of(node)
        if not lock.acquire(blocking=False):
            return PassResult(node, "skipped", notes=["a pass over this server is already running"])
        self.board.begin(node)
        try:
            return self._pass(node, force)
        finally:
            self.board.end(node)
            lock.release()

    def _pass(self, node: str, force: bool) -> PassResult:
        """
        Run a pass, the lock held.

        Args:
            node: The server.
            force: Read the whole list even when the server says nothing changed.

        Returns:
            How it went.
        """
        settings = SettingsStore(self.store).get()
        if not Follows(self.store).follows(node, settings):
            return PassResult(node, "skipped", notes=["this server is not followed"])
        states = NodeStates(self.store)
        before = states.get(node)
        result = PassResult(node)
        api = self._api_for(node)
        now = self._clock()
        stale = True
        if before.last_ok_at:
            try:
                stale = now - datetime.fromisoformat(before.last_ok_at) >= timedelta(
                    hours=settings.pass_hours
                )
            except ValueError:
                stale = True
        known = None if (force or stale) else before.fingerprint
        try:
            catalogue, api = self._catalogue(node, api, known, result)
        except NodeRefusedError as exc:
            return self._failed(node, result, states, before, _refusal_code(exc), exc)
        except NodeUnreachableError as exc:
            return self._failed(node, result, states, before, "unreachable", exc)
        except NodeError as exc:
            return self._failed(node, result, states, before, "node_error", exc)
        support = {"mode": "databases_only" if result.legacy else "full"}
        if catalogue.unchanged:
            result.state = "unchanged"
            states.record_look(node, support=support)
            return result
        result.problems = [dict(problem) for problem in catalogue.problems]
        result.rejected = [
            {"kind": r.kind, "id": r.id, "reason": r.reason} for r in catalogue.rejected
        ]
        try:
            self._take(node, api, catalogue, settings, result)
            self._say_held(node, api, result)
            self._retire_by_retention(node, settings, result)
        except _STORE_ERRORS as exc:
            _log.exception("Collecting %s failed at the store", node)
            return self._failed(node, result, states, before, "store", exc)
        complete = not result.failures and result.stopped is None
        result.state = (
            "ok" if complete else ("partial" if (result.fetched or result.held) else "failed")
        )
        text = "; ".join(result.failures[:3]) if result.failures else None
        state = states.record_pass(
            node,
            ok=complete,
            error=text,
            code=result.code,
            fingerprint=catalogue.fingerprint if complete else None,
            support=support,
        )
        if not complete:
            self._tell(node, before.last_error_code, state.failures, result.code, text)
        return result

    def _catalogue(
        self, node: str, api: NodeApi, known: str | None, result: PassResult
    ) -> tuple[RemoteCatalogue, NodeApi]:
        """
        Ask what the server has, falling back to the routes of a server before 4.3.

        Args:
            node: The server.
            api: Its 4.3 API.
            known: The fingerprint the central holds, or None.
            result: The pass, told when the server is before 4.3.

        Returns:
            What it said, and the API to take it with.
        """
        try:
            return api.list(known=known), api
        except NodeUnsupported:
            result.legacy = True
            result.notes.append(
                f"{node} has no routes to collect copies: only its database dumps are taken "
                "until it is updated to Noust 4.3"
            )
            legacy = self._legacy_for(api)
            return legacy.list(known=None), legacy

    def _take(
        self,
        node: str,
        api: NodeApi,
        catalogue: RemoteCatalogue,
        settings: Any,
        result: PassResult,
    ) -> None:
        """
        Take every copy of the plan.

        Args:
            node: The server.
            api: The server's API.
            catalogue: What it listed, judged.
            settings: The knobs.
            result: The pass.
        """
        collected = CollectedStore(self.store)
        rows = {(c.kind, c.item_id): c for c in collected.list(node=node, state=None)}
        collected.forget_retired_except(node, {(i.kind, i.id) for i in catalogue.items})
        plan = make_plan(catalogue.items, rows, kinds=settings.kinds)
        result.held = plan.held
        result.left = [
            {"kind": s.item.kind, "id": s.item.id, "reason": s.reason}
            for s in plan.skipped
            if s.reason in ("blocked",)
        ]
        for changed in plan.changed:
            result.notes.append(
                f"{changed.kind} {changed.id} is not the file the central took: the server says "
                "another digest now (it was sealed or rewritten); the central keeps what it took"
            )
        guard = Guard(self.layout.root, settings.min_free_bytes, measure=self._measure)
        throttle = self._throttle(settings.rate_bytes_per_second)
        self.board.update(node, todo=len(plan.fetch))
        for index, item in enumerate(plan.fetch):
            if self._should_stop():
                result.notes.append("the console is stopping; the pass resumes later")
                break
            quarantined = self._quarantine.get((node, item.kind, item.id))
            if quarantined == (item.main.size, item.main.sha256):
                result.left.append({"kind": item.kind, "id": item.id, "reason": "mismatch"})
                continue
            try:
                guard.require(item.size)
                copy = self._take_one(node, api, item, guard, throttle, catalogue)
            except DiskFullError as exc:
                result.stopped = "disk_full"
                result.code = "disk_full"
                result.failures.append(f"{exc.message}. {exc.details}")
                break
            except DigestMismatch as exc:
                self._quarantine[(node, item.kind, item.id)] = (item.main.size, item.main.sha256)
                result.code = "mismatch"
                result.failures.append(f"{item.kind} {item.id}: {exc.message}. {exc.details}")
                result.left.append({"kind": item.kind, "id": item.id, "reason": "mismatch"})
                continue
            except NodeRefusedError:
                raise
            except (TooManyFailures, NodeUnreachableError) as exc:
                result.code = result.code or "transfer_failed"
                result.failures.append(f"{item.kind} {item.id}: {exc.message}")
                break
            except TransferError as exc:
                result.code = result.code or exc.code
                result.failures.append(f"{item.kind} {item.id}: {exc.message}")
                continue
            collected.add(copy)
            result.fetched += 1
            result.bytes += copy.size
            self.board.update(node, taken=index + 1)
            audit.record(
                "central_backup.collect",
                target=f"node:{node}",
                details={
                    "kind": item.kind,
                    "copy": item.id,
                    "owner": item.owner,
                    "bytes": copy.size,
                    "verified": copy.verified,
                },
            )

    def _take_one(
        self,
        node: str,
        api: NodeApi,
        item: RemoteItem,
        guard: Guard,
        throttle: Throttle,
        catalogue: RemoteCatalogue,
    ) -> Collected:
        """
        Take every file of one copy, then put them in place and nowhere before.

        Args:
            node: The server.
            api: Its API.
            item: The copy.
            guard: The disk guard.
            throttle: The speed limit.
            catalogue: What the server said (its suggested piece).

        Returns:
            The copy, ready to be recorded: its files are in their places.

        Raises:
            TransferError: A file could not be taken (see :meth:`Fetcher.receive`).
        """
        directory = self.layout.copy_dir(node, item.folder, item.kind, item.id)
        label = f"{item.kind}: {item.owner}"
        received: list[Received] = []

        def progress_of(name: str) -> Callable[[int, int], None]:
            def tell(done: int, total: int) -> None:
                self.board.update(node, item=label, file=name, done=done, size=total)

            return tell

        for file in item.files:
            fetcher = Fetcher(
                api,
                throttle=throttle,
                guard=guard,
                piece=catalogue.piece_size,
                sleep=self._sleep or time.sleep,
                should_stop=self._should_stop,
                progress=progress_of(file.name),
                fs=self.fs,
            )
            got = fetcher.receive(item, file, directory)
            if got is None:
                # A sidecar that could not be taken is left out; the copy is its archive.
                if file.role == "archive":
                    raise TransferError(f"{file.name} could not be taken")
                continue
            received.append(got)
        # In place, the archive last: a crash between two renames leaves a sidecar with no archive
        # (nothing offers it) and never an archive that is half the files of its copy.
        for got in sorted(received, key=lambda r: r.role == "archive"):
            self.fs.rename(got.part, self.layout.file_path(directory, got.name))
        files = tuple(CollectedFile(r.name, r.size, r.sha256, r.role) for r in received)
        archive = next(r for r in received if r.role == "archive")
        return Collected(
            node=node,
            kind=item.kind,
            item_id=item.id,
            owner=item.owner,
            folder=item.folder,
            origin=item.origin,
            created_at=item.created_at,
            collected_at=utc_now_iso(),
            size=sum(f.size for f in files),
            files=files,
            verified=archive.verified,
            state=HELD,
            meta=dict(item.meta),
        )

    def _say_held(self, node: str, api: NodeApi, result: PassResult) -> None:
        """
        Tell the server which copies the central holds.

        Args:
            node: The server.
            api: Its API.
            result: The pass, which notes it when the server could not be told.
        """
        held = CollectedStore(self.store).list(node=node)
        refs = [
            (
                copy.kind,
                copy.item_id,
                next((f.sha256 for f in copy.files if f.role == "archive"), None),
            )
            for copy in held
        ]
        try:
            api.say_held(refs)
        except NodeError as exc:
            result.notes.append(f"{node} could not be told what the central holds: {exc.message}")

    def _retire_by_retention(self, node: str, settings: Any, result: PassResult) -> None:
        """
        Let go of what the central's own retention says, and nothing else.

        Args:
            node: The server.
            settings: The knobs.
            result: The pass.
        """
        for copy in expired(
            CollectedStore(self.store).list(node=node), settings, now=self._clock()
        ):
            self.let_go(copy, reason="retention")
            result.let_go += 1

    def let_go(self, copy: Collected, *, reason: str) -> None:
        """
        Let a copy go: its files off the disk, and a record that it was let go.

        The one way a copy leaves the central, for its retention and for its operator alike, so a
        server that still has the copy is not asked for it again.

        Args:
            copy: The copy.
            reason: ``retention`` or ``operator``.
        """
        directory = self.layout.copy_dir(copy.node, copy.folder, copy.kind, copy.item_id)
        self.fs.remove_tree(directory)
        CollectedStore(self.store).retire(copy.node, copy.kind, copy.item_id)
        audit.record(
            "central_backup.delete",
            target=f"node:{copy.node}",
            details={
                "kind": copy.kind,
                "copy": copy.item_id,
                "owner": copy.owner,
                "reason": reason,
            },
        )

    # ------------------------------------------------------------------- failure

    def _failed(
        self,
        node: str,
        result: PassResult,
        states: NodeStates,
        before: Any,
        code: str,
        exc: Exception,
    ) -> PassResult:
        """
        Write down a pass that could not even ask, and tell when it is time to.

        Args:
            node: The server.
            result: The pass.
            states: Where the state is kept.
            before: The state before the pass.
            code: A stable word for what went wrong.
            exc: What went wrong.

        Returns:
            The result, as failed.
        """
        text = str(getattr(exc, "message", exc))
        details = getattr(exc, "details", "")
        full = f"{text}. {details}".strip() if details else text
        result.state = "failed"
        result.code = code
        result.failures.append(full)
        state = states.record_pass(node, ok=False, error=full, code=code)
        self._tell(node, before.last_error_code, state.failures, code, full)
        return result

    def _tell(
        self, node: str, before: str | None, failures: int, code: str | None, text: str | None
    ) -> None:
        """
        Tell the operator, when it is time: at once for a copy that is not being kept, and for the
        rest the third time in a row, once.

        Args:
            node: The server.
            before: The code the last failure had.
            failures: Failures in a row, this one included.
            code: A stable word for this one.
            text: What it said.
        """
        if code is None or text is None:
            return
        time_to_say = (code in _IMMEDIATE and before != code) or failures == NOTIFY_AFTER
        if time_to_say:
            try:
                self._notify(node, code, text)
            except Exception:  # noqa: BLE001 - the boundary of a background thread: say and go on
                _log.exception("Could not tell the operator that collecting from %s failed", node)


def _refusal_code(exc: NodeRefusedError) -> str:
    """
    Args:
        exc: A server's refusal.

    Returns:
        ``ceiling`` for a refusal of this one request (a 403: the server's access ceiling does not
        let its central collect), ``refused`` for a refused token.
    """
    return "ceiling" if getattr(exc, "status_code", 0) == 403 else "refused"


def collected_directory(root: Path, node: str) -> Path:
    """
    Args:
        root: The central's folder of collected copies.
        node: The server.

    Returns:
        The folder all its copies are in.
    """
    return Layout(root).node_dir(node)
