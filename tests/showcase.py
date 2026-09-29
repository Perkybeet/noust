"""
An invented, believable small agency's server, for the documentation screenshots.

``tests.panel_factory`` seeds the machine the E2E suite runs its assertions against, and
``DEFAULT_DOMAINS`` (example.com and friends) is load-bearing there: a lot of
``panel/e2e/*.spec.ts`` names those domains directly, so it cannot change. The documentation
screenshots in ``docs/assets/console/`` are a different audience - a published, marketing-facing
picture of the console - and the owner does not want a real project, company, domain, GitHub
account or person recognisable in them, nor the RFC 2606 example domains that make a screenshot
read as a fixture rather than a real server. This module is that other dataset: a fictional web
agency ("Kelmoor") running a small fleet for different invented clients - "fra-1", its main
server, with eight applications of different kinds; "ams-3" and "lon-2", each smaller and in
its own state (see :data:`NODES`) - with a deployment history, domains, databases and the rest
of what :func:`seed_showcase_state` builds for whichever one ``--hostname`` names.
``scripts/console_server.py --showcase`` seeds a sandboxed machine from it, exactly the way it
seeds one from :func:`tests.panel_factory.seed_console_state` for every other purpose.

Every invented name below was checked not to collide with something real before it was used:

- domains: ``curl -s -H 'accept: application/dns-json'
  'https://cloudflare-dns.com/dns-query?name=<domain>&type=NS'`` against the registrable
  domain, kept only when it answers ``"Status": 3`` (NXDOMAIN). Checked on 2026-09-29:
  ``wrenfield.io``, ``kestrelworks.io``, ``corvane.io``, ``nortewave.com``, ``fennwick.io``,
  ``brumaria.es``, ``kelmoor.dev``, ``thornfield.io`` and ``palenbrook.io`` all answered
  NXDOMAIN; a first choice, ``verdala.com``, did not and was dropped.
  ``staging.wrenfield.io``, ``staging.kestrelworks.io`` and ``news.nortewave.com`` are
  subdomains of already-checked domains and need no separate check: nothing resolves
  under a domain that itself does not exist.
- the GitHub organisation: ``gh api users/<org>`` (equivalently, a plain
  ``GET https://api.github.com/users/<org>``) returning 404. Checked on 2026-09-29:
  ``kelmoor-labs`` answered 404 (``kelmoor`` alone did not, and was dropped).
- the fleet node names (``fra-1``, ``ams-3``, ``lon-2``): infrastructure labels, not
  domains or accounts, so they need no check of their own.
- the SSH addresses Settings > Servers shows for "ams-3" and "lon-2"
  (:data:`NODE_SSH_HOSTS`): 203.0.113.0/24 and 198.51.100.0/24, RFC 5737 documentation
  ranges never assigned to anyone, so they need no reachability check either.
- people: plainly invented, common first and last names, no public figure.

A future edit adding a domain, GitHub account or person here should check it the same way
before using it, and extend the note above.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from noust.core.fs import get_fs
from noust.core.store import (
    App,
    AppStatus,
    Database,
    DatabaseEngine,
    DomainKind,
    NoustStore,
    Service,
    Site,
)
from noust.core.utils import domain_to_app_name

#: The agency running every application below, and its own domain (see the module docstring
#: for the NXDOMAIN check): used for its operators' invented email addresses and nowhere else.
AGENCY = "Kelmoor"
AGENCY_DOMAIN = "kelmoor.dev"

#: The GitHub organisation every seeded source belongs to (see the module docstring for the
#: 404 check).
GITHUB_ORG = "kelmoor-labs"

#: Invented people who commit to the seeded repositories: name and the agency address they
#: commit under. Common, plainly invented names; nobody real.
_PEOPLE: tuple[tuple[str, str], ...] = (
    ("Marta Solano", "marta"),
    ("Iker Beitia", "iker"),
    ("Noa Ferreira", "noa"),
    ("Dana Kessler", "dana"),
    ("Owen Whitcombe", "owen"),
    ("Sara Lindqvist", "sara"),
    ("Julen Anitua", "julen"),
    ("Marc Bofarull", "marc"),
)

_AUTHORS: dict[str, tuple[str, str]] = {
    name: (name, f"{handle}@{AGENCY_DOMAIN}") for name, handle in _PEOPLE
}


def _author(name: str) -> tuple[str, str]:
    """Args: name: A key of :data:`_AUTHORS`. Returns: Its (name, email)."""
    return _AUTHORS[name]


@dataclass(frozen=True)
class ShowcaseCommit:
    """One deployment in an invented application's history."""

    sha: str
    message: str
    author: str
    status: str = "success"
    #: Verbatim build failure, for a commit whose ``status`` is "failed".
    error: str | None = None


@dataclass(frozen=True)
class ShowcaseApp:
    """
    One invented application of the showcase machine.

    Attributes:
        domain: Its primary domain (see the module docstring for the NXDOMAIN check).
        app_type: A :class:`~noust.core.store.AppType` value.
        repo: The repository under :data:`GITHUB_ORG` it deploys from.
        branch: The branch it deploys.
        port: The port its own process listens on, None for a static site or
            a type whose health check does not use one.
        status: An :class:`~noust.core.store.AppStatus` value.
        is_static: Whether it is a static site (no systemd unit).
        commits: Its deployment history, oldest first.
        deployed_days_ago: When each of ``commits`` started, oldest first, matching it
            one for one: fractional days ago, so the last entry can be a few minutes
            (a deploy still running) or hours (one that just failed).
        alias: An extra name it also answers on, added as a
            :class:`~noust.core.store.DomainKind.ALIAS`; None for an
            application with only its primary domain.
        has_cert: Whether its site carries a certificate.
        cert_expires_soon: Whether this application's certificate should lead its node's
            certificate list, which console_server.py's fake certbot answers in expiry
            order (offset 0 is the shortest validity) - at most one per node.
    """

    domain: str
    app_type: str
    repo: str
    branch: str
    port: int | None
    status: str
    is_static: bool
    commits: tuple[ShowcaseCommit, ...]
    deployed_days_ago: tuple[float, ...]
    alias: str | None = None
    has_cert: bool = True
    cert_expires_soon: bool = False


#: The eight applications on "fra-1", the agency's own main server, in the order the
#: Applications list shows them: mixed types, mostly running, one deploying, one failed.
#: Ports are spread out the way a real machine's would be, never colliding.
FRA_APPS: tuple[ShowcaseApp, ...] = (
    ShowcaseApp(
        domain="wrenfield.io",
        app_type="nextjs",
        repo="wrenfield-site",
        branch="main",
        port=3000,
        status=AppStatus.RUNNING.value,
        is_static=False,
        alias="www.wrenfield.io",
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
        deployed_days_ago=(21.0, 14.0, 7.0, 1.0),
    ),
    ShowcaseApp(
        domain="app.wrenfield.io",
        app_type="nextjs",
        repo="wrenfield-portal",
        branch="main",
        port=3001,
        # Its last release still serves while the deploy below is in progress: an
        # application's *resolved* state (noust.core.app_state.resolve_state) always
        # comes from systemd and a live port probe, never the stored column, so
        # AppStatus.DEPLOYING would never actually surface here - only the deployment
        # record's own "running" status does, which is what the console really shows
        # while a deploy is in flight (its row, the Overview's recent deployments).
        status=AppStatus.RUNNING.value,
        is_static=False,
        has_cert=False,
        commits=(
            ShowcaseCommit(
                "7c1d9a4", "Wire the billing page to the new invoices endpoint", "Noa Ferreira"
            ),
            ShowcaseCommit(
                "e08f61b",
                "Add multi-seat invites to the onboarding flow",
                "Dana Kessler",
                status="running",
            ),
        ),
        # The last one started three minutes ago and is still going.
        deployed_days_ago=(5.0, 3.0 / (24 * 60)),
    ),
    ShowcaseApp(
        domain="api.kestrelworks.io",
        app_type="nodejs",
        repo="kestrelworks-api",
        branch="main",
        port=4000,
        status=AppStatus.RUNNING.value,
        is_static=False,
        commits=(
            ShowcaseCommit("b4a0e27", "Add rate limiting to the public API", "Julen Anitua"),
            ShowcaseCommit("0d6c3f9", "Fix pagination cursor for large exports", "Sara Lindqvist"),
            ShowcaseCommit("6f2b8e5", "Upgrade Fastify to 5.x", "Julen Anitua"),
        ),
        deployed_days_ago=(18.0, 9.0, 3.0),
    ),
    ShowcaseApp(
        domain="status.kestrelworks.io",
        app_type="static",
        repo="kestrelworks-status",
        branch="main",
        port=None,
        status=AppStatus.RUNNING.value,
        is_static=True,
        commits=(
            ShowcaseCommit(
                "3a9e1c0", "Add the incidents timeline to the status page", "Marc Bofarull"
            ),
        ),
        deployed_days_ago=(10.0,),
    ),
    ShowcaseApp(
        domain="panel.corvane.io",
        app_type="python",
        repo="corvane-panel",
        branch="main",
        port=8000,
        status=AppStatus.RUNNING.value,
        is_static=False,
        has_cert=False,
        commits=(
            ShowcaseCommit("c7d2a68", "Ship the new billing dashboard widgets", "Dana Kessler"),
            ShowcaseCommit(
                "1e5f4b3", "Fix timezone handling in the reports export", "Noa Ferreira"
            ),
        ),
        deployed_days_ago=(12.0, 4.0),
    ),
    ShowcaseApp(
        domain="blog.nortewave.com",
        app_type="php-fpm",
        repo="nortewave-blog",
        branch="main",
        port=None,
        status=AppStatus.RUNNING.value,
        # A PHP-FPM application is stored as static: BaseDeployer.build_app sets
        # is_static from whether the deployer has a start command, and php_fpm.py's
        # get_start_command() returns "" (is_php_fpm(app) is what the console actually
        # asks; True app_type is enough for that, but this keeps the row the same shape
        # a real php-fpm deploy leaves).
        is_static=True,
        # A meaningful alternate name, not "www." on a subdomain (real sites only alias
        # www on the apex): a subdomain of the already-checked nortewave.com, so it needs
        # no NXDOMAIN check of its own (see the module docstring).
        alias="news.nortewave.com",
        cert_expires_soon=True,
        commits=(
            ShowcaseCommit("4b8c0f1", "Update the WordPress core and plugins", "Sara Lindqvist"),
            ShowcaseCommit("8a3d7e2", "Apply the new editorial theme", "Marta Solano"),
        ),
        deployed_days_ago=(20.0, 6.0),
    ),
    ShowcaseApp(
        domain="queue.fennwick.io",
        app_type="docker-compose",
        repo="fennwick-queue",
        branch="main",
        # Its own port, not None: a None service port would fall back to the
        # generic 3000 in console_server.py's unit model (service.port or 3000),
        # colliding with wrenfield.io's real one.
        port=9090,
        status=AppStatus.RUNNING.value,
        is_static=False,
        has_cert=False,
        commits=(
            ShowcaseCommit("f01a5c8", "Add the retry queue for failed webhooks", "Owen Whitcombe"),
            ShowcaseCommit("2c9b6d4", "Pin the worker image to 1.8.2", "Julen Anitua"),
        ),
        deployed_days_ago=(15.0, 5.0),
    ),
    ShowcaseApp(
        domain="shop.brumaria.es",
        app_type="nextjs",
        repo="brumaria-shop",
        branch="main",
        port=3100,
        status=AppStatus.FAILED.value,
        is_static=False,
        alias="tienda.brumaria.es",
        commits=(
            ShowcaseCommit("9d4e7a1", "Add gift card redemption to checkout", "Marc Bofarull"),
            ShowcaseCommit(
                "6b1f2c8", "Migrate the cart service to the new pricing API", "Sara Lindqvist"
            ),
            ShowcaseCommit(
                "d3a8e05",
                "Bump the Node runtime to 20 LTS",
                "Iker Beitia",
                status="failed",
                error=(
                    "npm ERR! code ELIFECYCLE\n"
                    "npm ERR! errno 1\n"
                    "npm ERR! shop@2.3.1 build: `next build`\n"
                    "npm ERR! Exit status 1"
                ),
            ),
        ),
        # The last one failed six hours ago: recent enough that the operator has not
        # rolled it back yet, which is what the diagnose page's demo is about.
        deployed_days_ago=(16.0, 8.0, 0.25),
    ),
)

#: "ams-3": a couple of client sites, both healthy, one certificate expiring soon - a
#: smaller, calmer server than "fra-1", the way a second region actually would be.
AMS_APPS: tuple[ShowcaseApp, ...] = (
    ShowcaseApp(
        domain="thornfield.io",
        app_type="nextjs",
        repo="thornfield-site",
        branch="main",
        port=3000,
        status=AppStatus.RUNNING.value,
        is_static=False,
        alias="www.thornfield.io",
        cert_expires_soon=True,
        commits=(
            ShowcaseCommit("d2a4c19", "Refresh the pricing page copy", "Noa Ferreira"),
            ShowcaseCommit("9f0b3e6", "Add the newsletter signup", "Marta Solano"),
        ),
        deployed_days_ago=(11.0, 2.0),
    ),
    ShowcaseApp(
        domain="palenbrook.io",
        app_type="nodejs",
        repo="palenbrook-api",
        branch="main",
        port=4100,
        status=AppStatus.RUNNING.value,
        is_static=False,
        commits=(
            ShowcaseCommit("5c8a1f0", "Add a health check endpoint", "Julen Anitua"),
            ShowcaseCommit("b3e7d29", "Bump dependencies", "Sara Lindqvist"),
        ),
        deployed_days_ago=(9.0, 1.0),
    ),
)

#: "lon-2": a staging server, everything green - no failure, no findings, one deploy a
#: little more recent than the rest so it does not read as identical to "fra-1"'s own.
LON_APPS: tuple[ShowcaseApp, ...] = (
    ShowcaseApp(
        # A subdomain of an already-checked apex (see the module docstring): nothing
        # resolves under a domain that does not exist, so this needs no check of its own.
        domain="staging.wrenfield.io",
        app_type="nextjs",
        repo="wrenfield-site",
        branch="staging",
        port=3000,
        status=AppStatus.RUNNING.value,
        is_static=False,
        # A staging site has no public certificate: it is also what keeps this node
        # genuinely calm, since the fake certbot always gives whichever certificate
        # leads the list the shortest validity of the lot.
        has_cert=False,
        commits=(
            ShowcaseCommit("a44f2c1", "Merge main into staging", "Owen Whitcombe"),
            ShowcaseCommit("c60d9b7", "Try the new pricing page copy live", "Noa Ferreira"),
        ),
        deployed_days_ago=(4.0, 0.5),
    ),
    ShowcaseApp(
        domain="staging.kestrelworks.io",
        app_type="nodejs",
        repo="kestrelworks-api",
        branch="staging",
        port=4000,
        status=AppStatus.RUNNING.value,
        is_static=False,
        has_cert=False,
        commits=(ShowcaseCommit("e91b0aa", "Verify the new rate limiter", "Dana Kessler"),),
        deployed_days_ago=(2.0,),
    ),
)

#: Each fleet node's own machine, keyed by the ``--hostname`` ``scripts/console_server.py
#: --showcase`` was given (the docs spec passes the same value for both): "fra-1" (the
#: default, also central role) is the agency's main server, "ams-3" and "lon-2" its other
#: two, each with its own applications and state rather than a copy of "fra-1"'s.
NODES: dict[str, tuple[ShowcaseApp, ...]] = {
    "fra-1": FRA_APPS,
    "ams-3": AMS_APPS,
    "lon-2": LON_APPS,
}

#: Fixed CPU, memory, disk (percent) and uptime (seconds) for every node's machine
#: snapshot *and* its metrics history's live tail (see console_server.py's
#: ``use_fixed_machine_stats``) - every node is really the same sandboxed host, so without
#: this, "ams-3" and "lon-2" show each other's numbers (coordinator review, on the fleet
#: page), and any node's own Overview chart ends at whatever this shared host's genuinely
#: noisy, often near-idle CPU happens to be, unlike its own top bar (coordinator review,
#: CPU dropping to 0%). "fra-1" keeps its disk at 24%, the reading it already showed
#: before this fix existed.
NODE_READINGS: dict[str, tuple[float, float, float, float]] = {
    # name: (cpu_percent, memory_percent, disk_percent, uptime_s)
    "fra-1": (34.0, 32.0, 24.0, 20 * 3_600 + 22 * 60),  # the agency's steady main server
    "ams-3": (12.0, 41.0, 57.0, 6 * 86_400 + 4 * 3_600),  # up ~6 days: a steady second region
    "lon-2": (46.0, 28.0, 18.0, 9 * 3_600 + 40 * 60),  # up ~9h40m: freshly redeployed staging
}

#: Each node's own address, as Settings > Servers shows it (``console_server.py``'s
#: ``join_fleet_node``). ``root@127.0.0.1`` - the loopback test seam every node's tunnel
#: actually dials - is correct for the E2E suite and wrong for a screenshot (coordinator
#: review): it gives the sandbox away. 203.0.113.0/24 and 198.51.100.0/24 are RFC 5737
#: documentation ranges, never assigned to anyone, so these need no reachability check of
#: their own. "fra-1" is the central itself and has no SSH address to show.
NODE_SSH_HOSTS: dict[str, str] = {
    "ams-3": "203.0.113.24",
    "lon-2": "198.51.100.61",
}

#: What a seeded application's systemd unit runs, by app type: illustrative only, matching
#: each real deployer's own ``ExecStart`` closely enough to read as genuine.
_SERVICE_COMMAND: dict[str, str] = {
    "nextjs": "/usr/bin/node current/server.js",
    "nodejs": "/usr/bin/node current/server.js",
    "python": "/usr/bin/python3 -m uvicorn app.main:app --host 127.0.0.1",
    "docker-compose": "/usr/bin/docker compose -f docker-compose.yml up",
}


@dataclass
class ShowcaseState:
    """
    What :func:`seed_showcase_state` put in the store.

    Same shape as :class:`tests.panel_factory.SeededState`: ``scripts/console_server.py``'s
    ``seed_machine`` reads either one the same way, whichever seeding function it called.
    """

    domains: list[str] = field(default_factory=list)
    app_ids: dict[str, int] = field(default_factory=dict)
    service_domains: list[str] = field(default_factory=list)
    site_domains: list[str] = field(default_factory=list)
    failed_domains: list[str] = field(default_factory=list)
    cert_domains: list[str] = field(default_factory=list)
    backup_domains: list[str] = field(default_factory=list)
    deployment_domains: list[str] = field(default_factory=list)
    static_domains: list[str] = field(default_factory=list)
    database_names: list[str] = field(default_factory=list)


def _build_log(app: ShowcaseApp, commit: ShowcaseCommit) -> str:
    """
    A deploy pipeline's own captured output for one commit of one application.

    Worded the way :class:`noust.deployers.recorder.DeploymentRecorder` captures a real
    deploy's output, with an author line the way ``git log -1 --pretty=fuller`` prints one:
    :class:`~noust.core.store.DeploymentRecord` has no author column (see the deployments
    list, which never shows one), so this is the one place the showcase's invented author
    names actually appear on screen, in the deployment's own build log.

    Args:
        app: The application deployed.
        commit: The commit deployed.

    Returns:
        The captured log text.
    """
    name, email = _author(commit.author)
    lines = [
        f"==> Fetching https://github.com/{GITHUB_ORG}/{app.repo} ({app.branch})",
        f"HEAD is now at {commit.sha} {commit.message}",
        f"Author: {name} <{email}>",
    ]
    if app.app_type in ("nextjs", "nodejs"):
        lines += [
            "==> Installing dependencies",
            "added 812 packages, and audited 813 packages in 14s",
        ]
        if app.app_type == "nextjs":
            lines += [
                "==> Building",
                "   Next.js 15.2.4",
                "   Creating an optimized production build ...",
            ]
            if commit.status == "failed" and commit.error is not None:
                lines += commit.error.splitlines()
                lines.append(f"Deploy failed; the previous release keeps serving {app.domain}")
                return "\n".join(lines) + "\n"
            lines.append(" ✓ Compiled successfully in 21.4s")
        else:
            lines += ["==> Running the test suite", "  42 passing (1.8s)"]
    elif app.app_type == "static":
        lines.append("==> Nothing to build: copying the tree as it is")
    elif app.app_type == "python":
        lines += [
            "==> Installing dependencies",
            "Successfully installed fastapi-0.115.0 uvicorn-0.30.6",
            "==> Running migrations",
            "  Applying reports.0007_add_export_index... OK",
        ]
    elif app.app_type == "php-fpm":
        lines += [
            "==> Installing dependencies",
            "Installing dependencies from lock file",
            "==> Updating WordPress core and plugins",
            "WordPress is at the latest version.",
        ]
    elif app.app_type == "docker-compose":
        lines += [
            "==> Pulling images",
            "worker Pulled",
            "==> Building",
            "Building worker",
        ]
    lines += [
        f"==> Activating release {commit.sha}",
        f"==> Health check passed: GET http://127.0.0.1:{app.port or 8080}/ answered 200 in 61 ms",
        f"Deployed {app.domain}",
    ]
    return "\n".join(lines) + "\n"


def _record_commit(
    store: NoustStore,
    log_root: Path,
    app: ShowcaseApp,
    commit: ShowcaseCommit,
    *,
    started: datetime,
) -> int:
    """
    Record one deployment of one showcase application, backdated, with its build log.

    Backdated with a direct update the way ``console_server.py``'s ``_tabs_deployment``
    does: :meth:`NoustStore.finish_deployment` always stamps "now", which would put every
    seeded deployment seconds apart regardless of ``started``.

    Args:
        store: The store.
        log_root: Where captured build logs live (``store.db_path.parent / "deploy-logs"``).
        app: The application.
        commit: The commit being recorded.
        started: When the deployment started.

    Returns:
        The deployment's id.
    """
    deployment_id = store.record_deployment_start(
        app.domain, "webhook", git_commit=commit.sha, git_branch=app.branch
    )
    store.annotate_deployment(deployment_id, commit_message=commit.message)

    directory = log_root / app.domain
    fs = get_fs()
    fs.make_dir(directory, mode=0o700, parents=True)
    log_path = directory / f"{deployment_id}.log"
    fs.write_text(log_path, _build_log(app, commit), mode=0o600)
    store.annotate_deployment(deployment_id, log_path=str(log_path))

    if commit.status == "running":
        # Still going: leave it queued/running, as record_deployment_start left it, only
        # backdated so it reads as "started a couple of minutes ago" rather than "now".
        with store._transaction() as cursor:
            cursor.execute(
                "UPDATE deployments SET started_at = ? WHERE id = ?",
                (started.isoformat(), deployment_id),
            )
        return int(deployment_id)

    duration = 24.0 if app.app_type == "nextjs" else 11.0
    finished = started + timedelta(seconds=duration)
    with store._transaction() as cursor:
        cursor.execute(
            "UPDATE deployments SET status = ?, error = ?, started_at = ?, finished_at = ?, "
            "duration_s = ? WHERE id = ?",
            (
                commit.status,
                commit.error,
                started.isoformat(),
                finished.isoformat(),
                round(duration, 1),
                deployment_id,
            ),
        )
    return int(deployment_id)


def cert_alt_names(node: str) -> dict[str, str]:
    """
    A node's own domains that have an alias, for the extra SAN their certificate shows.

    Read by ``console_server.py``'s ``make_runner`` before any seeding happens (the fake
    ``certbot certificates`` output is built from this and the plain ``certs`` list, not
    from the store), so it comes straight from the same :data:`NODES` data
    :func:`seed_showcase_state` seeds from, rather than a query.

    Args:
        node: Which of :data:`NODES` to read; the agency's main server by default.

    Returns:
        Domain to alias, for every application of that node that has one.
    """
    apps = NODES.get(node, FRA_APPS)
    return {app.domain: app.alias for app in apps if app.alias is not None}


def seed_showcase_state(store: NoustStore, *, node: str = "fra-1") -> ShowcaseState:
    """
    Populate a store with one of Kelmoor's invented machines: its applications, their
    deployment history, domains, databases - everything ``console_server.py``'s
    ``seed_machine`` reads generically off the store rather than by name, so this is the
    only place that needs to know the showcase's own domains, repositories and people.

    Args:
        store: The store to write to. Its directory also receives the captured build logs,
            the same way :func:`tests.panel_factory.seed_console_state` does.
        node: Which of :data:`NODES` to seed; the agency's main server by default. Unknown
            names fall back to it too, rather than raising, so a plain ``--showcase`` with
            no ``--hostname`` still seeds a complete machine.

    Returns:
        What was created.
    """
    apps = NODES.get(node, FRA_APPS)
    state = ShowcaseState()
    log_root = store.db_path.parent / "deploy-logs"
    now = datetime.now()

    for app in apps:
        record = store.create_app(
            App(
                domain=app.domain,
                app_type=app.app_type,
                source=f"https://github.com/{GITHUB_ORG}/{app.repo}.git",
                branch=app.branch,
                port=app.port,
                app_path=f"/var/www/apps/{domain_to_app_name(app.domain)}",
                status=app.status,
                is_static=app.is_static,
                ssl_enabled=True,
            )
        )
        assert record.id is not None
        state.domains.append(app.domain)
        state.app_ids[app.domain] = record.id
        if app.is_static:
            state.static_domains.append(app.domain)
        if app.status == AppStatus.FAILED.value:
            state.failed_domains.append(app.domain)

        # A static site (a plain one, or a PHP-FPM application, which is stored as one
        # too) has no unit of its own: the web server or the shared FPM pool runs it.
        if not app.is_static:
            unit = domain_to_app_name(app.domain)
            store.create_service(
                Service(
                    app_id=record.id,
                    name=unit,
                    unit_file=f"/etc/systemd/system/{unit}.service",
                    working_directory=f"/var/www/apps/{unit}",
                    command=_SERVICE_COMMAND.get(app.app_type, "/usr/bin/node current/server.js"),
                    status="failed" if app.status == AppStatus.FAILED.value else "active",
                    enabled=True,
                    port=app.port,
                )
            )
            state.service_domains.append(app.domain)

        store.create_site(
            Site(
                app_id=record.id,
                domain=app.domain,
                webserver="nginx",
                config_path=f"/etc/nginx/sites-available/{app.domain}",
                enabled=True,
                proxy_port=app.port,
                ssl_enabled=True,
                ssl_certificate=(
                    f"/etc/letsencrypt/live/{app.domain}/fullchain.pem" if app.has_cert else None
                ),
                ssl_key=(
                    f"/etc/letsencrypt/live/{app.domain}/privkey.pem" if app.has_cert else None
                ),
            )
        )
        state.site_domains.append(app.domain)
        if app.has_cert:
            state.cert_domains.append(app.domain)
        if app.alias is not None:
            store.add_domain(app.domain, app.alias, DomainKind.ALIAS.value)

        for commit, days_ago in zip(app.commits, app.deployed_days_ago, strict=True):
            _record_commit(store, log_root, app, commit, started=now - timedelta(days=days_ago))
        state.deployment_domains.append(app.domain)

    # Whichever domain leads state.cert_domains gets the shortest validity from
    # console_server.py's fake certbot (make_runner's _certificates): put the one app that
    # asked for it, if any, first.
    expiring = {a.domain for a in apps if a.cert_expires_soon}
    state.cert_domains.sort(key=lambda domain: 0 if domain in expiring else 1)

    state.backup_domains = [a.domain for a in apps[:2]]

    # One database per one or two of this node's own applications, plus a shared cache -
    # named after the node's first application rather than "fra-1"'s by name, so "ams-3"
    # and "lon-2" do not carry "wrenfield" in their own database names.
    primary = domain_to_app_name(apps[0].domain).replace("-", "_")
    engines = (
        (f"{primary}_production", DatabaseEngine.POSTGRESQL.value, 5432, apps[0].domain),
        ("sessions_cache", DatabaseEngine.REDIS.value, 6379, None),
    )
    if len(apps) > 1:
        secondary = domain_to_app_name(apps[1].domain).replace("-", "_")
        engines = (
            engines[0],
            (f"{secondary}_api", DatabaseEngine.MYSQL.value, 3306, apps[1].domain),
            engines[1],
        )
    for name, engine, port, owner in engines:
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

    return state
