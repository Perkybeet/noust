# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Secret managers whose values an application's variables refer to.

``API_KEY={{secret:vault/shop/payments#key}}`` does not keep the key anywhere in
Noust: the reference is, and the value is fetched when the application is
deployed. A rotated secret reaches the next release, and a manager that cannot
be reached, or that does not have the secret, stops the deployment before
anything is built or switched (it fails closed), so an application is never
started with an empty or stale value that nobody noticed.

A *provider* is a secret manager Noust was told about (``noust secrets add``):
its kind (:data:`KINDS`), its address, the few settings that say where in it
Noust looks, and a token. The token is a secret of Noust's own and lives in the
secret store under ``secret-providers/<name>``, never in the database or in
``config.yaml``. A provider may be restricted to one team, so a team's
application cannot name a manager that holds another team's secrets.

The value that is fetched does end up in the application's environment file
(``0600``, owned by the account the application runs as): systemd has to load
it from somewhere. It is in no other place Noust keeps: not the store, not the
audit trail, not an API response, not a log (the deploy's log is scrubbed of it
like any secret variable's value).
"""

from __future__ import annotations

import json
import logging
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import IO, Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode

from noust import __version__
from noust.core.accounts.oidc_model import check_endpoint_url
from noust.core.exceptions import IntegrationError, ValidationError
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore, get_store
from noust.core.variables.expressions import PROVIDER_NAME

_log = logging.getLogger(__name__)

#: Seconds before a request to a secret manager is abandoned: a deploy waits on it.
REQUEST_TIMEOUT = 15.0

#: Bytes of an answer read at most.
MAX_RESPONSE_BYTES = 1024 * 1024

#: The secret store's namespace for providers' tokens.
SECRET_NAMESPACE = "secret-providers"  # noqa: S105 - a directory name, not a credential

Opener = Callable[..., IO[bytes]]


class SecretProviderError(IntegrationError):
    """A secret could not be fetched; the message names the provider and the path, never a value."""


@dataclass(frozen=True)
class ProviderConfig:
    """
    A secret manager Noust can fetch from.

    Attributes:
        name: What references call it (``vault`` in ``{{secret:vault/...}}``).
        kind: One of :data:`KINDS`.
        address: Where it is; None for the kind's public service.
        settings: The non-secret settings of the kind (Vault's mount, Doppler's
            project and config...).
        team_id: The team allowed to name it; None for the whole server.
        created_at: When it was added.
        created_by: Who added it.
    """

    name: str
    kind: str
    address: str | None = None
    settings: Mapping[str, str] = field(default_factory=dict)
    team_id: int | None = None
    created_at: str = ""
    created_by: str | None = None


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Refuses every redirect: a token is never sent to a place the operator did not name."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class SecretHttp:
    """
    JSON over HTTPS to one secret manager.

    Args:
        timeout: Seconds per request.
        opener: Replacement for ``urlopen``'s calling convention; the tests' fake manager.
    """

    def __init__(self, *, timeout: float = REQUEST_TIMEOUT, opener: Opener | None = None) -> None:
        self.timeout = timeout
        self._opener: Opener = opener or urllib.request.build_opener(_NoRedirects).open

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
        what: str,
    ) -> dict[str, Any]:
        """
        Send one request and read the JSON object it answers.

        Args:
            method: ``GET`` or ``POST``.
            url: The address, already checked.
            headers: Extra headers (the token).
            body: A JSON body.
            what: The secret, for the messages ("vault/shop/key").

        Returns:
            The JSON object.

        Raises:
            SecretProviderError: Unreachable, a refusal (with the manager's own
                words), or an answer that is not a JSON object.
        """
        request_headers = {
            "Accept": "application/json",
            "User-Agent": f"noust/{__version__}",
            **(headers or {}),
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            raise SecretProviderError(
                f"The secret manager refused {what} ({exc.code})",
                details=_refusal_text(exc),
            ) from None
        except (URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", None) or exc
            raise SecretProviderError(
                f"The secret manager could not be reached for {what}",
                details=f"Check this server's outbound HTTPS access to it. It said: {reason}",
            ) from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise SecretProviderError(f"The secret manager's answer for {what} is too large")
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise SecretProviderError(
                f"The secret manager did not answer JSON for {what}"
            ) from None
        if not isinstance(parsed, dict):
            raise SecretProviderError(f"The secret manager did not answer an object for {what}")
        return parsed


def _refusal_text(exc: HTTPError) -> str:
    """
    Args:
        exc: urllib's error, carrying the response.

    Returns:
        The manager's own explanation, verbatim and short, or a hint by status.
    """
    try:
        body = json.loads(exc.read(64 * 1024) or b"{}")
    except (OSError, ValueError, AttributeError):
        body = {}
    said = ""
    if isinstance(body, dict):
        errors = body.get("errors")
        said = (
            "; ".join(str(item) for item in errors)
            if isinstance(errors, list)
            else str(body.get("message") or body.get("error") or "")
        )
    if said:
        return said[:300]
    return {
        401: "The manager did not accept the token. Replace it with 'noust secrets add NAME --token-stdin'.",
        403: "The token may not read this secret. Check its policy in the manager.",
        404: "The manager has no such secret. Check the path and the provider's settings.",
    }.get(exc.code, "Try again in a moment; check the manager's status.")


class SecretProvider:
    """
    One kind of secret manager. Subclasses fetch a value from their API.

    Args:
        config: The provider.
        token: Its token.
        http: The client; tests pass one over a fake.
    """

    #: The name of the kind.
    kind = ""
    #: The public address of the hosted service, when there is one.
    default_address: str | None = None
    #: Settings the kind needs, with a one-line description.
    required: dict[str, str] = {}
    #: Settings the kind accepts and can do without.
    optional: dict[str, str] = {}

    def __init__(self, config: ProviderConfig, token: str, http: SecretHttp | None = None) -> None:
        self.config = config
        self._token = token
        self.http = http or SecretHttp()

    @property
    def base(self) -> str:
        """The manager's address without a trailing slash."""
        return (self.config.address or self.default_address or "").rstrip("/")

    def fetch(self, path: str) -> str:
        """
        Args:
            path: What follows the provider's name in the reference.

        Returns:
            The secret's value.

        Raises:
            SecretProviderError: It could not be fetched.
        """
        raise NotImplementedError

    def _setting(self, key: str) -> str:
        value = str(self.config.settings.get(key) or "").strip()
        if not value:
            raise SecretProviderError(
                f"The provider {self.config.name} has no {key} set",
                details=f"Add it again: noust secrets add {self.config.name} --kind {self.kind} "
                f"--setting {key}=...",
            )
        return value


class VaultProvider(SecretProvider):
    """
    HashiCorp Vault's key-value engine, version 1 or 2.

    The path is ``secret/path#field``; ``#field`` may be left out when the
    secret holds exactly one field.
    """

    kind = "vault"
    required: dict[str, str] = {}
    optional = {
        "mount": "The key-value engine's mount (default: secret)",
        "kv_version": "1 or 2 (default: 2)",
        "namespace": "A Vault Enterprise namespace",
    }

    def fetch(self, path: str) -> str:
        secret_path, _, field_name = path.partition("#")
        secret_path = secret_path.strip("/")
        mount = str(self.config.settings.get("mount") or "secret").strip("/")
        version = str(self.config.settings.get("kv_version") or "2")
        if not secret_path:
            raise SecretProviderError("A Vault reference needs a path", details="vault/PATH#FIELD")
        if version == "1":
            url = f"{self.base}/v1/{quote(mount, safe='/')}/{quote(secret_path, safe='/')}"
        else:
            url = f"{self.base}/v1/{quote(mount, safe='/')}/data/{quote(secret_path, safe='/')}"
        headers = {"X-Vault-Token": self._token}
        namespace = str(self.config.settings.get("namespace") or "")
        if namespace:
            headers["X-Vault-Namespace"] = namespace
        what = f"{self.config.name}/{path}"
        answer = self.http.request("GET", url, headers=headers, what=what)
        data = answer.get("data")
        if version != "1" and isinstance(data, dict):
            data = data.get("data")
        if not isinstance(data, dict) or not data:
            raise SecretProviderError(f"Vault has no data at {what}")
        if field_name:
            if field_name not in data:
                raise SecretProviderError(
                    f"The Vault secret {what} has no field {field_name!r}",
                    details=f"It has: {', '.join(sorted(map(str, data)))}.",
                )
            return _text(data[field_name], what)
        if len(data) != 1:
            raise SecretProviderError(
                f"The Vault secret {what} has {len(data)} fields",
                details=f"Name one: {self.config.name}/{secret_path}#FIELD "
                f"(fields: {', '.join(sorted(map(str, data)))}).",
            )
        return _text(next(iter(data.values())), what)


class DopplerProvider(SecretProvider):
    """Doppler: a service token reads one config of one project; the path is the secret's name."""

    kind = "doppler"
    default_address = "https://api.doppler.com"
    required = {"project": "The Doppler project", "config": "The config (dev, stg, prd...)"}

    def fetch(self, path: str) -> str:
        project = self._setting("project")
        config = self._setting("config")
        what = f"{self.config.name}/{path}"
        query = urlencode({"project": project, "config": config, "name": path})
        answer = self.http.request(
            "GET",
            f"{self.base}/v3/configs/config/secret?{query}",
            headers={"Authorization": f"Bearer {self._token}"},
            what=what,
        )
        value = answer.get("value")
        if not isinstance(value, dict):
            raise SecretProviderError(f"Doppler did not return {what}")
        found = value.get("computed")
        if found is None:
            found = value.get("raw")
        return _text(found, what)


class InfisicalProvider(SecretProvider):
    """
    Infisical: a machine identity's access token (or its universal-auth client
    id in ``client_id`` and secret as the token); the path is the secret's name.
    """

    kind = "infisical"
    default_address = "https://app.infisical.com"
    required = {
        "project_id": "The Infisical project's id",
        "environment": "The environment's slug (dev, staging, prod...)",
    }
    optional = {
        "secret_path": "The folder the secrets are in (default: /)",
        "client_id": "A universal-auth client id; the stored token is then its client secret",
    }

    def fetch(self, path: str) -> str:
        project_id = self._setting("project_id")
        environment = self._setting("environment")
        folder = str(self.config.settings.get("secret_path") or "/")
        what = f"{self.config.name}/{path}"
        access = self._access_token(what)
        query = urlencode(
            {"workspaceId": project_id, "environment": environment, "secretPath": folder}
        )
        answer = self.http.request(
            "GET",
            f"{self.base}/api/v3/secrets/raw/{quote(path, safe='')}?{query}",
            headers={"Authorization": f"Bearer {access}"},
            what=what,
        )
        secret = answer.get("secret")
        if not isinstance(secret, dict):
            raise SecretProviderError(f"Infisical did not return {what}")
        return _text(secret.get("secretValue"), what)

    def _access_token(self, what: str) -> str:
        """
        Args:
            what: The secret being fetched, for messages.

        Returns:
            The bearer token to read with: the stored token, or the one a
            universal-auth login with it gives.
        """
        client_id = str(self.config.settings.get("client_id") or "")
        if not client_id:
            return self._token
        answer = self.http.request(
            "POST",
            f"{self.base}/api/v1/auth/universal-auth/login",
            body={"clientId": client_id, "clientSecret": self._token},
            what=what,
        )
        token = answer.get("accessToken")
        if not isinstance(token, str) or not token:
            raise SecretProviderError(f"Infisical did not log in for {what}")
        return token


def _text(value: object, what: str) -> str:
    """
    Args:
        value: What the manager returned for the secret.
        what: The secret, for the message.

    Returns:
        The value as text.

    Raises:
        SecretProviderError: The manager had no value, or one that cannot be a variable's.
    """
    if isinstance(value, bool) or value is None or not isinstance(value, (str, int, float)):
        raise SecretProviderError(f"The secret manager has no text value for {what}")
    return str(value)


#: The kinds Noust can fetch from.
KINDS: dict[str, type[SecretProvider]] = {
    VaultProvider.kind: VaultProvider,
    DopplerProvider.kind: DopplerProvider,
    InfisicalProvider.kind: InfisicalProvider,
}


def validate_provider(
    name: str,
    kind: str,
    address: str | None,
    settings: Mapping[str, str],
) -> tuple[str | None, dict[str, str]]:
    """
    Check what an operator typed for a provider.

    Args:
        name: What references will call it.
        kind: One of :data:`KINDS`.
        address: Where it is, or None for the kind's public service.
        settings: Its settings.

    Returns:
        The checked address (None for the kind's public service) and settings.

    Raises:
        ValidationError: An unknown kind, a name references cannot use, an
            address that is not HTTPS, a setting the kind does not know, or a
            required one missing.
    """
    if not PROVIDER_NAME.fullmatch(name or ""):
        raise ValidationError(
            f"Not a name for a secret manager: {name!r}",
            details="Use lower-case letters, digits and dashes, such as 'vault' or 'doppler-prod'.",
            field="name",
        )
    cls = KINDS.get(kind)
    if cls is None:
        raise ValidationError(
            f"Unknown secret manager: {kind!r}",
            details=f"Use one of: {', '.join(sorted(KINDS))}.",
            field="kind",
        )
    checked_address = None
    if address:
        checked_address = check_endpoint_url(
            address, "secret manager address", field_name="address"
        )
    elif cls.default_address is None:
        raise ValidationError(
            f"{kind} needs an address", details="Give --address https://...", field="address"
        )
    known = {*cls.required, *cls.optional}
    unknown = sorted(set(settings) - known - {"address"})
    if unknown:
        raise ValidationError(
            f"{kind} has no setting {', '.join(unknown)}",
            details=f"It takes: {', '.join(sorted(known)) or 'nothing'}.",
            field="settings",
        )
    cleaned = {key: str(value).strip() for key, value in settings.items() if key != "address"}
    missing = [key for key in cls.required if not cleaned.get(key)]
    if missing:
        raise ValidationError(
            f"{kind} needs: {', '.join(missing)}",
            details="; ".join(f"{key}: {cls.required[key]}" for key in missing),
            field="settings",
        )
    return checked_address, cleaned


class ProviderRegistry:
    """
    The secret managers the store knows, and the one place a reference is fetched.

    Args:
        store: The store; the process-wide one when None.
        secrets: Where tokens are kept.
        http: The HTTP client every provider fetches with; tests pass a fake.
    """

    def __init__(
        self,
        store: NoustStore | None = None,
        secrets: SecretStore | None = None,
        http: SecretHttp | None = None,
    ) -> None:
        self._store = store
        self._secrets = secrets
        self._http = http

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    @property
    def secrets(self) -> SecretStore:
        """Where the tokens are."""
        return self._secrets or SecretStore()

    def list(self) -> list[ProviderConfig]:
        """
        Returns:
            Every provider, by name.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT name, kind, address, settings, team_id, created_at, created_by "
                "FROM secret_providers ORDER BY name"
            )
            rows = cursor.fetchall()
        return [_config(tuple(row)) for row in rows]

    def get(self, name: str) -> ProviderConfig | None:
        """
        Args:
            name: The provider's name.

        Returns:
            It, or None.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT name, kind, address, settings, team_id, created_at, created_by "
                "FROM secret_providers WHERE name = ?",
                (name,),
            )
            row = cursor.fetchone()
        return _config(tuple(row)) if row is not None else None

    def save(
        self,
        name: str,
        kind: str,
        *,
        token: str | None,
        address: str | None = None,
        settings: Mapping[str, str] | None = None,
        team_id: int | None = None,
        actor: str | None = None,
    ) -> ProviderConfig:
        """
        Add a provider, or change one.

        Args:
            name: What references call it.
            kind: One of :data:`KINDS`.
            token: Its token; None keeps the stored one (a change of settings).
            address: Where it is.
            settings: Its non-secret settings.
            team_id: Restrict it to a team.
            actor: Who did it.

        Returns:
            The provider as stored.

        Raises:
            ValidationError: See :func:`validate_provider`, or no token for a new provider.
        """
        checked_address, cleaned = validate_provider(name, kind, address, settings or {})
        existing = self.get(name)
        if token is None and existing is None:
            raise ValidationError(
                f"{name} needs a token", details="Give it with --token-stdin.", field="token"
            )
        if token is not None and (not token.strip() or any(ord(c) < 32 for c in token.strip())):
            raise ValidationError("The token is empty or has control characters", field="token")
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if token is not None:
            self.secrets.write(f"{SECRET_NAMESPACE}/{name}", token.strip())
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO secret_providers (name, kind, address, settings, team_id, "
                "created_at, created_by, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET kind = excluded.kind, address = excluded.address, "
                "settings = excluded.settings, team_id = excluded.team_id, "
                "updated_at = excluded.updated_at",
                (
                    name,
                    kind,
                    checked_address,
                    json.dumps(cleaned, sort_keys=True),
                    team_id,
                    stamp,
                    actor,
                    stamp,
                ),
            )
        stored = self.get(name)
        return stored or ProviderConfig(name, kind, checked_address, cleaned, team_id, stamp, actor)

    def delete(self, name: str) -> bool:
        """
        Args:
            name: The provider's name.

        Returns:
            Whether there was one. Its token goes with it.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM secret_providers WHERE name = ?", (name,))
            removed = cursor.rowcount > 0
        if removed:
            self.secrets.delete(f"{SECRET_NAMESPACE}/{name}")
        return removed

    def fetch(self, name: str, path: str, *, team_id: int | None = None) -> str:
        """
        Fetch one secret.

        Args:
            name: The provider.
            path: The path inside it.
            team_id: The team of the application asking; a provider restricted
                to another team is refused as if it did not exist.

        Returns:
            The secret's value.

        Raises:
            SecretProviderError: No such provider (for this team), no token, or
                the manager could not give the value.
        """
        config = self.get(name)
        if config is None or (config.team_id is not None and config.team_id != team_id):
            raise SecretProviderError(
                f"There is no secret manager called {name}",
                details="List the ones this server knows with 'noust secrets list'.",
            )
        token = self.secrets.read(f"{SECRET_NAMESPACE}/{name}")
        if not token:
            raise SecretProviderError(
                f"The secret manager {name} has no token",
                details=f"Set it again: noust secrets add {name} --kind {config.kind} --token-stdin",
            )
        return KINDS[config.kind](config, token, self._http).fetch(path)

    def test(self, name: str) -> str:
        """
        Check that a provider answers, without reading any secret of the operator's.

        Args:
            name: The provider.

        Returns:
            A line saying what was checked.

        Raises:
            SecretProviderError: It does not answer, or has no token.
        """
        config = self.get(name)
        if config is None:
            raise SecretProviderError(f"There is no secret manager called {name}")
        token = self.secrets.read(f"{SECRET_NAMESPACE}/{name}")
        if not token:
            raise SecretProviderError(f"The secret manager {name} has no token")
        provider = KINDS[config.kind](config, token, self._http)
        return _probe(provider)


def _probe(provider: SecretProvider) -> str:
    """
    Ask a provider something that proves its address and token work.

    Args:
        provider: The provider.

    Returns:
        What was proven.

    Raises:
        SecretProviderError: It did not answer.
    """
    what = provider.config.name
    if isinstance(provider, VaultProvider):
        headers = {"X-Vault-Token": provider._token}
        provider.http.request(
            "GET", f"{provider.base}/v1/auth/token/lookup-self", headers=headers, what=what
        )
        return f"{what}: the token is valid"
    if isinstance(provider, DopplerProvider):
        provider.http.request(
            "GET",
            f"{provider.base}/v3/me",
            headers={"Authorization": f"Bearer {provider._token}"},
            what=what,
        )
        return f"{what}: the token is valid"
    if isinstance(provider, InfisicalProvider):
        access = provider._access_token(what)
        provider.http.request(
            "GET",
            f"{provider.base}/api/v1/auth/token",
            headers={"Authorization": f"Bearer {access}"},
            what=what,
        )
        return f"{what}: the token is valid"
    raise SecretProviderError(f"{what}: this kind cannot be tested")


def _config(row: tuple[object, ...]) -> ProviderConfig:
    """
    Args:
        row: name, kind, address, settings, team_id, created_at, created_by.

    Returns:
        The provider.
    """
    try:
        settings = json.loads(str(row[3]) or "{}")
    except ValueError:
        settings = {}
    return ProviderConfig(
        name=str(row[0]),
        kind=str(row[1]),
        address=None if row[2] is None else str(row[2]),
        settings={str(k): str(v) for k, v in settings.items()}
        if isinstance(settings, dict)
        else {},
        team_id=None if row[4] is None else int(str(row[4])),
        created_at=str(row[5]),
        created_by=None if row[6] is None else str(row[6]),
    )
