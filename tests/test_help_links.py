# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The console's links to the documentation lead somewhere (checklist N-18).

``panel/src/app/help-links.json`` says which page of the documentation explains each page of the
console. A link nobody checks is a link that rots the day a heading is renamed, so every entry is
resolved here against the documentation's own manifest and the headings of the pages it names: the
file must be a page of the site, the slug the one the site gives it, and the anchor a real heading.
(``panel/src/app/help.test.ts`` checks the other side: that every page of the console has an entry.)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import build_docs

ROOT = Path(__file__).resolve().parent.parent
TABLE = json.loads((ROOT / "panel/src/app/help-links.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def pages() -> dict[str, build_docs.Page]:
    return build_docs.load().by_source()


def entries() -> list[dict[str, str]]:
    return [TABLE["default"], *TABLE["pages"]]


@pytest.mark.parametrize("entry", entries(), ids=lambda e: f"{e.get('route', 'default')}")
def test_an_entry_names_a_page_of_the_site_and_a_heading_of_it(
    entry: dict[str, str], pages: dict[str, build_docs.Page]
) -> None:
    page = pages.get(entry["source"])

    assert page is not None, (
        f"{entry['source']} is not a page of the documentation (docs/site.yaml)"
    )
    assert page.slug == entry["slug"], (
        f"{entry['source']} is the route /{page.slug}/, not /{entry['slug']}/"
    )
    if "anchor" in entry:
        assert entry["anchor"] in page.anchors, (
            f"{entry['source']} has no heading #{entry['anchor']}; it has {sorted(page.anchors)[:12]}..."
        )


def test_no_route_is_listed_twice() -> None:
    routes = [e["route"] for e in TABLE["pages"]]

    assert len(routes) == len(set(routes))


def test_a_route_is_a_path_of_the_console() -> None:
    for entry in TABLE["pages"]:
        route = entry["route"]
        assert route.startswith("/"), route
        assert not route.endswith("/") or route == "/", route
        assert " " not in route


def test_the_default_is_the_documentation_of_the_console_itself() -> None:
    assert TABLE["default"] == {"slug": "console", "source": "docs/console.md"}
