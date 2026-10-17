# Copyright (c) 2024-2026 Yago Lopez Prado
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


class TestWhatTheProviderWritesByHand:
    def test_every_operation_is_an_operation_of_the_api(
        self, config: dict[str, Any], document: dict[str, Any]
    ) -> None:
        for name, resource in hand_written(config).items():
            for step in ("create", "read", "update", "delete"):
                if step in resource:
                    operation_of(document, resource[step])
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

    def test_a_job_backed_call_answers_202(
        self, config: dict[str, Any], document: dict[str, Any]
    ) -> None:
        asynchronous = {n: r for n, r in hand_written(config).items() if r.get("async")}
        assert asynchronous, "applications are the one resource made by a job"
        for name, resource in asynchronous.items():
            for step in ("create", "update", "delete"):
                if step in resource:
                    codes = success_codes(operation_of(document, resource[step]))
                    assert codes == ["202"], (name, step, codes)

    def test_what_the_others_do_is_not_a_job(
        self, config: dict[str, Any], document: dict[str, Any]
    ) -> None:
        for name, resource in hand_written(config).items():
            if resource.get("async"):
                continue
            for step in ("create", "update", "delete"):
                if step in resource:
                    codes = success_codes(operation_of(document, resource[step]))
                    assert "202" not in codes, (name, step, codes)

    def test_what_is_marked_sensitive_is_a_field_of_the_api(
        self, config: dict[str, Any], document: dict[str, Any]
    ) -> None:
        for name, resource in hand_written(config).items():
            fields: set[str] = set()
            for step in ("create", "update"):
                if step in resource:
                    operation = operation_of(document, resource[step])
                    if operation.get("requestBody"):
                        fields |= set(body_schema(document, operation).get("properties", {}))
                    fields |= set(answer_schema(document, operation).get("properties", {}))
            for sensitive in resource.get("sensitive", []):
                assert sensitive in fields, f"{name}: {sensitive!r} is a field of no call"

    def test_the_identity_names_what_the_paths_are_made_of(self, config: dict[str, Any]) -> None:
        for name, resource in hand_written(config).items():
            assert re.fullmatch(r"[a-z_]+(/[a-z_]+)*", resource["id"]), name


class TestThePageSaysWhatIsInTheMap:
    def test_every_resource_and_data_source_is_named_in_the_guide(
        self, config: dict[str, Any]
    ) -> None:
        text = DOC.read_text(encoding="utf-8")
        names = set(config["resources"]) | set(config["data_sources"]) | set(hand_written(config))

        missing = sorted(name for name in names if f"noust_{name}" not in text)

        assert missing == [], f"docs/terraform.md does not name noust_{missing}"
