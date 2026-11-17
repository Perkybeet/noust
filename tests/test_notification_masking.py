# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a notification says of another program's output never carries a credential out of the server.

A notice goes to places Noust does not control (an email thread, a Telegram topic, a webhook of
somebody else's) and carries the words of the tools that failed: certbot, a cleanup, the undoing of a
change of access. Those words can hold a token the tool echoed, a header, an address with its user, a
key. Every text field of a notification is cleaned in one place when it is made
(``noust.core.notifications.model``), and what it looks like to a person is hidden there, so every
channel, every composer and every stored notification reads the same masked text.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from noust.core.notifications.model import Excerpt, Fact, Notification, Section, State
from noust.core.notifications.render import email, telegram, webhook

#: What each line of a program's output could echo, with the secret of it, and none of them is a real
#: credential: the tokens are invented, and have no provider's full shape.
TOKEN = "ghp_" + "a1B2c3D4e5F6" * 3
PAT = "pq7rb3kw2hvxn5jsd6c4mfzg2u5yvtlaeo3idxqwk5jb2n4rt7la"
SECRETS = {
    "a token with a provider's prefix": (f"using {TOKEN} to push", TOKEN),
    "a bearer header": ("Authorization: Bearer abcdefghij0123456789", "abcdefghij0123456789"),
    "an assigned password": ("dns plugin: password=hunter2222 refused", "hunter2222"),
    "a token in the user of an address": (f"cloning https://{PAT}@dev.azure.com/o/p/_git/a", PAT),
    "a password in an address": (
        "pulling https://ana:s3cr3tvalue@git.example.test/a",
        "s3cr3tvalue",
    ),
    "an api key header": ("X-Api-Key: 0123456789abcdef", "0123456789abcdef"),
}


def make(**changes: Any) -> Notification:
    base: dict[str, Any] = {
        "kind": "cert_expiring",
        "code": "cert.renewal_failed",
        "state": State.FAILED,
        "locale": "en",
        "title": "Renewal failed",
        "subject": "shop.example.com",
        "summary": "certbot could not renew the certificate",
        "server": "web-1",
    }
    base.update(changes)
    return Notification(**base)


def everything_said(n: Notification) -> str:
    """What the three kinds of channel say of a notification, as one text."""
    sent = [
        json.dumps(webhook.render(n)),
        email.render(n).html,
        json.dumps(telegram.render(n, "123")),
        json.dumps(telegram.render(n, "123", plain=True)),
        json.dumps(n.to_dict()),
    ]
    return "\n".join(sent)


class TestTheExcerptIsMasked:
    @pytest.mark.parametrize("name", sorted(SECRETS))
    def test_a_line_of_a_program_is_hidden_in_every_channel(self, name: str) -> None:
        line, secret = SECRETS[name]
        n = make(excerpt=Excerpt("certbot said", ("one line before", line, "one line after")))

        assert secret not in "\n".join(n.excerpt.lines if n.excerpt else ())
        assert secret not in everything_said(n)

    def test_the_rest_of_the_line_and_the_other_lines_stay_as_they_were(self) -> None:
        n = make(
            excerpt=Excerpt(
                "certbot said",
                ("Saving debug log", "dns plugin: password=hunter2222 refused", "done"),
            )
        )

        assert n.excerpt is not None
        assert n.excerpt.lines == ("Saving debug log", "dns plugin: password=*** refused", "done")

    def test_a_key_of_several_lines_goes_whole_and_the_counts_follow(self) -> None:
        body = "MIIEvAIBADANBgkqhkiG9w0BAQEFAASCBKYwggSiAgEAAoIBAQDexample"
        n = make(
            excerpt=Excerpt(
                "certbot said",
                (
                    "before",
                    "-----BEGIN PRIVATE KEY-----",
                    body,
                    body,
                    "-----END PRIVATE KEY-----",
                    "after",
                ),
                omitted=3,
                pinned=5,
            )
        )

        assert n.excerpt is not None
        assert body not in everything_said(n)
        assert n.excerpt.lines == ("before", "***", "after")
        assert n.excerpt.omitted == 3
        assert 0 <= n.excerpt.pinned <= len(n.excerpt.lines)

    def test_a_secret_cannot_be_hidden_from_the_masking_by_the_marks_around_it(self) -> None:
        """Terminal escapes and invisible characters are taken out first, so they split nothing."""
        split = TOKEN[:8] + "​" + TOKEN[8:]
        coloured = "\x1b[31m" + TOKEN[:10] + "\x1b[0m" + TOKEN[10:]
        n = make(excerpt=Excerpt("x", (f"a {split} b", f"c {coloured} d")))

        assert n.excerpt is not None
        assert n.excerpt.lines == ("a *** b", "c *** d")

    def test_an_excerpt_with_no_line_is_still_none_of_a_blank_line(self) -> None:
        assert Excerpt("x", ()).lines == ()


class TestEveryOtherTextOfTheNoticeIsMasked:
    def test_the_facts_the_subject_and_the_summary(self) -> None:
        n = make(
            subject=f"https://{PAT}@git.example.test/a.git",
            summary="refused with password=hunter2222",
            facts=(Fact("source", "Source", f"https://{PAT}@git.example.test/a.git"),),
            command=Fact("command", "Run", f"noust env set TOKEN={TOKEN}", mono=True),
            sections=(
                Section(
                    "report", (Fact("k", "Key", "Authorization: Bearer abcdefghij0123456789"),)
                ),
            ),
        )

        said = everything_said(n)

        for secret in (PAT, "hunter2222", TOKEN, "abcdefghij0123456789"):
            assert secret not in said

    def test_what_is_not_a_secret_is_not_touched(self) -> None:
        n = make(
            facts=(
                Fact("commit", "Commit", "3f2a9c1e8b7d6a5f4e3d2c1b0a9f8e7d6c5b4a39", mono=True),
                Fact("path", "Folder", "/var/www/apps/shop-example-com/current"),
                Fact("url", "Address", "https://shop.example.com:8443/health"),
                Fact("mail", "Contact", "write to ops@example.com"),
            ),
            excerpt=Excerpt(
                "x", ("git@github.com:acme/shop.git", "GET /api/auth/tokens?limit=10 200")
            ),
        )

        assert [fact.value for fact in n.facts] == [
            "3f2a9c1e8b7d6a5f4e3d2c1b0a9f8e7d6c5b4a39",
            "/var/www/apps/shop-example-com/current",
            "https://shop.example.com:8443/health",
            "write to ops@example.com",
        ]
        assert n.excerpt is not None
        assert n.excerpt.lines == (
            "git@github.com:acme/shop.git",
            "GET /api/auth/tokens?limit=10 200",
        )

    def test_a_stored_notification_comes_back_as_it_went_out_and_masking_twice_changes_nothing(
        self,
    ) -> None:
        n = make(excerpt=Excerpt("x", (f"cloning https://{PAT}@host.example.test/a",)))

        again = Notification.from_dict(n.to_dict())

        assert again.excerpt == n.excerpt
        assert again.to_dict() == n.to_dict()
