# Copyright (c) 2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A fake copy of an application on a fake server, for the tests of an application on several servers.

:class:`Shop` answers the calls the fleet makes to a server about one application (list it,
deploy a commit, read the deployment, list its releases, activate one, export it, import it) and
keeps the state those calls change, so a rollout can be followed from the first server to the
last, and put back.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from tests.fleet_views_support import Node

DOMAIN = "shop.example.com"


@dataclass
class Upstream:
    """The repository the servers deploy from: where its branch is now."""

    head: str = "aaa1111"


@dataclass
class Shop:
    """
    One server's copy of an application.

    Attributes:
        node: The fake server it lives on.
        upstream: The repository, shared by every copy.
        domain: The application.
        commit: What it serves.
        layout: ``releases`` (can be put back instantly) or ``inplace``.
        status: What the server says it is doing.
        fail_deploy: When set, a deploy ends failed with this error.
        fail_health: When set, a deploy succeeds but the application is not serving after.
        schema_changed: Going back past the last deployment is refused for a schema change.
        deploys: Every body the deploy endpoint received.
        activations: Every release activated, with the query it came with.
        releases: The releases, newest first.
        deployments: Deployment id to its commit.
    """

    node: Node
    upstream: Upstream
    domain: str = DOMAIN
    commit: str = "aaa1111"
    layout: str = "releases"
    status: str = "running"
    fail_deploy: str | None = None
    fail_health: bool = False
    schema_changed: bool = False
    deploys: list[dict[str, Any]] = field(default_factory=list)
    activations: list[str] = field(default_factory=list)
    releases: list[dict[str, Any]] = field(default_factory=list)
    deployments: dict[int, str] = field(default_factory=dict)
    imports: list[dict[str, Any]] = field(default_factory=list)
    exports: list[dict[str, str]] = field(default_factory=list)
    image: str | None = None
    settings_changes: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.releases = [self._release("r1", self.commit, active=True)]
        self.install()

    @staticmethod
    def _release(release_id: str, commit: str, *, active: bool) -> dict[str, Any]:
        return {
            "id": release_id,
            "commit": commit,
            "created_at": "2026-10-10T10:00:00+00:00",
            "activated_at": None,
            "status": "active" if active else "superseded",
            "active": active,
            "on_disk": True,
        }

    # -- what the server shows -------------------------------------------------------------

    def row(self) -> dict[str, Any]:
        """Returns: The application as the server's list shows it."""
        return {
            "name": self.domain,
            "domain": self.domain,
            "status": "failed" if self.fail_health and self.commit != "aaa1111" else self.status,
            "branch": "main",
            "layout": self.layout,
            "app_type": "nodejs",
            "port": 3000,
            "last_deployment": {
                "id": max(self.deployments, default=0) or 1,
                "status": "success",
                "git_commit": self.commit,
                "finished_at": "2026-10-10T10:00:00+00:00",
            },
        }

    def install(self) -> None:
        """Make the fake server answer for this application."""
        self.node.apps = [self.row()]
        self.node.handlers[("GET", "/api/apps")] = self._list
        self.node.handlers[("POST", f"/api/apps/{self.domain}/deploy")] = self._deploy
        self.node.handlers[("GET", f"/api/apps/{self.domain}/releases")] = self._list_releases
        self.node.handlers[("GET", f"/api/apps/{self.domain}/export")] = self._export
        if self.image is not None:
            self.node.handlers[("GET", f"/api/apps/{self.domain}/container")] = self._container
            self.node.handlers[("PUT", f"/api/apps/{self.domain}/container")] = self._set_container

    def _container(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"domain": self.domain, "method": "image", "image": self.image}
        )

    def _set_container(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.settings_changes.append(body)
        self.image = body.get("image", self.image)
        return self._container(request)

    def _list(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"apps": [self.row()], "total": 1})

    def _list_releases(self, request: httpx.Request) -> httpx.Response:
        if self.layout != "releases":
            return httpx.Response(409, json={"detail": "deployed in place and has no releases"})
        return httpx.Response(
            200, json={"domain": self.domain, "items": self.releases, "total": len(self.releases)}
        )

    def _deploy(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.deploys.append(body)
        wanted = body.get("commit") or self.upstream.head
        if body.get("image_tag") and self.image is not None:
            repository = (
                self.image.rsplit(":", 1)[0] if ":" in self.image.rsplit("/", 1)[-1] else self.image
            )
            self.image = f"{repository}:{body['image_tag']}"
            wanted = f"image-{body['image_tag']}"
        elif not body.get("force") and not body.get("commit") and wanted == self.commit:
            return httpx.Response(
                200,
                json={
                    "message": "Nothing new to deploy",
                    "results": [{"domain": self.domain, "status": "nothing_new"}],
                },
            )
        deployment = max(self.deployments, default=0) + 1
        if self.fail_deploy:
            queued = self.node.queue(status="failed", error=self.fail_deploy)
        else:
            previous = next(r for r in self.releases if r["active"])
            previous["active"] = False
            previous["status"] = "superseded"
            release_id = f"r{len(self.releases) + 1}"
            self.releases.insert(0, self._release(release_id, wanted, active=True))
            self.commit = wanted
            self.deployments[deployment] = wanted
            self.node.handlers[("GET", f"/api/deployments/{deployment}")] = self._deployment(
                deployment
            )
            self.node.handlers[
                ("POST", f"/api/apps/{self.domain}/releases/{previous['id']}/activate")
            ] = self._activate(previous["id"])
            queued = self.node.queue(
                result={"domain": self.domain, "status": "updated", "deployment_id": deployment}
            )
        job_id = json.loads(queued.content)["job_id"]
        return httpx.Response(
            202,
            json={
                "message": "Update job created",
                "results": [{"domain": self.domain, "status": "queued", "job_id": job_id}],
            },
        )

    def _deployment(self, deployment: int):
        def answer(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "id": deployment,
                    "domain": self.domain,
                    "status": "success",
                    "triggered_by": "panel",
                    "git_commit": self.deployments[deployment],
                    "has_log": True,
                },
            )

        return answer

    def _activate(self, release_id: str):
        def answer(request: httpx.Request) -> httpx.Response:
            self.activations.append(f"{release_id}?{request.url.query.decode()}")
            if self.schema_changed:
                return httpx.Response(
                    409,
                    json={
                        "error": "schema_changed",
                        "detail": "Going back passes a deployment that changed the database schema",
                        "hint": "Confirm with schema_changed_ok.",
                    },
                )
            current = next(r for r in self.releases if r["active"])
            target = next(r for r in self.releases if r["id"] == release_id)
            current["active"] = False
            target["active"] = True
            self.commit = target["commit"]
            return httpx.Response(
                200,
                json={
                    "domain": self.domain,
                    "release_id": release_id,
                    "previous_id": current["id"],
                    "changed": True,
                    "rolled_back": True,
                    "deployment_id": None,
                },
            )

        return answer

    # -- copying ---------------------------------------------------------------------------

    def _export(self, request: httpx.Request) -> httpx.Response:
        with_secrets = request.url.params.get("with_secrets") == "true"
        self.exports.append({"with_secrets": str(with_secrets)})
        return httpx.Response(
            200,
            json={
                "format": "wasm-app",
                "version": 1,
                "secrets_included": with_secrets,
                "app": {
                    "domain": self.domain,
                    "app_type": "nodejs",
                    "source": "https://github.com/acme/shop.git",
                    "branch": "main",
                    "layout": self.layout,
                    "port": 3000,
                },
                "domains": {"aliases": [], "redirects": []},
                "env": {
                    "API_KEY": {
                        "secret": True,
                        "value": SECRET_VALUE if with_secrets else None,
                    },
                    "REGION": {"secret": False, "value": "eu"},
                },
            },
        )

    @classmethod
    def accept_imports(
        cls, node: Node, upstream: Upstream, *, imported: list[Shop], fail: str | None = None
    ) -> None:
        """
        Make a fake server take applications in through ``POST /api/apps/import``.

        Args:
            node: The server.
            upstream: The repository the new application deploys from.
            imported: Receives each application as it is created.
            fail: When set, the import ends failed with this error.
        """

        def answer(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            domain = body.get("domain") or body["document"]["app"]["domain"]
            if fail:
                return node.queue(status="failed", error=fail)
            if any(shop.domain == domain for shop in imported):
                return httpx.Response(
                    409, json={"error": "domain_conflict", "detail": f"{domain} is taken"}
                )
            shop = cls(node, upstream, domain=domain, commit=upstream.head)
            shop.imports.append(body)
            imported.append(shop)
            return node.queue(result={"domain": domain, "status": "imported"})

        node.handlers[("POST", "/api/apps/import")] = answer


#: A value that must never be seen anywhere but in the request that carries it.
SECRET_VALUE = "s3cr3t-" + "value-" + "never-logged"
