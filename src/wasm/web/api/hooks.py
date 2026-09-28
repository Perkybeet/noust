"""
Git webhook auto-deploy: the panel's only deliberately session-less mutation.

``POST /hooks/deploy/{domain}`` is called server-to-server by a git forge, so
there is no session, no cookie and therefore no CSRF to check. What
authenticates a delivery is a per-application secret, stored by the store and
presented the way each forge presents it: GitHub and Gitea sign the raw body
with HMAC-SHA256, GitLab sends the secret itself in a header. All three are
compared in constant time.

The refusals are deliberately unhelpful. A domain without a secret answers the
same generic 404 an unknown domain does, so the hook cannot be used to map
which applications are deployed; a failed signature answers 401 with no hint
of what was checked. Every outcome - accepted, ignored or refused - is written
to the audit log, never including the secret or the presented signature.

Wrong signatures are counted per application, not per address. A forge sends
every customer's deliveries from a few shared addresses, so a lockout of the
address - which is what this counted towards before - let anyone with an
account on the same forge point a webhook of their own at the panel with a
wrong secret and cut off every genuine delivery, for every application. Now
the application being guessed at stops taking deliveries for a while, and
nothing else does.

A delivery is dispatched on the event its forge says it is (``X-GitHub-Event``,
``X-Gitea-Event``, ``X-Gitlab-Event`` or GitLab's ``object_kind``): a push
updates the application, a ping is answered and nothing else, a pull (merge)
request goes to :func:`wasm.managers.previews.handle_pull_request`, and every
other event is acknowledged and ignored. Before 2.2 the event was never read,
so a pull request or ping delivery to an application without a pinned branch -
neither carries a ``ref`` - queued an update of production. A delivery that
names no event at all is still read as a push, which is what every forge sent
this endpoint until then.

The routers here are mounted in :mod:`wasm.web.server`, not in
:mod:`wasm.web.api.router`: the hook must not inherit the ``/api`` prefix and
its conventions, and the secret-management endpoints live under ``/api/apps``
where the middleware audits them like any other authenticated mutation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from collections import OrderedDict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from wasm.core.exceptions import DeploymentError, DomainError
from wasm.core.forge_events import Forge, PullRequestAction, PullRequestEvent
from wasm.core.store import DeploymentRecord, DeploymentTrigger, StoreError, get_store
from wasm.managers.previews import handle_pull_request
from wasm.web.api.auth import get_current_session
from wasm.web.api.deps import WASMErrorRoute, strict_domain
from wasm.web.auth import actor_label, get_audit_logger, get_client_ip
from wasm.web.jobs import JobContext, JobType, get_job_manager, run_update
from wasm.web.pydantic_compat import iso_offset_validator
from wasm.web.server import get_webhook_failures

#: The unauthenticated delivery surface, mounted at ``/hooks``.
router = APIRouter(route_class=WASMErrorRoute)

#: Secret management, mounted under ``/api/apps`` with ordinary session
#: authentication; POST and DELETE there require the ``admin`` scope through
#: the blanket policy in :func:`wasm.web.auth.required_scope`.
admin_router = APIRouter(route_class=WASMErrorRoute)

#: How many delivery ids the replay cache remembers.
DELIVERY_CACHE_SIZE = 512

#: How long a remembered delivery id stays a duplicate, in seconds. Forges
#: redeliver on timeout within seconds; ten minutes covers their retries
#: without remembering deliveries forever.
DELIVERY_TTL_SECONDS = 600

_GITHUB_SIGNATURE_PREFIX = "sha256="


class DeliveryCache:
    """
    Remembers recent delivery ids so a replayed delivery deploys nothing.

    In memory on purpose: a replay window has to survive a forge's automatic
    retries, not a panel restart, and the panel is a single process.
    """

    def __init__(
        self, capacity: int = DELIVERY_CACHE_SIZE, ttl: float = DELIVERY_TTL_SECONDS
    ) -> None:
        """
        Args:
            capacity: Ids remembered before the oldest is dropped.
            ttl: Seconds after which a remembered id stops being a duplicate.
        """
        self._capacity = capacity
        self._ttl = ttl
        self._entries: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()

    def seen(self, delivery_id: str) -> bool:
        """
        Check a delivery id and remember it in the same motion.

        Args:
            delivery_id: The id, already scoped to a domain by the caller.

        Returns:
            True when the id was already presented within the TTL.
        """
        now = time.monotonic()
        with self._lock:
            while self._entries:
                _oldest, stamp = next(iter(self._entries.items()))
                if now - stamp > self._ttl:
                    self._entries.popitem(last=False)
                else:
                    break

            if delivery_id in self._entries:
                return True

            self._entries[delivery_id] = now
            while len(self._entries) > self._capacity:
                self._entries.popitem(last=False)
            return False

    def clear(self) -> None:
        """Forget everything. For tests, which share the process-wide cache."""
        with self._lock:
            self._entries.clear()


_deliveries = DeliveryCache()


class WebhookSecretResponse(BaseModel):
    """
    A freshly minted webhook secret, shown this once and never again.

    Attributes:
        domain: Application the secret belongs to.
        secret: The secret in clear. This response is the only place the API
            ever returns it.
        hook_url: Where the forge should deliver.
    """

    domain: str
    secret: str
    hook_url: str


class WebhookDisabledResponse(BaseModel):
    """
    Confirmation that webhooks were disabled for an application.

    Attributes:
        domain: Application the secret was removed from.
        enabled: Always false.
    """

    domain: str
    enabled: bool = False


def mint_webhook_secret(domain: str) -> str:
    """
    Generate, store and return a fresh webhook secret for an application.

    The caller shows it once; regenerating replaces the old secret in the same
    motion, so revocation and rotation are the same operation.

    Args:
        domain: Application domain, already validated.

    Returns:
        The secret in clear.

    Raises:
        DeploymentError: When no application is deployed at the domain.
    """
    secret = secrets.token_urlsafe(32)
    if not get_store().set_webhook_secret(domain, secret):
        raise DeploymentError(
            f"Application not found: {domain}",
            details="Deploy it first, or check 'wasm list' for the exact domain.",
        )
    return secret


def webhook_update_job(domain: str, job_context: JobContext | None = None) -> dict[str, Any]:
    """
    Update a deployed application, recorded as webhook-triggered.

    The same sequence as the panel's update job with the provenance changed:
    the deployment history has to say a robot did this, not an operator.

    Args:
        domain: Domain of the application to update.
        job_context: Injected by the job manager.

    Returns:
        Summary of the update.

    Raises:
        WASMError: When the application is unknown or a step fails.
    """
    return run_update(domain, trigger=DeploymentTrigger.WEBHOOK.value, job_context=job_context)


def _hmac_hex(secret: str, body: bytes) -> str:
    """
    Compute the signature GitHub and Gitea expect.

    Args:
        secret: The webhook secret in clear.
        body: The raw request body, exactly as delivered.

    Returns:
        The lowercase hex HMAC-SHA256 digest.
    """
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _verify_provider(secret: str, body: bytes, request: Request) -> str | None:
    """
    Identify and verify the forge behind a delivery.

    Every provider header present is tried, and the first one that verifies
    wins; a delivery carrying only wrong credentials verifies as nothing.

    Args:
        secret: The application's webhook secret in clear.
        body: The raw request body.
        request: The incoming request.

    Returns:
        ``github``, ``gitea`` or ``gitlab`` when a credential verified, else
        None. Which header failed is deliberately not reported.
    """
    headers = request.headers

    github = headers.get("X-Hub-Signature-256")
    if github and github.startswith(_GITHUB_SIGNATURE_PREFIX):
        presented = github[len(_GITHUB_SIGNATURE_PREFIX) :].strip().lower()
        if hmac.compare_digest(_hmac_hex(secret, body), presented):
            return "github"

    gitea = headers.get("X-Gitea-Signature")
    if gitea and hmac.compare_digest(_hmac_hex(secret, body), gitea.strip().lower()):
        return "gitea"

    gitlab = headers.get("X-Gitlab-Token")
    if gitlab and hmac.compare_digest(secret.encode(), gitlab.encode()):
        return "gitlab"

    return None


def _delivery_id(request: Request) -> str | None:
    """
    Read the delivery id, whichever forge sent it.

    Args:
        request: The incoming request.

    Returns:
        The id, or None when the delivery carries none.
    """
    return (
        request.headers.get("X-GitHub-Delivery")
        or request.headers.get("X-Gitea-Delivery")
        or request.headers.get("X-Gitlab-Event-UUID")
    )


def _payload(body: bytes) -> dict[str, Any]:
    """
    Parse a delivery's JSON body.

    Args:
        body: The raw request body.

    Returns:
        The object, or an empty one when the body is not a JSON object.
    """
    try:
        payload = json.loads(body) if body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


#: Event kinds a delivery is dispatched on.
PUSH = "push"
PING = "ping"
PULL_REQUEST = "pull_request"

#: GitLab's ``X-Gitlab-Event`` header values and ``object_kind``s, in the
#: vocabulary above.
_GITLAB_EVENTS = {
    "push hook": PUSH,
    "merge request hook": PULL_REQUEST,
    "push": PUSH,
    "merge_request": PULL_REQUEST,
}


def _event_kind(provider: str, request: Request, payload: dict[str, Any]) -> str | None:
    """
    Say which event a verified delivery is.

    The header read is the one of the forge whose credential verified, so a
    GitLab delivery cannot claim to be a GitHub pull request. Gitea also
    sends ``X-GitHub-Event`` for compatibility, which is read after its own.

    Args:
        provider: ``github``, ``gitea`` or ``gitlab``, as verified.
        request: The delivery.
        payload: Its parsed body.

    Returns:
        ``push``, ``ping``, ``pull_request``, the forge's own name for any
        other event (lowercased), or None when the delivery names none.
    """
    headers = request.headers
    if provider == "gitlab":
        named = headers.get("X-Gitlab-Event") or payload.get("object_kind")
        if not isinstance(named, str) or not named.strip():
            return None
        lowered = named.strip().lower()
        return _GITLAB_EVENTS.get(lowered, lowered)
    if provider == "gitea":
        named = headers.get("X-Gitea-Event") or headers.get("X-GitHub-Event")
    else:
        named = headers.get("X-GitHub-Event")
    if not named or not named.strip():
        return None
    return named.strip().lower()


#: Pull request actions of GitHub and Gitea that matter to a preview.
_PR_ACTIONS = {
    "opened": PullRequestAction.OPENED,
    "reopened": PullRequestAction.OPENED,
    "ready_for_review": PullRequestAction.OPENED,
    "synchronize": PullRequestAction.UPDATED,
    "synchronized": PullRequestAction.UPDATED,
    "closed": PullRequestAction.CLOSED,
}

#: GitLab's merge request actions. ``update`` is also sent for a new title
#: or label; only an update that carries ``oldrev`` moved the branch.
_MR_ACTIONS = {
    "open": PullRequestAction.OPENED,
    "reopen": PullRequestAction.OPENED,
    "update": PullRequestAction.UPDATED,
    "close": PullRequestAction.CLOSED,
    "merge": PullRequestAction.CLOSED,
}


def _text(value: Any) -> str:
    """
    Read a payload field that should be text.

    Args:
        value: The field.

    Returns:
        It, stripped, or an empty string when it is not text.
    """
    return value.strip() if isinstance(value, str) else ""


def _section(value: Any) -> dict[str, Any]:
    """
    Read a payload field that should be an object.

    Args:
        value: The field.

    Returns:
        It, or an empty object when it is not one.
    """
    return value if isinstance(value, dict) else {}


def pull_request_event(provider: str, payload: dict[str, Any]) -> PullRequestEvent | None:
    """
    Translate a pull (merge) request delivery into a :class:`PullRequestEvent`.

    GitHub and Gitea send ``pull_request`` payloads of the same shape; GitLab
    sends ``merge_request`` with ``object_attributes``. A branch is from a
    fork when the repository it lives in is not the one the request targets
    (GitHub: a deleted fork has no head repository at all, and counts).

    Args:
        provider: ``github``, ``gitea`` or ``gitlab``.
        payload: The parsed body.

    Returns:
        The event, or None for an action a preview does not act on (labels,
        reviews, a merge request edit that pushed nothing) or a payload that
        lacks what an event needs.
    """
    if provider == "gitlab":
        return _merge_request_event(payload)
    forge = Forge.GITHUB if provider == "github" else Forge.GITEA
    action = _PR_ACTIONS.get(_text(payload.get("action")))
    pull = _section(payload.get("pull_request"))
    number = payload.get("number", pull.get("number"))
    head = _section(pull.get("head"))
    base = _section(pull.get("base"))
    base_repo = _section(base.get("repo")) or _section(payload.get("repository"))
    head_repo = _section(head.get("repo"))
    repository = _text(base_repo.get("full_name")) or _text(
        _section(payload.get("repository")).get("full_name")
    )
    branch = _text(head.get("ref"))
    if action is None or not isinstance(number, int) or isinstance(number, bool) or number < 1:
        return None
    if not repository or not branch:
        return None
    head_name = _text(head_repo.get("full_name"))
    from_fork = not head_name or head_name.lower() != repository.lower()
    installation = _section(payload.get("installation")).get("id")
    return PullRequestEvent(
        forge=forge,
        action=action,
        repository=repository,
        clone_url=_text(head_repo.get("clone_url")) or _text(base_repo.get("clone_url")),
        number=number,
        title=_text(pull.get("title")),
        branch=branch,
        base_branch=_text(base.get("ref")),
        head_sha=_text(head.get("sha")),
        from_fork=from_fork,
        installation_id=installation if isinstance(installation, int) else None,
    )


def _merge_request_event(payload: dict[str, Any]) -> PullRequestEvent | None:
    """
    Translate a GitLab merge request delivery.

    Args:
        payload: The parsed body.

    Returns:
        The event, or None (see :func:`pull_request_event`).
    """
    attributes = _section(payload.get("object_attributes"))
    action = _MR_ACTIONS.get(_text(attributes.get("action")))
    if action is PullRequestAction.UPDATED and not attributes.get("oldrev"):
        return None
    number = attributes.get("iid")
    project = _section(payload.get("project"))
    target = _section(attributes.get("target"))
    source = _section(attributes.get("source"))
    repository = _text(project.get("path_with_namespace")) or _text(
        target.get("path_with_namespace")
    )
    branch = _text(attributes.get("source_branch"))
    if action is None or not isinstance(number, int) or isinstance(number, bool) or number < 1:
        return None
    if not repository or not branch:
        return None
    source_project = attributes.get("source_project_id")
    target_project = attributes.get("target_project_id")
    from_fork = source_project is None or source_project != target_project
    return PullRequestEvent(
        forge=Forge.GITLAB,
        action=action,
        repository=repository,
        clone_url=_text(source.get("git_http_url")) or _text(project.get("git_http_url")),
        number=number,
        title=_text(attributes.get("title")),
        branch=branch,
        base_branch=_text(attributes.get("target_branch")),
        head_sha=_text(_section(attributes.get("last_commit")).get("id")),
        from_fork=from_fork,
    )


def _pushed_branch(payload: dict[str, Any]) -> str | None:
    """
    Extract the branch from a push payload.

    All three forges put ``refs/heads/<branch>`` in ``ref`` for push events.

    Args:
        payload: The parsed body.

    Returns:
        The branch name, or None when the payload names no branch - a tag
        push, a ping event, or a body that is not the JSON it claims to be.
    """
    ref = payload.get("ref")
    if not isinstance(ref, str) or not ref:
        return None
    if ref.startswith("refs/heads/"):
        return ref[len("refs/heads/") :]
    if ref.startswith("refs/"):
        return None
    return ref


def _record(request: Request, domain: str, result: str, detail: str) -> None:
    """
    Write one hook outcome to the audit log.

    Args:
        request: The delivery being answered.
        domain: Domain as it appeared in the path.
        result: ``accepted``, ``ignored`` or ``denied``.
        detail: Extra context. Never a secret and never a signature.
    """
    audit = get_audit_logger()
    if audit:
        audit.record(
            action="hooks.deploy",
            result=result,
            client_ip=get_client_ip(request),
            resource=f"/hooks/deploy/{domain}",
            detail=detail,
        )


@router.post("/deploy/{domain}")
async def deliver(domain: str, request: Request) -> JSONResponse:
    """
    Accept a signed push notification and queue the update it asks for.

    Async because the raw body has to be awaited before anything can be
    verified: the HMAC covers the bytes on the wire, not a parsed view of
    them.

    Args:
        domain: Domain of the application to update.
        request: The incoming delivery.

    Returns:
        202 with the queued job id; 200 when the delivery is authentic but
        ignored (wrong branch, a replayed delivery id, a ping); for a pull
        request, what :func:`_deliver_pull_request` answers; 202 ``ignored``
        for any other event.

    Raises:
        HTTPException: A generic 404 when the domain has no webhook configured
            or does not exist - the two are indistinguishable on purpose - and
            401 with no details when no presented credential verifies.
    """
    body = await request.body()

    try:
        validated = strict_domain(domain)
    except DomainError:
        _record(request, domain, "denied", "not a domain")
        raise HTTPException(status_code=404, detail="Not found") from None

    secret = get_store().get_webhook_secret(validated)
    if not secret:
        _record(request, validated, "denied", "unknown domain or webhooks not configured")
        raise HTTPException(status_code=404, detail="Not found")

    # Checked only once a secret exists, so this answers nothing about an
    # application the 404 above would not already have: failures are only
    # ever counted against a domain that has a webhook.
    failures = get_webhook_failures()
    if failures.is_locked(validated):
        remaining = failures.get_lockout_remaining(validated)
        _record(request, validated, "locked", f"refused for {remaining} more seconds")
        return JSONResponse(
            status_code=429,
            content={
                "error": "locked_out",
                "detail": "Too many deliveries with a wrong signature for this application.",
                "hint": "Check the webhook secret configured on the forge.",
                "fields": None,
            },
            headers={"Retry-After": str(remaining)},
        )

    provider = _verify_provider(secret, body, request)
    if provider is None:
        _record(request, validated, "denied", "signature verification failed")
        # A wrong signature is a guess at this application's secret, and is
        # counted against this application. Not against the address: see
        # the module docstring for why that cut off whole forges.
        failures.record_failure(validated)
        raise HTTPException(status_code=401, detail="Unauthorized")

    delivery = _delivery_id(request)
    if delivery and _deliveries.seen(f"{validated}:{delivery}"):
        _record(request, validated, "ignored", f"duplicate delivery {delivery} ({provider})")
        return JSONResponse(status_code=200, content={"status": "ignored", "reason": "duplicate"})

    payload = _payload(body)
    kind = _event_kind(provider, request, payload)
    if kind == PING:
        _record(request, validated, "ignored", f"ping ({provider})")
        return JSONResponse(status_code=200, content={"status": "ok", "event": PING})
    if kind == PULL_REQUEST:
        return await _deliver_pull_request(request, validated, provider, payload)
    if kind is not None and kind != PUSH:
        _record(request, validated, "ignored", f"{kind} event ({provider})")
        return JSONResponse(
            status_code=202, content={"status": "ignored", "reason": "event", "event": kind}
        )

    app = get_store().get_app(validated)
    branch = _pushed_branch(payload)
    if app is not None and app.branch and branch != app.branch:
        _record(
            request,
            validated,
            "ignored",
            f"push to {branch or 'no branch'}, app tracks {app.branch} ({provider})",
        )
        return JSONResponse(status_code=200, content={"status": "ignored", "reason": "branch"})

    job = get_job_manager().create_job(
        job_type=JobType.UPDATE,
        name=f"Update {validated}",
        description=f"Webhook-triggered update of {validated}",
        func=webhook_update_job,
        kwargs={"domain": validated},
        metadata={
            "domain": validated,
            "trigger": DeploymentTrigger.WEBHOOK.value,
            "provider": provider,
            "branch": branch,
            "delivery": delivery,
        },
        actor="webhook",
    )

    _record(
        request,
        validated,
        "accepted",
        f"queued job {job.id} ({provider}, branch {branch or 'any'})",
    )
    return JSONResponse(status_code=202, content={"job_id": job.id, "status": job.status.value})


async def _deliver_pull_request(
    request: Request, domain: str, provider: str, payload: dict[str, Any]
) -> JSONResponse:
    """
    Hand a verified pull request delivery to the previews of this application.

    Never an update of the application itself, whatever branch it tracks.

    Args:
        request: The delivery, for the audit record.
        domain: The application the webhook belongs to.
        provider: The forge that sent it.
        payload: Its parsed body.

    Returns:
        202 with the queued job ids; 200 when nothing was queued (an action
        previews ignore, previews off, a fork, the limit reached).
    """
    event = pull_request_event(provider, payload)
    if event is None:
        _record(request, domain, "ignored", f"pull request action not acted on ({provider})")
        return JSONResponse(status_code=200, content={"status": "ignored", "reason": "action"})

    # In a worker thread: the previews manager reads the store and may post a
    # pull request comment, neither of which belongs on the event loop.
    job_ids = await run_in_threadpool(handle_pull_request, event, app_domain=domain)
    summary = f"pull request #{event.number} {event.action.value} ({provider})"
    if not job_ids:
        _record(request, domain, "ignored", f"{summary}: no preview queued")
        return JSONResponse(status_code=200, content={"status": "ignored", "reason": "no_preview"})
    _record(request, domain, "accepted", f"{summary}: queued {', '.join(job_ids)}")
    return JSONResponse(status_code=202, content={"job_ids": job_ids, "status": "pending"})


def _known_app_domain(domain: str) -> str:
    """
    Validate a domain and require an application behind it.

    This is the authenticated management surface, so unlike the hook itself it
    may say plainly that nothing is deployed there.

    Args:
        domain: Domain from the request path.

    Returns:
        The validated domain.

    Raises:
        HTTPException: 404 when no application is deployed at the domain.
        DomainError: When the value is not a domain.
    """
    validated = strict_domain(domain)
    if get_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return validated


@admin_router.post("/{domain}/webhook-secret", response_model=WebhookSecretResponse)
def create_webhook_secret(
    domain: str,
    request: Request,
    session: Annotated[dict, Depends(get_current_session)],
) -> WebhookSecretResponse:
    """
    Mint (or replace) the webhook secret of an application.

    The secret appears in this response and nowhere else, ever again: the
    store keeps it for signature verification, but no listing or detail
    endpoint returns it. Calling this again rotates the secret, revoking the
    old one in the same motion.

    Args:
        domain: Domain of the application.
        request: The incoming request, for the audit record and the hook URL.
        session: The authenticated session.

    Returns:
        The secret, shown once, and the URL to configure at the forge.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = _known_app_domain(domain)
    secret = mint_webhook_secret(validated)

    audit = get_audit_logger()
    if audit:
        audit.record(
            action="hooks.secret.mint",
            result="success",
            client_ip=get_client_ip(request),
            actor=actor_label(session),
            resource=f"/api/apps/{validated}/webhook-secret",
            detail="webhook secret issued; shown once",
        )

    return WebhookSecretResponse(
        domain=validated,
        secret=secret,
        hook_url=f"{str(request.base_url).rstrip('/')}/hooks/deploy/{validated}",
    )


@admin_router.delete("/{domain}/webhook-secret", response_model=WebhookDisabledResponse)
def delete_webhook_secret(
    domain: str,
    request: Request,
    session: Annotated[dict, Depends(get_current_session)],
) -> WebhookDisabledResponse:
    """
    Disable webhooks for an application by discarding its secret.

    Args:
        domain: Domain of the application.
        request: The incoming request, for the audit record.
        session: The authenticated session.

    Returns:
        Confirmation that deliveries will now be answered with 404.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = _known_app_domain(domain)
    get_store().set_webhook_secret(validated, None)

    audit = get_audit_logger()
    if audit:
        audit.record(
            action="hooks.secret.disable",
            result="success",
            client_ip=get_client_ip(request),
            actor=actor_label(session),
            resource=f"/api/apps/{validated}/webhook-secret",
            detail="webhook secret discarded",
        )

    return WebhookDisabledResponse(domain=validated)


class WebhookDeliveryOut(BaseModel):
    """One webhook-triggered deployment attempt."""

    deployment_id: int
    status: str
    started_at: str | None = None
    git_commit: str | None = None
    error: str | None = None

    _iso_timestamps = iso_offset_validator("started_at")


class WebhookDeliveriesResponse(BaseModel):
    """Webhook-triggered deployments for one application, newest first."""

    items: list[WebhookDeliveryOut]
    total: int


#: Deployment rows fetched before filtering to webhook-triggered ones. History
#: is pruned to twenty rows per domain (see
#: :meth:`~wasm.core.store.WASMStore.prune_deployments`), so this comfortably
#: covers every attempt the store still keeps, webhook-triggered or not.
_DELIVERY_FETCH_LIMIT = 200


def _to_delivery(record: DeploymentRecord) -> WebhookDeliveryOut:
    """
    Args:
        record: A deployment row read from the store.

    Returns:
        The delivery shape of the row.
    """
    # Rows read back from the store always carry the id SQLite assigned them;
    # only an unsaved DeploymentRecord() has None here.
    if record.id is None:
        raise StoreError(
            "Deployment record has no id",
            details="This should not happen for a row read back from the store.",
        )
    return WebhookDeliveryOut(
        deployment_id=record.id,
        status=record.status,
        started_at=record.started_at,
        git_commit=record.git_commit,
        error=record.error,
    )


@admin_router.get("/{domain}/webhook/deliveries", response_model=WebhookDeliveriesResponse)
def webhook_deliveries(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> WebhookDeliveriesResponse:
    """
    List webhook-triggered deployments for one application.

    A view over the same deployment history every other surface reads, not a
    delivery log of its own: :func:`deliver` records one row per accepted push
    through the ordinary deployment recorder, tagged with the webhook trigger,
    so this is the one implementation of "what did the webhook do" - the
    deployment history already has it.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The webhook-triggered attempts, newest first.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = _known_app_domain(domain)
    records = get_store().list_deployments(domain=validated, limit=_DELIVERY_FETCH_LIMIT)
    items = [
        _to_delivery(record)
        for record in records
        if record.triggered_by == DeploymentTrigger.WEBHOOK.value
    ]
    return WebhookDeliveriesResponse(items=items, total=len(items))
