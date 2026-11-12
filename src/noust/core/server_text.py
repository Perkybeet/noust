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

#: What a source nothing deploys is told to do, said by several sentences of the wizard below: as
#: a container, with a Dockerfile or with Railpack, which says whether this server has it.
_CONTAINER_ROUTE = (
    "Deploy it as a container: add a Dockerfile to the repository and deploy it with --type "
    "container. Without a Dockerfile, install Railpack ({setup_command}), which builds it into a "
    "container by itself: --type container --builder railpack."
)
_CONTAINER_ROUTE_RAILPACK = (
    "Deploy it as a container, which Railpack builds without a Dockerfile (it is installed "
    "here): {command}. Or add a Dockerfile and deploy it with --type container."
)

#: What a Turborepo that is not a monorepo Noust deploys is told, whatever it lacks.
_TURBO_FIX = (
    "The monorepo type needs turbo.json, a workspace config and at least two apps under apps/, "
    "each with its own package.json. With a single app, move it to the root of the repository; "
    "or add a compose.yaml at the root that builds the apps and deploy it as Docker Compose."
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
    "server.descriptors_exhausted": (
        "File descriptors exhausted: the console has no file left to open, so what needs one fails"
    ),
    "server.descriptors_exhausted.hint": (
        "Restart the console to free them: {command}. Then run 'noust setup doctor': it says "
        "whether its unit still has the limit from before 4.3 (1024 files) and how to write the "
        "new one."
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
    # -- The picture on the sign-in screen (noust.validators.sign_in_image): why a file is not one -
    "sign_in_image.too_large": "The image is larger than {limit}",
    "sign_in_image.too_large.hint": (
        "Export it again at a smaller size or with more compression, and send it again."
    ),
    "sign_in_image.not_an_image": "The file is not a PNG, JPEG or WebP image",
    "sign_in_image.not_an_image.hint": (
        "The type is read from the file itself, not from its name: export the picture as a PNG, "
        "JPEG or WebP."
    ),
    "sign_in_image.svg_refused": "An SVG is not accepted as the sign-in image",
    "sign_in_image.svg_refused.hint": (
        "An SVG is code, and this image is shown to anyone who opens the console before they "
        "sign in. Export it as a PNG, JPEG or WebP."
    ),
    "sign_in_image.damaged": "The {kind} file is damaged or incomplete",
    "sign_in_image.damaged.hint": "Export the picture again and send the new file.",
    "sign_in_image.unsupported": "This {kind} file uses an encoding that browsers do not all draw",
    "sign_in_image.unsupported.hint": (
        "Save it again as a standard, 8-bit, non-hierarchical image and send it again."
    ),
    "sign_in_image.too_many_pixels": (
        "The image is {width} by {height} pixels, and the most is {limit} on each side"
    ),
    "sign_in_image.too_many_pixels.hint": "Scale it down and send it again.",
    "sign_in_image.animated": "An animated image is not accepted as the sign-in image",
    "sign_in_image.animated.hint": (
        "Use a still picture: an animation cannot be paused, and people read this screen."
    ),
    "sign_in_image.not_base64": "The image must be sent as base64 text",
    "sign_in_image.not_base64.hint": (
        "Send the file's bytes encoded as base64, without a data: prefix and without line breaks."
    ),
    "sign_in_image.bad_fit": "The fit must be cover or contain",
    "sign_in_image.bad_fit.hint": (
        "Use cover to fill the space (cropping what does not fit) or contain to show it whole."
    ),
    "sign_in_image.alt_too_long": "The description is longer than {limit} characters",
    "sign_in_image.alt_too_long.hint": "Say it shorter: a screen reader reads all of it.",
    "sign_in_image.alt_control": "The description must be one line of plain text",
    "sign_in_image.alt_control.hint": "Remove the line breaks and control characters.",
    "sign_in_image.no_image_yet": "There is no sign-in image to change the fit or the description of",
    "sign_in_image.no_image_yet.hint": "Send the image itself too.",
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
    # A server whose console has no file left to open answers every request with the same error.
    "fleet.node_descriptors_exhausted": "{node} ran out of file descriptors",
    "fleet.node_descriptors_exhausted.hint": (
        "Its console cannot open a file or a socket until some are freed. On {node}, run: "
        "{command}. Then run 'noust setup doctor' there: it says whether the console's unit still "
        "has the limit from before 4.3 (1024 files)."
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
        "Set monitor.smtp.* or monitor.email_api.* (Settings > Notification channels), then ask again."
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
    "doctor.limits.default": (
        "{unit} sets no limit of open files, so it has the system's default; a unit written now "
        "gets {new}"
    ),
    "doctor.limits.old": (
        "{unit} is limited to {limit} open files, and a unit written now gets {new}"
    ),
    "doctor.store.outside": (
        "Noust's store is in {store}, outside {state}, the state directory of this server: what "
        "looks only there (a copy of that directory, a package upgrade) does not find it"
    ),
    "doctor.store.orphan": (
        "{path} is an empty file left where the store would be: it is not the store, and the "
        "move replaces it"
    ),
    "doctor.stapling.site": (
        "{site} still asks nginx to staple OCSP responses, which Let's Encrypt stopped giving in "
        "August 2025: nginx logs a warning about it at every reload. Noust writes the site again "
        "without it the next time it writes the site (a deployment, a change of its domains)"
    ),
    "doctor.cron.without_environment": (
        "The task {name} of {domain} runs without the application's .env and tools: it was "
        "written before a task loaded them"
    ),
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
        "No framework asks for it: it was set in Settings > Compliance or with the command line. "
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
        "The control {control} asks for it, and it was turned on in Settings > Compliance (or "
        "with the command line). It applies to everybody, whatever their role."
    ),
    "controls.asked_by_setting.hint": "To turn it off there, or:\n  {command}",
    "controls.asked_by_framework": (
        "The control {control} asks for it, and the framework in force ({frameworks}) asks for "
        "that control, so it stays on while the framework is on and cannot be turned off by "
        "itself."
    ),
    "controls.asked_by_framework.hint": (
        "To turn the framework off (Settings > Compliance):\n  {command}"
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
    "jobs.title.backup_database": "Back up the database {database}",
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
    # An install or a removal from the Software page: the component is the registry's id, which the console says by name.
    "jobs.title.server_action_software_install": "Install {component}",
    "jobs.title.server_action_software_remove": "Remove {component}",
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
    # -- What the process monitor noticed about a process (noust.monitor.signals) ---------------
    # The numbers are values, written as the monitor rounds them (one decimal).
    "monitor.observation.name_pattern": (
        "Executable name starts with '{prefix}', a name used by known cryptomining and botnet "
        "malware. This is a note, not a verdict: identify the binary before acting on it."
    ),
    "monitor.observation.resource_usage": (
        "Sustained resource usage: CPU {cpu}% (threshold {cpu_threshold}%), memory {memory}% "
        "(threshold {memory_threshold}%)."
    ),
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
    # -- Why Noust does not change a system's packages (noust.managers.server.host, software) --
    "host.updates_unsupported.transactional": (
        "This system installs packages through transactions and a reboot (transactional-update or an "
        "image-based system), so Noust only reports its state. Update it with its own tool."
    ),
    "host.updates_unsupported.no_manager": (
        "Noust drives apt, dnf and zypper, and none of them is installed here. Update this system "
        "with its own package manager."
    ),
    "software.hub": "This central is a hub: it has no software of its own",
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
    # -- Publishing a TCP or UDP port: what nginx here lacks (noust.managers.stream_exposure) ----
    # Where a distribution builds the stream module into nginx there is no package to install, so
    # the fix is its own sentence and not an install command: a second code for the same problem.
    "streams.support.no_nginx": "nginx is not installed",
    "streams.support.no_nginx.hint": "Install it with: {command}",
    "streams.support.no_stream_module": "This nginx was built without the stream module",
    "streams.support.no_stream_module.hint": "Install the module with: {command}",
    "streams.support.no_stream_module_suse": "This nginx was built without the stream module",
    "streams.support.no_stream_module_suse.hint": (
        "openSUSE ships nginx with stream; install the nginx package from the distribution or "
        "from nginx.org."
    ),
    "streams.support.module_not_loaded": (
        "The stream module is built as a module and nothing loads it"
    ),
    "streams.support.module_not_loaded.hint": "Install the module with: {command}",
    "streams.support.module_not_loaded_suse": (
        "The stream module is built as a module and nothing loads it"
    ),
    "streams.support.module_not_loaded_suse.hint": "Add {line_command} at the top of nginx.conf.",
    # nginx's own words are the fix of this one (what `nginx -T` printed), so it has none here.
    "streams.support.config_unreadable": "nginx cannot read its own configuration",
    "streams.support.config_unparsable": "nginx's configuration is not readable: {reason}",
    "streams.support.foreign_block": (
        "nginx.conf already has a stream block that does not include Noust's ports"
    ),
    "streams.support.foreign_block.hint": (
        "Add this line inside it, then run the command again: {line_command}"
    ),
    "streams.support.no_stream_block": "nginx.conf has no stream block for Noust's ports",
    "streams.support.no_stream_block.hint": (
        "Add this at the top level of nginx.conf (beside http), then run the command again: "
        "{block_command}"
    ),
    # -- Publishing a TCP or UDP port: what a plan warns of, and what the firewall came to ------
    "streams.plan.nothing_listens": (
        "Nothing listens on {target} yet: visitors will be refused until the service is running"
    ),
    "streams.plan.open_to_everyone": "{protocol} {port} will be open to everyone",
    "streams.plan.open_to_everyone_without_login": (
        "{protocol} {port} will be open to everyone; a service without its own authentication "
        "should be restricted to known addresses"
    ),
    # One rule and several, because the plural of "rule" is not a suffix in every language.
    "streams.firewall.allowed_one": "{backend} now allows 1 rule for it",
    "streams.firewall.allowed_several": "{backend} now allows {count} rules for it",
    "streams.firewall.unchanged": "The firewall was not changed",
    "streams.firewall.unchanged_because": "The firewall was not changed: {note}",
    # -- Diagnose (noust.managers.diagnose): what each probe found, one sentence under its name ---
    # The evidence under a sentence is what the system printed and is never one of these. A probe
    # that is unmanaged says so in its own sentence (``..._unmanaged``) and a count has one form for
    # one and one for several, because neither is a suffix in every language.
    "diagnose.check.incomplete": "Could not complete this check",
    "diagnose.php_fpm.no_record": "No application record",
    "diagnose.php_fpm.ok": "The PHP-FPM pool answers ({service} {state})",
    "diagnose.php_fpm.pool_disabled": (
        "The PHP-FPM pool is disabled (stopped); start it with {command}."
    ),
    "diagnose.php_fpm.pool_missing": (
        "The PHP-FPM pool file {pool_file} is missing; redeploy with {command}."
    ),
    "diagnose.php_fpm.service_down": "{service}, which runs every PHP pool, is {state}.",
    "diagnose.php_fpm.config_refused": "PHP-FPM refuses its configuration; see {command} below.",
    "diagnose.php_fpm.no_answer": (
        "The PHP-FPM pool does not answer the health check; see PHP's errors below."
    ),
    "diagnose.containers.no_app": "No application recorded",
    "diagnose.containers.publishes_port": "The stack publishes a port: judged as a web below",
    "diagnose.containers.worker": "A worker: {summary}",
    "diagnose.containers.worker_with_port": (
        "A worker: {summary}; port {port} is recorded for it, and it publishes none. "
        "Clear it with: {command}"
    ),
    "diagnose.unit.skipped_php": (
        "PHP application: it runs in its PHP-FPM pool, checked under php_fpm, no unit"
    ),
    "diagnose.unit.skipped_static": "Static site: served directly by the web server, no unit",
    "diagnose.unit.missing": "No systemd unit found for {unit}",
    "diagnose.unit.active": "{unit_file} is active (running)",
    "diagnose.unit.active_unmanaged": "{unit_file} is active (running) (not managed by Noust)",
    "diagnose.unit.restarting": "{unit_file} is restarting ({restarts} restarts so far)",
    "diagnose.unit.restarting_unmanaged": (
        "{unit_file} is restarting (not managed by Noust) ({restarts} restarts so far)"
    ),
    "diagnose.unit.failed": "{unit_file} failed (Result={result})",
    "diagnose.unit.failed_unmanaged": "{unit_file} failed (not managed by Noust) (Result={result})",
    "diagnose.unit.state": "{unit_file} is {active_state}/{sub_state}",
    "diagnose.unit.state_unmanaged": (
        "{unit_file} is {active_state}/{sub_state} (not managed by Noust)"
    ),
    "diagnose.port.skipped_php": (
        "PHP application: it runs in its PHP-FPM pool, checked under php_fpm, "
        "no backend port to check"
    ),
    "diagnose.port.skipped_static": (
        "Static site: served directly by the web server, no backend port to check"
    ),
    "diagnose.port.not_recorded": "No port recorded for this app",
    "diagnose.port.listening": "Port {port} is listening",
    "diagnose.port.listening_process": "Port {port} is listening ({processes})",
    "diagnose.port.elsewhere": (
        "The app listens on {actual_port}, not the recorded port {recorded_port}"
    ),
    "diagnose.port.silent": "Nothing is listening on port {port}",
    "diagnose.http_direct.no_port": "No backend port to probe directly",
    "diagnose.http_direct.unreachable": "Could not reach the app directly on port {port}",
    "diagnose.http_direct.status": "HTTP {status} from 127.0.0.1:{port}",
    "diagnose.http_nginx.unreachable": "Could not reach nginx for {domain}",
    "diagnose.http_nginx.status": "HTTP {status} from nginx for Host: {domain}",
    "diagnose.journal.skipped_php": (
        "PHP application: it runs in its PHP-FPM pool, checked under php_fpm, "
        "no unit to read logs from"
    ),
    "diagnose.journal.skipped_static": (
        "Static site: served directly by the web server, no unit to read logs from"
    ),
    "diagnose.journal.no_unit": "No unit named {unit} to read logs from",
    "diagnose.journal.unreadable": "Could not read the journal for {unit_file}",
    "diagnose.journal.empty": "No journal entries for {unit_file}",
    "diagnose.journal.lines_one": "Last journal line for {unit_file}",
    "diagnose.journal.lines_several": "Last {count} journal lines for {unit_file}",
    "diagnose.blue_green.off": "Not in zero-downtime mode",
    "diagnose.blue_green.no_instance": (
        "Zero-downtime mode is on but no instance is recorded as serving"
    ),
    "diagnose.blue_green.upstream_mismatch": (
        "nginx proxies to {upstream_port}, but {serving} serves on {port}"
    ),
    "diagnose.blue_green.upstream_none": "nginx proxies to no upstream, but {serving} serves on {port}",
    "diagnose.blue_green.leftover": (
        "{unit}.service still runs beside the instances; "
        "the next activation will refuse to start blue on port {port}"
    ),
    "diagnose.blue_green.serving": "{serving} serves on port {port}; {idle} is {idle_state}",
    "diagnose.blue_green.serving_release": (
        "{serving} serves release {release} on port {port}; {idle} is {idle_state}"
    ),
    "diagnose.blue_green.idle_failed": (
        "{serving} serves on port {port}; {idle} is {idle_state}; "
        "its last start failed, so {serving} kept serving"
    ),
    "diagnose.blue_green.idle_failed_release": (
        "{serving} serves release {release} on port {port}; {idle} is {idle_state}; "
        "its last start failed, so {serving} kept serving"
    ),
    "diagnose.nginx_log.unreadable": "Could not read {path}",
    "diagnose.nginx_log.clean": "No nginx error log lines mention this domain",
    "diagnose.nginx_log.lines_one": "1 nginx error log line mentions this domain or an upstream",
    "diagnose.nginx_log.lines_several": (
        "{count} nginx error log lines mention this domain or an upstream"
    ),
    "diagnose.certificate.missing": "TLS is enabled for {domain} but no certificate was found",
    "diagnose.certificate.none": "No certificate found for {domain}",
    "diagnose.certificate.no_expiry": "Could not read the expiry date for {label}",
    "diagnose.certificate.bad_expiry": "Unreadable expiry date for {label}",
    "diagnose.certificate.expired": "The certificate for {label} expired on {expiry}",
    "diagnose.certificate.expires_one": "The certificate for {label} expires in 1 day",
    "diagnose.certificate.expires_several": "The certificate for {label} expires in {days} days",
    "diagnose.certificate.valid_one": "The certificate for {label} is valid for 1 more day",
    "diagnose.certificate.valid_several": (
        "The certificate for {label} is valid for {days} more days"
    ),
    "diagnose.last_deployment.none": "No deployment recorded for this domain",
    "diagnose.last_deployment.cancelled": (
        "The last deployment was cancelled; what served before still serves"
    ),
    "diagnose.last_deployment.cancelled_by": (
        "The last deployment was cancelled by {actor}; what served before still serves"
    ),
    "diagnose.last_deployment.failed": "The last deployment failed",
    "diagnose.last_deployment.failed_with": "The last deployment failed: {error}",
    "diagnose.last_deployment.succeeded": "The last deployment succeeded",
    "diagnose.last_deployment.state": "The last deployment is {status}",
    "diagnose.oom.unreadable": "Could not read the kernel log",
    "diagnose.oom.none": "No OOM kills in the kernel log in the last 7 days",
    "diagnose.oom.found": "The kernel log shows OOM activity in the last 7 days",
    "diagnose.oom.found_no_cgroup_v2": (
        "The kernel log shows OOM activity in the last 7 days, and this server cannot say which "
        "application it was: it does not use cgroup v2, where the kernel counts kills by unit"
    ),
    "diagnose.oom.found_no_file": (
        "The kernel log shows OOM activity in the last 7 days, and this server cannot say which "
        "application it was: the unit's memory.events cannot be read"
    ),
    "diagnose.oom.killed_one": (
        "The kernel killed this application for running out of memory once since its unit started "
        "({since})"
    ),
    "diagnose.oom.killed_several": (
        "The kernel killed this application for running out of memory {count} times since its unit "
        "started ({since})"
    ),
    "diagnose.oom.unit_result": (
        "systemd records that the last run of this application's unit ended because the kernel "
        "killed it for running out of memory"
    ),
    "diagnose.disk.usage": "{free} free ({percent}% used) on the filesystem holding {path}",
    "diagnose.zero_downtime.could": "Its updates restart it in place; it could update without a cut",
    # What the checks add up to, the most likely cause (``probable_cause``). A pool's cause is the
    # sentence of its own check, so it has no entry here.
    "diagnose.cause.worker": "The worker's {summary}; see their output below.",
    "diagnose.cause.no_unit": (
        "No systemd unit found for {unit}; it has never been started or its unit was removed."
    ),
    "diagnose.cause.descriptors": (
        "The console ran out of file descriptors (Too many open files) while it was checking "
        "{domain}, so the checks it could not run say nothing about the application. Restart "
        "the console to free them: {command}"
    ),
    "diagnose.descriptors.exhausted": "The console ran out of file descriptors while checking",
    "diagnose.cause.oom": "Killed by the kernel for running out of memory (limit or machine).",
    "diagnose.cause.oom_limit": (
        "The kernel killed {domain} for running out of memory: its limit is {limit} and it used up "
        "to {peak}, so it needs more than it is allowed. Raise the limit with: {command}"
    ),
    "diagnose.cause.oom_machine": (
        "The kernel killed {domain} for running out of memory and it has no memory limit, so the "
        "server ran out of memory (it used up to {peak}). Free memory on the server, or give the "
        "application a limit it can keep to: {command}"
    ),
    "diagnose.cause.oom_unit": (
        "systemd records that the last run of {domain} ended because the kernel killed it for "
        "running out of memory. See its memory limit with: {command}"
    ),
    "diagnose.cause.crash_loop": (
        "The process exits with status {status} at start; see the last journal lines below."
    ),
    "diagnose.cause.unit_failed": (
        "The unit failed to start (Result={result}, exit status {status}); "
        "see the last journal lines below."
    ),
    "diagnose.cause.upstream_mismatch": (
        "nginx proxies {domain} to {upstream_port}, but the {serving} instance listens on {port}."
    ),
    "diagnose.cause.upstream_none": (
        "nginx proxies {domain} to no upstream, but the {serving} instance listens on {port}."
    ),
    "diagnose.cause.port_elsewhere": "Listening on {actual_port}, Noust routes to {recorded_port}.",
    "diagnose.cause.port_silent": "The process is running but not listening on port {port}.",
    "diagnose.cause.nginx_status": (
        "The app answers directly, but nginx returns {status} for {domain}; "
        "nginx cannot reach the app."
    ),
    "diagnose.cause.nginx_unreachable": (
        "The app answers directly, but nginx cannot connect ({error}) for {domain}; "
        "nginx cannot reach the app."
    ),
    "diagnose.cause.certificate_expired": "The TLS certificate for {domain} expired on {expiry}.",
    "diagnose.cause.worker_port": (
        "{domain} publishes no port, yet port {port} is recorded for it: clear it with {command}."
    ),
    "diagnose.cause.disk_full": "The disk holding the apps directory is {percent}% full.",
    # -- The Docker networks of a Compose stack (noust.managers.stack_networks): what is worth ------
    # knowing about each, read from Docker and never changed. Who else is on a network is said for
    # one container, for several and for many (the first five by name), because the verb and the
    # count do not follow one rule in every language.
    "networks.not_applicable": (
        "This application does not run in Docker, so it has no Docker network."
    ),
    "networks.finding.host": (
        "It is the host's own network: the containers have no network of their own."
    ),
    "networks.finding.default_bridge": (
        "It is Docker's default bridge: every container started without a network is on it, "
        "and they reach each other by address."
    ),
    "networks.finding.shared_one": "It is shared: {members} is on it too.",
    "networks.finding.shared_several": "It is shared: {members} are on it too.",
    "networks.finding.shared_many": "It is shared: {members} and {more} more are on it too.",
    "networks.finding.owned_by_another": "It belongs to the stack {owner}, which made it.",
    "networks.finding.made_elsewhere": (
        "Compose did not make it: it was made by hand or by another tool."
    ),
    "networks.finding.internal": "It is internal: containers on it cannot reach the outside.",
    # -- Files > Storage (noust.managers.server.storage): what a cleanup would do, one sentence ----
    # for each action, said before anything is deleted.
    "storage.effect.journal": (
        "Rotates the journal and deletes archived files beyond the limit. The active file is "
        "not touched."
    ),
    "storage.effect.pkg_cache": "Deletes the downloaded package files.",
    "storage.effect.docker_build_cache": (
        "Deletes build cache older than a week. The next build is slower for it."
    ),
    "storage.effect.docker_dangling_images": (
        "Deletes images that have no name and no container. Named images are kept, and so is "
        "every noust_previous image."
    ),
    "storage.effect.docker_image": (
        "Deletes the image {image}, without --force: Docker refuses if a container uses it."
    ),
    "storage.effect.releases": (
        "Deletes the releases each application keeps beyond its own limit. The active release "
        "and the one a rollback goes to always stay."
    ),
    # -- Files > Storage: why a scheduled cleanup did nothing. The history keeps these as text, so ---
    # a row written before the codes read back as the sentence it is (``read_back``).
    "storage.run.policy_off": "the cleanup policy is switched off",
    "storage.run.below_threshold": (
        "{mount} is {percent}% full, below the {threshold}% the policy waits for"
    ),
    # -- Files > Swap (noust.managers.server.swap): why a swap file cannot be made, and what to know --
    "swap.unsupported_container": (
        "This is a container ({container}), where swap belongs to the host: swapon is refused "
        "inside it."
    ),
    "swap.cloud_init": (
        "cloud-init is configured with a swap: section, so it recreates the swap on every boot. "
        "Remove that section, or leave the swap to cloud-init."
    ),
    # -- Domains > Sites (noust.web.api.sites): what a site action did. The console tells a site ----
    # that came without HTTPS, or that its web server would not load, from one that came well by
    # these codes: ``sites.created.without_tls``, ``sites.created.without_tls_because`` and
    # ``sites.created.not_loaded`` are the three that are a warning and not a success.
    "sites.created.with_ssl": "Site created with SSL: {domain}.",
    "sites.created.reload": "Site created: {domain}. Reload {webserver} to serve it.",
    "sites.created.without_tls": "Site created: {domain}. TLS was requested but not enabled.",
    "sites.created.without_tls_because": (
        "Site created: {domain}. TLS was requested but not enabled: {error}."
    ),
    "sites.created.not_loaded": "Site created: {domain}, but {webserver} did not load it: {error}",
    "sites.config_updated": "Configuration updated for {domain}. Reload {webserver} to apply it.",
    "sites.enabled": "Site enabled: {domain}",
    "sites.disabled": "Site disabled: {domain}",
    "sites.deleted": "Site deleted: {domain}",
    "sites.reloaded": "{webserver} reloaded",
    # -- The new-application wizard (noust.deployers.inspect): what Noust found in a source -----
    # The verdict, and what to do about it. A source nothing recognised is refused with one of the
    # ``inspect.*`` sentences and its fix (the entry that ends in .hint); a source that was
    # recognised and that this server cannot build yet has its fix as a sentence of its own,
    # because the fix is the deployer's.
    "inspect.can_deploy": "Noust can deploy this as {type}.",
    "inspect.can_deploy_also": (
        "Noust can deploy this as {type}. It also looks like {others}; choose that type instead "
        "to deploy it that way."
    ),
    "inspect.container_dockerfile": (
        "Noust can deploy this as a container, built from its Dockerfile."
    ),
    "inspect.container_dockerfile_also": (
        "Noust can deploy this as a container, built from its Dockerfile. It also looks like "
        "{others}; choose that type instead to deploy it that way."
    ),
    "inspect.container_railpack": (
        "Noust can deploy this as a container, built by Railpack: it has no Dockerfile."
    ),
    "inspect.container_railpack_also": (
        "Noust can deploy this as a container, built by Railpack: it has no Dockerfile. It also "
        "looks like {others}; choose that type instead to deploy it that way."
    ),
    "inspect.missing_programs": "This is a {type} project, but this server does not have {programs}.",
    "inspect.install_toolchain": "Install the toolchain with: {command}. Then deploy it.",
    "inspect.install_deno": (
        "Install Deno 2 system-wide, where the build account can run it ({command}, or the "
        "installer of {url} with {env_command}). Then deploy it."
    ),
    "inspect.install_setup_init": "Install what it needs with {command}, then deploy it.",
    "inspect.dockerfile.unreadable": (
        "The repository has a {dockerfile}, but it could not be read as one."
    ),
    "inspect.dockerfile.unreadable.hint": (
        "Deploy it as a container: {command}. Check the {dockerfile} is a file, not a link, and "
        "is committed on this branch."
    ),
    "inspect.stack.no_program": (
        "This is a {stack} project ({marker}), but Noust found no program to run in it."
    ),
    "inspect.stack.no_program.hint": _CONTAINER_ROUTE,
    "inspect.stack.no_program_with_railpack": (
        "This is a {stack} project ({marker}), but Noust found no program to run in it."
    ),
    "inspect.stack.no_program_with_railpack.hint": _CONTAINER_ROUTE_RAILPACK,
    "inspect.stack.no_deployer": (
        "This is a {stack} project ({marker}); Noust has no {stack} deployer."
    ),
    "inspect.stack.no_deployer.hint": _CONTAINER_ROUTE,
    "inspect.stack.no_deployer_with_railpack": (
        "This is a {stack} project ({marker}); Noust has no {stack} deployer."
    ),
    "inspect.stack.no_deployer_with_railpack.hint": _CONTAINER_ROUTE_RAILPACK,
    "inspect.dotnet.no_web_project": (
        "This is a .NET project ({marker}), but Noust found no web project to run in it."
    ),
    "inspect.dotnet.no_web_project.hint": _CONTAINER_ROUTE,
    "inspect.dotnet.no_web_project_with_railpack": (
        "This is a .NET project ({marker}), but Noust found no web project to run in it."
    ),
    "inspect.dotnet.no_web_project_with_railpack.hint": _CONTAINER_ROUTE_RAILPACK,
    "inspect.package_json.unreadable": "package.json cannot be read as JSON.",
    "inspect.package_json.unreadable.hint": "Fix package.json and inspect again ({error}).",
    "inspect.package_json.no_start": (
        "package.json has no start script, no main entry and no framework Noust recognises "
        "(Next.js, Vite, Nuxt, SvelteKit, Astro, Remix, Angular)."
    ),
    "inspect.package_json.no_start.hint": (
        'Add a start script to package.json ({example_command}) or a "main" entry, so Noust '
        "knows how to run it; or choose the type explicitly."
    ),
    "inspect.subprojects": ("Nothing Noust deploys at the root of the repository; found {found}."),
    "inspect.subprojects.hint": (
        "Noust deploys a repository from its root, or one application per project of it: a "
        "JavaScript monorepo (npm, Yarn, pnpm, Lerna, Nx or Turborepo workspaces) is read with "
        "'{detect_command}' and each application created with '{create_command}', on releases and "
        "watching only its own files. Otherwise deploy each project from a repository of its "
        "own, or add a compose.yaml at the root that builds them and deploy it as Docker Compose."
    ),
    "inspect.no_match": "Nothing in the repository matches an application type Noust deploys.",
    "inspect.no_match.hint": (
        "Noust recognises a project by the files at the root of the repository ({examples}, or a "
        "package.json with a start script). Check the branch, or choose a type explicitly "
        "instead of relying on auto-detection."
    ),
    # A Turborepo that is close to a monorepo and is not one: what it lacks, as one sentence for
    # each way of lacking it.
    "inspect.turbo.no_workspace_no_apps": (
        "The repository uses Turborepo (turbo.json) but is not a monorepo Noust deploys: no "
        'workspace (pnpm-workspace.yaml, or "workspaces" in package.json); no app under apps/ '
        "has a package.json."
    ),
    "inspect.turbo.no_workspace_no_apps.hint": _TURBO_FIX,
    "inspect.turbo.no_workspace_one_app": (
        "The repository uses Turborepo (turbo.json) but is not a monorepo Noust deploys: no "
        'workspace (pnpm-workspace.yaml, or "workspaces" in package.json); only apps/{app}/ '
        "has a package.json under apps/."
    ),
    "inspect.turbo.no_workspace_one_app.hint": _TURBO_FIX,
    "inspect.turbo.no_workspace": (
        "The repository uses Turborepo (turbo.json) but is not a monorepo Noust deploys: no "
        'workspace (pnpm-workspace.yaml, or "workspaces" in package.json).'
    ),
    "inspect.turbo.no_workspace.hint": _TURBO_FIX,
    "inspect.turbo.no_apps": (
        "The repository uses Turborepo (turbo.json) but is not a monorepo Noust deploys: no app "
        "under apps/ has a package.json."
    ),
    "inspect.turbo.no_apps.hint": _TURBO_FIX,
    "inspect.turbo.one_app": (
        "The repository uses Turborepo (turbo.json) but is not a monorepo Noust deploys: only "
        "apps/{app}/ has a package.json under apps/."
    ),
    "inspect.turbo.one_app.hint": _TURBO_FIX,
    "inspect.turbo.other": "The repository uses Turborepo (turbo.json) but is not a monorepo Noust deploys.",
    "inspect.turbo.other.hint": _TURBO_FIX,
    # -- What a background job is doing (noust.web.jobs and the jobs of the application's pages): the ---
    # step it reports and the lines it leaves, one sentence each. A step that is a tool's own output,
    # or a phase a deployer names, is not one of these and travels as it came.
    "jobs.step.done": "Done",
    "jobs.step.complete": "Complete",
    "jobs.step.deploy.preparing": "Preparing deployment",
    "jobs.step.deploy.recipe": "Preparing the {recipe} recipe",
    "jobs.step.deploy.database": "Creating the {engine} database",
    "jobs.step.deploy.copying_data": "Copying the data",
    "jobs.step.deploy.deploying": "Deploying",
    "jobs.step.deploy.complete": "Deployment complete",
    "jobs.step.promote.complete": "Promotion complete",
    "jobs.step.update.complete": "Update complete",
    "jobs.step.update.unit_not_running": ("Restarted, but the unit is not running: check its logs"),
    "jobs.step.delete.complete": "Deletion complete",
    "jobs.step.migrate.planning": "Planning the migration",
    "jobs.step.migrate.moving": "Moving the tree onto releases",
    "jobs.step.migrate.complete": "Migration complete",
    "jobs.step.backup.creating": "Creating backup",
    "jobs.step.backup.complete": "Backup complete",
    "jobs.step.backup.uploading": "Uploading to {destination}",
    "jobs.step.backup.upload_complete": "Upload complete",
    "jobs.step.restore.restoring": "Restoring backup",
    "jobs.step.restore.downloading": "Downloading and restoring backup",
    "jobs.step.restore.complete": "Restore complete",
    "jobs.step.rollback.rolling_back": "Rolling back",
    "jobs.step.rollback.to_deployment": "Going back to deployment {deployment}",
    "jobs.step.rollback.complete": "Rollback complete",
    "jobs.step.pitr.recovering": "Point-in-time recovery: {action}",
    "jobs.step.engine.installing": "Installing {engine}",
    "jobs.step.engine.uninstalling": "Uninstalling {engine}",
    "jobs.step.certificate.requesting": "Requesting certificate",
    "jobs.step.certificate.serving": "Serving it",
    "jobs.step.certificate.created": "Certificate created",
    "jobs.step.exec.running": "Running a command in {domain}",
    "jobs.step.exec.finished": "Command finished",
    "jobs.step.renewal.renewing": "Renewing certificates",
    "jobs.step.renewal.complete": "Renewal complete",
    "jobs.step.renewal.none_due": "No certificate was due for renewal",
    "jobs.step.renewal.unreadable": (
        "certbot's certificate list could not be read, so which ones were renewed is unknown"
    ),
    "jobs.step.sandbox.building": "Building the current commit in the sandbox",
    "jobs.step.packages.installing": "Installing the system packages",
    "jobs.step.hardening.changing": "Changing the sandbox of the application's unit",
    "jobs.step.tuning.tuning": "Tuning the application's unit",
    "jobs.step.identity.moving": "Moving the application to its own account",
    "jobs.step.replicas.starting": "Starting the copies and asking each one if it answers",
    "jobs.step.zero_downtime.on": "Turning zero downtime on",
    "jobs.step.zero_downtime.off": "Turning zero downtime off",
    "jobs.step.staged.building": "Building without activating",
    "jobs.step.staged.built": "Built, waiting to be promoted",
    "jobs.step.staged.promoting": "Making the release serve",
    "jobs.step.staged.promoted": "Promoted",
    "jobs.step.bundle.building": "Building to ship",
    "jobs.step.bundle.packed": "Packed, waiting to be fetched",
    "jobs.step.bundle.receiving": "Receiving the release",
    "jobs.step.bundle.active": "Release active",
    "jobs.step.disk.measuring": "Measuring the application's directory",
    "jobs.step.disk.giving_limit": "Giving the limit to the kernel",
    "jobs.step.disk.taking_limit": "Taking the limit off the kernel",
    "jobs.step.clone.complete": "Clone complete",
    "jobs.step.import.complete": "Import complete",
    "jobs.step.domains.extending": "Extending the certificate to every domain",
    "jobs.step.domains.extended": "Certificate covers every domain",
    "jobs.step.domains.removing": "Removing the domain from the certificate",
    "jobs.step.domains.removed": "Certificate covers only the remaining domains",
    "jobs.step.domains.deleting": "Deleting the certificate of {name}",
    "jobs.step.domains.deleted": "The certificate is deleted",
    "jobs.step.domains.ordering": "Ordering the certificate of its own",
    "jobs.step.domains.giving_back": "Giving it back",
    "jobs.step.domains.in_place": "The certificate is in place",
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
