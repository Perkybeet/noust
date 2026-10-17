# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The fleet actions of an application on several servers: roll it out, and copy it.

Both are bulk actions like the others (:mod:`noust.web.fleet_jobs`): a plan first, batches, a
canary, a failure threshold, a record per server and a retry. What they add is the part that is
specific to one application on several servers, and every step is still a call to the server's own
API (rule 3):

- **placement_rollout** deploys one application on its servers so that they all end on the *same
  commit*. The first server goes alone and builds the head of the branch; the commit it ends on is
  fixed for the rest (and kept with the job, so a retry makes the same choice), so a push that
  lands during the rollout reaches none of them. When the rollout stops, the servers already moved
  are put back by their previous release, which each server does instantly and behind its health
  gate, and refuses when it would pass a schema change: what cannot be put back is said, not hidden.
- **placement_copy** puts an application on another server from the export of one that has it
  (the server's own ``export`` and ``import``), and brings it to the commit the source serves. The
  document, with the secrets when asked, goes from one server to the other through the central's
  memory and is written nowhere: not the job's result, not its log, not the record of the server.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Any
from urllib.parse import quote

from noust.core.exceptions import ValidationError
from noust.fleet.aggregate import Asker, Failure, NodeSource, decode_answer
from noust.fleet.placements import checked_domain
from noust.web.fleet_jobs import (
    ACTIONS,
    REQUEST_TIMEOUT,
    ActionFailed,
    ActionSpec,
    FleetRequest,
    NodeRun,
    NothingToUndo,
    NotNeeded,
    _dicts,
    resolve_targets,
)

#: What a commit is on the command line: hex, short or full. A name that begins with a dash, or
#: a branch, is not one, and never reaches ``git``.
_COMMIT = re.compile(r"[0-9a-f]{7,40}")

#: A tag name Noust passes to a server: no leading dash (an option to ``git``), no ``..``.
_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/+-]{0,127}")

#: An image tag, as a registry spells one (the same rule the servers apply).
_IMAGE_TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")

#: What an application's state is when it serves.
_SERVING = frozenset({"running", "static"})


def _checked_commit(value: object) -> str:
    """
    Check a commit an operator asked for.

    Args:
        value: The commit.

    Returns:
        It, lower case.

    Raises:
        ValidationError: When it is not 7 to 40 hexadecimal characters.
    """
    text = value.strip().lower() if isinstance(value, str) else ""
    if not _COMMIT.fullmatch(text):
        raise ValidationError(
            f"Not a commit: {value!r}",
            details="Give the commit as 7 to 40 hexadecimal characters (git rev-parse --short).",
            field="options",
        )
    return text


def _checked_tag(value: object) -> str:
    """
    Check a tag an operator asked for.

    Args:
        value: The tag.

    Returns:
        It.

    Raises:
        ValidationError: When it is not a tag name Noust passes on.
    """
    text = value.strip() if isinstance(value, str) else ""
    if not _TAG.fullmatch(text) or ".." in text:
        raise ValidationError(
            f"Not a tag: {value!r}",
            details="Give the tag as it is named in the repository, such as v2.0.0.",
            field="options",
        )
    return text


def _same_commit(left: str | None, right: str | None) -> bool:
    """
    Tell whether two commits, short or full, are the same one.

    Args:
        left: One commit.
        right: Another.

    Returns:
        True when one is a prefix of the other and both have at least 7 characters.
    """
    if not left or not right:
        return False
    a, b = left.lower(), right.lower()
    return min(len(a), len(b)) >= 7 and (a.startswith(b) or b.startswith(a))


def _check(allowed: dict[str, type], options: Any) -> dict[str, Any]:
    """
    Reject an option the action does not take.

    Args:
        allowed: Option name to its type.
        options: What was given.

    Returns:
        The options as a dict.

    Raises:
        ValidationError: On an option that is not one.
    """
    unknown = sorted(set(options) - set(allowed))
    if unknown:
        raise ValidationError(
            f"Unknown option(s): {', '.join(unknown)}",
            details=f"This action takes: {', '.join(sorted(allowed))}.",
            field="options",
        )
    return dict(options)


def _flag(options: dict[str, Any], name: str, default: bool) -> bool:
    """
    Read a yes/no option.

    Args:
        options: The options given.
        name: The option.
        default: What it is when not given.

    Returns:
        The flag.

    Raises:
        ValidationError: When it is given as anything but true or false.
    """
    value = options.get(name, default)
    if not isinstance(value, bool):
        raise ValidationError(f"{name} must be true or false", field="options")
    return value


def _rollout_options(options: Any) -> dict[str, Any]:
    """
    Check and complete the options of a rollout.

    Args:
        options: What was given.

    Returns:
        ``domain``, ``commit``, ``tag``, ``image_tag``, ``force`` and ``rollback_on_failure``.

    Raises:
        ValidationError: A domain that is not one, a commit, a tag or an image tag that is not
            one, or more than one of them.
    """
    given = _check(
        {
            "domain": str,
            "commit": str,
            "tag": str,
            "image_tag": str,
            "force": bool,
            "rollback_on_failure": bool,
        },
        options,
    )
    commit = given.get("commit")
    tag = given.get("tag")
    image_tag = given.get("image_tag")
    if sum(1 for named in (commit, tag, image_tag) if named) > 1:
        raise ValidationError(
            "A commit, a tag and an image tag each name what to deploy",
            details="Give one of them, or none to deploy the head of the branch.",
            field="options",
        )
    if image_tag and not _IMAGE_TAG.fullmatch(str(image_tag)):
        raise ValidationError(
            f"Not an image tag: {image_tag!r}",
            details="A tag is letters, digits, '_', '.' and '-', at most 128, and does not "
            "start with '.' or '-'.",
            field="options",
        )
    return {
        "domain": checked_domain(given.get("domain")),
        "commit": _checked_commit(commit) if commit else None,
        "tag": _checked_tag(tag) if tag else None,
        "image_tag": str(image_tag) if image_tag else None,
        "force": _flag(given, "force", False),
        "rollback_on_failure": _flag(given, "rollback_on_failure", True),
    }


def _apps_of(source: NodeSource) -> list[dict[str, Any]]:
    """
    Read the applications a server runs.

    Args:
        source: The server.

    Returns:
        Their rows.

    Raises:
        Failure: When it does not answer.
    """
    return _dicts(source.get("/api/apps"), "apps")


def _row(rows: list[dict[str, Any]], domain: str) -> dict[str, Any] | None:
    """
    Find one application among a server's.

    Args:
        rows: The server's applications.
        domain: The one wanted.

    Returns:
        Its row, or None.
    """
    return next((row for row in rows if str(row.get("domain") or row.get("name")) == domain), None)


def _commit_of(row: dict[str, Any] | None) -> str | None:
    """
    Read the commit an application serves out of its row.

    Args:
        row: The application's row.

    Returns:
        The short commit of its last deployment, or None.
    """
    last = (row or {}).get("last_deployment")
    commit = last.get("git_commit") if isinstance(last, dict) else None
    return commit if isinstance(commit, str) and commit else None


# --------------------------------------------------------------------------- rollout


def _rollout_prepare(
    request: FleetRequest, options: dict[str, Any], manager: Any, asker: Asker
) -> tuple[FleetRequest, list[str]]:
    """
    Refuse a rollout to a server that does not have the application, and name who goes first.

    Without a commit or a tag, the first server builds the head of the branch alone and the
    others are asked for the commit it ends on. A rollout is refused before it starts when a
    server that answers does not run the application: stopping there would leave the ones
    before it moved and the rest not.

    Args:
        request: The request.
        options: Its checked options.
        manager: The node registry.
        asker: Who asks.

    Returns:
        The request, with its first server as the canary when it needs one, and the
        sentences the plan shows.

    Raises:
        ValidationError: A server that answered does not run the application.
    """
    targets = resolve_targets(request, manager)
    domain = options["domain"]

    def missing(record: Any) -> str | None:
        try:
            rows = _apps_of(NodeSource(manager, record, asker, REQUEST_TIMEOUT))
        except Failure:
            return None
        return None if _row(rows, domain) else str(record.name)

    with ThreadPoolExecutor(max_workers=min(16, len(targets))) as pool:
        absent = [name for name in pool.map(missing, targets) if name]
    if absent:
        raise ValidationError(
            f"{domain} is not installed on {', '.join(absent)}",
            details=(
                "A rollout moves an application that is there. Put it on those servers first: "
                f"noust fleet placement copy {domain} --from <server> --to {absent[0]}"
            ),
            field="targets",
        )
    notes: list[str] = []
    from noust.fleet.builders import Builders

    recorded = Builders(manager.store).get(domain)
    if recorded is not None:
        notes.append(
            f"{recorded.node} is recorded as the build server of {domain}, and this rollout builds "
            f"on each server. To build once there and send the release to the others: "
            f"noust fleet placement ship {domain}"
        )
    explicit = options.get("commit") or options.get("tag") or options.get("image_tag")
    if options.get("image_tag"):
        notes.append(
            f"Every server is asked for the image tag {options['image_tag']}: build it once, push "
            "it to the registry, and the servers pull the same image."
        )
    if not explicit:
        first = request.canary or targets[0].name
        if request.canary is None:
            request = replace(request, canary=first)
        notes.append(
            f"{first} goes first and alone: it builds the head of the branch, and the commit it "
            "ends on is the one every other server is asked for, so they all run the same code."
        )
    return request, notes


def _rollout_precheck(run: NodeRun) -> None:
    """
    Say what a server would move from, and skip one that is already where it should be.

    Args:
        run: The server.

    Raises:
        NotNeeded: It already serves the commit asked for.
        Failure: It does not answer, or does not run the application.
    """
    domain = run.options["domain"]
    row = _row(_dicts(run.call("GET", "/api/apps"), "apps"), domain)
    if row is None:
        raise Failure(
            "error",
            f"{domain} is not installed on {run.name}: noust fleet placement copy {domain} "
            f"--from <server> --to {run.name}",
            code="app_not_installed",
        )
    current = _commit_of(row)
    wanted = run.options.get("commit")
    if wanted and _same_commit(current, wanted) and not run.options.get("force"):
        raise NotNeeded(f"{run.name} is already on {wanted}")
    run.step(
        f"{current or 'no deployment'} -> {wanted or run.options.get('tag') or 'the head of the branch'}"
    )


def _active_release(run: NodeRun, domain: str, row: dict[str, Any]) -> str | None:
    """
    Find the release a server serves now, to put it back by if the rollout stops.

    Args:
        run: The server.
        domain: The application.
        row: The application's row.

    Returns:
        The release's id; None for an application deployed in place, which has none.
    """
    if row.get("layout") != "releases" and row.get("app_type") != "container":
        return None
    response = run.send("GET", f"/api/apps/{quote(domain, safe='')}/releases")
    if response.status_code == 409:
        return None
    answer = decode_answer(run.name, "GET", f"/api/apps/{domain}/releases", response)
    for release in _dicts(answer, "items"):
        if release.get("active"):
            return str(release.get("id"))
    return None


def _image_of(run: NodeRun, domain: str) -> str | None:
    """
    Read the image a container application is configured to run.

    Args:
        run: The server.
        domain: The application.

    Returns:
        The registry reference with its tag, or None when the application does not run a
        registry image.
    """
    response = run.send("GET", f"/api/apps/{quote(domain, safe='')}/container")
    if response.status_code != 200:
        return None
    settings = decode_answer(run.name, "GET", f"/api/apps/{domain}/container", response)
    image = settings.get("image") if isinstance(settings, dict) else None
    return image if isinstance(image, str) and image else None


def _placement_rollout(run: NodeRun) -> None:
    """
    Deploy the application on one server, and fix the commit for the rest if this one decides it.

    Args:
        run: The server.

    Raises:
        NotNeeded: The server already serves the commit asked for.
        ActionFailed: The deploy failed, or the application is not serving after it.
    """
    domain = run.options["domain"]
    path = quote(domain, safe="")
    row = _row(_dicts(run.call("GET", "/api/apps"), "apps"), domain)
    if row is None:
        raise ActionFailed(f"{domain} is not installed on {run.name}")
    before = _commit_of(row)
    wanted, tag = run.options.get("commit"), run.options.get("tag")
    image_tag = run.options.get("image_tag")
    force = bool(run.options.get("force"))
    if wanted and _same_commit(before, wanted) and not force:
        raise NotNeeded(f"{run.name} is already on {wanted}")
    item: dict[str, Any] = {
        "domain": domain,
        "state": "running",
        "changed": False,
        "before": {
            "commit": before,
            "release": _active_release(run, domain, row),
            "layout": row.get("layout"),
        },
    }
    if image_tag:
        item["before"]["image"] = _image_of(run, domain)
    run.items.append(item)
    body: dict[str, Any] = {}
    if wanted:
        body["commit"] = wanted
    elif tag:
        body["tag"] = tag
    elif image_tag:
        body["image_tag"] = image_tag
    if force:
        body["force"] = True
    run.step("Deploying")
    response = run.send("POST", f"/api/apps/{path}/deploy", body=body)
    if response.status_code == 429:
        raise ActionFailed(
            f"{run.name}'s deploy queue is full",
            details="Wait for a build to finish there, or raise deploy.queue_limit.",
        )
    answer = decode_answer(run.name, "POST", f"/api/apps/{domain}/deploy", response)
    result = (_dicts(answer, "results") or [{}])[0]
    if result.get("status") == "nothing_new":
        # The branch did not move: this server already serves its head, which is then the
        # commit the others come to.
        item.update(state="succeeded", message="already on the head of the branch")
        if not wanted and not tag and not image_tag and before:
            run.pin("commit", before)
        run.step(f"Already on {before or 'the head of the branch'}")
        return
    job = run.run_job({"job_id": result.get("job_id")}, f"The deploy of {domain}")
    outcome = job.get("result")
    deployment_id = outcome.get("deployment_id") if isinstance(outcome, dict) else None
    after = before
    if deployment_id is not None:
        deployment = run.call("GET", f"/api/deployments/{deployment_id}")
        found = deployment.get("git_commit") if isinstance(deployment, dict) else None
        after = found if isinstance(found, str) and found else after
        item["deployment_id"] = deployment_id
    now = _row(_dicts(run.call("GET", "/api/apps"), "apps"), domain)
    status = str((now or {}).get("status") or "unknown")
    if status not in _SERVING:
        item.update(state="failed", changed=True, after={"commit": after, "status": status})
        raise ActionFailed(
            f"{domain} is {status} on {run.name} after the deploy",
            output=f"The deploy of {after or 'the application'} finished, and the application "
            f"does not serve: {status}.",
        )
    item.update(state="succeeded", changed=True, after={"commit": after, "status": status})
    if not wanted and not tag and not image_tag and after:
        run.pin("commit", after)
    run.step(f"Serving {after or image_tag or 'the new version'}")


def _placement_undo(run: NodeRun) -> None:
    """
    Put one server back on the release that served before the rollout.

    Args:
        run: The server, with the items its deploy recorded.

    Raises:
        NothingToUndo: Nothing was changed on this server.
        NotNeeded: The application was deployed in place and has no release to go back to.
        Failure: The server refused (a schema change, a release no longer on disk) or did not
            answer: its words are the reason.
    """
    domain = run.options["domain"]
    item = next((i for i in run.items if i.get("domain") == domain and i.get("changed")), None)
    if item is None:
        raise NothingToUndo
    before = item.get("before") or {}
    release = before.get("release")
    if not release:
        raise NotNeeded(
            f"{domain} is deployed in place on {run.name}: it has no earlier release to go back "
            f"to, so it stays on the new version (go back by a backup: noust rollback {domain})"
        )
    answer = run.call(
        "POST",
        f"/api/apps/{quote(domain, safe='')}/releases/{quote(str(release), safe='')}/activate",
    )
    if before.get("image"):
        # The release is back; the image the application is configured with must be too, or its
        # next update would pull the tag the rollout stopped on.
        run.call(
            "PUT",
            f"/api/apps/{quote(domain, safe='')}/container",
            body={"image": before["image"]},
        )
    item.update(
        state="rolled_back",
        undone=True,
        restored={"release": release, "commit": before.get("commit")},
    )
    if isinstance(answer, dict) and answer.get("changed") is False:
        item["note"] = "it was already on that release"


# --------------------------------------------------------------------------- copy


def _copy_options(options: Any) -> dict[str, Any]:
    """
    Check and complete the options of a copy.

    Args:
        options: What was given.

    Returns:
        ``domain``, ``from_node``, ``new_domain``, ``secrets`` and ``match_commit``.

    Raises:
        ValidationError: A domain that is not one, or no server to copy from.
    """
    given = _check(
        {"domain": str, "from_node": str, "new_domain": str, "secrets": bool, "match_commit": bool},
        options,
    )
    source = given.get("from_node")
    if not isinstance(source, str) or not source:
        raise ValidationError(
            "Say which server to copy the application from",
            details="Give the server that runs it (--from web-1).",
            field="options",
        )
    new = given.get("new_domain")
    return {
        "domain": checked_domain(given.get("domain")),
        "from_node": source,
        "new_domain": checked_domain(new) if new else None,
        "secrets": _flag(given, "secrets", True),
        "match_commit": _flag(given, "match_commit", True),
    }


def _copy_prepare(
    request: FleetRequest, options: dict[str, Any], manager: Any, asker: Asker
) -> tuple[FleetRequest, list[str]]:
    """
    Check that the source is a server of this central and is not among the destinations.

    Args:
        request: The request.
        options: Its checked options.
        manager: The node registry.
        asker: Who asks.

    Returns:
        The request, unchanged, and what the plan says about the secrets.

    Raises:
        ValidationError: The source is not registered, or is also a destination.
    """
    source = options["from_node"]
    manager.get(source)
    targets = resolve_targets(request, manager)
    if source in {record.name for record in targets} and not options.get("new_domain"):
        raise ValidationError(
            f"{source} is the server the application is copied from",
            details="Copy it to other servers, or give a new domain to make a second copy there.",
            field="targets",
        )
    notes = [
        f"The application is exported from {source} and imported on each server: its definition "
        "and, as asked, its secret values, which pass through this central and are kept nowhere."
        if options["secrets"]
        else f"The application is exported from {source} without its secret values: the servers "
        "refuse the import until they are given, so use this only for an application that has none."
    ]
    return request, notes


def _placement_copy(run: NodeRun) -> None:
    """
    Put the application on one server from the export of another, and bring it to the same commit.

    Args:
        run: The server it goes to.

    Raises:
        NotNeeded: The server already has an application by that domain.
        ActionFailed: The source does not have it, or the import failed.
    """
    options = run.options
    domain = options["domain"]
    target = options.get("new_domain") or domain
    peer = run.peer
    if peer is None:
        raise ActionFailed("There is no server to copy from")
    if _row(_dicts(run.call("GET", "/api/apps"), "apps"), target) is not None:
        raise NotNeeded(f"{target} is already on {run.name}")
    source_row = _row(_apps_of(peer), domain)
    if source_row is None:
        raise ActionFailed(f"{domain} is not installed on {peer.name}, which it is copied from")
    run.step(f"Reading {domain} from {peer.name}")
    document = peer.get(
        f"/api/apps/{quote(domain, safe='')}/export",
        params={"with_secrets": "true"} if options["secrets"] else None,
    )
    body: dict[str, Any] = {"document": document}
    if options.get("new_domain"):
        body["domain"] = target
    item: dict[str, Any] = {
        "domain": target,
        "state": "running",
        "copied_from": peer.name,
        "secrets": bool(options["secrets"]),
    }
    run.items.append(item)
    run.step(f"Creating {target}")
    answer = run.call("POST", "/api/apps/import", body=body)
    del body, document
    run.run_job(answer, f"The import of {target}")
    wanted = _commit_of(source_row)
    row = _row(_dicts(run.call("GET", "/api/apps"), "apps"), target)
    got = _commit_of(row)
    item["commit"] = got
    if options["match_commit"] and wanted and got and not _same_commit(got, wanted):
        run.step(f"Bringing {target} to {wanted}")
        response = run.send(
            "POST",
            f"/api/apps/{quote(target, safe='')}/deploy",
            body={"commit": wanted},
        )
        result = (
            _dicts(
                decode_answer(run.name, "POST", f"/api/apps/{target}/deploy", response), "results"
            )
            or [{}]
        )[0]
        if result.get("job_id"):
            run.run_job({"job_id": result["job_id"]}, f"The deploy of {target} at {wanted}")
            item["commit"] = wanted
    item["state"] = "succeeded"
    run.step(f"{target} is on {run.name}")


# ------------------------------------------------------------------ the requests


def rollout_request(
    placement: Any,
    members: list[str],
    *,
    commit: str | None = None,
    tag: str | None = None,
    image_tag: str | None = None,
    force: bool = False,
    rollback_on_failure: bool | None = None,
    serial: int | str | None = None,
    max_failures: int | None = None,
    canary: str | None = None,
) -> FleetRequest:
    """
    Build the request that rolls an application out over its servers.

    The one place a placement and what the operator asked for become a request, for the
    console's API and for the terminal alike. What is not asked for is the placement's own.

    Args:
        placement: The placement.
        members: The servers it runs on now.
        commit: Deploy exactly this commit everywhere.
        tag: Deploy the commit this tag points at.
        image_tag: For a registry image: the tag every server pulls.
        force: Rebuild even a server already on it.
        rollback_on_failure: Put the servers already moved back when it stops.
        serial: Servers at a time.
        max_failures: Failures that stop it.
        canary: The server that goes first.

    Returns:
        The request.

    Raises:
        ValidationError: The application runs on no server right now.
    """
    if not members:
        raise ValidationError(
            f"{placement.domain} runs on no server right now",
            details="Name servers for it, or label some with the placement's label.",
            field="targets",
        )
    options: dict[str, Any] = {
        "domain": placement.domain,
        "force": force,
        "rollback_on_failure": (
            placement.rollback_on_failure if rollback_on_failure is None else rollback_on_failure
        ),
    }
    if commit:
        options["commit"] = commit
    if tag:
        options["tag"] = tag
    if image_tag:
        options["image_tag"] = image_tag
    chosen = canary or (placement.canary if placement.canary in members else None)
    return FleetRequest(
        action="placement_rollout",
        nodes=list(members),
        serial=serial if serial is not None else placement.serial,
        max_failures=max_failures if max_failures is not None else placement.max_failures,
        canary=chosen,
        options=options,
    )


def copy_request(
    placement: Any,
    status: dict[str, Any] | None,
    *,
    from_node: str | None = None,
    to: list[str] | None = None,
    new_domain: str | None = None,
    secrets: bool = True,
    match_commit: bool = True,
) -> FleetRequest:
    """
    Build the request that puts an application on the servers that lack it.

    Args:
        placement: The placement.
        status: :func:`~noust.fleet.placement_status.placement_status`'s answer; it is only
            read when the source or the destinations are not named, and may be None otherwise.
        from_node: The server to copy from; one that serves it, on the reference commit
            when there is one, by default.
        to: The servers to copy to; the members that lack it by default.
        new_domain: Make the copy under another domain.
        secrets: Carry the secret values with it.
        match_commit: Bring each copy to the commit the source serves.

    Returns:
        The request.

    Raises:
        ValidationError: No server has the application to copy from, or none lacks it.
    """
    rows = (status or {}).get("servers", [])
    source = from_node
    if source is None:
        serving = [s for s in rows if s["present"] and s["reachable"]]
        ahead = [s for s in serving if s["sync"] == "current"] or serving
        if not ahead:
            raise ValidationError(
                f"No server of {placement.domain} has it to copy from",
                details="Deploy it on one server first, then copy it to the others.",
                field="from_node",
            )
        source = ahead[0]["node"]
    destinations = (
        list(to) if to is not None else [s["node"] for s in rows if s["sync"] == "missing"]
    )
    if not destinations:
        raise ValidationError(
            f"Every server of {placement.domain} already has it",
            details="Add servers to the placement first, or label them with its label.",
            field="to",
        )
    options: dict[str, Any] = {
        "domain": placement.domain,
        "from_node": source,
        "secrets": secrets,
        "match_commit": match_commit,
    }
    if new_domain:
        options["new_domain"] = new_domain
    return FleetRequest(action="placement_copy", nodes=destinations, options=options)


ACTIONS.update(
    {
        spec.name: spec
        for spec in (
            ActionSpec(
                "placement_rollout",
                "Roll an application out",
                (
                    ("GET", "/api/apps"),
                    ("POST", "/api/apps/{domain}/deploy"),
                    ("GET", "/api/deployments/{deployment_id}"),
                    ("GET", "/api/apps/{domain}/releases"),
                    ("POST", "/api/apps/{domain}/releases/{release_id}/activate"),
                ),
                serial=1,
                max_failures=0,
                run=_placement_rollout,
                check_options=_rollout_options,
                precheck=_rollout_precheck,
                prepare=_rollout_prepare,
                undo=_placement_undo,
            ),
            ActionSpec(
                "placement_copy",
                "Copy an application to servers",
                (("GET", "/api/apps"), ("POST", "/api/apps/import")),
                serial=2,
                max_failures=None,
                run=_placement_copy,
                check_options=_copy_options,
                prepare=_copy_prepare,
                peer_option="from_node",
            ),
        )
    }
)
