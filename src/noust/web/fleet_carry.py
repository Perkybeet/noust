# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Carry a big file from one server to another through the central, in chunks that can resume.

A release a build server made (:mod:`noust.web.fleet_ship_actions`) and a dump of a database that
moves to another server (:mod:`noust.web.fleet_database_actions`) are the same problem: hundreds of
megabytes, or gigabytes, between two servers that only the central reaches, each through its own
tunnel, and a request through a tunnel is cut after a few minutes. So the file is never held whole
and never written on the central. It is read from the first server by ranges and written to the
second as an upload that can resume from where it stopped; a chunk that fails is asked again from
the offset the receiving server says it holds, and an upload that cannot be finished is dropped
from the receiving server, not left on its disk (rule 3: the one implementation of this).

Both servers speak their own API, with the operator's identity and the node's own permissions;
nothing here knows what the file is.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from noust.core.exceptions import NoustError
from noust.fleet.aggregate import Failure, NodeSource, decode_answer
from noust.web.fleet_jobs import ActionFailed, NodeRun

__all__ = [
    "DOWNLOAD_CHUNK",
    "RETRY_PAUSE",
    "TRANSFER_RETRIES",
    "UPLOAD_CHUNK",
    "Carriage",
    "carry",
]

#: Bytes read from the first server at a time; a node refuses more than 8 MiB.
DOWNLOAD_CHUNK = 4 * 1024 * 1024

#: Bytes sent to the second server at a time, when it does not say (it says).
UPLOAD_CHUNK = 512 * 1024

#: Consecutive transfer failures before the server is given up on; each one resumes from the
#: offset the receiving server says it holds.
TRANSFER_RETRIES = 5

#: Seconds waited between two attempts of a chunk.
RETRY_PAUSE = 2.0


@dataclass(frozen=True)
class Carriage:
    """
    What is carried, from where, and to which upload.

    Attributes:
        source: The server the file is read from.
        read_path: Where it reads a range: ``GET`` with ``offset`` and ``length``.
        size: The file's size in bytes, as the first server reports it.
        start_path: Where an upload is started on the run's own server: ``POST``, which answers
            ``id``, ``received`` and ``chunk_size``.
        start_body: What starting it sends (the size, the digest, a name).
        upload_path: The path of one upload, with ``{id}`` where its identifier goes: ``GET``
            (how much it holds), ``PUT`` (a chunk at ``offset``) and ``DELETE``; ``/complete`` is
            appended to close it.
        noun: What it is, for the messages: ``the release``.
        gone_hint: What to do when the first server's copy ends early.
        read_params: Query parameters the read carries besides the range.
    """

    source: NodeSource
    read_path: str
    size: int
    start_path: str
    start_body: Mapping[str, Any]
    upload_path: str
    noun: str
    gone_hint: str
    read_params: Mapping[str, Any] = field(default_factory=dict)


def _chunks(data: bytes, size: int) -> Iterator[bytes]:
    """
    Cut bytes into the pieces a chunk may carry.

    Args:
        data: Bytes.
        size: The most one piece holds.

    Yields:
        The pieces, in order.
    """
    for start in range(0, len(data), size):
        yield data[start : start + size]


def carry(run: NodeRun, carriage: Carriage) -> str:
    """
    Send the file to the run's server, chunk by chunk, resuming after a failure.

    Args:
        run: The server it goes to.
        carriage: What is carried and where.

    Returns:
        The finished upload's identifier, ready to be used.

    Raises:
        ActionFailed: The first server's copy ended early, or the transfer kept failing; the
            upload is dropped from the receiving server.
    """
    source = carriage.source
    size = carriage.size
    started = run.call("POST", carriage.start_path, body=dict(carriage.start_body))
    upload_id = str(started["id"])
    upload = carriage.upload_path.format(id=upload_id)
    piece = int(started.get("chunk_size") or UPLOAD_CHUNK)
    received = int(started.get("received") or 0)
    failures = 0
    try:
        while received < size:
            run.step(f"Sending {carriage.noun} to {run.name}: {received * 100 // size}%")
            data = run.send(
                "GET",
                carriage.read_path,
                params={
                    **carriage.read_params,
                    "offset": received,
                    "length": min(DOWNLOAD_CHUNK, size - received),
                },
                on=source,
            )
            if data.status_code != 200 or not data.content:
                raise ActionFailed(
                    f"{source.name}'s copy of {carriage.noun} ended at {received} of {size} bytes",
                    details=carriage.gone_hint,
                    output=data.text[:2000] if data.status_code != 200 else None,
                )
            try:
                for chunk in _chunks(data.content, piece):
                    answer = run.send("PUT", upload, params={"offset": received}, content=chunk)
                    decoded = decode_answer(run.name, "PUT", carriage.upload_path, answer)
                    received = int(decoded["received"])
                failures = 0
            except Failure as exc:
                failures += 1
                if failures > TRANSFER_RETRIES:
                    raise ActionFailed(
                        f"Sending {carriage.noun} to {run.name} failed {failures} times in a row: "
                        f"{exc.message}",
                        output=exc.output,
                    ) from exc
                run.log(f"Chunk at {received} failed ({exc.message}); resuming")
                run.sleep(RETRY_PAUSE)
                # Where the receiving server stands is where the next range is read from.
                standing = run.call("GET", upload)
                received = int(standing["received"])
        run.call("POST", f"{upload}/complete")
    except (Failure, NoustError):
        # Not left on the receiving server's disk: the whole file.
        try:
            run.send("DELETE", upload)
        except Failure:
            run.log(f"The upload {upload_id} could not be removed from {run.name}")
        raise
    return upload_id
