# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust map``: how this server fits together, as text (checklist M-69).

The console draws :class:`~noust.managers.machine_map.MachineMap` as a diagram; this prints the
same relations one application at a time: the names it answers on, its databases, its scheduled
work and the data it keeps. ``--json`` prints the graph itself, the document ``GET /api/map`` answers.
"""

from __future__ import annotations

import json
from typing import Any

import click

from noust.cli.app import Context, NoustCommand, json_option, pass_context
from noust.managers.machine_map import default_builder


@click.command("map", cls=NoustCommand, read_only=True)
@click.option("--project", default=None, help="Only the projects with this slug.")
@click.option("--environment", default=None, help="Only the environments with this slug.")
@click.option("--team", default=None, help="Only this team's.")
@json_option("Print the graph as JSON.")
@pass_context
def cli(ctx: Context, project: str | None, environment: str | None, team: str | None) -> None:
    """Show how this server fits together: names, applications, databases, scheduled work and data."""
    graph = default_builder().build(project=project, environment=environment, team=team).to_dict()
    if ctx.json_output:
        click.echo(json.dumps(graph, default=str))
        return
    nodes: dict[str, dict[str, Any]] = {
        node["id"]: node for layer in graph["layers"] for node in layer["nodes"]
    }
    related: dict[str, dict[str, list[str]]] = {}
    for edge in graph["edges"]:
        if edge["relation"] == "serves":
            related.setdefault(edge["target"], {}).setdefault("names", []).append(
                nodes[edge["source"]]["label"]
            )
        else:
            word = {"uses": "databases", "runs": "scheduled", "keeps": "data"}[edge["relation"]]
            related.setdefault(edge["source"], {}).setdefault(word, []).append(
                nodes[edge["target"]]["label"]
            )
    logger = ctx.logger
    apps = [node for node in nodes.values() if node["kind"] == "application"]
    if not apps and not nodes:
        logger.info("Nothing is deployed on this server yet.")
        return
    for app in apps:
        state = f"  {app['state_word']}" if app.get("state_word") else ""
        detail = f"  ({app['detail']})" if app.get("detail") else ""
        logger.info(f"{app['label']}{state}{detail}")
        for word in ("names", "databases", "scheduled", "data"):
            values = related.get(app["id"], {}).get(word, [])
            if values:
                logger.list_item(f"{word}: {', '.join(values)}")
    linked = {edge["target"] for edge in graph["edges"] if edge["relation"] != "serves"}
    alone = [
        node
        for node in nodes.values()
        if node["kind"] in ("database", "scheduled") and node["id"] not in linked
    ]
    if alone:
        logger.info("Not tied to an application:")
        for node in alone:
            logger.list_item(
                f"{node['kind']}: {node['label']}"
                + (f" ({node['detail']})" if node.get("detail") else "")
            )
    if graph["truncated"]:
        logger.warning("The map is larger than it draws: the rest is left out.")
