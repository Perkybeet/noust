# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Console 2.3: recipes, other platforms' configuration, export and import, and the showcase's
metrics."""

from __future__ import annotations

import json
import tarfile
import time
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from .app_tabs import _tabs_metrics, _tabs_register
from .constants import (
    CATALOG_DOCKERFILE_APP,
    CATALOG_IMAGE_APP,
    CATALOG_READER_APP,
    CATALOG_RETIRED_APP,
    CATALOG_VAULT_APP,
)
from .domains import WIZARD_SOURCES
from .machine import Unit
from .sandbox import Sandbox

# ---------------------------------------------------------------------------
# 2.3: recipes, other platforms' configuration, export and import
# ---------------------------------------------------------------------------

#: A static site set up with everything an export carries: an alias, a cron
#: job, variables with a secret among them, a health check and a release
#: retention. Deployed at seed time by the real deploy, so its export is one
#: an import can deploy again in the sandbox.
EXPORT_APP = "lanzamiento.example.org"

#: Its alias, which an import onto another domain renames with it.
EXPORT_ALIAS = "www.lanzamiento.example.org"

#: Its cron job, named after it.
EXPORT_CRON = "lanzamiento-sitemap"

#: Its variables: the first two are shown, the secret one is hidden in an export.
EXPORT_ENV = {
    "SITE_NAME": "Lanzamiento",
    "ANALYTICS_ID": "UA-000000-2",
    "NEWSLETTER_API_KEY": "nl_live_8f2c61d0b7a94e3f",
}

#: The database it is linked to: named by an export, never recreated by an import.
EXPORT_DATABASE = "lanzamiento_newsletter"

#: The wizard's source whose repository carries a Railway configuration.
WIZARD_RAILWAY_SOURCE = "railway-api"

#: Where the recipes' archives come from, answered from files the seed builds:
#: the dev server never reaches the network.
_RECIPE_ARCHIVES = ("https://wordpress.org/latest.tar.gz",)


def _wordpress_archive() -> bytes:
    """
    Build a small stand-in for wordpress.org's latest.tar.gz.

    The layout WordPress ships (one ``wordpress/`` directory, ``index.php``,
    ``wp-content/``, the files the recipe refuses to serve), so the recipe's
    shared paths and refused paths have something to act on.

    Returns:
        The gzipped tarball.
    """
    import io

    files = {
        "wordpress/index.php": "<?php\ndefine('WP_USE_THEMES', true);\nrequire __DIR__ . '/wp-blog-header.php';\n",
        "wordpress/wp-blog-header.php": "<?php\n// WordPress, as the console server models it.\n",
        "wordpress/wp-config-sample.php": "<?php\ndefine('DB_NAME', 'database_name_here');\n",
        "wordpress/readme.html": "<!DOCTYPE html>\n<title>WordPress</title>\n",
        "wordpress/license.txt": "WordPress - Web publishing software\n",
        "wordpress/wp-admin/install.php": "<?php\n// The installer.\n",
        "wordpress/wp-content/index.php": "<?php\n// Silence is golden.\n",
        "wordpress/wp-content/plugins/index.php": "<?php\n// Silence is golden.\n",
        "wordpress/wp-content/themes/index.php": "<?php\n// Silence is golden.\n",
    }
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, text in files.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            info.mtime = int(time.time())
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _recipes_offline() -> None:
    """
    Answer the recipes' downloads from memory, and refuse every other one.

    Patched where the source manager opens a URL, below the checksum check:
    the archive is verified against the published SHA-1 exactly as on a real
    server, only both files come from here instead of wordpress.org.
    """
    import hashlib
    import io
    from urllib.error import URLError

    import noust.managers.source_manager as source_module

    archive = _wordpress_archive()
    answers = {
        _RECIPE_ARCHIVES[0]: archive,
        f"{_RECIPE_ARCHIVES[0]}.sha1": hashlib.sha1(archive, usedforsecurity=False)
        .hexdigest()
        .encode("ascii"),
    }

    def open_url(url: str, timeout: int = 0) -> Any:
        data = answers.get(url)
        if data is None:
            raise URLError(f"the console server does not reach the network ({url})")
        return io.BytesIO(data)

    source_module._open_url = open_url  # type: ignore[assignment]


def _recipes_php_fpm(sandbox: Sandbox) -> None:
    """
    Model PHP 8.3's FPM, so a PHP recipe deploys and its pool is written.

    The pool directory and the binary exist in the sandbox, where the
    deployer is told to look for them; the binary's configuration test and
    the reload go through the runner, which answers them. The pool is asked
    over FastCGI by the health gate, and answers the way WordPress does before
    it is installed: a redirect to its installer.

    Args:
        sandbox: The sandbox.
    """
    import functools

    import noust.deployers.helpers.php_fpm as fpm_helpers
    import noust.deployers.php_fpm as php_module

    (sandbox.etc / "php" / "8.3" / "fpm" / "pool.d").mkdir(parents=True, exist_ok=True)
    binary = sandbox.root / "usr" / "sbin" / "php-fpm8.3"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("", encoding="utf-8")
    binary.chmod(0o755)
    php_module.FPM_ROOT = sandbox.root  # type: ignore[misc]
    # The dependency report asks without a root: the same machine. A function and not a partial
    # of the root, because the deployer also passes the root (and a pinned version) itself.
    original_find = fpm_helpers.find_fpm

    def find_in_sandbox(root: Path | None = None, version: str | None = None) -> Any:
        return original_find(sandbox.root if root is None else root, version)

    fpm_helpers.find_fpm = find_in_sandbox  # type: ignore[assignment]

    def installer(_socket: Path, _params: Mapping[str, str], **_kwargs: Any) -> Any:
        return fpm_helpers.FastCgiResponse(
            status=302, headers={"location": "/wp-admin/install.php"}, stderr=""
        )

    php_module.fastcgi_probe = functools.partial(  # type: ignore[assignment]
        fpm_helpers.fastcgi_probe, requester=installer
    )
    # The machine snapshot sorts a PHP app by whether its pool's socket accepts a
    # connection; the modelled FPM runs every pool whose file is in place.
    if hasattr(php_module, "socket_accepts"):
        php_module.socket_accepts = lambda _path, *args, **kwargs: True


def seed_showcase_php_fpm_pools(sandbox: Sandbox, store: Any) -> None:
    """
    Write a pool file for each showcase PHP-FPM application (``--showcase`` only).

    ``_recipes_php_fpm`` above only models the shared FPM installation, so the wizard's own
    WordPress recipe deploy can write its pool for real and be asked over FastCGI - it never
    creates a pool for an application that was seeded directly into the store rather than
    deployed. ``pool_serving()`` (``noust/deployers/php_fpm.py``) reads the pool file's mere
    existence, cheaply, on every apps list request, so without one here a showcase PHP-FPM
    application would read as failed despite its stored status.

    Args:
        sandbox: The sandbox, with ``_recipes_php_fpm`` already run.
        store: The seeded store.
    """
    from noust.core.utils import domain_to_app_name

    pool_dir = sandbox.etc / "php" / "8.3" / "fpm" / "pool.d"
    for app in store.list_apps():
        if app.app_type != "php-fpm":
            continue
        name = domain_to_app_name(app.domain)
        (pool_dir / f"noust-{name}.conf").write_text(
            f"[{name}]\n"
            "user = www-data\n"
            "group = www-data\n"
            f"listen = /run/php/noust-{name}.sock\n"
            "pm = dynamic\n"
            "pm.max_children = 5\n",
            encoding="utf-8",
        )


def seed_showcase_machine_metrics() -> None:
    """
    Write a month of machine-wide history, so the Overview's charts show a curve
    (``--showcase`` only).

    The real :class:`~noust.web.metrics_collector.MetricsCollector` is a background thread
    that samples this process every couple of seconds; a screenshot taken moments after the
    server starts has nothing to chart yet, and every range - "Last hour" through "30d" -
    would read "Collecting samples." Written directly into the same store, over the same
    metric names ``MetricsCollector._system_pairs`` itself records, the way
    :func:`_tabs_metrics` writes an application's own history: denser the more recent, a
    daily wave, noise, and a couple of smooth spikes (a build, a burst of traffic) so the
    chart reads as a real machine's rather than a flat line.

    This only backfills the past: the collector's own ticks, from the moment it starts,
    are real ``psutil`` reads and would read as a machine noisily unlike this one, or
    (every seeded node being the same sandboxed host) unlike each other -
    :func:`use_fixed_machine_stats`, called once ``serve()`` knows which node this is, is
    what keeps those consistent with the history seeded here.
    """
    import math

    from noust.web.metrics_collector import get_metrics_store

    store = get_metrics_store()
    now = int(time.time())
    stamps = [
        *range(now - 30 * 86_400, now - 86_400, 1_800),
        *range(now - 86_400, now - 3_600, 120),
        *range(now - 3_600, now, 10),
    ]
    mem_total = 16 * 1024**3
    disk_total = 480 * 1024**3
    # Smooth bumps (a Gaussian each), not a step: one a few minutes ago, still visible on
    # "Last hour", and two further back that only "24h" and wider ranges will show.
    spike_minutes_ago = (4.0, 55.0, 340.0)
    for stamp in stamps:
        age_days = (now - stamp) / 86_400
        minutes_ago = (now - stamp) / 60.0
        daily = math.sin((stamp % 86_400) / 86_400 * 2 * math.pi - math.pi / 2)
        wobble = math.sin(stamp / 977.0) * 0.6 + math.sin(stamp / 331.0) * 0.4
        spike = max(
            math.exp(-(((minutes_ago - centre) / 2.4) ** 2)) for centre in spike_minutes_ago
        )
        cpu = max(1.0, min(96.0, 13.0 + 9.0 * daily + 4.0 * wobble + 44.0 * spike))
        mem_used = mem_total * max(0.12, min(0.85, 0.29 + 0.05 * daily + 0.02 * wobble))
        disk_used = disk_total * max(0.10, min(0.60, 0.235 + 0.0015 * (30 - age_days)))
        net_rx = max(150.0, 4_200 + 2_600 * wobble + 21_000 * spike)
        net_tx = max(90.0, 1_800 + 900 * wobble + 6_500 * spike)
        store.record_many(
            [
                ("cpu.percent", cpu),
                ("mem.used_bytes", mem_used),
                ("mem.total_bytes", float(mem_total)),
                ("swap.used_bytes", mem_total * 0.015),
                ("swap.total_bytes", float(2 * 1024**3)),
                ("disk.used_bytes", disk_used),
                ("disk.total_bytes", float(disk_total)),
                ("net.rx_bytes_s", net_rx),
                ("net.tx_bytes_s", net_tx),
                ("load.1m", max(0.05, 0.5 + 0.3 * wobble)),
            ],
            ts=stamp,
        )
    store.consolidate(now=now)


def seed_showcase_app_metrics(store: Any) -> None:
    """
    Write a month of CPU and memory history for each showcase application that has a real
    unit (``--showcase`` only).

    Reuses :func:`_tabs_metrics`, which already writes over the metric names an
    application's Metrics tab reads. A static site, a PHP-FPM pool and a Docker Compose
    stack are skipped: none of them is sampled by cgroup on a real machine either (see
    ``noust/web/metrics_collector.py``'s module docstring), so seeding history for them
    would chart something that was never measured.

    Args:
        store: The seeded store.
    """
    sampled_types = {"nextjs", "nodejs", "python"}
    base_mb = {"nextjs": 180.0, "nodejs": 120.0, "python": 150.0}
    for app in store.list_apps():
        if app.app_type not in sampled_types:
            continue
        starts = [
            parsed
            for record in store.list_deployments(app.domain)
            if record.started_at and (parsed := _parse_iso(record.started_at)) is not None
        ]
        if not starts:
            continue
        _tabs_metrics(app.domain, starts, base_mb=base_mb[app.app_type])


def _parse_iso(value: str) -> datetime | None:
    """
    Args:
        value: An ISO 8601 timestamp as the store writes one.

    Returns:
        The parsed moment, or None for a value :meth:`datetime.fromisoformat` refuses.
    """
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _platform_sources(sandbox: Sandbox) -> None:
    """
    Write the wizard's source that was configured for Railway.

    A Node API whose ``railway.toml`` names a health check path and timeout,
    a build and a start command: the inspection proposes them, and the
    review carries the health check into the deploy request.

    Args:
        sandbox: The sandbox.
    """
    root = sandbox.var / "www" / "src" / WIZARD_RAILWAY_SOURCE
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": "railway-api",
                "version": "0.9.0",
                "private": True,
                "scripts": {"build": "tsc -p .", "start": "node dist/server.js"},
                "dependencies": {"fastify": "5.2.1"},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "package-lock.json").write_text(
        '{\n  "name": "railway-api",\n  "lockfileVersion": 3,\n  "requires": true,\n'
        '  "packages": {}\n}\n',
        encoding="utf-8",
    )
    (root / "railway.toml").write_text(
        "[build]\n"
        'builder = "NIXPACKS"\n'
        'buildCommand = "npm run build"\n\n'
        "[deploy]\n"
        'startCommand = "npm start"\n'
        'healthcheckPath = "/healthz"\n'
        "healthcheckTimeout = 60\n"
        'restartPolicyType = "ON_FAILURE"\n',
        encoding="utf-8",
    )
    (root / "server.js").write_text(
        "require('http').createServer((q, s) => s.end('ok')).listen(process.env.PORT);\n",
        encoding="utf-8",
    )


def seed_exportable_app(sandbox: Sandbox) -> None:
    """
    Deploy the application the export and import screens work with.

    A static site from the wizard's ``landing`` source, deployed by the real
    job on releases, then given an alias, a cron job, a secret variable and a
    release retention through the same functions the console's own pages
    call. It is also linked to a database, which an export names and an
    import cannot recreate: the import's "not applied" list has an entry.
    A static site has no health check of its own (the web server answers for
    it), so an import of it applies none.

    Args:
        sandbox: The sandbox.
    """
    from noust.core.store import Database, DomainKind, get_store
    from noust.deployers import domains as domain_changes
    from noust.deployers.helpers.app_env import write_app_env
    from noust.deployers.lifecycle import set_release_retention
    from noust.managers.cron_manager import CronJob, CronManager
    from noust.web.jobs import Job, JobContext, JobType, deploy_app_job

    source = sandbox.var / "www" / "src" / WIZARD_SOURCES[1]
    # Run as the job function it is, outside the job manager: seeding must not
    # leave a job in the history the Activity page and its tests count.
    job = Job(id="seed2300", type=JobType.DEPLOY, name=f"Deploy {EXPORT_APP}", description="")
    deploy_app_job(
        job_context=JobContext(job, lambda _job: None),
        domain=EXPORT_APP,
        source=str(source),
        app_type="static",
        ssl=False,
        layout="releases",
    )
    domain_changes.add_domain(EXPORT_APP, EXPORT_ALIAS, DomainKind.ALIAS.value, issue_cert=False)
    set_release_retention(EXPORT_APP, 3)
    CronManager().create_job(
        CronJob(
            name=EXPORT_CRON,
            command="/usr/bin/curl -fsS http://127.0.0.1/sitemap.xml",
            schedule="*-*-* 04:15:00",
            app_domain=EXPORT_APP,
        )
    )
    store = get_store()
    app = store.get_app(EXPORT_APP)
    if app is not None:
        # Written the way the Environment tab writes it: a static deploy takes
        # no variables (nothing runs to read them), but a build-time .env is
        # an operator's to keep, and an export carries it.
        write_app_env(app, EXPORT_ENV)
        store.set_env_secret_marks(EXPORT_APP, {"NEWSLETTER_API_KEY": True})
        store.create_database(
            Database(
                app_id=app.id,
                name=EXPORT_DATABASE,
                engine="postgresql",
                port=5432,
                username=EXPORT_DATABASE,
            )
        )


def seed_fleet_node_app(
    sandbox: Sandbox,
    units: dict[str, Unit],
    ports: dict[str, int],
    domains: dict[str, str],
    domain: str,
) -> None:
    """
    Seed one running application that exists on this server alone.

    Read from :data:`FLEET_NODE_APP_ENV`, set only by the Playwright fixture that
    starts a fleet's node (panel/e2e/fleet.spec.ts): the central's own seed
    (:func:`seed_machine`) never has this domain, so once the console switches to
    this server, seeing it is proof the page just read this server's API, not a
    cache of the one the operator was on before.

    Args:
        sandbox: The sandbox.
        units: The modelled units, mutated in place.
        ports: Each unit's port, mutated in place.
        domains: Each unit's domain, mutated in place.
        domain: The application's domain.
    """
    from noust.core.store import App, get_store
    from noust.core.utils import domain_to_app_name

    root = sandbox.apps_dir / domain_to_app_name(domain)
    root.mkdir(parents=True, exist_ok=True)
    app = App(
        domain=domain,
        app_type="nodejs",
        source=f"https://github.com/example-org/{domain_to_app_name(domain)}.git",
        branch="main",
        port=41990,
        app_path=str(root),
        status="running",
        ssl_enabled=True,
        layout="inplace",
    )
    _tabs_register(sandbox, get_store(), units, ports, domains, app, working_directory=root)


def seed_catalog_apps(
    sandbox: Sandbox, units: dict[str, Unit], ports: dict[str, int], domains: dict[str, str]
) -> None:
    """
    Seed the applications the catalogue's pages are opened on.

    Two container applications (one built from a Dockerfile with a build secret, one pulled
    from a registry with an account), and three applications recorded as installed from a
    recipe: Vaultwarden one version behind the catalogue, FreshRSS at the catalogue's version,
    and one whose recipe is no longer there. Their secrets go to the sandbox's secret store,
    where a real deployment puts them, so the pages that must never show one are shown a real
    one to hide.

    Args:
        sandbox: The sandbox.
        units: The modelled units, mutated in place.
        ports: Each unit's port, mutated in place.
        domains: Each unit's domain, mutated in place.
    """
    from datetime import datetime as _datetime

    from noust.core.container_store import ContainerStore
    from noust.core.store import App, get_store
    from noust.core.utils import domain_to_app_name
    from noust.deployers.helpers.container_settings import settings_for_new_app
    from noust.recipes import get_recipe

    store = get_store()

    def register(domain: str, app_type: str, source: str, port: int, layout: str) -> None:
        root = sandbox.apps_dir / domain_to_app_name(domain)
        root.mkdir(parents=True, exist_ok=True)
        app = App(
            domain=domain,
            app_type=app_type,
            source=source,
            branch="main" if source.startswith("https://") else None,
            port=port,
            app_path=str(root),
            status="running",
            ssl_enabled=True,
            layout=layout,
        )
        _tabs_register(sandbox, store, units, ports, domains, app, working_directory=root)

    register(
        CATALOG_DOCKERFILE_APP,
        "container",
        "https://github.com/example-org/crate.git",
        41981,
        "releases",
    )
    ContainerStore(store).save(
        CATALOG_DOCKERFILE_APP,
        settings_for_new_app(
            CATALOG_DOCKERFILE_APP,
            {
                "method": "dockerfile",
                "dockerfile": "docker/Dockerfile.prod",
                "context": "services/api",
                "target": "runtime",
                "build_args": {"NODE_ENV": "production"},
                "container_port": 3000,
                "volumes": ["/data"],
                "build_secret_values": {
                    "npmrc": "//registry.npmjs.org/:_authToken=seeded-never-shown"
                },
            },
        ),
        actor="seed",
    )
    register(
        CATALOG_IMAGE_APP, "container", "ghcr.io/example-org/registry-demo:1.4", 41982, "releases"
    )
    ContainerStore(store).save(
        CATALOG_IMAGE_APP,
        settings_for_new_app(
            CATALOG_IMAGE_APP,
            {
                "method": "image",
                "image": "ghcr.io/example-org/registry-demo:1.4",
                "container_port": 8080,
                "registry_username": "ci",
                "registry_password": "seeded-registry-token-never-shown",
            },
        ),
        actor="seed",
    )

    now = _datetime.now().isoformat()
    installed = [
        (CATALOG_VAULT_APP, "vaultwarden", get_recipe("vaultwarden").kind, "1.36.0", 41983),
        (
            CATALOG_READER_APP,
            "freshrss",
            get_recipe("freshrss").kind,
            get_recipe("freshrss").version or "1",
            41984,
        ),
        (CATALOG_RETIRED_APP, "retired-recipe", "compose", "0.9.0", 41985),
    ]
    for domain, recipe, kind, version, port in installed:
        register(domain, "docker-compose", "", port, "inplace")
        row = store.get_app(domain)
        if row is None:
            continue
        with store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO app_recipes (app_id, recipe, kind, version, revision, installed_at, "
                "updated_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
                (row.id, recipe, kind, version, now, now),
            )


def seed_release_23(sandbox: Sandbox) -> None:
    """
    Seed what the 2.3 screens need beyond the machine seeded before them.

    Recipes deploy without the network (their archives are answered from
    here, PHP-FPM is modelled) and the wizard has a repository configured for
    another platform. The application an export is taken from is deployed
    later, by :func:`seed_exportable_app`, once the runner answers for the
    seeded units (a deploy's pre-flight asks whether nginx runs).

    Args:
        sandbox: The sandbox.
    """
    _recipes_offline()
    _recipes_php_fpm(sandbox)
    _platform_sources(sandbox)
