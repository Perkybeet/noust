# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Build once, run on every server: the fleet action behind a central's build server (checklist A-49).

A small VPS is a poor place to compile an application. A central picks one of its servers as the
builder of an application (:mod:`noust.fleet.builders`) and ``placement_ship`` does the rest, as a
bulk action like the others (:mod:`noust.web.fleet_jobs`): a plan first, a canary that goes alone,
batches, a failure threshold, a record per server, a retry. What it adds is what is specific to
moving a build, and every step is still a call to a server's own API (rule 3):

- **The first target builds** nothing: the builder does. The canary asks the builder to build the
  release without activating it and to pack it (``POST .../releases/bundle``, the same ``noust releases
  bundle`` runs), and the SHA-256 of what it made is pinned with the job, so the servers after it
  are sent exactly that file and a retry does not build again.
- **The bundle crosses the central in chunks**, through the two tunnels, never held whole and
  written nowhere on the central: read from the builder by ranges, written to the target as an
  upload that can resume from where it stopped. A request through a tunnel is cut after a few
  minutes, and a bundle is hundreds of megabytes.
- **The target does the rest**: ``POST .../releases/receive`` checks the bundle against the digest
  the builder gave, extracts it with every check an archive gets, runs the application's own
  hooks and activates it behind the health gate, which puts back what served when the release does
  not answer. When the job stops, the targets already moved are put back by their previous release
  (the same undo a rollout has).
- **The builder lets go of the bundle** when every target has it; one that was not fetched is swept
  after a day.

The central never gets the build's tree anywhere but through these requests, and a target accepts
nothing it was not told the digest of.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any
from urllib.parse import quote

from noust.core.exceptions import ValidationError
from noust.fleet.aggregate import Asker, Failure, NodeSource
from noust.fleet.builders import Builders
from noust.fleet.nodes import NodeManager
from noust.fleet.placements import checked_domain
from noust.web.fleet_carry import Carriage, carry
from noust.web.fleet_jobs import (
    ACTIONS,
    REQUEST_TIMEOUT,
    ActionFailed,
    ActionSpec,
    FleetRequest,
    NodeRun,
    NotNeeded,
    _ceiling_refuses,
    _dicts,
    offers,
    resolve_targets,
    schema_of,
)
from noust.web.fleet_placement_actions import (
    _SERVING,
    _active_release,
    _apps_of,
    _check,
    _checked_commit,
    _commit_of,
    _flag,
    _placement_undo,
    _row,
    _same_commit,
    rollout_request,
)

__all__ = [
    "BUILDER_OPERATIONS",
    "builder_declined",
    "placement_ship_request",
    "ship_request",
    "update_request",
]

#: What the builder must offer. The targets' own operations are the action's ``operations``.
BUILDER_OPERATIONS: tuple[tuple[str, str], ...] = (
    ("GET", "/api/apps"),
    ("POST", "/api/apps/{domain}/releases/bundle"),
    ("GET", "/api/apps/{domain}/releases/bundles/{bundle_id}"),
    ("GET", "/api/apps/{domain}/releases/bundles/{bundle_id}/data"),
    ("DELETE", "/api/apps/{domain}/releases/bundles/{bundle_id}"),
)

#: What a target must offer.
TARGET_OPERATIONS: tuple[tuple[str, str], ...] = (
    ("GET", "/api/apps"),
    ("POST", "/api/apps/{domain}/releases/uploads"),
    ("PUT", "/api/apps/{domain}/releases/uploads/{upload_id}"),
    ("GET", "/api/apps/{domain}/releases/uploads/{upload_id}"),
    ("POST", "/api/apps/{domain}/releases/uploads/{upload_id}/complete"),
    ("DELETE", "/api/apps/{domain}/releases/uploads/{upload_id}"),
    ("POST", "/api/apps/{domain}/releases/receive"),
    ("GET", "/api/apps/{domain}/releases"),
    ("POST", "/api/apps/{domain}/releases/{release_id}/activate"),
)


# --------------------------------------------------------------------------- options


def _ship_options(options: Any) -> dict[str, Any]:
    """
    Check and complete the options of a ship.

    Args:
        options: What was given.

    Returns:
        ``domain``, ``builder``, ``commit``, ``keep``, ``schema_changed_ok``, ``frozen_ok``,
        ``force`` and ``rollback_on_failure``, and ``bundle`` once a server pinned it.

    Raises:
        ValidationError: A domain that is not one, a commit that is not one, or a bundle that
            is not what the builder reports.
    """
    given = _check(
        {
            "domain": str,
            "builder": str,
            "commit": str,
            "keep": bool,
            "schema_changed_ok": bool,
            "frozen_ok": bool,
            "force": bool,
            "rollback_on_failure": bool,
            "bundle": dict,
        },
        options,
    )
    commit = given.get("commit")
    builder = given.get("builder")
    bundle = given.get("bundle")
    if bundle is not None:
        needed = {"id": str, "sha256": str, "size": int, "release_id": str}
        if not all(isinstance(bundle.get(key), kind) for key, kind in needed.items()):
            raise ValidationError("The bundle is not what a build server reports", field="options")
    return {
        "domain": checked_domain(given.get("domain")),
        "builder": str(builder).strip() if builder else None,
        "commit": _checked_commit(commit) if commit else None,
        "keep": _flag(given, "keep", False),
        "schema_changed_ok": _flag(given, "schema_changed_ok", False),
        "frozen_ok": _flag(given, "frozen_ok", False),
        "force": _flag(given, "force", False),
        "rollback_on_failure": _flag(given, "rollback_on_failure", True),
        "bundle": bundle,
    }


def _missing_operation(source: NodeSource, wanted: tuple[tuple[str, str], ...]) -> str | None:
    """
    Say which operation a server does not offer.

    Args:
        source: The server.
        wanted: The operations it must offer.

    Returns:
        The first one it lacks as ``METHOD path``; None when it has them all.

    Raises:
        Failure: The server did not answer.
    """
    schema = schema_of(source)
    for method, template in wanted:
        offered, _elevated = offers(schema, method, template)
        if not offered:
            return f"{method} {template}"
    return None


def _ship_prepare(
    request: FleetRequest, options: dict[str, Any], manager: NodeManager, asker: Asker
) -> tuple[FleetRequest, list[str]]:
    """
    Refuse a ship that cannot work, before anything is built, and name who goes first.

    The builder must be a server of this central that offers what building and carrying a
    release needs, has the application on the release layout, and has it as the same type as
    every server that receives it: a release runs on the type it was built as. All of it is
    asked before the build, because the build is the expensive part.

    Args:
        request: The request.
        options: Its checked options; ``builder`` is completed from the application's recorded
            builder when the request does not name one.
        manager: The node registry.
        asker: Who asks.

    Returns:
        The request, with its first target as the canary when the bundle is not built yet, and the
        sentences the plan shows.

    Raises:
        ValidationError: There is no builder, the builder is also a target, a server lacks what it
            needs, or the application is not there, not on releases, or of another type.
    """
    domain = options["domain"]
    targets = resolve_targets(request, manager)
    named = options.get("builder")
    if not named:
        recorded = Builders(manager.store).get(domain)
        named = recorded.node if recorded else None
    if not named:
        raise ValidationError(
            f"Say which server builds {domain}",
            details=f"Name it (--builder build-1) or record it: noust fleet builder set {domain} "
            "build-1.",
            field="builder",
        )
    options["builder"] = named
    record = manager.get(named)
    if record.name in {target.name for target in targets}:
        raise ValidationError(
            f"{named} builds {domain} and is also a server to ship it to",
            details="A server that builds and runs it updates the ordinary way (noust update); "
            "ship to the others.",
            field="targets",
        )
    builder = NodeSource(manager, record, asker, REQUEST_TIMEOUT)
    refused = _ceiling_refuses(record, ACTIONS_BUILDER_SPEC)
    if refused is not None:
        raise ValidationError(refused, details="Raise it on the node with 'noust fleet access'.")
    try:
        lacking = _missing_operation(builder, BUILDER_OPERATIONS)
        built_as = _row(_apps_of(builder), domain)
    except Failure as exc:
        raise ValidationError(
            f"{named} cannot be asked to build {domain}: {exc.message}",
            details=exc.hint or "Check it with 'noust node test'.",
            field="builder",
        ) from exc
    if lacking is not None:
        raise ValidationError(
            f"{named} does not offer {lacking}",
            details="It runs an older Noust: update it first.",
            field="builder",
        )
    if built_as is None:
        raise ValidationError(
            f"{domain} is not installed on {named}, which builds it",
            details="The builder needs the application (its source, variables and build "
            f"settings): noust fleet placement copy {domain} --from <server> --to {named}",
            field="builder",
        )
    if built_as.get("layout") != "releases":
        raise ValidationError(
            f"{domain} is not on the release layout on {named}",
            details="Only an application that builds releases can be built for others. On "
            f"{named} itself: noust app migrate {domain}",
            field="builder",
        )
    kind = built_as.get("app_type")
    problems: list[str] = []
    for target in targets:
        try:
            row = _row(_apps_of(NodeSource(manager, target, asker, REQUEST_TIMEOUT)), domain)
        except Failure:
            continue
        if row is None:
            problems.append(f"{domain} is not installed on {target.name}")
        elif row.get("app_type") != kind:
            problems.append(
                f"{domain} is {row.get('app_type')} on {target.name} and {kind} on {named}"
            )
        elif row.get("layout") != "releases":
            problems.append(f"{domain} is not on the release layout on {target.name}")
    if problems:
        raise ValidationError(
            "; ".join(problems),
            details="A release runs on the type it was built as, on the release layout, and a "
            f"server needs the application first: noust fleet placement copy {domain} --from "
            "<server> --to <server>",
            field="targets",
        )
    notes = [
        f"{named} builds {domain} once, in its build sandbox, without activating it; the release "
        f"travels to each server through its tunnel and is activated there behind the health "
        f"check, after the application's own hooks."
    ]
    if not options.get("bundle"):
        first = request.canary or targets[0].name
        if request.canary is None:
            request = replace(request, canary=first)
        notes.append(
            f"{first} goes first and alone: the build is made for it, and only then does it go to "
            "the rest."
        )
    else:
        notes.append(
            f"The build is already made ({options['bundle']['release_id']}): it is sent, not made again."
        )
    return request, notes


# --------------------------------------------------------------------------- one server


def _bundle_path(domain: str, bundle_id: str) -> str:
    """
    Args:
        domain: The application.
        bundle_id: The bundle.

    Returns:
        The path of the bundle on the builder.
    """
    return f"/api/apps/{quote(domain, safe='')}/releases/bundles/{quote(bundle_id, safe='')}"


def _built_bundle(run: NodeRun) -> dict[str, Any]:
    """
    Have the bundle: the one an earlier server or job pinned if the builder still holds it, else
    a new build.

    Args:
        run: The server it is for.

    Returns:
        The bundle as the builder reports it: ``id``, ``sha256``, ``size``, ``release_id``.

    Raises:
        ActionFailed: There is no builder, the build failed, or it reported no bundle.
    """
    builder = run.peer
    if builder is None:
        raise ActionFailed("There is no server to build on")
    domain = run.options["domain"]
    pinned = run.options.get("bundle")
    if pinned:
        try:
            run.call("GET", _bundle_path(domain, pinned["id"]), on=builder)
        except Failure:
            # Swept, or taken: built again, as it would have been without it.
            run.log(f"The bundle {pinned['id']} is no longer on {builder.name}: building again")
        else:
            run.step(f"Using the build {pinned['release_id']} made on {builder.name}")
            return dict(pinned)
    run.step(f"Building on {builder.name}")
    body: dict[str, Any] = {"keep": bool(run.options.get("keep"))}
    if run.options.get("commit"):
        body["commit"] = run.options["commit"]
    answer = run.call(
        "POST", f"/api/apps/{quote(domain, safe='')}/releases/bundle", body=body, on=builder
    )
    job = run.run_job(answer, f"The build of {domain}", on=builder)
    result = job.get("result")
    bundle = result.get("bundle") if isinstance(result, dict) else None
    if not isinstance(bundle, dict) or not bundle.get("id") or not bundle.get("sha256"):
        raise ActionFailed(
            f"{builder.name} built {domain} and reported no bundle",
            output=str(result)[:2000],
        )
    run.pin("bundle", bundle)
    return dict(bundle)


def _carry(run: NodeRun, builder: NodeSource, bundle: dict[str, Any]) -> str:
    """
    Send the bundle to the server, chunk by chunk, resuming after a failure.

    Args:
        run: The server it goes to.
        builder: The server the bundle is read from.
        bundle: The bundle as the builder reports it.

    Returns:
        The finished upload's id, ready to be received.

    Raises:
        ActionFailed: The builder's copy ended early, or the transfer kept failing; the upload is
            dropped from the server.
    """
    domain = run.options["domain"]
    base = f"/api/apps/{quote(domain, safe='')}/releases"
    return carry(
        run,
        Carriage(
            source=builder,
            read_path=f"{_bundle_path(domain, bundle['id'])}/data",
            size=int(bundle["size"]),
            start_path=f"{base}/uploads",
            start_body={"size": int(bundle["size"]), "sha256": bundle["sha256"]},
            upload_path=f"{base}/uploads/{{id}}",
            noun="the release",
            gone_hint="It may have been swept; ship again to build it afresh.",
        ),
    )


def _placement_ship(run: NodeRun) -> None:
    """
    Put a release the builder made on one server, and activate it there.

    Args:
        run: The server.

    Raises:
        NotNeeded: The server already serves the commit that was built.
        ActionFailed: The build, the transfer or the activation failed, or the application does
            not serve afterwards.
    """
    domain = run.options["domain"]
    path = quote(domain, safe="")
    row = _row(_dicts(run.call("GET", "/api/apps"), "apps"), domain)
    if row is None:
        raise ActionFailed(f"{domain} is not installed on {run.name}")
    before = _commit_of(row)
    builder = run.peer
    if builder is None:
        raise ActionFailed("There is no server to build on")
    bundle = _built_bundle(run)
    commit = bundle.get("commit")
    if commit and _same_commit(before, str(commit)) and not run.options.get("force"):
        raise NotNeeded(f"{run.name} is already on {str(commit)[:7]}")
    item: dict[str, Any] = {
        "domain": domain,
        "state": "running",
        "changed": False,
        "before": {
            "commit": before,
            "release": _active_release(run, domain, row),
            "layout": row.get("layout"),
        },
        "bundle": {
            "id": bundle["id"],
            "release_id": bundle["release_id"],
            "sha256": bundle["sha256"],
            "size": bundle["size"],
            "built_on": builder.name,
        },
    }
    run.items.append(item)
    upload_id = _carry(run, builder, bundle)
    run.step("Activating the release")
    answer = run.call(
        "POST",
        f"/api/apps/{path}/releases/receive",
        body={
            "upload_id": upload_id,
            "schema_changed_ok": bool(run.options.get("schema_changed_ok")),
            "frozen_ok": bool(run.options.get("frozen_ok")),
        },
    )
    job = run.run_job(answer, f"The release of {domain}")
    reported = job.get("result")
    outcome = reported if isinstance(reported, dict) else {}
    now = _row(_dicts(run.call("GET", "/api/apps"), "apps"), domain)
    status = str((now or {}).get("status") or "unknown")
    after = {"commit": outcome.get("commit") or commit, "status": status}
    if status not in _SERVING:
        item.update(state="failed", changed=True, after=after)
        raise ActionFailed(
            f"{domain} is {status} on {run.name} after the release",
            output=f"The release {bundle['release_id']} was activated, and the application does "
            f"not serve: {status}.",
        )
    item.update(
        state="succeeded",
        changed=True,
        after=after,
        deployment_id=outcome.get("deployment_id"),
    )
    run.step(f"Serving {str(after['commit'] or bundle['release_id'])[:12]}")


def _ship_finish(options: dict[str, Any], status: str, manager: NodeManager, asker: Asker) -> None:
    """
    Let the builder go of the bundle once every server has it.

    Args:
        options: The job's options, with the bundle a server pinned.
        status: How the job ended; a bundle is kept when it did not succeed, so a retry sends it
            instead of building again (the builder sweeps it after a day).
        manager: The node registry.
        asker: Who asks.
    """
    bundle = options.get("bundle")
    name = options.get("builder")
    if status != "succeeded" or not bundle or not name:
        return
    builder = NodeSource(manager, manager.get(str(name)), asker, REQUEST_TIMEOUT)
    response = builder.request("DELETE", _bundle_path(options["domain"], str(bundle["id"])))
    if response.status_code not in (200, 204, 400, 404):
        raise ActionFailed(f"{name} answered {response.status_code} to letting go of the bundle")


# ------------------------------------------------------------------ the request


def ship_request(
    domain: str,
    nodes: list[str],
    *,
    builder: str | None = None,
    commit: str | None = None,
    keep: bool = False,
    schema_changed_ok: bool = False,
    frozen_ok: bool = False,
    force: bool = False,
    rollback_on_failure: bool = True,
    serial: int | str | None = None,
    max_failures: int | None = None,
    canary: str | None = None,
) -> FleetRequest:
    """
    Build the request that ships an application's build to its servers.

    The one place what the operator asked for becomes a request, for the console's API and the
    terminal alike. Which server builds is the one named, else the application's recorded builder
    (:mod:`noust.fleet.builders`), which the plan insists on.

    Args:
        domain: The application.
        nodes: The servers that receive the release.
        builder: The server that builds it; the recorded one when None.
        commit: Build this commit instead of the head of the branch.
        keep: Keep the release the build made on the builder.
        schema_changed_ok: Confirm taking a release older than a deployment that changed the
            schema, on the servers that would refuse it.
        frozen_ok: Confirm switching although deploys are frozen.
        force: Send it even to a server already on its commit.
        rollback_on_failure: Put the servers already moved back when it stops.
        serial: Servers at a time.
        max_failures: Failures that stop it.
        canary: The server that goes first.

    Returns:
        The request.

    Raises:
        ValidationError: Nothing says which servers receive it.
    """
    if not nodes:
        raise ValidationError(
            f"{domain} has no server to ship to",
            details="Name servers for it, or label some with the placement's label.",
            field="targets",
        )
    options: dict[str, Any] = {
        "domain": domain,
        "keep": keep,
        "schema_changed_ok": schema_changed_ok,
        "frozen_ok": frozen_ok,
        "force": force,
        "rollback_on_failure": rollback_on_failure,
    }
    if builder:
        options["builder"] = builder
    if commit:
        options["commit"] = commit
    return FleetRequest(
        action="placement_ship",
        nodes=list(nodes),
        serial=serial,
        max_failures=max_failures,
        canary=canary,
        options=options,
    )


def placement_ship_request(
    placement: Any,
    members: Sequence[str],
    *,
    builder: str | None = None,
    commit: str | None = None,
    keep: bool = False,
    schema_changed_ok: bool = False,
    frozen_ok: bool = False,
    force: bool = False,
    rollback_on_failure: bool | None = None,
    serial: int | str | None = None,
    max_failures: int | None = None,
    canary: str | None = None,
) -> FleetRequest:
    """
    Build the request that ships a placement's build, with the placement's own strategy.

    What the operator did not say is the placement's (how many servers at a time, how many
    failures stop it, who goes first, whether it puts the moved servers back), exactly as a
    rollout takes them, so the console, the terminal and an update that follows the builder
    agree on what "the placement's own" is.

    Args:
        placement: The placement.
        members: The servers that receive the release.
        builder: The server that builds it; the application's recorded one when None.
        commit: Build this commit instead of the head of the branch.
        keep: Keep the release the build made on the builder.
        schema_changed_ok: Confirm taking a release older than a deployment that changed the
            schema.
        frozen_ok: Confirm switching although deploys are frozen.
        force: Send it even to a server already on its commit.
        rollback_on_failure: Put the servers already moved back when it stops; the placement's
            own when None.
        serial: Servers at a time; the placement's own when None.
        max_failures: Failures that stop it; the placement's own when None.
        canary: The server that goes first; the placement's own when it is a member.

    Returns:
        The request.

    Raises:
        ValidationError: Nothing says which servers receive it.
    """
    chosen = canary or (placement.canary if placement.canary in members else None)
    return ship_request(
        placement.domain,
        list(members),
        builder=builder,
        commit=commit,
        keep=keep,
        schema_changed_ok=schema_changed_ok,
        frozen_ok=frozen_ok,
        force=force,
        rollback_on_failure=(
            placement.rollback_on_failure if rollback_on_failure is None else rollback_on_failure
        ),
        serial=serial if serial is not None else placement.serial,
        max_failures=max_failures if max_failures is not None else placement.max_failures,
        canary=chosen,
    )


def builder_declined(
    builder: str,
    members: Sequence[str],
    *,
    tag: str | None = None,
    image_tag: str | None = None,
    build_on_each: bool = False,
) -> str | None:
    """
    Say why an update of an application that has a build server is not built there.

    An update follows the build server (:func:`update_request`) unless this answers. Each reason
    is a thing the build server does not do, or the operator chose against it; none is silent,
    because the plan says which it was.

    Args:
        builder: The application's recorded build server.
        members: The servers the update goes to.
        tag: A git tag the update was asked to deploy.
        image_tag: A registry image tag the update was asked to roll out.
        build_on_each: The operator asked for each server to build.

    Returns:
        The reason, or None when the build server builds it.
    """
    if build_on_each:
        return "you asked for each server to build it"
    if image_tag:
        return (
            f"the servers pull the image tag {image_tag} from a registry: there is nothing to build"
        )
    if tag:
        return (
            f"the build server builds a commit, not a tag: give the commit {tag} points at "
            "(--commit) to have it built there"
        )
    if builder in members:
        return (
            f"{builder} is also one of the servers it runs on, and a server that builds and "
            "runs it updates the ordinary way"
        )
    return None


def update_request(
    placement: Any,
    members: Sequence[str],
    *,
    manager: NodeManager,
    build_on_each: bool = False,
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
    Build the request that updates an application on all its servers.

    The one place "update it everywhere" becomes an action, for the console's rollout, the
    terminal's and any client of the API. An application with a recorded build server
    (:mod:`noust.fleet.builders`) is built there once and the release is sent to the servers
    (:func:`ship_request`), which is why anyone recorded a build server; one without is rolled
    out, each server building as it always did. What the build server cannot do (a registry
    image tag, a git tag, a server that is itself one of the members) or the operator declined
    (``build_on_each``) is a rollout, and the plan names the reason, so a server too small to
    compile the application is never silently asked to.

    Args:
        placement: The placement.
        members: The servers it runs on now.
        manager: The node registry, which keeps the recorded build servers.
        build_on_each: Build on each server even though there is a build server.
        commit: Deploy exactly this commit everywhere.
        tag: Deploy the commit this git tag points at (a rollout builds it).
        image_tag: For a registry image: the tag every server pulls (a rollout).
        force: Rebuild or resend even to a server already on it.
        rollback_on_failure: Put the servers already moved back when it stops; the placement's
            own when None.
        serial: Servers at a time; the placement's own when None.
        max_failures: Failures that stop it; the placement's own when None.
        canary: The server that goes first; the placement's own when None.

    Returns:
        The request: a ``placement_ship`` when the build server builds it, else a
        ``placement_rollout``.

    Raises:
        ValidationError: The application runs on no server right now.
    """
    recorded = Builders(manager.store).get(placement.domain)
    if recorded is not None and (
        builder_declined(
            recorded.node, members, tag=tag, image_tag=image_tag, build_on_each=build_on_each
        )
        is None
    ):
        return placement_ship_request(
            placement,
            members,
            commit=commit,
            force=force,
            rollback_on_failure=rollback_on_failure,
            serial=serial,
            max_failures=max_failures,
            canary=canary,
        )
    return rollout_request(
        placement,
        list(members),
        commit=commit,
        tag=tag,
        image_tag=image_tag,
        force=force,
        rollback_on_failure=rollback_on_failure,
        serial=serial,
        max_failures=max_failures,
        canary=canary,
        build_on_each=build_on_each,
    )


#: The action the builder is judged against (its ceiling): its own operations, not the targets'.
ACTIONS_BUILDER_SPEC = ActionSpec(
    "placement_ship_builder",
    "Build for other servers",
    BUILDER_OPERATIONS,
    serial=1,
    max_failures=0,
    run=_placement_ship,
)

ACTIONS.update(
    {
        "placement_ship": ActionSpec(
            "placement_ship",
            "Build on one server, run on others",
            TARGET_OPERATIONS,
            serial=1,
            max_failures=0,
            run=_placement_ship,
            check_options=_ship_options,
            prepare=_ship_prepare,
            undo=_placement_undo,
            peer_option="builder",
            finish=_ship_finish,
        )
    }
)
