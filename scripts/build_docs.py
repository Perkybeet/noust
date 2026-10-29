#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Check the documentation in ``docs/`` and turn it into what a documentation site needs.

The Markdown in ``docs/`` is the one source (checklist N-17). ``docs/site.yaml`` arranges it in
five tabs that answer five questions (Start, Guides, Concepts, Reference, Releases), each with its
groups and its pages, and gives every page a stable route, a type and a sentence that says what it
is for. Nothing here uploads anything.

Two outputs, from the same parsed pages and the same link resolution:

- ``--emit DIR``: **the package a website paints**, and nothing more is needed to paint it. The site
  resolves no link and reads no file of the repository (rule 3: one source, one resolver):

  - ``manifest.json``: the tabs, their groups and every page in reading order, each with its route,
    title, summary, type (tutorial, guide, explanation, reference), headings, previous and next,
    the file it comes from, its edit and report addresses and the date of its last commit.
  - ``pages/<route>.md``: each page's Markdown with every link already resolved: to another page's
    absolute route and the anchor that page gives the heading, to a file of the repository on
    GitHub, or to an image copied under ``assets/``. GitHub's alerts (``> [!NOTE]``) and the
    ``title="..."`` and ``tab="..."`` words of a code block are left as written for the site to draw.
  - ``redirects.json``: old route to new, for the web server.
  - ``llms.txt`` and ``llms-full.txt``, in the format <https://llmstxt.org> describes.
  - ``assets/``: only the images the pages use.

  A reference too long for one page (``split: 2`` in the manifest) is published as one page per
  second-level heading, below the route of the first, and links into it reach the part that holds
  the heading.

- ``--out DIR`` (the default action): a plain HTML preview, opened from disk, for reading the
  documentation without a site. It is not what is published.

Either fails on a link to nothing, an anchor the target page does not have, a Markdown file of
``docs/`` that the manifest neither lists nor excludes, an alert of a kind GitHub does not draw, or
a word on a code block other than ``title`` and ``tab``. Links are written the way GitHub renders
them (``compose.md#the-relay``, ``../SECURITY.md``) and heading anchors follow GitHub's rules, so
the files read the same on GitHub as on the site.

Usage:
    scripts/build_docs.py                         Build the preview into build/docs-site.
    scripts/build_docs.py --emit build/docs-package
                                                  Also write the package a site paints.
    scripts/build_docs.py --check                 Build both into a temporary directory and
                                                  report problems (CI runs this).
"""

from __future__ import annotations

import argparse
import copy
import html
import itertools
import json
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, select_autoescape
from markdown_it import MarkdownIt
from markdown_it.token import Token

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "docs" / "site.yaml"
DEFAULT_OUT = ROOT / "build" / "docs-site"

#: The package's own format. A site that reads it refuses one it does not know.
PACKAGE_FORMAT = 1

#: A search entry keeps this much of a section's text.
SEARCH_TEXT_LENGTH = 400

#: A split part's summary is its first sentence, at most this long.
SUMMARY_LENGTH = 200

#: ``<major>.<minor>`` folders of a built site.
VERSION_DIR = re.compile(r"\d+\.\d+")

#: What a page is for (https://diataxis.fr): learning by doing, doing one task, understanding, or
#: looking up.
PAGE_TYPES = ("tutorial", "guide", "explanation", "reference")

#: The alerts GitHub draws (``> [!NOTE]``); any other kind is shown there as plain text.
ALERT_KINDS = ("NOTE", "TIP", "IMPORTANT", "WARNING", "CAUTION")

#: The words a code block may carry after its language: the file it shows, and the tab it is in
#: when blocks side by side are variants of one step. GitHub ignores both.
FENCE_WORDS = ("title", "tab")

#: A route: lower-case segments separated by ``/``.
_SLUG = re.compile(r"[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*")

_ALERT = re.compile(r"\s*>\s*\[!(?P<kind>[A-Za-z]+)\]\s*")
_FENCE_INFO = re.compile(r'(?P<lang>[A-Za-z0-9_+#.-]+)(?P<words>(?:\s+[a-z]+="[^"]*")*)\s*')
_FENCE_WORD = re.compile(r'([a-z]+)="[^"]*"')
_EXTERNAL = re.compile(r"(?:[a-z][a-z0-9+.\-]*:|//).*", re.IGNORECASE | re.DOTALL)
_HTML_REF = re.compile(
    r"""(?P<attr>\b(?:href|src|srcset))=(?P<quote>["'])(?P<url>[^"']*)(?P=quote)"""
)
_INLINE_URL = re.compile(r"(?<=\]\()(?P<url><[^>\n]*>|[^)\s]+)")
_DEFINITION = re.compile(r"(?P<lead>\s{0,3}\[[^\]]+\]:\s*)(?P<url><[^>\n]*>|\S+)")
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".avif")


@dataclass(frozen=True)
class Problem:
    """
    Something wrong in the documentation.

    Attributes:
        source: The Markdown file (or the manifest) it is in.
        message: What is wrong and what would fix it.
    """

    source: str
    message: str

    def __str__(self) -> str:
        return f"{self.source}: {self.message}"


@dataclass
class Page:
    """
    One page of the documentation, as its source file has it.

    Attributes:
        source: Path of the Markdown file, relative to the repository.
        slug: The stable route, such as ``guides/databases/install-an-engine``.
        title: The page's title.
        summary: One sentence, for the index and the search.
        section: The group it is under in the navigation.
        type: ``tutorial``, ``guide``, ``explanation`` or ``reference``.
        tab: The slug of the tab it is under.
        lang: The language it is written in.
        split: Publish it as one page per heading of this level (2; None for one page).
        split_prefix: Taken off the front of each part's route (``noust-`` for the commands).
        markdown: The text as written.
        tokens: The parsed Markdown.
        headings: ``(level, anchor, text)`` of each heading, in order.
        html: The rendered body of the preview (links resolved).
    """

    source: str
    slug: str
    title: str
    summary: str
    section: str
    type: str = "guide"
    tab: str = ""
    lang: str = "en"
    split: int | None = None
    split_prefix: str = ""
    markdown: str = ""
    tokens: list[Token] = field(default_factory=list)
    headings: list[tuple[int, str, str]] = field(default_factory=list)
    html: str = ""

    @property
    def anchors(self) -> set[str]:
        """Every anchor the page defines."""
        return {anchor for _level, anchor, _text in self.headings}

    @property
    def depth(self) -> int:
        """How many folders deep the page's ``index.html`` is in a built site."""
        return self.slug.count("/") + 1


@dataclass
class Group:
    """
    A group of pages in a tab's sidebar.

    Attributes:
        title: The group's label.
        pages: Its pages, in reading order.
        sidebar: False for a group that is linked but not shown in the main sidebar.
    """

    title: str
    pages: list[Page]
    sidebar: bool = True


@dataclass
class Tab:
    """
    One of the questions the documentation is arranged by.

    Attributes:
        title: The tab's label.
        slug: The first segment of every route in it.
        summary: The question it answers.
        groups: Its groups, in order.
        footer: True for the pages linked from the foot of every page rather than a tab.
    """

    title: str
    slug: str
    summary: str
    groups: list[Group]
    footer: bool = False

    @property
    def pages(self) -> list[Page]:
        """Every page of the tab, in reading order."""
        return [page for group in self.groups for page in group.pages]


@dataclass
class Site:
    """
    The whole documentation, parsed.

    Attributes:
        title: The site's name.
        summary: What the project is, for llms.txt.
        repository: The GitHub repository's URL.
        branch: The branch its files are linked on.
        base: The path (or address) the site is served under, ending in ``/``.
        tabs: The tabs, in order.
        redirects: Old route to slug.
        home: What the landing page of the documentation shows (slugs checked).
        checked: Files that are not published but whose links are held to the same rules.
        problems: Everything wrong found so far.
        assets: Repository paths of the images pages use.
    """

    title: str
    summary: str
    repository: str
    branch: str
    base: str
    tabs: list[Tab]
    redirects: dict[str, str]
    home: dict[str, Any] = field(default_factory=dict)
    checked: list[Page] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    assets: set[str] = field(default_factory=set)

    @property
    def pages(self) -> list[Page]:
        """Every published page, in navigation order."""
        return [page for tab in self.tabs for page in tab.pages]

    @property
    def sections(self) -> list[tuple[str, list[Page]]]:
        """Returns: ``(label, pages)`` of every group, labelled with its tab, in order."""
        return [
            (f"{tab.title}: {group.title}", group.pages)
            for tab in self.tabs
            for group in tab.groups
        ]

    def by_source(self) -> dict[str, Page]:
        """Returns: Published pages by repository path."""
        return {page.source: page for page in self.pages}

    def problem(self, source: str, message: str) -> None:
        """
        Record a problem once, however many of the outputs meet it.

        Args:
            source: Where it is.
            message: What is wrong.
        """
        found = Problem(source, message)
        if found not in self.problems:
            self.problems.append(found)


# -- headings


def github_slug(text: str) -> str:
    """
    Make the anchor GitHub gives a heading.

    Args:
        text: The heading's plain text.

    Returns:
        Lower case, punctuation dropped (letters, digits, ``_`` and ``-`` stay), spaces as ``-``.
    """
    text = text.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text, flags=re.UNICODE)
    return text.replace(" ", "-")


def plain_text(inline: Token) -> str:
    """
    Read the text of an inline token without its markup.

    Args:
        inline: An ``inline`` token.

    Returns:
        Its text, with code spans and links reduced to what they say.
    """
    parts: list[str] = []
    for child in inline.children or []:
        if child.type in ("text", "code_inline"):
            parts.append(child.content)
        elif child.type in ("softbreak", "hardbreak"):
            parts.append(" ")
    return "".join(parts).strip()


def collect_headings(tokens: list[Token]) -> list[tuple[int, str, str]]:
    """
    List a page's headings with GitHub's anchors, repeats numbered.

    Args:
        tokens: The parsed page.

    Returns:
        ``(level, anchor, text)`` in order.
    """
    seen: dict[str, int] = {}
    found: list[tuple[int, str, str]] = []
    for index, token in enumerate(tokens):
        if token.type != "heading_open":
            continue
        text = plain_text(tokens[index + 1])
        anchor = github_slug(text)
        count = seen.get(anchor, 0)
        seen[anchor] = count + 1
        found.append((int(token.tag[1:]), anchor if count == 0 else f"{anchor}-{count}", text))
    return found


# -- loading


def parser() -> MarkdownIt:
    """Returns: The Markdown parser: CommonMark with tables and strikethrough, raw HTML allowed."""
    return MarkdownIt("commonmark", {"html": True}).enable("table").enable("strikethrough")


def _read_page(
    site: Site, md: MarkdownIt, root: Path, item: dict[str, Any], section: str, tab: str
) -> Page | None:
    """
    Parse one page the manifest lists.

    Args:
        site: The site, for its problems.
        md: The Markdown parser.
        root: The repository root.
        item: The page's entry in the manifest.
        section: The group it is in.
        tab: The tab it is in.

    Returns:
        The page, or None when its file does not exist.
    """
    source = item["source"]
    path = root / source
    if not path.is_file():
        site.problem(MANIFEST.name, f"{source} does not exist")
        return None
    text = path.read_text(encoding="utf-8")
    tokens = md.parse(text)
    headings = collect_headings(tokens)
    first = next((h for h in headings if h[0] == 1), None)
    slug = str(item.get("slug") or "")
    return Page(
        source=source,
        slug=slug,
        title=item.get("title") or (first[2] if first else slug),
        summary=" ".join(str(item.get("summary", "")).split()),
        section=section,
        type=str(item.get("type", "")),
        tab=tab,
        lang=str(item.get("lang", "en")),
        split=item.get("split"),
        split_prefix=str(item.get("split_prefix", "")),
        markdown=text,
        tokens=tokens,
        headings=headings,
    )


def _check_page_entry(site: Site, page: Page, tab: Tab, slugs: dict[str, str]) -> None:
    """
    Hold one page's entry to the manifest's rules.

    Args:
        site: The site, for its problems.
        page: The page.
        tab: Its tab.
        slugs: Routes seen so far, to the file that took each.
    """
    where = MANIFEST.name
    if not _SLUG.fullmatch(page.slug):
        site.problem(
            where, f"{page.source}: {page.slug!r} is not a route (a-z, 0-9, '.', '-', '/')"
        )
    elif page.slug != tab.slug and not page.slug.startswith(f"{tab.slug}/"):
        site.problem(
            where, f"{page.source}: the route {page.slug} is not under its tab {tab.slug}/"
        )
    if page.slug in slugs:
        site.problem(where, f"slug {page.slug!r} is used by {slugs[page.slug]} and {page.source}")
    slugs[page.slug] = page.source
    if page.type not in PAGE_TYPES:
        site.problem(
            where, f"{page.source}: type {page.type!r} is not one of {', '.join(PAGE_TYPES)}"
        )
    if page.split is not None and page.split != 2:
        site.problem(
            where, f"{page.source}: split is the heading level it is cut at, and only 2 is"
        )


def load(manifest: Path = MANIFEST, root: Path = ROOT) -> Site:
    """
    Read the manifest and parse every page it lists.

    Args:
        manifest: The site manifest (``docs/site.yaml``).
        root: The repository root.

    Returns:
        The parsed site, with its problems so far (a source that does not exist, a Markdown file
        in ``docs/`` the manifest neither lists nor excludes, a route outside its tab, a page with
        no type, a redirect or a landing-page link to a route that does not exist).
    """
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    base = str(data.get("base", "/docs/"))
    site = Site(
        title=data["title"],
        summary=" ".join(str(data["summary"]).split()),
        repository=data["repository"].rstrip("/"),
        branch=data.get("branch", "main"),
        base=base if base.endswith("/") else base + "/",
        tabs=[],
        redirects=dict(data.get("redirects") or {}),
        home=dict(data.get("home") or {}),
    )
    md = parser()
    slugs: dict[str, str] = {}
    listed: set[str] = set()
    for tab_entry in data["tabs"]:
        tab = Tab(
            title=tab_entry["title"],
            slug=tab_entry["slug"],
            summary=" ".join(str(tab_entry.get("summary", "")).split()),
            groups=[],
            footer=bool(tab_entry.get("footer", False)),
        )
        for group_entry in tab_entry["groups"]:
            group = Group(
                title=group_entry["title"], pages=[], sidebar=group_entry.get("sidebar", True)
            )
            for item in group_entry["pages"]:
                listed.add(item["source"])
                page = _read_page(site, md, root, item, group.title, tab.slug)
                if page is None:
                    continue
                _check_page_entry(site, page, tab, slugs)
                group.pages.append(page)
            tab.groups.append(group)
        site.tabs.append(tab)
    for item in data.get("checked") or []:
        page = _read_page(site, md, root, {"slug": "", **item}, "", "")
        if page is not None:
            site.checked.append(page)
    excluded = {item["source"] for item in data.get("exclude") or []}
    for path in sorted((root / "docs").glob("*.md")):
        relative = path.relative_to(root).as_posix()
        if relative not in listed and relative not in excluded:
            site.problem(
                relative,
                "is not in docs/site.yaml: list it under a tab, or under exclude with the "
                "reason it is not published",
            )
    for old, slug in site.redirects.items():
        if slug not in slugs:
            site.problem(MANIFEST.name, f"redirect {old} leads to {slug}")
        if old.strip("/") in slugs:
            site.problem(MANIFEST.name, f"redirect {old} hides the page published at that route")
    for slug in home_slugs(site.home):
        if slug not in slugs:
            site.problem(MANIFEST.name, f"home links to {slug}, which is not a route of the site")
    return site


def home_slugs(home: Mapping[str, Any]) -> list[str]:
    """
    Find every route the landing page's data names.

    Args:
        home: The ``home`` block of the manifest.

    Returns:
        Each value of a ``slug`` key, at any depth, in order.
    """
    found: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if key == "slug" and isinstance(item, str):
                    found.append(item)
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(home)
    return found


# -- the conventions


def check_conventions(site: Site, page: Page) -> None:
    """
    Hold a page to the three Markdown conventions a site draws and GitHub renders.

    An alert must be of a kind GitHub draws, and a code block may carry ``title="..."`` and
    ``tab="..."`` after its language and nothing else: GitHub would show anything else as text, and
    a site would draw nothing.

    Args:
        site: The site, for its problems.
        page: The page.
    """
    for token in page.tokens:
        if token.type == "fence" and token.info.strip():
            info = token.info.strip()
            match = _FENCE_INFO.fullmatch(info)
            if match is None:
                site.problem(
                    page.source, f"the code block ```{info} is not 'language word=\"...\"'"
                )
                continue
            for word in _FENCE_WORD.findall(match.group("words")):
                if word not in FENCE_WORDS:
                    site.problem(
                        page.source, f"the code block ```{info} has {word}=, not title= or tab="
                    )
    lines = page.markdown.splitlines()
    for token in page.tokens:
        if token.type != "blockquote_open" or token.map is None:
            continue
        match = _ALERT.fullmatch(lines[token.map[0]])
        if match and match.group("kind") not in ALERT_KINDS:
            site.problem(
                page.source,
                f"[!{match.group('kind')}] is not an alert GitHub draws ({', '.join(ALERT_KINDS)})",
            )


# -- links


@dataclass(frozen=True)
class Target:
    """
    Where a link leads.

    Attributes:
        kind: ``external``, ``anchor`` (this page), ``page``, ``file``, ``directory``, ``asset``
            or ``broken``.
        url: The link as written.
        page: The page it leads to, for ``page`` and ``anchor``.
        path: The repository path, for ``file``, ``directory`` and ``asset``.
        fragment: The anchor, without ``#``.
    """

    kind: str
    url: str
    page: Page | None = None
    path: str = ""
    fragment: str = ""


def _own_raw_path(site: Site, url: str) -> str | None:
    """
    Recognise an address of a file of this repository on GitHub, as a README has to write it.

    Args:
        site: The site.
        url: An absolute address.

    Returns:
        The repository path, or None when the address is not one of this repository's files.
    """
    owner_repo = site.repository.removeprefix("https://github.com/")
    for prefix in (
        f"https://raw.githubusercontent.com/{owner_repo}/{site.branch}/",
        f"{site.repository}/raw/{site.branch}/",
    ):
        if url.startswith(prefix):
            return url.removeprefix(prefix)
    return None


def locate(site: Site, page: Page, url: str, root: Path = ROOT) -> Target:
    """
    Find where one link of a page leads, recording a problem when it leads nowhere.

    Args:
        site: The site.
        page: The page the link is in.
        url: The link as written.
        root: The repository root.

    Returns:
        The target.
    """
    if not url:
        return Target("external", url)
    if _EXTERNAL.fullmatch(url):
        own = _own_raw_path(site, url)
        if own and own.lower().endswith(_IMAGE_SUFFIXES) and (root / own).is_file():
            site.assets.add(own)
            return Target("asset", url, path=own)
        return Target("external", url)
    path, _, fragment = url.partition("#")
    if path.startswith("/"):
        site.problem(page.source, f"{url} is an absolute path: write it relative to the file")
        return Target("broken", url)
    if not path:
        if fragment and fragment not in page.anchors:
            site.problem(page.source, f"#{fragment} is not a heading of this page")
        return Target("anchor", url, page=page, fragment=fragment)
    base = posixpath.dirname(page.source)
    target = posixpath.normpath(posixpath.join(base, path))
    if target.startswith(".."):
        site.problem(page.source, f"{url} leaves the repository")
        return Target("broken", url)
    by_source = site.by_source()
    if target in by_source:
        destination = by_source[target]
        if fragment and fragment not in destination.anchors:
            site.problem(page.source, f"{url}: {destination.source} has no heading #{fragment}")
        return Target("page", url, page=destination, fragment=fragment)
    file = root / target
    if not file.exists():
        site.problem(page.source, f"{url} does not exist")
        return Target("broken", url)
    if file.is_dir():
        return Target("directory", url, path=target)
    checked = next((p for p in site.checked if p.source == target), None)
    if checked is not None and fragment and fragment not in checked.anchors:
        site.problem(page.source, f"{url}: {checked.source} has no heading #{fragment}")
    if target.lower().endswith(_IMAGE_SUFFIXES):
        site.assets.add(target)
        return Target("asset", url, path=target)
    return Target("file", url, path=target, fragment=fragment)


def github_url(site: Site, target: Target) -> str:
    """
    Write the address of a repository file or folder on GitHub.

    Args:
        site: The site.
        target: A ``file`` or ``directory`` target.

    Returns:
        The address, with the anchor when there is one.
    """
    if target.kind == "directory":
        return f"{site.repository}/tree/{site.branch}/{target.path}"
    anchor = f"#{target.fragment}" if target.fragment else ""
    return f"{site.repository}/blob/{site.branch}/{target.path}{anchor}"


def route_from(current: Page | None, target_slug: str | None) -> str:
    """
    Make the relative URL from one page to another (or to the landing page) of the preview.

    Args:
        current: The page the link is in; None for the landing page.
        target_slug: The page linked to; None for the landing page.

    Returns:
        A relative URL ending in ``/``.
    """
    prefix = "../" * (current.depth if current is not None and current.slug else 0)
    return f"{prefix}{target_slug}/" if target_slug else (prefix or "./")


def resolve_link(site: Site, page: Page, url: str, root: Path = ROOT) -> str:
    """
    Resolve one link of a page to where it goes in the preview.

    Args:
        site: The site.
        page: The page the link is in.
        url: The link as written.
        root: The repository root.

    Returns:
        The URL to write. A problem is recorded on the site for a link that leads nowhere.
    """
    target = locate(site, page, url, root)
    if target.kind in ("external", "anchor", "broken"):
        return url
    if target.kind == "page" and target.page is not None:
        anchor = f"#{target.fragment}" if target.fragment else ""
        return route_from(page, target.page.slug) + anchor
    if target.kind == "asset":
        return "../" * page.depth + "assets/" + target.path
    return github_url(site, target)


def resolve_html(
    site: Site, page: Page, raw: str, root: Path = ROOT, resolve: Callable[[str], str] | None = None
) -> str:
    """
    Resolve the links inside a piece of raw HTML a Markdown file contains.

    Args:
        site: The site.
        page: The page it is in.
        raw: The HTML.
        root: The repository root.
        resolve: How to turn one address into another (the preview's, by default).

    Returns:
        The HTML with each ``href``, ``src`` and ``srcset`` resolved.
    """
    turn = resolve or (lambda url: resolve_link(site, page, url, root))

    def replace(match: re.Match[str]) -> str:
        url = turn(html.unescape(match.group("url")))
        return f"{match.group('attr')}={match.group('quote')}{html.escape(url, quote=True)}{match.group('quote')}"

    return _HTML_REF.sub(replace, raw)


# -- the preview


def render_page(site: Site, page: Page, md: MarkdownIt, root: Path = ROOT) -> str:
    """
    Render a page's body with its links resolved and its headings anchored.

    Args:
        site: The site.
        page: The page.
        md: The Markdown parser.
        root: The repository root.

    Returns:
        The HTML of the body.
    """
    # Rendering rewrites the tokens' links, so it works on a copy: rendering twice must not
    # resolve an address that was already resolved.
    tokens = copy.deepcopy(page.tokens)
    anchors = iter([anchor for _level, anchor, _text in page.headings])
    for token in tokens:
        if token.type == "heading_open":
            token.attrSet("id", next(anchors))
        if token.type == "inline":
            for child in token.children or []:
                if child.type == "link_open":
                    child.attrSet(
                        "href", resolve_link(site, page, child.attrGet("href") or "", root)
                    )
                elif child.type == "image":
                    child.attrSet("src", resolve_link(site, page, child.attrGet("src") or "", root))
                elif child.type == "html_inline":
                    child.content = resolve_html(site, page, child.content, root)
        elif token.type == "html_block":
            token.content = resolve_html(site, page, token.content, root)
    return md.renderer.render(tokens, md.options, {})


TEMPLATE = """\
<!doctype html>
<html lang="{{ lang }}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }}</title>
<meta name="description" content="{{ description }}">
<link rel="stylesheet" href="{{ root }}site.css">
<script defer src="{{ root }}site.js" data-root="{{ root }}"></script>
</head>
<body>
<header class="top">
  <a class="brand" href="{{ root }}">{{ site_title }}</a>
  <span class="version">{{ version }}</span>
  <form class="search" role="search">
    <input id="q" type="search" placeholder="Search the documentation" aria-label="Search the documentation" autocomplete="off">
    <ul id="results" hidden></ul>
  </form>
</header>
<div class="layout">
<nav aria-label="Documentation">
{% for section, pages in sections %}
  <h2>{{ section }}</h2>
  <ul>
  {% for p in pages %}
    <li><a href="{{ root }}{{ p.slug }}/"{% if current and p.slug == current.slug %} aria-current="page"{% endif %}>{{ p.title }}</a></li>
  {% endfor %}
  </ul>
{% endfor %}
</nav>
<main>
{{ body | safe }}
{% if current %}
<footer>
  <a href="{{ edit_url }}">Edit this page on GitHub</a> &middot; <a href="{{ root }}{{ current.slug }}.md">Markdown</a> &middot; <a href="{{ root }}llms.txt">llms.txt</a>
</footer>
{% endif %}
</main>
</div>
</body>
</html>
"""

CSS = """\
:root{color-scheme:light dark;--bg:#fff;--fg:#1c1c1e;--muted:#5b5b63;--line:#d9d9de;--accent:#5b3fd1;--code:#f3f3f6}
@media (prefers-color-scheme:dark){:root{--bg:#141417;--fg:#ececf0;--muted:#a0a0aa;--line:#2c2c33;--accent:#a08cff;--code:#1e1e24}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:var(--accent)}
.top{display:flex;gap:1rem;align-items:center;padding:.6rem 1rem;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg);z-index:2}
.brand{font-weight:600;text-decoration:none;color:var(--fg)}
.version{color:var(--muted);font:13px ui-monospace,monospace}
.search{margin-left:auto;position:relative;width:min(24rem,50%)}
.search input{width:100%;padding:.4rem .6rem;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);font:inherit}
#results{position:absolute;left:0;right:0;top:100%;margin:.25rem 0 0;padding:0;list-style:none;background:var(--bg);border:1px solid var(--line);border-radius:6px;max-height:60vh;overflow:auto}
#results li a{display:block;padding:.45rem .7rem;text-decoration:none;color:var(--fg)}
#results li a:hover,#results li a:focus{background:var(--code)}
#results small{display:block;color:var(--muted)}
.layout{display:flex;gap:2rem;max-width:80rem;margin:0 auto;padding:0 1rem}
nav{flex:0 0 14rem;padding:1rem 0;font-size:14px;position:sticky;top:3.2rem;align-self:flex-start;max-height:calc(100vh - 3.5rem);overflow:auto}
nav h2{font-size:13px;color:var(--muted);margin:1rem 0 .3rem}
nav ul{list-style:none;margin:0;padding:0}
nav a{display:block;padding:.15rem .5rem;border-left:2px solid transparent;text-decoration:none;color:var(--fg)}
nav a[aria-current=page]{border-left-color:var(--accent);font-weight:600}
main{flex:1;min-width:0;padding:1rem 0 4rem;max-width:52rem}
pre{background:var(--code);padding:.8rem 1rem;border-radius:6px;overflow:auto;font-size:13.5px;line-height:1.45}
code{background:var(--code);padding:.1em .3em;border-radius:4px;font-size:.9em}
pre code{background:none;padding:0}
table{border-collapse:collapse;display:block;overflow:auto;max-width:100%}
th,td{border:1px solid var(--line);padding:.35rem .6rem;text-align:left;vertical-align:top}
blockquote{margin:1rem 0;padding:.1rem 1rem;border-left:3px solid var(--line);color:var(--muted)}
img{max-width:100%;height:auto}
h1,h2,h3{line-height:1.25}
footer{margin-top:3rem;padding-top:1rem;border-top:1px solid var(--line);color:var(--muted);font-size:14px}
@media (max-width:48rem){.layout{flex-direction:column;gap:0}nav{position:static;max-height:none;flex:none}.search{width:100%}}
"""

JS = """\
(function () {
  var root = document.currentScript.getAttribute('data-root') || './';
  var input = document.getElementById('q');
  var list = document.getElementById('results');
  var index = null;
  input.form.addEventListener('submit', function (ev) { ev.preventDefault(); });
  function load() {
    if (index) return Promise.resolve(index);
    return fetch(root + 'search-index.json').then(function (r) { return r.json(); }).then(function (d) { index = d; return d; });
  }
  function score(entry, words) {
    var title = entry.title.toLowerCase(), text = entry.text.toLowerCase(), total = 0;
    for (var i = 0; i < words.length; i++) {
      var w = words[i], inTitle = title.indexOf(w) >= 0, inText = text.indexOf(w) >= 0;
      if (!inTitle && !inText) return 0;
      total += (inTitle ? 5 : 0) + (inText ? 1 : 0);
    }
    return total;
  }
  function show(items) {
    list.textContent = '';
    items.forEach(function (e) {
      var li = document.createElement('li'), a = document.createElement('a'), s = document.createElement('small');
      a.href = root + e.url; a.textContent = e.title; s.textContent = e.page;
      a.appendChild(s); li.appendChild(a); list.appendChild(li);
    });
    list.hidden = items.length === 0;
  }
  input.addEventListener('input', function () {
    var words = input.value.toLowerCase().split(/\\s+/).filter(Boolean);
    if (!words.length) { show([]); return; }
    load().then(function (entries) {
      var scored = [];
      entries.forEach(function (e) { var s = score(e, words); if (s) scored.push([s, e]); });
      scored.sort(function (a, b) { return b[0] - a[0]; });
      show(scored.slice(0, 10).map(function (x) { return x[1]; }));
    });
  });
  input.addEventListener('keydown', function (ev) { if (ev.key === 'Escape') { input.value = ''; show([]); } });
  document.addEventListener('keydown', function (ev) {
    if (ev.key === '/' && document.activeElement !== input && !/input|textarea/i.test(document.activeElement.tagName)) {
      ev.preventDefault(); input.focus();
    }
  });
})();
"""


def search_entries(site: Site, page: Page) -> list[dict[str, str]]:
    """
    Cut a page into the sections the preview's search finds.

    Args:
        site: The site (unused beyond the title; kept so the signature mirrors the other builders).
        page: The page.

    Returns:
        One entry per heading of level 2 or 3, and one for the page itself: ``title``, ``page``,
        ``url`` (a route with an anchor) and ``text`` (the first lines under it).
    """
    entries: list[dict[str, str]] = []
    current_title = page.title
    current_url = f"{page.slug}/"
    buffer: list[str] = []

    def flush() -> None:
        text = " ".join(" ".join(buffer).split())[:SEARCH_TEXT_LENGTH]
        if text or current_title:
            entries.append(
                {"title": current_title, "page": page.title, "url": current_url, "text": text}
            )
        buffer.clear()

    anchors = iter([a for _l, a, _t in page.headings])
    tokens = page.tokens
    for index, token in enumerate(tokens):
        if token.type == "heading_open":
            anchor = next(anchors)
            level = int(token.tag[1:])
            if level in (2, 3):
                flush()
                current_title = f"{page.title}: {plain_text(tokens[index + 1])}"
                current_url = f"{page.slug}/#{anchor}"
        elif token.type == "inline" and index > 0 and tokens[index - 1].type != "heading_open":
            buffer.append(plain_text(token))
        elif token.type == "fence":
            buffer.append(token.content)
    flush()
    return entries


def build(
    site: Site,
    out: Path,
    version: str,
    root: Path = ROOT,
) -> None:
    """
    Write one version of the preview and refresh the files that span versions.

    Args:
        site: The parsed site.
        out: The output directory (it may already hold other versions).
        version: ``<major>.<minor>``.
        root: The repository root.
    """
    target = out / version
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    md = parser()
    env = Environment(autoescape=select_autoescape(["html"]))
    template = env.from_string(TEMPLATE)
    sections = site.sections

    def page_html(body: str, current: Page | None, title: str, description: str, depth: int) -> str:
        return template.render(
            title=title,
            description=description,
            lang=current.lang if current else "en",
            root="../" * depth or "./",
            site_title=site.title,
            version=version,
            sections=sections,
            current=current,
            body=body,
            edit_url=f"{site.repository}/blob/{site.branch}/{current.source}" if current else "",
        )

    search: list[dict[str, str]] = []
    for page in site.pages:
        check_conventions(site, page)
        body = render_page(site, page, md, root)
        page.html = body
        directory = target / page.slug
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "index.html").write_text(
            page_html(
                body, page, f"{page.title} - {site.title}", page.summary or site.summary, page.depth
            ),
            encoding="utf-8",
        )
        (target / f"{page.slug}.md").write_text(page.markdown, encoding="utf-8")
        search += search_entries(site, page)
    for page in site.checked:
        render_page(site, page, md, root)

    landing = ["<h1>" + html.escape(site.title) + "</h1>", f"<p>{html.escape(site.summary)}</p>"]
    for section, pages in sections:
        landing.append(f"<h2>{html.escape(section)}</h2><ul>")
        for page in pages:
            summary = f" &mdash; {html.escape(page.summary)}" if page.summary else ""
            landing.append(
                f'<li><a href="{page.slug}/">{html.escape(page.title)}</a>{summary}</li>'
            )
        landing.append("</ul>")
    (target / "index.html").write_text(
        page_html("\n".join(landing), None, site.title, site.summary, 0), encoding="utf-8"
    )
    (target / "404.html").write_text(
        page_html(
            "<h1>Page not found</h1><p>Try the search above, or start from the "
            '<a href="./">index</a>.</p>',
            None,
            f"Not found - {site.title}",
            site.summary,
            0,
        ),
        encoding="utf-8",
    )
    (target / "site.css").write_text(CSS, encoding="utf-8")
    (target / "site.js").write_text(JS, encoding="utf-8")
    (target / "search-index.json").write_text(
        json.dumps(search, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    for asset in sorted(site.assets):
        destination = target / "assets" / asset
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / asset, destination)
    write_spanning_files(site, out)


def write_spanning_files(site: Site, out: Path) -> None:
    """
    Write what spans versions of the preview: the list, ``latest``, the top page and redirects.

    Args:
        site: The site (for its redirects).
        out: The output directory.
    """
    versions = sorted(
        (p.name for p in out.iterdir() if p.is_dir() and VERSION_DIR.fullmatch(p.name)),
        key=lambda name: tuple(int(part) for part in name.split(".")),
        reverse=True,
    )
    latest = versions[0]
    (out / "versions.json").write_text(
        json.dumps({"latest": latest, "versions": versions}, indent=2) + "\n", encoding="utf-8"
    )
    redirect = (
        '<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url={url}">'
        '<link rel="canonical" href="{url}"><title>Redirecting</title>'
        '<a href="{url}">Go to the documentation</a>\n'
    )
    (out / "latest").mkdir(exist_ok=True)
    (out / "latest" / "index.html").write_text(
        redirect.format(url=f"../{latest}/"), encoding="utf-8"
    )
    (out / "index.html").write_text(redirect.format(url=f"{latest}/"), encoding="utf-8")
    rules = [f"/latest/* /{latest}/:splat 302", f"/ /{latest}/ 302"]
    rules += [f"/{old.strip('/')}/ /{latest}/{slug}/ 301" for old, slug in site.redirects.items()]
    (out / "_redirects").write_text("\n".join(rules) + "\n", encoding="utf-8")
    (out / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")


# -- the package


@dataclass
class Part:
    """
    A page as the package publishes it: a whole source page, or one section of a split one.

    Attributes:
        page: The source page it comes from.
        slug: Its route.
        title: Its title.
        summary: Its sentence.
        markdown: Its text as written (a split part's headings raised so it opens with a title).
        headings: ``(level, anchor, text)`` as the part itself numbers them.
        anchors: The source page's anchor of each heading the part holds, to the part's own.
        parent: The route of the page it was cut from, for a split part.
    """

    page: Page
    slug: str
    title: str
    summary: str
    markdown: str
    headings: list[tuple[int, str, str]]
    anchors: dict[str, str]
    parent: str | None = None


def _first_sentence(tokens: list[Token]) -> str:
    """
    Read a part's first paragraph as its summary.

    Args:
        tokens: The part, parsed.

    Returns:
        Its first sentence, cut at ``SUMMARY_LENGTH`` characters; empty when it has no paragraph.
    """
    for index, token in enumerate(tokens):
        if token.type == "paragraph_open":
            text = " ".join(plain_text(tokens[index + 1]).split())
            sentence = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
            if len(sentence) > SUMMARY_LENGTH:
                sentence = sentence[: SUMMARY_LENGTH - 3].rsplit(" ", 1)[0] + "..."
            return sentence
    return ""


def split_page(page: Page, md: MarkdownIt) -> list[Part]:
    """
    Cut a page into the parts the package publishes.

    Args:
        page: The page.
        md: The Markdown parser.

    Returns:
        One part for a page that is not split. For a split one, the text before its first heading
        of the split level (at the page's own route), then one part per heading of that level, at
        ``<route>/<anchor>``, with every heading inside raised so the part opens with a title.
    """
    if page.split is None:
        return [
            Part(
                page=page,
                slug=page.slug,
                title=page.title,
                summary=page.summary,
                markdown=page.markdown,
                headings=list(page.headings),
                anchors={anchor: anchor for _l, anchor, _t in page.headings},
            )
        ]
    lines = page.markdown.splitlines(keepends=True)
    heading_lines = [
        token.map[0]
        for token in page.tokens
        if token.type == "heading_open" and token.map is not None
    ]
    cuts = [
        line
        for line, (level, _anchor, _text) in zip(heading_lines, page.headings, strict=True)
        if level == page.split
    ]
    bounds = [0, *cuts, len(lines)]
    parts: list[Part] = []
    raise_by = page.split - 1
    for number, (start, end) in enumerate(itertools.pairwise(bounds)):
        if start == end:
            continue
        chunk = lines[start:end]
        inside = [
            (index, heading)
            for index, (line, heading) in enumerate(zip(heading_lines, page.headings, strict=True))
            if start <= line < end
        ]
        if number > 0:
            raised = {heading_lines[i] - start for i, _h in inside}
            chunk = [
                line[raise_by:] if offset in raised and line.startswith("#" * page.split) else line
                for offset, line in enumerate(chunk)
            ]
        text = "".join(chunk)
        tokens = md.parse(text)
        local = collect_headings(tokens)
        anchors = {
            heading[1]: own[1] for (_index, heading), own in zip(inside, local, strict=False)
        }
        if number == 0:
            parts.append(
                Part(page, page.slug, page.title, page.summary, text, local, anchors, None)
            )
            continue
        _level, anchor, title = inside[0][1]
        segment = anchor.removeprefix(page.split_prefix) or anchor
        parts.append(
            Part(
                page=page,
                slug=f"{page.slug}/{segment}",
                title=title,
                summary=_first_sentence(tokens),
                markdown=text,
                headings=local,
                anchors=anchors,
                parent=page.slug,
            )
        )
    return parts


def _code_spans(line: str) -> list[tuple[bool, str]]:
    """
    Cut a line into what is inside a code span and what is not.

    Args:
        line: One line of Markdown.

    Returns:
        ``(is_code, text)`` pieces that join back into the line.
    """
    pieces: list[tuple[bool, str]] = []
    position = 0
    for match in re.finditer(r"`+", line):
        if match.start() < position:
            continue
        run = match.group()
        close = re.compile(rf"(?<!`){run}(?!`)").search(line, match.end())
        if close is None:
            continue
        pieces.append((False, line[position : match.start()]))
        pieces.append((True, line[match.start() : close.end()]))
        position = close.end()
    pieces.append((False, line[position:]))
    return pieces


def rewrite_links(markdown: str, tokens: list[Token], turn: Callable[[str], str]) -> str:
    """
    Rewrite every link and image address of a Markdown text, leaving code alone.

    Args:
        markdown: The text.
        tokens: The text, parsed (code blocks are found by their lines).
        turn: How to turn one address into another.

    Returns:
        The text with inline links, images, reference definitions and the ``href``, ``src`` and
        ``srcset`` of raw HTML rewritten; everything else byte for byte as it was.
    """
    lines = markdown.splitlines(keepends=True)
    code = {
        number
        for token in tokens
        if token.type in ("fence", "code_block") and token.map is not None
        for number in range(token.map[0], token.map[1])
    }

    def inline(match: re.Match[str]) -> str:
        url = match.group("url")
        if url.startswith("<") and url.endswith(">"):
            return f"<{turn(url[1:-1])}>"
        return turn(url)

    def definition(match: re.Match[str]) -> str:
        url = match.group("url")
        bare = url[1:-1] if url.startswith("<") and url.endswith(">") else url
        return match.group("lead") + turn(bare)

    def attribute(match: re.Match[str]) -> str:
        written = html.unescape(match.group("url"))
        url = turn(written)
        if url == written:
            # Left as written, entities and all: the text is the author's, not ours to re-escape.
            return match.group(0)
        return f"{match.group('attr')}={match.group('quote')}{html.escape(url, quote=True)}{match.group('quote')}"

    for number, line in enumerate(lines):
        if number in code:
            continue
        if _DEFINITION.fullmatch(line.rstrip("\n")):
            lines[number] = _DEFINITION.sub(definition, line, count=1)
            continue
        pieces = []
        for is_code, text in _code_spans(line):
            if not is_code:
                text = _INLINE_URL.sub(inline, text)
                text = _HTML_REF.sub(attribute, text)
            pieces.append(text)
        lines[number] = "".join(pieces)
    return "".join(lines)


def _addresses(tokens: list[Token]) -> list[str]:
    """
    Every address a parsed text links to.

    Args:
        tokens: The parsed text.

    Returns:
        Link targets, image sources and the addresses in raw HTML.
    """
    found: list[str] = []
    for token in tokens:
        children = token.children or []
        if token.type == "html_block":
            found += [m.group("url") for m in _HTML_REF.finditer(token.content)]
        for child in children:
            if child.type == "link_open":
                found.append(str(child.attrGet("href") or ""))
            elif child.type == "image":
                found.append(str(child.attrGet("src") or ""))
            elif child.type == "html_inline":
                found += [m.group("url") for m in _HTML_REF.finditer(child.content)]
    return found


class Package:
    """
    The pages of the site as the package publishes them, and how a link reaches each.

    Args:
        site: The parsed site.
        md: The Markdown parser.
    """

    def __init__(self, site: Site, md: MarkdownIt) -> None:
        self.site = site
        self.parts: list[Part] = []
        self.by_source: dict[str, list[Part]] = {}
        for page in site.pages:
            parts = split_page(page, md)
            self.parts += parts
            self.by_source[page.source] = parts
        counts = Counter(part.slug for part in self.parts)
        for slug in sorted(slug for slug, count in counts.items() if count > 1):
            site.problem(MANIFEST.name, f"two pages are published at {slug}")

    def url(self, slug: str) -> str:
        """
        Args:
            slug: A route.

        Returns:
            Its address under the site's base.
        """
        return f"{self.site.base}{slug}/"

    def holder(self, page: Page, fragment: str) -> tuple[Part, str]:
        """
        Find the part of a page that holds a heading.

        Args:
            page: The page.
            fragment: A source anchor of it, or empty for the page itself.

        Returns:
            The part and the anchor it gives the heading (empty for none).
        """
        parts = self.by_source[page.source]
        if fragment:
            for part in parts:
                if fragment in part.anchors:
                    return part, part.anchors[fragment]
        return parts[0], fragment

    def address(self, part: Part, url: str, root: Path) -> str:
        """
        Resolve one link of a part to the address the package writes.

        Args:
            part: The part the link is in.
            url: The link as written.
            root: The repository root.

        Returns:
            An absolute route under the base, a GitHub address, an asset under the base, an
            anchor of the part itself, or the link unchanged when it is external or broken.
        """
        target = locate(self.site, part.page, url, root)
        if target.kind in ("external", "broken"):
            return url
        if target.kind in ("anchor", "page") and target.page is not None:
            holder, anchor = self.holder(target.page, target.fragment)
            suffix = f"#{anchor}" if anchor else ""
            if holder is part and target.kind == "anchor":
                return suffix or url
            return self.url(holder.slug) + suffix
        if target.kind == "asset":
            return f"{self.site.base}assets/{target.path}"
        return github_url(self.site, target)


def listed_sources(manifest: Path = MANIFEST) -> list[str]:
    """
    Args:
        manifest: The site manifest.

    Returns:
        The repository path of every page it lists, without parsing them.
    """
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    return [
        item["source"] for tab in data["tabs"] for group in tab["groups"] for item in group["pages"]
    ]


def git_dates(root: Path, sources: list[str]) -> dict[str, str]:
    """
    Read the date of the last commit of each source, from one pass over the history.

    Args:
        root: The repository root.
        sources: Repository paths.

    Returns:
        ``YYYY-MM-DD`` by path; empty when git or the history is not there (an sdist).
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "log", "--format=%x00%cs", "--name-only", "--", *sources],
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    dates: dict[str, str] = {}
    current = ""
    wanted = set(sources)
    for line in result.stdout.splitlines():
        if line.startswith("\x00"):
            current = line[1:]
        elif line in wanted and line not in dates:
            dates[line] = current
    return dates


def emit(
    site: Site,
    out: Path,
    version: str,
    root: Path = ROOT,
    dates: Mapping[str, str] | None = None,
) -> Package:
    """
    Write the package a documentation site paints.

    Args:
        site: The parsed site.
        out: The directory to write it into (replaced).
        version: ``<major>.<minor>``.
        root: The repository root.
        dates: Date of the last commit of each source, when known.

    Returns:
        The package, with the site's problems recorded.
    """
    if out.exists():
        shutil.rmtree(out)
    (out / "pages").mkdir(parents=True)
    md = parser()
    package = Package(site, md)
    dates = dates or {}
    written: dict[str, str] = {}
    for page in site.pages:
        check_conventions(site, page)
    for part in package.parts:

        def turn(url: str, part: Part = part) -> str:
            return package.address(part, url, root)

        text = rewrite_links(part.markdown, md.parse(part.markdown), turn)
        for address in _addresses(md.parse(text)):
            if not (
                not address
                or address.startswith(("#", site.base, site.repository))
                or _EXTERNAL.fullmatch(address)
            ):
                site.problem(part.page.source, f"the link {address} could not be resolved")
        written[part.slug] = text
        destination = out / "pages" / f"{part.slug}.md"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    for page in site.checked:
        for address in _addresses(page.tokens):
            locate(site, page, address, root)
    for asset in sorted(site.assets):
        destination = out / "assets" / asset
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / asset, destination)
    manifest = package_manifest(site, package, version, dates)
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    redirects = [
        {"from": f"{site.base}{old.strip('/')}/", "to": package.url(slug), "status": 301}
        for old, slug in site.redirects.items()
    ]
    (out / "redirects.json").write_text(
        json.dumps({"redirects": redirects}, indent=1) + "\n", encoding="utf-8"
    )
    (out / "llms.txt").write_text(package_llms_txt(site, package, version), encoding="utf-8")
    (out / "llms-full.txt").write_text(package_llms_full(site, package, written), encoding="utf-8")
    return package


def _neighbour(package: Package, part: Part | None) -> dict[str, str] | None:
    """
    Args:
        package: The package.
        part: A part, or None.

    Returns:
        The route, title and address of a previous or next page, or None.
    """
    if part is None:
        return None
    return {"slug": part.slug, "title": part.title, "url": package.url(part.slug)}


def package_manifest(
    site: Site, package: Package, version: str, dates: Mapping[str, str]
) -> dict[str, Any]:
    """
    Describe the package: the tabs, their groups and every page, in reading order.

    Args:
        site: The site.
        package: Its parts.
        version: ``<major>.<minor>``.
        dates: Date of the last commit of each source.

    Returns:
        The content of ``manifest.json``.
    """
    tabs: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    for tab in site.tabs:
        groups: list[dict[str, Any]] = []
        order = [part for page in tab.pages for part in package.by_source[page.source]]
        for group in tab.groups:
            slugs = [part.slug for page in group.pages for part in package.by_source[page.source]]
            groups.append({"title": group.title, "sidebar": group.sidebar, "pages": slugs})
        for index, part in enumerate(order):
            page = part.page
            children = [p.slug for p in package.by_source[page.source] if p.parent == part.slug]
            report = urllib.parse.urlencode(
                {
                    "title": f"Documentation: {part.title}",
                    "body": f"Page: {package.url(part.slug)}\nSource: {page.source}\n\n",
                }
            )
            pages.append(
                {
                    "slug": part.slug,
                    "url": package.url(part.slug),
                    "markdown": f"pages/{part.slug}.md",
                    "title": part.title,
                    "summary": part.summary,
                    "type": page.type,
                    "lang": page.lang,
                    "tab": tab.slug,
                    "group": page.section,
                    "source": page.source,
                    "edit_url": f"{site.repository}/edit/{site.branch}/{page.source}",
                    "report_url": f"{site.repository}/issues/new?{report}",
                    "last_updated": dates.get(page.source),
                    "headings": [
                        {"level": level, "anchor": anchor, "text": text}
                        for level, anchor, text in part.headings
                        if level > 1
                    ],
                    "parent": part.parent,
                    "children": children,
                    "previous": _neighbour(package, order[index - 1] if index > 0 else None),
                    "next": _neighbour(
                        package, order[index + 1] if index + 1 < len(order) else None
                    ),
                }
            )
        first = order[0].slug if order else None
        tabs.append(
            {
                "title": tab.title,
                "slug": tab.slug,
                "summary": tab.summary,
                "footer": tab.footer,
                "url": package.url(first) if first else None,
                "groups": groups,
            }
        )
    return {
        "format": PACKAGE_FORMAT,
        "title": site.title,
        "summary": site.summary,
        "version": version,
        "base": site.base,
        "repository": site.repository,
        "branch": site.branch,
        "home": site.home,
        "tabs": tabs,
        "pages": pages,
    }


def package_llms_txt(site: Site, package: Package, version: str) -> str:
    """
    Write the llms.txt index of the package: what the project is and every page with its summary.

    Args:
        site: The site.
        package: Its parts.
        version: The version label.

    Returns:
        The file's text.
    """
    lines = [f"# {site.title}", "", f"> {site.summary}", ""]
    for tab in site.tabs:
        lines += [f"## {tab.title}", ""]
        for page in tab.pages:
            for part in package.by_source[page.source]:
                summary = f": {part.summary}" if part.summary else ""
                lines.append(f"- [{part.title}]({site.base}{part.slug}.md){summary}")
        lines.append("")
    lines.append(
        f"Version {version}. Every page is also available as Markdown at its address with `.md`."
    )
    return "\n".join(lines) + "\n"


def package_llms_full(site: Site, package: Package, written: Mapping[str, str]) -> str:
    """
    Concatenate every page of the package, each under a marker saying where it came from.

    Args:
        site: The site.
        package: Its parts.
        written: The Markdown written for each route.

    Returns:
        The file's text.
    """
    chunks = [f"# {site.title}\n\n> {site.summary}\n"]
    for part in package.parts:
        chunks.append(
            f"\n<!-- {site.base}{part.slug}.md (from {part.page.source}) -->\n\n"
            f"{written[part.slug].rstrip()}\n"
        )
    return "".join(chunks)


# -- running it


def current_version(root: Path = ROOT) -> str:
    """
    Read ``<major>.<minor>`` from ``pyproject.toml``, the version's one source.

    Args:
        root: The repository root.

    Returns:
        The minor line, such as ``3.4``.

    Raises:
        ValueError: The project version cannot be read.
    """
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"(\d+)\.(\d+)\.', text, re.MULTILINE)
    if match is None:
        raise ValueError("pyproject.toml has no [project] version")
    return f"{match.group(1)}.{match.group(2)}"


def build_site(
    out: Path,
    version: str,
    root: Path = ROOT,
    package: Path | None = None,
    dates: Mapping[str, str] | None = None,
) -> Site:
    """
    Load the manifest, build the preview (and the package) and return the site with its problems.

    Args:
        out: The preview's output directory.
        version: ``<major>.<minor>``.
        root: The repository root.
        package: Where to write the package; None for none.
        dates: Date of the last commit of each source, for the package.

    Returns:
        The site; ``site.problems`` is empty when the documentation is sound.
    """
    site = load(root / "docs" / "site.yaml", root)
    build(site, out, version, root)
    if package is not None:
        emit(site, package, version, root, dates)
    return site


def main(argv: list[str] | None = None) -> int:
    """
    Run the builder.

    Args:
        argv: The arguments after the program name.

    Returns:
        0 when the documentation is sound, 1 when it has problems, 2 for a usage error.
    """
    args = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    args.add_argument("--out", type=Path, default=DEFAULT_OUT, help="where to write the preview")
    args.add_argument("--emit", type=Path, help="where to write the package a site paints")
    args.add_argument("--version", help="the minor line, such as 3.4 (default: pyproject.toml's)")
    args.add_argument("--check", action="store_true", help="build both into a temporary directory")
    options = args.parse_args(argv)
    version = options.version or current_version()
    if not VERSION_DIR.fullmatch(version):
        print(f"error: {version!r} is not a <major>.<minor> version", file=sys.stderr)
        return 2
    if options.check:
        with tempfile.TemporaryDirectory() as scratch:
            site = build_site(Path(scratch) / "site", version, ROOT, Path(scratch) / "package", {})
    else:
        dates = git_dates(ROOT, listed_sources(MANIFEST)) if options.emit is not None else None
        site = build_site(options.out, version, ROOT, options.emit, dates)
    for problem in site.problems:
        print(problem, file=sys.stderr)
    if site.problems:
        print(f"{len(site.problems)} problem(s) in the documentation", file=sys.stderr)
        return 1
    if options.check:
        print(f"{len(site.pages)} pages checked")
    else:
        print(f"{len(site.pages)} pages built into {options.out / version}")
        if options.emit is not None:
            print(f"The package a site paints is in {options.emit}")
    return 0


def pages_as_json(site: Site) -> list[dict[str, Any]]:
    """
    Describe the pages as plain data (for tests and tools).

    Args:
        site: The site.

    Returns:
        ``slug``, ``source``, ``title``, ``section``, ``type`` and ``anchors`` of each page.
    """
    return [
        {
            "slug": p.slug,
            "source": p.source,
            "title": p.title,
            "section": p.section,
            "type": p.type,
            "anchors": sorted(p.anchors),
        }
        for p in site.pages
    ]


if __name__ == "__main__":
    sys.exit(main())
