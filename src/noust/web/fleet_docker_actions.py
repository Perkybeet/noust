# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Look for new versions of the images in use on every server of the fleet: the fleet action behind
``POST /api/fleet/docker/images/check`` (item 189).

The look exists on each server: ``POST /api/docker/images/check`` asks the registries with the accounts that
server keeps, as a job of its own. The central only asks each server for it through the tunnel it already
has and puts what they found side by side. It is a bulk action like the others (:mod:`noust.web.fleet_jobs`):
a plan first, a record per server, each server's words when something fails, and a central re-implements
nothing a server does (rule 3):

- **A server before 4.3** does not offer the look, and the plan shows it as unable to take part.
- **A server with no usable Docker** has nothing to look at, and is skipped as that and not as a failure.
- **Each server's job is followed**, its log relayed, and what it found is kept in short (how many images it
  looked at, how many have a new version and which); the whole of it is on the server (its own job and
  ``GET /api/docker/images``).
- **Sudo mode** is what each server's own schema marks for the look, and one confirmation covers the job.

Nothing is asked of a registry unless a person asked, here as on a server: no page starts it.
"""

from __future__ import annotations

from typing import Any

from noust.core.exceptions import ValidationError
from noust.core.server_text import say
from noust.fleet.aggregate import Failure
from noust.web.fleet_jobs import (
    ACTIONS,
    ActionSpec,
    FleetRequest,
    NodeRun,
    NotNeeded,
    _dicts,
    _options,
)

__all__ = ["ACTION", "DOCKER_OPERATIONS", "MAX_REFERENCES", "check_request"]

#: The action's name.
ACTION = "docker_images_check"

CHECK = "/api/docker/images/check"
SUMMARY = "/api/docker/summary"

#: What a server must offer to take part, as its own OpenAPI schema spells it.
DOCKER_OPERATIONS: tuple[tuple[str, str], ...] = (("POST", CHECK),)

#: The most images named in the short record of one server.
MAX_REFERENCES = 20


def _precheck(run: NodeRun) -> None:
    """
    Skip a server that has no Docker to look at.

    Args:
        run: The server.

    Raises:
        NotNeeded: Its Docker is not there or does not answer.
    """
    try:
        summary = run.call("GET", SUMMARY)
    except Failure as exc:
        if exc.kind == "unsupported":
            return
        raise
    if isinstance(summary, dict) and summary.get("available") is False:
        raise NotNeeded(f"{run.name} has no Docker to look at")


def _check(run: NodeRun) -> None:
    """
    Ask one server's registries through its own look, and keep what it found in short.

    Args:
        run: The server.

    Raises:
        ActionFailed: The server's job failed; its words are the output.
    """
    run.step("Asking the registries")
    answer = run.call("POST", CHECK, body={"pull": run.options["pull"]})
    job = run.run_job(answer, "The look for new versions")
    held = job.get("result")
    result: dict[str, Any] = held if isinstance(held, dict) else {}
    records = _dicts(result, "records")
    newer = [str(item.get("reference", "")) for item in records if item.get("state") == "newer"]
    item: dict[str, Any] = {
        "state": "succeeded",
        "looked": len(records),
        "newer": len(newer),
        "unknown": sum(1 for record in records if record.get("state") == "unknown"),
        "skipped": len(_dicts(result, "skipped")),
        "checked_at": result.get("checked_at"),
        "newer_references": newer[:MAX_REFERENCES],
    }
    run.items.append(item)
    run.step(f"Looked at {item['looked']} images: {item['newer']} have a new version")


def _check_options(options: Any) -> dict[str, Any]:
    """
    Check and complete the options of the action.

    Args:
        options: What was given: ``pull``.

    Returns:
        The options, judged.

    Raises:
        ValidationError: An option that is not one, or ``pull`` that is not true or false.
    """
    if not isinstance(options, dict):
        raise ValidationError.from_text(say("fleet_job.options_object"), field="options")
    return _options({"pull": (bool, False)})(options)


def check_request(
    nodes: list[str] | None = None, labels: dict[str, str] | None = None, *, pull: bool = False
) -> FleetRequest:
    """
    Build the request that looks for new versions on several servers.

    The one place what the operator asked for becomes a request, for the console's API and the terminal
    alike: a few servers at a time (each one's look is its own job and the registries are the slow part).

    Args:
        nodes: The servers to ask, by name.
        labels: Servers carrying every one of these labels are added.
        pull: Download what moved to compare, which a Docker without buildx needs; it is a server's
            own option and is asked of each.

    Returns:
        The request.
    """
    return FleetRequest(
        action=ACTION,
        nodes=list(nodes or []),
        labels=dict(labels or {}),
        options={"pull": pull},
    )


ACTIONS.update(
    {
        ACTION: ActionSpec(
            ACTION,
            "Look for new versions of the images in use",
            DOCKER_OPERATIONS,
            serial=4,
            max_failures=None,
            run=_check,
            check_options=_check_options,
            precheck=_precheck,
        )
    }
)
