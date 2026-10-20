# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Fake code hosts for the commit status and pull request comment tests.

One object answers for every host (GitLab, Gitea/Forgejo, Gogs, Bitbucket Cloud) behind the
``opener`` seam of :class:`noust.deployers.importers.remote.RemoteClient`, so no socket is
opened. It keeps what each endpoint was given, in the order it was given, and answers as the
real hosts do: a created comment gets an id, an edit of a comment that was deleted is a 404, and a
token without the scope to write is a 403.
"""

from __future__ import annotations

import json
import urllib.error
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

from tests.oidc_support import FakeResponse


@dataclass
class Call:
    """One request a host received."""

    method: str
    host: str
    path: str
    headers: dict[str, str]
    body: dict[str, Any]
    form: dict[str, str] = field(default_factory=dict)


class FakeForges:
    """
    Every code host, in one object.

    Attributes:
        calls: Everything asked, in order.
        comments: ``(host, repository path, request number)`` to the ids of its comments.
        deleted: Comment ids a person deleted on the host (editing one is a 404).
        write_allowed: Whether the credential may write at all.
    """

    def __init__(self) -> None:
        self.calls: list[Call] = []
        self.comments: dict[tuple[str, str, int], list[str]] = {}
        self.deleted: set[str] = set()
        self.write_allowed = True
        self.offline = False
        self._next_comment = 1000

    # ------------------------------------------------------------ the opener

    def opener(self, request: Any, timeout: float | None = None) -> FakeResponse:
        parts = urlsplit(request.full_url)
        data = request.data.decode() if request.data else ""
        headers = {key.lower(): value for key, value in request.headers.items()}
        body: dict[str, Any] = {}
        form: dict[str, str] = {}
        if data and headers.get("content-type", "").startswith("application/json"):
            body = json.loads(data)
        elif data:
            form = {key: values[0] for key, values in parse_qs(data).items()}
        method = request.get_method()
        call = Call(method, parts.netloc, parts.path, headers, body, form)
        self.calls.append(call)
        if self.offline:
            raise urllib.error.URLError("connection refused")
        if method != "GET" and not self.write_allowed:
            raise self._refuse(request, 403)
        return self._route(request, call)

    def _refuse(self, request: Any, status: int) -> urllib.error.HTTPError:
        return urllib.error.HTTPError(request.full_url, status, "refused", {}, None)  # type: ignore[arg-type]

    def _json(self, body: Any, status: int = 200) -> FakeResponse:
        return FakeResponse(json.dumps(body).encode(), status)

    def _created(self, host: str, repository: str, number: int) -> str:
        self._next_comment += 1
        comment_id = str(self._next_comment)
        self.comments.setdefault((host, repository, number), []).append(comment_id)
        return comment_id

    def _route(self, request: Any, call: Call) -> FakeResponse:
        path = call.path
        # GitLab: /api/v4/projects/<url-encoded path>/...
        if path.startswith("/api/v4/projects/"):
            rest = path[len("/api/v4/projects/") :]
            project, _, tail = rest.partition("/")
            repository = project.replace("%2F", "/")
            if tail.startswith("statuses/"):
                return self._json({"id": 1, "status": call.form.get("state")})
            if tail.startswith("merge_requests/") and "/notes" in tail:
                number = int(tail.split("/")[1])
                note = tail.split("/notes", 1)[1].strip("/")
                if call.method == "POST":
                    return self._json({"id": int(self._created(call.host, repository, number))})
                if note in self.deleted:
                    raise self._refuse(request, 404)
                return self._json({"id": int(note)})
        # Gitea / Forgejo / Gogs: /api/v1/repos/<owner>/<repo>/...
        if path.startswith("/api/v1/repos/"):
            owner, name, *tail = path[len("/api/v1/repos/") :].split("/")
            repository = f"{owner}/{name}"
            if tail[:1] == ["statuses"]:
                return self._json({"id": 1, "state": call.body.get("state")})
            if tail[:1] == ["issues"]:
                if tail[1:2] == ["comments"]:
                    if tail[2] in self.deleted:
                        raise self._refuse(request, 404)
                    return self._json({"id": int(tail[2])})
                number = int(tail[1])
                return self._json({"id": int(self._created(call.host, repository, number))})
        # Bitbucket Cloud: /2.0/repositories/<workspace>/<repo>/...
        if path.startswith("/2.0/repositories/"):
            workspace, name, *tail = path[len("/2.0/repositories/") :].split("/")
            repository = f"{workspace}/{name}"
            if tail[:1] == ["commit"]:
                return self._json({"key": call.body.get("key"), "state": call.body.get("state")})
            if tail[:1] == ["pullrequests"]:
                number = int(tail[1])
                if len(tail) > 3:
                    if tail[3] in self.deleted:
                        raise self._refuse(request, 404)
                    return self._json({"id": int(tail[3])})
                return self._json({"id": int(self._created(call.host, repository, number))})
        raise self._refuse(request, 404)

    # ---------------------------------------------------------- what to read

    def writes(self, host: str | None = None) -> list[Call]:
        """
        Returns:
            The requests that changed something, optionally of one host.
        """
        return [
            call
            for call in self.calls
            if call.method != "GET" and (host is None or call.host == host)
        ]
