# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the schema says of a few models is what the server does (item 171).

The console's types are generated from the schema, and two kinds of drift cost it hand-written narrowing: a
field the server always sends that the schema calls optional (a list with a default factory has no ``default``
in the schema, so the generator draws it optional), and a field of an answer or of a request typed as a loose
object that the console then has to narrow itself. This holds the models the built-in assistant, the plans and
the approvals answer with to what they do: what is always sent is required, a request's flags with a default
are not required of the caller, and the assistant's answer has a shape for every part of it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

from noust.core.runner import FakeRunner
from noust.web.server import create_app
from tests.support.web_auth import make_config


@pytest.fixture
def schemas(sandbox: Path, runner: FakeRunner) -> dict[str, Any]:
    app: FastAPI = create_app(make_config(sandbox))
    return dict(app.openapi()["components"]["schemas"])


#: Model to the fields the server always sends, which the schema says are required.
ALWAYS_SENT: dict[str, tuple[str, ...]] = {
    "PlanOutModel": ("commands", "files", "steps", "findings", "unknown", "needs"),
    "PlanFileOut": ("lines",),
    "PlanStepOut": ("commands",),
    "PlanNeedsOut": ("confirmations",),
    "ApprovalInfo": ("needs",),
    "ActivityCredential": ("clients",),
    "TurnOut": ("steps", "parts", "proposals", "not_offered", "warnings", "usage", "page"),
    "ProposalOut": ("facts", "needs"),
    "TurnUsageOut": ("notes",),
}


@pytest.mark.parametrize("model", sorted(ALWAYS_SENT))
def test_what_the_server_always_sends_is_required_in_the_schema(
    schemas: dict[str, Any], model: str
) -> None:
    assert model in schemas, f"{model} is not in the schema"
    required = set(schemas[model].get("required", []))

    assert set(ALWAYS_SENT[model]) <= required, sorted(set(ALWAYS_SENT[model]) - required)


@pytest.mark.parametrize("field", ["allow_private_host", "probe"])
def test_a_flag_a_request_may_leave_out_has_no_default_that_would_make_it_required(
    schemas: dict[str, Any], field: str
) -> None:
    # A property with a default is drawn as required by the generator; these are the caller's to leave out
    # (the server takes the default when it is not given, and says so in its docstring).
    schema = schemas["AssistantSettingsIn"]["properties"][field]

    assert field not in schemas["AssistantSettingsIn"].get("required", [])
    assert schema.get("default") is None
    assert {"type": "null"} in schema["anyOf"]


def is_free_form(schema: dict[str, Any]) -> bool:
    """Whether a property is an object with no shape: what the console has to narrow by hand."""
    options = schema.get("anyOf") or [schema]
    return any(
        option.get("type") == "object"
        and not option.get("properties")
        and option.get("additionalProperties") is True
        for option in options
    ) or any(not option or option == {} for option in options)


#: What the assistant answers with. Nothing of it is a loose object or an untyped value, but for the
#: arguments of a request that a conversation keeps as the model sent them.
ASSISTANT_ANSWERS = (
    "TurnOut",
    "TurnsOut",
    "TurnStartedOut",
    "StepOut",
    "PartOut",
    "ProposalOut",
    "CallOut",
    "NotOfferedOut",
    "TurnUsageOut",
    "TurnErrorOut",
    "UsageOut",
    "ConversationDetailOut",
    "MessageOut",
)

#: What a call carries as the model sent it is not a shape the server can give: it is the body of whatever
#: route the call is for.
FREE_BY_NATURE = {("CallOut", "body")}


@pytest.mark.parametrize("model", ASSISTANT_ANSWERS)
def test_no_part_of_the_assistants_answer_is_a_loose_object(
    schemas: dict[str, Any], model: str
) -> None:
    assert model in schemas, f"{model} is not in the schema"
    loose = [
        name
        for name, schema in schemas[model].get("properties", {}).items()
        if (model, name) not in FREE_BY_NATURE and is_free_form(schema)
    ]

    assert loose == []


def test_the_reason_the_session_gives_is_one_of_a_closed_list(schemas: dict[str, Any]) -> None:
    code = schemas["AssistantReason"]["properties"]["code"]

    assert set(code["enum"]) == {
        "assistant.unavailable.off",
        "assistant.unavailable.permission",
        "assistant.unavailable.account",
    }


def test_the_notes_of_the_usage_are_sentences_and_not_the_sessions_reasons(
    schemas: dict[str, Any],
) -> None:
    notes = schemas["UsageOut"]["properties"]["notes"]["items"]["$ref"].rsplit("/", 1)[1]

    # (Two models are called SentenceOut, so the schema qualifies this one with its module.)
    assert notes.endswith("assistant__SentenceOut")
    assert "enum" not in schemas[notes]["properties"]["code"]
