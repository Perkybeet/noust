# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The map from the API to a Terraform and OpenTofu provider (checklist J-14).

A provider is a Go program in a repository of its own; what it is derived from lives here,
in ``sdk/terraform/generator_config.yml``, and it is only worth having if it is true. These
tests hold every operation the map names to ``panel/openapi.json``: the path exists with that
verb, a create takes a JSON body, a read of one object answers an object, a list the provider
filters has the field it filters by, a job-backed call answers 202, and what is marked sensitive
is a field of the API. So an endpoint that is renamed or removed fails here, not in the provider.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).parent.parent
CONFIG = ROOT / "sdk" / "terraform" / "generator_config.yml"
SCHEMA = ROOT / "panel" / "openapi.json"
DOC = ROOT / "docs" / "terraform.md"

VERBS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


@pytest.fixture(scope="module")
def document() -> dict[str, Any]:
    return json.loads(SCHEMA.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


@pytest.fixture(scope="module")
def config() -> dict[str, Any]:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def operation_of(document: dict[str, Any], where: dict[str, str]) -> dict[str, Any]:
    """
    Args:
        document: The OpenAPI document.
        where: ``{"path", "method"}`` as the map writes them.

    Returns:
        The operation, which must exist.
    """
    method = where["method"]
    assert method in VERBS, f"{method} is not a verb of the API"
    item = document["paths"].get(where["path"])
    assert item is not None, f"{where['path']} is not a route of the API"
    operation = item.get(method.lower())
    assert operation is not None, f"{method} {where['path']} is not an operation of the API"
    return operation  # type: ignore[no-any-return]


def resolve(document: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """
    Args:
        document: The OpenAPI document.
        schema: A schema, perhaps a reference.

    Returns:
        The schema it stands for, with a reference to a schema followed.
    """
    while "$ref" in schema:
        schema = document["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    for combine in ("anyOf", "oneOf"):
        if combine in schema:
            for member in schema[combine]:
                found = resolve(document, member)
                if found.get("type") != "null":
                    return found
    return schema


def answer_schema(document: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
    """
    Args:
        document: The OpenAPI document.
        operation: One operation.

    Returns:
        The schema of its first successful JSON answer.
    """
    for code, reply in sorted(operation["responses"].items()):
        if code.startswith("2"):
            content = (reply.get("content") or {}).get("application/json")
            if content:
                return resolve(document, content["schema"])
    raise AssertionError("no successful JSON answer")


def body_schema(document: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
    """
    Args:
        document: The OpenAPI document.
        operation: One operation.

    Returns:
        The schema of its JSON request body.
    """
    body = (operation.get("requestBody") or {}).get("content", {}).get("application/json")
    assert body is not None, "it takes no JSON body"
    return resolve(document, body["schema"])


def success_codes(operation: dict[str, Any]) -> list[str]:
    return [code for code in operation["responses"] if code.startswith("2")]


def generated(config: dict[str, Any]) -> list[tuple[str, str, dict[str, str]]]:
    """
    Args:
        config: The map.

    Returns:
        ``(kind, name, location)`` for each operation of the generator's own sections.
    """
    found = []
    for section in ("resources", "data_sources"):
        for name, resource in config[section].items():
            for step in ("create", "read", "update", "delete"):
                if step in resource:
                    found.append((f"{section}.{name}", step, resource[step]))
    return found


class TestTheGeneratorsSections:
    def test_every_operation_is_an_operation_of_the_api(
        self, config: dict[str, Any], document: dict[str, Any]
    ) -> None:
        for where, step, location in generated(config):
            operation_of(document, location)
            assert step in {"create", "read", "update", "delete"}, where

    def test_a_resource_can_be_made_read_and_removed(self, config: dict[str, Any]) -> None:
        for name, resource in config["resources"].items():
            assert {"create", "read", "delete"} <= set(resource), name

    def test_what_is_read_is_an_object_and_what_is_made_takes_a_json_body(
        self, config: dict[str, Any], document: dict[str, Any]
    ) -> None:
        for section in ("resources", "data_sources"):
            for name, resource in config[section].items():
                read = operation_of(document, resource["read"])
                answer = answer_schema(document, read)
                assert answer.get("type") == "object" and answer.get("properties"), name
        for name, resource in config["resources"].items():
            assert body_schema(document, operation_of(document, resource["create"]))[
                "properties"
            ], name

    def test_one_kind_of_thing_is_read_the_same_way_as_a_resource_and_as_a_data_source(
        self, config: dict[str, Any]
    ) -> None:
        for name, source in config["data_sources"].items():
            if name in config["resources"]:
                assert source["read"] == config["resources"][name]["read"], name

    def test_the_aliases_name_a_parameter_of_the_read(
        self, config: dict[str, Any], document: dict[str, Any]
    ) -> None:
        for section in ("resources", "data_sources"):
            for name, resource in config[section].items():
                aliases = resource.get("schema", {}).get("attributes", {}).get("aliases", {})
                parameters = {
                    p["name"]
                    for p in operation_of(document, resource["read"]).get("parameters", [])
                    if p["in"] == "path"
                }
                assert set(aliases) <= parameters, (section, name)


def hand_written(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return config["x_noust"]["hand_written"]  # type: ignore[no-any-return]


STEPS = ("create", "read", "update", "delete")


def calls(resource: dict[str, Any], step: str) -> list[dict[str, str]]:
    """
    Args:
        resource: One entry of ``x_noust.hand_written``.
        step: ``create``, ``read``, ``update`` or ``delete``.

    Returns:
        The operations the step is made of: one, or several when the map writes a list (an application
        is updated by one call per thing that can change).
    """
    found = resource.get(step)
    if found is None:
        return []
    return list(found) if isinstance(found, list) else [found]


def jobs(resource: dict[str, Any]) -> set[str]:
    """
    Args:
        resource: One entry of ``x_noust.hand_written``.

    Returns:
        The steps whose call answers 202 with a job: ``async: true`` is all of them, a list names them.
    """
    marked = resource.get("async")
    if marked is True:
        return {"create", "update", "delete"}
    return set(marked or [])


class TestWhatTheProviderWritesByHand:
    def test_every_operation_is_an_operation_of_the_api(
        self, config: dict[str, Any], document: dict[str, Any]
    ) -> None:
        for name, resource in hand_written(config).items():
            for step in STEPS:
                for location in calls(resource, step):
                    operation_of(document, location)
            assert "read" in resource and "delete" in resource, name

    def test_none_is_also_a_generated_resource(self, config: dict[str, Any]) -> None:
        assert not set(hand_written(config)) & set(config["resources"]) - {"application"}

    def test_a_resource_the_api_lists_has_the_field_it_is_picked_by(
        self, config: dict[str, Any], document: dict[str, Any]
    ) -> None:
        for name, resource in hand_written(config).items():
            pick = resource["read"].get("pick")
            if pick is None:
                continue
            answer = answer_schema(document, operation_of(document, resource["read"]))
            items = None
            for member in (answer.get("properties") or {}).values():
                resolved = resolve(document, member)
                if resolved.get("type") == "array":
                    items = resolve(document, resolved["items"])
                    break
            if answer.get("type") == "array":
                items = resolve(document, answer["items"])
            assert items is not None, f"{name}: the list answers no array"
            assert pick in (items.get("properties") or {}), f"{name}: items have no {pick!r}"

    def test_what_is_not_picked_is_read_by_its_own_address(
        self, config: dict[str, Any], document: dict[str, Any]
    ) -> None:
        for name, resource in hand_written(config).items():
            if resource["read"].get("pick") is None:
                answer = answer_schema(document, operation_of(document, resource["read"]))
                assert answer.get("type") == "object", name

    def test_a_step_marked_as_a_job_answers_202(
        self, config: dict[str, Any], document: dict[str, Any]
    ) -> None:
        asynchronous = {n: r for n, r in hand_written(config).items() if jobs(r)}
        assert asynchronous, "applications are made by a job"
        for name, resource in asynchronous.items():
            for step in jobs(resource):
                assert step in STEPS and calls(resource, step), (name, step)
                for location in calls(resource, step):
                    codes = success_codes(operation_of(document, location))
                    assert codes == ["202"], (name, step, codes)

    def test_what_is_not_marked_is_not_a_job(
        self, config: dict[str, Any], document: dict[str, Any]
    ) -> None:
        for name, resource in hand_written(config).items():
            for step in ("create", "update", "delete"):
                if step in jobs(resource):
                    continue
                for location in calls(resource, step):
                    codes = success_codes(operation_of(document, location))
                    assert "202" not in codes, (name, step, codes)

    def test_what_is_marked_sensitive_is_a_field_of_the_api(
        self, config: dict[str, Any], document: dict[str, Any]
    ) -> None:
        for name, resource in hand_written(config).items():
            fields: set[str] = set()
            for step in ("create", "update"):
                for location in calls(resource, step):
                    operation = operation_of(document, location)
                    if operation.get("requestBody"):
                        fields |= set(body_schema(document, operation).get("properties", {}))
                    fields |= set(answer_schema(document, operation).get("properties", {}))
            for sensitive in resource.get("sensitive", []):
                assert sensitive in fields, f"{name}: {sensitive!r} is a field of no call"

    def test_the_identity_names_what_the_paths_are_made_of(self, config: dict[str, Any]) -> None:
        for name, resource in hand_written(config).items():
            assert re.fullmatch(r"[a-z_]+(/[a-z_]+)*", resource["id"]), name

    def test_the_list_data_sources_read_an_address_of_the_api(
        self, config: dict[str, Any], document: dict[str, Any]
    ) -> None:
        for name, source in config["x_noust"]["list_data_sources"].items():
            operation_of(document, source["read"])
            assert name not in config["data_sources"], (
                f"{name} is derived: it has a read of one object"
            )


class TestThePageSaysWhatIsInTheMap:
    def test_every_resource_and_data_source_is_named_in_the_guide(
        self, config: dict[str, Any]
    ) -> None:
        text = DOC.read_text(encoding="utf-8")
        names = set(config["resources"]) | set(config["data_sources"]) | set(hand_written(config))

        missing = sorted(name for name in names if f"noust_{name}" not in text)

        assert missing == [], f"docs/terraform.md does not name noust_{missing}"


PROVIDER = ROOT / "terraform-provider-noust"


def provided(config: dict[str, Any]) -> tuple[set[str], set[str]]:
    """
    Returns:
        The resources and data sources the map says the provider offers, with the ``noust_`` prefix.
    """
    offered = config["x_noust"]["provided"]
    return (
        {f"noust_{name}" for name in offered["resources"]},
        {f"noust_{name}" for name in offered["data_sources"]},
    )


@pytest.fixture(scope="module")
def report() -> dict[str, Any]:
    """terraform-provider-noust/permissions.json: computed from the operations the Go code calls."""
    return json.loads((PROVIDER / "permissions.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


class TestTheProviderOffersWhatTheMapSays:
    def test_the_resources_and_data_sources_are_the_ones_in_the_map(
        self, config: dict[str, Any], report: dict[str, Any]
    ) -> None:
        resources, data_sources = provided(config)
        found = set(report["resources"]) - {"provider"}

        assert {name for name in found if not name.startswith("data.")} == resources
        assert {
            name.removeprefix("data.") for name in found if name.startswith("data.")
        } == data_sources

    def test_every_resource_it_offers_is_in_the_map(self, config: dict[str, Any]) -> None:
        resources, data_sources = provided(config)
        mapped = {f"noust_{name}" for name in set(config["resources"]) | set(hand_written(config))}
        listed = {f"noust_{name}" for name in config["x_noust"]["list_data_sources"]}
        derived = {f"noust_{name}" for name in config["data_sources"]}

        assert resources <= mapped
        assert data_sources <= derived | listed

    def test_every_permission_the_provider_asks_for_is_one_of_the_servers(
        self, report: dict[str, Any], document: dict[str, Any]
    ) -> None:
        held = {
            operation.get("x-noust-permission")
            for item in document["paths"].values()
            for verb, operation in item.items()
            if verb in {"get", "post", "put", "patch", "delete"}
        }

        assert set(report["all"]) <= held

    def test_the_guide_lists_what_each_resource_needs(self, report: dict[str, Any]) -> None:
        text = DOC.read_text(encoding="utf-8")
        rows = {}
        for line in text.splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) == 3 and cells[0].startswith("`") and cells[2] in {"yes", "no"}:
                rows[cells[0].strip("`")] = (
                    sorted(part.strip("` ") for part in cells[1].split(",")),
                    cells[2] == "yes",
                )

        for name, needs in report["resources"].items():
            if name == "provider":
                continue
            assert name in rows, f"docs/terraform.md does not say what {name} needs"
            assert rows[name] == (sorted(needs["permissions"]), needs["sudo"]), name
