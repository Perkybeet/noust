# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The inventory an ENS auditor asks for (G15: op.exp.1, mp.info.2, mp.si.1).

op.exp.1.1 asks for an up-to-date inventory "with its nature and who is
responsible for it". The store already knows every application; this adds, per
application, what only the organisation can say:

- **owner**: who answers for it (a person, a team, a contact);
- **criticality**: ``low``, ``medium`` or ``high``, the category the
  organisation gave the service it supports (ENS Anexo I: BAJA, MEDIA, ALTA);
- **classification**: ``public``, ``internal``, ``restricted`` or
  ``confidential``, how its information is handled (mp.info.2: "uso público",
  "uso interno", "difusión limitada", "confidencial").

The fields live in ``app_inventory`` (schema v12, :data:`~noust.core.schema_v12.ENS_SQL`),
a table this module alone writes, and are exported as JSON or CSV for the
auditor and included in ``noust ens report``. Every change is an audit event,
``apps.inventory``.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from noust.core.audit import record
from noust.core.exceptions import ValidationError

if TYPE_CHECKING:
    from noust.core.store import NoustStore

CRITICALITIES: tuple[str, ...] = ("low", "medium", "high")
CLASSIFICATIONS: tuple[str, ...] = ("public", "internal", "restricted", "confidential")

#: Longest owner or note accepted: a name and a contact, not a document.
MAX_TEXT = 200

#: The columns an export carries, in order.
EXPORT_FIELDS: tuple[str, ...] = (
    "domain",
    "app_type",
    "status",
    "source",
    "owner",
    "criticality",
    "classification",
    "notes",
    "updated_at",
    "updated_by",
)

#: "Leave this field as it is", distinct from None ("clear it").
_UNSET: Any = object()


class InventoryError(ValidationError):
    """An inventory change that cannot be made as asked."""


@dataclass(frozen=True)
class InventoryEntry:
    """
    One application in the inventory.

    Attributes:
        domain: The application.
        app_type: Its type.
        status: Its last known status.
        source: Where its code comes from, without any credential.
        owner: Who answers for it.
        criticality: ``low``, ``medium`` or ``high``.
        classification: ``public``, ``internal``, ``restricted`` or
            ``confidential``.
        notes: Anything else the organisation records.
        updated_at: When the fields last changed, ISO 8601 UTC.
        updated_by: Who changed them.
    """

    domain: str
    app_type: str
    status: str
    source: str
    owner: str | None = None
    criticality: str | None = None
    classification: str | None = None
    notes: str | None = None
    updated_at: str | None = None
    updated_by: str | None = None

    @property
    def complete(self) -> bool:
        """Whether it has the owner and the criticality op.exp.1.1 asks for."""
        return bool(self.owner) and bool(self.criticality)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, and ``complete``.
        """
        return {**asdict(self), "complete": self.complete}


def _clean_source(source: str | None) -> str:
    """
    Name where an application's code comes from, without a credential.

    Args:
        source: The source as stored.

    Returns:
        ``host/org/repo``, a directory, or empty.
    """
    if not source:
        return ""
    from noust.core.ens.origins import describe_origin

    return describe_origin(source).display


def _text(value: Any, field: str) -> str | None:
    """
    Args:
        value: What was given for a free-text field.
        field: Its name, for the error.

    Returns:
        The text stripped, or None for empty.

    Raises:
        InventoryError: It is too long or spans lines.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > MAX_TEXT or "\n" in text or "\r" in text:
        raise InventoryError(
            f"The {field} must be one line of at most {MAX_TEXT} characters",
            field=field,
        )
    return text


def _level(value: Any, field: str, allowed: tuple[str, ...]) -> str | None:
    """
    Args:
        value: What was given for an enumerated field.
        field: Its name, for the error.
        allowed: Its values.

    Returns:
        The value, lower-case, or None for empty.

    Raises:
        InventoryError: It is not one of ``allowed``.
    """
    if value is None or not str(value).strip():
        return None
    text = str(value).strip().lower()
    if text not in allowed:
        raise InventoryError(
            f"{value!r} is not a {field}",
            details=f"Use one of: {', '.join(allowed)}.",
            field=field,
        )
    return text


class Inventory:
    """
    Read and change the inventory.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store the inventory lives in."""
        if self._store is not None:
            return self._store
        from noust.core.store import get_store

        return get_store()

    def _rows(self, where: str = "", params: tuple[Any, ...] = ()) -> list[InventoryEntry]:
        # ``where`` is one of this class's own fixed clauses, never input.
        query = (
            "SELECT a.domain, a.app_type, a.status, a.source, i.owner, i.criticality, "  # noqa: S608
            "i.classification, i.notes, i.updated_at, i.updated_by "
            "FROM apps a LEFT JOIN app_inventory i ON i.app_id = a.id "
            f"{where} ORDER BY a.domain"
        )
        with self.store._transaction() as cursor:
            rows = cursor.execute(query, params).fetchall()
        return [
            InventoryEntry(
                domain=str(row["domain"]),
                app_type=str(row["app_type"] or ""),
                status=str(row["status"] or ""),
                source=_clean_source(row["source"]),
                owner=row["owner"],
                criticality=row["criticality"],
                classification=row["classification"],
                notes=row["notes"],
                updated_at=row["updated_at"],
                updated_by=row["updated_by"],
            )
            for row in rows
        ]

    def list(self) -> list[InventoryEntry]:
        """
        Returns:
            Every application, with its inventory fields (None where unset),
            by domain.
        """
        return self._rows()

    def get(self, domain: str) -> InventoryEntry:
        """
        Args:
            domain: The application.

        Returns:
            Its entry.

        Raises:
            InventoryError: There is no such application.
        """
        rows = self._rows("WHERE a.domain = ?", (domain,))
        if not rows:
            raise InventoryError(
                f"No application named {domain}",
                details="List them with 'noust list'.",
                field="domain",
            )
        return rows[0]

    def set(
        self,
        domain: str,
        *,
        actor: str,
        owner: Any = _UNSET,
        criticality: Any = _UNSET,
        classification: Any = _UNSET,
        notes: Any = _UNSET,
    ) -> InventoryEntry:
        """
        Change an application's inventory fields.

        A field left out keeps its value; an empty one is cleared.

        Args:
            domain: The application.
            actor: Who makes the change, for the record.
            owner: Who answers for it.
            criticality: ``low``, ``medium`` or ``high``.
            classification: ``public``, ``internal``, ``restricted`` or
                ``confidential``.
            notes: Anything else.

        Returns:
            The entry as it is now.

        Raises:
            InventoryError: No such application, or a value is refused.
        """
        current = self.get(domain)
        values = {
            "owner": current.owner if owner is _UNSET else _text(owner, "owner"),
            "criticality": current.criticality
            if criticality is _UNSET
            else _level(criticality, "criticality", CRITICALITIES),
            "classification": current.classification
            if classification is _UNSET
            else _level(classification, "classification", CLASSIFICATIONS),
            "notes": current.notes if notes is _UNSET else _text(notes, "notes"),
        }
        changed = sorted(name for name, value in values.items() if getattr(current, name) != value)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO app_inventory (app_id, owner, criticality, classification, notes, "
                "updated_at, updated_by) SELECT id, ?, ?, ?, ?, ?, ? FROM apps WHERE domain = ? "
                "ON CONFLICT(app_id) DO UPDATE SET owner = excluded.owner, "
                "criticality = excluded.criticality, classification = excluded.classification, "
                "notes = excluded.notes, updated_at = excluded.updated_at, "
                "updated_by = excluded.updated_by",
                (
                    values["owner"],
                    values["criticality"],
                    values["classification"],
                    values["notes"],
                    now,
                    actor,
                    domain,
                ),
            )
        record(
            "apps.inventory",
            target=f"app:{domain}",
            details={"changed": changed, **{name: values[name] for name in changed}},
        )
        return self.get(domain)


def export_json(entries: list[InventoryEntry]) -> str:
    """
    Args:
        entries: The inventory.

    Returns:
        ``{"generated_at", "applications": [...]}`` as indented JSON.
    """
    return json.dumps(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "applications": [entry.to_dict() for entry in entries],
        },
        indent=2,
    )


def export_csv(entries: list[InventoryEntry]) -> str:
    """
    Args:
        entries: The inventory.

    Returns:
        One row per application, with a header of :data:`EXPORT_FIELDS`.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(EXPORT_FIELDS), extrasaction="ignore")
    writer.writeheader()
    for entry in entries:
        writer.writerow(
            {key: ("" if value is None else value) for key, value in asdict(entry).items()}
        )
    return buffer.getvalue()
