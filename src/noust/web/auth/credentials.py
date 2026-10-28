# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Reading a credential off a connection and judging it: the wrong ones are counted,
a guess is told from a typo, and what authenticates itself is left alone.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

from starlette.requests import HTTPConnection

from noust.web.auth.audit import failure_key, record_auth_failure
from noust.web.auth.config import get_audit_logger, get_global_token_manager, get_security_config
from noust.web.auth.connection import bearer_token, get_client_ip, subprotocol_token
from noust.web.auth.constants import API_TOKEN_PREFIXES, SESSION_COOKIE_NAME
from noust.web.auth.fleet import admit_fleet
from noust.web.auth.payload import CredentialRefused, credential_key, is_fleet, master_payload
from noust.web.permissions.roles import GRANT_RECOVERY

#: Paths whose endpoints verify a credential the request carries. Anywhere
#: else - ``/health``, the forge webhooks - nothing ever checks one, so the
#: rate limiter does not either: a credential it looked at there would land a
#: valid guess in the roomy budget, say so in ``X-RateLimit-Limit``, and never
#: count a wrong one.
CREDENTIAL_PATH_PREFIXES = ("/api", "/events", "/ws")


def carries_credentials(path: str) -> bool:
    """
    Report whether a request path is one whose endpoints verify credentials.

    Args:
        path: The request path.

    Returns:
        True for a path equal to or below one of
        :data:`CREDENTIAL_PATH_PREFIXES`, matched by whole segment.
    """
    return any(
        path == prefix or path.startswith(prefix + "/") for prefix in CREDENTIAL_PATH_PREFIXES
    )


@dataclass
class CredentialLedger:
    """
    The wrong credentials one request presented, and whether each was counted.

    The rate limiter checks a credential before any endpoint does, and an
    endpoint may never check it (a public route, a 404). Without a ledger the
    limiter had two bad choices: count the failure itself, so every endpoint
    that also counts it counted it twice, or count nothing, and be an oracle
    wherever no endpoint looked. So the limiter notes each wrong credential
    here, :func:`verify_credential` and :func:`authenticate_connection` mark
    the ones they count, and :func:`settle_credential_failures` counts the
    rest once the request is over.

    Attributes:
        client_ip: The address the request came from.
        resource: The path it reached, for the audit record.
        pending: Fingerprint of each wrong credential not yet counted, mapped
            to the channel it arrived on and the lockout key it counts under.
    """

    client_ip: str
    resource: str
    pending: dict[str, tuple[str, str]] = field(default_factory=dict)


_credential_ledger: ContextVar[CredentialLedger | None] = ContextVar(
    "noust_credential_ledger", default=None
)


def _fingerprint(credential: str) -> str:
    """
    Name a credential without keeping it.

    Args:
        credential: The credential.

    Returns:
        Its SHA-256, hexadecimal.
    """
    return hashlib.sha256(credential.encode("utf-8", "surrogateescape")).hexdigest()


def open_credential_ledger(client_ip: str, resource: str) -> Token[CredentialLedger | None]:
    """
    Start accounting for the wrong credentials of one request.

    Args:
        client_ip: The resolved client address.
        resource: The request path.

    Returns:
        The token :func:`settle_credential_failures` closes the ledger with.
    """
    return _credential_ledger.set(CredentialLedger(client_ip, resource))


def settle_credential_failures(token: Token[CredentialLedger | None]) -> None:
    """
    Count every wrong credential the request presented that nothing counted.

    Args:
        token: What :func:`open_credential_ledger` returned for this request.
    """
    ledger = _credential_ledger.get()
    _credential_ledger.reset(token)
    if ledger is None:
        return
    for source, key in ledger.pending.values():
        record_auth_failure(ledger.client_ip, ledger.resource, source, lockout_key=key)


def _note_wrong_credential(credential: str, source: str) -> None:
    """
    Note a wrong credential the rate limiter found, for counting later.

    Args:
        credential: The credential.
        source: Channel it arrived on.
    """
    ledger = _credential_ledger.get()
    if ledger is not None:
        fingerprint = _fingerprint(credential)
        if fingerprint not in ledger.pending:
            ledger.pending[fingerprint] = (source, failure_key(credential, ledger.client_ip))


def _mark_counted(*credentials: str | None) -> None:
    """
    Take credentials off the ledger because their failure has been counted.

    Args:
        *credentials: The credentials whose failure was just recorded.
    """
    ledger = _credential_ledger.get()
    if ledger is None:
        return
    for credential in credentials:
        if credential:
            ledger.pending.pop(_fingerprint(credential), None)


def _is_guess(credential: str) -> bool:
    """
    Report whether a credential that did not verify counts as a guess.

    Args:
        credential: A credential :func:`check_credential` refused.

    Returns:
        False for a session token this server signed: it can be expired,
        revoked or presented from the wrong address, but producing one takes
        the signing key, and counting it would lock out the operator whose
        browser simply outlived its session.
    """
    manager = get_global_token_manager()
    return manager is None or not manager.signed_session_token(credential)


#: Paths whose endpoints authenticate a bearer token of their own, which is none of the
#: console's credentials: a provisioning source's. The limiter must not take that token for a
#: wrong credential of the console (every request would then count against the lockout, and a
#: provider's first sync would lock its own address out), and the endpoints count their own
#: wrong tokens (:func:`record_auth_failure`).
SELF_AUTHENTICATING_PREFIXES = ("/api/scim", "/api/v1/scim")


def authenticates_itself(path: str) -> bool:
    """
    Args:
        path: A request path.

    Returns:
        Whether the endpoints below it check a bearer token that is theirs alone.
    """
    return any(
        path == prefix or path.startswith(prefix + "/") for prefix in SELF_AUTHENTICATING_PREFIXES
    )


def provisioning_identity(connection: HTTPConnection) -> str | None:
    """
    Name the provisioning source a request's bearer token belongs to.

    Args:
        connection: The incoming request.

    Returns:
        ``scim:<source>`` for a token of a source that is on, else None. A token that is not one
        is not noted for the lockout here: the endpoint it was presented to refuses and counts it.
    """
    token = bearer_token(connection)
    manager = get_global_token_manager()
    if not token or manager is None:
        return None
    from noust.core.accounts.scim import ScimManager

    source = ScimManager(manager.accounts.store, accounts=manager.accounts).authenticate(token)
    return f"scim:{source.name}" if source is not None else None


def rate_limit_identity(connection: HTTPConnection, client_ip: str) -> str | None:
    """
    Name the valid credential a request carries, for the rate limiter.

    Checks the channels a request can carry a credential on - the session
    cookie, the ``noust.token.`` subprotocol of a WebSocket handshake, the
    ``Authorization`` header - with :func:`check_credential`. A wrong one is
    noted on the request's :class:`CredentialLedger`, and counted against the
    lockout once: by the endpoint that refuses it, or when the request ends if
    no endpoint looked at it. A single-use WebSocket ticket is not looked at,
    because checking it would spend it.

    Args:
        connection: The incoming request or handshake.
        client_ip: The resolved client address, which a session is bound to.

    Returns:
        :func:`credential_key` of the first valid credential, or None when the
        request carries none, in which case it is counted by address.
    """
    if authenticates_itself(connection.url.path):
        return provisioning_identity(connection)
    candidates = [("cookie", connection.cookies.get(SESSION_COOKIE_NAME))]
    if connection.scope["type"] == "websocket":
        candidates.append(("websocket", subprotocol_token(connection)))
    candidates.append(("bearer", bearer_token(connection)))
    for source, credential in candidates:
        if not credential:
            continue
        try:
            payload = check_credential(credential, client_ip, connection)
        except CredentialRefused:
            # A real token from the wrong place is not a guess; the endpoint
            # refuses and audits it, and the request is counted by address.
            return None
        if payload is not None:
            return credential_key(payload)
        if _is_guess(credential):
            _note_wrong_credential(credential, source)
    return None


def check_credential(
    credential: str, client_ip: str, connection: HTTPConnection | None = None
) -> dict[str, Any] | None:
    """
    Verify one credential without recording anything.

    Args:
        credential: A session token, an API token or the master token.
        client_ip: Address presenting it.
        connection: The request or handshake that carries it. A fleet token
            is admitted against it (:func:`admit_fleet`); without one a fleet
            token is refused.

    Returns:
        The session payload, or None when the credential is not valid.

    Raises:
        CredentialRefused: When the credential is a fleet token that the
            connection may not carry, an API token refused on this request,
            or the master token under the ENS profile.
    """
    manager = get_global_token_manager()
    if manager is None or not credential:
        return None

    # The prefix routes to the token table and nowhere else, so an expired or
    # revoked API token cannot fall through to a slower master-token check.
    if credential.startswith(API_TOKEN_PREFIXES):
        token_payload = manager.verify_api_token(credential, client_ip)
        if token_payload is not None and is_fleet(token_payload):
            return admit_fleet(token_payload, connection)
        return token_payload

    payload = manager.verify_session_token(credential, client_ip)
    if payload is not None:
        return payload

    generation = manager.master_token_generation(credential)
    if generation is not None:
        grant = manager.master_grant()
        if grant == GRANT_RECOVERY:
            # ENS: the master token recovers access through the sign-in
            # page, as a session with its own second factor and audit trail;
            # as a standing Bearer it is a way around both (finding H1).
            raise CredentialRefused(
                401,
                "master_bearer_disabled",
                "The master token is not accepted as a credential on this server: its "
                "compliance controls leave it only account recovery.",
                "Sign in with an account. To recover access, sign in with the master "
                "token on the console's sign-in page, or use 'noust user' as root.",
                action="auth.break_glass",
            )
        return master_payload(client_ip, generation, grant)

    return None


def _audit_refusal(client_ip: str, resource: str, refusal: CredentialRefused) -> None:
    """
    Record a real credential presented where it is not accepted.

    Args:
        client_ip: Address it came from.
        resource: Path being reached.
        refusal: The refusal, carrying its action and reason.
    """
    audit = get_audit_logger()
    if audit is not None:
        audit.record(
            action=refusal.action,
            result="denied",
            client_ip=client_ip,
            resource=resource,
            detail=refusal.reason,
        )


def _audit_master_token_use(client_ip: str, resource: str, payload: Mapping[str, Any]) -> None:
    """
    Warn in the audit log that the master token itself was used as a credential.

    Every use is recorded: it is the one credential shared by whoever has
    root on this machine, and once accounts exist it is the break-glass one.

    Args:
        client_ip: Address it came from.
        resource: Path being reached.
        payload: The master token's payload.
    """
    audit = get_audit_logger()
    if audit is not None:
        audit.record(
            action="auth.break_glass",
            result="warning",
            client_ip=client_ip,
            actor="master",
            resource=resource,
            detail=(
                f"master token used directly as a credential ({payload.get('grant')}, "
                f"via {payload.get('source') or 'a header'})"
            ),
        )


def verify_credential(
    credential: str,
    client_ip: str,
    *,
    resource: str,
    source: str,
    connection: HTTPConnection | None = None,
) -> dict[str, Any] | None:
    """
    Verify one credential, counting the failure when it does not match.

    Args:
        credential: A session token or the master token.
        client_ip: Address presenting it.
        resource: Path being reached, for the audit record.
        source: Channel the credential arrived on: ``"cookie"`` or ``"bearer"``.
        connection: The request carrying it, which a fleet token is admitted
            against; a fleet token verified without one is refused.

    Returns:
        The session payload, tagged with the channel it arrived on under
        ``"source"`` for the record, or None when the credential is not valid.
        No policy reads the channel: CSRF and sudo mode follow the
        credential's ``"type"``, so a session cannot shed either by moving
        from the cookie to the header.

    Raises:
        CredentialRefused: When a real credential is refused on this request
            (a fleet token from anywhere but the tunnel, an API token outside
            its networks, the master token under the ENS profile). Audited,
            and not counted as a guess: the credential is real.
    """
    try:
        payload = check_credential(credential, client_ip, connection)
    except CredentialRefused as exc:
        _audit_refusal(client_ip, resource, exc)
        _mark_counted(credential)
        raise
    if payload is None:
        if _is_guess(credential):
            record_auth_failure(
                client_ip, resource, source, lockout_key=failure_key(credential, client_ip)
            )
            # The rate limiter may have noted the same guess; it is paid for.
            _mark_counted(credential)
        return None
    payload["source"] = source
    if payload.get("type") == "master":
        _audit_master_token_use(client_ip, resource, payload)
    return payload


def authenticate_connection(
    connection: HTTPConnection, ticket: str | None = None
) -> dict[str, Any] | None:
    """
    Authenticate a WebSocket handshake from every credential it may carry.

    Order: session cookie, ``noust.token.<token>`` subprotocol, ``Authorization:
    Bearer``, then a single-use ticket from ``POST /api/auth/ws-ticket``. A
    handshake that presents nothing usable counts as exactly one failure, not
    one per channel tried.

    Args:
        connection: The pending handshake.
        ticket: Single-use ticket from the query string, if any.

    Returns:
        The session payload, or None when the handshake is not authenticated.
    """
    manager = get_global_token_manager()
    if manager is None:
        return None

    config = get_security_config()
    client_ip = get_client_ip(connection, config)
    resource = connection.scope.get("path", "")

    candidates = (
        connection.cookies.get(SESSION_COOKIE_NAME),
        subprotocol_token(connection),
        bearer_token(connection),
    )
    try:
        for credential in candidates:
            if not credential:
                continue
            payload = check_credential(credential, client_ip, connection)
            if payload is not None:
                return payload

        if ticket:
            payload = manager.consume_ws_ticket(ticket, client_ip)
            if payload is not None and is_fleet(payload):
                return admit_fleet(payload, connection)
            if payload is not None:
                return payload
    except CredentialRefused as exc:
        _audit_refusal(client_ip, resource, exc)
        _mark_counted(*candidates)
        return None

    # A handshake that presented only retired fleet tokens is a central that
    # has not heard yet, not a guess from the address (see failure_key).
    presented = [credential for credential in candidates if credential]
    keys = {failure_key(credential, client_ip) for credential in presented}
    key = keys.pop() if len(keys) == 1 and not ticket else client_ip
    record_auth_failure(client_ip, resource, "websocket", lockout_key=key)
    # One failure per handshake, whichever channels the rate limiter noted.
    _mark_counted(*candidates)
    return None
