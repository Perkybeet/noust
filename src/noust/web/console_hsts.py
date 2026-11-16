# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The console's ``Strict-Transport-Security``, decided for one request (item 198).

:mod:`noust.core.hsts` holds the rule; this asks the two facts it needs: what ``web.hsts`` says and
the name ``noust console expose`` published the console on. Both are read on every response over
TLS, so a setting changed at the terminal or an ``unexpose`` takes effect on the next request and
no restart is part of the promise. A record that cannot be read sends nothing: a header that binds
a browser for a year is never sent on a guess.
"""

from __future__ import annotations

import logging
import sqlite3

from starlette.requests import HTTPConnection

from noust.core import hsts
from noust.core.config import Config
from noust.core.console_exposure_store import STATE_ACTIVE, ConsoleExposureStore
from noust.core.exceptions import NoustError

__all__ = ["header_for_connection", "mode", "published_name"]

logger = logging.getLogger(__name__)

#: What the last unreadable record said, so a store that stays broken is told once and not on
#: every response of every page.
_last_failure: str | None = None


def mode() -> str:
    """
    Returns:
        ``web.hsts`` as the console's configuration holds it.
    """
    return str(Config().get("web.hsts", hsts.DEFAULT_MODE))


def published_name() -> str | None:
    """
    Returns:
        The name the console was published on, or None when it was not (or the record that says
        so cannot be read).
    """
    global _last_failure
    try:
        state = ConsoleExposureStore().get()
    except (NoustError, sqlite3.Error, OSError) as exc:
        if str(exc) != _last_failure:
            _last_failure = str(exc)
            logger.warning(
                "Could not read where the console was published, so it sends no "
                "Strict-Transport-Security: %s",
                exc,
            )
        return None
    _last_failure = None
    if state is None or state.state != STATE_ACTIVE:
        return None
    return state.domain


def header_for_connection(connection: HTTPConnection, *, secure: bool) -> str | None:
    """
    Decide the ``Strict-Transport-Security`` of the response to a request.

    Args:
        connection: The request being answered.
        secure: Whether it arrived over TLS, directly or through a declared proxy.

    Returns:
        The header's value, or None to send nothing.
    """
    chosen = mode()
    # The record is only read when it can matter: cleartext and the two settings that do not
    # depend on it never reach the store.
    published = published_name() if secure and chosen not in (hsts.ALWAYS, hsts.NEVER) else None
    return hsts.header_for(chosen, secure=secure, host=connection.url.hostname, published=published)
