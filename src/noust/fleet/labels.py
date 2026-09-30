# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Node labels: ``key=value`` pairs a central's operator puts on its nodes.

A fleet action aims at nodes by name or by label (``env=prod``): what Portainer
calls edge groups and Ansible an inventory group, kept as small as it can be.
Labels live on the central only (the ``node_labels`` table of schema v12) and
say nothing to the node: they are how this central groups what it manages, not
a property of the server.

``noust node label`` and ``PUT /api/fleet/servers/{node}/labels`` both write
through :class:`NodeLabels`, the only writer, so the rules on what a label may
look like are checked once.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone

from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore, get_store

#: A label key: lower case, like a DNS label, so ``Env`` and ``env`` are never
#: two groups. Dots allow a namespace (``team.owner``).
KEY_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,61}[a-z0-9])?")

#: A label value: case kept, no spaces or separators, possibly empty.
VALUE_PATTERN = re.compile(r"[A-Za-z0-9._-]{0,63}")

#: Labels one node may carry. A handful is what grouping needs; more is a
#: sign the labels are being used as notes.
MAX_LABELS = 16


def validate_key(key: str) -> str:
    """
    Check a label key.

    Args:
        key: Such as ``env``.

    Returns:
        The key.

    Raises:
        ValidationError: When it is not a valid key.
    """
    if not isinstance(key, str) or not KEY_PATTERN.fullmatch(key):
        raise ValidationError(
            f"Invalid label key: {key!r}",
            details=(
                "A key is 1 to 63 lower-case letters, digits, '.', '_' or '-', starting "
                "and ending with a letter or digit, such as env or team.owner."
            ),
            field="labels",
        )
    return key


def validate_value(value: str) -> str:
    """
    Check a label value.

    Args:
        value: Such as ``prod``.

    Returns:
        The value.

    Raises:
        ValidationError: When it is not a valid value.
    """
    if not isinstance(value, str) or not VALUE_PATTERN.fullmatch(value):
        raise ValidationError(
            f"Invalid label value: {value!r}",
            details="A value is up to 63 letters, digits, '.', '_' or '-', such as prod.",
            field="labels",
        )
    return value


def parse_label(text: str) -> tuple[str, str]:
    """
    Read one ``key=value`` pair.

    Args:
        text: Such as ``env=prod``.

    Returns:
        The key and the value.

    Raises:
        ValidationError: When there is no ``=`` or either side is invalid.
    """
    key, separator, value = text.partition("=")
    if not separator:
        raise ValidationError(
            f"A label is key=value, not {text!r}",
            details="Such as env=prod. To remove a label, use key- (env-).",
            field="labels",
        )
    return validate_key(key.strip()), validate_value(value.strip())


def parse_selector(texts: Iterable[str]) -> dict[str, str]:
    """
    Read a label selector: every pair must match (``env=prod,role=web``).

    Args:
        texts: ``key=value`` pairs; each may itself hold several separated by commas.

    Returns:
        Key to the value it must have.

    Raises:
        ValidationError: When a pair is invalid or a key is asked two values.
    """
    selector: dict[str, str] = {}
    for text in texts:
        for piece in (part.strip() for part in text.split(",")):
            if not piece:
                continue
            key, value = parse_label(piece)
            if selector.get(key, value) != value:
                raise ValidationError(
                    f"The selector asks {key} to be both {selector[key]} and {value}",
                    details="A node has one value per key; give each key once.",
                    field="labels",
                )
            selector[key] = value
    return selector


def matches(labels: Mapping[str, str], selector: Mapping[str, str]) -> bool:
    """
    Report whether a node's labels satisfy a selector.

    Args:
        labels: The node's labels.
        selector: Key to required value.

    Returns:
        True when every pair of the selector is among the labels.
    """
    return all(labels.get(key) == value for key, value in selector.items())


class NodeLabels:
    """
    Read and change the labels of the nodes this central manages.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    def of(self, node: str) -> dict[str, str]:
        """
        Read one node's labels.

        Args:
            node: The node's name.

        Returns:
            Key to value, by key.
        """
        rows = (
            self.store._get_connection()
            .execute("SELECT key, value FROM node_labels WHERE node = ? ORDER BY key", (node,))
            .fetchall()
        )
        return {row["key"]: row["value"] for row in rows}

    def all(self) -> dict[str, dict[str, str]]:
        """
        Read every node's labels.

        Returns:
            Node name to its labels; a node without labels is absent.
        """
        rows = (
            self.store._get_connection()
            .execute("SELECT node, key, value FROM node_labels ORDER BY node, key")
            .fetchall()
        )
        labels: dict[str, dict[str, str]] = {}
        for row in rows:
            labels.setdefault(row["node"], {})[row["key"]] = row["value"]
        return labels

    def change(
        self,
        node: str,
        *,
        set_labels: Mapping[str, str] | None = None,
        remove: Iterable[str] = (),
        replace: bool = False,
    ) -> dict[str, str]:
        """
        Set, remove or replace a node's labels, in one transaction.

        Args:
            node: The node's name; it must be registered.
            set_labels: Keys to set, and their values.
            remove: Keys to remove; a key that is not there is not an error.
            replace: Drop every label first, so ``set_labels`` is the whole set.

        Returns:
            The node's labels afterwards.

        Raises:
            ValidationError: When a key or value is invalid, the node would carry
                more than :data:`MAX_LABELS`, or no node has that name.
        """
        wanted = {validate_key(k): validate_value(v) for k, v in (set_labels or {}).items()}
        dropped = {validate_key(key) for key in remove}
        if self.store.get_node(node) is None:
            raise ValidationError(
                f"No node named {node} is registered on this central",
                details="List them with 'noust node list'.",
                field="node",
            )
        current = {} if replace else self.of(node)
        final = {key: value for key, value in current.items() if key not in dropped}
        final.update(wanted)
        if len(final) > MAX_LABELS:
            raise ValidationError(
                f"A node carries at most {MAX_LABELS} labels; {node} would carry {len(final)}",
                details="Remove the ones no action selects by (key-).",
                field="labels",
            )
        now = datetime.now(timezone.utc).isoformat()
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM node_labels WHERE node = ?", (node,))
            cursor.executemany(
                "INSERT INTO node_labels (node, key, value, updated_at) VALUES (?, ?, ?, ?)",
                [(node, key, value, now) for key, value in sorted(final.items())],
            )
        return dict(sorted(final.items()))

    def select(self, selector: Mapping[str, str], nodes: Iterable[str]) -> list[str]:
        """
        Pick the nodes whose labels satisfy a selector.

        Args:
            selector: Key to required value; an empty one selects nothing,
                so a missing selector never means "every node".
            nodes: The candidates, in the order to keep.

        Returns:
            The matching names, in that order.
        """
        if not selector:
            return []
        labels = self.all()
        return [name for name in nodes if matches(labels.get(name, {}), selector)]
