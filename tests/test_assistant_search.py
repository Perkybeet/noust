# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Looking things up without a vector database: a ranking over words, in plain Python (item 171).

The assistant is given the operations of the API to find what the person wants to do (a thousand and
more would not fit in a request, and most are not for it), and the documentation to cite. Both are a
BM25 ranking over the words of each entry.
"""

from __future__ import annotations

from pathlib import Path

from noust.core.assistant.bm25 import Index, tokens
from noust.core.assistant.docsearch import DocIndex, split_markdown
from noust.core.assistant.opsearch import OperationIndex
from noust.core.operation_classes import NEUTRAL_PERMISSIONS, classify

OPENAPI = {
    "paths": {
        "/api/apps/{domain}/restart": {
            "post": {
                "summary": "Restart an application",
                "description": "Restart the unit and wait for the health check.",
                "tags": ["Apps"],
                "x-noust-permission": "apps.operate",
            }
        },
        "/api/apps/{domain}/limits": {
            "put": {
                "summary": "Set an application's resource limits",
                "tags": ["Apps"],
                "x-noust-permission": "apps.manage",
                "x-noust-requires-elevation": True,
            }
        },
        "/api/certs/{domain}/renew": {
            "post": {
                "summary": "Renew the certificate of a domain",
                "tags": ["Certificates"],
                "x-noust-permission": "apps.operate",
            }
        },
        "/api/apps/{domain}/logs": {
            "get": {
                "summary": "Read an application's journal",
                "tags": ["Apps"],
                "x-noust-permission": "apps.read",
            }
        },
        "/api/audit": {
            "get": {"summary": "Read the audit trail", "x-noust-permission": "audit.read"}
        },
        "/api/auth/tokens": {"get": {"summary": "List my tokens", "x-noust-permission": "self"}},
    }
}


def classes() -> dict[tuple[str, str], object]:
    return {
        (method.upper(), path): classify(method, path, operation)
        for path, item in OPENAPI["paths"].items()
        for method, operation in item.items()
    }


class TestTheRanking:
    def test_words_are_lowered_split_and_stemmed(self) -> None:
        assert tokens("Restarting the Applications' /api/apps/{domain}") == [
            "restart",
            "application",
            "api",
            "app",
            "domain",
        ]

    def test_the_entry_with_the_words_of_the_question_comes_first(self) -> None:
        index = Index(
            [
                ("a", "restart the application and wait for the health check"),
                ("b", "renew the certificate of a domain"),
                ("c", "list backups"),
            ]
        )
        assert [name for name, _score in index.search("restart my application")][:1] == ["a"]
        assert index.search("zzz") == []

    def test_a_filter_is_applied_before_the_limit(self) -> None:
        index = Index([("a", "restart"), ("b", "restart restart"), ("c", "restart")])
        assert [n for n, _ in index.search("restart", 2, allow=lambda name: name != "b")] == [
            "a",
            "c",
        ]


class TestFindingAnOperation:
    def build(self) -> OperationIndex:
        return OperationIndex(OPENAPI, classes())

    def test_the_operation_that_does_what_was_said(self) -> None:
        hits = self.build().search("restart an application")
        assert (hits[0].method, hits[0].path) == ("POST", "/api/apps/{domain}/restart")
        assert hits[0].permission == "apps.operate"
        assert hits[0].summary == "Restart an application"

    def test_what_asks_for_sudo_mode_says_so(self) -> None:
        hit = self.build().search("resource limits")[0]
        assert (hit.path, hit.elevation, hit.strong) == ("/api/apps/{domain}/limits", True, True)

    def test_what_is_not_offered_is_not_found(self) -> None:
        index = self.build()

        def offered(found) -> bool:
            return found.permission not in NEUTRAL_PERMISSIONS and found.permission != "audit.read"

        assert index.search("audit trail", allow=offered) == []
        assert index.search("my tokens", allow=offered) == []

    def test_an_operation_is_looked_up_by_its_method_and_path(self) -> None:
        index = self.build()
        assert index.get("post", "/api/certs/{domain}/renew") is not None
        assert index.get("DELETE", "/api/certs/{domain}/renew") is None


class TestTheDocumentation:
    PAGE = (
        "# The built-in assistant\n\nIntro text about the assistant.\n\n"
        "## Limits\n\nSteps in one answer are twenty by default. The month limit is a number.\n\n"
        "### Turning it off\n\nNoust assistant disable revokes every token at once.\n\n"
        "```\n# not a heading\n```\n"
    )

    def test_a_page_is_cut_at_its_headings_and_each_piece_knows_its_title(self) -> None:
        pieces = split_markdown("assistant.md", self.PAGE)
        assert [p.title for p in pieces] == [
            "The built-in assistant",
            "The built-in assistant > Limits",
            "The built-in assistant > Limits > Turning it off",
        ]
        assert pieces[1].page == "assistant.md"
        assert "twenty" in pieces[1].text
        assert "not a heading" in pieces[2].text

    def test_a_search_gives_the_piece_to_cite_and_its_page(self, tmp_path: Path) -> None:
        (tmp_path / "assistant.md").write_text(self.PAGE)
        (tmp_path / "other.md").write_text("# Backups\n\nA backup is a copy of an application.\n")
        (tmp_path / "CHANGELOG-4.2.md").write_text("# Changes\n\nassistant disable revokes.\n")
        docs = DocIndex([tmp_path])
        assert docs.available
        hits = docs.search("how do I turn the assistant off and revoke tokens")
        assert hits[0].page == "assistant.md"
        assert hits[0].title.endswith("Turning it off")
        # A page of changes is history, not documentation to cite.
        assert all(h.page != "CHANGELOG-4.2.md" for h in hits)

    def test_with_no_documentation_installed_there_is_nothing_to_offer(
        self, tmp_path: Path
    ) -> None:
        docs = DocIndex([tmp_path / "nowhere"])
        assert docs.available is False
        assert docs.search("anything") == []


def test_the_plan_the_assistant_reads_is_the_one_each_operation_really_publishes() -> None:
    # The pair, end to end: what the API's own document says of every operation that has a plan
    # (the one registry of them) is what a proposal is given to show before a person approves.
    import json
    from pathlib import Path

    from noust.core.assistant.opsearch import plan_of
    from noust.core.plans.registry import PLANNED

    document = json.loads(
        (Path(__file__).resolve().parent.parent / "panel" / "openapi.json").read_text("utf-8")
    )
    assert PLANNED
    for planned in PLANNED:
        operation = document["paths"][planned.path][planned.method.lower()]
        assert plan_of(operation) == f"{planned.plan_method} {planned.plan_path}", planned.key
