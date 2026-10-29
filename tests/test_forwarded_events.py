# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An event of a code host, as a central hands it to a server and the server reads it back.

What is pinned: an event survives the trip unchanged (JSON in between); the reading is closed (a
field nobody defined, a value of the wrong type or shape is a refusal that names the field, not
something half read); and what a server will act on is judged here, at the one place, because the
central that sends it is trusted with a deploy ceiling and not with the server's shell.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from noust.core.exceptions import ValidationError
from noust.core.forge_events import (
    Forge,
    PullRequestAction,
    PullRequestEvent,
    PushChange,
    PushEvent,
    TagEvent,
    event_kind,
    event_to_dict,
)
from noust.validators.forge_event import parse_forwarded_event

SHA = "a" * 40

PUSH = PushEvent(
    forge=Forge.GITHUB,
    repository="you/app",
    clone_url="https://github.com/you/app.git",
    branch="release/1.x",
    head_sha=SHA,
    installation_id=77,
    change=PushChange(messages=("fix: a\n\nbody",), files=("src/a.py", "README.md")),
)
TAG = TagEvent(
    forge=Forge.GITHUB,
    tag="v1.2.3",
    source="release",
    repository="you/app",
    clone_url="https://github.com/you/app.git",
    sha=SHA,
    installation_id=77,
    skip="it is a draft release",
)
PULL = PullRequestEvent(
    forge=Forge.GITHUB,
    action=PullRequestAction.OPENED,
    repository="you/app",
    clone_url="https://github.com/you/app.git",
    number=12,
    title="Add a thing",
    branch="feature/x",
    base_branch="main",
    head_sha=SHA,
    from_fork=False,
    installation_id=77,
    author="ana",
    sender="ana",
    bot=False,
    author_association="MEMBER",
    head_message="Add a thing",
)


def trip(event: Any) -> Any:
    """The event as JSON goes over the tunnel and as the server reads it."""
    wire = json.loads(json.dumps(event_to_dict(event)))
    return parse_forwarded_event(event_kind(event), wire)


class TestTheTrip:
    @pytest.mark.parametrize("event", [PUSH, TAG, PULL], ids=["push", "tag", "pull_request"])
    def test_an_event_arrives_as_it_left(self, event: Any) -> None:
        assert trip(event) == event

    def test_the_kind_is_the_name_the_route_takes(self) -> None:
        assert [event_kind(e) for e in (PUSH, TAG, PULL)] == ["push", "tag", "pull_request"]

    def test_a_push_with_no_file_list_stays_unknown_and_not_empty(self) -> None:
        unknown = PushEvent(
            forge=Forge.GITHUB,
            repository="you/app",
            clone_url="https://github.com/you/app.git",
            branch="main",
            head_sha=SHA,
            change=PushChange(messages=("m",), files=None, messages_complete=False),
        )

        read = trip(unknown)

        assert read.change.files is None and read.change.messages_complete is False

    def test_what_a_server_is_sent_is_plain_json(self) -> None:
        for event in (PUSH, TAG, PULL):
            json.dumps(event_to_dict(event))


class TestTheReadingIsClosed:
    def wire(self, event: Any = PUSH) -> dict[str, Any]:
        return json.loads(json.dumps(event_to_dict(event)))

    def test_a_field_nobody_defined_is_refused_by_name(self) -> None:
        wire = self.wire()
        wire["run"] = "rm -rf /"

        with pytest.raises(ValidationError, match="run"):
            parse_forwarded_event("push", wire)

    @pytest.mark.parametrize("missing", ["repository", "branch", "head_sha", "forge"])
    def test_a_field_that_is_missing_is_refused_by_name(self, missing: str) -> None:
        wire = self.wire()
        del wire[missing]

        with pytest.raises(ValidationError, match=missing):
            parse_forwarded_event("push", wire)

    @pytest.mark.parametrize("kind", ["", "deploy", "PUSH", None, 5])
    def test_a_kind_that_is_not_one_is_refused(self, kind: Any) -> None:
        with pytest.raises(ValidationError):
            parse_forwarded_event(kind, self.wire())

    @pytest.mark.parametrize("data", [None, [], "x", 5])
    def test_an_event_that_is_not_an_object_is_refused(self, data: Any) -> None:
        with pytest.raises(ValidationError):
            parse_forwarded_event("push", data)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("repository", "../x"),
            ("repository", "you/app\n"),
            ("repository", "you"),
            ("branch", "--upload-pack=x"),
            ("branch", "a..b"),
            ("branch", "a b"),
            ("branch", "x\n"),
            ("branch", "/x"),
            ("branch", "x/"),
            ("branch", "x.lock"),
            ("branch", ""),
            ("head_sha", "main"),
            ("head_sha", "A" * 40),
            ("head_sha", "a" * 39),
            ("head_sha", "a" * 40 + "\n"),
            ("clone_url", "http://github.com/you/app.git"),
            ("clone_url", "https://me:pw@github.com/you/app.git"),
            ("clone_url", "file:///etc"),
            ("clone_url", "https://github.com/you/app.git\nX: 1"),
            ("installation_id", 0),
            ("installation_id", True),
            ("installation_id", "7"),
            ("forge", "svn"),
            ("forge", 5),
        ],
    )
    def test_a_value_that_is_not_what_the_field_holds_is_refused_by_name(
        self, field: str, value: Any
    ) -> None:
        wire = self.wire()
        wire[field] = value

        with pytest.raises(ValidationError, match=field):
            parse_forwarded_event("push", wire)

    def test_a_pull_request_is_judged_the_same(self) -> None:
        wire = self.wire(PULL)
        for field, value in (
            ("number", 0),
            ("number", True),
            ("action", "merged"),
            ("branch", "a b"),
            ("from_fork", "yes"),
            ("bot", 1),
        ):
            with pytest.raises(ValidationError, match=field):
                parse_forwarded_event("pull_request", {**wire, field: value})

    def test_a_tag_is_judged_the_same(self) -> None:
        wire = self.wire(TAG)
        for field, value in (("tag", "v1 .2"), ("source", "cron"), ("sha", "x"), ("skip", 5)):
            with pytest.raises(ValidationError, match=field):
                parse_forwarded_event("tag", {**wire, field: value})

    def test_the_lists_of_a_push_are_bounded(self) -> None:
        wire = self.wire()
        wire["change"]["messages"] = ["m"] * 1001
        with pytest.raises(ValidationError, match="messages"):
            parse_forwarded_event("push", wire)

        wire = self.wire()
        wire["change"]["files"] = ["f"] * 20001
        with pytest.raises(ValidationError, match="files"):
            parse_forwarded_event("push", wire)

        wire = self.wire()
        wire["change"]["files"] = ["a\x00b"]
        with pytest.raises(ValidationError, match="files"):
            parse_forwarded_event("push", wire)
