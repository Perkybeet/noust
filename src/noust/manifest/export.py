# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Writing down a server that already runs: ``noust manifest export`` (J-15).

The way to adopt a server into a manifest without typing it all: what the
caller can see of its applications and projects, in the manifest's own format,
ready to be edited and kept in git. It writes what is safe to publish and what
a manifest can set again: where an application lives, where it is deployed from,
its branch, its limits, its health check and its aliases. Never a variable's
value - only a comment naming the variables there are, so the file says what
is missing - and never a secret of any kind.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from noust.client.errors import RemoteError
from noust.manifest.plan import Transport
from noust.manifest.schema import VERSION

HEADER = (
    "# A Noust manifest, exported from a server.\n"
    "# Variables are not exported: add them under 'env:' (a value, or {from_env: NAME}).\n"
    "# Apply it with: noust --context NAME manifest plan FILE, then manifest apply FILE.\n"
)


def _without_userinfo(value: str) -> str:
    """
    Args:
        value: A source: a repository address, an archive address or a path.

    Returns:
        It without the credentials an address may carry.
    """
    return re.sub(r"(?<=//)[^/@\s]*@", "", str(value))


def export(transport: Transport) -> str:
    """
    Describe what a caller sees of a server as a manifest.

    Args:
        transport: The server's routes.

    Returns:
        The manifest, as YAML.
    """
    projects = []
    for item in transport.get("/projects").get("projects", []):
        projects.append(
            {
                "team": item["team"],
                "project": item["slug"],
                "name": item.get("name"),
                "environments": sorted(item.get("environments", [])),
            }
        )
    applications = []
    for info in sorted(transport.get("/apps").get("apps", []), key=lambda a: a["domain"]):
        app: dict[str, Any] = {"domain": info["domain"]}
        for key, source in (
            ("source", "source"),
            ("type", "app_type"),
            ("branch", "branch"),
            ("port", "port"),
            ("team", "team"),
            ("project", "project"),
            ("environment", "environment"),
        ):
            if info.get(source) not in (None, ""):
                # A clone address may carry a token ("https://user:token@host/..."): not published.
                app[key] = _without_userinfo(info[source]) if key == "source" else info[source]
        limits = {
            k: info[k]
            for k in ("memory_max_mb", "cpu_quota_percent", "tasks_max")
            if info.get(k) is not None
        }
        if limits:
            app["limits"] = limits
        health = {
            k: info[f"health_{k}"] for k in ("path", "expect", "timeout") if info.get(f"health_{k}")
        }
        if health:
            app["health"] = health
        try:
            domains = transport.get(f"/apps/{info['domain']}/domains").get("domains", [])
        except RemoteError as exc:
            if exc.status not in (403, 404):
                raise
            domains = []
        aliases = [d["domain"] for d in domains if d.get("kind") == "alias"]
        if aliases:
            app["domains"] = aliases
        applications.append(app)
    document: dict[str, Any] = {"version": VERSION}
    if projects:
        document["projects"] = [{k: v for k, v in p.items() if v} for p in projects]
    document["applications"] = applications
    return HEADER + yaml.safe_dump(document, sort_keys=False, default_flow_style=False)
