# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The closed catalog of audit events.

ENS op.exp.8.r3.1 asks an operator to say which security events are audited,
and a free-form action string cannot answer that: before this catalog there
were some fifty call sites across fourteen files inventing names as they went,
and nothing could list them. Every event :func:`noust.core.audit.record`
accepts is declared here, with the category an auditor filters by, the syslog
severity it is shipped with and whether it is a sensitive read (revealing a
``.env``, exporting with secrets, reading the audit log itself).

An unknown name is a programming error. ``tests/test_audit_catalog.py`` scans
the source for literal event names and fails on one that is not declared; at
runtime an unknown name is still recorded, as ``audit.unknown_event`` with the
name it was given, so a missed declaration never loses an event.

Names are the RFC 5424 ``MSGID`` of the shipped message, so each one is
printable US-ASCII of at most 32 characters (RFC 5424 section 6.2.7); a test
holds every entry to it.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Syslog severities (RFC 5424, table 2) the catalog uses.
CRITICAL = 2
WARNING = 4
NOTICE = 5
INFO = 6

#: Every category, in the order ``noust audit events`` lists them.
CATEGORIES: tuple[str, ...] = (
    "access",
    "account",
    "config",
    "change",
    "read",
    "denial",
    "system",
    "host",
)

#: Longest name RFC 5424 accepts as a MSGID.
MAX_NAME_LENGTH = 32


@dataclass(frozen=True)
class EventSpec:
    """
    What the catalog says about one event.

    Attributes:
        category: One of :data:`CATEGORIES`.
        severity: Syslog severity of a successful occurrence. A failed or
            refused one is shipped at least at :data:`WARNING`.
        sensitive_read: The event records someone reading something secret.
        description: One sentence for the event table of ``docs/ENS.md``.
    """

    category: str
    severity: int
    description: str
    sensitive_read: bool = False


def _events(category: str, severity: int, entries: dict[str, str]) -> dict[str, EventSpec]:
    return {name: EventSpec(category, severity, text) for name, text in entries.items()}


def _reads(entries: dict[str, str]) -> dict[str, EventSpec]:
    return {
        name: EventSpec("read", INFO, text, sensitive_read=True) for name, text in entries.items()
    }


EVENTS: dict[str, EventSpec] = {
    # Access: every sign-in, credential and second-factor decision.
    **_events(
        "access",
        INFO,
        {
            "auth.login": "Sign-in to the console, successful or not.",
            "auth.logout": "Sign-out of a console session.",
            "auth.credential": "A credential was presented and checked.",
            "auth.elevate": "Sudo mode was confirmed for a session.",
            "auth.unlock": "A locked-out address or account was unlocked.",
            "auth.2fa.enroll": "A second factor enrolment was started.",
            "auth.2fa.confirm": "A second factor was confirmed.",
            "auth.2fa.backup_codes": "Backup codes for the second factor were issued.",
            "auth.password.change": "An account changed its own password.",
            "auth.notice.accept": "The access notice was accepted.",
            "auth.session.revoke": "A console session was revoked.",
            "auth.session.timeout": "A console session expired.",
            "auth.revoke_all": "Every console session was revoked.",
            "auth.revoke_others": "Every other console session was revoked.",
            "auth.ws_ticket": "A single-use WebSocket ticket was issued.",
            "auth.passkey.login": "Sign-in with a passkey.",
            "auth.passkey.rename": "A passkey was renamed.",
            "ws.connect": "A WebSocket handshake, accepted or refused.",
        },
    ),
    **_events(
        "access",
        NOTICE,
        {
            "auth.2fa.disable": "The second factor was turned off.",
            "auth.mfa.reset": "An account's second factors were reset.",
            "auth.password.reset": "An account's password was reset by someone else.",
            "auth.token.create": "An API token was issued.",
            "auth.token.revoke": "An API token was revoked.",
            "auth.passkey.register": "A passkey was registered.",
            "auth.passkey.remove": "A passkey was removed.",
            "auth.passkey.reset": "Every passkey of an account or of the master token was removed.",
        },
    ),
    **_events(
        "access",
        CRITICAL,
        {
            "auth.lockout": "Too many failed attempts locked an address or account out.",
            "auth.break_glass": "The emergency master token was used.",
            "auth.passkey.clone": "A passkey's signature counter went back: it may be cloned.",
        },
    ),
    # Denials: requests refused before they reached what they asked for.
    **_events(
        "denial",
        WARNING,
        {
            "auth.csrf": "A cookie request arrived without a valid CSRF token.",
            "auth.scope": "A credential asked for more than its scope allows.",
            "auth.fleet": "A fleet token was refused.",
            "auth.elevation": "An action needing sudo mode was refused without it.",
            "auth.token.denied": "An API token was refused.",
            "http.request": "An HTTP request was refused by the address allow-list.",
            "websocket.request": "A WebSocket was refused by the address allow-list.",
            "http.denied.rate": "A request was refused by the rate limiter.",
            "http.denied.size": "A request body was over the size limit.",
            "http.denied.permission": "A request was refused by the role's permissions.",
            "http.denied.approval": "A request needing approval was refused without it.",
            "apps.source.denied": "Code from an origin this server does not allow was refused.",
            "auth.lockdown.denied": "A sign-in was refused while the console is locked down.",
        },
    ),
    # Accounts and approvals.
    **_events(
        "account",
        NOTICE,
        {
            "user.create": "An account was created.",
            "user.invite": "An invitation to create an account was issued.",
            "user.activate": "An invitation was used to activate an account.",
            "user.update": "An account's details were changed.",
            "user.disable": "An account was disabled.",
            "user.enable": "An account was enabled.",
            "user.role_change": "An account's role was changed.",
            "user.unlock": "A locked account was unlocked.",
            "user.remove": "An account was removed.",
            "user.purge": "A removed account's personal data was purged.",
            "user.sod_exception": "A separation-of-duties exception was recorded.",
            "access.review": "A periodic review of accounts and roles was recorded.",
            "approval.request": "A four-eyes approval was requested.",
            "approval.approve": "A four-eyes request was approved.",
            "approval.reject": "A four-eyes request was rejected.",
            "approval.use": "An approved request was carried out.",
            "approval.expire": "A four-eyes request expired unused.",
        },
    ),
    # Configuration of Noust itself.
    **_events(
        "config",
        NOTICE,
        {
            "config.update": "Configuration keys were changed through the console.",
            "config.change": "A configuration key was changed.",
            "config.clean": "Obsolete configuration keys were removed.",
            "security.profile.change": "The security profile was changed.",
            "security.profile.exception": "An exception to the security profile was recorded.",
            "security.risk.accept": "A hardening finding was accepted as a risk.",
            "security.risk.revoke": "An accepted risk was withdrawn.",
            "audit.sink.change": "Where the audit log is shipped was changed.",
            "central.unlock": "A sealed central was unlocked.",
            "central.lock": "A central's secrets were locked.",
            "central.seal": "A central's secrets were sealed.",
            "central.unseal": "A central's secrets were unsealed.",
        },
    ),
    # Changes to applications and to the machine.
    **_events(
        "change",
        NOTICE,
        {
            "api.post": "A state-changing POST reached the API.",
            "api.put": "A state-changing PUT reached the API.",
            "api.patch": "A state-changing PATCH reached the API.",
            "api.delete": "A DELETE reached the API.",
            "job.queue": "A background job was queued.",
            "apps.create": "An application was deployed for the first time.",
            "apps.update": "An application was updated.",
            "apps.deploy": "A deployment of an application ran.",
            "apps.rollback": "An application was rolled back.",
            "apps.delete": "An application was deleted.",
            "apps.restart": "An application was restarted.",
            "apps.start": "An application was started.",
            "apps.stop": "An application was stopped.",
            "apps.migrate": "An application was moved onto releases.",
            "apps.limits": "An application's resource limits were changed.",
            "apps.source": "An application's source was changed.",
            "apps.import": "An application was imported.",
            "apps.export": "An application was exported without its secrets.",
            "apps.env.update": "An application's environment was changed.",
            "apps.env.write": "An application's .env file was written.",
            "apps.env.marks": "Which environment variables are secret was changed.",
            "apps.sandbox": "An application's build sandbox was changed.",
            "apps.backup_before_update": "Whether an update copies a stack's databases first was changed.",
            "apps.identity": "An application's own system account was given or taken away.",
            "apps.hooks": "An application's deploy hooks were set or cleared.",
            "hooks.deploy": "A deploy webhook delivery was handled.",
            "hooks.github": "A GitHub App delivery was handled.",
            "hooks.secret.mint": "An application's webhook secret was created or rotated.",
            "hooks.secret.reveal": "An application's webhook secret was shown.",
            "hooks.secret.disable": "An application's webhook was disabled.",
            "services.create": "A service unit was created.",
            "services.update": "A service unit was changed.",
            "services.delete": "A service unit was deleted.",
            "services.start": "A service was started.",
            "services.stop": "A service was stopped.",
            "services.restart": "A service was restarted.",
            "sites.create": "A web server site was created.",
            "sites.update": "A web server site was changed.",
            "sites.delete": "A web server site was deleted.",
            "sites.enable": "A web server site was enabled.",
            "sites.disable": "A web server site was disabled.",
            "certs.create": "A certificate was requested.",
            "certs.renew": "A certificate was renewed.",
            "certs.delete": "A certificate was deleted.",
            "certs.revoke": "A certificate was revoked.",
            "cron.create": "A scheduled task was created.",
            "cron.update": "A scheduled task was changed.",
            "cron.delete": "A scheduled task was deleted.",
            "cron.run": "A scheduled task was run by hand.",
            "cron.enable": "A scheduled task was enabled.",
            "cron.disable": "A scheduled task was disabled.",
            "backups.create": "A backup was taken.",
            "backups.delete": "A backup was deleted.",
            "backups.restore": "A backup was restored.",
            "backups.verify": "A backup was verified.",
            "backups.push": "A backup was sent to a remote destination.",
            "backup.schedule.create": "A backup schedule was created.",
            "backup.schedule.update": "A backup schedule was changed.",
            "backup.schedule.delete": "A backup schedule was deleted.",
            "backup.destination.create": "A remote backup destination was added.",
            "backup.destination.update": "A remote backup destination was changed.",
            "backup.destination.delete": "A remote backup destination was removed.",
            "db.create": "A database was created.",
            "db.drop": "A database was dropped.",
            "db.user.create": "A database user was created.",
            "db.user.drop": "A database user was dropped.",
            "db.user.rotate": "A database user's password was rotated.",
            "db.grant": "Database privileges were granted.",
            "db.revoke": "Database privileges were revoked.",
            "db.backup": "A database dump was taken.",
            "db.backup.policy": "A database's backup policy was set or changed.",
            "db.backup.policy.remove": "A database's backup policy was removed.",
            "db.backup.verify": "A database dump was checked or test-restored.",
            "db.backup.push": "A database dump was sent to a remote destination.",
            "db.backup.delete": "A database dump was deleted from here or from a destination.",
            "db.restore": "A database dump was restored.",
            "db.query": "A statement that may write ran in the SQL console.",
            "db.row.update": "A row was edited in the data explorer.",
            "db.row.insert": "A row was inserted in the data explorer.",
            "db.row.delete": "A row was deleted in the data explorer.",
            "db.link": "A database was linked to an application.",
            "db.unlink": "A database was unlinked from an application.",
            "db.adopt": "Databases created outside Noust were recorded as Noust's.",
            "db.forget": "A database that no longer exists was forgotten.",
            "db.fix_owner": "A database and its objects were given to another owner.",
            "db.user.profile": "A database user's access profile was changed.",
            "db.install": "A database engine was installed.",
            "db.uninstall": "A database engine was removed.",
            "integration.github.manifest": "Creating a GitHub App was started.",
            "integration.github.create": "A GitHub App was created.",
            "integration.github.install": "A GitHub App installation was added.",
            "integration.github.sync": "GitHub App installations were synchronised.",
            "integration.github.remove": "The GitHub App was removed.",
            "fleet.authorize": "A central was authorized on this node.",
            "fleet.deauthorize": "A central's authorization on this node was removed.",
            "fleet.access": "What a central may do on this node was changed.",
            "fleet.node.add": "A node was added to this central.",
            "fleet.node.remove": "A node was removed from this central.",
            "fleet.node.test": "A node's connection was tested.",
            "fleet.node.rekey": "A node's key was rotated.",
            "fleet.node.migrate": "A node was moved onto the restricted tunnel account.",
            "fleet.tunnel.up": "The tunnel to a node opened.",
            "fleet.tunnel.down": "The tunnel to a node closed.",
            "fleet.tunnel.hostkey_changed": "A node answered with a different host key.",
            "fleet.node.label": "A node's labels were changed.",
            "fleet.action": "An action was run on several servers of the fleet.",
            "system.update": "Noust itself was updated.",
            "previews.settings": "Preview deployments were configured.",
            "previews.enable": "Preview deployments were turned on.",
            "previews.disable": "Preview deployments were turned off.",
            "previews.remove": "A preview deployment was removed.",
            "server.update": "Operating system updates were applied.",
            "server.refresh": "The package index was refreshed.",
            "server.reboot": "A reboot or shutdown was requested or scheduled.",
            "server.firewall": "The firewall was changed.",
            "server.ssh": "The SSH daemon or its keys were changed.",
            "server.fail2ban": "fail2ban was installed or changed.",
            "server.storage": "Storage was cleaned or swap was changed.",
            "server.time": "The clock, time zone or NTP was changed.",
            "server.hostname": "The hostname was changed.",
            "server.users": "A system account was changed.",
            "incident.freeze": "An incident evidence package was taken.",
            "incident.unfreeze": "The console's incident lockdown was lifted.",
            "apps.inventory": "An application's owner, criticality or classification changed.",
        },
    ),
    **_events(
        "change",
        CRITICAL,
        {
            "incident.lockdown": "The console was locked down for an incident.",
        },
    ),
    # Sensitive reads: someone saw something secret.
    **_reads(
        {
            "apps.env.reveal": "An application's secret environment values were shown.",
            "apps.export.secrets": "An application was exported with its secrets.",
            "audit.read": "The audit log was read.",
            "audit.export": "The audit log was exported.",
            "users.list": "The list of accounts was read.",
            "tokens.list": "The list of API tokens was read.",
            "sessions.list": "The list of console sessions was read.",
            "processes.cmdline": "Command lines of running processes were read.",
            "logs.noust.read": "Noust's own logs were read.",
            "db.query.read": "A read-only statement ran in the SQL console.",
            "db.query.export": "A SQL console result was exported.",
            "db.browse": "Rows or key values were read in the data explorer.",
            "db.backup.download": "A database dump was downloaded.",
            "db.connection_string": "A database connection string was shown.",
            "db.user.password.show": "A database user's password was shown.",
            "backup.destination.key": "A backup destination's encryption key was shown.",
            "fleet.node.key": "A node's key was shown.",
            "compliance.read": "The compliance report was read.",
        }
    ),
    # Noust's own operation, the audit trail's included.
    **_events(
        "system",
        INFO,
        {
            "system.start": "The console started.",
            "system.stop": "The console stopped.",
            "audit.chain_start": "A new audit chain was started.",
            "audit.checkpoint": "The chain's head, for a receiver to compare with.",
            "audit.recovered": "Audit writes work again after a failure.",
            "audit.verify": "The audit chain was verified.",
            "audit.legacy": "A line of the old noust.audit logger with no catalog entry.",
            "audit.sink.recovered": "Shipping to an audit destination works again.",
            "cli.command.start": "A command that changes something was started at the terminal.",
            "cli.command": "A command that changes something at the terminal ended.",
            "retention.prune": "Records past their retention period were deleted.",
        },
    ),
    **_events(
        "system",
        NOTICE,
        {
            "audit.key_created": "A new key for the audit chain was created.",
            "audit.purge": "Audit files past their retention period were deleted.",
            "audit.review": "Someone attested that they reviewed the audit log for a period.",
        },
    ),
    **_events(
        "system",
        WARNING,
        {
            "audit.coalesced": "Repeated anonymous events were counted instead of written.",
            "audit.clock_unsynced": "The clock is not synchronised.",
            "audit.unknown_event": "An event missing from the catalog was recorded.",
        },
    ),
    **_events(
        "system",
        CRITICAL,
        {
            "audit.degraded": "The audit log is failing or over its size limit.",
            "audit.gap": "The audit chain has a gap or a broken link.",
            "audit.sink.degraded": "An audit destination has not received events for too long.",
        },
    ),
    # The host action ledger: what Noust actually did as root.
    **_events(
        "host",
        INFO,
        {
            "host.exec": "Noust ran a process that changes the system.",
            "host.fs": "Noust changed a file or directory.",
            "host.truncated": "Further host actions of one request were not recorded.",
        },
    ),
}


class UnknownAuditEvent(ValueError):
    """An event name that is not in :data:`EVENTS`."""


def spec_for(name: str) -> EventSpec:
    """
    Look up an event.

    Args:
        name: The event name.

    Returns:
        Its catalog entry.

    Raises:
        UnknownAuditEvent: The name is not in the catalog.
    """
    try:
        return EVENTS[name]
    except KeyError:
        raise UnknownAuditEvent(name) from None


def is_known(name: str) -> bool:
    """
    Report whether an event is in the catalog.

    Args:
        name: The event name.

    Returns:
        True when :func:`spec_for` would find it.
    """
    return name in EVENTS


def severity_of(spec: EventSpec, outcome: str) -> int:
    """
    The syslog severity one occurrence is shipped with.

    Args:
        spec: The event's catalog entry.
        outcome: How it ended.

    Returns:
        The catalog severity, raised to at least :data:`WARNING` when the
        outcome is a failure or a refusal: a failed sign-in matters more than
        a successful one.
    """
    if outcome in FAILED_OUTCOMES or outcome.startswith("error"):
        return min(spec.severity, WARNING)
    return spec.severity


#: Outcomes that mean the action did not happen as asked.
FAILED_OUTCOMES = frozenset({"failure", "denied", "error", "interrupted"})


def markdown_table() -> str:
    """
    Render the catalog as the Markdown table ``docs/ENS.md`` includes.

    Returns:
        One row per event, grouped by category.
    """
    rows = [
        "| Event | Category | Severity | Sensitive read | Description |",
        "|---|---|:-:|:-:|---|",
    ]
    order = {category: index for index, category in enumerate(CATEGORIES)}
    for name, spec in sorted(EVENTS.items(), key=lambda item: (order[item[1].category], item[0])):
        sensitive = "yes" if spec.sensitive_read else ""
        rows.append(
            f"| `{name}` | {spec.category} | {spec.severity} | {sensitive} | {spec.description} |"
        )
    return "\n".join(rows) + "\n"
