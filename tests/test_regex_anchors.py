# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A pattern anchored with ``$`` and applied with ``.match`` is not a whole-string check
(checklist L-09).

In Python ``$`` matches at the end of the string *and just before a trailing
newline*, so ``re.compile(r"^[a-z]+$").match("shop\\n")`` matches. For a year the
allowlist behind ``validate_app_name`` was exactly that, and the documentation
said newlines were refused. The check that means "the whole string" is
``fullmatch``.

This test finds every pattern that starts with ``^`` and ends with ``$`` and is
applied with ``.match`` or ``.search``, in the source tree. The sites left are
parsers of what a tool printed (a line of ``apt``, ``dnf``, ``certbot``,
``journalctl``): the tool ended the line, not a person, and a pattern that has to
accept the line's own ending would break on ``fullmatch``. They are listed
below, with the reason, and the list can only shrink: a new site fails here, and a
site that was fixed must come off the list.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "noust"

#: Sites that parse the output of a program or a file Noust wrote, not text a
#: person or a repository supplied. ``path::name`` is the file and the pattern.
PARSES_WHAT_A_TOOL_PRINTED = frozenset(
    {
        "managers/server/pkg/apt.py::_INST",
        "managers/server/pkg/apt.py::_CONFIG_LINE",
        "managers/server/pkg/dnf.py::_ADVISORY",
        "managers/server/pkg/dnf.py::_NVRA",
        "managers/server/pkg/zypper.py::_UNIT_NAME",
        "managers/server/journal.py::_BOOT_LINE",
        "managers/server/security_sudoers.py::_INCLUDE",
        "managers/server/security_sudoers.py::_DEFAULTS_LINE",
        "managers/server/security_sudoers.py::_ALIAS_LINE",
        "managers/server/storage.py::_IMAGE_ID",
        "managers/cert_manager.py::_SUBJECT_NAME",
        "managers/cert_manager.py::_ALT_NAME",
        "managers/cron_manager.py::_ELAPSE_TIMESTAMP",
        "managers/backup_destinations.py::_RCLONE_TIME_RE",
        "deployers/docker_compose.py::_CONTAINER_ID",
        "deployers/docker_compose.py::_UNIT_COMPOSE_FILE",
        "deployers/helpers/health_gate.py::_ATTEMPT",
        "deployers/helpers/php_fpm.py::_POOL_PATH",
        "deployers/helpers/php_fpm.py::_DEBIAN_VERSION",
        "deployers/helpers/compose_ports.py::_PORT_RANGE",
        "deployers/importers/heroku.py::_PROCFILE_LINE",
        "web/job_reconcile.py::_LOG_LINE",
        "managers/server/pkg/dnf.py::re.match[^s**s+(.+)$]",
        "managers/server/pkg/zypper.py::pattern",
        "managers/cron_manager.py::_NORMALIZED_FORM",
        "core/package_index.py::_ONE_LINE",
        "core/notifications/excerpt.py::_JOURNAL_HEADING",
        "core/audit/log.py::_STAMP",
    }
)

#: Version strings read from upstream tags and files Noust parses leniently.
PARSES_A_VERSION_STRING = frozenset(
    {
        "recipes/versions.py::_NUMBER",
        "core/tags.py::_VERSION",
        "core/versions.py::_VERSION_TEXT",
        "core/versions.py::_BARE_VERSION",
        "core/versions.py::_LTS",
        "core/versions.py::_PIN",
        "core/versions.py::re.match[^(===|==|!=|~=|>=|<=|>|<)s*(.]",
        "core/versions.py::re.match[^(^|~>|~|>=|<=|>|<|=)?s*(.*)]",
        "core/versions.py::re.match[^s*(S+)s+-s+(S+)s*$]",
    }
)

ALLOWED = PARSES_WHAT_A_TOOL_PRINTED | PARSES_A_VERSION_STRING


def literal(node: ast.expr) -> str | None:
    """The text of a string literal or f-string (the parts that are not fields)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value
            for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
    return None


def whole_string(pattern: str) -> bool:
    """Whether a pattern is written as if it matched the whole string."""
    return pattern.startswith("^") and pattern.endswith("$") and not pattern.endswith("\\$")


def offenders() -> set[str]:
    found: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        relative = path.relative_to(SRC).as_posix()
        compiled: dict[str, str] = {}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr == "compile"
                and node.value.args
            ):
                text = literal(node.value.args[0])
                if text is not None and whole_string(text):
                    for target in node.targets:
                        compiled[ast.unparse(target)] = text
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr not in ("match", "search"):
                continue
            receiver = ast.unparse(node.func.value)
            if receiver in compiled:
                found.add(f"{relative}::{receiver}")
            elif receiver == "re" and node.args:
                text = literal(node.args[0])
                if text is not None and whole_string(text):
                    # Backslashes left out: the key is read by people, not matched.
                    found.add(f"{relative}::re.{node.func.attr}[{text[:30].replace(chr(92), '')}]")
    return found


def test_no_new_pattern_judges_text_with_a_dollar_and_match() -> None:
    new = offenders() - ALLOWED

    assert new == set(), (
        "A pattern anchored with ^...$ and applied with .match or .search accepts a trailing "
        "newline. Use .fullmatch (or \\Z). Offenders:\n  " + "\n  ".join(sorted(new))
    )


def test_every_site_that_was_allowed_still_needs_to_be() -> None:
    stale = ALLOWED - offenders()

    assert stale == set(), (
        "These sites no longer have the problem (fixed or removed): take them off the list so "
        "the list only shrinks:\n  " + "\n  ".join(sorted(stale))
    )
