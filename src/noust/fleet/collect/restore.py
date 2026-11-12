# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Giving a copy back: from the central's disk to a server, through the tunnel (item 195).

A copy the central holds goes back to the server it came from, or to another of the fleet (a
replacement, a move), as an upload that resumes and is restored with the way a restore always goes
(:mod:`noust.managers.backup_return`): this module does not restore anything, it carries the file and
asks the server to restore it, as the person who asked, with the sudo mode the central vouches for
them, so the server's own permissions, ceiling and audit trail decide and record it (rule 3).

What it checks, because a restore replaces data:

1. **The copy on the central is whole.** Every file is read and compared with the digest taken when
   it arrived, before a byte is sent: a disk that went wrong in the meantime is found here and named,
   not on a server that has already started replacing what it holds.
2. **The server can receive it.** A server before 4.3 has no routes for it and is told to update, in
   words, before anything is sent.
3. **It arrives whole.** The file is sent in chunks at the offset the server says it holds, a failed
   chunk is asked again from there, and the digest is checked when it completes.
4. **The restore is followed to its end.** The server's own job is read until it finishes and its
   words are the result, verbatim.

Moving a copy between two servers through the central passes it through the central's line, which is
why this is never what a deploy or a migration does by default (it is what the operator asks for).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from noust.core.exceptions import NodeError, NodeRefusedError, NodeUnreachableError, NoustError
from noust.fleet.aggregate import Asker
from noust.fleet.client import NodeClient
from noust.fleet.collect.layout import Layout
from noust.fleet.collect.records import Collected
from noust.fleet.collect.throttle import Throttle
from noust.fleet.tunnels import TRANSFER

__all__ = ["GiveBack", "RestoreError", "damaged_files"]

#: Consecutive failures of a chunk before the transfer is given up on.
RETRIES = 5

#: Seconds waited between two attempts of a chunk, and between two looks at the server's job.
RETRY_PAUSE = 2.0
FOLLOW_PAUSE = 3.0

#: Bytes read from the central's disk and sent at a time, when the server does not say.
CHUNK = 512 * 1024

_DONE = {"completed", "failed", "cancelled"}
_BASE = "/api/backup-collection"


class RestoreError(NoustError):
    """A copy could not be given back; the words say why and what to do."""


def damaged_files(copy: Collected, layout: Layout) -> list[str]:
    """
    Find the files of a copy that are not what was taken.

    Args:
        copy: The copy.
        layout: Where the central keeps its copies.

    Returns:
        A sentence for each file that is missing or whose digest is not the one recorded; empty when
        the copy is whole.
    """
    directory = layout.copy_dir(copy.node, copy.folder, copy.kind, copy.item_id)
    found: list[str] = []
    for file in copy.files:
        path = layout.file_path(directory, file.name)
        if not path.is_file() or path.is_symlink():
            found.append(f"{file.name} is missing")
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                digest.update(block)
        if digest.hexdigest() != file.sha256 or path.stat().st_size != file.size:
            found.append(f"{file.name} is not what was taken (its digest changed)")
    return found


@dataclass
class GiveBack:
    """
    One copy going back to one server.

    Args:
        copy: The copy the central holds.
        target: The server it goes to (the one it came from, or another).
        layout: Where the central keeps its copies.
        asker: Who asked, as every call to a server says it.
        throttle: Paces the reads from the central's disk.
        client_for: Makes the client of a server's transfer connection.
        log: Told what is happening, for the job's log.
        step: Told ``(what, percent)``.
        sleep: Waits (tests record it).
        should_stop: Whether the console is stopping.
    """

    copy: Collected
    target: str
    layout: Layout
    asker: Asker
    throttle: Throttle
    client_for: Callable[[str], NodeClient] = lambda node: NodeClient(
        node, channel=TRANSFER, timeout=180.0
    )
    log: Callable[[str], None] = lambda _message: None
    step: Callable[[str, int], None] = lambda _what, _percent: None
    sleep: Callable[[float], None] = time.sleep
    should_stop: Callable[[], bool] = lambda: False

    # ------------------------------------------------------------------- calls

    def _call(self, client: NodeClient, method: str, path: str, **kw: Any) -> Any:
        """
        Make one call as the person who asked.

        Args:
            client: The server's client.
            method: The HTTP method.
            path: The path on the server.
            **kw: Query parameters, a body or content.

        Returns:
            The response.
        """
        return client.request(method, path, **self.asker.headers(), **kw)

    def _failed(self, what: str, response: Any) -> RestoreError:
        """
        Args:
            what: What was asked.
            response: What the server answered.

        Returns:
            The error, with the server's own words.
        """
        try:
            text = response.text[:2000]
        except (UnicodeDecodeError, AttributeError):
            text = ""
        return RestoreError(
            f"{self.target} answered {response.status_code} to {what}", details=text, output=text
        )

    # --------------------------------------------------------------------- run

    def run(
        self,
        *,
        target_args: Mapping[str, Any] | None = None,
        options: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Give the copy back and follow the restore to its end.

        Args:
            target_args: Where it goes on the server: ``domain``, or ``engine``, ``database`` and
                ``new_name``.
            options: What the operator confirmed (``restore_env``, ``safety_backup``,
                ``drop_existing``, ``schema_changed_ok``).

        Returns:
            What the server's restore did, with the server's name.

        Raises:
            RestoreError: The copy on the central is damaged, the server cannot receive it, the file
                did not arrive, or the restore failed (the server's words are in it).
            NodeRefusedError: The server refused the central's token.
        """
        self.step("Checking the copy the central holds", 2)
        bad = damaged_files(self.copy, self.layout)
        if bad:
            raise RestoreError(
                f"The copy the central holds of {self.copy.owner} is damaged",
                details="; ".join(bad)
                + ". Nothing was sent. Take another copy of it, or let the server's own be taken "
                "again once it is deleted here.",
            )
        client = self.client_for(self.target)
        self._require_receiver(client)
        directory = self.layout.copy_dir(
            self.copy.node, self.copy.folder, self.copy.kind, self.copy.item_id
        )
        main = next(f for f in self.copy.files if f.role == "archive")
        upload = self._send(
            client, self.layout.file_path(directory, main.name), main.name, main.sha256
        )
        sidecar = self._sidecar(directory)
        self.step(f"Restoring on {self.target}", 85)
        response = self._call(
            client,
            "POST",
            f"{_BASE}/restore",
            json={
                "upload_id": upload,
                "kind": self.copy.kind,
                "sidecar": sidecar,
                "meta": dict(self.copy.meta),
                "target": dict(target_args or {}),
                "options": dict(options or {}),
            },
        )
        if response.status_code >= 400:
            raise self._failed("the restore", response)
        job_id = str(response.json().get("job_id") or "")
        result = self._follow(client, job_id)
        return {"node": self.target, "kind": self.copy.kind, "copy": self.copy.item_id, **result}

    def _require_receiver(self, client: NodeClient) -> None:
        """
        Check the server has the routes a copy comes back through.

        Args:
            client: The server's client.

        Raises:
            RestoreError: A server before 4.3, which has none.
        """
        response = self._call(client, "GET", f"{_BASE}/held")
        if response.status_code == 404:
            raise RestoreError(
                f"{self.target} cannot receive a copy yet",
                details="It runs a Noust before 4.3, which has no routes for a copy that comes "
                f"back. Update it (noust fleet run noust_update --nodes {self.target}), then give "
                "the copy back. Nothing was sent.",
            )
        if response.status_code >= 400:
            raise self._failed("the list of the centrals that collect from it", response)

    def _sidecar(self, directory: Path) -> dict[str, Any] | None:
        """
        Read the sidecar the central holds of the copy, if it holds one.

        Args:
            directory: The copy's folder.

        Returns:
            What it says, or None.
        """
        sidecar = next((f for f in self.copy.files if f.role == "sidecar"), None)
        if sidecar is None:
            return None
        try:
            data = json.loads(self.layout.file_path(directory, sidecar.name).read_text("utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _send(self, client: NodeClient, path: Path, name: str, digest: str) -> str:
        """
        Send a file as an upload that resumes.

        Args:
            client: The server's client.
            path: The file on the central's disk.
            name: Its name.
            digest: Its SHA-256.

        Returns:
            The finished upload's identifier.

        Raises:
            RestoreError: The server refused it or it kept failing; the upload is thrown away.
        """
        size = path.stat().st_size
        started = self._call(
            client,
            "POST",
            f"{_BASE}/returns",
            json={"size": size, "sha256": digest, "file_name": name},
        )
        if started.status_code >= 400:
            raise self._failed("the start of the upload", started)
        info = started.json()
        upload, chunk = str(info["id"]), int(info.get("chunk_size") or CHUNK)
        received = int(info.get("received") or 0)
        failures = 0
        try:
            with path.open("rb") as handle:
                while received < size:
                    if self.should_stop():
                        raise RestoreError("The console is stopping; give the copy back again")
                    handle.seek(received)
                    data = handle.read(min(chunk, size - received))
                    try:
                        sent = self._call(
                            client,
                            "PUT",
                            f"{_BASE}/returns/{upload}",
                            params={"offset": received},
                            content=data,
                        )
                    except NodeRefusedError:
                        raise
                    except NodeUnreachableError:
                        sent = None
                    if sent is not None and sent.status_code == 200:
                        received = int(sent.json()["received"])
                        failures = 0
                        self.throttle.take(len(data))
                        self.step(
                            f"Sending {name} to {self.target}: {received * 100 // size}%",
                            5 + received * 75 // size,
                        )
                        continue
                    failures += 1
                    if failures > RETRIES:
                        raise RestoreError(
                            f"Sending {name} to {self.target} failed {failures} times in a row",
                            details="" if sent is None else self._failed("a chunk", sent).details,
                        )
                    self.log(f"A chunk at {received} failed; asking where {self.target} is")
                    self.sleep(RETRY_PAUSE)
                    standing = self._call(client, "GET", f"{_BASE}/returns/{upload}")
                    if standing.status_code == 200:
                        received = int(standing.json()["received"])
            done = self._call(client, "POST", f"{_BASE}/returns/{upload}/complete")
            if done.status_code >= 400:
                raise self._failed("closing the upload", done)
        except (RestoreError, NodeError):
            # Not left on the server's disk: the whole copy.
            try:
                self._call(client, "DELETE", f"{_BASE}/returns/{upload}")
            except NodeError:
                self.log(f"The upload {upload} could not be removed from {self.target}")
            raise
        return upload

    def _follow(self, client: NodeClient, job_id: str) -> dict[str, Any]:
        """
        Follow the server's restore until it ends.

        Args:
            client: The server's client.
            job_id: The server's job.

        Returns:
            What the job answered as its result.

        Raises:
            RestoreError: The job failed or was cancelled; its own words.
        """
        if not job_id:
            raise RestoreError(f"{self.target} did not say which job restores the copy")
        last = ""
        while True:
            if self.should_stop():
                raise RestoreError(
                    "The console is stopping",
                    details=f"The restore goes on at {self.target} as job {job_id}.",
                )
            response = self._call(client, "GET", f"/api/jobs/{job_id}")
            if response.status_code >= 400:
                raise self._failed("the restore job", response)
            job = response.json()
            progress = f"{job.get('current_step') or job.get('status')}"
            if progress != last:
                self.log(f"{self.target}: {progress}")
                last = progress
            status = str(job.get("status") or "")
            if status in _DONE:
                if status != "completed":
                    raise RestoreError(
                        f"The restore on {self.target} {status}",
                        details=str(job.get("error") or ""),
                        output=str(job.get("error") or "") or None,
                    )
                self.step(f"Restored on {self.target}", 100)
                result = job.get("result")
                return {"result": result if isinstance(result, dict) else {}}
            self.sleep(FOLLOW_PAUSE)
