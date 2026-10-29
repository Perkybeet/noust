# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The documentation (checklist N-17, N-22): its manifest, its preview and the package a site paints.

Two kinds of test. The first builds a small repository made for the purpose and pins how the
builder reads the manifest, resolves a link, an anchor, an image and a redirect, cuts a long
reference into pages and what it writes. The second builds the real documentation and holds it to
the bar CI holds it to: no link or anchor that leads nowhere, no Markdown file nobody listed, every
page with a type and a sentence, and the generated references current.
"""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

import pytest

from scripts import build_docs, generate_reference

ROOT = Path(__file__).resolve().parent.parent

MANIFEST = """\
title: Test docs
repository: https://github.com/example/project
branch: main
base: /docs/
summary: A project, for a test.
home:
  start:
    slug: start/overview
tabs:
  - title: Start
    slug: start
    summary: Where to begin.
    groups:
      - title: First
        pages:
          - source: README.md
            slug: start/overview
            type: explanation
            summary: The overview.
          - source: docs/guide.md
            slug: start/guide
            title: The guide
            type: tutorial
            summary: How to.
  - title: More
    slug: more
    groups:
      - title: Extra
        pages:
          - source: docs/extra.md
            slug: more/extra
            type: reference
exclude:
  - source: docs/private.md
    why: not for readers
redirects:
  old-guide: start/guide
"""

#: A manifest with a reference published one page per second-level heading.
SPLIT_MANIFEST = """\
title: Test docs
repository: https://github.com/example/project
branch: main
summary: A project, for a test.
tabs:
  - title: Reference
    slug: reference
    groups:
      - title: Lists
        pages:
          - source: docs/guide.md
            slug: reference/guide
            type: guide
            summary: How to.
          - source: docs/commands.md
            slug: reference/cli
            type: reference
            summary: Every command.
            split: 2
            split_prefix: tool-
exclude:
  - source: docs/extra.md
    why: not in this test
  - source: docs/private.md
    why: not for readers
"""

COMMANDS = """\
# Commands

Every command: [`run`](#tool-run), [`stop`](#tool-stop).

## tool run

Run the thing. It starts at once.

### Notes

See [stop](#tool-stop) and [the guide](guide.md#first-steps).

## tool stop

Stop the thing.

### Notes

Nothing else.
"""


def project(tmp_path: Path, files: dict[str, str], manifest: str = MANIFEST) -> Path:
    """Write a small repository and return its root."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "3.4.1"\n')
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "docs" / "site.yaml").write_text(manifest)
    defaults = {
        "README.md": "# Overview\n\nSee [the guide](docs/guide.md).\n",
        "docs/guide.md": "# Guide\n\n## First steps\n\nText.\n",
        "docs/extra.md": "# Extra\n",
        "docs/private.md": "# Private\n",
    }
    for name, content in {**defaults, **files}.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content))
    return tmp_path


def load(root: Path) -> build_docs.Site:
    return build_docs.load(root / "docs" / "site.yaml", root)


def render(root: Path, slug: str) -> str:
    """Render one page of a loaded project and return its body."""
    site = load(root)
    page = next(p for p in site.pages if p.slug == slug)
    body = build_docs.render_page(site, page, build_docs.parser(), root)
    assert not site.problems, [str(p) for p in site.problems]
    return body


def problems_of(root: Path) -> list[str]:
    """Build the preview and the package of a project and return every problem."""
    site = build_docs.build_site(root / "out", "3.4", root, root / "package", {})
    return [str(p) for p in site.problems]


# -- headings


@pytest.mark.parametrize(
    ("text", "anchor"),
    [
        ("Deploying", "deploying"),
        ("noust.yaml", "noustyaml"),
        ("What Noust is, and is not", "what-noust-is-and-is-not"),
        ("On a name of its own (guided)", "on-a-name-of-its-own-guided"),
        ("Rolling back past a migration", "rolling-back-past-a-migration"),
        ("`noust app adopt`", "noust-app-adopt"),
        ("API v1 / v2", "api-v1--v2"),
    ],
)
def test_headings_get_the_anchor_github_gives_them(text: str, anchor: str) -> None:
    assert build_docs.github_slug(text) == anchor


def test_a_repeated_heading_is_numbered_like_github_numbers_it(tmp_path: Path) -> None:
    root = project(tmp_path, {"docs/guide.md": "# Guide\n\n## Notes\n\n## Notes\n\n## Notes\n"})

    page = next(p for p in load(root).pages if p.slug == "start/guide")

    assert [anchor for _l, anchor, _t in page.headings] == ["guide", "notes", "notes-1", "notes-2"]


def test_inline_code_and_emphasis_in_a_heading_do_not_break_its_anchor(tmp_path: Path) -> None:
    root = project(tmp_path, {"docs/guide.md": "# Guide\n\n## The `build:` *block*\n"})

    page = next(p for p in load(root).pages if p.slug == "start/guide")

    assert "the-build-block" in page.anchors


# -- links in the preview


def test_a_link_to_another_page_becomes_a_relative_route(tmp_path: Path) -> None:
    body = render(project(tmp_path, {}), "start/overview")

    assert 'href="../../start/guide/"' in body


def test_a_link_with_an_anchor_keeps_it(tmp_path: Path) -> None:
    root = project(tmp_path, {"README.md": "# O\n\n[steps](docs/guide.md#first-steps)\n"})

    assert 'href="../../start/guide/#first-steps"' in render(root, "start/overview")


def test_a_link_inside_docs_is_resolved_from_the_file_that_holds_it(tmp_path: Path) -> None:
    root = project(tmp_path, {"docs/guide.md": "# G\n\n[extra](extra.md) [home](../README.md)\n"})

    body = render(root, "start/guide")

    assert 'href="../../more/extra/"' in body
    assert 'href="../../start/overview/"' in body


def test_a_link_to_a_file_that_is_not_a_page_goes_to_github(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {"README.md": "# O\n\n[the unit](src/x.py#L3) [dir](src)\n", "src/x.py": "x = 1\n"},
    )

    body = render(root, "start/overview")

    assert 'href="https://github.com/example/project/blob/main/src/x.py#L3"' in body
    assert 'href="https://github.com/example/project/tree/main/src"' in body


def test_an_external_address_and_a_mail_link_are_left_alone(tmp_path: Path) -> None:
    root = project(tmp_path, {"README.md": "# O\n\n[a](https://example.org/x) [b](mailto:a@b.c)\n"})

    body = render(root, "start/overview")

    assert 'href="https://example.org/x"' in body
    assert 'href="mailto:a@b.c"' in body


def test_a_link_to_nothing_is_a_problem_naming_the_file_and_the_link(tmp_path: Path) -> None:
    root = project(tmp_path, {"README.md": "# O\n\n[gone](docs/gone.md)\n"})
    site = load(root)

    build_docs.render_page(site, site.pages[0], build_docs.parser(), root)

    assert [str(p) for p in site.problems] == ["README.md: docs/gone.md does not exist"]


def test_an_anchor_the_target_does_not_have_is_a_problem(tmp_path: Path) -> None:
    root = project(tmp_path, {"README.md": "# O\n\n[x](docs/guide.md#no-such)\n"})
    site = load(root)

    build_docs.render_page(site, site.pages[0], build_docs.parser(), root)

    assert any("has no heading #no-such" in str(p) for p in site.problems)


def test_an_anchor_on_the_same_page_is_checked_too(tmp_path: Path) -> None:
    root = project(tmp_path, {"docs/guide.md": "# G\n\n## Real\n\n[ok](#real) [bad](#fake)\n"})
    site = load(root)
    page = next(p for p in site.pages if p.slug == "start/guide")

    build_docs.render_page(site, page, build_docs.parser(), root)

    assert [str(p) for p in site.problems] == ["docs/guide.md: #fake is not a heading of this page"]


def test_a_link_that_leaves_the_repository_is_a_problem(tmp_path: Path) -> None:
    root = project(tmp_path, {"docs/guide.md": "# G\n\n[x](../../etc/passwd)\n"})
    site = load(root)
    page = next(p for p in site.pages if p.slug == "start/guide")

    build_docs.render_page(site, page, build_docs.parser(), root)

    assert any("leaves the repository" in str(p) for p in site.problems)


def test_an_absolute_path_is_a_problem_rather_than_a_file_of_the_machine(tmp_path: Path) -> None:
    root = project(tmp_path, {"docs/guide.md": "# G\n\n[x](/etc/hostname)\n"})

    assert any("is an absolute path" in p for p in problems_of(root))


def test_rendering_twice_resolves_a_link_once(tmp_path: Path) -> None:
    root = project(tmp_path, {})
    site = load(root)
    md = build_docs.parser()

    first = build_docs.render_page(site, site.pages[0], md, root)
    second = build_docs.render_page(site, site.pages[0], md, root)

    assert first == second
    assert not site.problems


def test_images_are_copied_and_their_address_rewritten(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {"README.md": "# O\n\n![shot](docs/assets/shot.png)\n", "docs/assets/shot.png": "PNG"},
    )
    out = tmp_path / "out"
    site = build_docs.build_site(out, "3.4", root)

    assert not site.problems
    page = (out / "3.4" / "start" / "overview" / "index.html").read_text()
    assert 'src="../../assets/docs/assets/shot.png"' in page
    assert (out / "3.4" / "assets" / "docs" / "assets" / "shot.png").read_text() == "PNG"


def test_raw_html_in_a_page_has_its_addresses_resolved_too(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {
            "README.md": '# O\n\n<p align="center"><img src="docs/assets/logo.png" width="80"></p>\n',
            "docs/assets/logo.png": "PNG",
        },
    )

    body = render(root, "start/overview")

    assert 'src="../../assets/docs/assets/logo.png"' in body


# -- the manifest


def test_a_markdown_file_nobody_listed_is_a_problem(tmp_path: Path) -> None:
    root = project(tmp_path, {"docs/forgotten.md": "# Forgotten\n"})

    messages = [str(p) for p in load(root).problems]

    assert any(m.startswith("docs/forgotten.md: is not in docs/site.yaml") for m in messages)


def test_an_excluded_file_is_not_a_problem(tmp_path: Path) -> None:
    assert load(project(tmp_path, {})).problems == []


def test_a_page_listed_but_missing_is_a_problem(tmp_path: Path) -> None:
    root = project(tmp_path, {})
    (root / "docs" / "extra.md").unlink()

    assert any("docs/extra.md does not exist" in str(p) for p in load(root).problems)


def test_two_pages_cannot_share_a_route(tmp_path: Path) -> None:
    manifest = MANIFEST.replace(
        "slug: start/overview\n            type", "slug: start/guide\n            type"
    )

    problems = [str(p) for p in load(project(tmp_path, {}, manifest)).problems]

    assert any("slug 'start/guide' is used by" in m for m in problems)


def test_a_route_lives_under_its_tab(tmp_path: Path) -> None:
    manifest = MANIFEST.replace("slug: more/extra", "slug: start/extra")

    problems = [str(p) for p in load(project(tmp_path, {}, manifest)).problems]

    assert any("the route start/extra is not under its tab more/" in m for m in problems)


@pytest.mark.parametrize(
    "slug", ["More/Extra", "more//extra", "more/extra/", "more/ex tra", "../x"]
)
def test_a_route_is_lower_case_segments(tmp_path: Path, slug: str) -> None:
    manifest = MANIFEST.replace("slug: more/extra", f"slug: '{slug}'")

    problems = [str(p) for p in load(project(tmp_path, {}, manifest)).problems]

    assert any("is not a route" in m for m in problems), problems


def test_every_page_says_what_kind_of_page_it_is(tmp_path: Path) -> None:
    manifest = MANIFEST.replace("            type: reference\n", "")

    problems = [str(p) for p in load(project(tmp_path, {}, manifest)).problems]

    assert any("docs/extra.md: type '' is not one of tutorial, guide" in m for m in problems)


def test_a_redirect_to_a_page_that_is_gone_is_a_problem(tmp_path: Path) -> None:
    manifest = MANIFEST.replace("old-guide: start/guide", "old-guide: vanished")

    problems = [str(p) for p in load(project(tmp_path, {}, manifest)).problems]

    assert any("redirect old-guide leads to vanished" in m for m in problems)


def test_a_redirect_from_a_route_still_published_is_a_problem(tmp_path: Path) -> None:
    manifest = MANIFEST.replace("old-guide: start/guide", "more/extra: start/guide")

    problems = [str(p) for p in load(project(tmp_path, {}, manifest)).problems]

    assert any("hides the page published at that route" in m for m in problems)


def test_the_landing_page_names_only_routes_that_exist(tmp_path: Path) -> None:
    manifest = MANIFEST.replace("    slug: start/overview\ntabs", "    slug: start/nowhere\ntabs")

    problems = [str(p) for p in load(project(tmp_path, {}, manifest)).problems]

    assert any("home links to start/nowhere" in m for m in problems)


def test_the_title_is_the_manifests_or_the_first_heading(tmp_path: Path) -> None:
    pages = {p.slug: p.title for p in load(project(tmp_path, {})).pages}

    assert pages == {
        "start/overview": "Overview",
        "start/guide": "The guide",
        "more/extra": "Extra",
    }


def test_a_file_that_is_checked_but_not_published_has_its_links_held_to_the_rules(
    tmp_path: Path,
) -> None:
    manifest = (
        MANIFEST.replace(
            "          - source: README.md\n            slug: start/overview\n            type: explanation\n"
            "            summary: The overview.\n",
            "",
        ).replace("home:\n  start:\n    slug: start/overview\n", "")
        + "checked:\n  - source: README.md\n"
    )
    root = project(
        tmp_path,
        {
            "README.md": "# O\n\n## Install\n\n[gone](docs/gone.md)\n",
            "docs/guide.md": "# G\n\n[a](../README.md#install) [b](../README.md#nope)\n",
        },
        manifest,
    )

    problems = problems_of(root)

    assert "README.md: docs/gone.md does not exist" in problems
    assert "docs/guide.md: ../README.md#nope: README.md has no heading #nope" in problems
    assert not any("#install" in p for p in problems)


# -- the Markdown conventions


def test_the_alerts_github_draws_are_accepted_and_another_kind_is_a_problem(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {
            "docs/guide.md": "# G\n\n> [!NOTE]\n> Fine.\n\n> [!WARNING]\n> Fine.\n\n> [!DANGER]\n> No.\n"
        },
    )

    problems = problems_of(root)

    assert problems == [
        "docs/guide.md: [!DANGER] is not an alert GitHub draws (NOTE, TIP, IMPORTANT, WARNING, CAUTION)"
    ]


def test_a_code_block_may_name_its_file_and_its_tab_and_nothing_else(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {
            "docs/guide.md": (
                '# G\n\n```yaml title="noust.yaml"\na: 1\n```\n\n'
                '```bash tab="Debian"\napt install noust\n```\n\n'
                '```bash tab="Fedora" title="install.sh"\ndnf install noust\n```\n\n'
                '```bash colour="red"\nls\n```\n\n'
                "```bash title=noust.yaml\nls\n```\n"
            )
        },
    )

    problems = problems_of(root)

    assert problems == [
        'docs/guide.md: the code block ```bash colour="red" has colour=, not title= or tab=',
        "docs/guide.md: the code block ```bash title=noust.yaml is not 'language word=\"...\"'",
    ]


# -- what the preview writes


@pytest.fixture
def built(tmp_path: Path) -> Path:
    root = project(tmp_path / "repo", {})
    out = tmp_path / "out"
    site = build_docs.build_site(out, "3.4", root)
    assert not site.problems
    return out


def test_every_page_is_a_route_and_a_markdown_file(built: Path) -> None:
    version = built / "3.4"

    for slug in ("start/overview", "start/guide", "more/extra"):
        assert (version / slug / "index.html").is_file()
        assert (version / f"{slug}.md").is_file()


def test_the_markdown_of_a_preview_page_is_the_source_as_written(built: Path) -> None:
    assert (
        built / "3.4" / "start" / "guide.md"
    ).read_text() == "# Guide\n\n## First steps\n\nText.\n"


def test_the_search_index_has_the_page_and_its_sections(built: Path) -> None:
    entries = json.loads((built / "3.4" / "search-index.json").read_text())

    urls = {e["url"] for e in entries}
    assert {"start/guide/", "start/guide/#first-steps", "start/overview/"} <= urls
    section = next(e for e in entries if e["url"] == "start/guide/#first-steps")
    assert section["title"] == "The guide: First steps"
    assert section["text"] == "Text."


def test_a_page_has_the_navigation_the_search_and_a_link_to_edit_it(built: Path) -> None:
    html = (built / "3.4" / "start" / "guide" / "index.html").read_text()

    assert 'aria-current="page">The guide</a>' in html
    assert 'id="q"' in html and 'src="../../site.js"' in html
    assert "https://github.com/example/project/blob/main/docs/guide.md" in html
    assert '<link rel="stylesheet" href="../../site.css">' in html
    assert "<h2>Start: First</h2>" in html


def test_the_preview_has_no_inline_handler_a_strict_policy_would_refuse(built: Path) -> None:
    for page in built.rglob("*.html"):
        assert not re.search(r"\son[a-z]+=", page.read_text()), page


def test_nothing_in_the_site_loads_from_another_host(built: Path) -> None:
    for page in built.rglob("index.html"):
        text = page.read_text()
        assert not re.search(r'(src|href)="https?://(?!github\.com/example)', text), page


def test_the_top_of_the_site_points_at_the_newest_version(built: Path) -> None:
    assert json.loads((built / "versions.json").read_text()) == {
        "latest": "3.4",
        "versions": ["3.4"],
    }
    assert "url=3.4/" in (built / "index.html").read_text()
    assert "url=../3.4/" in (built / "latest" / "index.html").read_text()
    rules = (built / "_redirects").read_text().splitlines()
    assert "/latest/* /3.4/:splat 302" in rules
    assert "/old-guide/ /3.4/start/guide/ 301" in rules


def test_building_another_version_beside_keeps_the_first(tmp_path: Path) -> None:
    root = project(tmp_path / "repo", {})
    out = tmp_path / "out"
    build_docs.build_site(out, "3.4", root)
    build_docs.build_site(out, "3.10", root)
    build_docs.build_site(out, "3.5", root)

    listed = json.loads((out / "versions.json").read_text())

    assert listed == {"latest": "3.10", "versions": ["3.10", "3.5", "3.4"]}
    assert (out / "3.4" / "start" / "guide" / "index.html").is_file()
    assert "url=3.10/" in (out / "index.html").read_text()


def test_rebuilding_a_version_replaces_it(tmp_path: Path) -> None:
    root = project(tmp_path / "repo", {})
    out = tmp_path / "out"
    build_docs.build_site(out, "3.4", root)
    (out / "3.4" / "stale.html").write_text("x")

    build_docs.build_site(out, "3.4", root)

    assert not (out / "3.4" / "stale.html").exists()


def test_the_version_comes_from_pyproject(tmp_path: Path) -> None:
    root = project(tmp_path, {})

    assert build_docs.current_version(root) == "3.4"


# -- the package a site paints


def emitted(root: Path, dates: dict[str, str] | None = None) -> tuple[Path, build_docs.Site]:
    """Emit a project's package and return where it is, with the site."""
    out = root / "package"
    site = build_docs.load(root / "docs" / "site.yaml", root)
    build_docs.emit(site, out, "3.4", root, dates or {})
    return out, site


def page_of(package: Path, slug: str) -> dict[str, object]:
    manifest = json.loads((package / "manifest.json").read_text())
    return next(p for p in manifest["pages"] if p["slug"] == slug)


def test_the_package_manifest_holds_the_tabs_their_groups_and_every_page(tmp_path: Path) -> None:
    package, site = emitted(project(tmp_path, {}), {"docs/guide.md": "2026-10-28"})

    manifest = json.loads((package / "manifest.json").read_text())

    assert not site.problems
    assert manifest["format"] == build_docs.PACKAGE_FORMAT
    assert manifest["version"] == "3.4" and manifest["base"] == "/docs/"
    assert manifest["home"] == {"start": {"slug": "start/overview"}}
    assert [(t["slug"], t["url"]) for t in manifest["tabs"]] == [
        ("start", "/docs/start/overview/"),
        ("more", "/docs/more/extra/"),
    ]
    assert manifest["tabs"][0]["groups"] == [
        {"title": "First", "sidebar": True, "pages": ["start/overview", "start/guide"]}
    ]
    guide = page_of(package, "start/guide")
    assert guide["url"] == "/docs/start/guide/"
    assert guide["markdown"] == "pages/start/guide.md"
    assert guide["type"] == "tutorial" and guide["lang"] == "en"
    assert guide["tab"] == "start" and guide["group"] == "First"
    assert guide["last_updated"] == "2026-10-28"
    assert guide["edit_url"] == "https://github.com/example/project/edit/main/docs/guide.md"
    assert str(guide["report_url"]).startswith(
        "https://github.com/example/project/issues/new?title="
    )
    assert guide["headings"] == [{"level": 2, "anchor": "first-steps", "text": "First steps"}]


def test_previous_and_next_stay_inside_a_tab(tmp_path: Path) -> None:
    package, _site = emitted(project(tmp_path, {}))

    overview = page_of(package, "start/overview")
    guide = page_of(package, "start/guide")
    extra = page_of(package, "more/extra")

    assert overview["previous"] is None
    assert overview["next"] == {
        "slug": "start/guide",
        "title": "The guide",
        "url": "/docs/start/guide/",
    }
    assert guide["previous"] == {
        "slug": "start/overview",
        "title": "Overview",
        "url": "/docs/start/overview/",
    }
    assert guide["next"] is None
    assert extra["previous"] is None and extra["next"] is None


def test_a_link_in_the_package_is_an_absolute_route_with_the_anchor(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {
            "README.md": (
                "# O\n\n## Here\n\n[steps](docs/guide.md#first-steps), [up](#here), "
                '[extra](docs/extra.md "Extra") and ![shot](docs/assets/shot.png).\n'
            ),
            "docs/assets/shot.png": "PNG",
        },
    )

    package, site = emitted(root)
    text = (package / "pages" / "start" / "overview.md").read_text()

    assert not site.problems
    assert "[steps](/docs/start/guide/#first-steps)" in text
    assert "[up](#here)" in text
    assert '[extra](/docs/more/extra/ "Extra")' in text
    assert "![shot](/docs/assets/docs/assets/shot.png)" in text
    assert (package / "assets" / "docs" / "assets" / "shot.png").read_text() == "PNG"


def test_a_link_to_a_repository_file_or_folder_goes_to_github(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {"README.md": "# O\n\n[unit](src/x.py#L3) [dir](src/)\n", "src/x.py": "x = 1\n"},
    )

    package, _site = emitted(root)
    text = (package / "pages" / "start" / "overview.md").read_text()

    assert "[unit](https://github.com/example/project/blob/main/src/x.py#L3)" in text
    assert "[dir](https://github.com/example/project/tree/main/src)" in text


def test_an_image_of_this_repository_on_github_is_served_from_the_package(tmp_path: Path) -> None:
    raw = "https://raw.githubusercontent.com/example/project/main/docs/assets/shot.png"
    root = project(
        tmp_path,
        {
            "README.md": f'# O\n\n![shot]({raw})\n\n<picture><source srcset="{raw}"></picture>\n',
            "docs/assets/shot.png": "PNG",
        },
    )

    package, _site = emitted(root)
    text = (package / "pages" / "start" / "overview.md").read_text()

    assert "![shot](/docs/assets/docs/assets/shot.png)" in text
    assert 'srcset="/docs/assets/docs/assets/shot.png"' in text


def test_code_alerts_and_the_words_of_a_code_block_are_left_as_written(tmp_path: Path) -> None:
    guide = (
        "# Guide\n\n## First steps\n\n"
        "> [!TIP]\n> Read [the extra](extra.md) first.\n\n"
        "Write `[x](extra.md)` to link it, or ``a `[y](extra.md)` b``.\n\n"
        '```markdown title="README.md" tab="Markdown"\n[x](extra.md)\n```\n\n'
        "    [z](extra.md)\n"
    )
    root = project(tmp_path, {"docs/guide.md": guide})

    package, site = emitted(root)
    text = (package / "pages" / "start" / "guide.md").read_text()

    assert not site.problems
    assert "> [!TIP]\n> Read [the extra](/docs/more/extra/) first." in text
    assert "Write `[x](extra.md)` to link it, or ``a `[y](extra.md)` b``." in text
    assert '```markdown title="README.md" tab="Markdown"\n[x](extra.md)\n```' in text
    assert "    [z](extra.md)\n" in text


def test_reference_definitions_and_raw_html_are_resolved(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {
            "README.md": '# O\n\nSee [the guide][g].\n\n[g]: docs/guide.md#first-steps\n\n<a href="docs/extra.md">x</a>\n'
        },
    )

    package, site = emitted(root)
    text = (package / "pages" / "start" / "overview.md").read_text()

    assert not site.problems
    assert "[g]: /docs/start/guide/#first-steps" in text
    assert '<a href="/docs/more/extra/">x</a>' in text


def test_an_address_that_is_not_rewritten_is_left_byte_for_byte(tmp_path: Path) -> None:
    badge = '<img src="https://img.shields.io/pypi/v/noust?color=blue&logo=pypi" alt="Version">'
    root = project(tmp_path, {"README.md": f"# O\n\n<p>{badge}</p>\n"})

    package, _site = emitted(root)

    assert badge in (package / "pages" / "start" / "overview.md").read_text()


def test_a_split_reference_is_a_page_per_section_below_its_route(tmp_path: Path) -> None:
    root = project(tmp_path, {"docs/commands.md": COMMANDS}, SPLIT_MANIFEST)

    package, site = emitted(root)
    manifest = json.loads((package / "manifest.json").read_text())

    assert not site.problems
    assert manifest["tabs"][0]["groups"][0]["pages"] == [
        "reference/guide",
        "reference/cli",
        "reference/cli/run",
        "reference/cli/stop",
    ]
    index = page_of(package, "reference/cli")
    run = page_of(package, "reference/cli/run")
    assert index["children"] == ["reference/cli/run", "reference/cli/stop"]
    assert run["parent"] == "reference/cli"
    assert run["title"] == "tool run"
    assert run["summary"] == "Run the thing."
    assert run["headings"] == [{"level": 2, "anchor": "notes", "text": "Notes"}]
    assert (
        (package / "pages" / "reference" / "cli" / "run.md")
        .read_text()
        .startswith("# tool run\n\nRun the thing.")
    )


def test_a_link_into_a_split_reference_reaches_the_part_that_holds_the_heading(
    tmp_path: Path,
) -> None:
    guide = "# Guide\n\n## First steps\n\n[stop](commands.md#tool-stop), [its notes](commands.md#notes-1).\n"
    root = project(tmp_path, {"docs/commands.md": COMMANDS, "docs/guide.md": guide}, SPLIT_MANIFEST)

    package, site = emitted(root)
    pages = package / "pages" / "reference"

    assert not site.problems
    assert "[stop](/docs/reference/cli/stop/#tool-stop)" in (pages / "guide.md").read_text()
    assert "[its notes](/docs/reference/cli/stop/#notes)" in (pages / "guide.md").read_text()
    index = (pages / "cli.md").read_text()
    assert "[`run`](/docs/reference/cli/run/#tool-run)" in index
    run = (pages / "cli" / "run.md").read_text()
    assert "See [stop](/docs/reference/cli/stop/#tool-stop)" in run
    assert "[the guide](/docs/reference/guide/#first-steps)" in run


def test_the_package_lists_redirects_for_the_web_server(tmp_path: Path) -> None:
    package, _site = emitted(project(tmp_path, {}))

    assert json.loads((package / "redirects.json").read_text()) == {
        "redirects": [{"from": "/docs/old-guide/", "to": "/docs/start/guide/", "status": 301}]
    }


def test_llms_txt_follows_the_format_it_is_published_in(tmp_path: Path) -> None:
    package, _site = emitted(project(tmp_path, {}))

    lines = (package / "llms.txt").read_text().splitlines()

    assert lines[0] == "# Test docs"
    assert lines[2] == "> A project, for a test."
    assert "## Start" in lines and "## More" in lines
    assert "- [The guide](/docs/start/guide.md): How to." in lines
    assert all(
        re.fullmatch(r"- \[.+\]\(.+\.md\)(: .+)?", line) for line in lines if line.startswith("- ")
    )


def test_llms_full_holds_every_page_resolved_under_a_marker(tmp_path: Path) -> None:
    package, _site = emitted(project(tmp_path, {}))

    text = (package / "llms-full.txt").read_text()

    assert "<!-- /docs/start/guide.md (from docs/guide.md) -->" in text
    assert "See [the guide](/docs/start/guide/)." in text


def test_emitting_again_replaces_the_package(tmp_path: Path) -> None:
    root = project(tmp_path, {})
    package, _site = emitted(root)
    (package / "stale.md").write_text("x")

    emitted(root)

    assert not (package / "stale.md").exists()


def test_main_emits_the_package_with_the_dates_of_the_last_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, {})
    monkeypatch.setattr(build_docs, "ROOT", root)
    monkeypatch.setattr(build_docs, "MANIFEST", root / "docs" / "site.yaml")
    asked: list[list[str]] = []

    def dates(where: Path, sources: list[str]) -> dict[str, str]:
        asked.append(sources)
        return {"README.md": "2026-10-01"}

    monkeypatch.setattr(build_docs, "git_dates", dates)

    code = build_docs.main(
        ["--out", str(tmp_path / "out"), "--emit", str(tmp_path / "pkg"), "--version", "3.4"]
    )

    assert code == 0, capsys.readouterr().err
    assert asked == [["README.md", "docs/guide.md", "docs/extra.md"]]
    assert page_of(tmp_path / "pkg", "start/overview")["last_updated"] == "2026-10-01"


def test_main_reports_problems_and_exits_non_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, {"README.md": "# O\n\n[gone](docs/gone.md)\n"})
    monkeypatch.setattr(build_docs, "ROOT", root)
    monkeypatch.setattr(build_docs, "MANIFEST", root / "docs" / "site.yaml")

    code = build_docs.main(["--check", "--version", "3.4"])

    assert code == 1
    assert "docs/gone.md does not exist" in capsys.readouterr().err


def test_a_version_that_is_not_a_minor_line_is_a_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert build_docs.main(["--version", "latest"]) == 2
    assert "is not a <major>.<minor> version" in capsys.readouterr().err


# -- the real documentation


@pytest.fixture(scope="module")
def real_site(tmp_path_factory: pytest.TempPathFactory) -> build_docs.Site:
    out = tmp_path_factory.mktemp("site")
    return build_docs.build_site(out / "site", "3.4", ROOT, out / "package", {})


@pytest.fixture(scope="module")
def real_package(real_site: build_docs.Site, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("package")
    build_docs.emit(real_site, out, "3.4", ROOT, {})
    return out


def test_the_real_documentation_has_no_link_or_anchor_that_leads_nowhere(
    real_site: build_docs.Site,
) -> None:
    assert [str(p) for p in real_site.problems] == []


def test_every_markdown_file_of_docs_is_published_or_excluded_with_a_reason() -> None:
    data = build_docs.yaml.safe_load((ROOT / "docs" / "site.yaml").read_text())

    assert all(item.get("why") for item in data.get("exclude") or [])
    listed = set(build_docs.listed_sources(ROOT / "docs" / "site.yaml"))
    excluded = {item["source"] for item in data.get("exclude") or []}
    on_disk = {p.relative_to(ROOT).as_posix() for p in (ROOT / "docs").glob("*.md")}
    assert on_disk <= listed | excluded


def test_every_page_says_in_a_sentence_what_it_is_for(real_site: build_docs.Site) -> None:
    """The index for people and for machines is made of these summaries."""
    missing = [p.slug for p in real_site.pages if not p.summary]

    assert missing == []


def test_the_documentation_answers_five_questions(real_site: build_docs.Site) -> None:
    tabs = [(tab.slug, tab.footer) for tab in real_site.tabs]

    assert tabs == [
        ("start", False),
        ("guides", False),
        ("concepts", False),
        ("reference", False),
        ("releases", False),
        ("project", True),
    ]


def test_the_package_of_the_real_documentation_links_only_to_routes_and_addresses(
    real_package: Path,
) -> None:
    md = build_docs.parser()
    for page in (real_package / "pages").rglob("*.md"):
        for address in build_docs._addresses(md.parse(page.read_text(encoding="utf-8"))):
            assert not address.endswith(".md") or address.startswith("https://"), (page, address)
            assert ".md#" not in address or address.startswith("https://"), (page, address)


def test_the_command_reference_is_published_one_page_per_command(real_package: Path) -> None:
    from noust.cli.app import cli

    top = [path for path, _c in generate_reference.walk(cli, ("noust",)) if len(path) == 2]
    cli_page = page_of(real_package, "reference/cli")

    assert cli_page["children"] == [f"reference/cli/{path[1]}" for path in top]
    app = page_of(real_package, "reference/cli/app")
    assert app["title"] == "noust app"
    assert any(h["anchor"] == "noust-app-migrate" for h in app["headings"])  # type: ignore[index, union-attr]


def test_the_routes_the_product_and_the_old_site_used_still_lead_somewhere(
    real_site: build_docs.Site,
) -> None:
    """The console, the CLI and the repository's own files point at these; the slugs before the
    tabs are redirects."""
    slugs = {p.slug for p in real_site.pages}
    reachable = slugs | set(real_site.redirects)

    assert {
        "troubleshooting",
        "downgrading",
        "release-policy",
        "support",
        "reference-cli",
        "reference-api",
        "reference-config",
        "coolify",
        "cloud-init",
        "first-application",
        "frameworks",
        "requirements",
    } <= reachable
    assert all(real_site.redirects[old] in slugs for old in real_site.redirects)


def test_the_generated_references_match_the_code() -> None:
    stale = [
        page.name
        for page, make in generate_reference.PAGES.items()
        if not page.exists() or page.read_text(encoding="utf-8") != make()
    ]

    assert stale == [], "run scripts/generate_reference.py"


def test_the_generated_references_say_they_are_generated() -> None:
    for page in generate_reference.PAGES:
        assert "Generated by scripts/generate_reference.py" in page.read_text(encoding="utf-8")


def test_every_code_block_of_a_generated_reference_names_its_language() -> None:
    """A block without one is shown without highlighting, and the CLI reference had a thousand."""
    for make in generate_reference.PAGES.values():
        md = build_docs.parser()
        fences = [t for t in md.parse(make()) if t.type == "fence"]
        assert all(t.info.strip() for t in fences), [t.content for t in fences if not t.info][:3]


def test_the_cli_reference_lists_every_visible_command() -> None:
    from noust.cli.app import cli

    text = generate_reference.cli_page()
    commands = [path for path, _c in generate_reference.walk(cli, ("noust",))]

    assert len(commands) > 300
    for path in commands[1:]:
        level = "##" if len(path) == 2 else "###"
        assert f"\n{level} {' '.join(path)}\n" in text


def test_the_api_reference_lists_every_operation_of_the_schema() -> None:
    data = json.loads((ROOT / "panel" / "openapi.json").read_text())
    operations = sum(
        1 for item in data["paths"].values() for m in generate_reference.METHODS if m in item
    )

    assert f"{operations} operations." in generate_reference.api_page()
