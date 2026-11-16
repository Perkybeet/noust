# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The sentences of roles that the server says for the console to translate (item 151).

A table of :mod:`noust.core.server_text`, which merges it into its catalog: one sentence is
its entry here and a code to ask for it, and the console's own catalogs say it in Spanish. Kept
apart so the catalog does not grow past the size a module may have."""

from __future__ import annotations

SENTENCES: dict[str, str] = {
    "team_access.governs_members": "{role_name} governs a team's members, which a {kind} does not have",
    "team_access.governs_members.hint": "Give 'admin' on the {kind}, or {role_name} on the team.",
    "team_access.name_environment": "Name the environment",
    "team_access.name_environment.hint": "Give the environment's slug (environment=SLUG).",
    "team_access.name_project": "Name the project",
    "team_access.name_project.hint": "Give the project's slug (project=SLUG).",
    "team_access.name_team": "Name the team",
    "team_access.name_team.hint": "Give the team's slug (team=SLUG).",
    "team_access.no_such_account": "No account named {username}",
    "team_access.no_such_account.hint": "List the accounts with 'noust user list', or invite the person.",
    "team_access.no_such_role": "No role named {role}",
    "team_access.no_such_role.hint": "List them with 'noust access role list'.",
    "team_access.role_not_ownable": "The role {role_name} holds nothing a {kind} can own",
    "team_access.role_not_ownable.hint": "A team, a project or an environment can hold permissions about its applications, databases and backups; this role has none of those.",
}
