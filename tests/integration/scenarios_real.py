# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Real-software scenarios of wave 6 (W6-REAL): what was proved with a fake, run against the real thing.

They are scenarios of :mod:`run` (same container, same helpers, same report): ``run.py`` imports this
module before it picks what to run, so ``--scenario recipe_compose_memos`` selects one here like any
other. Each one drives the real CLI against real nginx, systemd and Docker, and cleans up after itself.

- ``recipe_compose_<name>`` (checklist D-24): the sixteen Compose recipes of the first wave installed
  with ``noust recipe install``, each pulling its pinned image into the harness container's own
  Docker, passing its health gate, answering through nginx with what the application really says,
  surviving a restart, and leaving nothing behind when deleted. Optional (``--scenario recipe_compose``):
  they pull gigabytes.
- ``coolify_takeover_of_a_real_server`` (checklist N-14): Coolify 4 installed with its own installer, an
  application and a two-container service deployed through its API, then adopted by Noust, Coolify
  taken down, the stacks given a loopback port and a site, and a restart of Docker survived with the
  volume's data. Optional (``--scenario coolify_takeover``): it installs Coolify.

Docker: the stacks run in the harness container's own Docker daemon, never the machine's. A scenario
that cannot pull its image (no network, a registry that refuses) is skipped, not failed.
"""

from __future__ import annotations

import base64
import json
import re
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import run
from run import (
    INNER_DOCKER_CLEAR,
    Scenario,
    ScenarioSkipped,
    require_docker,
    scenario,
)

#: A pull of a large image and a first start are slow; the gate itself waits for the application.
RECIPE_INSTALL_TIMEOUT = 2400

#: Seconds a stack gets to answer again after a restart.
RESTART_ANSWERS_WITHIN = 180


@dataclass(frozen=True)
class Probe:
    """
    One request through nginx and what a real instance answers.

    Attributes:
        path: The path asked for.
        status: The status expected (``"200"``), or a range (``"200-399"``).
        needle: A substring the body must contain; empty for none.
        auth: The names of the two variables that hold the account the recipe generated for the
            web interface (user, password), read from the stack's ``.env``; None for no account.
        host: The ``Host`` to send; the application's own domain when empty.
        why: What the probe proves, for the failure message.
    """

    path: str
    status: str = "200"
    needle: str = ""
    auth: tuple[str, str] | None = None
    host: str = ""
    why: str = ""


@dataclass(frozen=True)
class RecipeCase:
    """
    What a recipe is expected to do once installed.

    Attributes:
        probes: The requests that prove it is the application it says and is configured.
        variables: ``NAME=value`` for the variables the recipe requires and cannot generate.
        secrets: The variables whose generated values must be in the stack's ``.env`` and in no
            other file Noust writes world-readable.
    """

    probes: tuple[Probe, ...]
    variables: tuple[str, ...] = ()
    secrets: tuple[str, ...] = ()


#: The sixteen Compose recipes of the first wave and what each really answers. The needles are what
#: the application's own pages and APIs say, not what Noust writes.
COMPOSE_RECIPES: dict[str, RecipeCase] = {
    "code-server": RecipeCase(
        probes=(
            # {"status":"alive"|"expired","lastHeartbeat":...}: "expired" is what a server nobody has
            # opened yet answers, and the 200 is the health.
            Probe("/healthz", needle='"status"', why="code-server's own health endpoint"),
            Probe("/login", needle="code-server", why="the login page of the editor"),
        ),
        secrets=("PASSWORD",),
    ),
    "freshrss": RecipeCase(
        probes=(Probe("/", status="200-399", why="FreshRSS answers once its installer ran"),),
        variables=("ADMIN_EMAIL=it@example.com",),
        secrets=("ADMIN_PASSWORD", "ADMIN_API_PASSWORD"),
    ),
    "grafana": RecipeCase(
        probes=(
            Probe("/api/health", needle='"database"', why="Grafana's health JSON"),
            Probe("/login", needle="Grafana", why="the login page"),
        ),
        secrets=("GF_SECURITY_ADMIN_PASSWORD",),
    ),
    "homepage": RecipeCase(
        probes=(
            Probe("/api/healthcheck", why="Homepage's health endpoint"),
            Probe("/", needle="Homepage", why="the dashboard"),
        ),
    ),
    "jellyfin": RecipeCase(
        probes=(
            Probe("/health", needle="Healthy", why="Jellyfin's health endpoint"),
            Probe("/System/Info/Public", needle="ServerName", why="the public server info"),
        ),
    ),
    "linkding": RecipeCase(
        probes=(
            Probe("/health", needle="version", why="linkding's health JSON"),
            Probe("/login/", needle="Linkding", why="the login page"),
        ),
        secrets=("LD_SUPERUSER_PASSWORD",),
    ),
    "mailpit": RecipeCase(
        probes=(
            Probe("/livez", why="Mailpit's liveness endpoint"),
            Probe("/", status="401", why="the web interface asks for the generated account"),
            Probe(
                "/", needle="Mailpit", auth=("MAILPIT_USER", "MAILPIT_PASSWORD"), why="the inbox"
            ),
        ),
        secrets=("MAILPIT_PASSWORD",),
    ),
    "meilisearch": RecipeCase(
        probes=(
            Probe("/health", needle="available", why="Meilisearch's health endpoint"),
            Probe("/indexes", status="401", why="the API refuses a request with no key"),
        ),
        secrets=("MEILI_MASTER_KEY",),
    ),
    "memos": RecipeCase(
        probes=(
            Probe("/healthz", why="Memos' health endpoint"),
            Probe("/", needle="Memos", why="the single page application"),
        ),
    ),
    "navidrome": RecipeCase(
        probes=(
            # The ping answers a single dot, and the player's page says its name.
            Probe("/ping", needle=".", why="Navidrome's ping"),
            # The root redirects to the player, which names itself.
            Probe("/", status="302", why="the root sends a visitor to the player"),
            Probe("/app/", needle="Navidrome", why="the web player"),
        ),
    ),
    "ntfy": RecipeCase(
        probes=(
            Probe("/v1/health", needle='"healthy":true', why="ntfy's health JSON"),
            # GET /<topic> is the web application's page for any name, open to everybody; reading
            # the topic through the API is what the default access refuses.
            Probe(
                "/it-topic/json?poll=1",
                status="403",
                why="deny-all, the recipe's default access, on the API",
            ),
        ),
    ),
    "ollama": RecipeCase(
        probes=(
            Probe("/", needle="Ollama is running", why="Ollama's root"),
            Probe("/api/version", needle="version", why="the API"),
        ),
    ),
    "open-webui": RecipeCase(
        probes=(
            Probe("/health", needle="true", why="Open WebUI's health JSON"),
            Probe("/", needle="Open WebUI", why="the application"),
        ),
        secrets=("WEBUI_SECRET_KEY",),
    ),
    "stirling-pdf": RecipeCase(
        probes=(Probe("/api/v1/info/status", needle="UP", why="Stirling's status JSON"),),
        secrets=("SECURITY_INITIALLOGIN_PASSWORD",),
    ),
    "syncthing": RecipeCase(
        probes=(Probe("/rest/noauth/health", needle="OK", why="Syncthing's health endpoint"),),
    ),
    "vaultwarden": RecipeCase(
        probes=(
            Probe("/alive", why="Vaultwarden's liveness endpoint"),
            Probe("/", needle="Vaultwarden", why="the web vault"),
            Probe(
                "/api/config",
                needle="vaultwarden.recipe.test/api",
                why="the address the recipe configured, as the application reports it",
            ),
        ),
        secrets=("ADMIN_TOKEN",),
    ),
}


def recipe_domain(name: str) -> str:
    """
    Name the domain a recipe is installed on.

    Args:
        name: The recipe.

    Returns:
        ``<name>.recipe.test``.
    """
    return f"{name}.recipe.test"


def stack_env(sc: Scenario, domain: str) -> dict[str, str]:
    """
    Read the variables Noust wrote for a stack.

    Args:
        sc: The scenario.
        domain: The application.

    Returns:
        ``NAME`` to value, from the stack's ``.env``.
    """
    app = domain.replace(".", "-")
    text = sc.run(
        f"cat /var/www/apps/{app}/.env",
        timeout=15,
        label=f"the variables of {domain} (secrets are hidden below)",
    ).stdout
    found: dict[str, str] = {}
    for line in text.splitlines():
        name, _, value = line.partition("=")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            found[name] = value.strip().strip("'\"")
    # The evidence block above printed the file as it is: replace it by its names.
    sc.evidence[-1] = f"$ the variables of {domain}\n" + ", ".join(sorted(found))
    return found


def probe_through_nginx(
    sc: Scenario, domain: str, probe: Probe, variables: dict[str, str]
) -> tuple[str, str]:
    """
    Make one request the way a visitor does, through nginx on loopback.

    Args:
        sc: The scenario.
        domain: The application's domain.
        probe: What to ask.
        variables: The stack's variables, for the account of a probe that needs one.

    Returns:
        The status code and the body.
    """
    host = probe.host or domain
    credentials = ""
    if probe.auth is not None:
        user, password = (variables[probe.auth[0]], variables[probe.auth[1]])
        credentials = f"-u {shlex.quote(f'{user}:{password}')} "
    label = f"curl -H 'Host: {host}' http://127.0.0.1{probe.path}" + (
        " (as the generated account)" if probe.auth else ""
    )
    # The whole body is read (a page's title can come after kilobytes of links) but only an excerpt
    # is kept as evidence: a page of markup says nothing the status and the needle do not.
    proc = run.docker_exec(
        sc.container,
        f"curl -sS -m 60 {credentials}-o /tmp/probe.body -w '%{{http_code}}' "
        f"-H 'Host: {host}' 'http://127.0.0.1{probe.path}'; echo; head -c 200000 /tmp/probe.body",
        timeout=90,
        check=False,
    )
    code, _, body = proc.stdout.partition("\n")
    excerpt = " ".join(body.split())[:300]
    sc.evidence.append(
        f"$ {label}\n{code.strip()} ({len(body)} bytes) {excerpt}"
        + (f"\n--- stderr ---\n{proc.stderr.strip()}" if proc.stderr.strip() else "")
    )
    return code.strip(), body


def status_matches(code: str, expected: str) -> bool:
    """
    Tell whether a status is the one a probe expects.

    Args:
        code: The status.
        expected: ``"200"`` or ``"200-399"``.

    Returns:
        Whether it matches.
    """
    low, _, high = expected.partition("-")
    return code.isdigit() and int(low) <= int(code) <= int(high or low)


def wait_for_probe(
    sc: Scenario, domain: str, probe: Probe, variables: dict[str, str], *, within: float
) -> tuple[str, str]:
    """
    Ask until the probe is answered as expected, or the time is up.

    Args:
        sc: The scenario.
        domain: The application's domain.
        probe: What to ask.
        variables: The stack's variables.
        within: Seconds to keep asking.

    Returns:
        The last status and body.
    """
    deadline = time.monotonic() + within
    code, body = "", ""
    while True:
        code, body = probe_through_nginx(sc, domain, probe, variables)
        if status_matches(code, probe.status) and probe.needle in body:
            return code, body
        if time.monotonic() >= deadline:
            return code, body
        time.sleep(5)


def containers_of_the_stack(sc: Scenario) -> list[tuple[str, str, int]]:
    """
    List the containers the harness container's own Docker is running.

    The daemon is cleared before a recipe is installed, so these are the stack's.

    Args:
        sc: The scenario.

    Returns:
        ``(name, state, restart count)`` for each, from ``docker inspect``.
    """
    ids = sc.run("docker ps -aq", timeout=30, label="docker ps -aq (the stack's containers)")
    found: list[tuple[str, str, int]] = []
    for container in ids.stdout.split():
        info = sc.run(
            f"docker inspect -f '{{{{.Name}}}} {{{{.State.Status}}}} "
            f"{{{{if .State.Health}}}}{{{{.State.Health.Status}}}}{{{{else}}}}-{{{{end}}}} "
            f"{{{{.RestartCount}}}}' {container}",
            timeout=30,
            label=f"docker inspect {container}",
        ).stdout.split()
        name, state, health, restarts = info[0].lstrip("/"), info[1], info[2], int(info[3])
        found.append((name, state if health in ("-", "healthy") else f"{state}/{health}", restarts))
    return found


#: Resolvers the harness container's own Docker falls back on. The machine's (Docker Desktop's
#: 192.168.65.7) times out when the machine is busy, and a pull that cannot look a registry up
#: fails a scenario about something else.
FALLBACK_RESOLVERS = ("1.1.1.1", "8.8.8.8")

#: What a registry that cannot be reached looks like in a unit's journal.
UNREACHABLE_REGISTRY = re.compile(
    r"(pull access denied|toomanyrequests|no such host|TLS handshake timeout|i/o timeout|"
    r"context deadline exceeded|Temporary failure in name resolution|Could not resolve host)"
)


def ensure_inner_dns(sc: Scenario) -> None:
    """
    Give the harness container's own Docker resolvers to fall back on, once.

    Two places: the daemon reads the container's ``/etc/resolv.conf`` to look a registry up (a
    second and third nameserver there are tried when the first times out), and the containers it
    runs get ``dns`` from ``daemon.json``.

    Args:
        sc: The scenario.
    """
    sc.run(
        "grep -q 1.1.1.1 /etc/resolv.conf || "
        "printf 'nameserver 1.1.1.1\\nnameserver 8.8.8.8\\n' >> /etc/resolv.conf",
        timeout=15,
        label="the daemon's own lookups fall back on 1.1.1.1 and 8.8.8.8",
    )
    have = sc.run(
        "grep -c '\"dns\"' /etc/docker/daemon.json || true",
        timeout=15,
        check=False,
        label="does the inner Docker have resolvers of its own?",
    ).stdout.strip()
    if have == "1":
        return
    resolvers = ", ".join(f'"{address}"' for address in FALLBACK_RESOLVERS)
    # A heredoc: the document is JSON, and quoting it into one line is how it gets wrong.
    sc.run(
        "cat > /etc/docker/daemon.json <<'EOF'\n"
        "{\n"
        '  "storage-driver": "overlay2",\n'
        '  "features": {"containerd-snapshotter": false},\n'
        f'  "dns": [{resolvers}]\n'
        "}\n"
        "EOF\n"
        "systemctl restart docker\n"
        "for i in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 1; done\n"
        "docker info >/dev/null 2>&1",
        timeout=120,
        label=f"the inner Docker falls back on {', '.join(FALLBACK_RESOLVERS)}",
    )


def unit_journal(sc: Scenario, app: str) -> str:
    """
    Read the end of an application's unit journal, where Compose says why it could not start.

    Args:
        sc: The scenario.
        app: The application's name.

    Returns:
        The last lines.
    """
    return sc.run(
        f"journalctl -u {app} --no-pager -n 25",
        timeout=30,
        check=False,
        label=f"journalctl -u {app} (tail)",
    ).stdout


def recipe_scenario(name: str, case: RecipeCase) -> Callable[[Scenario], None]:
    """
    Make the scenario of one recipe.

    Args:
        name: The recipe.
        case: What it is expected to do.

    Returns:
        A scenario function.
    """

    def scenario_fn(sc: Scenario) -> None:
        require_docker(sc)
        ensure_inner_dns(sc)
        domain = recipe_domain(name)
        app = domain.replace(".", "-")
        sc.run(
            f"noust delete {domain} --force >/dev/null 2>&1; {INNER_DOCKER_CLEAR}; true",
            timeout=300,
            check=False,
            label=f"(clean start) delete {domain} and clear the harness container's Docker",
        )
        variables = " ".join(f"--var {shlex.quote(v)}" for v in case.variables)
        install = sc.run(
            f"noust recipe install {name} -d {domain} --no-ssl --no-backups {variables}".rstrip(),
            timeout=RECIPE_INSTALL_TIMEOUT,
            check=False,
            label=f"noust recipe install {name} -d {domain} --no-ssl --no-backups {variables}".rstrip(),
        )
        output = install.stdout + install.stderr
        if install.returncode != 0:
            # The deployment says that the unit failed; Compose says why in the journal.
            journal = unit_journal(sc, app)
            if UNREACHABLE_REGISTRY.search(output + journal):
                raise ScenarioSkipped(
                    f"the image of {name} cannot be pulled from here: "
                    f"{(journal or output).strip()[-400:]}"
                )
            output += "\n--- the unit's journal ---\n" + journal
        sc.check(
            install.returncode == 0,
            f"noust recipe install {name} failed ({install.returncode}):\n{output[-3500:]}",
        )
        try:
            _check_installed(sc, name, case, domain, app)
        finally:
            run.journal_tail(sc, "-u docker", "journalctl -u docker (tail)")
            sc.run(
                f"noust delete {domain} --force",
                timeout=600,
                check=False,
                label=f"cleanup: noust delete {domain} --force",
            )
            sc.run(
                INNER_DOCKER_CLEAR,
                timeout=300,
                check=False,
                label="cleanup: clear the harness container's Docker (the images stay)",
            )

    scenario_fn.__doc__ = (
        f"The {name} recipe installed for real: its image pulled, its health gate passed, its "
        "pages answered through nginx, a restart survived, and nothing left when deleted."
    )
    return scenario_fn


def _check_installed(sc: Scenario, name: str, case: RecipeCase, domain: str, app: str) -> None:
    """
    Check an installed recipe the way an operator would: from outside, then from inside.

    Args:
        sc: The scenario.
        name: The recipe.
        case: What it is expected to do.
        domain: The application's domain.
        app: Its name.
    """
    variables = stack_env(sc, domain)
    for secret in case.secrets:
        sc.check(bool(variables.get(secret)), f"{secret} was not generated into the stack's .env")
    env_mode = sc.run(f"stat -c '%a' /var/www/apps/{app}/.env", timeout=15, label="mode of .env")
    sc.check(env_mode.stdout.strip() in ("600", "640"), f".env is mode {env_mode.stdout.strip()}")

    for probe in case.probes:
        code, body = wait_for_probe(sc, domain, probe, variables, within=240)
        sc.evidence.append(f"# {probe.why}: {code}, {len(body)} bytes")
        sc.check(
            status_matches(code, probe.status),
            f"{name}: {probe.path} answered {code}, expected {probe.status} ({probe.why})",
        )
        sc.check(
            probe.needle in body,
            f"{name}: {probe.path} does not say {probe.needle!r} ({probe.why}): {body[:300]!r}",
        )

    containers = containers_of_the_stack(sc)
    # A health check that has a start period reports "starting" for as long as it says, after the
    # application already answers; the container is judged when that is over.
    patience = time.monotonic() + 180
    while any(state.endswith("/starting") for _, state, _ in containers):
        if time.monotonic() >= patience:
            break
        time.sleep(10)
        containers = containers_of_the_stack(sc)
    sc.check(bool(containers), f"{name} left no container running")
    for container, state, restarts in containers:
        sc.check(state == "running", f"{container} is {state}")
        sc.check(restarts == 0, f"{container} restarted {restarts} time(s) while it started")
    stored = sc.run(f"noust status {domain}", timeout=60, label=f"noust status {domain}").stdout
    sc.check("running" in stored.lower() or "Active: Yes" in stored, f"noust status:\n{stored}")

    # A restart: the stack comes back and answers what it answered.
    sc.run(f"noust restart {domain}", timeout=300, label=f"noust restart {domain}")
    first = case.probes[0]
    code, body = wait_for_probe(sc, domain, first, variables, within=RESTART_ANSWERS_WITHIN)
    sc.check(
        status_matches(code, first.status) and first.needle in body,
        f"{name} does not answer {first.path} after a restart: {code} {body[:200]!r}",
    )

    sc.run(f"noust delete {domain} --force", timeout=600, label=f"noust delete {domain}")
    left = sc.run("docker ps -aq", timeout=30, label="docker ps -aq (after the delete)")
    sc.check(left.stdout.strip() == "", f"containers are left after the delete: {left.stdout!r}")
    site = sc.run(
        f"test -e /etc/nginx/sites-enabled/{domain} && echo present || echo gone",
        timeout=15,
        label=f"is the site of {domain} still enabled?",
    )
    sc.check(site.stdout.strip() == "gone", f"the nginx site of {domain} is still enabled")


for _name, _case in COMPOSE_RECIPES.items():
    scenario(f"recipe_compose_{_name.replace('-', '_')}")(recipe_scenario(_name, _case))


# ---------------------------------------------------------------------------
# A server Coolify runs, taken over (checklist N-14; optional: --scenario coolify_takeover)
# ---------------------------------------------------------------------------

#: The release of Coolify the scenario installs. The installer is fetched live and takes the version
#: as its argument; what is verified here is this release, and a newer one is a new run.
COOLIFY_VERSION = "4.4.6"
COOLIFY_INSTALLER = "https://cdn.coollabs.io/coolify/install.sh"

#: Where the scenario keeps what it needs of Coolify: the installer, the API token (a file readable
#: by root only, never printed) and the uuid of its server.
COOLIFY_WORK = "/root/coolify-dl"
COOLIFY_API = "http://127.0.0.1:8000/api/v1"

TAKEOVER_APP = "whoami.takeover.test"
TAKEOVER_STACK = "stack.takeover.test"
#: Loopback ports the two stacks are made to publish, because Coolify's publish none.
TAKEOVER_APP_PORT = 18080
TAKEOVER_STACK_PORT = 18081

#: Makes the root user Coolify's installer leaves unseeded when the email is not one Laravel's
#: validation accepts, enables the API and makes a token the scenario keeps in a file.
COOLIFY_TOKEN_PHP = """\
if (! App\\Models\\User::where('id', 0)->exists()) {
    $u = (new App\\Models\\User)->forceFill(['id' => 0, 'name' => 'admin', 'email' => 'admin@example.com',
        'password' => Illuminate\\Support\\Facades\\Hash::make(bin2hex(random_bytes(12)) . 'Aa1!')]);
    $u->save();
}
$user = App\\Models\\User::find(0);
session(['currentTeam' => $user->teams()->first()]);
App\\Models\\InstanceSettings::get()->update(['is_api_enabled' => true]);
$token = $user->createToken('it', ['root']);
file_put_contents('/tmp/api-token', $token->plainTextToken);
echo "OK\\n";
"""

#: The service created through Coolify's API: two containers and a named volume, which is what a
#: panel's "service" is and what a takeover must not lose.
COOLIFY_SERVICE_COMPOSE = """\
services:
  web:
    image: traefik/whoami:v1.10.3
    environment:
      - WHOAMI_NAME=from-a-coolify-service
  cache:
    image: redis:7.4-alpine
    command: ["redis-server", "--appendonly", "yes"]
    volumes:
      - cache-data:/data
volumes:
  cache-data: {}
"""


def coolify_api(
    sc: Scenario,
    method: str,
    path: str,
    *,
    body: str | None = None,
    label: str,
    quiet: bool = False,
) -> Any:
    """
    Call Coolify's API from inside the harness container, as the root token the scenario made.

    Args:
        sc: The scenario.
        method: The HTTP method.
        path: The path under ``/api/v1``.
        body: A JSON document to send, or None.
        label: What the call is for, which is what the evidence shows (the token never is).
        quiet: Keep the answer out of the evidence: the server's settings hold a token of its own.

    Returns:
        The JSON it answered; an empty object when the answer is not JSON.
    """
    data = f"-H 'Content-Type: application/json' -d {shlex.quote(body)} " if body else ""
    proc = sc.run(
        f'curl -sS -m 120 -X {method} -H "Authorization: Bearer $(cat {COOLIFY_WORK}/api-token)" '
        f"{data}{COOLIFY_API}{path}",
        timeout=180,
        label=f"Coolify API: {method} {path} ({label})",
    )
    if quiet:
        sc.evidence[-1] = f"$ Coolify API: {method} {path} ({label})\n(the answer is not kept)"
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}


def install_coolify(sc: Scenario) -> str:
    """
    Install Coolify as its documentation says, make an API token for it and let it use the host.

    Args:
        sc: The scenario.

    Returns:
        The uuid of the server Coolify made of the host.
    """
    # A Coolify server has no nginx (Traefik owns 80 and 443) and reaches itself over ssh as root.
    sc.run(
        f"systemctl disable --now nginx; systemctl enable --now ssh; mkdir -p {COOLIFY_WORK}",
        timeout=120,
        label="(a Coolify server) no nginx, sshd for Coolify's own connection",
    )
    sc.run(
        f"cd {COOLIFY_WORK} && curl -fsSL -o install.sh {COOLIFY_INSTALLER} && sha256sum install.sh",
        timeout=120,
        label=f"download Coolify's installer ({COOLIFY_INSTALLER})",
    )
    # The password is made in the shell and handed to the installer through its environment: it is
    # printed nowhere. The installer is given $USER because it names its ssh key after it.
    installed = sc.run(
        f"cd {COOLIFY_WORK} && export USER=root ROOT_USERNAME=admin "
        "ROOT_USER_EMAIL=admin@example.com AUTOUPDATE=false "
        'ROOT_USER_PASSWORD="Cf-$(openssl rand -hex 10)-Aa1!" && '
        f'bash install.sh {COOLIFY_VERSION} > install.out 2>&1; echo "installer exit: $?"; '
        "tail -n 6 install.out | cut -c1-200",
        timeout=2100,
        check=False,
        label=f"bash install.sh {COOLIFY_VERSION} (Coolify's installer; its output is in install.out)",
    )
    sc.check(
        "installer exit: 0" in installed.stdout, f"Coolify's installer failed:\n{installed.stdout}"
    )
    sc.run(
        wait_script("curl -fsS -m 5 http://127.0.0.1:8000/api/health >/dev/null 2>&1", within=300),
        timeout=400,
        label="wait for Coolify to answer on :8000",
    )
    sc.run(
        f"cat > {COOLIFY_WORK}/token.php <<'PHP'\n{COOLIFY_TOKEN_PHP}PHP\n"
        f'docker exec coolify php artisan tinker --execute="$(cat {COOLIFY_WORK}/token.php)" 2>&1 '
        "| tail -3; "
        f"docker exec coolify cat /tmp/api-token > {COOLIFY_WORK}/api-token; "
        f"chmod 600 {COOLIFY_WORK}/api-token; docker exec coolify rm -f /tmp/api-token; "
        f"wc -c < {COOLIFY_WORK}/api-token",
        timeout=300,
        label="an API token for Coolify, kept in a file only root reads",
    )
    servers = coolify_api(sc, "GET", "/servers", label="the servers it knows", quiet=True)
    server = servers[0]["uuid"]
    coolify_api(
        sc, "POST", f"/servers/{server}/validate", label="Coolify checks it can use the host"
    )
    sc.run(
        wait_script(
            f'curl -sS -m 20 -H "Authorization: Bearer $(cat {COOLIFY_WORK}/api-token)" '
            f"{COOLIFY_API}/servers/{server} | grep -q '\"is_usable\":true'",
            within=200,
        ),
        timeout=300,
        check=False,
        label="wait for Coolify to call the host usable",
    )
    return str(server)


def wait_script(condition: str, *, within: int) -> str:
    """
    Make a shell loop that waits for a condition and then asserts it.

    Args:
        condition: A shell command that succeeds when the wait is over.
        within: Seconds to wait.

    Returns:
        The script.
    """
    return f"for i in $(seq 1 {within // 5}); do {condition} && break; sleep 5; done; {condition}"


def scenario_coolify_takeover(sc: Scenario) -> None:
    """
    A server a real Coolify runs is taken over by Noust, one stack at a time, without touching them.

    Coolify 4 is installed (its own installer, in the harness container's Docker) and runs two
    things: an application from an image, and a service of two containers with a volume. Both
    answer through Coolify's Traefik. Noust then adopts each (the containers are the same ones
    afterwards), Coolify and its proxy are taken down for good, each stack publishes a loopback port
    and is updated, nginx serves their names, the units are handed back, and a restart of Docker
    (what a reboot does) brings back the two stacks and nothing of Coolify, with the volume's data.
    """
    require_docker(sc)
    ensure_inner_dns(sc)
    sc.run(
        f"noust delete {TAKEOVER_APP} --force >/dev/null 2>&1; "
        f"noust delete {TAKEOVER_STACK} --force >/dev/null 2>&1; "
        f"{INNER_DOCKER_CLEAR}; rm -rf /data/coolify {COOLIFY_WORK}; true",
        timeout=300,
        check=False,
        label="(clean start) no application, no container, no Coolify",
    )
    try:
        _takeover(sc)
    finally:
        sc.run(
            f"noust delete {TAKEOVER_APP} --force; noust delete {TAKEOVER_STACK} --force; "
            f"{INNER_DOCKER_CLEAR}; rm -rf /data/coolify {COOLIFY_WORK}; "
            "sed -i '/coolify/d' /root/.ssh/authorized_keys; "
            # The harness container's nginx is what every other scenario serves through: the
            # scenario stopped it to be a Coolify server, and gives it back.
            "systemctl enable --now nginx; true",
            timeout=600,
            check=False,
            label="cleanup: delete both applications, clear Docker, forget Coolify, nginx back",
        )


def _takeover(sc: Scenario) -> None:
    """
    Install Coolify, deploy two things with it, take them over and check each step.

    Args:
        sc: The scenario.
    """
    server = install_coolify(sc)
    project = coolify_api(
        sc, "POST", "/projects", body='{"name":"takeover"}', label="a project to hold them"
    )["uuid"]
    app = coolify_api(
        sc,
        "POST",
        "/applications/dockerimage",
        body=json.dumps(
            {
                "project_uuid": project,
                "server_uuid": server,
                "environment_name": "production",
                "docker_registry_image_name": "traefik/whoami",
                "docker_registry_image_tag": "v1.10.3",
                "ports_exposes": "80",
                "domains": f"http://{TAKEOVER_APP}",
                "name": "whoami",
                "instant_deploy": True,
            }
        ),
        label="an application from an image, with a domain",
    )["uuid"]
    service = coolify_api(
        sc,
        "POST",
        "/services",
        body=json.dumps(
            {
                "project_uuid": project,
                "server_uuid": server,
                "environment_name": "production",
                "name": "stack",
                "docker_compose_raw": base64.b64encode(COOLIFY_SERVICE_COMPOSE.encode()).decode(),
                "instant_deploy": False,
            }
        ),
        label="a service of two containers and a volume",
    )["uuid"]
    coolify_api(
        sc,
        "PATCH",
        f"/services/{service}",
        body=json.dumps({"urls": [{"name": "web", "url": f"http://{TAKEOVER_STACK}:80"}]}),
        label="the service's domain",
    )
    coolify_api(sc, "POST", f"/services/{service}/start", label="start the service")
    app_dir = f"/data/coolify/applications/{app}"
    stack_dir = f"/data/coolify/services/{service}"

    # Both answer the way they always did: through Coolify's Traefik on port 80.
    served = ((TAKEOVER_APP, "Hostname:"), (TAKEOVER_STACK, "from-a-coolify-service"))
    for domain, needle in served:
        sc.run(
            wait_script(
                f"curl -sS -m 10 -H 'Host: {domain}' http://127.0.0.1/ | grep -q '{needle}'",
                within=300,
            ),
            timeout=360,
            label=f"{domain} answers through Coolify's Traefik",
        )
    sc.run(
        f"docker exec cache-{service} redis-cli set takeover-proof survives-the-takeover && "
        f"docker exec cache-{service} redis-cli save",
        timeout=60,
        label="data in the service's volume, which has to survive",
    )
    before = _container_ids(sc)

    # Noust, rehearsed and then for real.
    for domain, directory in ((TAKEOVER_APP, app_dir), (TAKEOVER_STACK, stack_dir)):
        plan = sc.run(
            f"noust --dry-run app adopt {domain} --path {directory} -y",
            timeout=300,
            label=f"noust --dry-run app adopt {domain} --path {directory} -y",
        ).stdout
        sc.check(
            "Rehearsed up: changes nothing" in plan,
            f"the rehearsal of {domain} changes something:\n{plan}",
        )
        sc.check(
            "Containers: none" not in plan,
            f"Noust does not see the containers of {domain}:\n{plan}",
        )
        sc.check("no repository" in plan, f"Noust does not say {domain} has no repository:\n{plan}")
        sc.run(
            f"noust app adopt {domain} --path {directory} -y",
            timeout=300,
            label=f"noust app adopt {domain} --path {directory} -y",
        )
    sc.check(
        _container_ids(sc) == before,
        "adopting changed a container: the stacks are not the ones Coolify made",
    )
    sc.run(
        f"curl -sS -m 10 -H 'Host: {TAKEOVER_APP}' http://127.0.0.1/ | grep -q Hostname",
        timeout=30,
        label="still served by Coolify's Traefik after the adoption",
    )

    # Coolify goes, for good: its own project down (three of its containers restart "always", so a
    # stop would not survive a restart of Docker) and its proxy and sentinel stopped while running.
    sc.run(
        "cd /data/coolify/source && docker compose --env-file .env -f docker-compose.yml "
        "-f docker-compose.prod.yml down && cd / && docker stop coolify-proxy coolify-sentinel",
        timeout=600,
        label="Coolify down: its compose project, then its proxy and sentinel",
    )
    sc.run("! ss -ltn | grep -E ':(80|443) '", timeout=15, label="ports 80 and 443 are free")
    stacks = _stack_names(sc, app, service)
    left = sc.run(
        "docker ps --format '{{.Names}}'", timeout=30, label="what still runs"
    ).stdout.split()
    sc.check(
        sorted(left) == sorted(stacks), f"only the two stacks should run, and these do: {left}"
    )

    # Each stack publishes a loopback port (Coolify's publish none) and is updated by Noust; a site
    # per name is written, which starts the web server nobody had started.
    for domain, directory, port in (
        (TAKEOVER_APP, app_dir, TAKEOVER_APP_PORT),
        (TAKEOVER_STACK, stack_dir, TAKEOVER_STACK_PORT),
    ):
        _publish_port(sc, directory, port)
        sc.run(
            f"noust update {domain}",
            timeout=900,
            label=f"noust update {domain} (no repository: the images are pulled)",
        )
        sc.run(
            wait_script(f"curl -sS -m 10 http://127.0.0.1:{port}/ | grep -q Hostname", within=120),
            timeout=180,
            label=f"{domain} answers on 127.0.0.1:{port}",
        )
    # The update writes the facts of the deployment into the .env every service of the stack reads,
    # so the cache is recreated with it; its volume is what has to be the same one.
    sc.check(
        sc.run(
            f"docker exec cache-{service} redis-cli get takeover-proof",
            timeout=30,
            label="the data in the volume, after the cache was recreated",
        ).stdout.strip()
        == "survives-the-takeover",
        "the data in the service's volume did not survive",
    )
    for domain, port in ((TAKEOVER_APP, TAKEOVER_APP_PORT), (TAKEOVER_STACK, TAKEOVER_STACK_PORT)):
        created = sc.run(
            f"noust site create -d {domain} -p {port} --no-ssl",
            timeout=300,
            label=f"noust site create -d {domain} -p {port} --no-ssl",
        ).stdout
        sc.check(
            "did not load the site" not in created,
            f"nginx did not load the site of {domain}:\n{created}",
        )
    sc.check(
        sc.run("systemctl is-active nginx", timeout=15, label="is nginx running?").stdout.strip()
        == "active",
        "nginx is not running after the sites were written",
    )
    for domain, needle in served:
        answer = sc.run(
            f"curl -sSi -m 10 -H 'Host: {domain}' http://127.0.0.1/",
            timeout=30,
            label=f"{domain} through nginx",
        ).stdout
        sc.check(
            "Server: nginx" in answer and needle in answer,
            f"{domain} is not served by nginx:\n{answer[:600]}",
        )

    # The units supervise the stacks again, and the verdict says so.
    for domain in (TAKEOVER_APP, TAKEOVER_STACK):
        sc.run(
            f"noust app reclaim {domain} -y", timeout=300, label=f"noust app reclaim {domain} -y"
        )
        verdict = sc.run(
            f"noust diagnose {domain}", timeout=120, check=False, label=f"noust diagnose {domain}"
        ).stdout
        sc.check(
            "Verdict: healthy" in verdict,
            f"{domain} is not healthy once reclaimed:\n{verdict[:800]}",
        )

    # What a reboot does: Docker starts again, restarts what it restarts, and nothing of Coolify
    # comes back.
    # Named before the restart: while the daemon starts again it answers a listing with nothing.
    expected = sorted(_stack_names(sc, app, service))
    sc.run(
        "systemctl restart docker",
        timeout=300,
        label="systemctl restart docker (what a reboot does)",
    )
    sc.run(
        wait_script(
            "[ \"$(docker ps --format '{{.Names}}' | sort | tr '\\n' ' ')\" = "
            f'"{" ".join(expected)} " ]',
            within=120,
        ),
        timeout=180,
        check=False,
        label="wait for the two stacks to be back",
    )
    # Long enough for anything of Coolify's to start its proxy again, which it must not.
    time.sleep(75)
    running = sorted(
        sc.run(
            "docker ps --format '{{.Names}}'", timeout=30, label="what runs after Docker restarted"
        ).stdout.split()
    )
    sc.check(running == expected, f"after a restart of Docker these run: {running}, not {expected}")
    for domain, needle in served:
        sc.run(
            wait_script(
                f"curl -sS -m 10 -H 'Host: {domain}' http://127.0.0.1/ | grep -q '{needle}'",
                within=120,
            ),
            timeout=180,
            label=f"{domain} answers through nginx after the restart",
        )
    sc.check(
        sc.run(
            f"docker exec cache-{service} redis-cli get takeover-proof",
            timeout=30,
            label="the data in the volume, after the restart",
        ).stdout.strip()
        == "survives-the-takeover",
        "the data in the service's volume did not survive a restart of Docker",
    )


def _container_ids(sc: Scenario) -> dict[str, str]:
    """
    Read the id of every running container that is not Coolify's own.

    Args:
        sc: The scenario.

    Returns:
        Name to id.
    """
    listed = sc.run(
        "docker ps --format '{{.Names}} {{.ID}}' | grep -v '^coolify'",
        timeout=30,
        label="the containers of the stacks and their ids",
    ).stdout
    return dict(line.split() for line in listed.splitlines() if line.strip())


def _stack_names(sc: Scenario, app: str, service: str) -> list[str]:
    """
    Name the containers the two stacks are made of.

    Args:
        sc: The scenario.
        app: The application's uuid.
        service: The service's uuid.

    Returns:
        The names: the application's (which Coolify suffixes with a time), the web and the cache.
    """
    named = sc.run(
        f"docker ps -a --filter label=coolify.applicationUuid={app} --format '{{{{.Names}}}}'",
        timeout=30,
        label="the application's container",
    ).stdout.split()
    return [*named, f"web-{service}", f"cache-{service}"]


def _publish_port(sc: Scenario, directory: str, port: int) -> None:
    """
    Make the web service of a Coolify stack publish a loopback port, which Coolify's never do.

    Args:
        sc: The scenario.
        directory: The stack's directory.
        port: The host port, on 127.0.0.1.
    """
    sc.run(
        "python3 - <<'PY'\n"
        "import glob, re\n"
        f"path = glob.glob('{directory}/docker-compose.y*ml')[0]\n"
        "text = open(path).read()\n"
        "web = re.search(r'^( +)container_name: (?:web-|[a-z0-9]{24}-)[^\\n]*\\n', text, re.M)\n"
        "assert web, 'no container_name of the web service'\n"
        f"block = web.group(1) + 'ports:\\n' + web.group(1) + '  - \"127.0.0.1:{port}:80\"\\n'\n"
        "open(path, 'w').write(text[:web.end()] + block + text[web.end():])\n"
        "PY",
        timeout=30,
        label=f"publish 127.0.0.1:{port}->80 in the compose file of {directory}",
    )


scenario("coolify_takeover_of_a_real_server")(scenario_coolify_takeover)

# The recipes pull gigabytes and the takeover installs Coolify: both need the network and take
# many minutes, so a broad pattern does not select them; naming them does.
for _recipe in COMPOSE_RECIPES:
    run.OPTIONAL_SCENARIOS[f"recipe_compose_{_recipe.replace('-', '_')}"] = "recipe_compose"
run.OPTIONAL_SCENARIOS["coolify_takeover_of_a_real_server"] = "coolify_takeover"
