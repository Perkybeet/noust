# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The API's compatibility promise, held by a test (checklist J-03).

``panel/openapi.baseline.json`` is the contract: the API as a release shipped
it, exported the way ``panel/openapi.json`` is. ``test_the_api_still_keeps_what_the_baseline_promises``
fails when an operation the baseline has

- disappears, or changes the method it answers to;
- loses a response code it documents;
- gains a required parameter, a required request-body field, or a body where
  it had none;
- loses a parameter, a request-body field or a response field it had.

Adding is always allowed: a new operation, a new optional parameter, a new
response field. A client written against the baseline keeps working.

**Accepting an intentional break.** A break is sometimes the right change (a
security fix, an operation that was never usable). It is then a decision, made
in the open, in the same change as the code:

1. ``python scripts/api_compat.py --update --accept-breaking`` rewrites the
   baseline from the API as it now is;
2. add the entry to ``docs/API-CHANGELOG.md`` under "Breaking changes", saying
   what, why and what a client does instead;
3. for anything but a security fix, it should have been announced first with
   :func:`noust.web.versioning.deprecated` and be past its ``Sunset``.

After a release, refresh the baseline with ``python scripts/api_compat.py
--update`` (no flag: it refuses while the API breaks the old one), so what the
release added is promised from then on.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from scripts import api_compat
from scripts.api_compat import BASELINE, Break, compare

pydantic = pytest.importorskip("pydantic")


def doc(**paths: dict[str, Any]) -> dict[str, Any]:
    """
    A small OpenAPI document.

    Args:
        **paths: Path item by name, ``/`` written as ``__``.

    Returns:
        The document.
    """
    return {
        "openapi": "3.1.0",
        "paths": {name.replace("__", "/"): item for name, item in paths.items()},
        "components": {"schemas": {}},
    }


def op(
    responses: dict[str, Any] | None = None,
    parameters: list[dict[str, Any]] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    One operation.

    Args:
        responses: Responses by status; ``{"200": {}}`` by default.
        parameters: Its parameters.
        body: Its ``requestBody``.

    Returns:
        The operation object.
    """
    operation: dict[str, Any] = {"responses": responses or {"200": {"description": "ok"}}}
    if parameters is not None:
        operation["parameters"] = parameters
    if body is not None:
        operation["requestBody"] = body
    return operation


def query(name: str, required: bool = False) -> dict[str, Any]:
    return {"name": name, "in": "query", "required": required, "schema": {"type": "string"}}


def json_body(schema: dict[str, Any], required: bool = True) -> dict[str, Any]:
    return {"required": required, "content": {"application/json": {"schema": schema}}}


def json_response(schema: dict[str, Any], status: str = "200") -> dict[str, Any]:
    return {status: {"description": "ok", "content": {"application/json": {"schema": schema}}}}


def obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


STRING = {"type": "string"}


def kinds(breaks: list[Break]) -> list[str]:
    return [item.kind for item in breaks]


class TestWhatIsAllowed:
    def test_the_same_document_breaks_nothing(self) -> None:
        document = doc(__api__apps={"get": op()})

        assert compare(document, copy.deepcopy(document)) == []

    def test_a_new_operation_a_new_path_and_a_new_method_are_fine(self) -> None:
        before = doc(__api__apps={"get": op()})
        after = doc(
            __api__apps={"get": op(), "post": op()},
            __api__servers={"get": op()},
        )

        assert compare(before, after) == []

    def test_a_new_response_code_and_a_new_optional_parameter_are_fine(self) -> None:
        before = doc(__api__apps={"get": op(parameters=[query("limit")])})
        after = doc(
            __api__apps={
                "get": op(
                    responses={"200": {"description": "ok"}, "409": {"description": "busy"}},
                    parameters=[query("limit"), query("after"), query("sort", required=False)],
                )
            }
        )

        assert compare(before, after) == []

    def test_a_new_response_field_and_a_new_optional_request_field_are_fine(self) -> None:
        before = doc(
            __api__x={
                "post": op(
                    responses=json_response(obj({"id": STRING})),
                    body=json_body(obj({"name": STRING}, ["name"])),
                )
            }
        )
        after = doc(
            __api__x={
                "post": op(
                    responses=json_response(obj({"id": STRING, "extra": STRING})),
                    body=json_body(obj({"name": STRING, "note": STRING}, ["name"])),
                )
            }
        )

        assert compare(before, after) == []

    def test_a_required_field_becoming_optional_is_fine(self) -> None:
        before = doc(__api__x={"post": op(body=json_body(obj({"a": STRING}, ["a"])))})
        after = doc(__api__x={"post": op(body=json_body(obj({"a": STRING})))})

        assert compare(before, after) == []

    def test_a_rename_of_a_path_parameter_is_not_a_change_on_the_wire(self) -> None:
        def named(template: str, parameter: str) -> dict[str, Any]:
            path_parameter = {"name": parameter, "in": "path", "required": True, "schema": STRING}
            return {"paths": {template: {"get": op(parameters=[path_parameter])}}}

        before = named("/api/apps/{domain}", "domain")
        after = named("/api/apps/{name}", "name")

        assert compare(before, after) == []

    def test_a_deprecated_operation_is_still_there(self) -> None:
        before = doc(__api__old={"get": op()})
        after = doc(__api__old={"get": {**op(), "deprecated": True}})

        assert compare(before, after) == []

    def test_the_extensions_are_not_part_of_the_promise(self) -> None:
        before = doc(__api__x={"get": {**op(), "x-noust-permission": "apps.read"}})
        after = doc(__api__x={"get": {**op(), "x-noust-permission": "apps.manage"}})

        assert compare(before, after) == []


class TestWhatBreaks:
    def test_a_removed_operation(self) -> None:
        before = doc(__api__apps={"get": op(), "post": op()})
        after = doc(__api__apps={"get": op()})

        breaks = compare(before, after)

        assert kinds(breaks) == ["operation-removed"]
        assert breaks[0].operation == "POST /api/apps"

    def test_a_removed_path(self) -> None:
        breaks = compare(doc(__api__apps={"get": op()}), doc())

        assert kinds(breaks) == ["operation-removed"]

    def test_a_changed_method_says_so(self) -> None:
        before = doc(__api__apps__restart={"get": op()})
        after = doc(__api__apps__restart={"post": op()})

        breaks = compare(before, after)

        assert kinds(breaks) == ["method-changed"]
        assert breaks[0].operation == "GET /api/apps/restart"
        assert "POST" in breaks[0].detail

    def test_a_lost_response_code(self) -> None:
        before = doc(
            __api__x={
                "get": op(responses={"200": {"description": "ok"}, "404": {"description": "n"}})
            }
        )
        after = doc(__api__x={"get": op(responses={"200": {"description": "ok"}})})

        breaks = compare(before, after)

        assert kinds(breaks) == ["response-removed"]
        assert "404" in breaks[0].detail

    def test_a_new_required_query_parameter(self) -> None:
        before = doc(__api__x={"get": op(parameters=[query("limit")])})
        after = doc(
            __api__x={"get": op(parameters=[query("limit"), query("scope", required=True)])}
        )

        breaks = compare(before, after)

        assert kinds(breaks) == ["parameter-required"]
        assert "scope" in breaks[0].detail

    def test_an_optional_parameter_becoming_required(self) -> None:
        before = doc(__api__x={"get": op(parameters=[query("limit")])})
        after = doc(__api__x={"get": op(parameters=[query("limit", required=True)])})

        assert kinds(compare(before, after)) == ["parameter-required"]

    def test_a_new_required_header(self) -> None:
        header = {"name": "X-Thing", "in": "header", "required": True, "schema": STRING}
        before = doc(__api__x={"get": op()})
        after = doc(__api__x={"get": op(parameters=[header])})

        assert kinds(compare(before, after)) == ["parameter-required"]

    def test_a_removed_parameter(self) -> None:
        before = doc(__api__x={"get": op(parameters=[query("limit"), query("status")])})
        after = doc(__api__x={"get": op(parameters=[query("limit")])})

        breaks = compare(before, after)

        assert kinds(breaks) == ["parameter-removed"]
        assert "status" in breaks[0].detail

    def test_a_required_body_where_there_was_none(self) -> None:
        before = doc(__api__x={"post": op()})
        after = doc(__api__x={"post": op(body=json_body(obj({"a": STRING})))})

        assert kinds(compare(before, after)) == ["body-required"]

    def test_an_optional_body_becoming_required(self) -> None:
        before = doc(__api__x={"post": op(body=json_body(obj({"a": STRING}), required=False))})
        after = doc(__api__x={"post": op(body=json_body(obj({"a": STRING}), required=True))})

        assert kinds(compare(before, after)) == ["body-required"]

    def test_a_new_optional_body_is_fine(self) -> None:
        before = doc(__api__x={"post": op()})
        after = doc(__api__x={"post": op(body=json_body(obj({"a": STRING}), required=False))})

        assert compare(before, after) == []

    def test_a_new_required_body_field(self) -> None:
        before = doc(__api__x={"post": op(body=json_body(obj({"a": STRING}, ["a"])))})
        after = doc(
            __api__x={"post": op(body=json_body(obj({"a": STRING, "b": STRING}, ["a", "b"])))}
        )

        breaks = compare(before, after)

        assert kinds(breaks) == ["body-field-required"]
        assert "b" in breaks[0].detail

    def test_an_optional_body_field_becoming_required(self) -> None:
        before = doc(__api__x={"post": op(body=json_body(obj({"a": STRING})))})
        after = doc(__api__x={"post": op(body=json_body(obj({"a": STRING}, ["a"])))})

        assert kinds(compare(before, after)) == ["body-field-required"]

    def test_a_removed_body_field(self) -> None:
        before = doc(__api__x={"post": op(body=json_body(obj({"a": STRING, "b": STRING})))})
        after = doc(__api__x={"post": op(body=json_body(obj({"a": STRING})))})

        breaks = compare(before, after)

        assert kinds(breaks) == ["body-field-removed"]
        assert "b" in breaks[0].detail

    def test_a_removed_response_field(self) -> None:
        before = doc(
            __api__x={"get": op(responses=json_response(obj({"id": STRING, "n": STRING})))}
        )
        after = doc(__api__x={"get": op(responses=json_response(obj({"id": STRING})))})

        breaks = compare(before, after)

        assert kinds(breaks) == ["response-field-removed"]
        assert "n" in breaks[0].detail

    def test_a_response_media_type_that_goes_away(self) -> None:
        before = doc(__api__x={"get": op(responses=json_response(obj({"id": STRING})))})
        after = doc(
            __api__x={
                "get": op(
                    responses={
                        "200": {"description": "ok", "content": {"text/plain": {"schema": STRING}}}
                    }
                )
            }
        )

        assert kinds(compare(before, after)) == ["media-type-removed"]

    def test_every_break_of_one_operation_is_reported(self) -> None:
        before = doc(
            __api__x={
                "post": op(
                    responses={"200": {"description": "ok"}, "404": {"description": "n"}},
                    parameters=[query("a")],
                )
            }
        )
        after = doc(
            __api__x={
                "post": op(
                    responses={"200": {"description": "ok"}},
                    parameters=[query("b", required=True)],
                )
            }
        )

        assert sorted(kinds(compare(before, after))) == [
            "parameter-removed",
            "parameter-required",
            "response-removed",
        ]


class TestSchemasAreResolved:
    def referenced(self, **schemas: dict[str, Any]) -> dict[str, Any]:
        document = doc(
            __api__x={
                "post": op(
                    responses=json_response({"$ref": "#/components/schemas/Out"}),
                    body=json_body({"$ref": "#/components/schemas/In"}),
                )
            }
        )
        document["components"]["schemas"] = schemas
        return document

    def test_a_reference_is_followed_on_both_sides(self) -> None:
        before = self.referenced(In=obj({"a": STRING}, ["a"]), Out=obj({"id": STRING, "n": STRING}))
        after = self.referenced(
            In=obj({"a": STRING, "b": STRING}, ["a", "b"]), Out=obj({"id": STRING})
        )

        assert sorted(kinds(compare(before, after))) == [
            "body-field-required",
            "response-field-removed",
        ]

    def test_a_renamed_model_is_not_a_change(self) -> None:
        before = self.referenced(In=obj({"a": STRING}), Out=obj({"id": STRING}))
        after = self.referenced(In=obj({"a": STRING}), Out=obj({"id": STRING}))
        after["components"]["schemas"]["Renamed"] = after["components"]["schemas"].pop("Out")
        after["paths"]["/api/x"]["post"]["responses"]["200"]["content"]["application/json"][
            "schema"
        ] = {"$ref": "#/components/schemas/Renamed"}

        assert compare(before, after) == []

    def test_a_nested_object_is_compared(self) -> None:
        before = self.referenced(
            In=obj({"a": obj({"b": STRING})}), Out=obj({"inner": obj({"x": STRING, "y": STRING})})
        )
        after = self.referenced(
            In=obj({"a": obj({"b": STRING}, ["b"])}), Out=obj({"inner": obj({"x": STRING})})
        )

        breaks = compare(before, after)

        assert sorted(kinds(breaks)) == ["body-field-required", "response-field-removed"]
        assert any("inner.y" in item.detail for item in breaks)
        assert any("a.b" in item.detail for item in breaks)

    def test_a_list_of_objects_is_compared(self) -> None:
        before = self.referenced(
            In=obj({}),
            Out=obj({"items": {"type": "array", "items": obj({"id": STRING, "n": STRING})}}),
        )
        after = self.referenced(
            In=obj({}), Out=obj({"items": {"type": "array", "items": obj({"id": STRING})}})
        )

        breaks = compare(before, after)

        assert kinds(breaks) == ["response-field-removed"]
        assert "items[].n" in breaks[0].detail

    def test_an_optional_model_is_compared_through_its_union(self) -> None:
        optional = {"anyOf": [{"$ref": "#/components/schemas/Inner"}, {"type": "null"}]}
        before = self.referenced(
            In=obj({}), Out=obj({"inner": optional}), Inner=obj({"x": STRING, "y": STRING})
        )
        after = self.referenced(In=obj({}), Out=obj({"inner": optional}), Inner=obj({"x": STRING}))

        assert kinds(compare(before, after)) == ["response-field-removed"]

    def test_a_model_made_of_parts_is_compared_through_its_parts(self) -> None:
        before = self.referenced(
            In=obj({}), Out={"allOf": [obj({"id": STRING}), obj({"n": STRING})]}
        )
        after = self.referenced(In=obj({}), Out={"allOf": [obj({"id": STRING})]})

        assert kinds(compare(before, after)) == ["response-field-removed"]

    def test_a_model_that_contains_itself_terminates(self) -> None:
        tree = obj({"name": STRING, "child": {"$ref": "#/components/schemas/Tree"}})
        before = self.referenced(In=obj({}), Out=tree, Tree=tree)
        before["components"]["schemas"]["Out"] = {"$ref": "#/components/schemas/Tree"}
        after = copy.deepcopy(before)

        assert compare(before, after) == []

        after["components"]["schemas"]["Tree"]["properties"].pop("name")
        assert kinds(compare(before, after)) == ["response-field-removed"]

    def test_a_missing_reference_is_not_a_crash(self) -> None:
        before = self.referenced(In=obj({}), Out=obj({"id": STRING}))
        after = self.referenced(In=obj({}))

        # The target is gone from the new document: nothing to compare against, and the
        # structural checks still run.
        assert compare(before, after) is not None


class TestTheCommand:
    def test_it_passes_when_nothing_breaks(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps(doc(__api__apps={"get": op()})))
        current = tmp_path / "current.json"
        current.write_text(json.dumps(doc(__api__apps={"get": op(), "post": op()})))

        status = api_compat.main(["--baseline", str(baseline), "--current", str(current)])

        assert status == 0
        assert "1 operation" in capsys.readouterr().out

    def test_it_fails_and_names_every_break(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps(doc(__api__apps={"get": op(), "post": op()})))
        current = tmp_path / "current.json"
        current.write_text(json.dumps(doc(__api__apps={"get": op()})))

        status = api_compat.main(["--baseline", str(baseline), "--current", str(current)])

        assert status == 1
        err = capsys.readouterr().err
        assert "POST /api/apps" in err
        assert "docs/API-CHANGELOG.md" in err
        assert "--accept-breaking" in err

    def test_it_reports_a_missing_baseline(self, tmp_path: Path) -> None:
        current = tmp_path / "current.json"
        current.write_text(json.dumps(doc()))

        assert (
            api_compat.main(["--baseline", str(tmp_path / "none.json"), "--current", str(current)])
            == 1
        )

    def test_update_takes_an_addition_without_asking(self, tmp_path: Path) -> None:
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps(doc(__api__apps={"get": op()})))
        current = tmp_path / "current.json"
        added = doc(__api__apps={"get": op(), "post": op()})
        current.write_text(json.dumps(added))

        status = api_compat.main(
            ["--baseline", str(baseline), "--current", str(current), "--update"]
        )

        assert status == 0
        assert json.loads(baseline.read_text()) == added

    def test_update_refuses_a_break_unless_it_is_accepted(self, tmp_path: Path) -> None:
        old = doc(__api__apps={"get": op(), "post": op()})
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps(old))
        current = tmp_path / "current.json"
        current.write_text(json.dumps(doc(__api__apps={"get": op()})))
        arguments = ["--baseline", str(baseline), "--current", str(current), "--update"]

        assert api_compat.main(arguments) == 1
        assert json.loads(baseline.read_text()) == old

        assert api_compat.main([*arguments, "--accept-breaking"]) == 0
        assert json.loads(baseline.read_text()) == doc(__api__apps={"get": op()})

    def test_the_baseline_is_written_the_way_the_export_is(self, tmp_path: Path) -> None:
        baseline = tmp_path / "baseline.json"
        current = tmp_path / "current.json"
        current.write_text(json.dumps(doc(__api__b={"get": op()}, __api__a={"get": op()})))

        api_compat.main(["--baseline", str(baseline), "--current", str(current), "--update"])

        text = baseline.read_text()
        assert text == json.dumps(json.loads(text), sort_keys=True, indent=2) + "\n"


class TestTheRealBaseline:
    def test_the_baseline_is_there_and_is_an_openapi_document(self) -> None:
        document = json.loads(BASELINE.read_text(encoding="utf-8"))

        assert document["openapi"].startswith("3.")
        assert "/api/apps" in document["paths"]
        assert document["info"]["version"] == "1"

    @pytest.mark.skipif(
        not pydantic.VERSION.startswith("2."),
        reason="the baseline is exported under pydantic 2; 1.10 words some schemas differently",
    )
    def test_the_api_still_keeps_what_the_baseline_promises(self) -> None:
        """
        The compatibility promise, held against the real API.

        If this fails, an operation of the baseline was removed or changed
        incompatibly. Read the list it prints: each line is one break. See the
        module docstring for how to accept one on purpose.
        """
        from scripts.export_openapi import _build_schema

        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))

        breaks = compare(baseline, _build_schema())

        assert not breaks, (
            "The API no longer keeps what panel/openapi.baseline.json promises:\n"
            + "\n".join(f"  {item}" for item in breaks)
            + "\n\nUndo the change, or accept it on purpose: see the docstring of "
            "tests/test_api_compat.py and add it to docs/API-CHANGELOG.md."
        )
