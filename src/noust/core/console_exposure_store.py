# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What ``noust console expose`` created, kept so ``unexpose`` removes that and no more.

One row of the ``console_exposure`` table of schema v14 (``id = 1``). The row is
written when an exposure starts, in the ``pending`` state, and updated as each
part is made, so a run that died halfway can still be undone: ``unexpose`` reads
what the row says exists and removes exactly that. :class:`ConsoleExposureStore`
is the only reader and writer; the manager
(:mod:`noust.managers.console_exposure`) decides what the values mean.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore, get_store

#: Created, not yet finished: the next ``unexpose`` cleans it up.
STATE_PENDING = "pending"
#: Everything is in place.
STATE_ACTIVE = "active"
STATES: tuple[str, ...] = (STATE_PENDING, STATE_ACTIVE)

#: The row's only key.
ROW_ID = 1


@dataclass
class ExposureState:
    """
    What an exposure made, and what the settings it changed held before.

    Attributes:
        domain: The public name the console answers on.
        port: The console's port on loopback.
        scheme: ``http``, or ``https`` when the console serves TLS itself.
        allow_ips: Addresses and networks allowed through nginx; empty for any.
        site_created: Noust wrote the nginx site (it did not exist).
        certificate_created: Noust obtained the certificate (no valid one covered the name).
        proxy_added: The trusted proxy added to the console's unit, or None when
            it was there already (and so is left).
        previous_public_url: ``web.public_url`` before, or None when it was unset.
        previous_allowed_hosts: ``web.allowed_hosts`` before, or None when it was unset.
        state: :data:`STATE_PENDING` or :data:`STATE_ACTIVE`.
        created_at: When it started, ISO 8601 UTC.
        created_by: Who asked, an actor label.
    """

    domain: str
    port: int
    scheme: str = "http"
    allow_ips: list[str] = field(default_factory=list)
    site_created: bool = False
    certificate_created: bool = False
    proxy_added: str | None = None
    previous_public_url: str | None = None
    previous_allowed_hosts: list[str] | None = None
    state: str = STATE_PENDING
    created_at: str = ""
    created_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the exposure for the API and the CLI.

        Returns:
            Every field, JSON-ready.
        """
        return {
            "domain": self.domain,
            "url": f"https://{self.domain}",
            "port": self.port,
            "scheme": self.scheme,
            "allow_ips": list(self.allow_ips),
            "site_created": self.site_created,
            "certificate_created": self.certificate_created,
            "proxy_added": self.proxy_added,
            "state": self.state,
            "created_at": self.created_at,
            "created_by": self.created_by,
        }


class ConsoleExposureStore:
    """
    Read and write the one exposure a server may have.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    def get(self) -> ExposureState | None:
        """
        Read the exposure.

        Returns:
            The state, or None when the console was never exposed (or was unexposed).
        """
        row = (
            self.store._get_connection()
            .execute("SELECT * FROM console_exposure WHERE id = ?", (ROW_ID,))
            .fetchone()
        )
        if row is None:
            return None
        return ExposureState(
            domain=str(row["domain"]),
            port=int(row["port"]),
            scheme=str(row["scheme"]),
            allow_ips=_json_list(row["allow_ips"]) or [],
            site_created=bool(row["site_created"]),
            certificate_created=bool(row["certificate_created"]),
            proxy_added=row["proxy_added"],
            previous_public_url=row["previous_public_url"],
            previous_allowed_hosts=_json_list(row["previous_allowed_hosts"]),
            state=str(row["state"]),
            created_at=str(row["created_at"]),
            created_by=row["created_by"],
        )

    def save(self, state: ExposureState) -> None:
        """
        Write the exposure, replacing the one there.

        Args:
            state: What to record.

        Raises:
            ValidationError: When the state names something the table cannot hold.
        """
        if state.state not in STATES:
            raise ValidationError(
                f"Unknown exposure state: {state.state!r}",
                details=f"It is one of {', '.join(STATES)}.",
            )
        if not state.created_at:
            state.created_at = datetime.now(timezone.utc).isoformat()
        with self.store._transaction() as cursor:
            cursor.execute(
                """
                INSERT OR REPLACE INTO console_exposure (
                    id, domain, port, scheme, allow_ips, site_created, certificate_created,
                    proxy_added, previous_public_url, previous_allowed_hosts, state,
                    created_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ROW_ID,
                    state.domain,
                    int(state.port),
                    state.scheme,
                    json.dumps(list(state.allow_ips)),
                    int(state.site_created),
                    int(state.certificate_created),
                    state.proxy_added,
                    state.previous_public_url,
                    (
                        None
                        if state.previous_allowed_hosts is None
                        else json.dumps(list(state.previous_allowed_hosts))
                    ),
                    state.state,
                    state.created_at,
                    state.created_by,
                ),
            )

    def clear(self) -> None:
        """Forget the exposure."""
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM console_exposure WHERE id = ?", (ROW_ID,))


def _json_list(value: Any) -> list[str] | None:
    """
    Read a JSON list of strings out of a column.

    Args:
        value: The column's text, or None.

    Returns:
        The list, or None when the column is empty or not a list of strings.
    """
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, list):
        return None
    return [str(item) for item in parsed]
