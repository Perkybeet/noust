# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A server that keeps state, for the manifest's tests.

:class:`FakeServer` answers the routes a plan reads and an apply calls with the
shapes the real API uses, and keeps what was done to it: so applying a manifest
and then planning it again can be shown to find nothing left to do (that the
whole thing is idempotent), without a console or a network.
"""

from __future__ import annotations

import re
from typing import Any

from noust.client.errors import RemoteError


def refused(status: int, code: str, detail: str) -> RemoteError:
    """
    Args:
        status: The HTTP status.
        code: The error code.
        detail: What was refused.

    Returns:
        The error a client raises for such an answer.
    """
    return RemoteError(detail, status=status, code=code)


class FakeServer:
    """
    The routes a manifest touches, over an in-memory state.

    Attributes:
        calls: Every call received, as ``(method, path, body)``.
        apps: Applications by domain, as ``GET /apps`` shows them.
        elevated: Whether the token may confirm sudo mode (the variables'
            comparison and write need it).
    """

    def __init__(self, *, elevated: bool = True) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.apps: dict[str, dict[str, Any]] = {}
        self.teams = {"acme"}
        self.projects: dict[tuple[str, str], dict[str, Any]] = {
            ("acme", "web"): {
                "team": "acme",
                "slug": "web",
                "name": "Web",
                "environments": ["production"],
            }
        }
        self.env: dict[str, dict[str, str]] = {}
        self.aliases: dict[str, list[str]] = {}
        self.databases: dict[str, list[tuple[str, str]]] = {}
        self.schedules: dict[str, dict[str, Any]] = {}
        self.jobs: dict[str, dict[str, Any]] = {}
        self.elevated = elevated
        #: Cron jobs, as ``GET /cron`` shows them; ``cron_readable`` False makes the route a 403.
        self.cron: dict[str, dict[str, Any]] = {}
        self.cron_readable = True
        #: The servers of a fleet (a central's), their labels and team, and the placements.
        self.nodes: dict[str, dict[str, Any]] = {}
        self.placements: dict[str, dict[str, Any]] = {}
        self.fleet_readable = True
        #: A central's servers, each a server of its own that its API reaches through the proxy.
        self.node_servers: dict[str, FakeServer] = {}
        self.fail_on: set[str] = set()
        self.job_error: str | None = None
        self._next_job = 0

    # --------------------------------------------------------------- helpers

    def add_app(self, domain: str, **fields: Any) -> None:
        """
        Args:
            domain: The application.
            **fields: What ``GET /apps`` shows of it, over a plain default.
        """
        self.apps[domain] = {
            "domain": domain,
            "name": domain,
            "status": "running",
            "app_type": "nodejs",
            "source": "https://example.test/shop.git",
            "branch": "main",
            "port": 3000,
            "team": "acme",
            "project": "web",
            "environment": "production",
            "memory_max_mb": None,
            "cpu_quota_percent": None,
            "tasks_max": None,
            "health_path": None,
            "health_expect": None,
            "health_timeout": None,
            **fields,
        }

    def _job(self) -> dict[str, Any]:
        self._next_job += 1
        job_id = f"job{self._next_job}"
        self.jobs[job_id] = {
            "id": job_id,
            "state": "failed" if self.job_error else "succeeded",
            "error": self.job_error,
        }
        return {"job_id": job_id, "job": {"id": job_id}}

    def _record(self, method: str, path: str, body: Any) -> None:
        self.calls.append((method, path, body))
        if path in self.fail_on:
            raise refused(500, "boom", f"{method} {path} failed")

    def add_node(self, name: str, **fields: Any) -> FakeServer:
        """
        Args:
            name: A server of the fleet.
            **fields: What ``GET /nodes`` shows of it, over a plain default.

        Returns:
            The server behind it, for the test to give applications to.
        """
        self.nodes[name] = {"name": name, "team": None, "labels": {}, **fields}
        self.node_servers[name] = FakeServer(elevated=self.elevated)
        return self.node_servers[name]

    def _through_a_node(self, method: str, path: str, *args: Any, **query: Any) -> Any:
        """
        Answer a call to ``/nodes/{node}/api/...`` with the node's own routes.

        Args:
            method: ``get``, ``post``, ``put`` or ``patch``.
            path: The central's route.
            *args: The body, when the method takes one.
            **query: Query parameters.

        Returns:
            What the node answered, or the central's own 404 for a server it does not manage.
        """
        match = re.fullmatch(r"/nodes/([^/]+)/api(/.*)", path)
        if match is None:
            raise AssertionError(f"unexpected {method.upper()} {path}")
        node = self.node_servers.get(match.group(1))
        if node is None:
            raise refused(404, "not_found", f"Node not found: {match.group(1)}")
        return getattr(node, method)(match.group(2), *args, **query)

    # -------------------------------------------------------------------- GET

    def get(self, path: str, **query: Any) -> Any:
        """
        Args:
            path: A route below the API.
            **query: Query parameters.

        Returns:
            What the real route would answer.
        """
        self._record("GET", path, None)
        if path.startswith("/nodes/") and "/api/" in path:
            return self._through_a_node("get", path, **query)
        if path == "/cron":
            if not self.cron_readable:
                raise refused(403, "instance_scope_required", "This is about the whole server")
            return {"jobs": list(self.cron.values()), "total": len(self.cron)}
        if path == "/nodes":
            if not self.fleet_readable:
                raise refused(403, "permission_denied", "Not allowed")
            return {"items": [dict(item) for item in self.nodes.values()]}
        if path == "/fleet/placements":
            if not self.fleet_readable:
                raise refused(403, "instance_scope_required", "This is about the whole server")
            return {"placements": list(self.placements.values())}
        if path == "/apps":
            return {"apps": list(self.apps.values()), "total": len(self.apps)}
        if path == "/projects":
            return {"projects": list(self.projects.values()), "total": len(self.projects)}
        if path == "/teams":
            return {"teams": [{"slug": t, "name": t.title()} for t in sorted(self.teams)]}
        if path == "/backup-schedules":
            return {"schedules": list(self.schedules.values())}
        if m := re.fullmatch(r"/apps/([^/]+)/domains", path):
            domain = m.group(1)
            rows = [{"domain": domain, "kind": "primary"}] + [
                {"domain": a, "kind": "alias"} for a in self.aliases.get(domain, [])
            ]
            return {"app": domain, "domains": rows}
        if m := re.fullmatch(r"/apps/([^/]+)/databases", path):
            domain = m.group(1)
            return {
                "domain": domain,
                "databases": [
                    {"domain": domain, "engine": e, "database": n, "env_var": "DATABASE_URL"}
                    for e, n in self.databases.get(domain, [])
                ],
            }
        if m := re.fullmatch(r"/jobs/([^/]+)", path):
            return self.jobs[m.group(1)]
        if m := re.fullmatch(r"/jobs/([^/]+)/log", path):
            return {"content": f"log of {m.group(1)}", "truncated": False}
        raise AssertionError(f"unexpected GET {path}")

    # ------------------------------------------------------------------- POST

    def post(self, path: str, body: Any = None, **query: Any) -> Any:
        """
        Args:
            path: A route below the API.
            body: The request.
            **query: Query parameters.

        Returns:
            What the real route would answer.
        """
        self._record("POST", path, body)
        if path.startswith("/nodes/") and "/api/" in path:
            return self._through_a_node("post", path, body, **query)
        if path == "/cron/preview":
            aliases = {
                "hourly": "*-*-* *:00:00",
                "daily": "*-*-* 00:00:00",
                "weekly": "Mon *-*-* 00:00:00",
                "monthly": "*-*-01 00:00:00",
            }
            return {"calendar": aliases.get(body["schedule"], body["schedule"]), "next_runs": []}
        if path == "/cron":
            aliases = {
                "hourly": "*-*-* *:00:00",
                "daily": "*-*-* 00:00:00",
                "weekly": "Mon *-*-* 00:00:00",
                "monthly": "*-*-01 00:00:00",
            }
            previous = self.cron.get(body["name"], {})
            self.cron[body["name"]] = {
                "name": body["name"],
                "command": body["command"],
                "user": body.get("user") or "noust-app",
                "working_directory": body.get("working_directory") or "",
                "app_domain": body.get("app_domain") or "",
                "schedule": body["schedule"] if body["schedule"] in aliases else "custom",
                "on_calendar": aliases.get(body["schedule"], body["schedule"]),
                "enabled": previous.get("enabled", True),
                "timeout": body.get("timeout"),
                "service": body.get("service") or "",
            }
            return {"success": True, "message": "created", "job": self.cron[body["name"]]}
        if m := re.fullmatch(r"/cron/([^/]+)/(enable|disable)", path):
            self.cron[m.group(1)]["enabled"] = m.group(2) == "enable"
            return {"success": True, "message": m.group(2)}
        if m := re.fullmatch(r"/teams/([^/]+)/projects", path):
            team = m.group(1)
            self.projects[(team, body["slug"])] = {
                "team": team,
                "slug": body["slug"],
                "name": body["name"],
                "environments": ["production"],
            }
            return {}
        if m := re.fullmatch(r"/teams/([^/]+)/projects/([^/]+)/environments", path):
            self.projects[(m.group(1), m.group(2))]["environments"].append(body["slug"])
            return {}
        if path == "/apps":
            self.add_app(
                body["domain"],
                source=body.get("source"),
                branch=body.get("branch"),
                app_type="nodejs" if body.get("app_type") == "auto" else body.get("app_type"),
                port=body.get("port") or 3000,
                team=body.get("team") or "acme",
                project=body.get("project") or "web",
                environment=body.get("environment") or "production",
                memory_max_mb=body.get("memory_max_mb"),
                health_path=body.get("health_path"),
                health_expect=body.get("health_expect"),
                health_timeout=body.get("health_timeout"),
            )
            self.env[body["domain"]] = dict(body.get("env_vars", {}))
            if body.get("database"):
                self.databases.setdefault(body["domain"], []).append(
                    (body["database"]["engine"], body["database"].get("name") or "created_db")
                )
            return self._job()
        if m := re.fullmatch(r"/apps/([^/]+)/move", path):
            self.apps[m.group(1)].update({k: v for k, v in body.items() if v})
            return {}
        if m := re.fullmatch(r"/apps/([^/]+)/domains", path):
            self.aliases.setdefault(m.group(1), []).append(body["domain"])
            return {}
        if m := re.fullmatch(r"/apps/([^/]+)/databases", path):
            self.databases.setdefault(m.group(1), []).append(
                (body["engine"], body.get("name") or "app_db")
            )
            return self._job()
        if m := re.fullmatch(r"/apps/([^/]+)/restart", path):
            return {"message": "Restarted"}
        if path == "/backup-schedules":
            self.schedules[body["domain"]] = {
                "domain": body["domain"],
                **{k: v for k, v in body.items() if k != "domain"},
            }
            return {}
        raise AssertionError(f"unexpected POST {path}")

    # ------------------------------------------------------------- PUT, PATCH

    def put(self, path: str, body: Any) -> Any:
        """
        Args:
            path: A route below the API.
            body: The request.

        Returns:
            What the real route would answer.
        """
        self._record("PUT", path, body)
        if path.startswith("/nodes/") and "/api/" in path:
            return self._through_a_node("put", path, body)
        if m := re.fullmatch(r"/nodes/([^/]+)/team", path):
            self.nodes[m.group(1)]["team"] = body["team"]
            return dict(self.nodes[m.group(1)])
        if m := re.fullmatch(r"/fleet/servers/([^/]+)/labels", path):
            self.nodes[m.group(1)]["labels"] = dict(body["labels"])
            return {"node": m.group(1), "labels": dict(body["labels"])}
        if m := re.fullmatch(r"/fleet/placements/([^/]+)", path):
            current = self.placements.get(m.group(1), {"nodes": [], "labels": {}})
            self.placements[m.group(1)] = {
                "domain": m.group(1),
                "nodes": body["nodes"] if body.get("nodes") is not None else current["nodes"],
                "labels": body["labels"] if body.get("labels") is not None else current["labels"],
                "serial": body.get("serial"),
                "max_failures": body.get("max_failures"),
                "canary": body.get("canary"),
                "rollback_on_failure": body.get("rollback_on_failure", True),
            }
            return self.placements[m.group(1)]
        if m := re.fullmatch(r"/backup-schedules/([^/]+)", path):
            self.schedules[m.group(1)] = {
                "domain": m.group(1),
                **{k: v for k, v in body.items() if k != "domain"},
            }
            return {}
        raise AssertionError(f"unexpected PUT {path}")

    def patch(self, path: str, body: Any) -> Any:
        """
        Args:
            path: A route below the API.
            body: The request.

        Returns:
            What the real route would answer.
        """
        self._record("PATCH", path, body)
        if path.startswith("/nodes/") and "/api/" in path:
            return self._through_a_node("patch", path, body)
        if m := re.fullmatch(r"/apps/([^/]+)/branch", path):
            self.apps[m.group(1)]["branch"] = body["branch"]
            return {}
        if m := re.fullmatch(r"/apps/([^/]+)/limits", path):
            self.apps[m.group(1)].update({k: v for k, v in body.items() if k != "restart"})
            return {}
        if m := re.fullmatch(r"/apps/([^/]+)/health", path):
            self.apps[m.group(1)].update({f"health_{k}": v for k, v in body.items()})
            return {}
        if m := re.fullmatch(r"/apps/([^/]+)/env", path):
            if not self.elevated:
                raise refused(403, "elevation_required", "Confirm it's you")
            current = self.env.setdefault(m.group(1), {})
            merged = {k: v for k, v in current.items() if k not in body.get("unset", [])}
            merged.update(body.get("set", {}))
            changed = sorted(
                k for k in set(current) | set(merged) if current.get(k) != merged.get(k)
            )
            if not body.get("dry_run"):
                self.env[m.group(1)] = merged
            return {
                "changed": changed,
                "dry_run": bool(body.get("dry_run")),
                "restart_required": bool(changed),
            }
        raise AssertionError(f"unexpected PATCH {path}")
