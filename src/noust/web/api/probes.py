# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/probes``: addresses asked from outside the application, and their public status page.

A thin client of :class:`~noust.managers.probes.ProbeManager` and
:class:`~noust.managers.status_page.StatusPage`, which ``noust probe`` uses too
(rule 3). A probe is an address, the statuses that mean well, how often to ask
and how many bad answers in a row make it down; the monitor asks them on a
clock and tells the notification channels when one goes down and when it answers
again (``probe_failed``, ``probe_recovered``). The probes marked public are the
components of a static status page the monitor rewrites, and putting that page on
a name over TLS is a job, like publishing the console.

Literal paths come before the parametrised ones.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core import audit
from noust.core.probe_store import (
    DEFAULT_FAIL_AFTER,
    DEFAULT_INTERVAL_S,
    DEFAULT_TIMEOUT_S,
    Probe,
)
from noust.managers.probes import ProbeManager, ProbeView, public_address
from noust.managers.status_page import StatusPage, StatusPageSite
from noust.web.api.auth import get_current_session
from noust.web.api.config import load_config, persist
from noust.web.api.databases.common import session_actor
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated
from noust.web.auth import actor_label
from noust.web.jobs import JobContext, JobType, get_job_manager
from noust.web.pydantic_compat import dump_model

router = APIRouter(route_class=NoustErrorRoute)

Session = Annotated[dict[str, Any], Depends(get_current_session)]
Elevated = Annotated[dict[str, Any], Depends(require_elevated)]


class UptimeModel(BaseModel):
    """
    How often an address answered over a window.

    Attributes:
        checks: Times it was asked.
        good: Times it answered well.
        percent: Good answers as a percentage; null when it was never asked.
        mean_ms: The mean latency of the good answers.
        max_ms: The slowest.
    """

    checks: int
    good: int
    percent: float | None = None
    mean_ms: float | None = None
    max_ms: int | None = None


class StatusModel(BaseModel):
    """
    Where a probe stands.

    Attributes:
        state: ``unknown``, ``up`` or ``down``.
        since: Epoch seconds the state began.
        failures: Bad answers in a row.
        last_checked: When it was last asked.
        last_status: The HTTP status of the last answer.
        last_latency_ms: How long it took.
        last_error: What was wrong with the last answer, in one line.
    """

    state: str
    since: float
    failures: int
    last_checked: float | None = None
    last_status: int | None = None
    last_latency_ms: int | None = None
    last_error: str | None = None


class ProbeModel(BaseModel):
    """
    One probe, where it stands and how it has done.

    Attributes:
        name: Its identifier.
        url: What is asked.
        address: The same without its query: what notifications and the status
            page may show.
        method: ``GET`` or ``HEAD``.
        expect_status: The statuses that mean well.
        expect_text: A word the page must contain.
        interval_s: Seconds between two asks.
        timeout_s: Seconds an answer may take.
        fail_after: Bad answers in a row that make it down.
        public: Shown on the status page.
        title: What the status page calls it.
        enabled: Whether it is asked.
        status: Where it stands.
        uptime: By window (``24h``, ``7d``, ``30d``, ``90d``).
    """

    name: str
    url: str
    address: str
    method: str
    expect_status: str
    expect_text: str
    interval_s: int
    timeout_s: int
    fail_after: int
    public: bool
    title: str
    enabled: bool
    status: StatusModel
    uptime: dict[str, UptimeModel]


class ProbeBody(BaseModel):
    """
    What a probe is, to create or replace it.

    Attributes:
        url: What to ask: ``http://`` or ``https://``, no credentials in it.
        method: ``GET`` or ``HEAD``.
        expect_status: The statuses that mean well: ``200-399`` or ``200,204``.
        expect_text: A word the page must contain (GET only).
        interval_s: Seconds between two asks (30 to 3600).
        timeout_s: Seconds an answer may take (1 to 60, under the interval).
        fail_after: Bad answers in a row that make it down (1 to 10).
        public: Show it on the status page.
        title: What the status page calls it.
        enabled: Whether it is asked.
    """

    url: str
    method: str = "GET"
    expect_status: str = "200-399"
    expect_text: str = ""
    interval_s: int = DEFAULT_INTERVAL_S
    timeout_s: int = DEFAULT_TIMEOUT_S
    fail_after: int = DEFAULT_FAIL_AFTER
    public: bool = False
    title: str = ""
    enabled: bool = True


class IncidentModel(BaseModel):
    """
    A stretch an address did not answer.

    Attributes:
        started_at: Epoch seconds of the first bad answer.
        ended_at: When it answered again; null while it is down.
        reason: What the first bad answer said.
    """

    started_at: float
    ended_at: float | None = None
    reason: str = ""


class AnswerModel(BaseModel):
    """
    One answer an address gave.

    Attributes:
        ts: Epoch seconds.
        ok: Whether it was good.
        status: The HTTP status.
        latency_ms: How long it took.
        error: What was wrong.
    """

    ts: float
    ok: bool
    status: int | None = None
    latency_ms: int | None = None
    error: str | None = None


class StatusPageModel(BaseModel):
    """
    The public status page.

    Attributes:
        enabled: The monitor writes its files.
        directory: Where they go.
        title: What the page is called.
        components: How many probes are marked public.
        domain: The name it is published on, when it is.
        site_created: Publishing made the nginx site.
        certificate_created: Publishing made the certificate.
    """

    enabled: bool
    directory: str
    title: str
    components: int
    domain: str | None = None
    site_created: bool = False
    certificate_created: bool = False


class StatusPageBody(BaseModel):
    """
    What to change of the status page; a field left out is kept.

    Attributes:
        enabled: The monitor writes the page's files.
        title: What the page is called (80 characters at most).
        directory: Where the files go; empty for the default.
    """

    enabled: bool | None = None
    title: str | None = None
    directory: str | None = None


class PublishBody(BaseModel):
    """
    Where to put the page.

    Attributes:
        domain: A name of its own that points at this server.
        skip_dns_check: Do not require the name to resolve to this server (NAT).
    """

    domain: str = Field(min_length=1, max_length=253)
    skip_dns_check: bool = False


class ProbeListResponse(BaseModel):
    """
    The probes.

    Attributes:
        items: Every probe by name.
        down: How many are down now.
        status_page: The public status page.
    """

    items: list[ProbeModel]
    down: int
    status_page: StatusPageModel


class ProbeHistoryResponse(BaseModel):
    """
    What one probe has done lately.

    Attributes:
        name: The probe.
        incidents: Its newest incidents first.
        answers: Its newest answers, oldest first.
    """

    name: str
    incidents: list[IncidentModel]
    answers: list[AnswerModel]


class CheckResponse(BaseModel):
    """
    What asking a probe now got.

    Attributes:
        ok: It answered well.
        status: The HTTP status.
        latency_ms: How long it took.
        error: What was wrong, in one line.
        probe: The probe as it stands now.
    """

    ok: bool
    status: int | None = None
    latency_ms: int | None = None
    error: str | None = None
    probe: ProbeModel


class StatusPageFilesResponse(BaseModel):
    """
    The files of the status page after writing them.

    Attributes:
        files: Their paths.
    """

    files: list[str]


#: The plain fields of a probe, as :meth:`ProbeView.to_dict` names them.
_PROBE_FIELDS = (
    "name",
    "url",
    "address",
    "method",
    "expect_status",
    "expect_text",
    "interval_s",
    "timeout_s",
    "fail_after",
    "public",
    "title",
    "enabled",
)


def _manager() -> ProbeManager:
    """
    Returns:
        The probes over the process-wide store. Module level so a test can
        stand in another.
    """
    return ProbeManager()


def _model(view: ProbeView) -> ProbeModel:
    """
    Args:
        view: A probe with where it stands.

    Returns:
        The response model.
    """
    body = view.to_dict()
    return ProbeModel(
        **{key: body[key] for key in _PROBE_FIELDS},
        status=StatusModel(**body["status"]),
        uptime={window: UptimeModel(**value) for window, value in body["uptime"].items()},
    )


def _page() -> StatusPage:
    """
    Returns:
        The status page over the process-wide store and configuration.
    """
    return StatusPage(_manager().store, config=load_config())


def _status_page(manager: ProbeManager) -> StatusPageModel:
    """
    Args:
        manager: The probes.

    Returns:
        The status page's settings and whether it is on a name.
    """
    page = _page()
    published = StatusPageSite(page).record()
    return StatusPageModel(
        enabled=page.enabled,
        directory=str(page.directory),
        title=page.title,
        components=sum(1 for probe in manager.store.probes() if probe.public and probe.enabled),
        domain=published.domain if published else None,
        site_created=published.site_created if published else False,
        certificate_created=published.certificate_created if published else False,
    )


@router.get("", response_model=ProbeListResponse)
def list_probes(session: Session) -> ProbeListResponse:
    """
    List the probes with where each stands and how it has done.

    Args:
        session: The authenticated session.

    Returns:
        The probes, how many are down, and the status page.
    """
    manager = _manager()
    items = [_model(view) for view in manager.views()]
    return ProbeListResponse(
        items=items,
        down=sum(1 for item in items if item.status.state == "down"),
        status_page=_status_page(manager),
    )


@router.get("/status-page", response_model=StatusPageModel)
def get_status_page(session: Session) -> StatusPageModel:
    """
    Say how the public status page is set up and whether it is on a name.

    Args:
        session: The authenticated session.

    Returns:
        The settings and the publication.
    """
    return _status_page(_manager())


@router.put("/status-page", response_model=StatusPageModel)
def set_status_page(body: StatusPageBody, session: Elevated) -> StatusPageModel:
    """
    Change the status page's settings.

    Args:
        body: What to change.
        session: The authenticated session in sudo mode (the monitor, as root,
            writes the files where the directory says).

    Returns:
        The settings as they now stand.

    Raises:
        ConfigError: 400 naming the setting that is not acceptable.
    """
    config = load_config()
    changed: list[str] = []
    for key, value in (
        ("enabled", body.enabled),
        ("title", body.title),
        ("directory", body.directory),
    ):
        if value is not None:
            config.set(f"status_page.{key}", value)
            changed.append(key)
    if changed:
        persist(config)
        audit.record(
            "status_page.set",
            actor=session_actor(session),
            target="status-page",
            details={"changed": changed},
        )
    return _status_page(_manager())


@router.post("/status-page/write", response_model=StatusPageFilesResponse)
def write_status_page(session: Session) -> StatusPageFilesResponse:
    """
    Write the status page's files now, instead of at the monitor's next scan.

    Args:
        session: The authenticated session.

    Returns:
        The files written.
    """
    return StatusPageFilesResponse(files=[str(path) for path in _page().write()])


def publish_job(
    domain: str, skip_dns_check: bool, actor: str, job_context: JobContext
) -> dict[str, Any]:
    """
    Put the status page on a name, as a job.

    Args:
        domain: The name.
        skip_dns_check: Skip the DNS check.
        actor: Who asked.
        job_context: The job's handle.

    Returns:
        The record of what was made.
    """
    job_context.update("Writing the page and obtaining a certificate", 10)
    record = StatusPageSite(_page()).publish(domain, skip_dns_check=skip_dns_check)
    job_context.log(f"The status page is at https://{record.domain}", "success")
    return record.to_dict()


def unpublish_job(job_context: JobContext) -> dict[str, Any]:
    """
    Take the status page off its name, as a job.

    Args:
        job_context: The job's handle.

    Returns:
        What was removed.
    """
    job_context.update("Removing", 10)
    removed = StatusPageSite(_page()).unpublish()
    for item in removed:
        job_context.log(f"Removed {item}", "warning" if item.startswith("NOT removed") else "info")
    return {"removed": removed}


@router.post("/status-page/publish", response_model=JobAcceptedResponse, status_code=202)
def publish_status_page(body: PublishBody, session: Elevated) -> JobAcceptedResponse:
    """
    Put the status page on a name of its own, over TLS.

    Args:
        body: The name.
        session: The authenticated session in sudo mode.

    Returns:
        The queued job.
    """
    job = get_job_manager().create_job(
        job_type=JobType.SITE_ACTION,
        name=f"Publish the status page at {body.domain}",
        description="Writing the page, the nginx site and obtaining a certificate",
        func=publish_job,
        kwargs={
            "domain": body.domain,
            "skip_dns_check": body.skip_dns_check,
            "actor": actor_label(session),
        },
        metadata={"kind": "status_page_publish", "status_domain": body.domain},
        actor=actor_label(session),
    )
    audit.record(
        "status_page.publish",
        actor=session_actor(session),
        target=f"status-page:{body.domain}",
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message="Publishing the status page queued",
        job=job.to_dict(),
    )


@router.delete("/status-page/site", response_model=JobAcceptedResponse, status_code=202)
def unpublish_status_page(session: Elevated) -> JobAcceptedResponse:
    """
    Take the status page off its name, removing exactly what publishing made.

    Args:
        session: The authenticated session in sudo mode.

    Returns:
        The queued job.
    """
    job = get_job_manager().create_job(
        job_type=JobType.SITE_ACTION,
        name="Unpublish the status page",
        description="Removing the nginx site and the certificate publishing made",
        func=unpublish_job,
        metadata={"kind": "status_page_unpublish"},
        actor=actor_label(session),
    )
    audit.record("status_page.unpublish", actor=session_actor(session), target="status-page")
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message="Unpublishing the status page queued",
        job=job.to_dict(),
    )


@router.get("/{name}/history", response_model=ProbeHistoryResponse)
def probe_history(name: str, session: Session) -> ProbeHistoryResponse:
    """
    Say what one probe has done lately.

    Args:
        name: The probe.
        session: The authenticated session.

    Returns:
        Its incidents and its latest answers.

    Raises:
        HTTPException: 404 when there is no such probe.
    """
    manager = _manager()
    probe = manager.store.get(name)
    if probe is None or probe.id is None:
        raise HTTPException(status_code=404, detail=f"No probe named {name!r}")
    return ProbeHistoryResponse(
        name=name,
        incidents=[IncidentModel(**item.to_dict()) for item in manager.store.incidents(probe.id)],
        answers=[AnswerModel(**item) for item in manager.store.recent(probe.id)],
    )


@router.put("/{name}", response_model=ProbeModel)
def save_probe(name: str, body: ProbeBody, session: Session) -> ProbeModel:
    """
    Create a probe or replace the one of that name.

    Replacing it asks the address afresh: what the old settings had decided is
    forgotten.

    Args:
        name: The probe's name.
        body: What it is.
        session: The authenticated session.

    Returns:
        The probe as stored.

    Raises:
        ValidationError: 400 naming the field that is not acceptable, an
            address on this server's own network included.
    """
    manager = _manager()
    saved = manager.save(Probe(name=name, **dump_model(body)), actor=actor_label(session))
    audit.record(
        "probe.set",
        actor=session_actor(session),
        target=f"probe:{saved.name}",
        details={"address": public_address(saved.url), "enabled": saved.enabled},
    )
    view = manager.view(saved.name)
    if view is None:  # pragma: no cover - the probe was stored a line ago
        raise HTTPException(status_code=404, detail=f"No probe named {saved.name!r}")
    return _model(view)


@router.post("/{name}/check", response_model=CheckResponse)
def check_probe(name: str, session: Session) -> CheckResponse:
    """
    Ask a probe now, whatever its interval, and keep the answer.

    Args:
        name: The probe.
        session: The authenticated session.

    Returns:
        What it answered, and the probe as it now stands.

    Raises:
        HTTPException: 404 when there is no such probe.
    """
    manager = _manager()
    check = manager.check(name)
    view = manager.view(name)
    if check is None or view is None:
        raise HTTPException(status_code=404, detail=f"No probe named {name!r}")
    return CheckResponse(
        ok=check.answer.ok,
        status=check.answer.status,
        latency_ms=check.answer.latency_ms,
        error=check.answer.error,
        probe=_model(view),
    )


@router.delete("/{name}", status_code=204)
def remove_probe(name: str, session: Session) -> None:
    """
    Remove a probe with what it kept.

    Args:
        name: The probe.
        session: The authenticated session.

    Raises:
        HTTPException: 404 when there is no such probe.
    """
    if not _manager().remove(name):
        raise HTTPException(status_code=404, detail=f"No probe named {name!r}")
    audit.record("probe.remove", actor=session_actor(session), target=f"probe:{name}")
