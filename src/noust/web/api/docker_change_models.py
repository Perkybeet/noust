# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The shapes the Docker zone's changes answer with and ask for: updating an image, bringing a stack to
Noust, and a stack's Compose document with its history.

They sit beside :mod:`noust.web.api.docker_models`, which draws the rows of the zone (the image row carries
the :class:`~noust.web.api.docker_models.DockerUpdateOut`); the pages that change something carry the rest.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from noust.deployers.compose_stack_edit import ComposeView, EditCheck, VersionView
from noust.managers.docker_bring import Bringing
from noust.managers.docker_updates import UpdatePlan
from noust.web.api.docker_models import DockerCopyOut, DockerOwnerOut, DockerUpdateOut


class DockerCheckRequest(BaseModel):
    """
    Ask the registries whether the images in use have a new version.

    Attributes:
        pull: Download what moved to compare, which is what a Docker without buildx needs. Without it nothing
            is downloaded and a Docker that cannot read a manifest says so.
    """

    pull: bool = False


class DockerUpdateRequest(BaseModel):
    """
    Update an image through its application.

    Attributes:
        accept_major: The person takes a new major version. A database's also needs a copy that was proven
            to restore: that is not something this can say yes to.
    """

    accept_major: bool = False


def update_out(plan: UpdatePlan) -> DockerUpdateOut:
    """
    Args:
        plan: What updating an image does.

    Returns:
        Its API shape.
    """
    body: dict[str, Any] = plan.to_dict()
    body["owner"] = DockerOwnerOut(**body["owner"])
    body["backup"] = DockerCopyOut(**body["backup"]) if body["backup"] else None
    return DockerUpdateOut(**body)


class DockerBringRequest(BaseModel):
    """
    Bring a stack that already runs to Noust.

    Attributes:
        name: What the application will be called in Noust's list; the stack's own name when absent. It is the
            only thing typed: the folder and the compose file are what the stack's containers say.
        preview: Answer what would be registered and what is not changed, and register nothing.
        accept_recreate: Go on although Compose says starting the stack the way Noust would changes what
            runs. It is read first; the preview shows Compose's own words.
    """

    name: str | None = Field(default=None, max_length=253)
    preview: bool = False
    accept_recreate: bool = False


class DockerBringRestartOut(BaseModel):
    """
    A container of the stack that a restart of the machine would leave down.

    Attributes:
        container: Its name.
        policy: Its restart policy.
        why: ``policy`` or ``stopped``.
    """

    container: str
    policy: str
    why: str


class DockerBringOut(BaseModel):
    """
    What bringing a stack to Noust registers, or did.

    Attributes:
        stack: The Compose project.
        name: What the application is called in Noust's list.
        folder: The directory the stack runs from, where it stays.
        compose_file: Its compose file, relative to the folder.
        project: The Compose project every command passes with ``-p`` from then on.
        containers: The containers of the stack.
        running: Whether any of them runs.
        headless: The stack publishes no port.
        port: The port registered for it; null for a worker.
        site: The operator's site file that serves it, never rewritten.
        source: Where updates fetch from; empty for a stack that is not a checkout.
        unit: The unit created for it, enabled and not started.
        counts: ``containers``, ``jobs`` (of them, the ones that already ended), ``volumes`` and ``networks``.
        rehearsal: What ``docker compose up --dry-run`` said, verbatim.
        recreates: What starting it the way Noust would changes, in Compose's own words; empty when nothing.
        warnings: What adopting found worth saying.
        restart_loss: What a restart of the machine would not bring back.
        adopted: It was registered; false for a preview.
        deployment_id: The adoption's history row.
    """

    stack: str
    name: str
    folder: str
    compose_file: str
    project: str | None = None
    containers: list[str] = Field(default_factory=list)
    running: bool = False
    headless: bool = False
    port: int | None = None
    site: str | None = None
    source: str = ""
    unit: str = ""
    counts: dict[str, int] = Field(default_factory=dict)
    rehearsal: str = ""
    recreates: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    restart_loss: list[DockerBringRestartOut] = Field(default_factory=list)
    adopted: bool = False
    deployment_id: int | None = None


def bring_out(found: Bringing) -> DockerBringOut:
    """
    Args:
        found: What bringing a stack registers, or did.

    Returns:
        Its API shape.
    """
    return DockerBringOut(**found.to_dict())


#: The largest document accepted: a stack is a few KiB (the same limit as a pasted document).
MAX_DOCUMENT_CHARS = 256 * 1024


class DockerComposeOut(BaseModel):
    """
    A stack's Compose document, or why it is not edited here.

    Attributes:
        stack: The Compose project.
        domain: The application it belongs to; null for a stack nobody of Noust made.
        editable: Whether it is edited here.
        kind: ``document`` (Noust keeps it) or ``file`` (a file in the folder the stack runs from) when it is.
        why_code: When it is not: ``repository`` (its document is in git), ``recipe`` (the recipe's),
            ``generated`` (a container application's, written by Noust), ``bring_first`` (Noust did not make the
            stack), ``no_file`` or ``not_compose``.
        why: The same in a sentence that says where it is changed.
        text: The document, when it is edited here; reading it is a reveal and is recorded.
        path: Where it is.
        version: The number of its newest version in the history; null when none was kept.
    """

    stack: str
    domain: str | None = None
    editable: bool
    kind: str | None = None
    why_code: str = ""
    why: str = ""
    text: str | None = None
    path: str | None = None
    version: int | None = None


class DockerComposeCheckRequest(BaseModel):
    """
    A document to judge, and write nowhere.

    Attributes:
        text: The document as it would be saved.
    """

    text: str = Field(max_length=MAX_DOCUMENT_CHARS)


class DockerComposeSaveRequest(BaseModel):
    """
    A document to apply behind the health gate.

    Attributes:
        text: The document as it would be saved.
        reason: Why, in a line: it is kept with the version, with who saved it and when.
    """

    text: str = Field(max_length=MAX_DOCUMENT_CHARS)
    reason: str = Field(default="", max_length=500)


class DockerComposeRestoreRequest(BaseModel):
    """
    Put an earlier version back.

    Attributes:
        reason: Why, in a line.
    """

    reason: str = Field(default="", max_length=500)


class DockerProblemOut(BaseModel):
    """
    Something that stops a document from being applied.

    Attributes:
        code: ``compose`` (Compose does not read it), ``privileges`` (it asks for root on this server),
            ``variables`` (it requires a value the stack lacks) or ``image_missing`` (a registry has no such
            image).
        message: What is wrong, in a sentence.
        output: Compose's own words, verbatim, when the problem is its.
        line: The line of the document it is about, when it says.
        service: The service it is about, when there is one.
    """

    code: str
    message: str
    output: str = ""
    line: int | None = None
    service: str | None = None


class DockerCheckLineOut(BaseModel):
    """
    One thing that was checked.

    Attributes:
        name: ``compose``, ``privileges``, ``images`` or ``variables``.
        ok: Whether it passed.
        detail: What was found, in a few words.
    """

    name: str
    ok: bool
    detail: str = ""


class DockerServiceOut(BaseModel):
    """
    A service of the document.

    Attributes:
        name: Its name.
        image: Its image; null when it is built.
    """

    name: str
    image: str | None = None


class DockerImageCheckOut(BaseModel):
    """
    What is known of an image the change brings in.

    Attributes:
        service: The service that runs it.
        image: The image.
        state: ``present`` (this machine has it), ``found`` (its registry has it), ``missing``, ``unknown`` (it
            could not be asked) or ``built`` (the service builds it).
        detail: What the registry said, when it could not tell.
    """

    service: str
    image: str
    state: str
    detail: str = ""


class DockerEffectOut(BaseModel):
    """
    One thing Compose's rehearsal says starting the stack from the document would do.

    Attributes:
        kind: ``container``, ``volume`` or ``network``.
        name: As Compose names it.
        service: The service, for a container.
        action: ``recreate``, ``create``, ``remove``, ``start`` or ``stop``.
    """

    kind: str
    name: str
    service: str = ""
    action: str


class DockerEffectsOut(BaseModel):
    """
    What the change would do, from Compose's own rehearsal (``up --dry-run``).

    Attributes:
        changes: What would be recreated, created, removed, started or stopped; what only keeps running is not
            here.
        output: Compose's words, verbatim.
        removes_volumes: Whether a volume would go, which is data.
    """

    changes: list[DockerEffectOut] = Field(default_factory=list)
    output: str = ""
    removes_volumes: bool = False


class DockerComposeCheckOut(BaseModel):
    """
    A document, judged and written nowhere.

    Attributes:
        stack: The Compose project.
        domain: The application.
        kind: ``document`` or ``file``.
        changed: Whether it differs from what the stack has.
        valid: Whether nothing stops it from being applied.
        problems: What stops it.
        warnings: What is worth knowing and does not stop it.
        checks: What was checked, in order.
        services: The services of the document.
        images: What is known of the images the change brings in.
        diff: What changes, as a unified diff.
        added: Lines added.
        removed: Lines removed.
        effects: What starting the stack from it would do; null when Compose could not rehearse it.
    """

    stack: str
    domain: str
    kind: str
    changed: bool = False
    valid: bool = True
    problems: list[DockerProblemOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    checks: list[DockerCheckLineOut] = Field(default_factory=list)
    services: list[DockerServiceOut] = Field(default_factory=list)
    images: list[DockerImageCheckOut] = Field(default_factory=list)
    diff: str = ""
    added: int = 0
    removed: int = 0
    effects: DockerEffectsOut | None = None


class DockerComposeVersionOut(BaseModel):
    """
    One version of a stack's document.

    Attributes:
        version: Its number for the stack, from 1, never reused.
        sha256: The hash of the text.
        size: The text's length in bytes.
        source: ``found`` (what Noust first found), ``edit``, ``revert`` (put back after the update failed) or
            ``restore`` (an earlier version put back).
        actor: Who saved it; null for what Noust did on its own.
        reason: Why, in a line.
        created_at: When, ISO 8601 UTC.
    """

    version: int
    sha256: str
    size: int
    source: str
    actor: str | None = None
    reason: str = ""
    created_at: str


class DockerComposeVersionsOut(BaseModel):
    """
    The versions of a stack's document, newest first.

    Attributes:
        items: The versions, without their text.
    """

    items: list[DockerComposeVersionOut] = Field(default_factory=list)


class DockerComposeVersionDetailOut(BaseModel):
    """
    One version, with its text and what putting it back would change.

    Attributes:
        version: Its number.
        source: How it came to be.
        actor: Who saved it.
        reason: Why.
        created_at: When.
        text: The document; reading it is a reveal.
        diff: What restoring it would change in what runs now, as a unified diff.
        diff_against: What it is compared with: ``current``.
    """

    version: int
    source: str
    actor: str | None = None
    reason: str = ""
    created_at: str
    text: str
    diff: str = ""
    diff_against: str = "current"


def compose_out(view: ComposeView) -> DockerComposeOut:
    """
    Args:
        view: A stack's document, or why it is not edited here.

    Returns:
        Its API shape.
    """
    return DockerComposeOut(**view.to_dict())


def check_out(found: EditCheck) -> DockerComposeCheckOut:
    """
    Args:
        found: A document, judged.

    Returns:
        Its API shape.
    """
    return DockerComposeCheckOut(**found.to_dict())


def version_detail_out(found: VersionView) -> DockerComposeVersionDetailOut:
    """
    Args:
        found: One version with its text.

    Returns:
        Its API shape.
    """
    return DockerComposeVersionDetailOut(
        version=found.version,
        source=found.source,
        actor=found.actor,
        reason=found.reason,
        created_at=found.created_at,
        text=found.text,
        diff=found.diff,
        diff_against=found.diff_against,
    )
