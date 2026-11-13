# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The line of help of each command the root group loads on demand. Generated, do not edit.

``noust --help`` and the shell completion list every command with the first paragraph of its help,
and the help is in the module that defines it, so drawing the page meant importing all of them
(seven hundred modules, a second and a half). This table is what Click reads instead, and
``noust.cli.app.LazyGroup`` falls back to importing a command that is not in it. A test holds it to
the commands: after changing a command's help or adding a command, run
``python scripts/generate_command_summaries.py``.

Each entry is ``(hidden, deprecated, short_help, help)``: the attributes of the command that decide
its line. ``help`` is its first paragraph only, which is all Click reads for the line.
"""

from __future__ import annotations

SUMMARIES: dict[str, tuple[bool, bool | str, str, str]] = {
    "2fa": (
        False,
        False,
        "",
        "Enrol, confirm, disable or recover two-factor authentication for logins.",
    ),
    "access": (
        False,
        False,
        "",
        "Roles, and where each person holds them.",
    ),
    "alert": (
        False,
        False,
        "",
        "Alert rules on CPU, memory, disk, load and traffic.",
    ),
    "app": (
        False,
        False,
        "",
        "Change how a deployed application is laid out and what it may use.",
    ),
    "approval": (
        False,
        False,
        "",
        "Four-eyes requests: list them, and approve or reject them as root.",
    ),
    "assistant": (
        False,
        False,
        "",
        "Set up and look after the built-in assistant (an AI, with a model of your choice).",
    ),
    "audit": (
        False,
        False,
        "",
        "Read, verify, export and review the audit log.",
    ),
    "auth": (
        False,
        False,
        "",
        "How people sign in to the console besides a password.",
    ),
    "backup": (
        False,
        False,
        "",
        "Create, inspect and restore application backups.",
    ),
    "central": (
        False,
        False,
        "",
        "Run and look after a central: the Noust that manages your servers.",
    ),
    "cert": (
        False,
        False,
        "",
        "Obtain, inspect and retire the SSL certificates of this server.",
    ),
    "compliance": (
        False,
        False,
        "",
        "Choose compliance frameworks (ENS, ISO 27001, NIS2, CIS, SOC 2, GDPR) and their controls.",
    ),
    "compose": (
        False,
        False,
        "",
        "Look at a Docker Compose document before it is deployed.",
    ),
    "config": (
        False,
        False,
        "",
        "Read, upgrade, clean and edit Noust's configuration file.",
    ),
    "console": (
        False,
        False,
        "",
        "Publish the console on a name of its own, over TLS, and choose its sign-in picture.",
    ),
    "context": (
        False,
        False,
        "",
        "Save and choose the servers this machine operates through their API.",
    ),
    "create": (
        False,
        False,
        "",
        "Deploy a web application and put it online.",
    ),
    "cron": (
        False,
        False,
        "",
        "Run commands on a schedule, as systemd timers.",
    ),
    "db": (
        False,
        False,
        "",
        "Install engines, and manage databases, users, backups and application links.",
    ),
    "delete": (
        False,
        False,
        "",
        "Delete an application and everything deployed with it.",
    ),
    "diagnose": (
        False,
        False,
        "",
        "Explain why an application is down.",
    ),
    "dns": (
        False,
        False,
        "",
        "DNS accounts, the DNS-01 challenge, and Noust DNS: a name server of your own.",
    ),
    "docker": (
        False,
        False,
        "",
        "What Docker has on this server, and updating and editing it with a net.",
    ),
    "domain": (
        False,
        False,
        "",
        "Serve an application on more domains: aliases and redirects.",
    ),
    "ens": (
        False,
        False,
        "",
        "Check this server against Spain's ENS (category MEDIUM) and write the evidence.",
    ),
    "env": (
        False,
        False,
        "",
        "Read and set the environment variables of a deployed application.",
    ),
    "environment": (
        False,
        False,
        "",
        "Manage the environments of the projects.",
    ),
    "fleet": (
        False,
        False,
        "",
        "Enroll this server in a fleet, or see the fleet from its central.",
    ),
    "github": (
        False,
        False,
        "",
        "This server's GitHub App: private repositories, push and pull request events.",
    ),
    "gitlab": (
        False,
        False,
        "",
        "GitLab connections: a project and branch picker, and private clones.",
    ),
    "health": (
        False,
        False,
        "",
        "Check the server and report anything that needs attention.",
    ),
    "import": (
        False,
        False,
        "",
        "Read Vercel, Railway, Render or Heroku configuration from a repository, or a Coolify, Dokploy, CapRover or Dokku application from its server.",
    ),
    "incident": (
        False,
        False,
        "",
        "Freeze the evidence of a security incident and lock the console down.",
    ),
    "list": (
        False,
        False,
        "",
        "List the applications deployed on this server.",
    ),
    "log": (
        False,
        False,
        "",
        "Search the logs of every application, and set how long the journal keeps them.",
    ),
    "logdrain": (
        False,
        False,
        "",
        "Ship the logs of applications to Loki, Axiom, Fluent Bit, OTLP or a remote syslog.",
    ),
    "logs": (
        False,
        False,
        "",
        "Show what an application has been writing to its log.",
    ),
    "manifest": (
        False,
        False,
        "",
        "Write a server down, preview what would change, and apply it.",
    ),
    "map": (
        False,
        False,
        "",
        "Show how this server fits together: names, applications, databases, scheduled work and data.",
    ),
    "mcp": (
        False,
        False,
        "",
        "Let clients inspect, deploy and control applications through MCP.",
    ),
    "monitor": (
        False,
        False,
        "",
        "Watch this server and write down what stands out.",
    ),
    "node": (
        False,
        False,
        "",
        "Manage the servers this central manages, through their API over SSH tunnels.",
    ),
    "notify": (
        False,
        False,
        "",
        "Notification channels: what Noust tells you, and where.",
    ),
    "passkey": (
        False,
        False,
        "",
        "List and remove the passkeys that sign in to the console; recover when all are lost.",
    ),
    "preview": (
        False,
        False,
        "",
        "Pull request previews: a short-lived copy of an application per pull request.",
    ),
    "probe": (
        False,
        False,
        "",
        "Probes of an address from outside the application, and the public status page.",
    ),
    "project": (
        False,
        False,
        "",
        "Manage the projects of the teams.",
    ),
    "push": (
        False,
        False,
        "",
        "Deploy an application with git push, to a repository on this server.",
    ),
    "recipe": (
        False,
        False,
        "",
        "The recipe catalogue: browse it, install from it and keep what you installed up to date.",
    ),
    "registry": (
        False,
        False,
        "",
        "The container registry accounts used to pull and build private images.",
    ),
    "releases": (
        False,
        False,
        "",
        "List an application's releases and go back to one instantly.",
    ),
    "restart": (
        False,
        False,
        "",
        "Restart an application, picking up its current build and environment.",
    ),
    "restore-server": (
        False,
        False,
        "",
        "Bring a whole server back from a bundle of Noust's own state.",
    ),
    "rollback": (
        False,
        False,
        "",
        "Return an application to its most recent backup.",
    ),
    "runtime": (
        False,
        False,
        "",
        "The language runtimes (Node, Python, PHP) applications can be pinned to.",
    ),
    "secrets": (
        False,
        False,
        "",
        "Secret managers (Vault, Doppler, Infisical) that variables refer to.",
    ),
    "self-backup": (
        False,
        False,
        "",
        "Back up Noust's own state (store, configuration, secrets, keys, certificates, units).",
    ),
    "self-update": (
        False,
        False,
        "",
        "Let this server install Noust's own new releases: when, which, and what it did.",
    ),
    "server": (
        False,
        False,
        "",
        "Manage this server: updates, reboots, disks, swap, the clock and the journal.",
    ),
    "service": (
        False,
        False,
        "",
        "Manage the systemd services Noust owns.",
    ),
    "sessions": (
        False,
        False,
        "",
        "Manage active panel sessions.",
    ),
    "setup": (
        False,
        False,
        "",
        "Prepare this server, and check that it stayed prepared.",
    ),
    "site": (
        False,
        False,
        "",
        "Manage the nginx and apache virtual hosts on this server.",
    ),
    "start": (
        False,
        False,
        "",
        "Start an application that is stopped.",
    ),
    "status": (
        False,
        False,
        "",
        "Show how an application is configured and whether it is running.",
    ),
    "stop": (
        False,
        False,
        "",
        "Stop an application and leave it stopped.",
    ),
    "store": (
        False,
        False,
        "",
        "Inspect and maintain the database Noust records deployments in.",
    ),
    "stream": (
        False,
        False,
        "",
        "Publish a TCP or UDP port in front of a backend, through nginx and the firewall.",
    ),
    "support": (
        False,
        False,
        "",
        "Collect what a maintainer needs to help you, with the secrets masked.",
    ),
    "team": (
        False,
        False,
        "",
        "Manage teams, and the people in them.",
    ),
    "token": (
        False,
        False,
        "",
        "Manage API tokens: named, scoped credentials for scripts and automation.",
    ),
    "tunnel": (
        False,
        False,
        "",
        "Reach an application through a Cloudflare Tunnel: no public address, no open port.",
    ),
    "update": (
        False,
        False,
        "",
        "Pull the latest code, rebuild and restart an application.",
    ),
    "upload": (
        False,
        False,
        "",
        "Give Noust the code of an application as an archive.",
    ),
    "user": (
        False,
        False,
        "",
        "Manage the accounts that sign in to the console, each with its roles.",
    ),
    "variables": (
        False,
        False,
        "",
        "Variables shared by the server, a team, a project or an environment.",
    ),
    "web": (
        False,
        False,
        "",
        "Run the browser panel for this server.",
    ),
    "webserver": (
        False,
        False,
        "",
        "Which web server serves each site, and moving them to the other one.",
    ),
    "workspace": (
        False,
        False,
        "",
        "A monorepo as one application per workspace.",
    ),
}
