# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What SCIM clients read before they provision, and how they ask for less: the resource types and
schemas this server models, and the ``attributes`` / ``excludedAttributes`` projection of a resource.

RFC 7643 section 6 and 7 define the discovery documents, RFC 7644 section 3.4.2.5 the projection.
They are data and a pure function, so they are here and not in the service that touches accounts.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
from typing import Any

SCHEMA_USER = "urn:ietf:params:scim:schemas:core:2.0:User"
SCHEMA_GROUP = "urn:ietf:params:scim:schemas:core:2.0:Group"
SCHEMA_RESOURCE_TYPE = "urn:ietf:params:scim:schemas:core:2.0:ResourceType"
SCHEMA_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Schema"
SCHEMA_LIST = "urn:ietf:params:scim:api:messages:2.0:ListResponse"

#: What a resource always carries, whatever a client asked for.
ALWAYS_RETURNED = frozenset({"schemas", "id"})
MAX_PROJECTED = 50


def _attribute(
    name: str,
    kind: str = "string",
    *,
    required: bool = False,
    multi: bool = False,
    mutability: str = "readWrite",
    returned: str = "default",
    unique: str = "none",
    case_exact: bool = False,
    description: str = "",
    sub: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Args:
        name: The attribute's name.
        kind: SCIM's type name.
        required: Whether a client must send it.
        multi: Whether it has several values.
        mutability: ``readOnly``, ``readWrite``, ``immutable`` or ``writeOnly``.
        returned: ``always``, ``default``, ``request`` or ``never``.
        unique: ``none``, ``server`` or ``global``.
        case_exact: Whether comparing it respects case.
        description: What it is.
        sub: Sub-attributes of a complex one.

    Returns:
        An attribute definition as RFC 7643 section 7 writes it.
    """
    definition: dict[str, Any] = {
        "name": name,
        "type": kind,
        "multiValued": multi,
        "description": description or name,
        "required": required,
        "caseExact": case_exact,
        "mutability": mutability,
        "returned": returned,
        "uniqueness": unique,
    }
    if kind == "complex":
        definition["subAttributes"] = sub or []
    return definition


def _members() -> list[dict[str, Any]]:
    """
    Returns:
        The sub-attributes of ``groups`` and ``members``: a reference to another resource.
    """
    return [
        _attribute("value", description="The id of the resource."),
        _attribute("display", description="A name for it.", mutability="readOnly"),
        _attribute("$ref", "reference", description="Where it is.", mutability="readOnly"),
    ]


def schema_documents(base_url: str) -> list[dict[str, Any]]:
    """
    Args:
        base_url: Where the SCIM API is reached.

    Returns:
        The two schemas this server models, as ``Schema`` resources.
    """
    user = [
        _attribute(
            "userName",
            required=True,
            unique="server",
            description="The provider's name for the person, unique among its users.",
        ),
        _attribute("externalId", description="The provider's own id for the person."),
        _attribute("displayName", description="How the person is greeted."),
        _attribute(
            "name",
            "complex",
            description="The person's name.",
            sub=[
                _attribute("formatted", description="The whole name."),
                _attribute("givenName", description="The first name."),
                _attribute("familyName", description="The family name."),
            ],
        ),
        _attribute(
            "emails",
            "complex",
            multi=True,
            description=(
                "The person's addresses. The primary one ties their sign-in to their account; "
                "once an account has one it is not changed from here."
            ),
            sub=[
                _attribute("value", description="The address."),
                _attribute("type", description="work or home."),
                _attribute("primary", "boolean", description="Whether it is the main one."),
            ],
        ),
        _attribute(
            "active",
            "boolean",
            description="Whether the person may sign in; false switches the account off.",
        ),
        _attribute(
            "groups",
            "complex",
            multi=True,
            mutability="readOnly",
            description="The groups of this provider the person is in.",
            sub=_members(),
        ),
    ]
    group = [
        _attribute("displayName", required=True, unique="server", description="The group's name."),
        _attribute("externalId", description="The provider's own id for the group."),
        _attribute(
            "members",
            "complex",
            multi=True,
            description="The users in the group.",
            sub=[*_members(), _attribute("type", description="User.", mutability="immutable")],
        ),
    ]
    documents = []
    for schema_id, name, description, attributes in (
        (SCHEMA_USER, "User", "A person who may have an account here.", user),
        (
            SCHEMA_GROUP,
            "Group",
            "A group whose members get the roles the source's mapping says.",
            group,
        ),
    ):
        documents.append(
            {
                "schemas": [SCHEMA_SCHEMA],
                "id": schema_id,
                "name": name,
                "description": description,
                "attributes": attributes,
                "meta": {"resourceType": "Schema", "location": f"{base_url}/Schemas/{schema_id}"},
            }
        )
    return documents


def resource_type_documents(base_url: str) -> list[dict[str, Any]]:
    """
    Args:
        base_url: Where the SCIM API is reached.

    Returns:
        The two resource types, as ``ResourceType`` resources.
    """
    return [
        {
            "schemas": [SCHEMA_RESOURCE_TYPE],
            "id": name,
            "name": name,
            "endpoint": f"/{name}s",
            "description": description,
            "schema": schema,
            "schemaExtensions": [],
            "meta": {
                "resourceType": "ResourceType",
                "location": f"{base_url}/ResourceTypes/{name}",
            },
        }
        for name, schema, description in (
            ("User", SCHEMA_USER, "A person who may have an account here."),
            ("Group", SCHEMA_GROUP, "A group of people."),
        )
    ]


def list_response(resources: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Args:
        resources: The whole list.

    Returns:
        The ListResponse a discovery endpoint answers (RFC 7644 section 4).
    """
    return {
        "schemas": [SCHEMA_LIST],
        "totalResults": len(resources),
        "startIndex": 1,
        "itemsPerPage": len(resources),
        "Resources": resources,
    }


# ------------------------------------------------------------------------- projection


def parse_attribute_list(value: str | Iterable[str] | None) -> list[str]:
    """
    Args:
        value: ``attributes`` or ``excludedAttributes``: comma separated names, or a list.

    Returns:
        The names, trimmed; a URN-prefixed one is reduced to the attribute it ends in. At most
        :data:`MAX_PROJECTED`.
    """
    if value is None:
        return []
    pieces = value.split(",") if isinstance(value, str) else list(value)
    names: list[str] = []
    for piece in pieces:
        text = str(piece).strip()
        if not text or len(text) > 128:
            continue
        if text.lower().startswith("urn:"):
            text = text.rsplit(":", 1)[-1]
        names.append(text)
    return names[:MAX_PROJECTED]


def _key(mapping: Mapping[str, Any], name: str) -> str | None:
    """
    Args:
        mapping: A resource or a complex value.
        name: An attribute name in any case.

    Returns:
        The key the mapping spells it with, or None.
    """
    lowered = name.lower()
    return next((key for key in mapping if key.lower() == lowered), None)


def _keep(value: Any, names: list[str]) -> Any:
    """
    Args:
        value: A complex value, or a list of them.
        names: The sub-attributes to keep.

    Returns:
        The value with only those.
    """
    if isinstance(value, list):
        return [_keep(item, names) for item in value]
    if not isinstance(value, dict):
        return value
    kept = {}
    for name in names:
        key = _key(value, name)
        if key is not None:
            kept[key] = value[key]
    return kept


def _drop(value: Any, names: list[str]) -> Any:
    """
    Args:
        value: A complex value, or a list of them.
        names: The sub-attributes to remove.

    Returns:
        The value without them.
    """
    if isinstance(value, list):
        return [_drop(item, names) for item in value]
    if not isinstance(value, dict):
        return value
    gone = {key for name in names if (key := _key(value, name)) is not None}
    return {key: item for key, item in value.items() if key not in gone}


def project(
    resource: Mapping[str, Any], attributes: list[str], excluded: list[str]
) -> dict[str, Any]:
    """
    Return a resource with only what the client asked for (RFC 7644 section 3.4.2.5).

    ``attributes`` names what to return besides what is always returned; ``excludedAttributes``
    names what not to return of the default set, and is ignored when ``attributes`` is given. A
    dotted name reaches a sub-attribute.

    Args:
        resource: A user or group resource.
        attributes: The attributes to return.
        excluded: The attributes to leave out.

    Returns:
        A new resource.
    """
    if not attributes and not excluded:
        return dict(resource)
    if attributes:
        whole = {name.lower() for name in attributes if "." not in name}
        parts: dict[str, list[str]] = {}
        for name in attributes:
            head, _, rest = name.partition(".")
            if rest:
                parts.setdefault(head.lower(), []).append(rest)
        shaped: dict[str, Any] = {}
        for key, value in resource.items():
            lowered = key.lower()
            if lowered in ALWAYS_RETURNED or lowered in whole:
                shaped[key] = copy.deepcopy(value)
            elif lowered in parts:
                shaped[key] = _keep(copy.deepcopy(value), parts[lowered])
        return shaped
    removed: dict[str, list[str]] = {}
    for name in excluded:
        head, _, rest = name.partition(".")
        removed.setdefault(head.lower(), []).append(rest)
    shaped = {}
    for key, value in resource.items():
        lowered = key.lower()
        if lowered in ALWAYS_RETURNED or lowered not in removed:
            shaped[key] = copy.deepcopy(value)
        elif all(removed[lowered]):  # only sub-attributes were named
            shaped[key] = _drop(copy.deepcopy(value), [rest for rest in removed[lowered] if rest])
    return shaped


def project_list(
    listing: Mapping[str, Any], attributes: list[str], excluded: list[str]
) -> dict[str, Any]:
    """
    Args:
        listing: A ListResponse.
        attributes: The attributes to return.
        excluded: The attributes to leave out.

    Returns:
        The same list with every resource projected.
    """
    shaped = dict(listing)
    shaped["Resources"] = [
        project(item, attributes, excluded) for item in listing.get("Resources", [])
    ]
    return shaped
