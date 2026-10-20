# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
New Relic is a destination of logs of its own (checklist G-14).

It takes the Log API's shape, not Loki's or a plain JSON array: a license key in the ``Api-Key``
header (not a bearer token), one ``common`` block of attributes beside the lines, and the US or
the EU endpoint. What is pinned: the settings a New Relic drain takes and refuses, the key is a
secret that leaves in one header, the body is the Log API's, a destination that answers an error
says what it answered, and the kind is in the places that list the kinds.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import ValidationError
from noust.core.log_drain_store import KINDS, Drain
from noust.managers.log_drains import build_transport, validate_settings
from noust.managers.log_transports import DeliveryError, LogEvent, NewRelicTransport

LICENSE = "eu01xx0123456789abcdef0123456789abcdefNRAL"


def event(message: str = "hello", **fields: Any) -> LogEvent:
    values: dict[str, Any] = {
        "ts_ns": 1_790_000_000_123_456_789,
        "message": message,
        "app": "shop.example.com",
        "process": "web",
        "unit": "shop-example-com.service",
        "priority": 6,
        "host": "web-1",
    }
    values.update(fields)
    return LogEvent(**values)


class Opener:
    def __init__(self, status: int = 202) -> None:
        self.status = status
        self.requests: list[Any] = []

    def __call__(self, request: Any, timeout: float = 0) -> Any:
        self.requests.append(request)
        opener = self

        class Response:
            status = opener.status

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *exc: object) -> None:
                return None

            def read(self, size: int = -1) -> bytes:
                return b'{"requestId":"abc"}'

        return Response()


def test_new_relic_is_a_kind() -> None:
    assert "newrelic" in KINDS


def test_the_schema_accepts_the_kind(tmp_path: Path) -> None:
    from noust.core.log_drain_store import get_drain, put_drain
    from noust.core.store import NoustStore

    NoustStore.reset_instance()
    try:
        store = NoustStore(tmp_path / "noust.db")
        put_drain(
            Drain(name="relic", kind="newrelic", settings={"region": "eu"}),
            updated_by=None,
            store=store,
        )
        saved = get_drain("relic", store=store)
        assert saved is not None and saved.kind == "newrelic"
    finally:
        NoustStore.reset_instance()


class TestTheSettings:
    def test_the_region_defaults_to_the_us_and_the_key_is_required(self) -> None:
        cleaned = validate_settings("newrelic", {}, has_secret=True)

        assert cleaned == {"region": "us"}

    def test_the_eu_region_is_accepted(self) -> None:
        assert validate_settings("newrelic", {"region": "eu"}, has_secret=True) == {"region": "eu"}

    def test_a_region_that_does_not_exist_is_refused(self) -> None:
        with pytest.raises(ValidationError) as caught:
            validate_settings("newrelic", {"region": "mars"}, has_secret=True)
        assert caught.value.field == "region"

    def test_without_its_license_key_it_is_refused(self) -> None:
        with pytest.raises(ValidationError) as caught:
            validate_settings("newrelic", {}, has_secret=False)
        assert caught.value.field == "secret"

    def test_an_address_of_its_own_replaces_the_region(self) -> None:
        cleaned = validate_settings(
            "newrelic",
            {"url": "https://gov-log-api.newrelic.com/log/v1"},
            has_secret=True,
        )

        assert cleaned["url"] == "https://gov-log-api.newrelic.com/log/v1"

    def test_a_setting_that_is_not_new_relics_is_refused_by_name(self) -> None:
        with pytest.raises(ValidationError) as caught:
            validate_settings("newrelic", {"dataset": "x"}, has_secret=True)
        assert caught.value.field == "dataset"

    def test_labels_are_kept(self) -> None:
        cleaned = validate_settings("newrelic", {"labels": {"env": "prod"}}, has_secret=True)

        assert cleaned["labels"] == {"env": "prod"}


class TestTheRequest:
    def sent(self, region: str = "us", **kwargs: Any) -> tuple[Opener, Any]:
        opener = Opener()
        transport = NewRelicTransport(
            region=region, secret=LICENSE, labels={"env": "prod"}, opener=opener, **kwargs
        )
        transport.send([event("boom", priority=3), event("fine")])
        request = opener.requests[0]
        return opener, json.loads(request.data)

    def test_the_us_endpoint_is_the_default(self) -> None:
        opener, _ = self.sent()

        assert opener.requests[0].full_url == "https://log-api.newrelic.com/log/v1"

    def test_the_eu_endpoint(self) -> None:
        opener, _ = self.sent("eu")

        assert opener.requests[0].full_url == "https://log-api.eu.newrelic.com/log/v1"

    def test_an_address_of_its_own_is_used_as_given(self) -> None:
        opener, _ = self.sent(url="https://gov-log-api.newrelic.com/log/v1")

        assert opener.requests[0].full_url == "https://gov-log-api.newrelic.com/log/v1"

    def test_the_key_leaves_in_the_api_key_header_and_nowhere_else(self) -> None:
        opener, body = self.sent()

        headers = {key.lower(): value for key, value in opener.requests[0].header_items()}
        assert headers["api-key"] == LICENSE
        assert "authorization" not in headers
        assert LICENSE not in json.dumps(body)

    def test_the_body_is_the_log_apis_with_one_common_block(self) -> None:
        _, body = self.sent()

        [block] = body
        assert block["common"]["attributes"] == {
            "service": "noust",
            "host": "web-1",
            "env": "prod",
        }
        first, second = block["logs"]
        assert first["message"] == "boom"
        assert first["timestamp"] == 1_790_000_000_123
        assert first["attributes"] == {
            "app": "shop.example.com",
            "process": "web",
            "unit": "shop-example-com.service",
            "level": "error",
        }
        assert second["message"] == "fine"
        assert second["attributes"]["level"] == "info"

    def test_a_line_too_long_is_cut(self) -> None:
        opener = Opener()
        NewRelicTransport(region="us", secret=LICENSE, labels={}, opener=opener).send(
            [event("x" * 100_000)]
        )

        [block] = json.loads(opener.requests[0].data)
        assert len(block["logs"][0]["message"]) < 10_000

    def test_an_answer_that_is_an_error_says_what_it_was(self) -> None:
        import io
        import urllib.error

        def refuse(request: Any, timeout: float = 0) -> Any:
            raise urllib.error.HTTPError(
                request.full_url,
                403,
                "Forbidden",
                {},
                io.BytesIO(b'{"error":"bad key"}'),  # type: ignore[arg-type]
            )

        transport = NewRelicTransport(region="us", secret=LICENSE, labels={}, opener=refuse)

        with pytest.raises(DeliveryError, match="HTTP 403") as caught:
            transport.send([event()])
        assert "bad key" in str(caught.value.details)


def test_the_drain_builds_its_transport() -> None:
    drain = Drain(name="relic", kind="newrelic", settings={"region": "eu"})

    transport = build_transport(drain, LICENSE)

    assert isinstance(transport, NewRelicTransport)
    assert transport.url == "https://log-api.eu.newrelic.com/log/v1"
