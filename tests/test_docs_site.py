# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The documentation site (checklist N-17, N-22): a static, searchable, versioned build of ``docs/``.

Two kinds of test. The first builds a small repository made for the purpose and pins how the
builder resolves a link, an anchor, an image and a redirect, and what it writes. The second
builds the real documentation and holds it to the bar CI holds it to: no link or anchor that
leads nowhere, no Markdown file nobody listed, and the generated references current.
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
summary: A project, for a test.
sections:
  - title: Start
    pages:
      - source: README.md
        slug: overview
        summary: The overview.
      - source: docs/guide.md
        slug: guide
        title: The guide
        summary: How to.
  - title: More
    pages:
      - source: docs/extra.md
        slug: extra
exclude:
  - source: docs/private.md
    why: not for readers
redirects:
  old-guide: guide
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

    page = next(p for p in load(root).pages if p.slug == "guide")

    assert [anchor for _l, anchor, _t in page.headings] == ["guide", "notes", "notes-1", "notes-2"]


def test_inline_code_and_emphasis_in_a_heading_do_not_break_its_anchor(tmp_path: Path) -> None:
    root = project(tmp_path, {"docs/guide.md": "# Guide\n\n## The `build:` *block*\n"})

    page = next(p for p in load(root).pages if p.slug == "guide")

    assert "the-build-block" in page.anchors


# -- links


def test_a_link_to_another_page_becomes_a_relative_route(tmp_path: Path) -> None:
    body = render(project(tmp_path, {}), "overview")

    assert 'href="../guide/"' in body


def test_a_link_with_an_anchor_keeps_it(tmp_path: Path) -> None:
    root = project(tmp_path, {"README.md": "# O\n\n[steps](docs/guide.md#first-steps)\n"})

    assert 'href="../guide/#first-steps"' in render(root, "overview")


def test_a_link_inside_docs_is_resolved_from_the_file_that_holds_it(tmp_path: Path) -> None:
    root = project(tmp_path, {"docs/guide.md": "# G\n\n[extra](extra.md) [home](../README.md)\n"})

    body = render(root, "guide")

    assert 'href="../extra/"' in body
    assert 'href="../overview/"' in body


def test_a_link_to_a_file_that_is_not_a_page_goes_to_github(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {"README.md": "# O\n\n[the unit](src/x.py#L3) [dir](src)\n", "src/x.py": "x = 1\n"},
    )

    body = render(root, "overview")

    assert 'href="https://github.com/example/project/blob/main/src/x.py#L3"' in body
    assert 'href="https://github.com/example/project/tree/main/src"' in body


def test_an_external_address_and_a_mail_link_are_left_alone(tmp_path: Path) -> None:
    root = project(tmp_path, {"README.md": "# O\n\n[a](https://example.org/x) [b](mailto:a@b.c)\n"})

    body = render(root, "overview")

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
    page = next(p for p in site.pages if p.slug == "guide")

    build_docs.render_page(site, page, build_docs.parser(), root)

    assert [str(p) for p in site.problems] == ["docs/guide.md: #fake is not a heading of this page"]


def test_a_link_that_leaves_the_repository_is_a_problem(tmp_path: Path) -> None:
    root = project(tmp_path, {"docs/guide.md": "# G\n\n[x](../../etc/passwd)\n"})
    site = load(root)
    page = next(p for p in site.pages if p.slug == "guide")

    build_docs.render_page(site, page, build_docs.parser(), root)

    assert any("leaves the repository" in str(p) for p in site.problems)


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
    page = (out / "3.4" / "overview" / "index.html").read_text()
    assert 'src="../assets/docs/assets/shot.png"' in page
    assert (out / "3.4" / "assets" / "docs" / "assets" / "shot.png").read_text() == "PNG"


def test_raw_html_in_a_page_has_its_addresses_resolved_too(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {
            "README.md": '# O\n\n<p align="center"><img src="docs/assets/logo.png" width="80"></p>\n',
            "docs/assets/logo.png": "PNG",
        },
    )

    body = render(root, "overview")

    assert 'src="../assets/docs/assets/logo.png"' in body


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
    manifest = MANIFEST.replace("slug: extra", "slug: guide")

    problems = [str(p) for p in load(project(tmp_path, {}, manifest)).problems]

    assert any("slug 'guide' is used by" in m for m in problems)


def test_a_redirect_to_a_page_that_is_gone_is_a_problem(tmp_path: Path) -> None:
    manifest = MANIFEST.replace("old-guide: guide", "old-guide: vanished")

    problems = [str(p) for p in load(project(tmp_path, {}, manifest)).problems]

    assert any("redirect old-guide leads to vanished" in m for m in problems)


def test_the_title_is_the_manifests_or_the_first_heading(tmp_path: Path) -> None:
    pages = {p.slug: p.title for p in load(project(tmp_path, {})).pages}

    assert pages == {"overview": "Overview", "guide": "The guide", "extra": "Extra"}


# -- what is written


@pytest.fixture
def built(tmp_path: Path) -> Path:
    root = project(tmp_path / "repo", {})
    out = tmp_path / "out"
    site = build_docs.build_site(out, "3.4", root)
    assert not site.problems
    return out


def test_every_page_is_a_route_a_markdown_file_and_a_page_of_the_index(built: Path) -> None:
    version = built / "3.4"

    for slug in ("overview", "guide", "extra"):
        assert (version / slug / "index.html").is_file()
        assert (version / f"{slug}.md").is_file()
    assert "[The guide](guide.md): How to." in (version / "llms.txt").read_text()


def test_the_markdown_of_a_page_is_the_source_as_written(built: Path) -> None:
    assert (built / "3.4" / "guide.md").read_text() == "# Guide\n\n## First steps\n\nText.\n"


def test_llms_txt_follows_the_format_it_is_published_in(built: Path) -> None:
    lines = (built / "3.4" / "llms.txt").read_text().splitlines()

    assert lines[0] == "# Test docs"
    assert lines[2] == "> A project, for a test."
    assert "## Start" in lines and "## More" in lines
    assert all(
        re.match(r"- \[.+\]\(.+\.md\)(: .+)?$", line) for line in lines if line.startswith("- ")
    )


def test_llms_full_holds_every_page_under_a_marker(built: Path) -> None:
    text = (built / "3.4" / "llms-full.txt").read_text()

    assert "<!-- guide.md (from docs/guide.md) -->" in text
    assert "## First steps" in text


def test_the_search_index_has_the_page_and_its_sections(built: Path) -> None:
    entries = json.loads((built / "3.4" / "search-index.json").read_text())

    urls = {e["url"] for e in entries}
    assert {"guide/", "guide/#first-steps", "overview/"} <= urls
    section = next(e for e in entries if e["url"] == "guide/#first-steps")
    assert section["title"] == "The guide: First steps"
    assert section["text"] == "Text."


def test_a_page_has_the_navigation_the_search_and_a_link_to_edit_it(built: Path) -> None:
    html = (built / "3.4" / "guide" / "index.html").read_text()

    assert 'aria-current="page">The guide</a>' in html
    assert 'id="q"' in html and 'src="../site.js"' in html
    assert "https://github.com/example/project/blob/main/docs/guide.md" in html
    assert '<link rel="stylesheet" href="../site.css">' in html


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
    assert "/old-guide/ /3.4/guide/ 301" in rules


def test_building_another_version_beside_keeps_the_first(tmp_path: Path) -> None:
    root = project(tmp_path / "repo", {})
    out = tmp_path / "out"
    build_docs.build_site(out, "3.4", root)
    build_docs.build_site(out, "3.10", root)
    build_docs.build_site(out, "3.5", root)

    listed = json.loads((out / "versions.json").read_text())

    assert listed == {"latest": "3.10", "versions": ["3.10", "3.5", "3.4"]}
    assert (out / "3.4" / "guide" / "index.html").is_file()
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


def test_main_reports_problems_and_exits_non_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, {"README.md": "# O\n\n[gone](docs/gone.md)\n"})
    monkeypatch.setattr(build_docs, "ROOT", root)
    monkeypatch.setattr(build_docs, "MANIFEST", root / "docs" / "site.yaml")
    original = build_docs.build_site
    monkeypatch.setattr(
        build_docs, "build_site", lambda out, version, root_=root: original(out, version, root)
    )

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
    return build_docs.build_site(out, "3.4", ROOT)


def test_the_real_documentation_has_no_link_or_anchor_that_leads_nowhere(
    real_site: build_docs.Site,
) -> None:
    assert [str(p) for p in real_site.problems] == []


def test_every_markdown_file_of_docs_is_published_or_excluded_with_a_reason() -> None:
    data = build_docs.yaml.safe_load((ROOT / "docs" / "site.yaml").read_text())

    assert all(item.get("why") for item in data.get("exclude") or [])
    listed = {p["source"] for section in data["sections"] for p in section["pages"]}
    excluded = {item["source"] for item in data.get("exclude") or []}
    on_disk = {p.relative_to(ROOT).as_posix() for p in (ROOT / "docs").glob("*.md")}
    assert on_disk <= listed | excluded


def test_every_page_says_in_a_sentence_what_it_is_for(real_site: build_docs.Site) -> None:
    """The index for people and for machines is made of these summaries."""
    missing = [p.slug for p in real_site.pages if not p.summary]

    assert missing == []


def test_the_pages_named_by_the_product_exist() -> None:
    """The console, the CLI and the repository's own files point at these routes."""
    slugs = {p.slug for p in build_docs.load().pages}

    assert {
        "overview",
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
    } <= slugs


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


def test_the_cli_reference_lists_every_visible_command() -> None:
    from noust.cli.app import cli

    text = generate_reference.cli_page()
    commands = [path for path, _c in generate_reference.walk(cli, ("noust",))]

    assert len(commands) > 300
    for path in commands[1:]:
        assert f"### {' '.join(path)}\n" in text


def test_the_api_reference_lists_every_operation_of_the_schema() -> None:
    data = json.loads((ROOT / "panel" / "openapi.json").read_text())
    operations = sum(
        1 for item in data["paths"].values() for m in generate_reference.METHODS if m in item
    )

    assert f"{operations} operations." in generate_reference.api_page()
