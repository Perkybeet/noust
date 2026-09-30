# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the per-channel renderers in :mod:`noust.core.notifications.render`.

A renderer is a pure function, so what is defended here is its *contract*, for
the whole catalog in both languages and for text nobody should trust:

- **Every platform limit holds, and never at the console link's cost**: the
  link is a button, a context block, a title URL; only the excerpt gives way.
- **Escaping is done once, at the boundary of each grammar**: Telegram's HTML,
  Slack's mrkdwn, Discord's markdown, email's HTML. Hostile text arrives as
  text.
- **The decisions of the design hold on every channel**: state told three
  ways, no emoji, silent for what went right, the console link once.
- **The webhook's contract**: version 1, additive to the keys that existed.

What the messages *look like* is pinned by the snapshot tests instead.
"""

from __future__ import annotations

import hashlib
import hmac
import html as html_lib
import json
import re
from html.parser import HTMLParser

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from noust.core.notifications.composers import compose_deploy
from noust.core.notifications.excerpt import normalize
from noust.core.notifications.model import (
    STATE_COLORS,
    STATE_GLYPHS,
    Excerpt,
    Fact,
    Link,
    Notification,
    State,
)
from noust.core.notifications.render import discord, email, slack, telegram, webhook
from noust.core.notifications.render.common import http_url, render_text
from noust.deployers.deploy_events import DeployEvent, DeployEventKind
from tests.notifications_support import (
    FIXED_TS,
    catalog,
    context,
    hostile,
    html_text,
    json_strings,
    telegram_text,
)

LOCALES = ("en", "es")
CHAT_ID = "-1001234567890"

ALL = [
    pytest.param(locale, code, id=f"{locale}-{code}")
    for locale in LOCALES
    for code in catalog(locale)
]
HOSTILE = [
    pytest.param(locale, name, id=f"{locale}-{name}")
    for locale in LOCALES
    for name in hostile(locale)
]
EVERYTHING = ALL + HOSTILE


def get(locale: str, name: str) -> Notification:
    source = hostile(locale) if name.startswith("hostile.") else catalog(locale)  # type: ignore[arg-type]
    return source[name]


def notification(**overrides: object) -> Notification:
    fields: dict = {
        "kind": "deploy_failed",
        "code": "deploy.failed",
        "state": State.FAILED,
        "locale": "en",
        "title": "Deploy failed",
        "subject": "shop.example.com",
        "summary": "It stopped at an error.",
        "server": "web-1",
        "facts": (Fact("server", "Server", "web-1"),),
        "links": (Link("console", "Open in the console", "https://c.example.com/apps/x"),),
        "ts": FIXED_TS,
        "id": "00000000-0000-4000-8000-000000000001",
    }
    fields.update(overrides)
    return Notification(**fields)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

ALLOWED_TELEGRAM_TAGS = {"b", "i", "code", "pre", "a"}


def check_telegram_html(text: str) -> None:
    """Balanced, only the tags we use, no bare markup characters."""
    stack: list[str] = []
    for match in re.finditer(r"<(/?)([a-z]+)[^>]*>", text):
        closing, tag = match.group(1) == "/", match.group(2)
        assert tag in ALLOWED_TELEGRAM_TAGS, tag
        if closing:
            assert stack and stack.pop() == tag, f"unbalanced </{tag}>"
        else:
            stack.append(tag)
    assert not stack, stack
    bare = re.sub(r"<[^>]+>", "", text)
    assert "<" not in bare and ">" not in bare, "a bare < or >"
    assert not re.search(r"&(?!lt;|gt;|amp;|quot;)", bare), "a bare &"


class TestTelegram:
    def test_the_headline_is_state_word_and_subject(self) -> None:
        payload = telegram.render(catalog("en")["deploy.succeeded"], CHAT_ID)

        assert payload["text"].splitlines()[0] == "● <b>Deployed</b> · shop.example.com"

    def test_html_mode_and_no_link_preview(self) -> None:
        payload = telegram.render(catalog("en")["deploy.succeeded"], CHAT_ID)

        assert payload["parse_mode"] == "HTML"
        assert payload["link_preview_options"] == {"is_disabled": True}
        assert payload["chat_id"] == CHAT_ID

    @pytest.mark.parametrize(
        ("state", "silent"),
        [
            (State.OK, True),
            (State.PROGRESS, True),
            (State.INFO, True),
            (State.WARNING, False),
            (State.FAILED, False),
        ],
    )
    def test_only_what_needs_attention_makes_a_sound(self, state: State, silent: bool) -> None:
        payload = telegram.render(notification(state=state), CHAT_ID)

        assert payload["disable_notification"] is silent

    def test_the_link_is_a_button_not_a_line(self) -> None:
        n = catalog("en")["deploy.succeeded"]

        payload = telegram.render(n, CHAT_ID)

        button = payload["reply_markup"]["inline_keyboard"][0][0]
        assert button == {"text": "Open in the console", "url": n.links[0].url}
        assert n.links[0].url not in payload["text"]

    def test_no_link_no_button(self) -> None:
        payload = telegram.render(catalog("en")["test"], CHAT_ID)

        assert "reply_markup" not in payload

    def test_a_link_that_is_not_http_is_never_a_button(self) -> None:
        n = notification(links=(Link("console", "Open", "javascript:alert(1)"),))

        assert "reply_markup" not in telegram.render(n, CHAT_ID)

    def test_the_facts_are_bold_labels_and_the_server_is_first(self) -> None:
        text = telegram.render(catalog("en")["deploy.succeeded"], CHAT_ID)["text"]

        lines = text.splitlines()
        assert lines[3] == "<b>Server:</b> web-1"
        assert "<b>Started by:</b> Webhook" in lines

    def test_a_command_is_code(self) -> None:
        text = telegram.render(catalog("en")["cert.expiring"], CHAT_ID)["text"]

        assert "<b>Renew with:</b> <code>noust cert renew shop.example.com</code>" in text

    def test_the_excerpt_is_a_pre_block_with_an_italic_label(self) -> None:
        text = telegram.render(catalog("en")["deploy.rolled_back"], CHAT_ID)["text"]

        assert "<i>Last lines of the journal of shop-example-com</i>" in text
        assert "<pre>" in text and text.rstrip().endswith("</pre>")

    def test_the_journals_date_and_host_are_dropped_when_every_line_has_them(self) -> None:
        text = telegram.render(catalog("en")["deploy.rolled_back"], CHAT_ID)["text"]

        assert "Sep 29 10:45" not in text
        assert "systemd[1]: shop-example-com.service: Failed with result" in text

    def test_they_are_kept_when_a_line_lacks_them(self) -> None:
        n = notification(
            excerpt=Excerpt("Output", ("Sep 29 10:45:12 web-1 sshd[1]: x", "  at Module.load"))
        )

        text = telegram.render(n, CHAT_ID)["text"]

        assert "Sep 29 10:45:12 web-1 sshd[1]: x" in text

    def test_the_excerpt_is_at_most_eight_lines_and_says_what_it_left_out(self) -> None:
        n = catalog("en")["deploy.rolled_back"]

        text = telegram.render(n, CHAT_ID)["text"]

        pre = re.search(r"<pre>(.*)</pre>", text, re.S)
        assert pre is not None
        body = pre.group(1).splitlines()
        assert len(body) <= 9  # eight lines and the marker
        assert re.fullmatch(r"\u2026 \d+ earlier lines omitted", body[-1])

    def test_spanish_marker_and_labels(self) -> None:
        text = telegram.render(catalog("es")["deploy.rolled_back"], CHAT_ID)["text"]

        assert "<b>Servidor:</b> web-1" in text
        assert re.search(r"\d+ líneas anteriores omitidas", text)

    def test_hostile_markup_arrives_as_text(self) -> None:
        payload = telegram.render(hostile("en")["hostile.deploy_failed"], CHAT_ID)

        check_telegram_html(payload["text"])
        assert "&lt;b&gt;x&lt;/b&gt;&amp;amp;&lt;!channel&gt;" in payload["text"]
        assert "<b>x</b>" not in payload["text"]

    def test_the_urlopen_error_of_a_probe_is_escaped(self) -> None:
        text = telegram.render(hostile("en")["hostile.deploy_failed"], CHAT_ID)["text"]

        assert "&lt;urlopen error [Errno 111] Connection refused&gt;" in text

    @pytest.mark.parametrize(("locale", "name"), EVERYTHING)
    def test_every_message_is_valid_html_within_the_limit(self, locale: str, name: str) -> None:
        payload = telegram.render(get(locale, name), CHAT_ID)

        check_telegram_html(payload["text"])
        assert len(telegram_text(payload)) <= 4096

    @pytest.mark.parametrize(("locale", "name"), EVERYTHING)
    def test_the_link_survives_whatever_the_size(self, locale: str, name: str) -> None:
        n = get(locale, name)
        payload = telegram.render(n, CHAT_ID)

        if n.console_link is not None:
            assert payload["reply_markup"]["inline_keyboard"][0][0]["url"] == n.console_link.url

    def test_the_plain_retry_has_no_markup_and_the_link_as_a_line(self) -> None:
        n = catalog("en")["deploy.rolled_back"]

        payload = telegram.render(n, CHAT_ID, plain=True)

        assert "parse_mode" not in payload
        assert "<" not in payload["text"].replace("<urlopen", "")
        assert payload["text"].rstrip().endswith(n.links[0].url)
        assert "reply_markup" not in payload
        assert len(payload["text"]) <= 4096

    def test_plain_retry_keeps_silence_and_no_preview(self) -> None:
        payload = telegram.render(catalog("en")["deploy.succeeded"], CHAT_ID, plain=True)

        assert payload["disable_notification"] is True
        assert payload["link_preview_options"] == {"is_disabled": True}

    @settings(
        max_examples=120,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        subject=st.text(max_size=400),
        branch=st.text(max_size=300),
        message=st.text(max_size=300),
        lines=st.lists(st.text(max_size=400), max_size=60),
    )
    def test_any_text_keeps_the_html_valid_and_within_the_limit(
        self, subject: str, branch: str, message: str, lines: list[str]
    ) -> None:
        n = _deploy_with(subject or "x", branch, message, lines)

        payload = telegram.render(n, CHAT_ID)

        check_telegram_html(payload["text"])
        assert len(telegram_text(payload)) <= 4096
        assert payload["reply_markup"]["inline_keyboard"][0][0]["url"] == n.links[0].url
        assert "<b>Server:</b>" in payload["text"]


def _deploy_with(subject: str, branch: str, message: str, lines: list[str]) -> Notification:
    """A failed deploy whose every free-text field is the given text."""
    event = DeployEvent(
        kind=DeployEventKind.FAILED,
        domain=subject,
        deployment_id=7,
        trigger="webhook",
        commit="a1b2c3d",
        branch=branch,
        commit_message=message,
        release_id="20260929-104449",
        duration_s=72,
        operation="update",
        error_message=message or "failed",
        error_output="\n".join(lines),
        ts=FIXED_TS,
    )
    return compose_deploy(event, context("en"))


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------


class TestSlack:
    def test_a_colour_strip_and_a_fallback_and_no_top_level_text(self) -> None:
        n = catalog("en")["deploy.rolled_back"]

        payload = slack.render(n)

        assert "text" not in payload
        attachment = payload["attachments"][0]
        assert attachment["color"] == STATE_COLORS[State.WARNING].strip
        assert attachment["fallback"] == "▲ Rolled back · shop.example.com"

    @pytest.mark.parametrize("state", list(State))
    def test_the_strip_is_the_states_colour(self, state: State) -> None:
        attachment = slack.render(notification(state=state))["attachments"][0]

        assert attachment["color"] == STATE_COLORS[state].strip
        assert attachment["fallback"].startswith(STATE_GLYPHS[state])

    def test_the_layout_is_header_summary_facts_excerpt_context(self) -> None:
        blocks = slack.render(catalog("en")["deploy.rolled_back"])["attachments"][0]["blocks"]

        assert [block["type"] for block in blocks] == [
            "header",
            "section",
            "section",
            "section",
            "section",
            "context",
        ]
        assert blocks[0]["text"] == {
            "type": "plain_text",
            "text": "▲ Rolled back · shop.example.com",
            "emoji": False,
        }
        assert blocks[2]["fields"][0] == {"type": "mrkdwn", "text": "*Server*\nweb-1"}

    def test_the_link_is_a_context_link_never_a_button(self) -> None:
        n = catalog("en")["deploy.succeeded"]

        payload = slack.render(n)

        context_block = payload["attachments"][0]["blocks"][-1]
        assert context_block["elements"][0]["text"] == f"<{n.links[0].url}|Open in the console>"
        assert context_block["elements"][-1]["text"] == "Noust · web-1"
        assert "button" not in json.dumps(payload)

    def test_no_link_leaves_only_the_footer(self) -> None:
        blocks = slack.render(hostile("en")["hostile.no_link"])["attachments"][0]["blocks"]

        assert [element["text"] for element in blocks[-1]["elements"]] == ["Noust · web-1"]

    def test_control_sequences_are_entities(self) -> None:
        payload = slack.render(hostile("en")["hostile.deploy_failed"])
        text = json.dumps(payload)

        assert "<!channel>" not in text.replace("<https://", "")
        assert "&lt;!channel&gt;" in text
        assert "<@U123>" not in text

    def test_the_excerpt_cannot_close_its_own_code_block(self) -> None:
        payload = slack.render(hostile("en")["hostile.deploy_failed"])

        code = next(
            block["text"]["text"]
            for block in payload["attachments"][0]["blocks"]
            if block["type"] == "section" and "```" in block.get("text", {}).get("text", "")
        )
        assert code.count("```") == 2

    def test_markup_characters_of_untrusted_values_do_not_format(self) -> None:
        payload = slack.render(hostile("en")["hostile.deploy_failed"])
        fields = next(
            block["fields"]
            for block in payload["attachments"][0]["blocks"]
            if block["type"] == "section" and "fields" in block
        )
        commit = next(f["text"] for f in fields if f["text"].startswith("*Commit*"))

        assert "*star*" not in commit
        assert "~tilde~" not in commit

    def test_an_identifier_with_underscores_is_left_alone(self) -> None:
        n = notification(
            facts=(Fact("server", "Server", "web-1"), Fact("commit", "Commit", "feature_x_y"))
        )

        fields = slack.render(n)["attachments"][0]["blocks"][2]["fields"]

        assert fields[1]["text"] == "*Commit*\nfeature_x_y"

    @pytest.mark.parametrize(("locale", "name"), EVERYTHING)
    def test_every_platform_limit_holds(self, locale: str, name: str) -> None:
        blocks = slack.render(get(locale, name))["attachments"][0]["blocks"]

        assert len(blocks) <= 50
        for block in blocks:
            if block["type"] == "header":
                assert len(block["text"]["text"]) <= 150
            if block["type"] == "section":
                if "text" in block:
                    assert len(block["text"]["text"]) <= 3000
                assert len(block.get("fields", [])) <= 10
                assert all(len(f["text"]) <= 2000 for f in block.get("fields", []))
            if block["type"] == "context":
                assert len(block["elements"]) <= 10

    @settings(max_examples=100, deadline=None)
    @given(
        subject=st.text(max_size=400),
        branch=st.text(max_size=300),
        message=st.text(max_size=300),
        lines=st.lists(st.text(max_size=400), max_size=60),
    )
    def test_any_text_keeps_the_limits_and_the_link(
        self, subject: str, branch: str, message: str, lines: list[str]
    ) -> None:
        n = _deploy_with(subject or "x", branch, message, lines)

        blocks = slack.render(n)["attachments"][0]["blocks"]

        assert len(blocks) <= 50
        assert blocks[0]["type"] == "header" and len(blocks[0]["text"]["text"]) <= 150
        for block in blocks:
            if block["type"] == "section":
                assert len(block.get("text", {}).get("text", "")) <= 3000
                assert all(len(f["text"]) <= 2000 for f in block.get("fields", []))
        assert n.links[0].url in blocks[-1]["elements"][0]["text"]


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------


def embed_size(embed: dict) -> int:
    return (
        len(embed.get("title", ""))
        + len(embed.get("description", ""))
        + len(embed.get("footer", {}).get("text", ""))
        + sum(len(f["name"]) + len(f["value"]) for f in embed.get("fields", []))
    )


class TestDiscord:
    def test_one_embed_with_title_url_colour_footer_and_time(self) -> None:
        n = catalog("en")["deploy.rolled_back"]

        payload = discord.render(n)

        embed = payload["embeds"][0]
        assert len(payload["embeds"]) == 1
        assert embed["title"] == "▲ Rolled back · shop.example.com"
        assert embed["url"] == n.links[0].url
        assert embed["color"] == int(STATE_COLORS[State.WARNING].strip.lstrip("#"), 16)
        assert embed["footer"] == {"text": "Noust · web-1"}
        assert embed["timestamp"] == "2026-09-29T10:45:51+00:00"

    def test_the_title_is_the_link_so_the_url_is_not_written_again(self) -> None:
        payload = discord.render(catalog("en")["deploy.succeeded"])

        assert payload["embeds"][0]["url"] not in payload["embeds"][0]["description"]

    def test_facts_are_inline_fields_server_first(self) -> None:
        fields = discord.render(catalog("en")["deploy.succeeded"])["embeds"][0]["fields"]

        assert fields[0] == {"name": "Server", "value": "web-1", "inline": True}
        assert all(field["inline"] for field in fields)

    def test_no_mention_can_ping_anyone(self) -> None:
        payload = discord.render(hostile("en")["hostile.deploy_failed"])
        text = json.dumps(payload, ensure_ascii=False)

        assert payload["allowed_mentions"] == {"parse": []}
        assert "@everyone" not in text and "@here" not in text
        assert "@\u200beveryone" in text

    def test_a_user_mention_is_defused_in_prose_fields(self) -> None:
        fields = discord.render(hostile("en")["hostile.deploy_failed"])["embeds"][0]["fields"]
        commit = next(f["value"] for f in fields if f["name"] == "Commit")

        assert "<@U123>" not in commit
        assert "<\u200b@U123>" in commit

    def test_markdown_in_values_is_escaped(self) -> None:
        fields = discord.render(hostile("en")["hostile.deploy_failed"])["embeds"][0]["fields"]
        commit = next(f["value"] for f in fields if f["name"] == "Commit")

        assert "\\*star\\*" in commit
        assert "\\~tilde\\~" in commit
        assert "\\[a\\]" in commit

    def test_the_excerpt_is_a_code_block_that_cannot_be_closed_from_inside(self) -> None:
        description = discord.render(hostile("en")["hostile.deploy_failed"])["embeds"][0][
            "description"
        ]

        assert description.count("```") == 2

    @pytest.mark.parametrize(
        ("state", "flag"),
        [
            (State.OK, 4096),
            (State.PROGRESS, 4096),
            (State.INFO, 4096),
            (State.WARNING, None),
            (State.FAILED, None),
        ],
    )
    def test_quiet_states_suppress_the_notification(self, state: State, flag: int | None) -> None:
        payload = discord.render(notification(state=state))

        assert payload.get("flags") == flag

    def test_without_a_link_the_title_is_not_a_link(self) -> None:
        embed = discord.render(hostile("en")["hostile.no_link"])["embeds"][0]

        assert "url" not in embed

    @pytest.mark.parametrize(("locale", "name"), EVERYTHING)
    def test_every_platform_limit_holds(self, locale: str, name: str) -> None:
        embed = discord.render(get(locale, name))["embeds"][0]

        assert len(embed["title"]) <= 256
        assert len(embed["description"]) <= 4096
        assert len(embed["fields"]) <= 25
        assert all(len(f["name"]) <= 256 and len(f["value"]) <= 1024 for f in embed["fields"])
        assert len(embed["footer"]["text"]) <= 2048
        assert embed_size(embed) <= 6000

    @settings(max_examples=100, deadline=None)
    @given(
        subject=st.text(max_size=400),
        branch=st.text(max_size=300),
        message=st.text(max_size=300),
        lines=st.lists(st.text(max_size=400), max_size=60),
    )
    def test_any_text_keeps_the_limits_the_link_and_the_mentions(
        self, subject: str, branch: str, message: str, lines: list[str]
    ) -> None:
        n = _deploy_with(subject or "x", branch, message, lines)

        payload = discord.render(n)

        embed = payload["embeds"][0]
        assert embed_size(embed) <= 6000
        assert embed["url"] == n.links[0].url
        assert payload["allowed_mentions"] == {"parse": []}
        assert "@everyone" not in json.dumps(payload).replace("@\\u200beveryone", "")


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------

#: What version 1 promises: these keys, these types. Nothing here may be
#: removed or change type; keys may be added.
V1_KEYS = {
    "version": int,
    "id": str,
    "event": str,
    "code": str,
    "state": str,
    "locale": str,
    "title": str,
    "summary": str,
    "body": str,
    "domain": (str, type(None)),
    "server": str,
    "ts": str,
    "facts": list,
    "links": list,
    "excerpt": (dict, type(None)),
}
#: The keys the webhook had before 3.1. They keep their meaning.
LEGACY_KEYS = ("event", "title", "body", "domain", "ts")


class TestWebhook:
    @pytest.mark.parametrize(("locale", "name"), EVERYTHING)
    def test_the_contract_holds_for_every_event(self, locale: str, name: str) -> None:
        payload = webhook.render(get(locale, name))

        for key, kind in V1_KEYS.items():
            assert isinstance(payload[key], kind), key
        assert payload["version"] == 1
        json.dumps(payload)
        for fact in payload["facts"]:
            assert set(fact) == {"key", "label", "value"}
        for link in payload["links"]:
            assert set(link) == {"rel", "label", "url"}
        if payload["excerpt"] is not None:
            assert set(payload["excerpt"]) - {"pinned"} == {"label", "lines", "omitted"}

    def test_the_legacy_keys_keep_their_meaning(self) -> None:
        payload = webhook.render(catalog("en")["deploy.rolled_back"])

        assert payload["event"] == "deploy_rolled_back"
        assert payload["domain"] == "shop.example.com"
        assert payload["ts"] == "2026-09-29T10:45:51Z"
        assert payload["title"] == "Rolled back: shop.example.com"
        assert not payload["body"].startswith("▲")
        assert payload["body"].startswith(payload["summary"])

    def test_the_fine_code_and_state_are_data(self) -> None:
        payload = webhook.render(catalog("en")["update.succeeded"])

        assert (payload["event"], payload["code"], payload["state"]) == (
            "deploy_success",
            "update.succeeded",
            "ok",
        )

    def test_facts_carry_stable_keys_and_the_command(self) -> None:
        payload = webhook.render(catalog("en")["cert.expiring"])

        assert [fact["key"] for fact in payload["facts"]] == ["server", "covers", "renew"]
        assert payload["facts"][0] == {"key": "server", "label": "Server", "value": "web-1"}

    def test_the_link_is_data(self) -> None:
        payload = webhook.render(catalog("en")["deploy.succeeded"])

        assert payload["links"] == [
            {
                "rel": "console",
                "label": "Open in the console",
                "url": "https://console.example.com/n/web-1/apps/shop.example.com/deployments/42",
            }
        ]

    def test_the_excerpt_is_verbatim_lines(self) -> None:
        payload = webhook.render(catalog("en")["deploy.rolled_back"])

        assert payload["excerpt"]["lines"][0].startswith("Sep 29 10:45")
        assert payload["excerpt"]["omitted"] > 0

    def test_a_missing_domain_is_null(self) -> None:
        assert webhook.render(catalog("en")["test"])["domain"] is None

    def test_the_language_labels_are_the_readers(self) -> None:
        payload = webhook.render(catalog("es")["deploy.succeeded"])

        assert payload["locale"] == "es"
        assert payload["facts"][0]["label"] == "Servidor"

    def test_the_signature_is_hmac_sha256_of_the_exact_body(self) -> None:
        body = b'{"version": 1}'

        signature = webhook.sign(body, "s3cret-value")

        expected = hmac.new(b"s3cret-value", body, hashlib.sha256).hexdigest()
        assert signature == f"sha256={expected}"

    def test_headers_name_the_delivery_and_sign_it_only_with_a_secret(self) -> None:
        body = b"{}"
        n = catalog("en")["deploy.succeeded"]

        signed = webhook.headers(n, body, "s3cret-value")
        unsigned = webhook.headers(n, body, "")

        assert signed["X-Noust-Event"] == "deploy_success"
        assert signed["X-Noust-Delivery"] == n.id
        assert signed["X-Noust-Signature"] == webhook.sign(body, "s3cret-value")
        assert "X-Noust-Signature" not in unsigned
        assert unsigned["X-Noust-Delivery"] == n.id


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


class _Links(HTMLParser):
    """Every URL an email's HTML points at."""

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []
        self.images: list[str] = []
        self.tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        values = dict(attrs)
        if tag == "a":
            self.hrefs.append(values.get("href") or "")
        if tag == "img":
            self.images.append(values.get("src") or "")


def parsed(page: str) -> _Links:
    parser = _Links()
    parser.feed(page)
    return parser


class TestEmail:
    def test_the_subject_is_state_subject_and_server(self) -> None:
        rendered = email.render(catalog("en")["deploy.rolled_back"])

        assert rendered.subject == "[Noust] Rolled back: shop.example.com (web-1)"

    def test_a_subject_without_a_subject_line(self) -> None:
        rendered = email.render(notification(subject=""))

        assert rendered.subject == "[Noust] Deploy failed (web-1)"

    def test_the_subject_is_one_bounded_line(self) -> None:
        rendered = email.render(hostile("en")["hostile.huge_subject"])

        assert "\n" not in rendered.subject
        assert len(rendered.subject) <= 200

    def test_the_headers_mark_it_automatic_and_filterable(self) -> None:
        n = catalog("en")["deploy.rolled_back"]

        rendered = email.render(n)

        assert rendered.headers["Auto-Submitted"] == "auto-generated"
        assert rendered.headers["X-Auto-Response-Suppress"] == "All"
        assert rendered.headers["X-Noust-Event"] == "deploy_rolled_back"
        assert rendered.headers["X-Noust-Code"] == "deploy.rolled_back"
        assert rendered.headers["X-Noust-Server"] == "web-1"
        assert rendered.message_id == n.id

    def test_the_text_part_is_its_own_layout_with_the_url_and_a_footer(self) -> None:
        n = catalog("es")["deploy.rolled_back"]

        text = email.render(n).text

        assert text.startswith("▲ Revertido · shop.example.com\n")
        assert f"Abrir en la consola: {n.links[0].url}" in text
        assert "\n-- \nEnviado por Noust · web-1 · 2026-09-29 10:45 UTC\n" in text
        assert text.rstrip().endswith("Cambia lo que recibes en Ajustes > Notificaciones")
        assert text.count(n.links[0].url) == 1

    def test_the_html_carries_the_url_only_in_the_button(self) -> None:
        n = catalog("en")["deploy.rolled_back"]

        page = email.render(n).html

        assert parsed(page).hrefs == [n.links[0].url]
        assert html_text(page).count(n.links[0].url) == 0

    def test_the_wordmark_is_the_one_inline_image(self) -> None:
        rendered = email.render(catalog("en")["deploy.succeeded"])

        assert parsed(rendered.html).images == ["cid:noust-wordmark"]
        assert len(rendered.images) == 1
        image = rendered.images[0]
        assert (image.cid, image.subtype) == ("noust-wordmark", "png")
        assert image.data.startswith(b"\x89PNG\r\n\x1a\n")

    @pytest.mark.parametrize(("locale", "name"), EVERYTHING)
    def test_no_remote_images_ever(self, locale: str, name: str) -> None:
        page = email.render(get(locale, name)).html

        assert all(src.startswith("cid:") for src in parsed(page).images)

    def test_it_declares_and_handles_dark_mode(self) -> None:
        page = email.render(catalog("en")["deploy.succeeded"]).html

        assert '<meta name="color-scheme" content="light dark">' in page
        assert "@media (prefers-color-scheme: dark)" in page
        assert 'lang="en"' in page

    def test_the_essentials_are_inline_for_clients_that_drop_style_blocks(self) -> None:
        page = email.render(catalog("en")["deploy.succeeded"]).html

        assert re.search(r'<table[^>]*role="presentation"', page)
        assert "background-color:#ffffff" in page
        assert "color:#161616" in page

    def test_the_state_is_told_by_colour_glyph_and_word(self) -> None:
        page = email.render(catalog("en")["deploy.rolled_back"]).html

        assert STATE_COLORS[State.WARNING].light in page
        assert STATE_COLORS[State.WARNING].dark in page
        assert "▲&nbsp;Rolled back" in page

    def test_a_hidden_preheader_carries_the_summary(self) -> None:
        n = catalog("en")["deploy.rolled_back"]

        page = email.render(n).html

        assert re.search(r"display:none[^>]*>" + re.escape(n.summary), page)
        assert n.summary not in html_text(page).replace(n.summary, "", 1)

    def test_no_link_no_button(self) -> None:
        page = email.render(hostile("en")["hostile.no_link"]).html

        assert parsed(page).hrefs == []

    def test_a_link_that_is_not_http_is_not_a_button(self) -> None:
        n = notification(links=(Link("console", "Open", "javascript:alert(1)"),))

        assert parsed(email.render(n).html).hrefs == []

    def test_hostile_text_is_escaped_in_the_html(self) -> None:
        page = email.render(hostile("en")["hostile.deploy_failed"]).html

        assert "<b>x</b>" not in page
        assert "&lt;b&gt;x&lt;/b&gt;" in page
        assert "<!channel>" not in page
        assert "<urlopen error" not in page
        assert "javascript:" not in page

    def test_the_html_parses_to_the_facts_a_reader_sees(self) -> None:
        text = html_text(email.render(catalog("en")["deploy.succeeded"]).html)

        assert "Server" in text and "web-1" in text
        assert "a1b2c3d (main): Fix cart total" in text

    @pytest.mark.parametrize(("locale", "name"), EVERYTHING)
    def test_it_stays_well_under_gmails_clipping(self, locale: str, name: str) -> None:
        rendered = email.render(get(locale, name))

        assert len(rendered.html.encode()) < 60_000
        assert len(rendered.text.encode()) < 20_000

    def test_the_state_is_not_said_twice_when_there_is_no_subject(self) -> None:
        page = email.render(catalog("en")["report.observations"]).html

        assert page.count("Process observations") == 2  # the <title> and the one heading
        assert "<h1" in page and page.count("<h1") == 1

    def test_a_subject_gets_its_own_heading_below_the_state(self) -> None:
        page = email.render(catalog("en")["deploy.succeeded"]).html

        assert re.search(r'class="state"[^>]*>\u25cf&nbsp;Deployed</p>', page)
        assert re.search(r"<h1[^>]*>shop\.example\.com</h1>", page)

    def test_a_report_lists_its_sections(self) -> None:
        rendered = email.render(catalog("en")["report.observations"])

        assert "xmrig (PID 1234)" in html_text(rendered.html)
        assert "xmrig (PID 1234)" in rendered.text
        assert rendered.subject == "[Noust] Process observations (web-1)"


# ---------------------------------------------------------------------------
# Every channel
# ---------------------------------------------------------------------------

EMOJI_RANGE = re.compile("[\U0001f000-\U0001faff☀-⛿✅❌✔✖⭐️]")
ENGLISH_WORDS = frozenset(
    {"the", "did", "not", "is", "was", "and", "with", "from", "have", "been", "of", "failed"}
)


def visible(channel: str, n: Notification) -> str:
    """What a reader of one channel sees, as text."""
    if channel == "telegram":
        return telegram_text(telegram.render(n, CHAT_ID))
    if channel == "slack":
        return "\n".join(json_strings(slack.render(n)))
    if channel == "discord":
        return "\n".join(json_strings(discord.render(n)))
    if channel == "email-text":
        return email.render(n).text
    if channel == "email-html":
        return html_text(email.render(n).html)
    return webhook.render(n)["body"]


CHANNELS = ("telegram", "slack", "discord", "email-text", "email-html", "webhook")


#: Facts whose value Noust composed (the rest is verbatim from elsewhere).
NOUST_VALUES = frozenset({"used", "free", "duration", "downtime", "size", "trigger", "since"})


class TestEveryChannel:
    @pytest.mark.parametrize("channel", CHANNELS)
    @pytest.mark.parametrize(("locale", "name"), ALL)
    def test_no_emoji_anywhere(self, channel: str, locale: str, name: str) -> None:
        text = visible(channel, get(locale, name))

        assert EMOJI_RANGE.search(text.replace("▲", "").replace("✕", "")) is None

    @pytest.mark.parametrize("channel", [c for c in CHANNELS if c != "webhook"])
    @pytest.mark.parametrize("state", list(State))
    def test_the_state_glyph_leads(self, channel: str, state: State) -> None:
        text = visible(channel, notification(state=state))

        assert STATE_GLYPHS[state] in text

    @pytest.mark.parametrize(
        "channel", ["telegram", "slack", "discord", "email-text", "email-html"]
    )
    @pytest.mark.parametrize(("locale", "name"), ALL)
    def test_the_summary_is_said_once(self, channel: str, locale: str, name: str) -> None:
        n = get(locale, name)

        assert visible(channel, n).count(n.summary) == 1

    @pytest.mark.parametrize(("locale", "name"), ALL)
    def test_the_console_link_is_written_once_per_channel(self, locale: str, name: str) -> None:
        n = get(locale, name)
        if n.console_link is None:
            pytest.skip("no link")
        url = n.console_link.url

        assert json.dumps(telegram.render(n, CHAT_ID)).count(url) == 1
        assert json.dumps(slack.render(n)).count(url) == 1
        assert json.dumps(discord.render(n)).count(url) == 1
        assert email.render(n).text.count(url) == 1
        assert email.render(n).html.count(url) == 1

    @pytest.mark.parametrize(("locale", "name"), ALL)
    def test_the_summary_repeats_neither_the_title_nor_a_fact(self, locale: str, name: str) -> None:
        """Design rule D-2, over the whole catalog."""
        n = get(locale, name)

        assert normalize(n.title) not in normalize(n.summary)
        for fact in (*n.facts, *([n.command] if n.command else [])):
            if len(fact.value) > 6:
                assert fact.value not in n.summary, fact.key

    @pytest.mark.parametrize(("locale", "name"), ALL)
    def test_the_server_is_named_once_in_the_body(self, locale: str, name: str) -> None:
        n = get(locale, name)

        assert [fact.key for fact in n.facts].count("server") == 1
        assert n.facts[0].key == "server"

    @pytest.mark.parametrize(("locale", "name"), ALL)
    def test_no_line_is_said_twice_outside_the_excerpt(self, locale: str, name: str) -> None:
        n = get(locale, name)
        excerpt_lines = set(n.excerpt.lines) if n.excerpt else set()
        lines = [
            line.strip()
            for line in render_text(n).splitlines()
            if line.strip() and line.strip() not in excerpt_lines
        ]
        seen: dict[str, int] = {}
        for line in lines:
            key = normalize(line)
            if len(key) > 3:
                seen[key] = seen.get(key, 0) + 1

        assert [line for line, count in seen.items() if count > 1] == []

    @pytest.mark.parametrize(("locale", "name"), ALL)
    def test_the_excerpt_repeats_nothing_noust_said(self, locale: str, name: str) -> None:
        n = get(locale, name)
        if n.excerpt is None:
            pytest.skip("no excerpt")
        said = {normalize(n.summary), normalize(n.headline)}
        said |= {normalize(fact.value) for fact in n.facts}
        if n.command:
            said.add(normalize(n.command.value))

        assert [line for line in n.excerpt.lines if normalize(line) in said] == []

    def test_spanish_notifications_keep_no_english_word_of_noust(self) -> None:
        leaks = []
        for name, n in catalog("es").items():
            words = [n.title, n.summary, *(fact.label for fact in n.facts)]
            words += [fact.value for fact in n.facts if fact.key in NOUST_VALUES]
            if n.command:
                words.append(n.command.label)
            if n.links:
                words.append(n.links[0].label)
            for text in words:
                found = set(re.findall(r"[a-zA-Z']+", text.lower())) & ENGLISH_WORDS
                if found:
                    leaks.append((name, text, sorted(found)))

        assert leaks == []

    @pytest.mark.parametrize("channel", CHANNELS)
    @pytest.mark.parametrize(("locale", "name"), ALL)
    def test_the_title_carries_no_fact(self, channel: str, locale: str, name: str) -> None:
        n = get(locale, name)

        for fact in n.facts[1:]:
            if len(fact.value) > 6 and fact.key not in ("covers",):
                assert fact.value not in n.title, (fact.key, fact.value)

    def test_the_wordmark_png_is_a_real_image_that_fits_an_email(self) -> None:
        image = email.render(catalog("en")["deploy.succeeded"]).images[0]

        assert image.data.startswith(b"\x89PNG")
        assert len(image.data) < 20_000

    def test_http_url_refuses_what_is_not_a_web_address(self) -> None:
        assert http_url("https://a.example/x") == "https://a.example/x"
        assert http_url("http://a.example") == "http://a.example"
        assert http_url("javascript:alert(1)") is None
        assert http_url("https://a.example/a b") is None
        assert http_url("https://") is None
        assert http_url("ftp://a.example") is None

    def test_html_unescape_round_trip_of_a_headline(self) -> None:
        n = notification(subject="a&b<c>d")

        text = telegram_text(telegram.render(n, CHAT_ID))

        assert html_lib.unescape(text).splitlines()[0].endswith("a&b<c>d")


# ---------------------------------------------------------------------------
# The first error, brought up above the end of the output
# ---------------------------------------------------------------------------

PINNED_ERROR = "Sep 30 09:14:02 web-1 node[73120]: Error: broken on purpose"
PINNED = Excerpt(
    "Last lines of the journal of shop-example-com",
    (
        PINNED_ERROR,
        *(
            f"Sep 30 09:14:07 web-1 systemd[1]: shop-example-com.service: line {n}"
            for n in range(11)
        ),
    ),
    omitted=10,
    pinned=1,
)


class TestPinnedError:
    @pytest.mark.parametrize("channel", CHANNELS)
    def test_every_channel_shows_the_error_first_and_marks_it(self, channel: str) -> None:
        text = visible(channel, notification(excerpt=PINNED))

        error = text.find("node[73120]: Error: broken on purpose")
        marker = text.find("First error above; the last lines follow")
        tail = text.find("shop-example-com.service: line 10")
        assert -1 < error < marker < tail

    def test_telegram_keeps_it_when_the_tail_gives_way(self) -> None:
        text = telegram.render(notification(excerpt=PINNED), CHAT_ID)["text"]

        pre = re.search(r"<pre>(.*)</pre>", text, re.S)
        assert pre is not None
        body = pre.group(1).splitlines()
        assert body[0] == "node[73120]: Error: broken on purpose"
        assert body[1] == "… First error above; the last lines follow"
        assert body[-2] == "systemd[1]: shop-example-com.service: line 10"
        assert len(body) <= 10  # eight lines and the two markers

    def test_the_marker_is_in_the_readers_language(self) -> None:
        text = visible("telegram", notification(excerpt=PINNED, locale="es"))

        assert "Primer error arriba; siguen las últimas líneas" in text

    def test_nothing_is_marked_when_only_the_error_is_left(self) -> None:
        n = notification(excerpt=Excerpt("x", (PINNED_ERROR,), omitted=3, pinned=1))

        assert "First error above" not in visible("email-text", n)

    def test_the_webhook_says_how_many_lines_are_pinned(self) -> None:
        payload = webhook.render(notification(excerpt=PINNED))

        assert payload["excerpt"]["pinned"] == 1
        assert payload["excerpt"]["lines"][0] == PINNED_ERROR
        assert "pinned" not in webhook.render(catalog("en")["deploy.rolled_back"])["excerpt"]
