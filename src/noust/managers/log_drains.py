# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where the logs of applications are sent besides the journal (checklist B-73).

An application's logs are in the journal, and the console reads them there. A drain
sends them on: to Grafana Loki, to an Axiom dataset, to anything that takes a JSON
array over HTTP (Fluent Bit's ``http`` input, Vector, Logstash), to an OpenTelemetry
collector, or to a remote syslog. They are the destinations of the whole server, set
here and shipped by :mod:`noust.managers.log_shipping`, which reads the journal from
where each destination got to and sends what is new.

**Secrets before they leave.** Each line is scrubbed of the values the application
holds as secrets (its environment, Noust's own credentials) before it is sent; the
destination's own token never touches a column, the audit trail or an error: it is in
the secret store, under ``log-drains/<name>``, and leaves in one header.

**Destinations that are not trusted by accident.** A URL has no user or password in
it, a plaintext ``http`` destination is allowed only on this machine (or anywhere
outside the ENS profile, with a warning in ``status``), and a syslog over TLS checks
the certificate and the name, with a CA or a pin if given.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit

from noust.central import require_server_role
from noust.core import audit, log_drain_store
from noust.core.audit.settings import SyslogDestination
from noust.core.exceptions import NoustError, ValidationError
from noust.core.log_drain_store import KINDS, MAX_NAME_LENGTH, NAME, Drain, secret_name
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore, get_store
from noust.managers.log_transports import (
    AxiomTransport,
    DeliveryError,
    JsonTransport,
    LogEvent,
    LokiTransport,
    OtlpTransport,
    SyslogTransport,
    Transport,
)
from noust.validators.domain import validate_domain

logger = logging.getLogger(__name__)

#: Where Axiom's API is, unless the organisation's region says otherwise.
AXIOM_URL = "https://api.axiom.co"

#: The transports of a remote syslog.
SYSLOG_TRANSPORTS = ("udp", "tcp", "tls")

#: A label name Loki, OpenTelemetry and most destinations accept.
_LABEL = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,40}$")

#: Most static labels a drain adds to every line.
MAX_LABELS = 10

#: An Axiom dataset's name.
_DATASET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

#: A hexadecimal SHA-256.
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")

#: How the destination is told who is asking.
AUTH_KINDS = ("none", "bearer", "basic")

#: The settings each kind takes besides the common ones.
_KIND_KEYS: dict[str, tuple[str, ...]] = {
    "loki": ("url", "tenant", "username"),
    "axiom": ("url", "dataset"),
    "http": ("url", "auth", "username"),
    "otlp": ("url", "auth", "username"),
    "syslog": (
        "transport",
        "host",
        "port",
        "facility",
        "ca",
        "client_cert",
        "client_key",
        "pin_sha256",
        "server_name",
    ),
}

#: Settings every kind takes.
_COMMON_KEYS = ("labels",)


def _is_local(host: str) -> bool:
    """
    Tell whether a host name is this machine.

    Args:
        host: A host name or address.

    Returns:
        True for the loopback names and addresses.
    """
    return host in ("localhost", "::1") or host.startswith("127.")


def check_url(raw: str, *, field: str = "url") -> str:
    """
    Accept the address of a destination.

    Args:
        raw: What the operator gave.
        field: The setting it is, for the error.

    Returns:
        The address.

    Raises:
        ValidationError: It is not an http or https address, holds a user or a
            password, or is plaintext to another machine under the ENS profile.
    """
    how = "Give the address of the destination, such as https://logs.example.com."
    if (
        not isinstance(raw, str)
        or not raw
        or len(raw) > 2048
        or any(ord(char) < 32 for char in raw)
    ):
        raise ValidationError(f"{raw!r} is not an address", details=how, field=field)
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValidationError(f"{raw!r} is not an http or https address", details=how, field=field)
    if parts.username or parts.password:
        raise ValidationError(
            "An address cannot hold a user or a password",
            details="Put the credentials in the token or the user and password the drain "
            "keeps in the secret store: an address is shown and logged.",
            field=field,
        )
    if parts.query or parts.fragment:
        raise ValidationError(
            "An address cannot hold a query or a fragment",
            details="Name the destination by its address; the dataset, the tenant and the "
            "labels are settings of their own.",
            field=field,
        )
    if parts.scheme == "http" and not _is_local(parts.hostname):
        from noust.core.ens import profile

        if profile.is_ens():
            raise ValidationError(
                "The ENS profile ships logs only over TLS",
                details=f"Use https:// for {parts.hostname}.",
                field=field,
            )
    return raw.rstrip("/") if parts.path in ("", "/") else raw


def _file(raw: Any, field: str) -> str:
    """
    Accept the path of a certificate or key file.

    Args:
        raw: What the operator gave.
        field: The setting it is.

    Returns:
        The absolute path of a readable file.

    Raises:
        ValidationError: It is relative or not a readable file.
    """
    if not isinstance(raw, str) or not raw.startswith("/") or not os.access(raw, os.R_OK):
        raise ValidationError(
            f"{raw!r} is not a file Noust can read",
            details="Give the absolute path of the file.",
            field=field,
        )
    return raw


def _labels(raw: Any) -> dict[str, str]:
    """
    Accept the labels added to every line.

    Args:
        raw: A mapping, or nothing.

    Returns:
        The labels.

    Raises:
        ValidationError: There are too many, or a name or a value is unusable.
    """
    if raw in (None, {}):
        return {}
    if not isinstance(raw, Mapping) or len(raw) > MAX_LABELS:
        raise ValidationError(f"Labels are at most {MAX_LABELS} names with values", field="labels")
    cleaned: dict[str, str] = {}
    for key, value in raw.items():
        if (
            not isinstance(key, str)
            or _LABEL.match(key) is None
            or key in ("job", "app", "process", "host")
            or not isinstance(value, str)
            or len(value) > 100
            or any(ord(char) < 32 for char in value)
        ):
            raise ValidationError(
                f"{key!r} is not a label Noust can add",
                details="A label is a name of letters, digits and underscores with a short text "
                "value; job, app, process and host are Noust's own.",
                field="labels",
            )
        cleaned[key] = value
    return cleaned


def validate_settings(kind: str, raw: Mapping[str, Any], *, has_secret: bool) -> dict[str, Any]:
    """
    Check what a destination of a kind is told.

    Args:
        kind: ``loki``, ``axiom``, ``http``, ``otlp`` or ``syslog``.
        raw: The settings, as the operator gave them.
        has_secret: Whether the drain has a token or a password.

    Returns:
        The settings, normalised, without anything the kind does not take.

    Raises:
        ValidationError: A setting is missing, unknown or not one a destination
            can have; the error names the field.
    """
    if kind not in KINDS:
        raise ValidationError(
            f"{kind!r} is not a kind of destination",
            details=f"Use one of: {', '.join(KINDS)}.",
            field="kind",
        )
    allowed = (*_KIND_KEYS[kind], *_COMMON_KEYS)
    unknown = sorted(set(raw) - set(allowed))
    if unknown:
        raise ValidationError(
            f"{unknown[0]!r} is not a setting of a {kind} destination",
            details=f"Its settings are: {', '.join(allowed)}.",
            field=unknown[0],
        )
    cleaned: dict[str, Any] = {}
    labels = _labels(raw.get("labels"))
    if labels:
        cleaned["labels"] = labels
    if kind == "syslog":
        transport = str(raw.get("transport") or "tls")
        if transport not in SYSLOG_TRANSPORTS:
            raise ValidationError(
                f"{transport!r} is not a syslog transport",
                details=f"Use one of: {', '.join(SYSLOG_TRANSPORTS)}.",
                field="transport",
            )
        host = raw.get("host")
        if not isinstance(host, str) or not host or any(c in host for c in " /\0\n"):
            raise ValidationError("A syslog destination needs a host", field="host")
        port = raw.get("port") or (6514 if transport == "tls" else 514)
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValidationError(f"{port!r} is not a port", field="port")
        facility = raw.get("facility", 16)
        if isinstance(facility, bool) or not isinstance(facility, int) or not 0 <= facility <= 23:
            raise ValidationError("A syslog facility is a number from 0 to 23", field="facility")
        cleaned.update(transport=transport, host=host, port=port, facility=facility)
        if transport != "tls":
            from noust.core.ens import profile

            if profile.is_ens() and not _is_local(host):
                raise ValidationError(
                    "The ENS profile ships logs only over TLS",
                    details=f"Use the tls transport for {host}.",
                    field="transport",
                )
        for key in ("ca", "client_cert", "client_key"):
            if raw.get(key):
                cleaned[key] = _file(raw[key], key)
        if bool(cleaned.get("client_cert")) != bool(cleaned.get("client_key")):
            raise ValidationError(
                "A client certificate needs its key, and the other way round", field="client_cert"
            )
        if raw.get("pin_sha256"):
            if _SHA256.match(str(raw["pin_sha256"])) is None:
                raise ValidationError("A pin is 64 hexadecimal digits", field="pin_sha256")
            cleaned["pin_sha256"] = str(raw["pin_sha256"]).lower()
        if raw.get("server_name"):
            cleaned["server_name"] = str(raw["server_name"])
        return cleaned

    url = raw.get("url") or (AXIOM_URL if kind == "axiom" else None)
    if url is None:
        raise ValidationError(f"A {kind} destination needs an address", field="url")
    cleaned["url"] = check_url(str(url))
    if kind == "axiom":
        dataset = raw.get("dataset")
        if not isinstance(dataset, str) or _DATASET.match(dataset) is None:
            raise ValidationError(
                "An Axiom destination needs its dataset",
                details="A dataset is letters, digits, '.', '_' and '-'.",
                field="dataset",
            )
        cleaned["dataset"] = dataset
        if not has_secret:
            raise ValidationError("An Axiom destination needs its API token", field="secret")
    if kind == "loki":
        if raw.get("tenant"):
            tenant = str(raw["tenant"])
            if len(tenant) > 100 or any(ord(char) < 32 for char in tenant):
                raise ValidationError("A tenant is a short text", field="tenant")
            cleaned["tenant"] = tenant
    if kind in ("loki", "http", "otlp"):
        auth = raw.get("auth") or ("basic" if raw.get("username") else "bearer")
        if kind != "loki":
            if auth not in AUTH_KINDS:
                raise ValidationError(
                    f"{auth!r} is not a way to authenticate",
                    details=f"Use one of: {', '.join(AUTH_KINDS)}.",
                    field="auth",
                )
            cleaned["auth"] = auth
        if raw.get("username"):
            user = str(raw["username"])
            if len(user) > 200 or any(ord(char) < 32 for char in user) or ":" in user:
                raise ValidationError("A user name is a short text without ':'", field="username")
            cleaned["username"] = user
        if cleaned.get("auth") == "basic" and not cleaned.get("username"):
            raise ValidationError("Basic authentication needs a user name", field="username")
    return cleaned


def validate_name(name: str) -> str:
    """
    Accept a destination's name.

    Args:
        name: What the operator gave.

    Returns:
        The name.

    Raises:
        ValidationError: It is not a short lower-case label.
    """
    if not isinstance(name, str) or len(name) > MAX_NAME_LENGTH or NAME.fullmatch(name) is None:
        raise ValidationError(
            f"{name!r} is not a name for a destination",
            details="Use a short lower-case name such as loki or grafana-cloud.",
            field="name",
        )
    return name


def build_transport(drain: Drain, secret: str | None) -> Transport:
    """
    Make the transport of a destination.

    Args:
        drain: The destination.
        secret: Its token or password, when it has one.

    Returns:
        The transport.
    """
    settings = drain.settings
    labels = dict(settings.get("labels") or {})
    if drain.kind == "syslog":
        return SyslogTransport(
            SyslogDestination(
                transport=settings["transport"],
                host=settings["host"],
                port=settings["port"],
                facility=settings.get("facility", 16),
                ca=settings.get("ca"),
                client_cert=settings.get("client_cert"),
                client_key=settings.get("client_key"),
                pin_sha256=settings.get("pin_sha256"),
                server_name=settings.get("server_name"),
            )
        )
    if drain.kind == "axiom":
        return AxiomTransport(
            settings["url"], dataset=settings["dataset"], secret=secret, labels=labels
        )
    if drain.kind == "loki":
        username = settings.get("username")
        return LokiTransport(
            settings["url"],
            tenant=settings.get("tenant"),
            labels=labels,
            auth="basic" if username else ("bearer" if secret else None),
            username=username,
            secret=secret,
        )
    auth = settings.get("auth") if secret else "none"
    username = settings.get("username")
    if drain.kind == "otlp":
        return OtlpTransport(
            settings["url"], auth=auth, username=username, secret=secret, labels=labels
        )
    return JsonTransport(
        settings["url"], auth=auth, username=username, secret=secret, labels=labels
    )


class LogDrains:
    """
    Defines, changes and tests the destinations of logs.

    Attributes:
        store: The store the destinations are kept in.
    """

    def __init__(
        self, *, store: NoustStore | None = None, secrets: SecretStore | None = None
    ) -> None:
        """
        Initialize the manager.

        Args:
            store: The store; the process-wide one by default.
            secrets: Where tokens and passwords are kept; Noust's by default.
        """
        self._store = store
        self._secrets = secrets

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    @property
    def secrets(self) -> SecretStore:
        """The secret store."""
        return self._secrets or SecretStore()

    def list_drains(self) -> list[Drain]:
        """
        Returns:
            Every destination by name.
        """
        return log_drain_store.list_drains(store=self.store)

    def get(self, name: str) -> Drain:
        """
        Read a destination.

        Args:
            name: Its name.

        Returns:
            The destination.

        Raises:
            ValidationError: There is none of that name.
        """
        found = log_drain_store.get_drain(name, store=self.store)
        if found is None:
            raise ValidationError(
                f"There is no destination called {name!r}",
                details="See them with: noust logdrain list",
                field="name",
            )
        return found

    def has_secret(self, name: str) -> bool:
        """
        Tell whether a destination has a token or a password.

        Args:
            name: Its name.

        Returns:
            True when one is kept.
        """
        return self.secrets.read(secret_name(name)) is not None

    def secret_of(self, name: str) -> str | None:
        """
        Read a destination's token or password.

        Args:
            name: Its name.

        Returns:
            The secret, or None.
        """
        return self.secrets.read(secret_name(name))

    def _apps(self, apps: Sequence[str]) -> tuple[str, ...]:
        """
        Check the applications a destination is narrowed to.

        Args:
            apps: Domains.

        Returns:
            The domains, validated and known.

        Raises:
            ValidationError: One is not deployed.
        """
        cleaned = []
        for domain in apps:
            valid = validate_domain(domain)
            if self.store.get_app(valid) is None:
                raise ValidationError(
                    f"{valid} is not deployed here",
                    details="A destination can be narrowed to the applications of this server.",
                    field="apps",
                )
            cleaned.append(valid)
        return tuple(sorted(set(cleaned)))

    def save(
        self,
        name: str,
        kind: str,
        settings: Mapping[str, Any],
        *,
        apps: Sequence[str] = (),
        secret: str | None = None,
        keep_secret: bool = False,
        enabled: bool = True,
        actor: str = "system",
    ) -> Drain:
        """
        Create a destination, or change one.

        Args:
            name: Its name.
            kind: Its kind.
            settings: What it is told (see :func:`validate_settings`).
            apps: The applications it receives the logs of; none is all of them.
            secret: Its token or password.
            keep_secret: Leave the token it has as it is (the console edits a
                destination without showing it).
            enabled: Whether the shipper sends to it.
            actor: Who asked, for the audit trail.

        Returns:
            The destination as stored.

        Raises:
            ValidationError: A setting is not one a destination can have.
        """
        require_server_role("Log destinations")
        checked = validate_name(name)
        existing = log_drain_store.get_drain(checked, store=self.store)
        if existing is not None and existing.kind != kind:
            raise ValidationError(
                f"{checked} is a {existing.kind} destination: remove it to make another kind",
                field="kind",
            )
        has_secret = bool(secret) or (
            keep_secret and existing is not None and self.has_secret(checked)
        )
        cleaned = validate_settings(kind, settings, has_secret=has_secret)
        drain = Drain(
            name=checked,
            kind=kind,
            settings=cleaned,
            apps=self._apps(apps),
            enabled=enabled,
        )
        if secret:
            self.secrets.write(secret_name(checked), secret)
        elif not keep_secret and existing is not None:
            self.secrets.delete(secret_name(checked))
        log_drain_store.put_drain(drain, updated_by=actor, store=self.store)
        audit.record(
            "logs.drain",
            target=f"drain:{checked}",
            details={
                "action": "set" if existing is None else "change",
                "kind": kind,
                "apps": list(drain.apps),
                "authenticated": has_secret,
                "by": actor,
            },
        )
        return log_drain_store.get_drain(checked, store=self.store) or drain

    def remove(self, name: str, *, actor: str = "system") -> None:
        """
        Forget a destination and its token.

        Args:
            name: Its name.
            actor: Who asked, for the audit trail.

        Raises:
            ValidationError: There is none of that name.
        """
        require_server_role("Log destinations")
        found = self.get(name)
        log_drain_store.delete_drain(found.name, store=self.store)
        self.secrets.delete(secret_name(found.name))
        audit.record(
            "logs.drain",
            target=f"drain:{found.name}",
            details={"action": "remove", "kind": found.kind, "by": actor},
        )

    def set_enabled(self, name: str, enabled: bool, *, actor: str = "system") -> Drain:
        """
        Turn a destination on or off.

        Args:
            name: Its name.
            enabled: Whether the shipper sends to it.
            actor: Who asked, for the audit trail.

        Returns:
            The destination.

        Raises:
            ValidationError: There is none of that name.
        """
        require_server_role("Log destinations")
        found = self.get(name)
        log_drain_store.set_enabled(found.name, enabled, store=self.store)
        audit.record(
            "logs.drain",
            target=f"drain:{found.name}",
            details={"action": "enable" if enabled else "disable", "by": actor},
        )
        return replace(found, enabled=enabled)

    def test(self, name: str, *, actor: str = "system") -> str:
        """
        Send one line to a destination, and say how it went.

        Args:
            name: Its name.
            actor: Who asked, for the audit trail.

        Returns:
            What was sent, for the operator to look for at the other end.

        Raises:
            ValidationError: There is none of that name.
            DeliveryError: The destination did not accept it; the message carries
                what it said.
        """
        import socket
        import time

        drain = self.get(name)
        message = f"noust log drain test from {socket.gethostname()}"
        event = LogEvent(
            ts_ns=time.time_ns(),
            message=message,
            app="noust-test",
            process="test",
            unit="noust-logship",
            priority=6,
            host=socket.gethostname(),
        )
        transport = build_transport(drain, self.secret_of(drain.name))
        try:
            transport.send([event])
        except NoustError:
            audit.record(
                "logs.drain",
                target=f"drain:{drain.name}",
                outcome="failure",
                details={"action": "test", "by": actor},
            )
            raise
        finally:
            transport.close()
        audit.record(
            "logs.drain", target=f"drain:{drain.name}", details={"action": "test", "by": actor}
        )
        return message


__all__ = ["DeliveryError", "LogDrains", "build_transport", "validate_settings"]
