#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Build the documentation site from ``docs/``: static, searchable, versioned, machine-readable.

The Markdown in ``docs/`` (and the README) is the one source (checklist N-17). This turns it into a
site anybody can host, or open from disk, **without publishing anything**: nothing here knows a
domain, and nothing is uploaded. The CI job ``docs`` builds it and fails when a link, an anchor or
a generated reference is wrong; the artifact is there for the day the owner decides to publish.

What it writes under ``--out`` (``build/docs-site`` by default):

- ``<version>/<slug>/index.html``: one page per entry of ``docs/site.yaml``, with the navigation, a
  local search box and a link to edit the source. The slugs are the stable routes; renaming one
  needs an entry under ``redirects`` in the manifest.
- ``<version>/<slug>.md``: the page's Markdown, as written, for readers that are not browsers.
- ``<version>/llms.txt`` and ``llms-full.txt``: the index and the whole text, in the format
  <https://llmstxt.org> describes.
- ``<version>/search-index.json`` and ``site.js``: the local search, no service and no CDN.
- ``<version>/assets/``: only the images the pages use.
- ``versions.json``, ``latest/`` (a redirect), ``index.html`` and ``_redirects`` at the top, rebuilt
  from every ``<major>.<minor>`` folder already in ``--out``, so building 3.5 beside 3.4 keeps both.

Links are written the way GitHub renders them (``compose.md#the-relay``, ``../SECURITY.md``) and are
resolved here: a link to a page in the site becomes a relative link to its route, a link to another
file of the repository becomes a link to that file on GitHub, and one that leads nowhere is an
error, as is an anchor the target page does not have. Heading anchors follow GitHub's rules, so
the links already written keep working.

Usage:
    scripts/build_docs.py                    Build the current version into build/docs-site.
    scripts/build_docs.py --check            Build into a temporary directory and report problems.
    scripts/build_docs.py --version 3.4 --out public
"""

from __future__ import annotations

import argparse
import copy
import html
import json
import posixpath
import re
import shutil
import sys
import tempfile
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

#: A search entry keeps this much of a section's text.
SEARCH_TEXT_LENGTH = 400

#: ``<major>.<minor>`` folders of a built site.
VERSION_DIR = re.compile(r"^\d+\.\d+$")


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
    One page of the site.

    Attributes:
        source: Path of the Markdown file, relative to the repository.
        slug: The stable route.
        title: The page's title.
        summary: One sentence, for the index and the search.
        section: The navigation section it is under.
        markdown: The text as written.
        tokens: The parsed Markdown.
        headings: ``(level, anchor, text)`` of each heading, in order.
        html: The rendered body (links resolved).
    """

    source: str
    slug: str
    title: str
    summary: str
    section: str
    markdown: str = ""
    tokens: list[Token] = field(default_factory=list)
    headings: list[tuple[int, str, str]] = field(default_factory=list)
    html: str = ""

    @property
    def anchors(self) -> set[str]:
        """Every anchor the page defines."""
        return {anchor for _level, anchor, _text in self.headings}


@dataclass
class Site:
    """
    The whole documentation, parsed.

    Attributes:
        title: The site's name.
        summary: What the project is, for llms.txt.
        repository: The GitHub repository's URL.
        branch: The branch its files are linked on.
        sections: ``(title, pages)`` in navigation order.
        redirects: Old route to slug.
        problems: Everything wrong found so far.
        assets: Repository path to wanted, the images pages use.
    """

    title: str
    summary: str
    repository: str
    branch: str
    sections: list[tuple[str, list[Page]]]
    redirects: dict[str, str]
    problems: list[Problem] = field(default_factory=list)
    assets: set[str] = field(default_factory=set)

    @property
    def pages(self) -> list[Page]:
        """Every page, in navigation order."""
        return [page for _title, pages in self.sections for page in pages]

    def by_source(self) -> dict[str, Page]:
        """Returns: Pages by repository path."""
        return {page.source: page for page in self.pages}


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


def load(manifest: Path = MANIFEST, root: Path = ROOT) -> Site:
    """
    Read the manifest and parse every page it lists.

    Args:
        manifest: The site manifest (``docs/site.yaml``).
        root: The repository root.

    Returns:
        The parsed site, with its problems so far (a source that does not exist, a Markdown file
        in ``docs/`` the manifest neither lists nor excludes).
    """
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    site = Site(
        title=data["title"],
        summary=data["summary"].strip(),
        repository=data["repository"].rstrip("/"),
        branch=data.get("branch", "main"),
        sections=[],
        redirects=dict(data.get("redirects") or {}),
    )
    md = parser()
    slugs: dict[str, str] = {}
    listed: set[str] = set()
    for entry in data["sections"]:
        pages: list[Page] = []
        for item in entry["pages"]:
            source = item["source"]
            listed.add(source)
            path = root / source
            if not path.is_file():
                site.problems.append(Problem(str(manifest.name), f"{source} does not exist"))
                continue
            slug = item["slug"]
            if slug in slugs:
                site.problems.append(
                    Problem(
                        str(manifest.name), f"slug {slug!r} is used by {slugs[slug]} and {source}"
                    )
                )
            slugs[slug] = source
            text = path.read_text(encoding="utf-8")
            tokens = md.parse(text)
            headings = collect_headings(tokens)
            first = next((h for h in headings if h[0] == 1), None)
            pages.append(
                Page(
                    source=source,
                    slug=slug,
                    title=item.get("title") or (first[2] if first else slug),
                    summary=" ".join(str(item.get("summary", "")).split()),
                    section=entry["title"],
                    markdown=text,
                    tokens=tokens,
                    headings=headings,
                )
            )
        site.sections.append((entry["title"], pages))
    excluded = {item["source"] for item in data.get("exclude") or []}
    for path in sorted((root / "docs").glob("*.md")):
        relative = path.relative_to(root).as_posix()
        if relative not in listed and relative not in excluded:
            site.problems.append(
                Problem(
                    relative,
                    "is not in docs/site.yaml: list it under a section, or under exclude with the "
                    "reason it is not published",
                )
            )
    for old, slug in site.redirects.items():
        if slug not in slugs:
            site.problems.append(Problem(str(manifest.name), f"redirect {old} leads to {slug}"))
    return site


# -- links


def route_from(current: Page | None, target_slug: str | None) -> str:
    """
    Make the relative URL from one page to another (or to the landing page).

    Args:
        current: The page the link is in; None for the landing page.
        target_slug: The page linked to; None for the landing page.

    Returns:
        A relative URL ending in ``/``.
    """
    here = [] if current is None else [current.slug]
    prefix = "../" * len(here)
    return f"{prefix}{target_slug}/" if target_slug else (prefix or "./")


_EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.\-]*:|//)", re.IGNORECASE)
_HTML_REF = re.compile(r"""(?P<attr>\b(?:href|src))=(?P<quote>["'])(?P<url>[^"']*)(?P=quote)""")


def resolve_link(site: Site, page: Page, url: str, root: Path = ROOT) -> str:
    """
    Resolve one link of a page to where it goes in the site.

    Args:
        site: The site.
        page: The page the link is in.
        url: The link as written.
        root: The repository root.

    Returns:
        The URL to write. A problem is recorded on the site for a link that leads nowhere.
    """
    if not url or _EXTERNAL.match(url):
        return url
    path, _, fragment = url.partition("#")
    if not path:
        if fragment and fragment not in page.anchors:
            site.problems.append(Problem(page.source, f"#{fragment} is not a heading of this page"))
        return url
    base = posixpath.dirname(page.source)
    target = posixpath.normpath(posixpath.join(base, path))
    if target.startswith(".."):
        site.problems.append(Problem(page.source, f"{url} leaves the repository"))
        return url
    by_source = site.by_source()
    if target in by_source:
        destination = by_source[target]
        if fragment and fragment not in destination.anchors:
            site.problems.append(
                Problem(page.source, f"{url}: {destination.source} has no heading #{fragment}")
            )
        return route_from(page, destination.slug) + (f"#{fragment}" if fragment else "")
    file = root / target
    if not file.exists():
        site.problems.append(Problem(page.source, f"{url} does not exist"))
        return url
    if file.is_dir():
        return f"{site.repository}/tree/{site.branch}/{target}"
    if target.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")):
        site.assets.add(target)
        return "../" + "assets/" + target
    return f"{site.repository}/blob/{site.branch}/{target}" + (f"#{fragment}" if fragment else "")


def resolve_html(site: Site, page: Page, raw: str, root: Path = ROOT) -> str:
    """
    Resolve the links inside a piece of raw HTML a Markdown file contains.

    Args:
        site: The site.
        page: The page it is in.
        raw: The HTML.
        root: The repository root.

    Returns:
        The HTML with each ``href`` and ``src`` resolved.
    """

    def replace(match: re.Match[str]) -> str:
        url = resolve_link(site, page, html.unescape(match.group("url")), root)
        return f"{match.group('attr')}={match.group('quote')}{html.escape(url, quote=True)}{match.group('quote')}"

    return _HTML_REF.sub(replace, raw)


# -- rendering


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
<html lang="en">
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
  <form class="search" role="search" onsubmit="return false">
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
nav h2{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:1rem 0 .3rem}
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
    Cut a page into the sections the search finds.

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


def llms_txt(site: Site, version: str) -> str:
    """
    Write the llms.txt index: what the project is and every page with its summary.

    Args:
        site: The site.
        version: The version label.

    Returns:
        The file's text.
    """
    lines = [f"# {site.title}", "", f"> {site.summary}", ""]
    for section, pages in site.sections:
        lines += [f"## {section}", ""]
        for page in pages:
            summary = f": {page.summary}" if page.summary else ""
            lines.append(f"- [{page.title}]({page.slug}.md){summary}")
        lines.append("")
    lines.append(
        f"Version {version}. Every page is also available as Markdown at its address with `.md`."
    )
    return "\n".join(lines) + "\n"


def llms_full(site: Site) -> str:
    """
    Concatenate every page's Markdown, each under a marker saying where it came from.

    Args:
        site: The site.

    Returns:
        The file's text.
    """
    chunks = [f"# {site.title}\n\n> {site.summary}\n"]
    for page in site.pages:
        chunks.append(
            f"\n<!-- {page.slug}.md (from {page.source}) -->\n\n{page.markdown.rstrip()}\n"
        )
    return "".join(chunks)


# -- the build


def build(
    site: Site,
    out: Path,
    version: str,
    root: Path = ROOT,
) -> None:
    """
    Write one version of the site and refresh the files that span versions.

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
        body = render_page(site, page, md, root)
        page.html = body
        directory = target / page.slug
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "index.html").write_text(
            page_html(body, page, f"{page.title} - {site.title}", page.summary or site.summary, 1),
            encoding="utf-8",
        )
        (target / f"{page.slug}.md").write_text(page.markdown, encoding="utf-8")
        search += search_entries(site, page)

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
    (target / "llms.txt").write_text(llms_txt(site, version), encoding="utf-8")
    (target / "llms-full.txt").write_text(llms_full(site), encoding="utf-8")
    for asset in sorted(site.assets):
        destination = target / "assets" / asset
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / asset, destination)
    write_spanning_files(site, out)


def write_spanning_files(site: Site, out: Path) -> None:
    """
    Write what spans versions: the list, ``latest``, the top page and the redirects.

    Args:
        site: The site (for its legacy redirects).
        out: The output directory.
    """
    versions = sorted(
        (p.name for p in out.iterdir() if p.is_dir() and VERSION_DIR.match(p.name)),
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


def build_site(out: Path, version: str, root: Path = ROOT) -> Site:
    """
    Load the manifest, build the site and return it with its problems.

    Args:
        out: The output directory.
        version: ``<major>.<minor>``.
        root: The repository root.

    Returns:
        The site; ``site.problems`` is empty when the documentation is sound.
    """
    site = load(root / "docs" / "site.yaml", root)
    build(site, out, version, root)
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
    args.add_argument("--out", type=Path, default=DEFAULT_OUT, help="where to write the site")
    args.add_argument("--version", help="the minor line, such as 3.4 (default: pyproject.toml's)")
    args.add_argument("--check", action="store_true", help="build into a temporary directory")
    options = args.parse_args(argv)
    version = options.version or current_version()
    if not VERSION_DIR.match(version):
        print(f"error: {version!r} is not a <major>.<minor> version", file=sys.stderr)
        return 2
    if options.check:
        with tempfile.TemporaryDirectory() as scratch:
            site = build_site(Path(scratch), version)
    else:
        site = build_site(options.out, version)
    for problem in site.problems:
        print(problem, file=sys.stderr)
    if site.problems:
        print(f"{len(site.problems)} problem(s) in the documentation", file=sys.stderr)
        return 1
    where = "a temporary directory" if options.check else str(options.out / version)
    print(f"{len(site.pages)} pages built into {where}")
    return 0


def pages_as_json(site: Site) -> list[dict[str, Any]]:
    """
    Describe the pages as plain data (for tests and tools).

    Args:
        site: The site.

    Returns:
        ``slug``, ``source``, ``title``, ``section`` and ``anchors`` of each page.
    """
    return [
        {
            "slug": p.slug,
            "source": p.source,
            "title": p.title,
            "section": p.section,
            "anchors": sorted(p.anchors),
        }
        for p in site.pages
    ]


if __name__ == "__main__":
    sys.exit(main())
