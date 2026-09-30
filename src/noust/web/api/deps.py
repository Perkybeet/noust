"""
What every endpoint in this package needs before it may call a manager.

Three things live here because they used to be repeated, inconsistently, in
every module of the API:

- **The error boundary.** Managers raise :class:`~noust.core.exceptions.NoustError`
  subclasses carrying an actionable message. Each handler used to wrap its
  manager call in ``try/except Exception`` and answer 500, which is how a
  rejected domain name and a dead certbot ended up as the same HTTP status. The
  translation is stated once, as a route class every router installs, so a
  handler can simply let the error propagate.
- **The same shape for everything else that can fail an API request.** A
  pydantic validation failure and a plain ``HTTPException`` used to answer in
  Starlette's own shapes - a list of ``{"loc": ..., "msg": ...}`` entries, or a
  bare ``{"detail": ...}`` - which meant a client needed three parsers for one
  API. :func:`install_error_handlers` reshapes both, but only under ``/api``:
  the server-rendered pages this application still has raise the same
  exceptions and must keep answering exactly as they did.
- **Strict identifier checks.** The panel runs as root, so a name arriving in a
  path segment or a JSON body is validated with :mod:`noust.validators.names`
  and :mod:`noust.validators.domain` before it becomes a path, a unit name or
  SQL. Validation that only normalises is not enough here: ``sub/dir`` must be
  refused, not silently turned into ``sub``.

The route class exists because a router cannot register an exception handler in
FastAPI; only an application can. :func:`install_error_handlers` registers the
same translation on the application for anything raised outside a route, and
the two are deliberately the same function.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Mapping
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exception_handlers import (
    http_exception_handler as _default_http_exception_handler,
)
from fastapi.exception_handlers import (
    request_validation_exception_handler as _default_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from noust.central import RoleError
from noust.core.accounts import AccountNotFoundError, AuthenticationFailed
from noust.core.applock import AppBusyError
from noust.core.ens.incident import IncidentLockdownError
from noust.core.exceptions import (
    ConfigError,
    DatabaseExistsError,
    DatabaseNotFoundError,
    DomainConflictError,
    DomainError,
    FleetUnavailableError,
    IntegrationError,
    NodeError,
    NodeRefusedError,
    NodeUnreachableError,
    NoustError,
    SecurityError,
    SourceError,
    ValidationError,
)
from noust.core.exceptions import (
    PermissionError as NoustPermissionError,
)
from noust.core.sealing import SealError, SecretsLockedError, WrongPassphraseError
from noust.validators.domain import validate_domain
from noust.web.auth import (
    SCOPE_RANK,
    actor_label,
    ensure_permission,
    ensure_scope,
    get_audit_logger,
    get_client_ip,
    is_elevated,
    is_fleet,
    require_auth,
)
from noust.web.permissions import ALL_PERMISSIONS
from noust.web.pydantic_compat import dump_model

#: Requests under this prefix are the JSON API and answer in the contract this
#: module defines. Everything else - server-rendered pages, the webhook
#: surface mounted at the application root - keeps whatever shape it already
#: had; reshaping it would change a response nobody asked to change.
API_PATH_PREFIX = "/api"

#: ``error`` value for a bare ``HTTPException``, keyed by its status code. A
#: status this table does not name falls back to :func:`_default_error_for_status`.
_ERROR_BY_STATUS: dict[int, str] = {
    400: "validation_error",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    429: "rate_limited",
}

#: Status used for a NoustError with no more specific mapping. A manager that
#: raises anything else is reporting that the operation failed on the server,
#: not that the request was malformed.
DEFAULT_ERROR_STATUS = 500

#: HTTP status per error class, most specific first: the first entry the
#: exception is an instance of wins, so a subclass must be listed before its
#: base class.
_STATUS_BY_ERROR: tuple[tuple[type[NoustError], int], ...] = (
    (DatabaseNotFoundError, 404),
    # Before SecurityError, whose subclasses they are: an unknown account is
    # a missing resource, and a refused credential is a 401 whatever its
    # reason, which the error does not carry to the client.
    (AccountNotFoundError, 404),
    (AuthenticationFailed, 401),
    (DatabaseExistsError, 409),
    (DomainConflictError, 409),
    (AppBusyError, 409),
    (SecurityError, 400),
    (ValidationError, 400),
    (DomainError, 400),
    (ConfigError, 400),
    (SourceError, 400),
    (NoustPermissionError, 403),
    # The central's sealed secrets: a wrong passphrase is a refused
    # credential, a locked central is locked (423) until the operator unlocks
    # it, and any other seal error (not sealed, a damaged header) is a
    # conflict with the store's state.
    (WrongPassphraseError, 403),
    (SecretsLockedError, 423),
    (SealError, 409),
    # A hub was asked for something only a server does.
    (RoleError, 409),
    # GitHub refused or could not be reached: the fault is upstream.
    (IntegrationError, 502),
    # A node's tunnel did not open, or the node refused the central's token:
    # the fault is between this server and the node, never in the request.
    (FleetUnavailableError, 503),
    (NodeUnreachableError, 502),
    (NodeRefusedError, 502),
    # Every other fleet error is a registration the policy refused - a bad
    # join code, a name taken, two-factor authentication still off - and its
    # sentences are what the operator has to change.
    (NodeError, 400),
)


#: Errors whose ``error`` code and hint are a promise to the console rather
#: than derived from the class name and the details. An application being
#: busy is a wait, not a fault: the console branches on ``app_busy`` to offer
#: the running job instead of an error, and the exception's own details are
#: written for a terminal ("retry the command") rather than for a screen that
#: can link to the job.
_CONTRACT_BY_ERROR: tuple[tuple[type[NoustError], str, str], ...] = (
    (AppBusyError, "app_busy", "Wait for it to finish, or follow it in Jobs"),
)

#: Errors whose ``error`` code is a promise to the console but whose hint is
#: still their own ``details``: the console branches on the code (the unlock
#: form, a hidden page on a hub) and shows the sentence as it is.
_CODE_BY_ERROR: tuple[tuple[type[NoustError], str], ...] = (
    (IncidentLockdownError, "incident_lockdown"),
    (AuthenticationFailed, "invalid_credentials"),
    (WrongPassphraseError, "wrong_passphrase"),
    (SecretsLockedError, "central_locked"),
    (RoleError, "hub_role"),
)

#: Fleet errors whose ``error`` code is a promise to the console, like
#: :data:`_CONTRACT_BY_ERROR`, but whose ``details`` is the tool's own output
#: - ssh's stderr, the node's answer - and travels verbatim in ``output``,
#: with a fixed hint above it.
_OUTPUT_CONTRACT_BY_ERROR: tuple[tuple[type[NoustError], str, str], ...] = (
    (
        NodeUnreachableError,
        "node_unreachable",
        "Check that the node is up and that its SSH address and host key are the "
        "ones the central recorded; the output below is ssh's own.",
    ),
    (
        NodeRefusedError,
        "node_refused",
        "The node no longer accepts this central's fleet token. Run `noust fleet "
        "authorize` on the node again, and paste the new join code here.",
    ),
)


class ErrorResponse(BaseModel):
    """
    Body of any failed API call.

    Attributes:
        detail: What went wrong. Named ``detail`` so the shape matches
            FastAPI's own ``HTTPException`` responses and clients need one
            code path.
        hint: How to fix it, when the manager supplied one.
        error: Machine-readable error code, for clients that branch on it:
            the lowercased :class:`~noust.core.exceptions.NoustError` subclass
            name, or one of the fixed values in :data:`_ERROR_BY_STATUS` for
            an error that never became a Noust exception.
        fields: Field name to message, for a validation failure that names
            more than one field. ``None`` for every other kind of error.
        output: The failing tool's own output, verbatim, when the error
            carries one - a rejected web server configuration, for example.
            ``None`` for every error that has no external tool output to show.
    """

    detail: str
    hint: str | None = None
    error: str
    fields: dict[str, str] | None = None
    output: str | None = None


class JobAcceptedResponse(BaseModel):
    """
    Body of a long operation that was handed to the job manager.

    Attributes:
        job_id: Identifier to poll or subscribe to.
        status: Job status at the moment the request returned.
        message: Human-readable summary.
        job: Full job snapshot, the same shape the jobs API returns.
    """

    job_id: str
    status: str
    message: str
    job: dict[str, Any] = Field(default_factory=dict)


def status_for(exc: NoustError) -> int:
    """
    Map a Noust error onto an HTTP status.

    Args:
        exc: The raised error.

    Returns:
        The status code to answer with.
    """
    for error_type, status in _STATUS_BY_ERROR:
        if isinstance(exc, error_type):
            return status
    return DEFAULT_ERROR_STATUS


def _fields_of(exc: NoustError) -> dict[str, str] | None:
    """
    Key an error by the request field it is about, when it names one.

    Args:
        exc: The raised error.

    Returns:
        ``{field: message}``, or None when the error is not about one field.
    """
    field = getattr(exc, "field", None)
    return {field: exc.message} if field else None


def error_response(exc: NoustError) -> JSONResponse:
    """
    Render a Noust error as the API's error body.

    Args:
        exc: The raised error.

    Returns:
        The JSON response, with the status implied by the error class.
    """
    # NoustError.__str__ appends "\n  Details: ..." when details is set, which
    # would repeat the hint inside detail too; .message is the bare sentence,
    # and details travels only in hint.  NoustError defaults ``details`` to an
    # empty string; an empty hint is no hint, and the client should not have
    # to know the difference.
    # Lowercased so a client branches on one casing convention regardless of
    # whether the code came from a Noust exception or from the fixed
    # vocabulary in _ERROR_BY_STATUS.
    error = type(exc).__name__.lower()
    hint: str | None = getattr(exc, "details", None) or None
    output: str | None = getattr(exc, "output", None)
    for error_type, code, contract_hint in _CONTRACT_BY_ERROR:
        if isinstance(exc, error_type):
            error, hint = code, contract_hint
            break
    for error_type, code in _CODE_BY_ERROR:
        if isinstance(exc, error_type):
            error = code
            break
    for error_type, code, contract_hint in _OUTPUT_CONTRACT_BY_ERROR:
        if isinstance(exc, error_type):
            error, hint, output = code, contract_hint, output or exc.details or None
            break
    return JSONResponse(
        status_code=status_for(exc),
        content=dump_model(
            ErrorResponse(
                detail=exc.message,
                hint=hint,
                error=error,
                output=output,
                fields=_fields_of(exc),
            )
        ),
    )


class NoustErrorRoute(APIRoute):
    """
    Route that answers a :class:`NoustError` instead of crashing on it.

    Every router in this package is built with ``route_class=NoustErrorRoute``,
    which is the only way to attach an error boundary to a router rather than
    to the whole application.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        """
        Wrap the generated handler in the API's error boundary.

        Returns:
            The wrapped handler.
        """
        handler = super().get_route_handler()

        async def wrapped(request: Request) -> Response:
            try:
                return await handler(request)
            except NoustError as exc:
                return error_response(exc)

        return wrapped


def _is_api_request(request: Request) -> bool:
    """
    Report whether a request is the JSON API, as opposed to a server-rendered
    page or the webhook surface.

    Args:
        request: The request under evaluation.

    Returns:
        True when the reshaped error contract applies.
    """
    return request.url.path.startswith(API_PATH_PREFIX)


def _default_error_for_status(status_code: int) -> str:
    """
    Choose an ``error`` value for a status :data:`_ERROR_BY_STATUS` does not name.

    Args:
        status_code: The HTTP status being answered.

    Returns:
        ``"internal"`` for a server error, ``"validation_error"`` otherwise -
        a bare ``HTTPException`` below 500 is always a rejected request, never
        a mapped Noust exception (those go through :func:`error_response`).
    """
    return "internal" if status_code >= 500 else "validation_error"


def _error_for_http_exception(exc: StarletteHTTPException) -> str:
    """
    Args:
        exc: The exception being answered.

    Returns:
        The ``error`` value for its status code.
    """
    return _ERROR_BY_STATUS.get(exc.status_code, _default_error_for_status(exc.status_code))


async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> Response:
    """
    Reshape a bare ``HTTPException`` into the API's error contract.

    Two shapes reach here. Most handlers still raise ``HTTPException`` with a
    plain string ``detail`` - "Service not found: foo" - which is mapped onto
    an ``error`` value by status code. A few, such as login, need a code the
    status alone cannot carry (``totp_required`` and ``invalid_token`` are
    both a 401) and raise with a ``detail`` dict that already carries
    ``error``; that dict passes through unchanged rather than being reduced
    to the status-code default.

    Args:
        request: The request being answered.
        exc: The exception raised.

    Returns:
        The reshaped response for an API request, or whatever FastAPI's own
        handler would have answered for anything else - a server-rendered
        page raises the same exception type and must not change shape.
    """
    if not _is_api_request(request):
        return await _default_http_exception_handler(request, exc)

    headers = getattr(exc, "headers", None)
    detail = exc.detail

    if isinstance(detail, Mapping) and "error" in detail:
        body: dict[str, Any] = {
            "error": detail["error"],
            "detail": detail.get("detail", ""),
            "hint": detail.get("hint"),
            "fields": detail.get("fields"),
            "output": detail.get("output"),
        }
        return JSONResponse(status_code=exc.status_code, content=body, headers=headers)

    response = ErrorResponse(
        detail=str(detail), hint=None, error=_error_for_http_exception(exc), fields=None
    )
    return JSONResponse(status_code=exc.status_code, content=dump_model(response), headers=headers)


async def handle_validation_error(request: Request, exc: RequestValidationError) -> Response:
    """
    Reshape a pydantic validation failure into the API's error contract.

    FastAPI's own body is a list of ``{"loc": [...], "msg": ...}`` entries, one
    per failed field, which a client has to walk to find out what to show next
    to which input. This keys the same information by field name instead.

    Args:
        request: The request being answered.
        exc: The exception raised.

    Returns:
        The reshaped response for an API request, or FastAPI's own for
        anything else.
    """
    if not _is_api_request(request):
        return await _default_validation_exception_handler(request, exc)

    fields: dict[str, str] = {}
    for error in exc.errors():
        loc = error.get("loc") or ()
        # The leading element is "body", "query" or "path"; a client wants
        # the field it filled in, not which part of the request carried it.
        name = str(loc[-1]) if loc else "body"
        fields[name] = str(error.get("msg", "Invalid value"))

    response = ErrorResponse(
        detail="Validation failed", hint=None, error="validation_error", fields=fields or None
    )
    return JSONResponse(status_code=422, content=dump_model(response))


def install_error_handlers(app: FastAPI) -> None:
    """
    Register the same translation for errors raised outside a route.

    Args:
        app: The application to register the handler on.
    """

    async def handle(request: Request, exc: Exception) -> Response:
        """
        Render a Noust error, re-raising anything else.

        Args:
            request: The request being served. Unused; Starlette's handler
                signature requires it.
            exc: The exception Starlette caught.

        Returns:
            The error response.

        Raises:
            Exception: The original exception, when it is not a Noust error.
                Starlette types handlers against ``Exception``, so this
                narrowing is the handler's own guard rather than an assertion.
        """
        if not isinstance(exc, NoustError):
            raise exc
        return error_response(exc)

    async def handle_http(request: Request, exc: Exception) -> Response:
        """
        Narrow to :class:`StarletteHTTPException` before delegating.

        Same reasoning as :func:`handle`: Starlette's registry types every
        handler against the base ``Exception``, and the class actually
        registered for is only known at the call site, so the narrowing has
        to happen here rather than in :func:`handle_http_exception`'s own
        signature.

        Args:
            request: The request being served.
            exc: The exception Starlette caught.

        Returns:
            The error response.

        Raises:
            Exception: The original exception, on the type error this
                registration guarantees never happens.
        """
        if not isinstance(exc, StarletteHTTPException):
            raise exc
        return await handle_http_exception(request, exc)

    async def handle_validation(request: Request, exc: Exception) -> Response:
        """
        Narrow to :class:`RequestValidationError` before delegating.

        Args:
            request: The request being served.
            exc: The exception Starlette caught.

        Returns:
            The error response.

        Raises:
            Exception: The original exception, on the type error this
                registration guarantees never happens.
        """
        if not isinstance(exc, RequestValidationError):
            raise exc
        return await handle_validation_error(request, exc)

    app.add_exception_handler(NoustError, handle)
    app.add_exception_handler(StarletteHTTPException, handle_http)
    app.add_exception_handler(RequestValidationError, handle_validation)


def require_scope(scope: str) -> Callable[..., Coroutine[Any, Any, dict[str, Any]]]:
    """
    Build a dependency that demands a minimum 3.0 credential scope.

    Every route's permission is enforced where the credential is resolved,
    by ``require_auth`` from the maps in :mod:`noust.web.permissions`; prefer
    :func:`require_permission` for anything new. This remains for handlers
    written against scopes: it can only tighten, and a payload's scope is
    what its permissions amount to.

    Args:
        scope: The minimum scope, one of ``read``, ``deploy`` or ``admin``.

    Returns:
        A dependency that yields the session payload, exactly as
        ``require_auth`` does, so it can replace it in an endpoint signature.

    Raises:
        ValueError: When the scope is not a scope. At import time, on purpose:
            a typo here must fail the module, not silently guard nothing.
    """
    if scope not in SCOPE_RANK:
        raise ValueError(f"Unknown scope {scope!r}; use one of {sorted(SCOPE_RANK)}")

    async def dependency(
        request: Request, session: dict[str, Any] = Depends(require_auth)
    ) -> dict[str, Any]:
        """
        Args:
            request: The incoming request.
            session: The authenticated session payload.

        Returns:
            The session payload.

        Raises:
            HTTPException: 403 when the credential's scope is below ``scope``.
        """
        ensure_scope(request, session, scope)
        return session

    return dependency


def require_permission(permission: str) -> Callable[..., Coroutine[Any, Any, dict[str, Any]]]:
    """
    Build a dependency asking for one more permission than the route's own.

    The route's own permission is enforced by ``require_auth``, from the maps
    in :mod:`noust.web.permissions`; this is for a handler whose need is
    stricter than its route's, stated in its signature. It can only tighten.

    Args:
        permission: A :class:`~noust.web.permissions.Permission` value.

    Returns:
        A dependency yielding the payload, like ``require_auth``.

    Raises:
        ValueError: When the permission does not exist. At import time, on
            purpose: a typo must fail the module, not guard nothing.
    """
    if permission not in ALL_PERMISSIONS:
        raise ValueError(f"Unknown permission {permission!r}")

    async def dependency(
        request: Request, session: dict[str, Any] = Depends(require_auth)
    ) -> dict[str, Any]:
        """
        Args:
            request: The incoming request.
            session: The authenticated payload.

        Returns:
            The payload.

        Raises:
            HTTPException: 403 when the permission is missing.
        """
        ensure_permission(request, session, permission)
        return session

    return dependency


#: Wire format for a refused destructive action, per D5 and the auth
#: endpoints' own error shape: passes through ``handle_http_exception``
#: verbatim because it already carries ``error``.
_ELEVATION_REQUIRED_DETAIL: dict[str, Any] = {
    "error": "elevation_required",
    "detail": "Confirm it's you to continue",
    "hint": "POST /api/auth/elevate with your two-factor code, or the master token.",
    "fields": None,
}


#: Credentials sudo mode did not ask to confirm in 3.0: the master token and
#: API tokens. A payload says itself whether it is exempt now
#: (``elevation_exempt``, set where the credential is verified): API tokens
#: issued since 3.1 are not unless issued so, and nothing is under the ENS
#: profile. This set only decides for a payload that does not say.
ELEVATION_EXEMPT_TYPES = frozenset({"master", "api_token"})


def elevation_satisfied(session: dict[str, Any]) -> bool:
    """
    Report whether sudo mode is satisfied for a credential right now.

    The one answer :func:`ensure_elevated` and a central vouching for its
    operator (``X-Noust-Elevated``) both use.

    Args:
        session: The authenticated payload.

    Returns:
        True for a credential not asked for sudo mode, or a session inside
        its window.
    """
    exempt = session.get("elevation_exempt")
    if exempt is None:
        exempt = session.get("type") in ELEVATION_EXEMPT_TYPES
    return bool(exempt) or is_elevated(session)


#: A fleet request whose central did not vouch for its operator's sudo mode:
#: the same code, so the console's "Confirm it's you" works unchanged - on the
#: central, where the operator's session lives.
_FLEET_ELEVATION_REQUIRED_DETAIL: dict[str, Any] = {
    "error": "elevation_required",
    "detail": "Confirm it's you on the central to continue",
    "hint": "POST /api/auth/elevate on the central, then retry through it.",
    "fields": None,
}


def _refuse_elevation(request: Request, session: dict[str, Any], detail: dict[str, Any]) -> None:
    """
    Audit and raise a refused elevated action.

    Args:
        request: The incoming request, for the audit record.
        session: The authenticated session payload.
        detail: The error body.

    Raises:
        HTTPException: Always, a 403 carrying ``detail``.
    """
    audit = get_audit_logger()
    if audit:
        audit.record(
            action="auth.elevation",
            result="denied",
            client_ip=get_client_ip(request),
            actor=actor_label(session),
            resource=request.url.path,
            detail=(
                "the central did not confirm sudo mode"
                if is_fleet(session)
                else "session is not elevated"
            ),
        )
    raise HTTPException(status_code=403, detail=dict(detail))


def ensure_elevated(request: Request, session: dict[str, Any]) -> None:
    """
    Refuse a destructive action from a session that has not confirmed recently.

    This is the chokepoint D5's sudo mode runs at. A console session must have
    called ``POST /api/auth/elevate`` within the last ten minutes. The master
    token as a Bearer, and API tokens issued before 3.1 or issued with sudo
    allowed, are exempt, because issuing that credential at all already
    required an operator's confirmation once; see :func:`elevation_satisfied`.

    The exemption is decided by what the credential *is*
    (``session["type"]``, set where it was verified), never by the channel it
    arrived on. A session token is accepted in the ``Authorization`` header
    too, and exempting that header is how moving the same session out of the
    cookie used to skip the confirmation. A payload with no type is not
    exempt. This can be called both as a route dependency and, for the
    handful of endpoints where elevation depends on the request body (a
    write-mode database query, an unmasked env read), directly from inside
    the handler.

    A central's fleet token is an API token, but it is not exempt the way
    one is: it carries every operator of the central, and only the central
    saw which of them confirmed. It passes when the central vouched for the
    operator's sudo mode (``X-Noust-Elevated``, honoured by
    :func:`noust.web.auth.admit_fleet` on a fleet token only). That is what
    covers the actions only a body makes elevated, which no OpenAPI extension
    can describe.

    Args:
        request: The incoming request, for the audit record.
        session: The authenticated session payload.

    Raises:
        HTTPException: 403 with ``error: "elevation_required"`` when a session
            has not elevated, or its window has expired, or a central did not
            vouch for its operator.
    """
    if is_fleet(session):
        if session.get("elevation_attested") is True:
            return
        _refuse_elevation(request, session, _FLEET_ELEVATION_REQUIRED_DETAIL)

    if elevation_satisfied(session):
        return

    _refuse_elevation(request, session, _ELEVATION_REQUIRED_DETAIL)


async def require_elevated(
    request: Request, session: dict[str, Any] = Depends(require_auth)
) -> dict[str, Any]:
    """
    Dependency form of :func:`ensure_elevated`, for endpoints that require it
    unconditionally.

    Args:
        request: The incoming request.
        session: The authenticated session payload.

    Returns:
        The session payload, exactly as ``require_auth`` does, so it can
        replace it in an endpoint signature.

    Raises:
        HTTPException: 403 with ``error: "elevation_required"``, per
            :func:`ensure_elevated`.
    """
    ensure_elevated(request, session)
    return session


def strict_domain(value: str) -> str:
    """
    Validate a domain that is about to become a file name or a certificate name.

    :func:`noust.validators.domain.validate_domain` normalises as well as
    validates: it strips a scheme, a port and everything after the first ``/``,
    so ``evil/../..`` would come back as ``evil`` and the caller would act on a
    resource the client never named. Anything that changes here beyond case and
    surrounding whitespace is therefore refused.

    Args:
        value: Domain exactly as it arrived from the client.

    Returns:
        The validated, lowercased domain.

    Raises:
        DomainError: When the value is not a domain, or is a domain only after
            characters were removed from it.
    """
    normalised = validate_domain(value)
    if normalised != value.strip().lower():
        raise DomainError(
            f"Invalid domain: {value!r}",
            details=(
                "Send the bare domain name. Schemes, ports, paths and '..' "
                "segments are not accepted here."
            ),
        )
    return normalised
