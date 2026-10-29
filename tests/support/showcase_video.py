# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Kelmoor as the product video tells it: the "video" profile of :mod:`tests.support.showcase`.

The documentation screenshots (the "docs" profile) are pictures of a moment: one
application already failed, one deploy that never ends. The product video is a morning at
the agency instead, filmed from the real console against ``scripts/console_server.py
--showcase --profile video``, and what happens in it really happens in the sandbox:

- an operator, Marta Solano, signs in with her own account (:func:`seed_video_people`), so
  what she does is audited under her name, not the access token's;
- she creates ``tidewick.io`` from a repository, and the deploy streams, passes its health
  check and serves;
- Iker Beitia's push to ``shop.brumaria.es`` (:data:`NEXT_COMMITS`) builds a release that
  never answers, and the health gate goes back to the release that was serving: the
  application never stops, which is why it starts this profile running, on releases, with
  four good deployments behind it;
- a certificate twelve days from expiry is renewed, WordPress is installed from the
  catalog, the nightly backups and their two destinations are there, and on "ams-3" the
  disk rule is firing until she acknowledges it.

So every application is on the releases layout but the ones that cannot be (a Compose stack
and a PHP-FPM site deploy in place), every history is written in working hours
(``ShowcaseApp.clock``), there are four servers (:data:`VIDEO_NODES`) and nineteen
applications once the two the video creates exist: eleven on "fra-1", three on "ams-3", two
on "lon-2" and three on "mad-1".

Every name was checked the way :mod:`tests.support.showcase`'s docstring says. The domains
this profile adds answered NXDOMAIN on 2026-10-29 (``tidewick.io``, ``alderbrook.es``,
``marenbay.com``, ``quillfield.io``); ``api.``, ``status.`` and ``www.`` names are
subdomains of checked ones. "mad-1" is an infrastructure label like the other three, and its
SSH address is in an RFC 5737 range. Lucía Ortega, the agency's client at Brumaria, is as
invented as the agency's own people.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from noust.core.store import AppStatus, Database, DatabaseEngine
from tests.support.showcase import AGENCY_DOMAIN, ShowcaseApp, ShowcaseCommit

if TYPE_CHECKING:
    from noust.core.store import NoustStore
    from tests.support.showcase import ShowcaseState

_RUNNING = AppStatus.RUNNING.value

#: "fra-1", the agency's central and main production server, in Frankfurt: nine
#: applications before the video adds ``tidewick.io`` and ``alderbrook.es``.
VIDEO_FRA_APPS: tuple[ShowcaseApp, ...] = (
    ShowcaseApp(
        domain="wrenfield.io",
        app_type="nextjs",
        repo="wrenfield-site",
        branch="main",
        port=3000,
        status=_RUNNING,
        is_static=False,
        alias="www.wrenfield.io",
        layout="releases",
        commits=(
            ShowcaseCommit("a1c4f92", "Rework the homepage hero section", "Marta Solano"),
            ShowcaseCommit(
                "5e9d0b3", "Bump Next.js to 15.2 and fix the sitemap route", "Iker Beitia"
            ),
            ShowcaseCommit("2f7a8c1", "Add the contact form's spam check", "Marta Solano"),
            ShowcaseCommit(
                "9b3e0d6", "Tune image loading on the case studies page", "Owen Whitcombe"
            ),
        ),
        deployed_days_ago=(21, 14, 7, 1),
        clock=("10:12", "16:40", "12:05", "17:22"),
    ),
    ShowcaseApp(
        domain="app.wrenfield.io",
        app_type="nextjs",
        repo="wrenfield-portal",
        branch="main",
        port=3001,
        status=_RUNNING,
        is_static=False,
        has_cert=False,
        layout="releases",
        commits=(
            ShowcaseCommit(
                "7c1d9a4", "Wire the billing page to the new invoices endpoint", "Noa Ferreira"
            ),
            ShowcaseCommit(
                "e08f61b", "Add multi-seat invites to the onboarding flow", "Dana Kessler"
            ),
        ),
        deployed_days_ago=(5, 2),
        clock=("15:31", "10:48"),
    ),
    ShowcaseApp(
        domain="api.kestrelworks.io",
        app_type="nodejs",
        repo="kestrelworks-api",
        branch="main",
        port=4000,
        status=_RUNNING,
        is_static=False,
        layout="releases",
        commits=(
            ShowcaseCommit("b4a0e27", "Add rate limiting to the public API", "Julen Anitua"),
            ShowcaseCommit("0d6c3f9", "Fix pagination cursor for large exports", "Sara Lindqvist"),
            ShowcaseCommit("6f2b8e5", "Upgrade Fastify to 5.x", "Julen Anitua"),
        ),
        deployed_days_ago=(18, 9, 3),
        clock=("09:47", "14:20", "11:02"),
    ),
    ShowcaseApp(
        domain="status.kestrelworks.io",
        app_type="static",
        repo="kestrelworks-status",
        branch="main",
        port=None,
        status=_RUNNING,
        is_static=True,
        layout="releases",
        commits=(
            ShowcaseCommit(
                "3a9e1c0", "Add the incidents timeline to the status page", "Marc Bofarull"
            ),
            ShowcaseCommit("8c42d1f", "Announce Saturday's maintenance window", "Marc Bofarull"),
        ),
        # The second one is this morning's first deploy, at 08:02.
        deployed_days_ago=(10, 0),
        clock=("16:05", "08:02"),
    ),
    ShowcaseApp(
        domain="panel.corvane.io",
        app_type="python",
        repo="corvane-panel",
        branch="main",
        port=8000,
        status=_RUNNING,
        is_static=False,
        has_cert=False,
        layout="releases",
        commits=(
            ShowcaseCommit("c7d2a68", "Ship the new billing dashboard widgets", "Dana Kessler"),
            ShowcaseCommit(
                "1e5f4b3", "Fix timezone handling in the reports export", "Noa Ferreira"
            ),
        ),
        deployed_days_ago=(12, 4),
        clock=("13:15", "10:37"),
    ),
    ShowcaseApp(
        domain="blog.nortewave.com",
        app_type="php-fpm",
        repo="nortewave-blog",
        branch="main",
        port=None,
        status=_RUNNING,
        # Stored as static, the way a real PHP-FPM deploy leaves it (see showcase.FRA_APPS).
        is_static=True,
        alias="news.nortewave.com",
        cert_expires_soon=True,
        commits=(
            ShowcaseCommit("4b8c0f1", "Update the WordPress core and plugins", "Sara Lindqvist"),
            ShowcaseCommit("8a3d7e2", "Apply the new editorial theme", "Marta Solano"),
        ),
        deployed_days_ago=(20, 6),
        clock=("09:30", "12:44"),
    ),
    ShowcaseApp(
        domain="queue.fennwick.io",
        app_type="docker-compose",
        repo="fennwick-queue",
        branch="main",
        port=9090,
        status=_RUNNING,
        is_static=False,
        has_cert=False,
        commits=(
            ShowcaseCommit("f01a5c8", "Add the retry queue for failed webhooks", "Owen Whitcombe"),
            ShowcaseCommit("2c9b6d4", "Pin the worker image to 1.8.2", "Julen Anitua"),
        ),
        deployed_days_ago=(15, 5),
        clock=("15:12", "09:55"),
    ),
    ShowcaseApp(
        domain="shop.brumaria.es",
        app_type="nextjs",
        repo="brumaria-shop",
        branch="main",
        port=3100,
        # Running: the push that fails (NEXT_COMMITS) happens during the video, and the
        # release it builds never takes the traffic.
        status=_RUNNING,
        is_static=False,
        alias="tienda.brumaria.es",
        layout="releases",
        commits=(
            ShowcaseCommit("9d4e7a1", "Add gift card redemption to checkout", "Marc Bofarull"),
            ShowcaseCommit(
                "6b1f2c8", "Migrate the cart service to the new pricing API", "Sara Lindqvist"
            ),
            ShowcaseCommit("c27e4d1", "Fix the VAT rounding on invoices", "Sara Lindqvist"),
            ShowcaseCommit("5f8a3b6", "Speed up the product listing query", "Marc Bofarull"),
        ),
        deployed_days_ago=(16, 8, 4, 1),
        clock=("11:20", "16:48", "10:05", "18:31"),
    ),
    ShowcaseApp(
        domain="automations.kelmoor.dev",
        app_type="nodejs",
        repo="kelmoor-automations",
        branch="main",
        port=5678,
        status=_RUNNING,
        is_static=False,
        recipe="n8n",
        commits=(ShowcaseCommit("0a6d3e9", "Install n8n from the catalog", "Julen Anitua"),),
        deployed_days_ago=(27,),
        clock=("12:30",),
    ),
)

#: "ams-3", the client server in Amsterdam: three sites, and a disk at 91 % (the API's
#: database, ``palenbrook_api``, grew), which is the alert that fires.
VIDEO_AMS_APPS: tuple[ShowcaseApp, ...] = (
    ShowcaseApp(
        domain="thornfield.io",
        app_type="nextjs",
        repo="thornfield-site",
        branch="main",
        port=3000,
        status=_RUNNING,
        is_static=False,
        alias="www.thornfield.io",
        layout="releases",
        commits=(
            ShowcaseCommit("d2a4c19", "Refresh the pricing page copy", "Noa Ferreira"),
            ShowcaseCommit("9f0b3e6", "Add the newsletter signup", "Marta Solano"),
        ),
        deployed_days_ago=(11, 2),
        clock=("10:20", "15:43"),
    ),
    ShowcaseApp(
        domain="palenbrook.io",
        app_type="nodejs",
        repo="palenbrook-api",
        branch="main",
        port=4100,
        status=_RUNNING,
        is_static=False,
        layout="releases",
        commits=(
            ShowcaseCommit("5c8a1f0", "Add a health check endpoint", "Julen Anitua"),
            ShowcaseCommit("b3e7d29", "Bump dependencies", "Sara Lindqvist"),
        ),
        deployed_days_ago=(9, 1),
        clock=("12:10", "09:14"),
    ),
    ShowcaseApp(
        domain="marenbay.com",
        app_type="static",
        repo="marenbay-site",
        branch="main",
        port=None,
        status=_RUNNING,
        is_static=True,
        alias="www.marenbay.com",
        layout="releases",
        commits=(
            ShowcaseCommit("e4b7a20", "Publish the autumn menu", "Noa Ferreira"),
            ShowcaseCommit("71c9f3d", "Add the table booking widget", "Owen Whitcombe"),
        ),
        deployed_days_ago=(6, 3),
        clock=("17:05", "11:48"),
    ),
)

#: "lon-2", staging in London: freshly redeployed this morning, nothing public.
VIDEO_LON_APPS: tuple[ShowcaseApp, ...] = (
    ShowcaseApp(
        domain="staging.wrenfield.io",
        app_type="nextjs",
        repo="wrenfield-site",
        branch="staging",
        port=3000,
        status=_RUNNING,
        is_static=False,
        has_cert=False,
        layout="releases",
        commits=(
            ShowcaseCommit("a44f2c1", "Merge main into staging", "Owen Whitcombe"),
            ShowcaseCommit("c60d9b7", "Try the new pricing page copy live", "Noa Ferreira"),
        ),
        deployed_days_ago=(4, 0),
        clock=("13:30", "09:40"),
    ),
    ShowcaseApp(
        domain="staging.kestrelworks.io",
        app_type="nodejs",
        repo="kestrelworks-api",
        branch="staging",
        port=4000,
        status=_RUNNING,
        is_static=False,
        has_cert=False,
        layout="releases",
        commits=(ShowcaseCommit("e91b0aa", "Verify the new rate limiter", "Dana Kessler"),),
        deployed_days_ago=(2,),
        clock=("16:22",),
    ),
)

#: "mad-1", the clients in Spain, in Madrid: up for a month, one shop and its API.
VIDEO_MAD_APPS: tuple[ShowcaseApp, ...] = (
    ShowcaseApp(
        domain="quillfield.io",
        app_type="nextjs",
        repo="quillfield-shop",
        branch="main",
        port=3000,
        status=_RUNNING,
        is_static=False,
        alias="www.quillfield.io",
        layout="releases",
        commits=(
            ShowcaseCommit("4d1e8b2", "Add the size guide to product pages", "Noa Ferreira"),
            ShowcaseCommit(
                "93f0c5a", "Switch checkout to the new payment provider", "Julen Anitua"
            ),
        ),
        deployed_days_ago=(24, 6),
        clock=("12:30", "10:15"),
    ),
    ShowcaseApp(
        domain="api.quillfield.io",
        app_type="python",
        repo="quillfield-api",
        branch="main",
        port=8000,
        status=_RUNNING,
        is_static=False,
        layout="releases",
        commits=(
            ShowcaseCommit("2b8e6d1", "Add the stock sync endpoint", "Dana Kessler"),
            ShowcaseCommit("f5a1c07", "Cache the catalogue export", "Dana Kessler"),
        ),
        deployed_days_ago=(13, 2),
        clock=("09:20", "17:50"),
    ),
    ShowcaseApp(
        domain="status.quillfield.io",
        app_type="static",
        repo="quillfield-status",
        branch="main",
        port=None,
        status=_RUNNING,
        is_static=True,
        layout="releases",
        commits=(ShowcaseCommit("6e3d9b4", "Publish the status page", "Marc Bofarull"),),
        deployed_days_ago=(19,),
        clock=("11:00",),
    ),
)

#: The fleet: "fra-1" is the central, the other three join it.
VIDEO_NODES: dict[str, tuple[ShowcaseApp, ...]] = {
    "fra-1": VIDEO_FRA_APPS,
    "ams-3": VIDEO_AMS_APPS,
    "lon-2": VIDEO_LON_APPS,
    "mad-1": VIDEO_MAD_APPS,
}

#: CPU, memory, disk (percent) and uptime (seconds) of each server, as
#: ``showcase.NODE_READINGS`` gives them for the docs; "ams-3"'s disk is at 91 %, past the
#: agency's rule (85 %) and the monitor's built-in check (90 %) alike.
VIDEO_READINGS: dict[str, tuple[float, float, float, float]] = {
    "fra-1": (34.0, 32.0, 24.0, 20 * 3_600 + 22 * 60),
    "ams-3": (12.0, 41.0, 91.0, 6 * 86_400 + 4 * 3_600),
    "lon-2": (46.0, 28.0, 18.0, 9 * 3_600 + 40 * 60),
    "mad-1": (21.0, 37.0, 42.0, 31 * 86_400 + 2 * 3_600),
}

#: Each node's SSH address, as Settings > Servers shows it (RFC 5737 ranges).
VIDEO_SSH_HOSTS: dict[str, str] = {
    "ams-3": "203.0.113.24",
    "lon-2": "198.51.100.61",
    "mad-1": "203.0.113.87",
}


@dataclass(frozen=True)
class NextCommit:
    """
    What the next push to one of the agency's repositories holds.

    Read by ``scripts/console_server``'s code host, which answers ``git`` for
    ``github.com/kelmoor-labs`` in this profile: the commit a fetch finds, its subject, and
    whether the release built from it starts at all.

    Attributes:
        commit: The commit, its subject and its author.
        crashes: The release it builds exits as soon as it is started (a runtime bump that
            left the build output where the old runtime put it), so its health check fails.
        journal: What the release's process prints before it exits, when it crashes.
    """

    commit: ShowcaseCommit
    crashes: bool = False
    journal: tuple[str, ...] = ()


#: The repositories the video deploys from, by name under ``github.com/kelmoor-labs``.
NEXT_COMMITS: dict[str, NextCommit] = {
    # A new site: the application the video creates first.
    "tidewick-site": NextCommit(
        ShowcaseCommit("7e2c9d4", "First release of the Tidewick site", "Noa Ferreira")
    ),
    # Iker's push: the build passes and the release never answers.
    "brumaria-shop": NextCommit(
        ShowcaseCommit("d3a8e05", "Bump the Node runtime to 24 LTS", "Iker Beitia"),
        crashes=True,
        journal=(
            "node:internal/modules/cjs/loader:1386",
            "Error: Cannot find module '/var/www/apps/shop-brumaria-es/current/.next/standalone/server.js'",
            "    at Module._resolveFilename (node:internal/modules/cjs/loader:1383:15)",
            "Node.js v24.11.0",
        ),
    ),
}

#: The applications the video creates, and the server they are created on.
VIDEO_NEW_APPS: dict[str, str] = {"tidewick.io": "fra-1", "alderbrook.es": "fra-1"}


@dataclass(frozen=True)
class VideoPerson:
    """
    One of the agency's people, with an account on the central.

    Attributes:
        username: Sign-in name.
        name: How the console names them.
        role: Their role.
        email: Where their invitations and password resets go.
    """

    username: str
    name: str
    role: str
    email: str


#: The operator the video is filmed as.
OPERATOR = "marta"

#: The password every seeded account signs in with (the sandbox only).
VIDEO_PASSWORD = "Kelmoor-morning-shift-26"

#: The agency's people (the authors of :mod:`tests.support.showcase`), and the client who
#: may only look at her own shop.
VIDEO_PEOPLE: tuple[VideoPerson, ...] = (
    VideoPerson("marta", "Marta Solano", "admin", f"marta@{AGENCY_DOMAIN}"),
    VideoPerson("dana", "Dana Kessler", "operator", f"dana@{AGENCY_DOMAIN}"),
    VideoPerson("julen", "Julen Anitua", "operator", f"julen@{AGENCY_DOMAIN}"),
    VideoPerson("iker", "Iker Beitia", "operator", f"iker@{AGENCY_DOMAIN}"),
    VideoPerson("noa", "Noa Ferreira", "viewer", f"noa@{AGENCY_DOMAIN}"),
    VideoPerson("owen", "Owen Whitcombe", "viewer", f"owen@{AGENCY_DOMAIN}"),
    VideoPerson("sara", "Sara Lindqvist", "viewer", f"sara@{AGENCY_DOMAIN}"),
    VideoPerson("marc", "Marc Bofarull", "viewer", f"marc@{AGENCY_DOMAIN}"),
    VideoPerson("lucia", "Lucía Ortega", "viewer", "lucia@brumaria.es"),
)

#: Each server's databases: name, engine, port, the application that uses it and its size in
#: bytes (``palenbrook_api`` is what filled "ams-3"'s disk).
VIDEO_DATABASES: dict[str, tuple[tuple[str, str, int, str | None, int], ...]] = {
    "fra-1": (
        ("wrenfield_prod", DatabaseEngine.POSTGRESQL.value, 5432, "wrenfield.io", 2_254_857_830),
        ("brumaria_shop", DatabaseEngine.POSTGRESQL.value, 5432, "shop.brumaria.es", 6_227_702_579),
        ("nortewave_wp", DatabaseEngine.MYSQL.value, 3306, "blog.nortewave.com", 671_088_640),
        ("sessions", DatabaseEngine.REDIS.value, 6379, None, 48_234_496),
    ),
    "ams-3": (
        ("thornfield_prod", DatabaseEngine.POSTGRESQL.value, 5432, "thornfield.io", 1_288_490_188),
        ("palenbrook_api", DatabaseEngine.POSTGRESQL.value, 5432, "palenbrook.io", 3_650_722_201),
    ),
    "lon-2": (
        (
            "wrenfield_staging",
            DatabaseEngine.POSTGRESQL.value,
            5432,
            "staging.wrenfield.io",
            318_767_104,
        ),
    ),
    "mad-1": (
        ("quillfield_prod", DatabaseEngine.POSTGRESQL.value, 5432, "quillfield.io", 943_718_400),
    ),
}


@dataclass(frozen=True)
class VideoAlertRule:
    """
    One alert rule as the agency wrote it.

    Attributes:
        name: Its identifier.
        metric: A key of :data:`noust.core.alerts.model.METRICS`.
        threshold: The limit, in the metric's unit.
        duration_s: How long it must be past the limit.
        severity: ``warning`` or ``failed``.
        note: What whoever reads it later should know.
        app_domain: The application an application metric is about.
        nodes: The servers that have it; None for every one.
    """

    name: str
    metric: str
    threshold: float
    duration_s: int
    severity: str
    note: str
    app_domain: str | None = None
    nodes: tuple[str, ...] | None = None


VIDEO_ALERT_RULES: tuple[VideoAlertRule, ...] = (
    VideoAlertRule(
        "disk-almost-full",
        "disk",
        85.0,
        600,
        "warning",
        "Old releases and database dumps are the usual suspects.",
    ),
    VideoAlertRule("cpu-saturated", "cpu", 90.0, 300, "warning", "A build or a runaway worker."),
    VideoAlertRule(
        "shop-server-errors",
        "app_5xx",
        5.0,
        300,
        "failed",
        "Brumaria's checkout: call Iker or Sara.",
        app_domain="shop.brumaria.es",
        nodes=("fra-1",),
    ),
)

#: The rule that is firing when the video starts, where, at what reading, and for how
#: many minutes already.
VIDEO_FIRING: dict[str, tuple[str, float, int]] = {"ams-3": ("disk-almost-full", 91.0, 9)}

#: When the nightly backups run, and the two places each copy goes besides this server.
BACKUP_TIME = "03:00"
BACKUP_DESTINATIONS = ("offsite-sftp", "vault-r2")


#: The secret the shop's deploy-on-push webhook is signed with (the sandbox only): Iker's push
#: in the video is a signed delivery, as the forge would send it.
VIDEO_WEBHOOK_SECRET = "kelmoor-video-webhook-secret"

#: The application whose pushes deploy it in the video.
PUSH_DEPLOYED = "shop.brumaria.es"


def backed_up(node: str) -> list[str]:
    """
    Args:
        node: A server of :data:`VIDEO_NODES`.

    Returns:
        The applications backed up every night on it: every one with data of its own (a
        process, a database, uploads), so not the static sites.
    """
    return [app.domain for app in VIDEO_NODES.get(node, ()) if app.app_type != "static"]


def video_rules_for(node: str) -> tuple[VideoAlertRule, ...]:
    """
    Args:
        node: A server of :data:`VIDEO_NODES`.

    Returns:
        The alert rules that server has.
    """
    return tuple(rule for rule in VIDEO_ALERT_RULES if rule.nodes is None or node in rule.nodes)


def seed_video_extras(store: NoustStore, node: str, state: ShowcaseState) -> None:
    """
    Seed what the "video" profile adds to a server's store: its databases, what was
    installed from the catalog, its alert rules and the alert that is firing.

    Args:
        store: The store.
        node: Which server of :data:`VIDEO_NODES`.
        state: What :func:`tests.support.showcase.seed_showcase_state` created; gains the
            database names.
    """
    import time as time_module
    from datetime import datetime

    from noust.core.alerts.model import AlertRule
    from noust.core.alerts.rules import FIRING, AlertRules, AlertState
    from noust.recipes import get_recipe

    for name, engine, port, owner, _size in VIDEO_DATABASES.get(node, ()):
        store.create_database(
            Database(
                app_id=state.app_ids[owner] if owner else None,
                name=name,
                engine=engine,
                port=port,
                username=None if engine == DatabaseEngine.REDIS.value else name,
            )
        )
        state.database_names.append(name)

    now = datetime.now().isoformat()
    for app in VIDEO_NODES.get(node, ()):
        if app.recipe is None:
            continue
        recipe = get_recipe(app.recipe)
        with store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO app_recipes (app_id, recipe, kind, version, revision, installed_at, "
                "updated_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
                (
                    state.app_ids[app.domain],
                    recipe.name,
                    recipe.kind,
                    recipe.version or "1",
                    now,
                    now,
                ),
            )

    if PUSH_DEPLOYED in state.app_ids:
        store.set_webhook_secret(PUSH_DEPLOYED, VIDEO_WEBHOOK_SECRET)

    rules = AlertRules(store)
    saved = {}
    for rule in video_rules_for(node):
        saved[rule.name] = rules.save(
            AlertRule(
                name=rule.name,
                metric=rule.metric,
                comparator=">",
                threshold=rule.threshold,
                app_domain=rule.app_domain,
                duration_s=rule.duration_s,
                severity=rule.severity,
                note=rule.note,
            ),
            actor="marta",
        )
    firing = VIDEO_FIRING.get(node)
    if firing is not None:
        name, value, minutes = firing
        since = time_module.time() - minutes * 60
        rule_id = saved[name].id
        assert rule_id is not None
        rules.put_state(
            AlertState(
                rule_id=rule_id,
                subject="",
                state=FIRING,
                since=since,
                last_value=value,
                announced=True,
                announced_at=since,
            )
        )


@dataclass(frozen=True)
class VideoCredentials:
    """
    How the operator of the video signs in.

    Attributes:
        username: Her sign-in name.
        password: Her password.
        totp_secret: Her authenticator's secret, when one was enrolled.
    """

    username: str
    password: str
    totp_secret: str | None


def seed_video_people(store: NoustStore, *, second_factor: bool = True) -> VideoCredentials:
    """
    Give every one of :data:`VIDEO_PEOPLE` an account on the central, through the real
    account manager, and enrol the operator's authenticator the way her first sign-in would.

    Args:
        store: The central's store.
        second_factor: Enrol the operator's authenticator, which needs the key that seals
            it beside the store; a test of the accounts alone can leave it out.

    Returns:
        How the operator signs in.
    """
    from noust.core import totp
    from noust.core.accounts.manager import AccountManager

    accounts = AccountManager(store)
    operator_id: int | None = None
    for person in VIDEO_PEOPLE:
        account = accounts.create(
            person.username,
            person.role,
            password=VIDEO_PASSWORD,
            display_name=person.name,
            email=person.email,
            created_by="seed",
        )
        if person.username == OPERATOR:
            operator_id = account.id
    secret: str | None = None
    if second_factor and operator_id is not None:
        secret = accounts.begin_totp(operator_id)
        accounts.confirm_totp(operator_id, totp.totp_now(secret))
    return VideoCredentials(OPERATOR, VIDEO_PASSWORD, secret)
