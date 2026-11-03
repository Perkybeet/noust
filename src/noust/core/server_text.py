# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The sentences the server says for the console to translate.

The console speaks English and Spanish, and a sentence that arrives from the server as a bare
string can only be shown as it came: a Spanish console with an English paragraph in it, which
is what an operator saw in Domains > Tunnels and in the installation check. A sentence that a
person is meant to read therefore carries, beside its English text, a **code** and its
**parameters**. The console looks the code up in its own catalog (``serverText.<code>``) and
puts the parameters in; when it has no entry for a code it shows the English text, which is
always sent, so a server newer than its console still says something true.

This module is the one place the English sentences live (rule 3): the CLI prints
:attr:`ServerText.text`, the API sends it together with :meth:`ServerText.to_dict`, and a test
holds the catalog to the code that asks for it. A sentence is a whole sentence with
placeholders, never a fragment to glue to another one, because word order is not the same in
both languages.

**A sentence's fix** is the catalog entry named like the sentence plus :data:`HINT_SUFFIX`
(``tunnels.store_sealed`` and ``tunnels.store_sealed.hint``): it is said with the same values,
and a sentence without one has none. One code in a response therefore tells the console both
the problem and the way out.

**What is never a sentence here**: another program's own output (nginx's, systemd's,
certbot's), a command to run, a path, a version a program printed, a name somebody typed, a
control's id, a person. They are values, sent as parameters or as the text beside the code, and
the console shows them as they are (``docs/api.md``, "Sentences with a code"). A value named
``command``, or ending in ``_command``, is a command: :func:`is_command`.

**The console is held to this catalog.** :func:`typescript_codes` writes the table
``panel/src/api/serverTextCodes.gen.ts`` (``scripts/generate_server_text.py``), and a test of the
console fails for a code without an entry in English and in Spanish, with the same values. There
is no other way for a sentence to reach the console with a code: a module that says something to
a person in words of its own (a flag, a ``what``/``how`` pair, a dict of fields) has not been
given codes yet.
"""

from __future__ import annotations

import re
import string
from collections.abc import Container, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

#: What a value may be: the console puts these in a sentence, nothing structured.
Param = str | int | float | bool

#: Appended to a sentence's code to name its fix.
HINT_SUFFIX = ".hint"

#: What a call that waits for a second person's decision is told to do next, said twice below.
_SEND_AGAIN = (
    "Once approved, send exactly the same call again with the header {header}: {request}, "
    "within {minutes} minutes."
)

#: Every sentence, by code. Grouped by the area that says it. ``tests/test_server_text.py`` holds
#: it to the code: a code asked for and absent fails, and a sentence nobody says fails.
CATALOG: dict[str, str] = {
    # -- Tunnels (Domains > Tunnels): what is missing before one can be made --------------
    "tunnels.cloudflared_missing": "cloudflared is not installed",
    "tunnels.cloudflared_missing.hint": (
        "Noust installs it from Cloudflare's own package repository, {url}, with the key Cloudflare "
        "signs it with: run 'noust tunnel install-cloudflared', then this command again."
    ),
    "tunnels.store_sealed": "The secret store is sealed",
    "tunnels.store_sealed.hint": (
        "A tunnel's connector reads its token at boot, with nobody to type a passphrase. "
        "Run tunnels on a server, not on a sealed central."
    ),
    "tunnels.no_account": "There is no Cloudflare account to manage a tunnel with",
    "tunnels.no_account.hint": (
        "Add one with Account > Cloudflare Tunnel:Edit: "
        "noust dns provider add NAME --kind cloudflare --token-stdin"
    ),
    # -- Database engines (Databases > New database, Install an engine): why one cannot be --
    # installed on this server. The sentence and its fix, which together are the English reason
    # the API has always sent (``noust.managers.database.flavours``).
    "database_engines.conflict_mysql": (
        "{installed} is installed, and {requested} cannot run beside it: they share "
        "port 3306, their packages and /var/lib/mysql."
    ),
    "database_engines.conflict_mysql.hint": "Remove {installed} first if you mean to replace it.",
    "database_engines.conflict_redis": (
        "{installed} is installed, and {requested} cannot run beside it: they share "
        "port 6379 and their packages."
    ),
    "database_engines.conflict_redis.hint": "Remove {installed} first if you mean to replace it.",
    "database_engines.no_package_manager": (
        "Noust installs {engine} with apt, dnf or zypper, and this system has none of them."
    ),
    "database_engines.no_package_manager.hint": (
        "Run it as an instance, a container that needs no package manager ({command}), or "
        "install it with the distribution's package manager: Noust manages it from there."
    ),
    "database_engines.redis_not_shipped": "{system} does not ship Redis",
    "database_engines.redis_not_shipped.hint": "Install Valkey instead: run '{command}'.",
    "database_engines.valkey_not_shipped": "{system} does not ship Valkey",
    "database_engines.valkey_not_shipped.hint": (
        "Valkey is packaged from Debian 13 and Ubuntu 24.04 on. Install Redis instead: "
        "run '{command}'."
    ),
    "database_engines.not_available": "Noust cannot install {engine} on {system}",
    "database_engines.not_available.hint": (
        "Run it as an instance, a container that needs no package manager ({command}), or "
        "install it with the system's package manager: Noust manages it from there."
    ),
    # -- What a reboot would break (noust.managers.server.power, the checks before a reboot) ---
    # What each check found, as the one sentence under its title in the reboot dialog. A list of
    # names (applications, jobs) and what findmnt said are values, shown as they are.
    "power_checks.jobs_running": "Running now: {jobs}",
    "power_checks.jobs_idle": "Nothing is running",
    "power_checks.console_returns": "The console starts on boot",
    "power_checks.console_stays_down": (
        "The console is not an enabled systemd unit: it will not come back after the "
        "reboot. Run 'noust web enable' first."
    ),
    "power_checks.apps_stay_down": (
        "These applications are not enabled and will not start again: {apps}"
    ),
    "power_checks.apps_return": "Every application starts on boot",
    "power_checks.fstab_correct": "fstab is correct",
    "power_checks.fstab_problem": (
        "findmnt --verify found a problem, and a bad fstab can leave the machine "
        "in emergency mode: {output}"
    ),
    "power_checks.ramdisk_missing": "The newest kernel, {release}, has no initial ramdisk in /boot",
    "power_checks.ramdisk_present": "The newest kernel is complete",
    # -- A person, when a central acted for them (noust.core.audit.actor) -----------------------
    "actors.through_central": "{person} (from the central {central})",
    # -- What an operator may call an object (noust.validators.object_names) ----------------
    "names.rule.words": (
        "Use lower-case letters, digits, '-' and '_', starting with a letter, "
        "at most {max} characters."
    ),
    "names.rule.dashes": (
        "Use lower-case letters, digits and single dashes, starting with a letter, "
        "at most {max} characters."
    ),
    "names.rule.underscores": (
        "Use lower-case letters, digits and single underscores, starting with a letter, "
        "at most {max} characters."
    ),
    # -- A fleet view: why some servers did not show their part of it --------------------------
    # One sentence for all the servers that refused the same permission; the console counts and
    # names them from the group, so no number or list of names is glued into it.
    "fleet.refused.permission": (
        "Your role {role} does not hold the permission {permission} on these servers"
    ),
    "fleet.refused.permission.hint": (
        "Ask a security officer of each of them for an account with a role that holds it, "
        "or have that permission added to your role there."
    ),
    # -- A fleet job's title (noust.core.fleet_job_titles): the name its console job carries ----
    # One sentence for one server and one for several, because the plural of "server" is not a
    # suffix in every language. The job's name is this text, so the console, a notice and the
    # activity of the Overview say the same thing in the language of whoever reads.
    "fleet_job.certs_renew.single": "Renew certificates on 1 server",
    "fleet_job.certs_renew.several": "Renew certificates on {count} servers",
    "fleet_job.backups_run.single": "Back up applications now on 1 server",
    "fleet_job.backups_run.several": "Back up applications now on {count} servers",
    "fleet_job.backups_verify.single": "Verify the newest backups on 1 server",
    "fleet_job.backups_verify.several": "Verify the newest backups on {count} servers",
    "fleet_job.apps_update.single": "Update applications on 1 server",
    "fleet_job.apps_update.several": "Update applications on {count} servers",
    "fleet_job.apps_restart.single": "Restart applications on 1 server",
    "fleet_job.apps_restart.several": "Restart applications on {count} servers",
    "fleet_job.noust_update.single": "Update Noust on 1 server",
    "fleet_job.noust_update.several": "Update Noust on {count} servers",
    "fleet_job.index_refresh.single": "Refresh package lists on 1 server",
    "fleet_job.index_refresh.several": "Refresh package lists on {count} servers",
    "fleet_job.os_updates.single": "Apply operating system updates on 1 server",
    "fleet_job.os_updates.several": "Apply operating system updates on {count} servers",
    "fleet_job.settings_apply.single": "Apply the fleet's settings on 1 server",
    "fleet_job.settings_apply.several": "Apply the fleet's settings on {count} servers",
    "fleet_job.placement_rollout.single": "Roll an application out on 1 server",
    "fleet_job.placement_rollout.several": "Roll an application out on {count} servers",
    "fleet_job.placement_copy.single": "Copy an application to 1 server",
    "fleet_job.placement_copy.several": "Copy an application to {count} servers",
    "fleet_job.placement_ship.single": "Build on one server, run on others on 1 server",
    "fleet_job.placement_ship.several": "Build on one server, run on others on {count} servers",
    "fleet_job.database_transfer.single": "Move a database to another server",
    "fleet_job.database_transfer.several": "Move a database to {count} servers",
    "fleet_job.dns_cluster.single": "Set up Noust DNS on 1 server",
    "fleet_job.dns_cluster.several": "Set up Noust DNS on {count} servers",
    # -- The recipe catalogue: why a recipe is listed and cannot be installed ---------------
    "recipes.unavailable.licence": "Its licence does not let an operator run it for others",
    "recipes.unavailable.licence.hint": (
        "If you hold a licence that does, deploy it from its own repository with 'noust create'."
    ),
    "recipes.unavailable.not_for_production": (
        "Its own project says this edition is not for production"
    ),
    "recipes.unavailable.not_for_production.hint": (
        "Use the edition the project supports for production, deployed from its repository."
    ),
    "recipes.unavailable.no_image": "The project publishes no container image that could be pinned",
    "recipes.unavailable.no_image.hint": (
        "Deploy it from the project's repository with 'noust create', or add a Dockerfile of your "
        "own."
    ),
    "recipes.unavailable.not_an_application": (
        "It is a framework to build an application with, not an application to install"
    ),
    "recipes.unavailable.not_an_application.hint": (
        "Deploy your own project from its repository with 'noust create'."
    ),
    "recipes.unavailable.host_ports": (
        "It needs ports that the server's web server and mail already hold"
    ),
    "recipes.unavailable.host_ports.hint": "Run it on a server of its own.",
    "recipes.unavailable.archived": "The project is archived and no longer maintained",
    "recipes.unavailable.archived.hint": (
        "Use the recipe that replaces it, when the catalogue names one."
    ),
    "recipes.unavailable.platform_stack": (
        "It is a platform of dozens of services with a proxy of its own"
    ),
    "recipes.unavailable.platform_stack.hint": (
        "Run it on a server of its own with the project's installer."
    ),
    "recipes.unavailable.no_sign_in": (
        "Its web interface has no sign-in of its own and holds the keys it works with"
    ),
    "recipes.unavailable.no_sign_in.hint": (
        "Reach it through an SSH tunnel to the server, as its project documents, never through a "
        "public address."
    ),
    "recipes.unavailable.root_equivalent": (
        "It controls the server's Docker daemon, which is the same as root on the server"
    ),
    "recipes.unavailable.root_equivalent.hint": (
        "Deploy it yourself with 'noust create' if you accept that, on a server that is yours."
    ),
    # -- The recipe catalogue: what is root on the server by design ---------------------------
    "recipes.root_equivalent.docker_socket": (
        "{title} controls the Docker daemon of this server, which is the same as having root on it"
    ),
    "recipes.root_equivalent.docker_socket.hint": (
        "It can start any container with any part of the server mounted, Noust's applications "
        "and their data included. Install it only if you would give this application root on the "
        "server."
    ),
    "recipes.root_equivalent.agent": (
        "{title} is an AI agent that runs commands, and whoever can operate it can make it do "
        "anything it can do: access to it is the same as access to a shell"
    ),
    "recipes.root_equivalent.agent.hint": (
        "Its own security policy says it is not a boundary between users. Give its sign-in only "
        "to people you would give a shell on this server, and give it no model key or account "
        "you would not hand to them."
    ),
    "recipes.root_equivalent.confirm_required": (
        "{title} is root on this server, and installing it needs your confirmation"
    ),
    "recipes.root_equivalent.confirm_required.hint": (
        "If you accept that, confirm it and install again: {command}"
    ),
    # -- Mail an account is sent (an invitation, a password reset) -------------------------
    "mail.no_transport": "There is no way to send mail from this server",
    "mail.no_transport.hint": (
        "Set monitor.smtp.* or monitor.email_api.* (Settings > Channels), then ask again."
    ),
    "mail.no_public_url": "The console has no public address to put in the link",
    "mail.no_public_url.hint": "Set web.public_url to the address people open the console at.",
    "invitation.mail_failed": "The invitation could not be mailed: {reason}",
    "invitation.mail_failed.hint": "The invitation exists: hand its link over yourself.",
    # -- Roles: one list, one protected owner, several roles per account (4.1) --------------
    "roles.unknown": "There is no role named {name}",
    "roles.unknown.hint": "List the roles with: {command}",
    "roles.name_invalid": "Invalid role name: {name}",
    "roles.name_invalid.hint": (
        "Use 2 to 40 lower-case letters, digits or '-', starting and ending with a letter or "
        "digit, such as 'release-manager'."
    ),
    "roles.name_system": "{name} is the name of a built-in role",
    "roles.name_system.hint": (
        "Pick another name. The built-in roles can be edited and put back, not redefined under "
        "their own name."
    ),
    "roles.name_taken": "A role named {name} already exists",
    "roles.name_taken.hint": "Pick another name, or look at that one with: {command}",
    "roles.too_many": "A server keeps at most {max} roles of its own",
    "roles.too_many.hint": "Remove one nobody holds. List them with: {command}",
    "roles.owner_protected": "The owner role cannot be changed or deleted",
    "roles.owner_protected.hint": (
        "It holds every permission there is, so that the server always has somebody who can do "
        "everything. To give a person all of it, give them the owner role."
    ),
    "roles.owner_by_owner": "Only an owner can give or take the owner role",
    "roles.owner_by_owner.hint": (
        "Ask an account that has it. On the server itself, root can: noust user roles."
    ),
    "roles.last_owner": "{username} is the last account that can use the owner role",
    "roles.last_owner.hint": (
        "A server always keeps somebody who can do everything. Give the owner role to another "
        "account that can sign in first, then this change can go ahead."
    ),
    "roles.in_use": "The role {name} is still given to {count} account(s) or place(s)",
    "roles.in_use.hint": "See who holds it with: {command}, take it from them, then delete it.",
    "roles.seeded_kept": "{name} ships with Noust, so it cannot be deleted",
    "roles.seeded_kept.hint": "Change what it holds, or put it back to how it comes with: {command}",
    "roles.reset_custom": "{name} is a role of your own, so there is no shipped version to go back to",
    "roles.reset_custom.hint": "Change it, or remove it with: {command}",
    "roles.permissions_unknown": "Unknown permissions: {names}",
    "roles.permissions_unknown.hint": "List every permission there is with: {command}",
    "roles.permissions_none": "A role needs at least one permission",
    "roles.permissions_none.hint": "Pick what it may do, such as apps.read and apps.deploy.",
    "roles.permissions_many": "A role holds at most {max} permissions",
    "roles.no_roles": "An account needs at least one role",
    "roles.no_roles.hint": "Give it one, such as viewer.",
    "roles.separation_role": "{subject} would govern access and run the server at once",
    "roles.separation_role.hint": (
        "It holds {governing} (who may run the server) together with {running} (running it). "
        "Separation of duties keeps them in different hands: split it into two roles."
    ),
    "roles.separation_account": "{username} would govern access and run the server at once",
    "roles.separation_account.hint": (
        "Its roles together hold {governing} (who may run the server) with {running} (running "
        "it). Separation of duties keeps them in different hands: give the person a second "
        "account for the other duty."
    ),
    "roles.auditor_exclusive": (
        "{username} is an auditor, who checks everybody and holds nothing more"
    ),
    "roles.auditor_exclusive.hint": "Use another account for what it should be able to do.",
    "roles.offer_unknown": "{permission} is not on offer for the role {name}",
    "roles.offer_unknown.hint": "See what is on offer with: {command}",
    # -- The installation check (Settings > About, noust setup doctor) ---------------------
    # Sections: the heading's text is its own key in the console; only the sentence that
    # explains one is here.
    "doctor.platform.supported": "{label}: supported",
    "doctor.platform.pip_only": "{label}: installed with pip, there is no package for it",
    "doctor.platform.best_effort": "{label}: best effort, not a tested platform",
    "doctor.platform.unsupported": "{label}: not a platform Noust declares support for",
    "doctor.platform.eol": "{release} stops receiving security updates on {date}",
    "doctor.platform.explain": "The platforms Noust installs on are listed in docs/PLATFORMS.md",
    "doctor.memory.unreadable": "Memory: could not be read",
    "doctor.memory.below_floor": "Memory: {amount}, under the {floor} Noust needs to run",
    "doctor.memory.below_floor_swap": (
        "Memory: {amount}, swap {swap}, under the {floor} Noust needs to run"
    ),
    "doctor.memory.below_minimum": "Memory: {amount}, under the {minimum} a server is sized for",
    "doctor.memory.below_minimum_swap": (
        "Memory: {amount}, swap {swap}, under the {minimum} a server is sized for"
    ),
    "doctor.memory.ok": "Memory: {amount} (minimum {minimum})",
    "doctor.memory.ok_swap": "Memory: {amount}, swap {swap} (minimum {minimum})",
    "doctor.memory.note": (
        "Builds are what need memory; a swap file lets one that needs more than the server has "
        "finish slowly instead of being killed."
    ),
    "doctor.cpu.unreadable": "Processors: could not be read",
    "doctor.cpu.below_floor": "Processors: {amount} (Noust needs at least {floor} to run)",
    "doctor.cpu.below_minimum": "Processors: {amount} (a server is sized for at least {minimum})",
    "doctor.cpu.ok": "Processors: {amount} (minimum {minimum})",
    "doctor.cpu.note": "Noust and a build need at least one.",
    "doctor.disk.unreadable": "Free disk: could not be read",
    "doctor.disk.below_floor": (
        "Free disk: {free} free of {total} at {path}, under the {floor} Noust needs to run"
    ),
    "doctor.disk.below_minimum": (
        "Free disk: {free} free of {total} at {path}, under the {minimum} a server is sized for"
    ),
    "doctor.disk.ok": "Free disk: {free} free of {total} at {path} (minimum {minimum})",
    "doctor.disk.note": (
        "'noust server cleanup' removes what Noust can prove is unused; every release on disk "
        "keeps its code and dependencies."
    ),
    "doctor.port.free": "Port {port}: free (the web server is not listening on it yet)",
    "doctor.port.web_server": "Port {port}: {holders}",
    "doctor.port.container": "Port {port}: published by a container ({holders})",
    "doctor.port.container_note": (
        "Another platform's proxy or a Compose stack holds it, so nginx cannot listen on it "
        "until that stops."
    ),
    "doctor.port.held": "Port {port}: held by {holders}, not by the web server",
    "doctor.port.held_note": (
        "A certificate is asked for on port 80 and sites are served on 443: the web server "
        "cannot start while another program holds them."
    ),
    "doctor.program.version": "{command}: {version}",
    "doctor.program.installed": "{command}: installed",
    "doctor.program.missing": "{command}: not installed",
    "doctor.program.optional_missing": "{command}: not installed (optional)",
    "doctor.web.none": "Web server: not installed",
    "doctor.web.running": "  {unit} service: running",
    "doctor.web.stopped": "  {unit} service: not running",
    "doctor.node.npm_missing": "npm: not installed, although it ships with Node.js",
    "doctor.node.not_needed": "node: not installed (only Node.js applications need it)",
    "doctor.python.missing": "python3: not installed, needed for Python apps",
    "doctor.config.apps_dir_ok": "Apps directory: {path}",
    "doctor.config.apps_dir_missing": "Apps directory: {path} not found",
    "doctor.config.log_dir_ok": "Log directory: {path}",
    "doctor.config.log_dir_missing": "Log directory: {path} not found",
    "doctor.config.file_ok": "Config file: {path}",
    "doctor.config.file_missing": "Config file: {path} not found",
    "doctor.ssh.no_key": "SSH key: not found",
    "doctor.ssh.key_ok": "SSH key: {path}",
    "doctor.ssh.github_connected": "GitHub SSH: connected",
    "doctor.ssh.github_refused": "GitHub SSH: not configured",
    "doctor.ssh.github_note": "Add your key to GitHub: https://github.com/settings/keys",
    "doctor.ssh.github_untested": "GitHub SSH: not tested here",
    "doctor.relays.left": "{domain}: {what}",
    "doctor.relays.unread": "Not checked: {reason}",
    "doctor.names.explain": (
        "Nothing here stops working: Noust accepts the old names while they are met. "
        "'noust setup rename' finishes what can be finished without restarting anything."
    ),
    # -- The terminal: what keeps it from opening, and its limits ----------------------------
    # A command is a value ({command}): the console shows it as it is, and a test runs it.
    "terminal.server_off": "The terminal on the server itself is off (terminal.server.enabled)",
    "terminal.server_off.hint": (
        "It is a root shell, so it stays off until an administrator turns it on: {command}"
    ),
    "terminal.server_control": (
        "The control {control} is set to refused on this server, so the terminal on the server "
        "itself stays off"
    ),
    "terminal.server_control.hint": (
        "No framework asks for it: it was set in Settings > Controls or with the command line. "
        "To allow it: {command}"
    ),
    "terminal.server_framework": (
        "The compliance framework in force ({frameworks}) keeps the terminal on the server "
        "itself off (the control {control} is refused)"
    ),
    "terminal.server_framework.hint": (
        "A root shell in the browser is the console at its most root-equivalent. To allow it "
        "anyway, which is put on record: {command}. "
        "Or turn the framework off: {turn_off_command}"
    ),
    "terminal.disabled": "Terminals are turned off on this server",
    "terminal.disabled.hint": "An administrator turns them on with: {command}",
    "terminal.limit_total": "{open} terminals are open, the most this server allows",
    "terminal.limit_total.hint": "Close one, or raise terminal.max_sessions.",
    "terminal.limit_user": "You already have {open} terminals open, the most one person may",
    "terminal.limit_user.hint": "Close one, or raise terminal.max_sessions_per_user.",
    # -- The settings a central manages on every server (noust.fleet.settings) ---------------
    "fleet_settings.unknown_kind": "{kind} is not something the fleet manages",
    "fleet_settings.unknown_kind.hint": "The fleet manages: {kinds}.",
    "fleet_settings.kind_not_yet": "The central cannot manage {kind} for the fleet yet",
    "fleet_settings.kind_not_yet.hint": "For now, set it on each server, in its own settings.",
    "fleet_settings.value_not_object": "The value of a {kind} must be an object of fields",
    "fleet_settings.value_not_object.hint": (
        "Send the fields that a server's own settings take for a {kind}."
    ),
    "fleet_settings.unknown_field": "A {kind} has no field called {field}",
    "fleet_settings.unknown_field.hint": "A {kind} takes: {fields}.",
    "fleet_settings.missing_field": "A {kind} needs {field}",
    "fleet_settings.missing_field.hint": "A server cannot make one without it.",
    "fleet_settings.value_too_large": "This {kind} is larger than the {limit} bytes a setting may be",
    "fleet_settings.secret_missing": "{field} is hidden and nothing is stored for it yet",
    "fleet_settings.secret_missing.hint": (
        "Type the secret again: it is only shown as {redacted} when one is stored."
    ),
    "fleet_settings.bad_scope": "{scope} is not a place a setting of the fleet can be set",
    "fleet_settings.bad_scope.hint": "Use 'fleet' for every server, or 'node:<server>' for one.",
    "fleet_settings.permission_missing": "Setting a {kind} for the fleet needs the permission {permission}",
    "fleet_settings.permission_missing.hint": (
        "It is the permission that setting one on a single server asks: ask an administrator "
        "for a role that has it."
    ),
    "fleet_settings.unknown_node": "There is no server called {node} in this fleet",
    "fleet_settings.unknown_node.hint": "List the servers with: {command}",
    "fleet_settings.managed_missing": "This server holds no {kind} called {name}",
    "fleet_settings.managed_missing.hint": (
        "A setting is recorded as the fleet's after the central has written it: write it first."
    ),
    "fleet_settings.not_managed": "No central manages the {kind} {name} on this server",
    "fleet_settings.not_managed.hint": "Only a setting a central pushed can be asked back.",
    "fleet_settings.sync_ceiling": (
        "This server lets its central do at most '{level}', and applying the fleet's settings "
        "needs 'admin'"
    ),
    "fleet_settings.sync_ceiling.hint": "Raise it on this server, as root:\n  {command}",
    "fleet_settings.sync_route": "A push of the fleet's settings cannot {method} {path}",
    "fleet_settings.sync_route.hint": (
        "It may only write the settings the fleet manages: channels, alert rules, backup "
        "destinations, registry logins, shared variables and the address of the central's console."
    ),
    "fleet_settings.single_name": "There is one {kind} for the fleet and it is called {name}",
    "fleet_settings.single_name.hint": "Set it as {name}.",
    "fleet_settings.field_not_text": "The {field} of a {kind} must be text",
    "fleet_settings.field_not_text.hint": "Send it as a string, such as https://central.example.com.",
    "fleet_settings.sync_required": (
        "Only the central that manages this server can record a setting as the fleet's"
    ),
    "fleet_settings.sync_required.hint": (
        "The central does it when it applies a setting; nobody records one by hand."
    ),
    "fleet_settings.backend_changed": (
        "The backup destination {name} is {have} on this server and the fleet wants it {want}"
    ),
    "fleet_settings.backend_changed.hint": (
        "No route changes the backend of a destination: remove it on that server, or name the "
        "fleet's one differently."
    ),
    "fleet_settings.item_conflict": (
        "The {kind} {name} already exists on this server and is not the fleet's"
    ),
    "fleet_settings.item_conflict.hint": (
        "Apply again letting the fleet take it over, or give the fleet's setting another name."
    ),
    "fleet_settings.item_unsupported": (
        "This server cannot take a {kind} from the fleet: it does not offer {operation}"
    ),
    "fleet_settings.item_unsupported.hint": "It runs an older Noust: update it first.",
    "fleet_settings.no_such_setting": "The fleet has no {kind} called {name}",
    "fleet_settings.no_such_setting.hint": "List what it has with: GET /api/fleet/settings",
    "fleet_settings.bad_mode": "{mode} is not a way a server can follow the fleet's settings",
    "fleet_settings.bad_mode.hint": "Use 'managed' to follow them, or 'independent' to be left out.",
    # -- Where a channel of the fleet can be heard (noust.fleet.settings.reach) --------------
    "fleet_settings.route_invalid": "The route of this channel is not valid: {problem}",
    "fleet_settings.route_invalid.hint": (
        "A fleet route takes apps (domains, with * and ?), tags, min_severity, server_events "
        "and servers (names and patterns of this central's servers)."
    ),
    "fleet_settings.route_local": "A channel of the fleet cannot be routed by {list}",
    "fleet_settings.route_local.hint": (
        "Projects and environments are each server's own. Route by domain, tag, severity, event "
        "or server, or make a team's channel on the server that has the project."
    ),
    "fleet_settings.out_of_reach.server": (
        "The route of the {kind} {name} does not name this server, so it is not sent here"
    ),
    "fleet_settings.out_of_reach.server.hint": (
        "Add this server to the servers of the route, or take the list away, to send it here."
    ),
    "fleet_settings.out_of_reach.domain": (
        "The route of the {kind} {name} names domains that no application of this server has, "
        "so it is not sent here"
    ),
    "fleet_settings.out_of_reach.domain.hint": (
        "It is sent here once an application of this server has a domain the route names: "
        "apply again after deploying it."
    ),
    "fleet_settings.out_of_reach.tag": (
        "The route of the {kind} {name} names tags that no application of this server carries, "
        "so it is not sent here"
    ),
    "fleet_settings.out_of_reach.tag.hint": (
        "It is sent here once an application of this server carries a tag the route names "
        "(together with a domain it names, when the route has both): apply again after tagging it."
    ),
    "fleet_settings.reach_unknown": (
        "The applications of this server could not be read, so it is not known whether the "
        "{kind} {name} can match anything on it"
    ),
    "fleet_settings.reach_unknown.hint": (
        "Nothing was sent. Apply again when the server answers its applications."
    ),
    # -- Database servers: versions without support (4.1, item 103) -----------------------
    # One sentence for a version whose project has stopped fixing security problems, one for a
    # version that is about to; both are confirmed with unsupported_ok.
    "database_servers.unsupported_ended": (
        "{name} {version} has had no security fixes since {end_of_life}"
    ),
    "database_servers.unsupported_ended.hint": (
        "Run it only for an application that needs it, and leave it off the internet. To go "
        "ahead, confirm that you accept running it: unsupported_ok in the API, "
        "--accept-unsupported on the command line."
    ),
    "database_servers.unsupported_ending": (
        "{name} {version} stops getting security fixes on {end_of_life}"
    ),
    "database_servers.unsupported_ending.hint": (
        "A server made today would run for a long time without them. To go ahead, confirm that "
        "you accept it: unsupported_ok in the API, --accept-unsupported on the command line."
    ),
    # -- Database servers: the list, and the default of an engine (4.1, item 103) --------------
    "database_servers.unknown": "There is no database server {key}",
    "database_servers.unknown.hint": "List them with: {command}",
    "database_servers.default_app": (
        "{key} is the database of an application's stack, so it cannot be the default"
    ),
    "database_servers.default_app.hint": (
        "A default is the system's engine or an instance Noust runs. Make one with: {command}"
    ),
    "database_servers.default_not_ready": "{key} is {state}, so it cannot be the default yet",
    "database_servers.default_not_ready.hint": "Wait for it to finish, then choose it again.",
    # -- Moving a site to the other web server: what the new site does not carry over (4.1, 105) --
    "webserver_move.unreadable": "{source} would not read this site: {problem} (line {line})",
    "webserver_move.unreadable.hint": (
        "Fix the site where it is, with the editor of its page, then plan the move again."
    ),
    "webserver_move.unsupported": "{directive} (line {line}) has no equivalent in {target}",
    "webserver_move.unsupported.hint": (
        "The new site will not do what that line did. Check the site after the move, or leave "
        "the application where it is."
    ),
    "webserver_move.protection": (
        "{directive} (line {line}) protects the site, and {target} has nothing equivalent"
    ),
    "webserver_move.protection.hint": (
        "The new site would not be protected that way. Put another protection in front of it "
        "first, or leave the application where it is."
    ),
    "webserver_move.included": "Line {line} includes {path}, which is not carried over",
    "webserver_move.included.hint": (
        "The translation does not read the files a site includes. Copy what that file holds "
        "into the new site by hand, or leave the application where it is."
    ),
    "webserver_move.variable": "{directive} (line {line}) reads {variable}, which {target} cannot give",
    "webserver_move.variable.hint": (
        "The new site would send something else in its place. Check it after the move, or leave "
        "the application where it is."
    ),
    "webserver_move.approximate": "{directive} (line {line}) is carried over as {result}",
    "webserver_move.tuning": "{directive} (line {line}) tunes {source} and is left out",
    # -- Moving a site to the other web server: what stops the move, and what it needs (4.1, 105) --
    "webserver_move.target_missing": "{target} is not installed on this server",
    "webserver_move.target_missing.hint": "Install it first, then plan the move again: {command}",
    "webserver_move.unknown": "{domain} is neither an application nor a site behind {source}",
    "webserver_move.unknown.hint": "See what is behind each web server with: {command}",
    "webserver_move.operator_site": "The site of {domain} is yours, not Noust's ({path})",
    "webserver_move.operator_site.hint": (
        "Noust never rewrites a site you wrote. Write its equivalent for {target} at "
        "{target_path} yourself, then plan the move again: both files are left as they are and "
        "only the application moves."
    ),
    "webserver_move.no_site": "{domain} has no site file on {source}",
    "webserver_move.no_site.hint": (
        "There is nothing to translate. Deploy the application again so that it gets a site, "
        "then plan the move again."
    ),
    "webserver_move.compose_only": "{domain} is a Docker Compose application, and those are served by nginx only",
    "webserver_move.compose_only.hint": "Leave it behind nginx.",
    "webserver_move.console_site": (
        "{domain} is the public name of the console, which Noust sets up behind nginx only"
    ),
    "webserver_move.console_site.hint": (
        "Take it off first with {command}, or leave the web server as it is."
    ),
    "webserver_move.status_site": (
        "{domain} is the public name of the status page, which Noust sets up behind nginx only"
    ),
    "webserver_move.status_site.hint": (
        "Take it off first with {command}, or leave the web server as it is."
    ),
    "webserver_move.zero_downtime": "{domain} runs in zero-downtime mode, which only nginx supports",
    "webserver_move.zero_downtime.hint": "Turn the mode off first, then plan the move again: {command}",
    "webserver_move.partial_handover": (
        "{target} is not serving: moving {count} of the {total} sites would leave the others "
        "without a web server"
    ),
    "webserver_move.partial_handover.hint": (
        "Ports 80 and 443 belong to one web server at a time. Move every site together: {command}"
    ),
    "webserver_move.left_behind": "{name} is a site of {source} that Noust cannot move",
    "webserver_move.left_behind.hint": (
        "{source} would stop serving it. Write its equivalent for {target} at {target_path} "
        "yourself, or leave the web server as it is."
    ),
    "webserver_move.losses_to_accept": (
        "The new sites will not do {count} thing(s) that the old ones did"
    ),
    "webserver_move.losses_to_accept.hint": (
        "Read them in the plan. To go ahead anyway, accept them: accept_losses in the API, "
        "--accept-losses on the command line."
    ),
    "webserver_move.confirm_handover": (
        "{target} will take over ports 80 and 443 from {source} for {count} site(s)"
    ),
    "webserver_move.confirm_handover.hint": (
        "Every site is unreachable for the seconds the swap takes, and everything is put back if "
        "one of them does not answer. To go ahead: confirm in the API, --yes on the command line."
    ),
    "webserver_move.confirm_live": "{count} site(s) will move from {source} to {target}, which is serving",
    "webserver_move.confirm_live.hint": (
        "Each site is tested by {target} before it is switched, and put back if it does not "
        "answer. To go ahead: confirm in the API, --yes on the command line."
    ),
    "webserver_move.rejected": "{target} did not accept the translated site of {domain}",
    "webserver_move.rejected.hint": (
        "Its own message is shown as it was. Nothing was changed: fix what it names in the site "
        "on {source}, or leave the application where it is."
    ),
    "webserver_move.not_serving": "{target} did not start",
    "webserver_move.not_serving.hint": (
        "Its own message is shown as it was. {source} was started again and the sites are as they "
        "were."
    ),
    "webserver_move.unanswered": "{domain} did not answer through {target}",
    "webserver_move.unanswered.hint": (
        "What was tried and what the application's unit said is shown as it was. Everything was "
        "put back: {source} serves the sites again."
    ),
    "webserver_move.not_restored": "Not everything could be put back",
    "webserver_move.not_restored.hint": (
        "What follows says what was left and where it is. The audit log says what was moved."
    ),
    # -- A database an engine holds and Noust does not manage yet (4.1) ---------------------
    "databases.already_added": "The database {name} on {engine} is already added to Noust",
    # -- Which control asks something of a person, and how to stop it asking -----------------
    # (noust.core.compliance.explain decides which one; the words are here.)
    "controls.asked_by_setting": (
        "The control {control} asks for it, and it was turned on in Settings > Controls (or "
        "with the command line). It applies to everybody, whatever their role."
    ),
    "controls.asked_by_setting.hint": "To turn it off there, or:\n  {command}",
    "controls.asked_by_framework": (
        "The control {control} asks for it, and the framework in force ({frameworks}) asks for "
        "that control, so it stays on while the framework is on and cannot be turned off by "
        "itself."
    ),
    "controls.asked_by_framework.hint": (
        "To turn the framework off (Settings > Controls):\n  {command}"
    ),
    # -- A call that needs a second person's approval, or a reason ---------------------------
    "approvals.asked_waiting": (
        "This needs a second person's approval, whoever you are. Request {request} is waiting "
        "for an account with one of these roles to decide it: {approvers}."
    ),
    "approvals.asked_waiting.hint": _SEND_AGAIN,
    "approvals.asked_approved": (
        "This needs a second person's approval, whoever you are. Request {request} is "
        "approved: send the call again with it now."
    ),
    "approvals.asked_approved.hint": _SEND_AGAIN,
    "approvals.reason_required": (
        "Say why: this call needs a second person's approval, and a reason."
    ),
    "approvals.reason_required.hint": "Send the reason in the {header} header (percent-encoded).",
    "approvals.central_missing": "This needs a second person's approval on the central.",
    "approvals.central_missing.hint": (
        "Ask for it on the central and retry through it once it is approved."
    ),
    # -- The GitHub access a central leases to a server, and what an ended one looks like ----
    "sources.lease_expired_quiet": (
        "The GitHub access that {central} leased to this server for {repository} ended at "
        "{ended_at}, and {central} has not called since {last_call}"
    ),
    "sources.lease_expired_quiet.hint": (
        "Anything that starts on this server and reads {repository} (a scheduled update, a "
        "preview) fails until {central} calls again and leases a new one: check that the central "
        "is running and reaches this server. A deploy key does not depend on the central; "
        "see: {command}"
    ),
    "sources.no_app": "This central has no GitHub App to lease access with",
    "sources.no_app.hint": (
        "Create it in the console (Integrations, GitHub) or with: noust github setup"
    ),
    "sources.delivery_not_retryable": (
        "That event is {state}, and only an event that was given up on can be handed over again"
    ),
    "sources.delivery_not_retryable.hint": (
        "A server that took the event is not sent it twice; one that is still waiting is tried "
        "again by itself."
    ),
    "sources.central_only": (
        "Only the central that manages this server can lease it access to GitHub"
    ),
    "sources.central_only.hint": (
        "The central makes and renews the lease itself; there is nothing to do on this server. "
        "To clone with a credential of this server's own, give the application a deploy key."
    ),
    "sources.node_unsupported": (
        "{node} runs a Noust that cannot hold the GitHub access this central leases"
    ),
    "sources.node_unsupported.hint": (
        "Update Noust on {node} to 4.1 or later; the access is leased within a minute of it."
    ),
    "sources.node_ceiling": (
        "{node} lets this central only read, so it cannot be leased access to GitHub"
    ),
    "sources.node_ceiling.hint": (
        "Leasing needs the deploy ceiling or higher. On {node}, as root: {command}"
    ),
    "sources.node_unreachable": "{node} could not be reached to lease it access to GitHub",
    "sources.node_unreachable.hint": (
        "It is tried again within a minute, and the access it holds ends on its own. Check it "
        "with: {command}"
    ),
    "sources.node_refused": "{node} would not take the access to GitHub: {reason}",
    "sources.node_refused.hint": (
        "The server's own words are beside this. It is tried again in a few minutes."
    ),
    "sources.webhook_unreachable": (
        "GitHub cannot reach this central, so a push to a repository its servers deploy deploys "
        "nothing"
    ),
    "sources.webhook_unreachable.hint": (
        "The servers it manages have no address GitHub could call: the App's webhook is this "
        "central's. On a server with a web server, give it a name of its own: {command}. Behind a "
        "proxy of your own, say where it answers: {config_command}"
    ),
    "sources.node_independent": (
        "{node} is independent, so this central hands it nothing from GitHub"
    ),
    "sources.node_independent.hint": (
        "Make it follow the fleet again and the access and the events of its repositories reach "
        "it. Until then it deploys from its own credentials."
    ),
    "sources.forward_gave_up": (
        "{node} did not take the {kind} of {repository} in {minutes} minutes, and the central "
        "gave up"
    ),
    "sources.forward_gave_up.hint": (
        "Nothing was deployed on {node} for it. Once it answers again, update the application "
        "there, or push again."
    ),
    "sources.forward_refused": "{node} would not take the {kind} of {repository}: {reason}",
    "sources.forward_refused.hint": (
        "The server's own words are beside this. Nothing was deployed on {node} for it."
    ),
    "sources.github_refused": "GitHub would not give a token for installation {installation}",
    "sources.github_refused.hint": (
        "GitHub's own words are beside this. If the App was removed or its permissions changed, "
        "accept the new permissions in the App's settings on GitHub, or install it again."
    ),
    "sources.no_installation": "No installation of the GitHub App reaches {repository}",
    "sources.no_installation.hint": (
        "Install the App on the account {owner} and let it read {repository}: {url}"
    ),
    "sources.not_reachable": "The GitHub App cannot read {repository}",
    "sources.not_reachable.hint": (
        "Add {repository} to the repositories the App's installation reaches: {url}"
    ),
    "sources.lease_expired": (
        "The GitHub access that {central} leased to this server for {repository} ended at "
        "{ended_at} and has not been renewed"
    ),
    "sources.lease_expired.hint": (
        "{central} last called at {last_call} and did not lease a new one: it may not reach "
        "GitHub, or its GitHub App no longer covers {repository}. Until it does, anything that "
        "starts on this server and reads {repository} fails. A deploy key does not depend on the "
        "central; see: {command}"
    ),
    # -- Noust DNS: what setting up a name server takes on, and how to delegate a domain to it ----
    "authdns.warning.availability": (
        "A name server is a service the whole internet depends on: if it is down, every name it "
        "serves is down"
    ),
    "authdns.warning.availability.hint": (
        "Run at least two, on servers in different networks, and keep their operating systems "
        "updated."
    ),
    "authdns.warning.amplification": (
        "A name server on the internet can be used to flood other people with traffic"
    ),
    "authdns.warning.amplification.hint": (
        "Noust DNS never recurses, limits how fast it answers one address and hands zone transfers "
        "only to the servers you name, which keeps it a poor tool for that. It still answers on "
        "port 53 for anyone, so keep it updated."
    ),
    "authdns.delegation.glue": (
        "At your registrar, register {hosts} as name servers with these addresses: {addresses}"
    ),
    "authdns.delegation.glue.hint": (
        "A name server inside the domain it serves can only be found through addresses the "
        "registrar itself holds (glue records). Your registrar may call them host records or "
        "child name servers."
    ),
    "authdns.delegation.nameservers": (
        "At your registrar, set the name servers of {zone} to {nameservers}"
    ),
    "authdns.delegation.nameservers.hint": (
        "Look for custom name servers or DNS delegation in the domain's settings. The change "
        "reaches resolvers after the registrar's TTL, often a few hours and at most two days."
    ),
    "authdns.delegation.ds": (
        "Once the name servers have changed, add this DS record to {zone} at your registrar: {ds}"
    ),
    "authdns.delegation.ds.hint": (
        "Enter the key tag, the algorithm, the digest type and the digest in the order your "
        "registrar's form asks for them. Adding the DS before the name servers changed makes "
        "resolvers that validate refuse the domain."
    ),
    "authdns.delegation.check": "Check that resolvers find your name servers",
    "authdns.delegation.check.hint": "Run: {command}",
    "authdns.delegation.one_server": "Only one name server answers for {zone}",
    "authdns.delegation.one_server.hint": (
        "If it is down, {zone} is down. Add a secondary on another server, in another network, "
        "and name it in the zone."
    ),
    "authdns.delegation.same_network": "{first} and {second} are in the same network",
    "authdns.delegation.same_network.hint": (
        "One outage there takes both. Put one of them in another network."
    ),
    "authdns.check.not_delegated": "Resolvers find {found} for {zone}, not your name servers",
    "authdns.check.not_delegated.hint": (
        "Change the name servers at your registrar and wait for its TTL to pass."
    ),
    "authdns.check.no_public_answer": (
        "No public resolver answered, so the delegation of {zone} could not be checked"
    ),
    "authdns.check.no_public_answer.hint": (
        "Check this server's outbound access to port 53 of the public resolvers."
    ),
    "authdns.check.silent": "{server} at {address} did not answer for {zone}",
    "authdns.check.silent.hint": (
        "Port 53 over UDP and TCP must be open to the internet on {address}, in the firewall and "
        "in your provider's panel."
    ),
    "authdns.check.not_authoritative": (
        "{server} at {address} answered, but not as the authority for {zone}"
    ),
    "authdns.check.not_authoritative.hint": (
        "The zone is not loaded there yet, or another program answers on port 53."
    ),
    "authdns.check.recurses": "{server} at {address} answers questions about other domains",
    "authdns.check.recurses.hint": (
        "A name server open to recursion can be used to attack others. Noust DNS never does "
        "this: another program is answering on that address."
    ),
    "authdns.check.out_of_sync": (
        "{server} serves serial {serial} of {zone} and {primary} serves {primary_serial}"
    ),
    "authdns.check.out_of_sync.hint": (
        "A secondary copies a zone a few seconds after the primary changes it. If this lasts, "
        "check that the primary can reach it on port 53."
    ),
    "authdns.check.ds_missing": "The parent zone has no DS record for {zone}, which is signed",
    "authdns.check.ds_missing.hint": (
        "Add it at your registrar, after the name servers have changed."
    ),
    "authdns.check.ds_mismatch": (
        "The DS record the parent zone holds for {zone} is not the one of this zone's key"
    ),
    "authdns.check.ds_mismatch.hint": ("Replace it at your registrar with the one in the steps."),
    "authdns.check.ds_dangling": (
        "The parent zone holds a DS record for {zone}, and the zone is not signed"
    ),
    "authdns.check.ds_dangling.hint": (
        "Resolvers that validate refuse the domain. Remove the DS at your registrar, or sign the "
        "zone."
    ),
    # -- A deployment that did not finish (noust.core.deploy_failures): the first line of the --
    # error a job and a deployment keep. What follows it (the probes, the journal, the package
    # manager's and the build's own output) is another program's words and is never a sentence.
    "deploy_failure.release_unhealthy": "Release {release} did not pass its health check",
    "deploy_failure.release_unhealthy_back": (
        "Release {release} did not pass its health check; release {previous} is active again"
    ),
    "deploy_failure.release_unhealthy_down": (
        "Release {release} did not pass its health check; release {previous} is active again "
        "but is not answering either"
    ),
    "deploy_failure.app_unhealthy": "{domain} did not pass its health check",
    "deploy_failure.rollback_unhealthy": (
        "{domain} did not pass its health check after the rollback"
    ),
    "deploy_failure.stack_unhealthy": "The update of {domain} did not pass its health check",
    "deploy_failure.stack_nothing_before": (
        "The update of {domain} did not pass its health check; nothing was running before it, "
        "so nothing was put back"
    ),
    "deploy_failure.stack_not_restored": (
        "The update of {domain} did not pass its health check; what was serving could not be "
        "put back entirely"
    ),
    "deploy_failure.stack_back": (
        "The update of {domain} did not pass its health check; the containers that were "
        "serving are running again"
    ),
    "deploy_failure.stack_back_down": (
        "The update of {domain} did not pass its health check; the previous containers are "
        "back but are not answering either"
    ),
    "deploy_failure.install_failed": "Dependency installation failed",
    "deploy_failure.install_timeout": (
        "Dependency installation did not finish within {seconds}s and was stopped"
    ),
    "deploy_failure.install_oom": "Dependency installation ran out of memory and was killed",
    "deploy_failure.build_failed": "Build failed",
    "deploy_failure.build_timeout": "Build did not finish within {seconds}s and was stopped",
    "deploy_failure.build_oom": "Build killed due to insufficient memory (exit code 137)",
    # -- What a background job is (noust.core.job_titles): one sentence for each type, and for the ----
    # types that are about an application, one for a job that does not know which. A client says
    # these in its language; the job's own English name is what a script has always read.
    "jobs.title.deploy": "Deploy {domain}",
    "jobs.title.deploy_any": "Deploy an application",
    "jobs.title.update": "Update {domain}",
    "jobs.title.update_any": "Update an application",
    "jobs.title.backup": "Back up {domain}",
    "jobs.title.backup_any": "Back up an application",
    "jobs.title.restore": "Restore a backup into {domain}",
    "jobs.title.restore_any": "Restore a backup",
    "jobs.title.rollback": "Roll back {domain}",
    "jobs.title.rollback_any": "Roll back an application",
    "jobs.title.push": "Send a backup to {destination}",
    "jobs.title.push_any": "Send a backup to its destination",
    "jobs.title.cert_create": "Get a certificate for {domain}",
    "jobs.title.cert_create_any": "Get a certificate",
    "jobs.title.cert_acme_account": "Register the ACME account",
    "jobs.title.cert_renew": "Renew the certificate of {domain}",
    "jobs.title.cert_renew_all": "Renew the certificates that are due",
    "jobs.title.service_action": "Restart services",
    "jobs.title.site_status_page_publish": "Publish the status page at {domain}",
    "jobs.title.site_status_page_unpublish": "Unpublish the status page",
    "jobs.title.site_console_expose": "Publish the console at {domain}",
    "jobs.title.site_console_unexpose": "Unpublish the console",
    "jobs.title.site_webserver_move": "Move the sites to {webserver}",
    "jobs.title.site_action": "Change a site",
    "jobs.title.delete": "Delete {domain}",
    "jobs.title.delete_any": "Delete an application",
    "jobs.title.migrate": "Move {domain} onto the release layout",
    "jobs.title.migrate_any": "Move an application onto the release layout",
    "jobs.title.zero_downtime": "Change zero-downtime activation for {domain}",
    "jobs.title.zero_downtime_any": "Change zero-downtime activation",
    "jobs.title.sandbox_test": "Try {domain} in the build sandbox",
    "jobs.title.sandbox_test_any": "Try an application in the build sandbox",
    "jobs.title.identity_migrate": "Give {domain} its own system account",
    "jobs.title.identity_migrate_any": "Give an application its own system account",
    "jobs.title.cloudflared_install": "Install cloudflared",
    "jobs.title.hardening": "Change the sandbox of {domain}",
    "jobs.title.hardening_any": "Change the sandbox of an application",
    "jobs.title.tuning": "Tune {domain}",
    "jobs.title.tuning_any": "Tune an application",
    "jobs.title.disk": "Measure the disk use of {domain}",
    "jobs.title.disk_any": "Measure the disk use of an application",
    "jobs.title.replicas": "Change the copies of {domain}",
    "jobs.title.replicas_any": "Change the copies of an application",
    "jobs.title.os_refresh": "Refresh the package lists",
    "jobs.title.os_update": "Update the system",
    "jobs.title.cleanup": "Clean up the server",
    "jobs.title.swap_create": "Create a swap file",
    "jobs.title.swap_remove": "Remove the swap file",
    "jobs.title.swap": "Change the swap file",
    "jobs.title.disk_scan": "Measure what takes disk space",
    "jobs.title.server_action_dns_install": "Install Knot DNS",
    "jobs.title.server_action_dns_setup": "Set up Noust DNS as a {role}",
    "jobs.title.server_action": "Run a server task",
    "jobs.title.server_security": "Run a security task",
    "jobs.title.database_of_app": "Work on a database of {domain}",
    "jobs.title.database": "Run a database task",
    "jobs.title.fleet": "Run an action across the fleet",
    "jobs.title.node_enroll": "Enroll {node}",
    "jobs.title.node_enroll_any": "Enroll a server",
    "jobs.title.node_provision": "Create the server {node}",
    "jobs.title.node_provision_any": "Create a server",
    "jobs.title.self_update": "Update Noust",
    "jobs.title.app_exec": "Run a command in {domain}",
    "jobs.title.app_exec_any": "Run a command in an application",
    "jobs.title.manifest": "Apply a manifest",
    "jobs.title.custom_of_app": "Run a task for {domain}",
    "jobs.title.custom": "Run a task",
    # -- What runs on a request and is not a job (noust.web.operations): the closed list the ----
    # server announces as the `operation` event, said with the subject it names as `target`.
    "operations.app_start": "Start {target}",
    "operations.app_stop": "Stop {target}",
    "operations.app_restart": "Restart {target}",
    "operations.service_start": "Start the service {target}",
    "operations.service_stop": "Stop the service {target}",
    "operations.service_restart": "Restart the service {target}",
    "operations.engine_start": "Start the database engine {target}",
    "operations.engine_stop": "Stop the database engine {target}",
    "operations.engine_restart": "Restart the database engine {target}",
    "operations.cron_run": "Run the scheduled task {target}",
    "operations.backup_verify": "Verify the backup {target}",
    "operations.monitor_install": "Install the monitor",
    "operations.monitor_scan": "Scan the server with the monitor",
    "operations.probe_check": "Check the probe {target}",
    "operations.node_test": "Test the connection to {target}",
    "operations.waf_install": "Install the web application firewall",
    "operations.compose_import": "Import a Compose project",
    "operations.env_sync": "Sync the environment of {target}",
    "operations.app_move": "Move {target} to another project",
    "operations.build_cache_clear": "Clear the build cache of {target}",
    "operations.build_apply": "Apply the build settings of {target}",
    "operations.power_reboot": "Restart the server",
    "operations.power_shutdown": "Shut the server down",
    "operations.sites_reload": "Reload the web server's sites",
    "operations.app_apply": "Apply a declaration to {target}",
    "operations.fleet_settings_apply": "Apply the fleet's settings",
    "operations.encryption_enable": "Turn on backup encryption",
    "operations.encryption_rotate": "Rotate the backup encryption key",
    # -- The server's software (noust.managers.server.components): what Noust uses each piece for, --
    # and why one is not removed (item 118). A removal is refused with one of the sentences of
    # ``software.refused``; a piece that is never removed says why in its own ``.never``.
    "software.noust.purpose": "Noust itself, with the Python libraries it is built on",
    "software.noust.never": (
        "Removing it would take Noust, and the console you are using, off this server"
    ),
    "software.systemd.purpose": (
        "The service manager every application, site and unit of Noust runs under"
    ),
    "software.systemd.never": "Everything on this server, Noust included, runs under it",
    "software.sshd.purpose": "The SSH server: how you, and a central, reach this machine",
    "software.sshd.never": "Removing it would lock you out of this machine",
    "software.firewall.purpose": (
        "The firewall Noust opens and closes ports through, for published ports and for the "
        "databases it exposes"
    ),
    "software.firewall.never": (
        "Noust opens and closes ports through it, and it is what keeps the server closed"
    ),
    "software.git.purpose": "Fetches the source of every application that deploys from a repository",
    "software.git.never": "Every deployment from a repository needs it",
    "software.curl.purpose": (
        "Downloads the signing keys and the installers Noust verifies before it trusts them"
    ),
    "software.curl.never": "Noust downloads the keys and the installers it verifies with it",
    "software.gnupg.purpose": "Checks the signing key of every repository Noust adds",
    "software.gnupg.never": "Noust checks the signing key of every repository it adds with it",
    "software.nginx.purpose": "The web server that serves applications and ends their HTTPS",
    "software.apache.purpose": "The Apache web server, for the sites that run behind it",
    "software.certbot.purpose": "Gets and renews HTTPS certificates from Let's Encrypt and other authorities",
    "software.nodejs.purpose": "Builds and runs Node.js applications",
    "software.php.purpose": "PHP-FPM, which runs PHP applications",
    "software.docker.purpose": (
        "Runs container and Compose applications, and the database engines Noust runs in containers"
    ),
    "software.postgresql.purpose": "PostgreSQL, a database engine for applications",
    "software.mysql.purpose": "MySQL or MariaDB, a database engine for applications",
    "software.redis.purpose": "Redis or Valkey, a key-value store and cache for applications",
    "software.mongodb.purpose": "MongoDB, a document database for applications",
    "software.cloudflared.purpose": (
        "The connector a Cloudflare Tunnel runs through, to reach this server without opening a port"
    ),
    "software.nginx_stream_module.purpose": (
        "Lets nginx publish a TCP or UDP port, such as a database or a game server"
    ),
    "software.waf.purpose": "ModSecurity and the OWASP Core Rule Set: the web application firewall of a site",
    "software.knot.purpose": "Knot DNS, the name server of Noust's own DNS zones",
    "software.fail2ban.purpose": "Bans the addresses that guess passwords or probe the web server",
    "software.chrony.purpose": "Keeps the clock right by network time, which certificates and logs rely on",
    "software.auto_updates.purpose": "Installs the system's security updates by itself",
    "software.rclone.purpose": "Uploads backups to remote destinations",
    # -- Why a piece of software is not removed or installed here (noust.managers.server.software) --
    "software.refused.never": "{component} is never removed",
    "software.refused.not_installed": "{component} is not installed",
    "software.refused.dependents": (
        "{component} cannot be removed while something depends on it: {dependents}"
    ),
    "software.refused.dependents.hint": (
        "Stop using it first (remove or move what is listed), then remove it."
    ),
    "software.refused.takes_never": (
        "Removing {component} would also remove {other}, which is never removed"
    ),
    "software.refused.takes_never.hint": "Nothing was removed.",
    "software.refused.takes_in_use": (
        "Removing {component} would also remove {other}, which is in use: {dependents}"
    ),
    "software.refused.takes_in_use.hint": "Nothing was removed. Stop using {other} first.",
    "software.refused.cannot_tell": "Noust cannot tell what depends on {component}",
    "software.refused.cannot_tell.hint": (
        "{reason} Removing it without knowing could break what depends on it."
    ),
    "software.refused.unknown_origin": "Noust did not install {component}",
    "software.refused.unknown_origin.hint": (
        "Other things on the system may use it. Repeat the request confirming it to remove it "
        "anyway."
    ),
}


def _names_in(template: str | None) -> tuple[str, ...] | None:
    """
    Args:
        template: A catalog entry, or None.

    Returns:
        The values the entry is said with, sorted; None when there is no entry.
    """
    if template is None:
        return None
    return tuple(sorted({name for _, name, _, _ in string.Formatter().parse(template) if name}))


def values_of(code: str) -> set[str]:
    """
    Args:
        code: A sentence's code.

    Returns:
        The values it is said with: those of its text and of its fix.
    """
    names: set[str] = set()
    for key in (code, code + HINT_SUFFIX):
        names |= set(_names_in(CATALOG.get(key)) or ())
    return names


def is_command(name: str) -> bool:
    """
    Args:
        name: The name of a value a sentence takes.

    Returns:
        Whether the value is a command to run: ``command``, or a name that ends in ``_command``.
        The console shows such a value as it is, in mono, and never translates or breaks it.
    """
    return name == "command" or name.endswith("_command")


def commands_of(code: str) -> tuple[str, ...]:
    """
    Args:
        code: A sentence's code.

    Returns:
        The values of the sentence and of its fix that are commands, sorted.
    """
    return tuple(sorted(name for name in values_of(code) if is_command(name)))


def typescript_codes() -> str:
    """
    Returns:
        The table of sentences as the TypeScript module the console is held to
        (``panel/src/api/serverTextCodes.gen.ts``), byte for byte what the generator writes: for
        each sentence the values its text takes, the values its fix takes (null when it has
        none) and which of them are commands. The console's English and Spanish catalogs must
        have an entry for each, with the same values.
    """
    import json

    lines = []
    for code in sorted(CATALOG):
        if code.endswith(HINT_SUFFIX):
            continue
        text = _names_in(CATALOG[code])
        hint = _names_in(CATALOG.get(code + HINT_SUFFIX))
        lines.append(
            f"  {json.dumps(code)}: {{ "
            f"text: {json.dumps(list(text or ()))}, "
            f"hint: {json.dumps(None if hint is None else list(hint))}, "
            f"commands: {json.dumps(list(commands_of(code)))} }},"
        )
    body = "\n".join(lines)
    return (
        "// Generated by scripts/generate_server_text.py from src/noust/core/server_text.py.\n"
        "// Do not edit: change the sentence there and run the script.\n"
        "\n"
        "export interface ServerTextEntry {\n"
        "  /** The values the sentence is said with. */\n"
        "  readonly text: readonly string[];\n"
        "  /** The values its fix is said with; null when the sentence has no fix. */\n"
        "  readonly hint: readonly string[] | null;\n"
        "  /** The values that are commands: shown as they are, in mono. */\n"
        "  readonly commands: readonly string[];\n"
        "}\n"
        "\n"
        "/** Every sentence the server says with a code, with the values each takes. */\n"
        f"export const SERVER_TEXT = {{\n{body}\n}} as const satisfies Record<string, ServerTextEntry>;\n"
        "\n"
        "export type ServerTextCode = keyof typeof SERVER_TEXT;\n"
    )


@dataclass(frozen=True)
class ServerText:
    """
    One sentence of the server's, as a code and the values to put in it.

    It cannot be made for a code the catalog does not have, nor with values other than the ones
    the sentence takes: a wrong sentence is found by the test that builds it, not by an
    operator reading a blank.

    Attributes:
        code: The catalog key (``tunnels.store_sealed``), which the console translates.
        params: Values for the placeholders; text, numbers and flags only.
    """

    code: str
    params: Mapping[str, Param] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.code not in CATALOG:
            raise KeyError(f"{self.code}: there is no such sentence in noust.core.server_text")
        wanted, given = values_of(self.code), set(self.params)
        if wanted - given:
            raise ValueError(f"{self.code} needs {', '.join(sorted(wanted - given))}")
        if given - wanted:
            raise ValueError(f"{self.code} takes no {', '.join(sorted(given - wanted))}")
        for name, value in self.params.items():
            if not isinstance(value, str | int | float | bool):
                raise TypeError(f"{self.code}: {name} is {type(value).__name__}, not a plain value")

    @property
    def text(self) -> str:
        """The sentence in English."""
        return CATALOG[self.code].format(**self.params)

    @property
    def hint(self) -> str | None:
        """How to fix it in English, when the sentence has a fix."""
        template = CATALOG.get(self.code + HINT_SUFFIX)
        return template.format(**self.params) if template is not None else None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The code and the values, which is all the console needs next to the text.
        """
        return {"code": self.code, "params": dict(self.params)}


def read_back(
    text: str | None, codes: Iterable[str], *, integers: Container[str] = ()
) -> ServerText | None:
    """
    Read a stored sentence back as the catalog sentence that made it.

    A job and a deployment keep an error as text, and the code it was said with is gone. The
    English text is the catalog entry with the values put in, so it reads back: the same
    sentence for an error recorded before the code existed as for one recorded now.

    Args:
        text: One line, as stored.
        codes: The sentences it may be, by code; the order is the order they are tried in.
        integers: The values that are numbers (``count``), which the console puts in as numbers.
            Every other value is one word, a name or an id with no space in it.

    Returns:
        The sentence the whole line is, or None when it is not one of them.
    """
    if not text:
        return None
    for code in codes:
        template = CATALOG[code]
        pattern = re.escape(template)
        for name in _names_in(template) or ():
            shape = r"\d+" if name in integers else r"\S+"
            pattern = pattern.replace(re.escape("{" + name + "}"), f"(?P<{name}>{shape})")
        found = re.fullmatch(pattern, text)
        if found is not None:
            return say(
                code,
                **{
                    name: int(value) if name in integers else value
                    for name, value in found.groupdict().items()
                },
            )
    return None


def say(code: str, **params: Param) -> ServerText:
    """
    Make a sentence.

    Args:
        code: Its catalog key.
        **params: The values its text and its fix are said with.

    Returns:
        The sentence.

    Raises:
        KeyError: When the catalog has no such code.
        ValueError: When a value is missing or one is not taken.
        TypeError: When a value is not text, a number or a flag.
    """
    return ServerText(code, params)
