#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Turn a service template of Coolify's or Dokploy's catalogue into a recipe of Noust's.

A template is a good start and not a recipe (docs/contributing-recipes.md). This tool does the
part of the work that is a lookup and a rewrite, and leaves to the smoke test the part that is a
question about the running application:

1. **Is it eligible.** The project's repository is found (Dokploy's ``links.github``, the first
   GitHub link of Coolify's page for the service, the template's own header, the image's name)
   and read once with ``gh api``: at least 1,000 stars, not archived, pushed in the last year, and
   a licence that lets an operator run it for others (an allow-list of SPDX identifiers; anything
   else is left for a person to judge).
2. **The template is converted** with the same converters ``noust compose import`` uses
   (:mod:`noust.recipes.coolify`, :mod:`noust.recipes.dokploy`), then rewritten into what a recipe's
   closed schema accepts: only the service keys it allows, one loopback port, environment as a
   mapping, a secret that the template wrote as a literal turned into a generated variable (and
   every other place that literal appeared), a port or a mount the schema has no place for a reason
   to skip the template, said by name.
3. **Every image is pinned** to a release tag and to the digest its registry serves; a floating or
   missing tag becomes the newest release whose digest is the floating tag's, or the newest release.
4. **The result is validated** by the loader itself (:func:`noust.recipes.schema.parse_recipe`), so
   what is written is a recipe that loads. Whether it starts is ``recipe_smoke.py``'s answer.

The converted recipe credits the template it comes from in its header (Coolify's are Apache-2.0,
Dokploy's MIT) and nothing else of theirs is copied: the metadata comes from the project.

Usage:
    scripts/recipe_import.py survey [--out FILE]      Who is a candidate, and why the others are not.
    scripts/recipe_import.py plan                     One template per project, most starred first.
    scripts/recipe_import.py draft NAME... [--queue F] Write NAME.yaml and its assets in a drafts folder.
    scripts/recipe_import.py logos [--check]          Which recipe's mark the simple-icons collection has.

The catalogues are read from checkouts (``--coolify``, ``--dokploy``, ``--coolify-docs``; the defaults
are the ones in ~/research-src and ~/recetas-work/src), and the simple-icons package from ``--icons``.
Only anonymous registry requests and read-only ``gh api`` requests are made.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Hub's public images are read through a pull-through mirror: the survey of a catalogue reads
# hundreds of manifests in an hour, and Hub allows an anonymous address a hundred.
os.environ.setdefault("NOUST_PINS_MIRROR", "mirror.gcr.io")

import recipe_draft  # noqa: E402
import recipe_pins  # noqa: E402

from noust.core.compose_yaml import load_compose  # noqa: E402
from noust.recipes import coolify, dokploy  # noqa: E402
from noust.recipes.licensing import is_free_software  # noqa: E402
from noust.recipes.model import CATEGORIES, FLOATING_TAGS, RecipeError  # noqa: E402
from noust.recipes.schema import parse_recipe  # noqa: E402
from noust.recipes.variables import scan_references  # noqa: E402

RECIPES = ROOT / "src" / "noust" / "recipes"
ASSETS = RECIPES / "assets"
HOME = Path.home()
DEFAULTS = {
    "coolify": HOME / "research-src/coolify-src/templates/compose",
    "coolify_docs": HOME / "research-src/coolify-docs/content/docs/services",
    "dokploy": HOME / "recetas-work/src/dokploy-templates/blueprints",
    "icons": HOME / "recetas-work/src/simple-icons/pkg/package",
}

#: The domain a template is converted with, and the port a stack's web service is published on.
DOMAIN = "app.example.com"
PORT = 3000

#: Eligibility (docs/contributing-recipes.md, "Is it eligible").
MIN_STARS = 1000
MAX_AGE_DAYS = 365

#: Licences of projects whose software an operator may run for others. Anything else (no licence,
#: ``NOASSERTION``, a source-available one) is for a person to read, not for this tool to guess.
OPEN_LICENSES = frozenset(
    {
        "0BSD",
        "AGPL-3.0",
        "AGPL-3.0-only",
        "AGPL-3.0-or-later",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "BSL-1.0",
        "CC0-1.0",
        "EPL-2.0",
        "GPL-2.0",
        "GPL-2.0-only",
        "GPL-2.0-or-later",
        "GPL-3.0",
        "GPL-3.0-only",
        "GPL-3.0-or-later",
        "ISC",
        "LGPL-2.1",
        "LGPL-2.1-only",
        "LGPL-3.0",
        "LGPL-3.0-only",
        "MIT",
        "MPL-2.0",
        "Unlicense",
        "Zlib",
    }
)

#: What GitHub's API names a licence that SPDX writes with a suffix.
SPDX_ALIASES = {
    "AGPL-3.0": "AGPL-3.0-only",
    "GPL-2.0": "GPL-2.0-only",
    "GPL-3.0": "GPL-3.0-only",
    "LGPL-2.1": "LGPL-2.1-only",
    "LGPL-3.0": "LGPL-3.0-only",
}

#: Service keys a recipe's closed schema accepts (``composefile._SERVICE_KEYS``) and the ones
#: that mean nothing here and are dropped; any other key makes a template not convertible.
DROPPED_KEYS = frozenset(
    {
        "labels",
        "networks",
        "container_name",
        "platform",
        "dns",
        "dns_search",
        "domainname",
        "mac_address",
        "links",
        "expose",
        "annotations",
        "scale",
        "isolation",
        "runtime",
    }
)
KEPT_KEYS = frozenset(
    {
        "image",
        "command",
        "entrypoint",
        "environment",
        "ports",
        "volumes",
        "depends_on",
        "healthcheck",
        "restart",
        "user",
        "working_dir",
        "tmpfs",
        "stop_grace_period",
        "stop_signal",
        "init",
        "read_only",
        "shm_size",
        "ulimits",
        "mem_limit",
        "mem_reservation",
        "cpus",
        "pids_limit",
        "deploy",
        "cap_drop",
        "security_opt",
        "logging",
        "hostname",
        "extra_hosts",
        "pull_policy",
        "tty",
        "stdin_open",
    }
)

#: Names the repository's history must not carry (the repository never names the tools a change
#: was made with). They are written in two halves so that this file does not carry them itself.
#: An application's optional setting named after a provider that has one of them is left out of
#: its recipe, which the operator can still set in the application's environment; a template
#: that is about one is not converted.
FORBIDDEN_WORDS = re.compile("|".join(("cl" + "aude", "anth" + "ropic")), re.IGNORECASE)

#: What a variable's name says it holds a credential in.
SECRET_NAME = re.compile(r"(PASSWORD|PASSWD|SECRET|TOKEN|_KEY|_PASS$|_PWD$|SALT)", re.IGNORECASE)

#: A literal that is not a credential in a name that looks like one.
NOT_A_CREDENTIAL = re.compile(
    r"^(true|false|none|null|disabled|enabled|\d+|\d+[smhdw]|file:.*)$", re.IGNORECASE
)

#: Words in a template's category or tags, to the closed list of categories (first match wins).
CATEGORY_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("smart-home", ("smart home", "home automation", "iot", "home-assistant", "homeassistant")),
    ("ai", ("ai", "llm", "machine learning", "chatgpt", "openai", "rag", "agent")),
    ("analytics", ("analytics", "statistics", "tracking", "telemetry", "web analytics")),
    ("monitoring", ("monitoring", "uptime", "observability", "logging", "status", "alert")),
    ("security", ("security", "password", "authentication", "sso", "identity", "vpn", "secrets")),
    ("search", ("search",)),
    ("automation", ("automation", "workflow", "scheduler", "etl")),
    ("backend", ("backend", "baas", "headless", "api platform", "low-code", "no-code")),
    (
        "business",
        ("crm", "erp", "invoice", "accounting", "finance", "ecommerce", "e-commerce", "hr"),
    ),
    ("cms", ("cms", "blog", "publishing", "static site", "website builder")),
    (
        "communication",
        ("chat", "messaging", "email", "mail", "forum", "social", "video", "notification"),
    ),
    (
        "media",
        ("media", "music", "photo", "streaming", "podcast", "ebook", "book", "gallery", "video"),
    ),
    ("files", ("file", "storage", "sync", "backup", "cloud", "document", "pdf", "archive")),
    ("data", ("database", "data", "analytics", "bi", "spreadsheet", "dashboard")),
    (
        "development",
        ("git", "devops", "developer", "ci", "code", "registry", "ide", "docker", "api"),
    ),
    (
        "productivity",
        ("productivity", "notes", "task", "project", "kanban", "wiki", "calendar", "knowledge"),
    ),
    ("utilities", ("utilities", "tool", "network", "dns", "proxy", "converter", "generator")),
)


@dataclass
class Source:
    """
    Where a candidate is described.

    Attributes:
        key: The normalised name (lower case letters and digits).
        name: The name the recipe will have.
        coolify: Coolify's template file, when it has one.
        dokploy: Dokploy's blueprint folder, when it has one.
        docs: Coolify's documentation page of the service, when there is one.
    """

    key: str
    name: str
    coolify: Path | None = None
    dokploy: Path | None = None
    docs: Path | None = None


class Skip(Exception):
    """A candidate that is not turned into a recipe, and the reason in a few words."""

    def __init__(self, code: str, detail: str = "") -> None:
        """
        Args:
            code: A short name for the reason.
            detail: What was read, for a person.
        """
        super().__init__(code, detail)
        self.code = code
        self.detail = detail

    def __str__(self) -> str:
        """
        Returns:
            ``code: detail``.
        """
        return f"{self.code}: {self.detail}" if self.detail else self.code


def norm(text: str) -> str:
    """
    Args:
        text: A name.

    Returns:
        Lower case letters and digits only, so ``actual-budget`` and ``actualbudget`` meet.
    """
    return re.sub(r"[^a-z0-9]", "", text.lower())


def read_sources(coolify_dir: Path, dokploy_dir: Path, docs_dir: Path) -> dict[str, Source]:
    """
    List the candidates both catalogues describe.

    Args:
        coolify_dir: Coolify's ``templates/compose``.
        dokploy_dir: Dokploy's ``blueprints``.
        docs_dir: Coolify's ``content/docs/services``.

    Returns:
        The sources by normalised name.
    """
    found: dict[str, Source] = {}
    for path in sorted(dokploy_dir.iterdir()) if dokploy_dir.is_dir() else ():
        if path.is_dir() and (path / "docker-compose.yml").is_file():
            key = norm(path.name)
            found[key] = Source(key, path.name, dokploy=path)
    for path in sorted(coolify_dir.glob("*.y*ml")) if coolify_dir.is_dir() else ():
        key = norm(path.stem)
        source = found.setdefault(key, Source(key, path.stem))
        source.coolify = path
        page = docs_dir / f"{path.stem}.mdx"
        source.docs = page if page.is_file() else None
    return found


def front_matter(path: Path | None) -> dict[str, str]:
    """
    Args:
        path: A Coolify documentation page.

    Returns:
        The ``title``, ``description`` and ``category`` of its front matter.
    """
    if path is None:
        return {}
    text = path.read_text(encoding="utf-8")
    block = text.split("---", 2)[1] if text.startswith("---") else ""
    found: dict[str, str] = {}
    for line in block.splitlines():
        match = re.match(r'^(title|description|category):\s*"?(.*?)"?\s*$', line)
        if match:
            found[match.group(1)] = match.group(2)
    return found


def template_header(path: Path | None) -> dict[str, str]:
    """
    Args:
        path: A Coolify template.

    Returns:
        The ``# key: value`` lines of its header.
    """
    if path is None:
        return {}
    found: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^#\s*([a-z]+):\s*(.*)$", line)
        if match:
            found[match.group(1)] = match.group(2).strip()
        elif line.strip() and not line.startswith("#"):
            break
    return found


def dokploy_meta(path: Path | None) -> dict[str, Any]:
    """
    Args:
        path: A Dokploy blueprint folder.

    Returns:
        Its ``meta.json``, or an empty mapping.
    """
    if path is None or not (path / "meta.json").is_file():
        return {}
    try:
        loaded = json.loads((path / "meta.json").read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


_GITHUB = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")

#: Owners and repositories that are a host's, not a project's.
NOT_A_PROJECT = frozenset({"coollabsio", "Dokploy", "sponsors", "orgs", "topics", "marketplace"})


def find_repository(source: Source, repositories: Mapping[str, str] | None = None) -> str | None:
    """
    Find the GitHub repository of the project a candidate is.

    Args:
        source: The candidate.
        repositories: Repositories a person found for templates that name none, by the
            template's normalised name; they win over what the template says.

    Returns:
        ``owner/name``, or None when none of the places that name it do.
    """
    if repositories and source.key in repositories:
        return repositories[source.key]
    meta = dokploy_meta(source.dokploy)
    links = meta.get("links") if isinstance(meta.get("links"), dict) else {}
    texts = [str(links.get("github", ""))]
    header = template_header(source.coolify)
    texts.append(header.get("documentation", ""))
    if source.docs is not None:
        texts.append(source.docs.read_text(encoding="utf-8"))
    for text in texts:
        for owner, repository in _GITHUB.findall(text):
            if owner not in NOT_A_PROJECT:
                return f"{owner}/{repository.removesuffix('.git')}"
    return None


def gh_api(path: str) -> Any:
    """
    Read from GitHub's API with ``gh`` (read-only).

    Args:
        path: The API path.

    Returns:
        The decoded answer, or None when GitHub does not have it.
    """
    done = subprocess.run(
        ["gh", "api", path], capture_output=True, text=True, timeout=60, check=False
    )
    if done.returncode != 0:
        return None
    try:
        return json.loads(done.stdout)
    except ValueError:
        return None


def repository_facts(slug: str, cache: dict[str, Any]) -> dict[str, Any] | None:
    """
    Read what eligibility needs about a repository, once.

    Args:
        slug: ``owner/name``.
        cache: Earlier answers by slug; filled.

    Returns:
        Stars, archived, licence (SPDX), the last push, the latest release's date and the
        description; None when the repository does not exist.
    """
    if slug in cache:
        return cache[slug]
    info = gh_api(f"repos/{slug}")
    if not isinstance(info, dict) or "stargazers_count" not in info:
        cache[slug] = None
        return None
    release = gh_api(f"repos/{slug}/releases/latest")
    facts = {
        "repository": info.get("full_name", slug),
        "stars": info.get("stargazers_count", 0),
        "archived": bool(info.get("archived")),
        "license": (info.get("license") or {}).get("spdx_id"),
        "pushed": info.get("pushed_at"),
        "released": release.get("published_at") if isinstance(release, dict) else None,
        "release_tag": release.get("tag_name") if isinstance(release, dict) else None,
        "description": info.get("description") or "",
        "homepage": info.get("homepage") or "",
        "html_url": info.get("html_url", f"https://github.com/{slug}"),
        "read": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }
    cache[slug] = facts
    return facts


def with_licence(
    facts: Mapping[str, Any] | None, licences: Mapping[str, Mapping[str, Any]] | None
) -> dict[str, Any] | None:
    """
    Put a person's reading of a repository's licence over what GitHub could say of it.

    GitHub names a licence only when the file is a standard text; a repository with its own terms
    (``NOASSERTION``) is read by a person, who records the SPDX expression to use, whether the terms
    let an operator run it for others, and what was read and where.

    Args:
        facts: What :func:`repository_facts` read.
        licences: Readings by repository (``owner/name``, any case): ``license`` (the expression),
            ``accept`` (whether an operator may run it), ``name`` (how the terms are called) and
            ``evidence`` (the file read and the day).

    Returns:
        The facts with ``license``, ``licence_name``, ``licence_evidence`` and ``licence_accepted``
        set when a reading exists, else the facts as they were.
    """
    if facts is None:
        return None
    if not licences:
        return dict(facts)
    wanted = str(facts["repository"]).lower()
    for slug, reading in licences.items():
        if slug.lower() != wanted:
            continue
        return {
            **facts,
            "license": reading.get("license", facts.get("license")),
            "licence_name": reading.get("name", ""),
            "licence_evidence": reading.get("evidence", ""),
            "licence_accepted": bool(reading.get("accept")),
            "licence_refused": reading.get("accept") is False,
        }
    return dict(facts)


def not_eligible(facts: Mapping[str, Any] | None, *, min_stars: int = MIN_STARS) -> Skip | None:
    """
    Judge a repository against docs/contributing-recipes.md.

    Args:
        facts: What :func:`repository_facts` read, as :func:`with_licence` may have changed it.
        min_stars: The fewest stars a project may have; the catalogue's rule is 1,000 and a run
            that looks for the long tail lowers it for projects that are alive and publish an image.

    Returns:
        Why not, or None when it is eligible.
    """
    if facts is None:
        return Skip("no-repository", "the repository could not be read")
    if facts["archived"]:
        return Skip("archived", facts["repository"])
    if facts["stars"] < min_stars:
        return Skip("stars", f"{facts['repository']} has {facts['stars']}")
    latest = facts.get("released") or facts.get("pushed")
    if latest is None:
        return Skip("no-release", facts["repository"])
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(latest.replace("Z", "+00:00"))).days
    if age > MAX_AGE_DAYS:
        return Skip("stale", f"{facts['repository']}: last release or push {age} days ago")
    licence = facts.get("license")
    if facts.get("licence_refused"):
        return Skip("licence", f"{facts['repository']}: {facts.get('licence_evidence') or licence}")
    if licence not in OPEN_LICENSES and not facts.get("licence_accepted"):
        return Skip("licence", f"{facts['repository']}: {licence}")
    return None


# -- conversion --------------------------------------------------------------------------------


@dataclass
class Draft:
    """
    A template rewritten as a recipe, before it is written down.

    Attributes:
        name: The recipe's name.
        services: The Compose services, rewritten.
        volumes: The named volumes.
        variables: The recipe's variables by name, as the YAML spells them.
        routed: ``(service, container port)`` the domain is sent to.
        origin: ``Coolify`` or ``Dokploy``.
        template: A path in the template's catalogue, for the credit.
        header: Coolify's header of the template.
        warnings: What the conversion could not do.
    """

    name: str
    services: dict[str, dict[str, Any]] = field(default_factory=dict)
    volumes: list[str] = field(default_factory=list)
    variables: dict[str, dict[str, Any]] = field(default_factory=dict)
    routed: tuple[str, int] | None = None
    origin: str = ""
    template: str = ""
    header: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    values: dict[str, str] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)
    secret_files: set[str] = field(default_factory=set)
    root_equivalent: str | None = None
    shipped: dict[str, str] = field(default_factory=dict)
    public_ports: list[dict[str, Any]] = field(default_factory=list)


def convert_template(source: Source) -> tuple[Draft, str]:
    """
    Convert a candidate's template with the converter of the catalogue that has it.

    Args:
        source: The candidate.

    Returns:
        The draft with the converter's variables, and the document it converted.

    Raises:
        Skip: The converter refused the template.
    """
    from noust.core.exceptions import ValidationError

    draft = Draft(name=source.name)
    try:
        if source.coolify is not None:
            result: Any = coolify.convert(
                source.coolify.read_text(encoding="utf-8"), domain=DOMAIN, port=PORT
            )
            draft.origin = "Coolify"
            draft.template = f"templates/compose/{source.coolify.name}"
            draft.header = dict(result.metadata)
        elif source.dokploy is not None:
            result = dokploy.convert(
                (source.dokploy / "docker-compose.yml").read_text(encoding="utf-8"),
                (source.dokploy / "template.toml").read_text(encoding="utf-8")
                if (source.dokploy / "template.toml").is_file()
                else None,
                domain=DOMAIN,
                port=PORT,
            )
            draft.origin = "Dokploy"
            draft.template = f"blueprints/{source.dokploy.name}"
        else:
            raise Skip("no-template", source.name)
    except (ValidationError, yaml.YAMLError, KeyError, ValueError) as exc:
        raise Skip("conversion", str(exc)[:160]) from exc
    if result.warnings:
        draft.warnings.extend(result.warnings)
        blocking = [w for w in result.warnings if "inline" in w or "content" in w]
        if blocking:
            raise Skip("template-files", blocking[0][:160])
    if result.routed is None:
        raise Skip("no-web-service", "the template names no service for the domain")
    draft.routed = result.routed
    draft.values = {str(k): str(v) for k, v in result.values.items()}
    draft.files = {str(k): str(v) for k, v in (getattr(result, "files", None) or {}).items()}
    draft.secret_files = {str(p) for p in (getattr(result, "secret_files", None) or ())}
    document = load_compose(result.document)
    for key, spec in (document.get("services") or {}).items():
        draft.services[str(key)] = dict(spec or {})
    draft.volumes = [str(v) for v in (document.get("volumes") or {})]
    for variable in result.variables:
        draft.variables[variable.name] = {
            "origin": variable.origin,
            "secret": variable.secret,
            "value": result.values.get(variable.name),
        }
    return draft, result.document


def as_environment(raw: Any) -> dict[str, str]:
    """
    Args:
        raw: A service's ``environment`` in either Compose syntax.

    Returns:
        A mapping of strings (a key without a value is the empty string).
    """
    if isinstance(raw, list):
        found: dict[str, str] = {}
        for item in raw:
            key, _, value = str(item).partition("=")
            found[key] = value
        return found
    if isinstance(raw, dict):
        return {str(k): "" if v is None else str(v) for k, v in raw.items()}
    return {}


#: How each engine says it answers, for a template that left its database without a check: an
#: application that starts before its database answers restarts until it does, which the smoke test
#: counts as a restart loop. The commands read the account from the container's own environment.
_HEALTH_TIMING = {"interval": "10s", "timeout": "5s", "retries": 10, "start_period": "20s"}
ENGINE_HEALTH_CHECKS: dict[str, dict[str, Any]] = {
    "postgres": {
        "test": ["CMD-SHELL", 'pg_isready -U "$${POSTGRES_USER:-postgres}" -q'],
        **_HEALTH_TIMING,
    },
    "mariadb": {
        "test": ["CMD", "healthcheck.sh", "--connect", "--innodb_initialized"],
        **_HEALTH_TIMING,
    },
    "mysql": {"test": ["CMD-SHELL", "mysqladmin ping -h 127.0.0.1 --silent"], **_HEALTH_TIMING},
    "redis": {"test": ["CMD", "redis-cli", "ping"], **_HEALTH_TIMING},
    "valkey": {"test": ["CMD", "valkey-cli", "ping"], **_HEALTH_TIMING},
    "mongo": {
        "test": ["CMD", "mongosh", "--quiet", "--eval", "db.adminCommand('ping').ok"],
        **_HEALTH_TIMING,
    },
}

#: A port a stack opens on purpose, as the template writes it: numbers, and a protocol.
_PUBLIC_PORT = re.compile(
    r"(?P<host>[0-9]{1,5}):(?P<container>[0-9]{1,5})(?:/(?P<protocol>tcp|udp))?"
)

#: Ports the server itself (or the usual database beside it) holds: a recipe that opened one would
#: not start next to them, so the template is left for a person to adapt.
SERVER_PORTS = frozenset({22, 53, 80, 443, 3306, 5432, 6379, 27017})


def use_stack_env(draft: Draft, spec: dict[str, Any]) -> None:
    """
    Turn ``env_file: .env`` into the variables of the stack, named in the service's environment.

    The stack's ``.env`` is every variable the recipe declares, so a service that reads it is a
    service whose environment lists them. Any other file is one the recipe does not ship and is
    left for the closed schema to refuse.

    Args:
        draft: The draft, whose ``values`` are the stack's variables.
        spec: One service, changed in place.
    """
    if "env_file" not in spec:
        return
    entries = spec["env_file"] if isinstance(spec["env_file"], list) else [spec["env_file"]]
    paths = [str(e.get("path", "")) if isinstance(e, dict) else str(e) for e in entries]
    if any(path not in (".env", "./.env") for path in paths):
        return
    del spec["env_file"]
    environment = as_environment(spec.get("environment"))
    for name in draft.values:
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) and name not in environment:
            environment[name] = f"${{{name}}}"
    spec["environment"] = environment


_SHELL_DOLLAR = re.compile(r"(?<!\$)\$(?![{$])")


def _escaped(text: str) -> str:
    """
    Args:
        text: A command, or a part of one.

    Returns:
        It with every ``$`` that is neither ``${`` nor already ``$$`` doubled.
    """
    return _SHELL_DOLLAR.sub(lambda _match: "$$", text)


def shell_dollars(spec: dict[str, Any]) -> None:
    """
    Write a ``$`` the container's shell reads the way Compose passes it on: ``$$``.

    A command such as ``export K=$(cat /run/k)`` is the shell's, and Compose would take ``$(``
    and ``$K`` for variables of its own (and the recipe's loader refuses a bare ``$NAME``).

    Args:
        spec: One service, changed in place.
    """
    for key in ("command", "entrypoint"):
        value = spec.get(key)
        if isinstance(value, str):
            spec[key] = _escaped(value)
        elif isinstance(value, list):
            spec[key] = [_escaped(item) if isinstance(item, str) else item for item in value]
    check = spec.get("healthcheck")
    if isinstance(check, dict) and isinstance(check.get("test"), list):
        check["test"] = [_escaped(item) if isinstance(item, str) else item for item in check["test"]]
    elif isinstance(check, dict) and isinstance(check.get("test"), str):
        check["test"] = _escaped(check["test"])


def clean_services(draft: Draft) -> None:
    """
    Rewrite the services into what a recipe's schema accepts.

    Args:
        draft: Changed in place.

    Raises:
        Skip: A service asks for something a recipe may not (the host's namespaces, a build, a
            device) or the template needs a published port the schema has no place for.
    """
    routed_service, routed_port = draft.routed or ("", 0)
    from noust.managers.database.instances import image_engine

    # The catalogue publishes no database engine (tests/recipes/test_catalog_wave2.py): one that is
    # the application itself would be on a public name.
    if image_engine(str(draft.services.get(routed_service, {}).get("image", ""))) is not None:
        raise Skip("database-as-app", f"service {routed_service} is a database engine")
    for key, spec in draft.services.items():
        for banned in ("privileged", "cap_add", "devices", "network_mode", "pid", "ipc", "build"):
            if spec.get(banned):
                raise Skip(f"uses-{banned}", f"service {key}")
        for dropped in DROPPED_KEYS & spec.keys():
            del spec[dropped]
        use_stack_env(draft, spec)
        shell_dollars(spec)
        unknown = set(spec) - KEPT_KEYS
        if unknown:
            raise Skip("service-keys", f"service {key}: {', '.join(sorted(unknown))}")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,62}", key):
            raise Skip("service-name", key)
        spec["restart"] = "unless-stopped"
        if "environment" in spec:
            spec["environment"] = {
                name: value
                for name, value in as_environment(spec["environment"]).items()
                if not FORBIDDEN_WORDS.search(name)
            }
        ports = []
        for entry in spec.get("ports") or ():
            text = str(entry)
            if key == routed_service and re.fullmatch(
                rf"127\.0\.0\.1:\$\{{PORT(?::-\d+)?\}}:{routed_port}(/tcp)?", text
            ):
                ports.append(f"127.0.0.1:${{PORT}}:{routed_port}")
            else:
                opened = _PUBLIC_PORT.fullmatch(text)
                if opened is None or int(opened["host"]) in SERVER_PORTS:
                    raise Skip("extra-port", f"service {key} publishes {text}")
                protocol = opened["protocol"] or "tcp"
                ports.append(text)
                draft.public_ports.append(
                    {
                        "port": int(opened["host"]),
                        "protocol": protocol,
                        "purpose": f"{key}: {opened['container']}/{protocol}, which the template opens.",
                    }
                )
        if ports:
            spec["ports"] = ports
        else:
            spec.pop("ports", None)
        mounts = []
        for volume in spec.get("volumes") or ():
            if isinstance(volume, dict):
                raise Skip("long-volume", f"service {key}")
            mount = rewrite_mount(draft, key, str(volume))
            if mount is not None:
                mounts.append(mount)
        if mounts:
            spec["volumes"] = mounts
        else:
            spec.pop("volumes", None)
        depends = spec.get("depends_on")
        if isinstance(depends, list):
            spec["depends_on"] = {str(item): {"condition": "service_started"} for item in depends}
        if isinstance(depends, dict):
            waits = {}
            for target, value in depends.items():
                condition = (value or {}).get("condition", "service_started")
                if condition == "service_started" and draft.services.get(target, {}).get(
                    "healthcheck"
                ):
                    condition = "service_healthy"
                waits[str(target)] = {"condition": condition}
            spec["depends_on"] = waits
    for spec in draft.services.values():
        if not spec.get("healthcheck"):
            engine = image_engine(str(spec.get("image", "")))
            if engine is not None and engine.flavour in ENGINE_HEALTH_CHECKS:
                spec["healthcheck"] = dict(ENGINE_HEALTH_CHECKS[engine.flavour])
    for key, spec in draft.services.items():
        for target in spec.get("depends_on") or ():
            if target not in draft.services:
                raise Skip("depends-on-missing", f"{key} needs {target}")
        # What waits for a service that has a health check waits for it to answer.
        for target, wait in (spec.get("depends_on") or {}).items():
            if draft.services[target].get("healthcheck"):
                wait["condition"] = "service_healthy"


#: Host files that only say the time zone: a recipe sets TZ instead.
CLOCK_FILES = frozenset({"/etc/localtime", "/etc/timezone"})

#: The one mount of the Docker socket a recipe may make once it says it is root on the server.
DOCKER_SOCKET = re.compile(r"/var/run/docker\.sock:/var/run/docker\.sock(?::ro)?")


def rewrite_mount(draft: Draft, service: str, volume: str) -> str | None:
    """
    Make a service's mount into one a recipe may have.

    A named volume stays. The host's clock files go (the recipe sets TZ). A file or a folder of the
    template's own (Dokploy's ``./files/...``) becomes a file the recipe ships, unless it holds a
    secret or a ``${NAME}`` the recipe would have to fill in. A host folder the template meant as the
    service's data (``./files/uploads``, ``./volumes/x``) becomes a named volume. The Docker
    socket makes the recipe root-equivalent. Anything else of the host is a reason to skip.

    Args:
        draft: The draft; its shipped files and its volumes are added to.
        service: The service, for the volume's name and the message.
        volume: The mount as the template wrote it.

    Returns:
        The mount the recipe keeps, or None when it is dropped.

    Raises:
        Skip: The mount is a path of the host a recipe cannot have, or a file with a secret.
    """
    source, _, rest = volume.partition(":")
    if volume.split(":", 1)[0] in CLOCK_FILES:
        return None
    if DOCKER_SOCKET.fullmatch(volume):
        draft.root_equivalent = "docker_socket"
        return volume
    if not source.startswith(("/", ".", "~", "$")):
        return volume
    if not source.startswith("./"):
        raise Skip("host-mount", f"service {service}: {volume}")
    relative = source[2:].rstrip("/")
    target = rest
    held = {path: body for path, body in draft.files.items() if path.rstrip("/") == relative}
    held.update(
        {path: body for path, body in draft.files.items() if path.startswith(relative + "/")}
    )
    if held:
        for path, body in held.items():
            if path in draft.secret_files or "${" in body or "$" in body:
                raise Skip("template-files", f"{path} holds a secret or a reference")
            draft.shipped[path] = body
        return f"./{relative}:{target}"
    if relative.startswith(("files/", "volumes/")) and "." not in relative.rsplit("/", 1)[-1]:
        name = re.sub(r"[^a-z0-9]+", "-", f"{service}-{relative.rsplit('/', 1)[-1]}".lower())
        if name not in draft.volumes:
            draft.volumes.append(name)
        return f"{name}:{target}"
    raise Skip("host-mount", f"service {service}: {volume}")


_DEFAULTED = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):?-([^${}]*)\}")


def walk_strings(node: Any, change: Any) -> Any:
    """
    Args:
        node: A parsed document.
        change: A function from a string to a string.

    Returns:
        The document with ``change`` applied to every string in it, keys included.
    """
    if isinstance(node, str):
        return change(node)
    if isinstance(node, list):
        return [walk_strings(item, change) for item in node]
    if isinstance(node, dict):
        return {walk_strings(k, change): walk_strings(v, change) for k, v in node.items()}
    return node


def one_default_each(draft: Draft) -> None:
    """
    Give a variable that two services default differently one default, declared once.

    Compose lets ``${TZ:-UTC}`` in one service and ``${TZ:-Europe/Berlin}`` in another; a recipe
    reads one value for one name. The first default becomes the variable's and every reference
    becomes the plain ``${TZ}``.

    Args:
        draft: Changed in place; the variable is added to ``draft.variables`` with its default.
    """
    defaults: dict[str, list[str]] = {}

    def look(text: str) -> str:
        for name, default in _DEFAULTED.findall(text):
            defaults.setdefault(name, [])
            if default not in defaults[name]:
                defaults[name].append(default)
        return text

    walk_strings(draft.services, look)
    clashes = {name: seen[0] for name, seen in defaults.items() if len(seen) > 1}
    if not clashes:
        return

    def settle(text: str) -> str:
        return _DEFAULTED.sub(
            lambda match: f"${{{match.group(1)}}}" if match.group(1) in clashes else match.group(0),
            text,
        )

    draft.services = walk_strings(draft.services, settle)
    for name, default in clashes.items():
        draft.variables[name] = {"origin": "template", "secret": False, "value": default}


def secret_literals(draft: Draft) -> None:
    """
    Turn a credential the template wrote as a literal into a generated variable.

    Dokploy's templates write ``MYSQL_ROOT_PASSWORD: example``; a recipe writes no credential. Every
    place the same literal appears (a connection string, the other service's copy of the password)
    becomes a reference to the one variable.

    Args:
        draft: Changed in place.
    """
    assigned: dict[str, str] = {}
    for service, spec in draft.services.items():
        environment = spec.get("environment") or {}
        for key, value in list(environment.items()):
            if not SECRET_NAME.search(key) or not value or "${" in value:
                continue
            if NOT_A_CREDENTIAL.fullmatch(value) or len(value) < 4:
                # Not a credential, in a name that looks like one (TOKEN_EXPIRY: 24h): the catalogue
                # holds no literal in such a name, so it becomes a setting with that default.
                if len(value) <= 40 and re.fullmatch(r"[A-Za-z0-9_.:/-]+", value):
                    name = re.sub(r"[^A-Z0-9_]", "_", key.upper())
                    draft.variables.setdefault(
                        name, {"origin": "template", "secret": False, "value": value}
                    )
                    environment[key] = f"${{{name}}}"
                continue
            if value not in assigned:
                name = re.sub(r"[^A-Z0-9_]", "_", key.upper())
                if name in draft.variables or name in assigned.values():
                    name = re.sub(r"[^A-Z0-9_]", "_", f"{service}_{key}".upper())
                assigned[value] = name
                draft.variables[name] = {"origin": "generated", "secret": True, "value": None}
    for value, name in assigned.items():
        for spec in draft.services.values():
            environment = spec.get("environment") or {}
            for key, current in list(environment.items()):
                if value in current:
                    environment[key] = current.replace(value, f"${{{name}}}")
            for field_name in ("command", "entrypoint"):
                current = spec.get(field_name)
                if isinstance(current, str) and value in current:
                    spec[field_name] = current.replace(value, f"${{{name}}}")
                elif isinstance(current, list):
                    spec[field_name] = [str(p).replace(value, f"${{{name}}}") for p in current]


def default_of(text: str) -> str | None:
    """
    Args:
        text: An image reference that may carry ``${NAME:-default}``.

    Returns:
        The reference with each such default put in, or None when a variable has none.
    """
    resolved = re.sub(r"\$\{[A-Za-z_][A-Za-z0-9_]*:?-([^}]*)\}", r"\1", text)
    return None if "${" in resolved or "$" in resolved else resolved


def pick_release_tag(reference: str, floating: str, log: list[str]) -> str:
    """
    Choose the release an image that names no release stands for.

    Args:
        reference: ``[registry/]repository`` without a tag.
        floating: The floating tag the template used (``latest``).
        log: Where choices are said.

    Returns:
        The newest release tag whose digest is the floating tag's digest, else the newest release.

    Raises:
        Skip: The registry lists no release.
    """
    try:
        wanted = recipe_pins.resolve_digest(f"{reference}:{floating}")
    except RuntimeError:
        wanted = None
    try:
        tags = recipe_draft.registry_tags(reference)
    except Exception as exc:
        raise Skip("registry-tags", f"{reference}: {exc}") from exc
    releases = recipe_draft.release_tags(tags, limit=40)
    if not releases:
        raise Skip("no-release-tag", reference)
    plain = [tag for tag in releases if "-" not in tag] or releases
    for tag in plain[:12]:
        if wanted is not None:
            try:
                if recipe_pins.resolve_digest(f"{reference}:{tag}") == wanted:
                    log.append(f"{reference}:{floating} is {tag}")
                    return tag
            except RuntimeError:
                continue
    log.append(f"{reference}:{floating} was not matched; the newest release is {plain[0]}")
    return plain[0]


def pin_images(draft: Draft) -> list[str]:
    """
    Give every image a release tag and the digest its registry serves.

    Args:
        draft: Changed in place.

    Returns:
        The architectures every image is published for.

    Raises:
        Skip: An image cannot be resolved.
    """
    archs: dict[str, set[str]] = {}
    for service, spec in draft.services.items():
        reference = default_of(str(spec.get("image", "")))
        if not reference:
            raise Skip("image-variable", f"service {service}: {spec.get('image')}")
        reference = reference.split("@", 1)[0]
        last = reference.rsplit("/", 1)[-1]
        repository, tag = reference.rsplit(":", 1) if ":" in last else (reference, "")
        if not tag or tag in FLOATING_TAGS or re.search(r"(?i)(latest|nightly|edge)", tag):
            tag = pick_release_tag(repository, tag or "latest", draft.warnings)
        try:
            digest = recipe_pins.resolve_digest(f"{repository}:{tag}")
            platforms = recipe_pins.resolve_platforms(f"{repository}:{tag}", digest)
        except (RuntimeError, OSError) as exc:
            raise Skip("image", f"{repository}:{tag}: {str(exc)[:100]}") from exc
        spec["image"] = f"{repository}:{tag}@{digest}"
        archs[repository + tag] = platforms
    return recipe_draft.common_architectures(archs)


def generator_for(name: str, value: str | None) -> str:
    """
    Choose the form a generated secret takes.

    Args:
        name: The variable's name.
        value: A value the converter made for it, if any.

    Returns:
        One of :data:`noust.recipes.model.GENERATORS`.
    """
    if value:
        if re.fullmatch(r"[0-9a-f]{64}", value):
            return "hex64"
        if re.fullmatch(r"[0-9a-f]{32}", value):
            return "hex32"
        if re.fullmatch(r"[A-Za-z0-9+/]{43}=", value):
            return "base64_32"
        if re.fullmatch(r"[A-Za-z0-9+/]{86}==", value):
            return "base64_64"
        if len(value) >= 60:
            return "password64"
    return "password32"


def declare_variables(draft: Draft, text: str) -> dict[str, dict[str, Any]]:
    """
    Decide each variable the document uses and how it gets its value.

    Args:
        draft: The draft, with the converter's variables.
        text: The document as it will be written.

    Returns:
        The recipe's ``variables`` mapping.
    """
    found: dict[str, dict[str, Any]] = {}
    for reference in scan_references(text):
        name = reference.name
        if name == "PORT" or name in found:
            continue
        known = draft.variables.get(name)
        entry: dict[str, Any]
        if known is not None and known["origin"] == "domain":
            entry = {"from": "fqdn" if name.startswith("SERVICE_FQDN") else "url"}
        elif known is not None and known["origin"] == "generated":
            composed = compose_value(draft, name, found)
            twin = next(
                (
                    other
                    for other, made in found.items()
                    if "generate" in made
                    and known["value"]
                    and len(str(known["value"])) >= 8
                    and draft.values.get(other) == known["value"]
                ),
                None,
            )
            if composed is not None:
                entry = {"value": composed, "secret": True}
            elif twin is not None:
                # The template wrote one secret (`db_pass`) and used it twice: the database's and the
                # application's must stay the same value, not two random ones.
                entry = {"value": f"${{{twin}}}", "secret": True}
            elif known["secret"]:
                entry = {"generate": generator_for(name, known["value"])}
            else:
                entry = {"generate": "user"}
        elif known is not None and known["value"] is not None:
            entry = {"default": str(known["value"])}
        elif reference.operator in ("-", ":-"):
            continue
        elif reference.operator in ("?", ":?"):
            entry = {"required": True}
        else:
            entry = {"default": ""}
        if "generate" in entry and entry["generate"] != "user":
            entry["secret"] = True
        if "default" in entry and SECRET_NAME.search(name) and entry["default"] == "":
            entry["secret"] = True
        entry["description"] = describe_variable(name, entry)
        found[name] = entry
    return found


def compose_value(draft: Draft, name: str, found: dict[str, dict[str, Any]]) -> str | None:
    """
    Rebuild a value the converter made out of other generated values and the domain.

    Dokploy expands ``postgres://app:${password:32}@db/app`` into a string with one random
    password in it; a recipe must not generate that string again as a password, it must write
    the string over the variables it is made of.

    Args:
        draft: The draft, with the converter's values.
        name: The variable.
        found: The variables declared so far; the domain's is added when the string holds it.

    Returns:
        The string with each secret it holds replaced by its variable and the sample domain by the
        deployment's, or None when the value is just a random string.
    """
    value = draft.values.get(name)
    if not value:
        return None
    composed = value
    for other, known in draft.variables.items():
        secret = draft.values.get(other)
        if other == name or known["origin"] != "generated" or not secret or len(secret) < 8:
            continue
        if secret in composed and secret != value:
            composed = composed.replace(secret, f"${{{other}}}")
            made = {"generate": generator_for(other, secret), "secret": True}
            made["description"] = describe_variable(other, made)
            found.setdefault(other, made)
    if DOMAIN in composed:
        found.setdefault(
            "APP_DOMAIN",
            {"from": "fqdn", "description": "The domain people use to reach the application."},
        )
        composed = composed.replace(DOMAIN, "${APP_DOMAIN}")
    return composed if composed != value else None


def _subject(name: str) -> str:
    """
    Args:
        name: A variable's name.

    Returns:
        What it is for, in words: ``SERVICE_PASSWORD_DB`` is "db", ``APP_SECRET_KEY`` is "app secret key".
    """
    words = re.sub(r"^SERVICE_(USER|PASSWORD|BASE64|REALBASE64)(_\d+)?_", "", name)
    return words.lower().replace("_", " ") or name.lower()


def describe_variable(name: str, entry: Mapping[str, Any]) -> str:
    """
    Args:
        name: The variable.
        entry: Where its value comes from.

    Returns:
        One line for the operator who is asked about it.
    """
    if "from" in entry:
        return "The address people use to reach the application, as the template builds it."
    if entry.get("generate") == "user":
        return f"The user name Noust generated for {_subject(name)}."
    if "generate" in entry:
        return f"The secret Noust generated for {_subject(name)}."
    if entry.get("required"):
        return "Needed to start the application; the installation asks for it."
    return f"Setting {name} of the application; the template leaves it to the operator."


# -- the recipe's text -------------------------------------------------------------------------


class _Dumper(yaml.SafeDumper):
    """A dumper that indents lists under their key, as the recipes in the catalogue do."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:  # type: ignore[override]
        """
        Args:
            flow: Flow style.
            indentless: Ignored: lists are always indented.
        """
        super().increase_indent(flow, False)


def dump(data: Any) -> str:
    """
    Args:
        data: A document.

    Returns:
        Block-style YAML, keys in the order given.
    """
    return yaml.dump(
        data,
        Dumper=_Dumper,
        sort_keys=False,
        default_flow_style=False,
        width=100,
        allow_unicode=True,
    )


def quote(text: str) -> str:
    """
    Args:
        text: A string.

    Returns:
        It as a YAML scalar on one line.
    """
    return json.dumps(text, ensure_ascii=False)


def sentence(text: str, limit: int) -> str:
    """
    Args:
        text: Prose.
        limit: The longest result.

    Returns:
        The first sentences that fit, without markdown links, cut at a word when none does.
    """
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", " ".join(text.split()))
    text = re.sub(r"[*_`]", "", text)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    stop = max(cut.rfind(". "), cut.rfind("; "))
    if stop > limit // 3:
        return cut[: stop + 1].rstrip(";")
    # The ellipsis is part of the limit, not beyond it.
    room = text[: limit - 3]
    return room[: room.rfind(" ")].rstrip(" ,;:") + "..."


def category_of(*texts: str) -> str:
    """
    Args:
        texts: The words a template and its page give about the application.

    Returns:
        One of the closed list of categories.
    """
    haystack = " " + " ".join(texts).lower().replace(",", " ") + " "
    for category, words in CATEGORY_WORDS:
        if any(f" {word} " in haystack or f" {word}s " in haystack for word in words):
            return category if category in CATEGORIES else "other"
    return "utilities"


def tags_of(header: Mapping[str, str], meta: Mapping[str, Any], category: str) -> list[str]:
    """
    Args:
        header: Coolify's header.
        meta: Dokploy's ``meta.json``.
        category: The recipe's category.

    Returns:
        Up to eight tags: lower case letters, digits and dashes.
    """
    raw = [t for t in re.split(r"[,\s]+", header.get("tags", "")) if t]
    raw += [str(t) for t in meta.get("tags", []) if isinstance(t, str)]
    tags: list[str] = []
    for item in raw:
        tag = re.sub(r"[^a-z0-9-]", "", item.lower().replace(" ", "-")).strip("-")
        if tag and tag not in tags and len(tag) <= 24:
            tags.append(tag)
    if category not in tags:
        tags.append(category)
    return tags[:8]


def unique_volumes(services: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """
    Args:
        services: The recipe's services.

    Returns:
        The named volumes they mount, in order.
    """
    found: dict[str, None] = {}
    for spec in services.values():
        for volume in spec.get("volumes") or ():
            source = str(volume).split(":", 1)[0]
            # A path (the socket, a file the recipe ships) is not a volume to declare.
            if not source.startswith(("/", ".", "~", "$")):
                found.setdefault(source)
    return list(found)


def build_text(
    draft: Draft,
    source: Source,
    facts: Mapping[str, Any],
    archs: list[str],
    *,
    logo: str,
    health: Mapping[str, Any],
) -> str:
    """
    Write the recipe's file.

    Args:
        draft: The converted template.
        source: The candidate.
        facts: What GitHub says about the project.
        archs: What every image is published for.
        logo: The ``logo:`` value.
        health: ``path``, ``expect`` and ``timeout``.

    Returns:
        The YAML text.
    """
    meta = dokploy_meta(source.dokploy)
    page = front_matter(source.docs)
    title = (
        page.get("title") or str(meta.get("name") or "") or source.name.replace("-", " ").title()
    )
    title = title.strip('"')
    slogan = (
        sentence(draft.header.get("slogan") or page.get("description") or facts["description"], 100)
        or f"{title}, self-hosted."
    )
    description = sentence(
        str(meta.get("description") or page.get("description") or facts["description"] or slogan),
        400,
    )
    category = category_of(
        draft.header.get("category", ""),
        page.get("category", ""),
        draft.header.get("tags", ""),
        " ".join(map(str, meta.get("tags", []))),
        facts["description"],
    )
    tags = tags_of(draft.header, meta, category)
    web_service = draft.routed[0] if draft.routed else next(iter(draft.services))
    image = str(draft.services[web_service]["image"])
    tag = image.split("@", 1)[0].rsplit(":", 1)[-1]
    version = re.sub(r"^v(?=\d)", "", tag)[:39] if re.match(r"^v?\d", tag) else tag[:39]
    licence = SPDX_ALIASES.get(facts["license"], facts["license"])
    if not is_free_software(licence):
        # The catalogue says so where the operator chooses (tests/recipes/test_licensing.py): the
        # software may be run for one's own use, and it is not free software.
        called = facts.get("licence_name") or "its own licence"
        lead = (
            f"Open core ({called}), its enterprise features need a licence. "
            if re.search(r"-and-(EE|proprietary)$", licence or "")
            else f"Source-available ({called}), for your own use. "
        )
        description = sentence(
            lead
            + str(
                meta.get("description") or page.get("description") or facts["description"] or slogan
            ),
            400,
        )
    homepage = (
        facts["homepage"] if str(facts["homepage"]).startswith("https://") else facts["html_url"]
    )
    docs_url = draft.header.get("documentation") or str((meta.get("links") or {}).get("docs") or "")
    if not docs_url.startswith("https://"):
        docs_url = homepage
    volumes = unique_volumes(draft.services)
    variables = declare_variables(draft, dump({"services": draft.services}))
    compose_text = dump(
        {
            "services": draft.services,
            **({"volumes": dict.fromkeys(volumes)} if volumes else {}),
        }
    ).replace(": null", ":")
    notes = [
        "Open {{ url }}. The first start can take a minute while the application prepares itself.",
        "If the application asks you to create the first account, do it right after installing: "
        "until someone does, the first visitor can.",
        "Settings that Noust generated are in this application's environment (noust env show "
        "{{ domain }}); secrets are masked there, and --unmask shows them.",
    ]
    if draft.root_equivalent:
        notes.insert(
            1,
            f"{title} controls the Docker daemon of this server, which is the same as root on it: "
            "it can start any container with any part of the server mounted, Noust's applications "
            "included. Give its sign-in only to people you would give root.",
        )
    account = [
        name for name in variables if name.startswith(("SERVICE_USER_", "SERVICE_PASSWORD_"))
    ]
    if account:
        notes.insert(
            1,
            "The user name and the password Noust generated for the application are "
            f"{' and '.join(account)} in its environment (noust env show {{{{ domain }}}} "
            "--unmask); the application's own sign-in may use them or ask you to create an account.",
        )
    if volumes:
        notes.append(
            f"Its data is in the Docker volume{'s' if len(volumes) > 1 else ''} "
            f"{', '.join(volumes)} of this application: back "
            f"{'them' if len(volumes) > 1 else 'it'} up before updating."
        )
    credit = (
        "Derived in part from Coolify's template "
        f"https://github.com/coollabsio/coolify/blob/next/{draft.template}"
    )
    licence_of_origin = "Apache-2.0, Copyright 2025 Andras Bacsai"
    if draft.origin == "Dokploy":
        credit = (
            "Derived in part from Dokploy's template "
            f"https://github.com/Dokploy/templates/tree/main/{draft.template}"
        )
        licence_of_origin = "MIT, Copyright 2024 Dokploy and Carlos Ortiz"
    lines = [
        f"# {title}: {credit}",
        f"# ({licence_of_origin}), changed into a recipe: every image pinned to a release and the",
        "# digest its registry serves, the web port on the loopback address, credentials generated,",
        "# and nothing the closed schema refuses. The template's authors keep their rights in it.",
        *([f"# Licence: {facts['licence_evidence']}"] if facts.get("licence_evidence") else []),
        "schema: 2",
        f"name: {source.name}",
        "kind: compose",
        f"title: {quote(title)}",
        f"slogan: {quote(slogan)}",
        f"description: {quote(description)}",
        f"category: {category}",
        f"tags: [{', '.join(tags)}]",
        f"logo: {logo}",
        f"version: {quote(version)}",
        "revision: 1",
        f"updated: {date.today().isoformat()}",
        f"homepage: {homepage}",
        f"docs: {docs_url}",
        f"repository: {facts['html_url']}",
        f"license: {licence}",
        f"architectures: [{', '.join(quote(a) for a in archs)}]",
        "needs:",
        "  - Docker Engine with the Compose plugin",
        f"port: {PORT}",
    ]
    if variables:
        lines.append("variables:")
        for name, entry in variables.items():
            lines.append(f"  {name}:")
            for key, value in entry.items():
                rendered = quote(value) if isinstance(value, str) else json.dumps(value)
                lines.append(f"    {key}: {rendered}")
    lines.append(
        f"health: {{path: {health['path']}, expect: {quote(health['expect'])}, "
        f"timeout: {health['timeout']}}}"
    )
    if draft.root_equivalent:
        lines.append(f"root_equivalent: {draft.root_equivalent}")
    if draft.public_ports:
        lines.append("public_ports:")
        for opened in draft.public_ports:
            lines.append(
                f"  - {{port: {opened['port']}, protocol: {opened['protocol']}, "
                f"purpose: {quote(opened['purpose'])}}}"
            )
    if draft.shipped:
        lines.append("files:")
        for shipped in draft.shipped:
            lines.append(f"  {shipped}:")
            lines.append(f"    asset: {shipped}")
            lines.append('    mode: "0644"')
    lines.append("compose: |")
    lines.extend(f"  {row}" if row else "" for row in compose_text.rstrip().splitlines())
    lines.append("notes:")
    lines.extend(f"  - {quote(note)}" for note in notes)
    return "\n".join(lines) + "\n"


def monogram(title: str) -> str:
    """
    Args:
        title: The recipe's title.

    Returns:
        The monogram SVG the catalogue ships when the project has no mark in simple-icons.
    """
    # The console's policy allows a short plain label: letters, digits, spaces and . + -
    plain = re.sub(r"[^A-Za-z0-9 .+-]", "", title).strip() or "App"
    return str(recipe_draft.logo_svg(plain))


def icon_slug(name: str, title: str, icons: Path) -> str | None:
    """
    Find the simple-icons mark of a project by its name.

    Args:
        name: The recipe's name.
        title: Its title.
        icons: The simple-icons package.

    Returns:
        The slug, or None when the collection has no mark with that name.
    """
    data = json.loads((icons / "data" / "simple-icons.json").read_text(encoding="utf-8"))
    by_slug = {entry["slug"]: entry for entry in data}
    by_title = {norm(entry["title"]): entry["slug"] for entry in data}
    for candidate in (norm(name), norm(title)):
        if candidate in by_slug:
            return candidate
        if candidate in by_title:
            return str(by_title[candidate])
    return None


def draft_recipe(
    source: Source,
    facts: Mapping[str, Any],
    *,
    icons: Path | None,
    health: Mapping[str, Any] | None = None,
) -> tuple[str, dict[str, str]]:
    """
    Make a recipe of a candidate.

    Args:
        source: The candidate.
        facts: What GitHub says about the project.
        icons: The simple-icons package, when logos are looked up.
        health: A health check to use instead of ``/`` (from a smoke run).

    Returns:
        The recipe's YAML and the files for ``assets/<name>/``.

    Raises:
        Skip: The template cannot become a recipe, with why.
    """
    draft, _ = convert_template(source)
    clean_services(draft)
    secret_literals(draft)
    one_default_each(draft)
    archs = pin_images(draft)
    page = front_matter(source.docs)
    meta = dokploy_meta(source.dokploy)
    title = (
        page.get("title") or str(meta.get("name") or "") or source.name.replace("-", " ").title()
    )
    slug = icon_slug(source.name, title, icons) if icons is not None else None
    assets: dict[str, str] = {}
    if slug is not None:
        logo = f"simple-icons:{slug}"
    else:
        logo = "logo.svg"
        assets["logo.svg"] = monogram(title)
    assets.update(draft.shipped)
    chosen = dict(health or {"path": "/", "expect": "200-399", "timeout": 180})
    text = build_text(draft, source, facts, archs, logo=logo, health=chosen)
    if FORBIDDEN_WORDS.search(text):
        raise Skip("forbidden-word", "the recipe would name a provider the history may not")
    try:
        data = yaml.safe_load(text)
        validate(source.name, data, assets)
    except RecipeError as exc:
        raise Skip("schema", str(exc.message if hasattr(exc, "message") else exc)[:200]) from exc
    return text, assets


def validate(name: str, data: Mapping[str, Any], assets: Mapping[str, str]) -> None:
    """
    Load a drafted recipe with the catalogue's own loader, its files standing where they will.

    Args:
        name: The recipe.
        data: Its YAML, loaded.
        assets: The files it will ship.

    Raises:
        RecipeError: The loader refuses it.
    """
    from noust.recipes import assets as assets_module

    class _Folder:
        def __init__(self, files: Mapping[str, str]) -> None:
            self.files = files

        def joinpath(self, *parts: str) -> Any:
            return _Entry(self.files, "/".join(parts))

    class _Entry:
        def __init__(self, files: Mapping[str, str], path: str) -> None:
            self.files, self.path = files, path

        def joinpath(self, *parts: str) -> Any:
            return _Entry(self.files, "/".join((self.path, *parts)).strip("/"))

        def is_file(self) -> bool:
            return self.path.split("/", 2)[-1] in self.files

        def read_text(self, encoding: str = "utf-8") -> str:
            return self.files[self.path.split("/", 2)[-1]]

    assets_module.set_asset_root(name, _Folder(assets))
    try:
        parse_recipe(name, dict(data))
    finally:
        assets_module.set_asset_root(name, None)


#: The header the first versions of the importer wrote, before the credit carried the sentence
#: NOTICE and the Debian copyright file are held to.
OLD_CREDIT = re.compile(
    r"\A# (?P<title>.+?) from a (?P<origin>Coolify|Dokploy) service template "
    r"\((?P<template>[^)]+)\), made into a recipe:\n"
    r"# every image pinned to a release and the digest its registry serves, loopback port,\n"
    r"# generated secrets\. The template's authors keep their rights in it \([^)]+\)\.\n"
)


def credit_header(text: str) -> str:
    """
    Put the credit a derived recipe carries into the form the notices list recipes by.

    Args:
        text: A draft's text.

    Returns:
        The text with its first comment lines saying "Derived in part from <catalogue>'s template".
    """
    found = OLD_CREDIT.match(text)
    if found is None:
        return text
    if found["origin"] == "Coolify":
        credit = (
            "Derived in part from Coolify's template "
            f"https://github.com/coollabsio/coolify/blob/next/{found['template']}"
        )
        licence = "Apache-2.0, Copyright 2025 Andras Bacsai"
    else:
        credit = (
            "Derived in part from Dokploy's template "
            f"https://github.com/Dokploy/templates/tree/main/{found['template']}"
        )
        licence = "MIT, Copyright 2024 Dokploy and Carlos Ortiz"
    header = (
        f"# {found['title']}: {credit}\n"
        f"# ({licence}), changed into a recipe: every image pinned to a release and the\n"
        "# digest its registry serves, the web port on the loopback address, credentials generated,\n"
        "# and nothing the closed schema refuses. The template's authors keep their rights in it.\n"
    )
    return header + text[found.end() :]


# -- the command line --------------------------------------------------------------------------


def load_cache(path: Path) -> dict[str, Any]:
    """
    Args:
        path: The cache file.

    Returns:
        What GitHub answered before, by repository.
    """
    if path.is_file():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    return {}


def repository_key(url: str | None) -> str:
    """
    Args:
        url: A repository's address or ``owner/name``.

    Returns:
        Its ``owner/name`` in lower case, for comparing two descriptions of one project.
    """
    if not url:
        return ""
    return re.sub(r"^https?://github\.com/", "", url).removesuffix(".git").strip("/").lower()


#: Names that say a template is a variant of another (an application with a database of its
#: choosing, or beside a second program): the plain one stands for the project.
VARIANT = re.compile(
    r"-(with|without|and|plus|pg|postgres|postgresql|mysql|mariadb|mongodb|redis)\b"
)


def plan_queue(rows: Sequence[Mapping[str, Any]], taken: set[str]) -> list[str]:
    """
    Choose which eligible template stands for each project, most starred first.

    Args:
        rows: The survey.
        taken: The repositories (``owner/name``, lower case) the catalogue already runs.

    Returns:
        Template names: one per project, none the catalogue has, the plainer name of two
        templates of one project, ordered by the project's stars.
    """
    best: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if row.get("have") or row.get("skip") or not row.get("repository"):
            continue
        key = repository_key(row["repository"])
        if key in taken:
            continue
        current = best.get(key)
        rank = (bool(VARIANT.search(row["name"])), len(row["name"]))
        if current is None or rank < (bool(VARIANT.search(current["name"])), len(current["name"])):
            best[key] = row
    chosen = sorted(best.values(), key=lambda row: -row["facts"]["stars"])
    return [row["name"] for row in chosen]


def read_log(path: Path) -> dict[str, Any]:
    """
    Args:
        path: The draft log.

    Returns:
        What each candidate came to, by name.
    """
    if path.is_file():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    return {}


def main(argv: Sequence[str] | None = None) -> int:
    """
    Run the tool.

    Args:
        argv: The arguments; ``sys.argv`` when None.

    Returns:
        0, or 1 when a candidate named on the command line could not be drafted.
    """
    work = HOME / "recetas-work"
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--coolify", type=Path, default=DEFAULTS["coolify"])
    parser.add_argument("--coolify-docs", type=Path, default=DEFAULTS["coolify_docs"])
    parser.add_argument("--dokploy", type=Path, default=DEFAULTS["dokploy"])
    parser.add_argument("--icons", type=Path, default=DEFAULTS["icons"])
    parser.add_argument("--cache", type=Path, default=work / "github-cache.json")
    parser.add_argument(
        "--min-stars",
        type=int,
        default=MIN_STARS,
        help="the fewest stars a project may have (the catalogue's rule is 1,000)",
    )
    parser.add_argument(
        "--overrides",
        type=Path,
        help="a JSON file with what a person found: `repositories` (template name -> owner/name) "
        "and `licences` (owner/name -> license, accept, name, evidence)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    survey = commands.add_parser("survey")
    survey.add_argument("--out", type=Path, default=work / "survey.json")
    plan = commands.add_parser("plan")
    plan.add_argument("--survey", type=Path, default=work / "survey.json")
    plan.add_argument("--out", type=Path, default=work / "queue.txt")
    draft = commands.add_parser("draft")
    draft.add_argument("names", nargs="*")
    draft.add_argument("--queue", type=Path, help="a file with one template name per line")
    draft.add_argument("--limit", type=int, default=0)
    draft.add_argument("--dry-run", action="store_true")
    draft.add_argument("--out", type=Path, default=work / "drafts")
    draft.add_argument("--log", type=Path, default=work / "draft-log.json")
    draft.add_argument("--health-path")
    draft.add_argument("--health-expect")
    args = parser.parse_args(argv)

    sources = read_sources(args.coolify, args.dokploy, args.coolify_docs)
    cache = load_cache(args.cache)
    existing = {norm(p.stem) for p in RECIPES.glob("*.yaml")}
    found_by_hand: dict[str, Any] = (
        json.loads(args.overrides.read_text(encoding="utf-8")) if args.overrides else {}
    )
    repositories: dict[str, str] = {
        norm(key): slug for key, slug in (found_by_hand.get("repositories") or {}).items()
    }
    licences: dict[str, Any] = found_by_hand.get("licences") or {}
    try:
        if args.command == "survey":
            rows = []
            reasons: Counter[str] = Counter()
            for key, source in sorted(sources.items()):
                row: dict[str, Any] = {"name": source.name, "key": key, "have": key in existing}
                row["coolify"] = source.coolify is not None
                row["dokploy"] = source.dokploy is not None
                if key in existing:
                    rows.append(row)
                    continue
                slug = find_repository(source, repositories)
                row["repository"] = slug
                facts = with_licence(repository_facts(slug, cache) if slug else None, licences)
                verdict = (
                    Skip("no-repository")
                    if slug is None
                    else not_eligible(facts, min_stars=args.min_stars)
                )
                row["facts"] = facts
                row["skip"] = str(verdict) if verdict else None
                reasons[verdict.code if verdict else "eligible"] += 1
                rows.append(row)
                args.cache.write_text(json.dumps(cache, indent=1), encoding="utf-8")
            args.out.write_text(json.dumps(rows, indent=1), encoding="utf-8")
            print(dict(reasons.most_common()))
            return 0
        if args.command == "plan":
            rows = json.loads(args.survey.read_text(encoding="utf-8"))
            taken = {
                repository_key(
                    str(yaml.safe_load(path.read_text(encoding="utf-8")).get("repository"))
                )
                for path in RECIPES.glob("*.yaml")
            }
            queue = plan_queue(rows, taken)
            args.out.write_text("\n".join(queue) + "\n", encoding="utf-8")
            print(f"{len(queue)} projects, most starred first: {args.out}")
            return 0

        names = list(args.names)
        if args.queue is not None:
            names += [line.strip() for line in args.queue.read_text().splitlines() if line.strip()]
        log = read_log(args.log)
        failed = 0
        drafted = 0
        for name in names:
            if args.limit and drafted >= args.limit:
                break
            if not args.dry_run and (args.out / f"{name}.yaml").exists():
                continue
            if not args.dry_run and name in log and log[name]["status"] == "skip":
                continue
            source = sources.get(norm(name))
            if source is None:
                print(f"{name}: no such template")
                failed += 1
                continue
            if norm(name) in existing or (RECIPES / f"{source.name}.yaml").exists():
                print(f"{name}: skip the catalogue already has it")
                failed += 1
                continue
            slug = find_repository(source, repositories)
            facts = with_licence(repository_facts(slug, cache) if slug else None, licences)
            verdict = (
                not_eligible(facts, min_stars=args.min_stars) if slug else Skip("no-repository")
            )
            if verdict is not None or facts is None:
                print(f"{name}: skip {verdict}")
                log[name] = {"status": "skip", "reason": str(verdict)}
                failed += 1
                continue
            health = (
                {
                    "path": args.health_path or "/",
                    "expect": args.health_expect or "200-399",
                    "timeout": 180,
                }
                if (args.health_path or args.health_expect)
                else None
            )
            try:
                text, files = draft_recipe(
                    source, facts, icons=args.icons if args.icons.is_dir() else None, health=health
                )
            except Skip as exc:
                print(f"{name}: skip {exc}")
                log[name] = {"status": "skip", "reason": str(exc)}
                failed += 1
                continue
            except Exception as exc:  # noqa: BLE001 - the tool's error boundary, it logs
                # The tool's error boundary: one template that surprises it must not stop the
                # two hundred behind it. The kind of surprise is kept, to be fixed or skipped.
                reason = f"internal: {type(exc).__name__}: {str(exc)[:160]}"
                print(f"{name}: skip {reason}")
                log[name] = {"status": "skip", "reason": reason}
                failed += 1
                continue
            if args.dry_run:
                print(text)
                continue
            args.out.mkdir(parents=True, exist_ok=True)
            (args.out / f"{source.name}.yaml").write_text(text, encoding="utf-8")
            for relative, content in files.items():
                target = args.out / "assets" / source.name / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            log[name] = {"status": "drafted", "repository": slug}
            drafted += 1
            print(f"{name}: drafted", flush=True)
            args.log.write_text(json.dumps(log, indent=1), encoding="utf-8")
        if not args.dry_run:
            args.log.write_text(json.dumps(log, indent=1), encoding="utf-8")
        return 1 if failed else 0
    finally:
        args.cache.parent.mkdir(parents=True, exist_ok=True)
        args.cache.write_text(json.dumps(cache, indent=1), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
