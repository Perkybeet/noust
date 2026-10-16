#!/usr/bin/env python3
"""
Generate ``docs/llms.txt`` (and, on request, ``docs/llms-full.txt``) from the documentation.

Noust has no documentation website by the owner's decision, so the documents an
client should read are the ones in ``docs/``. ``llms.txt`` is their index in the
https://llmstxt.org format (a title, a summary and links with a sentence each).
It is generated, never edited: CI runs this with ``--check`` and fails when the
committed copy is stale. It changes only when a document is added or retitled.

``llms-full.txt`` is the same documents in one file, for a client that wants
everything in one request. It changes with every edit of any document, so it is
not committed: ``--full`` writes it (a release publishes it).

Usage:
    scripts/generate_llms.py            Write docs/llms.txt.
    scripts/generate_llms.py --check    Exit 1 when docs/llms.txt is out of date.
    scripts/generate_llms.py --full     Also write docs/llms-full.txt.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
RAW = "https://raw.githubusercontent.com/Perkybeet/noust/main/docs"

TITLE = "Noust"
SUMMARY = (
    "Noust deploys web applications on Linux servers: Nginx or Apache, TLS, systemd, databases and "
    "backups, every deploy a health-gated release with instant rollback, and an optional browser "
    "console over a JSON API."
)
INTRO = (
    "Noust (previously WASM) is a Python 3.10+ command line, run as root on the server it manages, "
    "with a browser console and a JSON API at /api (OpenAPI at /api/openapi.json). Other servers, "
    "scripts and MCP clients use the API with a bearer token: the command line can drive a server "
    "remotely (noust --context NAME ...), and POST /api/mcp speaks the Model Context Protocol. "
    "Variables' values and secrets are never given to a client."
)

#: The documents a client should read, in order, with what each is for. A name here that is not in
#: ``docs/`` fails the build: a link to nothing is worse than none.
DOCUMENTS: tuple[tuple[str, str], ...] = (
    (
        "api.md",
        "The JSON API: authentication, tokens, errors, versions, jobs, events and every area",
    ),
    (
        "mcp.md",
        "The MCP endpoint: tools, prompts, resources, guardrails and how to connect a client",
    ),
    (
        "remote.md",
        "Operating a server from elsewhere: contexts, tokens that never go in an argument, exit codes",
    ),
    ("manifest.md", "A server written down: validate, plan, apply and export a manifest"),
    (
        "security.md",
        "The security model: roles, tokens, sudo mode, four eyes, audit, what is not defended",
    ),
    ("teams.md", "Teams, projects and environments; roles on a project; tokens bound to a team"),
    ("sso.md", "Single sign-on with OpenID Connect and turning the password sign-in off"),
    ("console.md", "The browser console, screen by screen"),
    ("CONFIG.md", "Every configuration setting, its default and what it does"),
    ("releases.md", "Releases, the health gate and rollback"),
    ("compose.md", "Docker Compose applications, hooks, the relay and adopting a stack"),
    ("containers.md", "Container applications: a Dockerfile, a registry image or Railpack"),
    ("build.md", "How an application is built: commands, runtimes, the sandbox"),
    ("recipes.md", "The catalogue of applications that install with one command"),
    ("databases.md", "Databases: engines, instances, access, backups and imports"),
    ("domains.md", "Domains, certificates and DNS"),
    ("disaster-recovery.md", "Backing up Noust itself and restoring a server"),
    ("terminal.md", "The terminal and one-off commands in the browser"),
    ("CENTRAL.md", "Running a central that manages several servers"),
    ("MONITOR.md", "The monitor, observations and notifications"),
    ("probes.md", "Probes of an address from outside the application, and a public status page"),
    ("ENS.md", "The ENS category MEDIUM profile and its evidence"),
    ("PLATFORMS.md", "Supported operating systems, processors and the installer"),
)


def title_of(path: Path) -> str:
    """
    Args:
        path: A Markdown document.

    Returns:
        Its first level-one heading.

    Raises:
        SystemExit: When it has none.
    """
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    raise SystemExit(f"{path} has no title")


def render_index() -> str:
    """
    Returns:
        The content of ``docs/llms.txt``.
    """
    lines = [f"# {TITLE}", "", f"> {SUMMARY}", "", INTRO, "", "## Documentation", ""]
    for name, purpose in DOCUMENTS:
        path = DOCS / name
        if not path.exists():
            raise SystemExit(
                f"docs/{name} is listed in scripts/generate_llms.py and does not exist"
            )
        lines.append(f"- [{title_of(path)}]({RAW}/{name}): {purpose}")
    lines += [
        "",
        "## Optional",
        "",
        f"- [The documentation in one file]({RAW}/llms-full.txt): every document above, in order "
        "(published with each release)",
        "",
    ]
    return "\n".join(lines)


def render_full() -> str:
    """
    Returns:
        The content of ``docs/llms-full.txt``.
    """
    parts = [f"# {TITLE} documentation", "", f"> {SUMMARY}", ""]
    for name, _purpose in DOCUMENTS:
        text = (DOCS / name).read_text(encoding="utf-8").rstrip("\n")
        parts += [f"<!-- docs/{name} -->", "", text, ""]
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    """
    Args:
        argv: Arguments; the process's by default.

    Returns:
        0, or 1 when ``--check`` finds a stale file.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else "")
    parser.add_argument("--check", action="store_true", help="Fail when llms.txt is out of date.")
    parser.add_argument("--full", action="store_true", help="Also write llms-full.txt.")
    args = parser.parse_args(argv)
    outputs = {DOCS / "llms.txt": render_index()}
    if args.full:
        outputs[DOCS / "llms-full.txt"] = render_full()
    stale = [
        path
        for path, content in outputs.items()
        if not path.exists() or path.read_text(encoding="utf-8") != content
    ]
    if args.check:
        for path in stale:
            print(
                f"{path.relative_to(ROOT)} is out of date: run scripts/generate_llms.py",
                file=sys.stderr,
            )
        return 1 if stale else 0
    for path, content in outputs.items():
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
