# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A server's API for its copies, as the collector uses it (item 195).

The collector asks three things of a server: what copies it has, a range of one file, and (once it
has some) to be told which the central holds. :class:`NodeApi` is those three, so the engine of the
collector is tested without a tunnel and a real HTTP server, and a server that is not 4.3 has its own
way of answering them:

- :class:`ClientApi` is a 4.3 server: ``/api/backup-collection``, over the central's own connection
  for transfers, asking for the one permission it needs (``backups.collect``) and no other.
- :class:`LegacyApi` is a 4.2 server, which has no such routes: only its database dumps can be taken,
  by the routes they always had (``/api/databases/backups`` and its ``/data``), and the answer is put
  into the same shape and judged by the same rules as any other, so a 4.2 server is not trusted more.
  Those routes need sudo mode, which the central vouches for on behalf of the operator who turned
  collecting on for that server (the confirmation that turning it on asked for), and the server's own
  ceiling still decides.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import quote

from noust.core.exceptions import NodeError
from noust.fleet.client import NodeClient
from noust.fleet.collect.names import RemoteCatalogue, RemoteFile, RemoteItem, parse_catalogue
from noust.fleet.tunnels import TRANSFER

__all__ = [
    "ACTOR",
    "PIECE_TIMEOUT",
    "ClientApi",
    "LegacyApi",
    "NodeApi",
    "NodeUnsupported",
    "client_api_for",
]

#: Who a server's audit trail says is collecting.
ACTOR = "noust-collect"

#: What the collector asks of a server: reading the copies and saying which it holds. Nothing else.
PERMISSIONS = ("backups.collect",)

#: What a 4.2 server wants for its dump downloads.
LEGACY_PERMISSIONS = ("databases.manage",)

#: Seconds a request for a piece may take: a piece through a slow line is not a failed request.
PIECE_TIMEOUT = 180.0


class NodeUnsupported(NodeError):
    """The server does not have the routes for what was asked (a server before 4.3)."""


class NodeApi(Protocol):
    """What the collector asks of a server."""

    #: Whether this is a server before 4.3, which can only give its database dumps.
    legacy: bool

    def list(self, *, known: str | None = None) -> RemoteCatalogue:
        """
        Ask what copies the server has.

        Args:
            known: The fingerprint the central already holds; the server answers "unchanged" when
                it is still the list's.

        Returns:
            What the server said, judged.

        Raises:
            NodeUnsupported: The server has no such routes.
            NodeError: Any other failure, with the server's own words.
        """
        ...

    def read(self, item: RemoteItem, file: RemoteFile, offset: int, length: int) -> bytes:
        """
        Read a range of one file.

        Args:
            item: The copy.
            file: The file, as the server listed it.
            offset: Where to start.
            length: How many bytes at most.

        Returns:
            The bytes; fewer at the end, none past it.

        Raises:
            NodeError: The server refused or could not.
        """
        ...

    def say_held(self, held: Sequence[tuple[str, str, str | None]]) -> None:
        """
        Tell the server which copies the central holds, in place of what it said before.

        Args:
            held: ``(kind, identifier, sha256)`` of each.

        Raises:
            NodeError: The server refused or could not.
        """
        ...


def _error(node: str, what: str, response: Any) -> NodeError:
    """
    Build the error for an answer that was not a success, with the server's words.

    Args:
        node: The server.
        what: What was asked.
        response: The httpx response.

    Returns:
        The error to raise.
    """
    try:
        text = response.text[:2000]
    except (UnicodeDecodeError, AttributeError):
        text = ""
    return NodeError(f"{node} answered {response.status_code} to {what}", details=text, output=text)


class ClientApi:
    """
    A 4.3 server, over the central's connection for transfers.

    Args:
        client: The central's client for that server, on the transfer channel.
        heartbeat: Seconds between this central's calls, said in each when it can promise to keep
            calling.
    """

    legacy = False

    def __init__(self, client: NodeClient, *, heartbeat: int | None = None) -> None:
        self.client = client
        self._heartbeat = heartbeat

    def _call(self, method: str, path: str, **kw: Any) -> Any:
        """
        Make one call with the permission collecting needs and no other.

        Args:
            method: The HTTP method.
            path: The path on the server.
            **kw: Query parameters or a body.

        Returns:
            The response.
        """
        return self.client.request(
            method,
            path,
            actor=ACTOR,
            actor_scope="read",
            actor_permissions=PERMISSIONS,
            heartbeat=self._heartbeat,
            **kw,
        )

    def list(self, *, known: str | None = None) -> RemoteCatalogue:
        """
        Ask what copies the server has.

        Args:
            known: The fingerprint the central already holds.

        Returns:
            What the server said, judged.

        Raises:
            NodeUnsupported: The server has no such route (a 404).
            NodeError: Any other failure.
        """
        response = self._call(
            "GET", "/api/backup-collection", params={"known": known} if known else None
        )
        if response.status_code == 404:
            raise NodeUnsupported(f"{self.client.node} has no backup-collection routes")
        if response.status_code >= 400:
            raise _error(self.client.node, "GET /api/backup-collection", response)
        try:
            return parse_catalogue(response.json())
        except ValueError as exc:
            raise NodeError(
                f"{self.client.node} answered the list of its copies with something that is not JSON"
            ) from exc

    def read(self, item: RemoteItem, file: RemoteFile, offset: int, length: int) -> bytes:
        """
        Read a range of one file.

        Args:
            item: The copy.
            file: The file.
            offset: Where to start.
            length: How many bytes at most.

        Returns:
            The bytes.

        Raises:
            NodeError: The server refused or could not.
        """
        response = self._call(
            "GET",
            "/api/backup-collection/files",
            params={
                "kind": item.kind,
                "item": item.id,
                "name": file.name,
                "offset": offset,
                "length": length,
            },
        )
        if response.status_code != 200:
            raise _error(self.client.node, f"the file {file.name}", response)
        content: bytes = response.content
        return content

    def say_held(self, held: Sequence[tuple[str, str, str | None]]) -> None:
        """
        Tell the server which copies the central holds.

        Args:
            held: ``(kind, identifier, sha256)`` of each.

        Raises:
            NodeError: The server refused or could not.
        """
        response = self._call(
            "PUT",
            "/api/backup-collection/held",
            json={"items": [{"kind": k, "id": i, "sha256": s} for k, i, s in held]},
        )
        if response.status_code >= 400:
            raise _error(self.client.node, "PUT /api/backup-collection/held", response)


def _slug(text: str) -> str:
    """
    Args:
        text: What a copy is of.

    Returns:
        The same as one plain name.
    """
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in text).strip(".-")
    return cleaned[:120] or "copy"


def _utc(value: Any) -> str:
    """
    Args:
        value: A time a 4.2 server wrote.

    Returns:
        It in UTC, ISO 8601; the epoch when it cannot be read (so the copy is rejected as nothing).
    """
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return "1970-01-01T00:00:00+00:00"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


class LegacyApi:
    """
    A server before 4.3: only its database dumps, by the routes they always had.

    Args:
        client: The central's client for that server, on the transfer channel.
        heartbeat: Seconds between this central's calls, when it can promise to keep calling.
    """

    legacy = True

    def __init__(self, client: NodeClient, *, heartbeat: int | None = None) -> None:
        self.client = client
        self._heartbeat = heartbeat

    def list(self, *, known: str | None = None) -> RemoteCatalogue:
        """
        List the server's dumps in the shape a 4.3 server lists its copies.

        Args:
            known: Ignored: a 4.2 server has no fingerprint of its list.

        Returns:
            The dumps, judged by the same rules as any other list.

        Raises:
            NodeError: The server refused or could not.
        """
        response = self.client.request(
            "GET",
            "/api/databases/backups",
            actor=ACTOR,
            actor_scope="read",
            actor_permissions=("databases.read",),
            heartbeat=self._heartbeat,
        )
        if response.status_code >= 400:
            raise _error(self.client.node, "GET /api/databases/backups", response)
        try:
            body = response.json()
        except ValueError as exc:
            raise NodeError(
                f"{self.client.node} answered its dumps with something not JSON"
            ) from exc
        items: list[dict[str, Any]] = []
        for dump in body.get("backups", []) if isinstance(body, dict) else []:
            if not isinstance(dump, dict):
                continue
            engine, name = str(dump.get("engine", "")), str(dump.get("name", ""))
            database = str(dump.get("database", ""))
            kind = str(dump.get("kind", "unknown"))
            items.append(
                {
                    "kind": "database",
                    "id": f"{engine}:{name}",
                    "owner": f"{engine}/{database}",
                    "folder": _slug(f"{engine}.{database}"),
                    "created_at": _utc(dump.get("created")),
                    "origin": kind if kind in ("scheduled", "safety") else "manual",
                    "sealed": False,
                    "files": [
                        {
                            "name": name,
                            "size": dump.get("size"),
                            "sha256": dump.get("sha256"),
                            "role": "archive",
                        }
                    ],
                    "meta": {"engine": engine, "database": database},
                    "blocked": None,
                }
            )
        digest = hashlib.sha256(
            repr(sorted((i["id"], i["files"][0]["size"]) for i in items)).encode()
        )
        return parse_catalogue({"fingerprint": digest.hexdigest()[:24], "items": items})

    def read(self, item: RemoteItem, file: RemoteFile, offset: int, length: int) -> bytes:
        """
        Read a range of a dump by the route it always had.

        Args:
            item: The copy (a dump).
            file: Its file.
            offset: Where to start.
            length: How many bytes at most.

        Returns:
            The bytes.

        Raises:
            NodeError: The server refused or could not.
        """
        response = self.client.request(
            "GET",
            f"/api/databases/backups/{quote(file.name, safe='')}/data",
            params={"engine": item.meta.get("engine", ""), "offset": offset, "length": length},
            actor=ACTOR,
            actor_scope="admin",
            actor_permissions=LEGACY_PERMISSIONS,
            elevated=True,
            heartbeat=self._heartbeat,
        )
        if response.status_code != 200:
            raise _error(self.client.node, f"the dump {file.name}", response)
        content: bytes = response.content
        return content

    def say_held(self, held: Sequence[tuple[str, str, str | None]]) -> None:
        """
        A server before 4.3 cannot be told which copies the central holds; nothing is sent.

        Args:
            held: Ignored.
        """


def client_api_for(node: str, *, heartbeat: int | None = None) -> ClientApi:
    """
    Make the client of a server's transfer connection.

    Args:
        node: The server.
        heartbeat: Seconds between this central's calls, when it can promise to keep calling.

    Returns:
        The API.
    """
    return ClientApi(NodeClient(node, channel=TRANSFER, timeout=PIECE_TIMEOUT), heartbeat=heartbeat)
