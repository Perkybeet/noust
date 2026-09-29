# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
One scrubber for the text Noust keeps about the work it did.

Build output, job log lines and failure messages are raw tool output, and the
tools run with the application's environment: a build script that echoes
``$API_KEY``, a framework that prints its configuration when it fails, a
connection error that quotes the whole ``DATABASE_URL``. That text is stored in
the deployment history, the job log files and the job rows, and all of it is
readable with the ``read`` scope - a far wider audience than the ``.env`` it
came from, which is 0600.

:class:`Scrubber` replaces known secret values with :data:`REDACTED` wherever
that text is persisted or published. It works on values, not on shapes: it
cannot recognise a secret it was not told about, so its inputs matter -
:func:`secret_env_values` for an environment, :func:`app_secret_values` for a
deployed application and :func:`known_credentials` for Noust's own
configuration - and :func:`scrubber_for` combines them for the usual case.

Values shorter than :data:`MIN_SECRET_LENGTH` are never scrubbed: a secret
variable set to ``true`` or ``test`` would otherwise blank out every ordinary
word containing it and make the log unreadable, and a value that short is not
protecting anything worth the cost.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import unquote

from noust.core.config import REDACTED, Config, redact_secrets
from noust.core.exceptions import NoustError
from noust.core.secret_detection import URL_CREDENTIALS, classify, name_looks_secret

logger = logging.getLogger(__name__)

#: The shortest value treated as a secret. See the module docstring.
MIN_SECRET_LENGTH = 6


class Scrubber:
    """
    Replace known secret values in text with :data:`REDACTED`.

    Thread-safe: a job adds its application's values from the worker thread
    while its log lines are being scrubbed, and the replacement pattern is
    swapped in one assignment so a reader never sees a half-built one.
    """

    def __init__(self, values: Iterable[str] = ()) -> None:
        """
        Args:
            values: Secret values to scrub from the start.
        """
        self._lock = threading.Lock()
        self._values: frozenset[str] = frozenset()
        self._pattern: re.Pattern[str] | None = None
        self.add(values)

    def __bool__(self) -> bool:
        """True when there is at least one value to scrub."""
        return self._pattern is not None

    def add(self, values: Iterable[str]) -> None:
        """
        Scrub these values as well from now on.

        A multi-line value (a PEM key) is also added line by line, because a
        tool that prints it usually prints it one line per log call.

        Args:
            values: Secret values. Non-strings, and strings shorter than
                :data:`MIN_SECRET_LENGTH` once trimmed, are ignored.
        """
        fresh: set[str] = set()
        for value in values:
            if not isinstance(value, str):
                continue
            lines = (line.strip() for line in value.splitlines())
            for candidate in {value, value.strip(), *lines}:
                if len(candidate.strip()) >= MIN_SECRET_LENGTH:
                    fresh.add(candidate)
        with self._lock:
            if fresh <= self._values:
                return
            self._values = self._values | fresh
            # Longest first, so a secret that contains another is replaced
            # whole instead of leaving its remainder behind.
            ordered = sorted(self._values, key=len, reverse=True)
            self._pattern = re.compile("|".join(re.escape(value) for value in ordered))

    def scrub(self, text: str) -> str:
        """
        Replace every known secret in a piece of text.

        Args:
            text: Text about to be stored or shown.

        Returns:
            The text with each secret value replaced by :data:`REDACTED`.
        """
        pattern = self._pattern
        if pattern is None or not text:
            return text
        return pattern.sub(REDACTED, text)


def secret_env_values(env: Mapping[str, Any], marks: Mapping[str, bool] | None = None) -> list[str]:
    """
    Pick the values of an environment that are secrets.

    Uses :func:`~noust.core.secret_detection.classify`, the one classifier for
    the question, rather than a third opinion here. A value classified secret
    for any reason other than an embedded URL credential is scrubbed whole; a
    value that is only secret because of a credential inside it (a password
    in ``DATABASE_URL``) has just that credential scrubbed, so the rest of
    the connection string - host, database name - stays in the log, which is
    what makes the log worth reading afterwards.

    Scrubbing is deliberately a superset of the name-only heuristic
    (:func:`~noust.core.secret_detection.name_looks_secret`), even when
    ``classify`` relaxes a name-based verdict because the name also looks
    public (``NEXT_PUBLIC_API_KEY``, ``VITE_API_SECRET``): that relaxation
    exists to decide what a human is shown, not what a build tool might echo
    into a log nobody expected it to print. Over-scrubbing a log line is
    harmless; a secret-shaped value appearing verbatim in one is not, so a
    variable is scrubbed whenever its name alone would have flagged it,
    unless the operator has explicitly marked it not secret.

    Args:
        env: Variable name to value. Non-string values are ignored.
        marks: Operator overrides for this application, as for
            :func:`~noust.core.secret_detection.classify`. A variable marked
            not secret is trusted completely: nothing about it, including a
            credential embedded in its value, is scrubbed.

    Returns:
        The secret values, including each URL password both as written and
        percent-decoded.
    """
    strings = {str(key): value for key, value in env.items() if isinstance(value, str)}
    values: list[str] = []
    for key, value in strings.items():
        if not value:
            continue
        verdict = classify(key, value, marks)
        if verdict.marked and not verdict.secret:
            continue
        if verdict.secret and verdict.reason != "url credentials":
            values.append(value)
            continue
        if not verdict.marked and name_looks_secret(key):
            values.append(value)
            continue
        for match in URL_CREDENTIALS.finditer(value):
            password = match.group(0)[len(match.group("prefix")) : -1]
            values.extend({password, unquote(password)})
    return values


def config_secret_values(config: Any) -> list[str]:
    """
    Collect the secret values of a configuration structure.

    Walks the structure alongside :func:`~noust.core.config.redact_secrets`'
    output, so a value is a secret here exactly when ``noust config show``
    would hide it.

    Args:
        config: Configuration mapping, sequence or scalar.

    Returns:
        Every string value the redaction replaces.
    """
    values: list[str] = []

    def walk(original: Any, redacted: Any) -> None:
        if isinstance(original, dict) and isinstance(redacted, dict):
            for key, value in original.items():
                walk(value, redacted.get(key))
        elif isinstance(original, (list, tuple)) and isinstance(redacted, list):
            for item, masked in zip(original, redacted, strict=False):
                walk(item, masked)
        elif isinstance(original, str) and redacted == REDACTED and original != REDACTED:
            values.append(original)

    walk(config, redact_secrets(config))
    return values


def known_credentials() -> list[str]:
    """
    Collect Noust's own credentials: database passwords, SMTP, webhooks.

    Returns:
        The secret values of the loaded configuration; none when it cannot
        be read, which is logged.
    """
    try:
        return config_secret_values(Config().to_dict())
    except (NoustError, OSError) as exc:
        logger.warning("Could not read the configuration to scrub its credentials: %s", exc)
        return []


def app_secret_values(domain: str) -> list[str]:
    """
    Collect the secrets of a deployed application.

    Its ``.env``, the variables recorded on its row and on its unit's row (an
    older deploy kept them there), and its webhook secret. An application that
    is not deployed has none. Reading them is best effort: a store or a file
    that cannot be read is logged and skipped, because the caller is about to
    write a log line or record an outcome, and the one thing worse than a
    partly scrubbed line is losing the deployment's record altogether.

    Args:
        domain: The application's domain.

    Returns:
        The secret values found.
    """
    # Deferred for the same reason as in secret_env_values: these live in the
    # deployers package, which imports this module.
    from noust.core.store import get_store
    from noust.core.utils import domain_to_app_name
    from noust.deployers.helpers.app_env import read_app_env

    values: list[str] = []
    try:
        store = get_store()
        app = store.get_app(domain)
        if app is None:
            return values
        marks = app.env_secret_marks
        values.extend(secret_env_values(app.env_vars, marks))
        service = store.get_service(domain_to_app_name(domain))
        if service is not None:
            values.extend(secret_env_values(service.environment, marks))
        webhook_secret = store.get_webhook_secret(domain)
        if webhook_secret:
            values.append(webhook_secret)
        values.extend(secret_env_values(read_app_env(app), marks))
    except (NoustError, OSError, sqlite3.Error) as exc:
        logger.warning("Could not read the secrets of %s to scrub its logs: %s", domain, exc)
    return values


def _marks_for(domain: str) -> dict[str, bool]:
    """
    Read an application's operator-set secret marks, best effort.

    Args:
        domain: The application's domain.

    Returns:
        The marks, or empty when the application is unknown or its store row
        cannot be read - the same best-effort contract as
        :func:`app_secret_values`.
    """
    from noust.core.store import get_store

    try:
        app = get_store().get_app(domain)
    except (NoustError, sqlite3.Error) as exc:
        logger.warning("Could not read the secret marks of %s: %s", domain, exc)
        return {}
    return app.env_secret_marks if app is not None else {}


def scrubber_for(domain: str | None = None, env: Mapping[str, Any] | None = None) -> Scrubber:
    """
    Build the scrubber for work on one application.

    Args:
        domain: The application, when the work concerns one.
        env: Extra variables the work runs with, such as the environment a
            fresh deploy was given before any ``.env`` exists.

    Returns:
        A scrubber for Noust's credentials, the application's secrets and the
        secret values of ``env``.
    """
    scrubber = Scrubber(known_credentials())
    if domain:
        scrubber.add(app_secret_values(domain))
    if env:
        scrubber.add(secret_env_values(env, _marks_for(domain) if domain else None))
    return scrubber
