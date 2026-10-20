# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The generic webhook carries the headers its receiver asks for (the gap the parity audit found).

n8n, Home Assistant and the internal gateways of a company answer only to a request that carries an
``Authorization: Bearer`` or an API key of their own, which the webhook channel could not send.
``webhook_headers`` holds them, one ``Name: value`` per line. What is pinned: the lines are read by
one parser that refuses what could split a request or take a header Noust owns, the headers reach
the request beside the ones Noust sets, the setting is a secret (redacted when read back, sealed in
the file), and a bad value is refused when it is saved, not when the first alert needs it.
"""

from __future__ import annotations

import pytest

from noust.core import notifier as notifier_module
from noust.core.config import ConfigError, is_secret_key, redact_secrets
from noust.core.notifications import instances
from noust.core.notifications.render import webhook
from tests.notifications_support import hostile


class TestTheParser:
    def test_one_header_per_line_with_the_name_before_the_first_colon(self) -> None:
        text = "Authorization: Bearer abc.def\nX-Environment: production\n"

        assert webhook.parse_headers(text) == {
            "Authorization": "Bearer abc.def",
            "X-Environment": "production",
        }

    def test_a_value_may_hold_colons_and_spaces(self) -> None:
        assert webhook.parse_headers("X-Trace: a:b c") == {"X-Trace": "a:b c"}

    def test_blank_lines_and_windows_line_ends_are_nothing(self) -> None:
        assert webhook.parse_headers("\r\nX-A: 1\r\n\r\nX-B: 2\r\n") == {"X-A": "1", "X-B": "2"}

    def test_empty_means_none(self) -> None:
        assert webhook.parse_headers("") == {}
        assert webhook.parse_headers(None) == {}

    @pytest.mark.parametrize(
        ("text", "why"),
        [
            ("no colon here", "line 1"),
            (": value", "line 1"),
            ("Bad Name: value", "name"),
            ("X-A: ", "value"),
            ("X-A: one\nX-a: two", "twice"),
            ("X-A: café", "ASCII"),
            ("X-A: a\x00b", "control"),
            ("Content-Type: text/plain", "Noust sets"),
            ("Content-Length: 3", "Noust sets"),
            ("Host: elsewhere.example", "Noust sets"),
            ("X-Noust-Signature: sha256=00", "Noust sets"),
            ("x-noust-event: forged", "Noust sets"),
            ("Transfer-Encoding: chunked", "Noust sets"),
            ("User-Agent: other", "Noust sets"),
        ],
    )
    def test_what_could_split_a_request_or_take_a_header_noust_owns_is_refused(
        self, text: str, why: str
    ) -> None:
        with pytest.raises(ValueError, match=why):
            webhook.parse_headers(text)

    def test_too_many_headers_are_refused(self) -> None:
        text = "\n".join(f"X-H{i}: v" for i in range(webhook.MAX_CUSTOM_HEADERS + 1))

        with pytest.raises(ValueError, match="at most"):
            webhook.parse_headers(text)

    def test_a_value_too_long_is_refused(self) -> None:
        with pytest.raises(ValueError, match="longer"):
            webhook.parse_headers("X-A: " + "x" * (webhook.MAX_HEADER_VALUE + 1))


class TestTheRequest:
    def test_the_custom_headers_reach_the_request_beside_the_ones_noust_sets(self) -> None:
        n = next(iter(hostile("en").values()))
        body = webhook.encode(webhook.render(n))

        result = webhook.headers(n, body, "s3cr3t", {"Authorization": "Bearer abc"})

        assert result["Authorization"] == "Bearer abc"
        assert result["X-Noust-Event"] == n.kind
        assert result["X-Noust-Delivery"] == n.id
        assert result["X-Noust-Signature"].startswith("sha256=")

    def test_a_custom_header_can_never_replace_one_noust_sets(self) -> None:
        n = next(iter(hostile("en").values()))
        body = webhook.encode(webhook.render(n))

        result = webhook.headers(n, body, "", {"X-Noust-Event": "forged"})

        assert result["X-Noust-Event"] == n.kind

    def test_the_channel_sends_them(self) -> None:
        n = next(iter(hostile("en").values()))

        request = notifier_module._webhook_request(
            "https://hooks.example.com/noust", n, "", "Authorization: Bearer abc\nX-A: 1"
        )

        sent = {key.lower(): value for key, value in request.header_items()}
        assert sent["authorization"] == "Bearer abc"
        assert sent["x-a"] == "1"
        assert sent["content-type"] == "application/json"


class TestTheSetting:
    def test_it_is_a_secret_by_its_name(self) -> None:
        assert is_secret_key("webhook_headers")

    def test_it_is_redacted_when_read_back(self) -> None:
        shown = redact_secrets(
            {"notifications": {"channels": {"webhook": {"webhook_headers": "Authorization: x"}}}}
        )

        assert shown["notifications"]["channels"]["webhook"]["webhook_headers"] == "***"

    def test_the_webhook_type_accepts_it_and_other_types_do_not(self) -> None:
        assert "webhook_headers" in instances.TYPES["webhook"].keys
        assert "webhook_headers" in instances.TYPES["webhook"].secret_keys
        assert "webhook_headers" not in instances.TYPES["slack"].keys

    def test_a_bad_value_is_refused_when_it_is_saved(self) -> None:
        with pytest.raises(ValueError, match="webhook_headers"):
            instances._check_type_settings(
                "webhook", {"webhook_headers": "not a header"}, "notifications.channels.webhook"
            )

    def test_a_good_value_passes(self) -> None:
        instances._check_type_settings(
            "webhook",
            {"webhook_headers": "Authorization: Bearer abc"},
            "notifications.channels.webhook",
        )

    def test_the_redaction_placeholder_is_not_a_header(self) -> None:
        instances._check_type_settings(
            "webhook", {"webhook_headers": "***"}, "notifications.channels.webhook"
        )

    def test_the_configuration_refuses_it_too(self) -> None:
        from noust.core.config import Config

        Config.reset_instance()
        try:
            with pytest.raises(ConfigError, match="webhook_headers"):
                Config().set("notifications.channels.webhook.webhook_headers", "garbage")
        finally:
            Config.reset_instance()
