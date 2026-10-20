#!/usr/bin/env python3
"""
The importers against the platforms they read, running for real (checklist N-13).

``noust import --from coolify|caprover|dokku`` reads an application, its storage and its databases
from another platform. The unit tests answer for the shapes the platforms' source says they return;
this harness raises the platforms and asks. It builds the wheel from the working tree, starts one
privileged container with systemd as PID 1 and a Docker of its own (``Dockerfile.systemd``), installs
Noust in it, and for each platform:

* ``coolify``: Coolify 4 installed with its own installer; an application from an image with a
  persistent volume and a file mount, and a PostgreSQL database it connects to, are made through
  Coolify's API. The importer reads them over that API, and the printed dump command is run against
  Coolify's own Postgres container to prove it writes a restorable dump.
* ``dokku``: Dokku's own image, with the Postgres plugin; an application with a database linked, a
  storage mount, a domain and a variable. The importer asks the ``dokku`` command (a one-line wrapper
  that enters the container, as the command would on a Dokku host) and the printed export is run.
* ``caprover``: CapRover's own image on a swarm of one node; its root domain set, an application
  with a persistent directory and a variable, and a database application from an image. The importer
  signs in to its API.

It is not part of pytest (nothing here is named ``test_*``): it drives Docker and the network (the
platforms' installers and images). ``run.py`` is loaded as a module and reused, never edited.
Everything it creates is named ``<prefix>-...`` (``NOUST_IT_PREFIX``) and carries the label
``noust.test=1``; the containers a platform starts live in the harness container's own Docker, never
the host's, and what belongs to anything else is never listed, stopped or removed.

A platform that cannot be raised here is reported as skipped with the reason, never as passed: the
unit tests' doubles are then all that stands behind its importer, and the report says so.

Usage:
    .venv/bin/python tests/integration/importers_real_run.py
    .venv/bin/python tests/integration/importers_real_run.py --scenario dokku
    .venv/bin/python tests/integration/importers_real_run.py --keep --skip-image-build
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import secrets
import shlex
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

INTEGRATION_DIR = Path(__file__).resolve().parent
REPO_ROOT = INTEGRATION_DIR.parents[1]

if TYPE_CHECKING:
    # The real run.py on disk, for the checker; the module itself is loaded below.
    from run import HarnessError as HarnessError
    from run import Scenario as Scenario
    from run import ScenarioSkipped as ScenarioSkipped


def _load_run() -> Any:
    """
    Load tests/integration/run.py as a module, without touching sys.path.

    Returns:
        The loaded module.
    """
    spec = importlib.util.spec_from_file_location(
        "noust_integration_run", INTEGRATION_DIR / "run.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {INTEGRATION_DIR / 'run.py'}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


run = _load_run()
sh = run.sh
docker_exec = run.docker_exec
if not TYPE_CHECKING:
    Scenario = run.Scenario
    HarnessError = run.HarnessError
    ScenarioSkipped = run.ScenarioSkipped

PREFIX = run.RESOURCE_PREFIX
CONTAINER_NAME = f"{PREFIX}-importers"
WORK = "/root/importers"

#: The loopback address every importer is pointed at: Coolify and CapRover publish a port the
#: harness container reaches, and Noust accepts http only there.
LOOPBACK = "127.0.0.1"

FALLBACK_RESOLVERS = ("1.1.1.1", "8.8.8.8")

#: Three rows in a table, so a dump has something a restore listing can name.
SEED_SQL = "create table orders(id int); insert into orders values (1), (2), (3);"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def ensure_inner_dns(sc: Scenario) -> None:
    """
    Give the harness container's own Docker resolvers to fall back on, once.

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


def parse_json(text: str, what: str) -> Any:
    """
    Read the JSON a command printed.

    Args:
        text: Standard output.
        what: What printed it, for the message.

    Returns:
        The document.

    Raises:
        AssertionError: It is not JSON.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"{what} did not print JSON: {text[:400]!r}") from exc


def secret_file(name: str, content: str) -> None:
    """
    Put a throwaway secret in the container as a file only root reads, without a command line.

    Args:
        name: The path in the container.
        content: What it holds.
    """
    with tempfile.TemporaryDirectory() as scratch:
        local = Path(scratch) / "secret"
        local.write_text(content + "\n", encoding="utf-8")
        local.chmod(0o600)
        sh(["docker", "cp", str(local), f"{CONTAINER_NAME}:{name}"], timeout=30)
    docker_exec(CONTAINER_NAME, f"chown root:root {name} && chmod 600 {name}", timeout=15)


def import_proposal(sc: Scenario, arguments: str, label: str) -> dict[str, Any]:
    """
    Run ``noust import --json`` and return the proposal.

    Args:
        sc: The scenario.
        arguments: Everything after ``noust import``.
        label: What it is for.

    Returns:
        The proposal as a dict.
    """
    proc = sc.run(
        f"noust import {arguments} --json",
        timeout=300,
        check=False,
        label=f"noust import {arguments} --json ({label})",
    )
    sc.check(proc.returncode == 0, f"noust import failed ({proc.returncode}): {proc.stderr[-800:]}")
    document = parse_json(proc.stdout, "noust import")
    sc.check(isinstance(document, dict), f"the proposal is not an object: {proc.stdout[:300]!r}")
    return dict(document)


def run_printed_dump(sc: Scenario, command: str, label: str) -> str:
    """
    Run the dump command the importer printed, in a scratch directory, and return the file's path.

    The command ends in ``> NAME.EXT``: the harness runs it as an operator would, where it is, and
    reads the file it wrote.

    Args:
        sc: The scenario.
        command: The command, as the proposal gives it.
        label: What it is for.

    Returns:
        The path of the file it wrote.
    """
    name = command.rsplit("> ", 1)[-1].strip()
    sc.check(
        bool(name) and "/" not in name and " " not in name,
        f"the dump command does not end in a file name: {command!r}",
    )
    made = sc.run(
        f"cd {WORK} && rm -f {name} && {command} && wc -c < {name}",
        timeout=180,
        label=f"{command} ({label})",
    )
    sc.check(int(made.stdout.strip() or 0) > 200, f"{name} is {made.stdout.strip()} bytes")
    return f"{WORK}/{name}"


def storage_of(sc: Scenario, proposal: dict[str, Any], mount_path: str) -> dict[str, Any]:
    """Find the storage the proposal lists for a mount path, or fail with what it lists."""
    found = [s for s in proposal["storage"] if s["mount_path"] == mount_path]
    listed = [s["mount_path"] for s in proposal["storage"]]
    sc.check(len(found) == 1, f"storage at {mount_path!r}: the proposal lists {listed}")
    return dict(found[0])


def database_of(sc: Scenario, proposal: dict[str, Any], engine: str) -> dict[str, Any]:
    """Find the database of an engine in the proposal, or fail with what it lists."""
    found = [d for d in proposal["database_details"] if d["engine"] == engine]
    listed = [(d["engine"], d["name"]) for d in proposal["database_details"]]
    sc.check(len(found) == 1, f"{engine} databases: the proposal lists {listed}")
    return dict(found[0])


# ---------------------------------------------------------------------------
# Coolify
# ---------------------------------------------------------------------------

#: The release of Coolify installed. The installer is fetched live and takes the version as its
#: argument; what is verified here is this release, and a newer one is a new run.
COOLIFY_VERSION = "4.4.6"
COOLIFY_INSTALLER = "https://cdn.coollabs.io/coolify/install.sh"
COOLIFY_WORK = "/root/coolify-dl"
COOLIFY_API = f"http://{LOOPBACK}:8000/api/v1"
COOLIFY_URL = f"http://{LOOPBACK}:8000"

#: Makes the root user the installer leaves unseeded, enables the API and makes a token the scenario
#: keeps in a file only root reads.
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


def coolify_api(
    sc: Scenario,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    label: str,
    quiet: bool = False,
) -> Any:
    """
    Call Coolify's API from inside the harness container, as the root token the scenario made.

    The body is not in the evidence (it may hold a database password), and the token never is.

    Args:
        sc: The scenario.
        method: The HTTP method.
        path: The path under ``/api/v1``.
        body: A JSON document to send, or None.
        label: What the call is for, which is what the evidence shows.
        quiet: Keep the answer out of the evidence (a server's settings hold a token of its own).

    Returns:
        The JSON it answered; an empty object when the answer is not JSON.
    """
    data = (
        f"-H 'Content-Type: application/json' -d {shlex.quote(json.dumps(body))} " if body else ""
    )
    proc = sc.run(
        f'curl -sS -m 120 -X {method} -H "Authorization: Bearer $(cat {COOLIFY_WORK}/api-token)" '
        f"{data}{COOLIFY_API}{path}",
        timeout=180,
        label=f"Coolify API: {method} {path} ({label})",
    )
    shown = "(the answer is not kept)" if quiet else proc.stdout.strip()[:300]
    sc.evidence[-1] = f"$ Coolify API: {method} {path} ({label})\n{shown}"
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
        wait_script(
            f"curl -fsS -m 5 http://{LOOPBACK}:8000/api/health >/dev/null 2>&1", within=300
        ),
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


def scenario_coolify(sc: Scenario) -> None:
    """
    A real Coolify: an application, a volume, a file, a database it connects to, imported.

    What this proves for real: the storages endpoint answers the shape the importer reads, the
    database is found by the variable that names it, the proposal carries no password, and the dump
    command the importer prints writes a dump that Postgres itself can list.
    """
    run.require_docker(sc)
    ensure_inner_dns(sc)
    password = secrets.token_hex(12)
    sc.run(
        f"{run.INNER_DOCKER_CLEAR}; rm -rf /data/coolify {COOLIFY_WORK}; true",
        timeout=300,
        check=False,
        label="(clean start) no container, no Coolify",
    )
    try:
        server = install_coolify(sc)
        project = coolify_api(sc, "POST", "/projects", body={"name": "onb"}, label="a project")[
            "uuid"
        ]
        app = coolify_api(
            sc,
            "POST",
            "/applications/dockerimage",
            body={
                "project_uuid": project,
                "server_uuid": server,
                "environment_name": "production",
                "docker_registry_image_name": "traefik/whoami",
                "docker_registry_image_tag": "v1.10.3",
                "ports_exposes": "80",
                "domains": "http://shop.onb.test",
                "name": "shop",
                "instant_deploy": False,
            },
            label="an application from an image",
        )["uuid"]
        coolify_api(
            sc,
            "POST",
            f"/applications/{app}/storages",
            body={"type": "persistent", "name": "uploads", "mount_path": "/data/uploads"},
            label="a persistent volume",
        )
        coolify_api(
            sc,
            "POST",
            f"/applications/{app}/storages",
            body={"type": "file", "mount_path": "/etc/shop/app.conf", "content": "mode=live\n"},
            label="a file mount",
        )
        database = coolify_api(
            sc,
            "POST",
            "/databases/postgresql",
            body={
                "project_uuid": project,
                "server_uuid": server,
                "environment_name": "production",
                "name": "shopdb",
                "postgres_user": "shop",
                "postgres_password": password,
                "postgres_db": "shop",
                "instant_deploy": True,
            },
            label="a PostgreSQL database, started",
        )["uuid"]
        sc.run(
            wait_script(
                f"docker exec {database} pg_isready -U shop -d shop >/dev/null 2>&1", within=300
            ),
            timeout=400,
            label="wait for Coolify's Postgres to accept connections",
        )
        sc.run(
            f"docker exec {database} psql -U shop -d shop -c {shlex.quote(SEED_SQL)}",
            timeout=60,
            label="three rows in the database",
        )
        coolify_api(
            sc,
            "POST",
            f"/applications/{app}/envs",
            body={
                "key": "DATABASE_URL",
                "value": f"postgres://shop:{password}@{database}:5432/shop",
            },
            label="the variable that connects the application to the database",
        )

        listed = sc.run(
            f"noust import --from coolify --url {COOLIFY_URL} --token-file {COOLIFY_WORK}/api-token --json",
            timeout=120,
            label="noust import --from coolify --url ... --token-file ... --json (the list)",
        )
        names = [item["name"] for item in parse_json(listed.stdout, "the list")]
        sc.check("shop" in names, f"the list does not show the application: {names}")

        proposal = import_proposal(
            sc,
            f"--from coolify --url {COOLIFY_URL} --token-file {COOLIFY_WORK}/api-token --app shop",
            "the application",
        )
        sc.check(password not in json.dumps(proposal), "the proposal carries the database password")
        volume = storage_of(sc, proposal, "/data/uploads")
        sc.check(bool(volume.get("export")), "the volume has no export command")
        file_mount = storage_of(sc, proposal, "/etc/shop/app.conf")
        sc.check(file_mount is not None, "the file mount is not listed")
        pg = database_of(sc, proposal, "postgresql")
        sc.check(pg["database"] == "shop" and pg["user"] == "shop", f"the database: {pg}")
        sc.check(pg["linked"] is True, "the database is not marked as linked by DATABASE_URL")
        sc.check(
            database in str(pg["reference"]) + str(pg["dump"]),
            "the dump does not name the container",
        )
        sc.check(password not in str(pg["dump"]), "the dump command carries the password")

        dumped = run_printed_dump(sc, str(pg["dump"]), "run on the Coolify server")
        contents = sc.run(
            f"docker exec -i {database} pg_restore --list < {dumped} | grep -i orders",
            timeout=60,
            label="pg_restore --list of that dump (it names the table)",
        )
        sc.check("orders" in contents.stdout, "the dump does not hold the table")
    finally:
        sc.run(
            f"{run.INNER_DOCKER_CLEAR}; rm -rf /data/coolify {COOLIFY_WORK}; "
            "sed -i '/coolify/d' /root/.ssh/authorized_keys; systemctl enable --now nginx; true",
            timeout=600,
            check=False,
            label="cleanup: clear Docker, forget Coolify, nginx back",
        )


# ---------------------------------------------------------------------------
# Dokku
# ---------------------------------------------------------------------------

DOKKU_IMAGE = "dokku/dokku:latest"
DOKKU_CONTAINER = f"{PREFIX}-dokku"

#: What stands for "the dokku command of this machine": it enters the container, as the command
#: does on a Dokku host (whose command is a wrapper around the same thing).
DOKKU_WRAPPER = f'#!/bin/sh\nexec docker exec -i {DOKKU_CONTAINER} dokku "$@"\n'


def scenario_dokku(sc: Scenario) -> None:
    """
    A real Dokku: an application with a database linked, a storage mount, a domain, a variable.

    What this proves for real: the importer's argv answers on a real Dokku (``apps:list``, the
    ``*:report`` flags, ``config:export``), the database is found through its plugin, and the export
    command it prints writes a dump Postgres can list.
    """
    run.require_docker(sc)
    ensure_inner_dns(sc)
    sc.run(
        f"{run.INNER_DOCKER_CLEAR}; rm -rf /var/lib/dokku; true",
        timeout=300,
        check=False,
        label="(clean start) no container, no Dokku data",
    )
    try:
        sc.run(
            f"mkdir -p /var/lib/dokku && docker run -d --name {DOKKU_CONTAINER} "
            "--env DOKKU_HOSTNAME=dokku.onb.test "
            "--volume /var/lib/dokku:/mnt/dokku "
            "--volume /var/run/docker.sock:/var/run/docker.sock "
            f"{DOKKU_IMAGE}",
            timeout=900,
            label=f"docker run -d --name {DOKKU_CONTAINER} {DOKKU_IMAGE} (Dokku's own image)",
        )
        sc.run(
            wait_script(f"docker exec {DOKKU_CONTAINER} dokku version >/dev/null 2>&1", within=300),
            timeout=400,
            label="wait for Dokku to answer",
        )
        sc.run(f"docker exec {DOKKU_CONTAINER} dokku version", timeout=30, label="dokku version")
        sc.run(
            f"docker exec {DOKKU_CONTAINER} dokku plugin:install "
            "https://github.com/dokku/dokku-postgres.git postgres",
            timeout=900,
            label="dokku plugin:install dokku-postgres",
        )
        for command in (
            "apps:create shop",
            "postgres:create shopdb",
            "postgres:link shopdb shop --no-restart",
            "domains:add shop shop.onb.test",
            "config:set --no-restart shop MODE=live",
            "storage:ensure-directory shop",
            "storage:mount shop /var/lib/dokku/data/storage/shop:/app/uploads",
        ):
            sc.run(
                f"docker exec {DOKKU_CONTAINER} dokku {command}",
                timeout=600,
                label=f"dokku {command}",
            )
        sc.run(
            "docker exec dokku.postgres.shopdb psql -U postgres -d shopdb -c "
            '"create table orders(id int); insert into orders values (1), (2), (3);"',
            timeout=120,
            label="three rows in the database",
        )
        # The wrapper stands for the dokku command of a Dokku host.
        secret_file("/usr/local/bin/dokku", DOKKU_WRAPPER.rstrip("\n"))
        sc.run("chmod 755 /usr/local/bin/dokku", timeout=15, label="chmod 755 /usr/local/bin/dokku")

        listed = sc.run(
            "noust import --from dokku --json",
            timeout=120,
            label="noust import --from dokku --json",
        )
        names = [item["name"] for item in parse_json(listed.stdout, "the list")]
        sc.check("shop" in names, f"the list does not show the application: {names}")

        proposal = import_proposal(sc, "--from dokku --app shop", "the application")
        sc.check("shop.onb.test" in proposal["domains"], f"domains: {proposal['domains']}")
        mount = storage_of(sc, proposal, "/app/uploads")
        sc.check(
            mount["host_path"] == "/var/lib/dokku/data/storage/shop",
            f"the mount's host path is {mount['host_path']!r}",
        )
        pg = database_of(sc, proposal, "postgresql")
        sc.check(pg["name"] == "shopdb", f"the database is {pg['name']!r}")
        sc.check(pg["linked"] is True, "the linked database is not marked as linked")
        sc.check("postgres:export" in str(pg["dump"]), f"the dump is {pg['dump']!r}")

        dumped = run_printed_dump(sc, str(pg["dump"]), "the export, run on the Dokku host")
        contents = sc.run(
            f"docker exec -i dokku.postgres.shopdb pg_restore --list < {dumped} | grep -i orders",
            timeout=60,
            label="pg_restore --list of that export (it names the table)",
        )
        sc.check("orders" in contents.stdout, "the export does not hold the table")
    finally:
        sc.run(
            f"rm -f /usr/local/bin/dokku; {run.INNER_DOCKER_CLEAR}; rm -rf /var/lib/dokku; true",
            timeout=600,
            check=False,
            label="cleanup: the wrapper, Docker, Dokku's data",
        )


# ---------------------------------------------------------------------------
# CapRover
# ---------------------------------------------------------------------------

CAPROVER_IMAGE = "caprover/caprover:latest"
CAPROVER_URL = f"http://{LOOPBACK}:3000"
CAPROVER_ROOT = "onb.localtest.me"


def caprover_api(
    sc: Scenario,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    token: str | None = None,
    label: str,
) -> dict[str, Any]:
    """
    Call CapRover's API from inside the harness container.

    CapRover answers HTTP 200 with a ``status`` in the body, whatever happened (1000 is success).

    Args:
        sc: The scenario.
        method: The HTTP method.
        path: The path under ``/api/v2``.
        body: A JSON document to send, or None.
        token: The authentication token from the login, or None.
        label: What the call is for, which is what the evidence shows.

    Returns:
        The document it answered.
    """
    data = (
        f"-H 'Content-Type: application/json' -d {shlex.quote(json.dumps(body))} " if body else ""
    )
    auth = f"-H 'x-captain-auth: {token}' " if token else ""
    proc = sc.run(
        f"curl -sS -m 120 -X {method} -H 'x-namespace: captain' {auth}{data}{CAPROVER_URL}/api/v2{path}",
        timeout=180,
        check=False,
        label=f"CapRover API: {method} {path} ({label})",
    )
    sc.evidence[-1] = f"$ CapRover API: {method} {path} ({label})\n(the answer is not kept)"
    document = parse_json(proc.stdout, f"CapRover {path}")
    sc.check(isinstance(document, dict), f"CapRover {path} answered {proc.stdout[:200]!r}")
    return dict(document)


def scenario_caprover(sc: Scenario) -> None:
    """
    A real CapRover: an application with a persistent directory and a variable, and a database app.

    What this proves for real: the sign-in and the application definitions endpoint answer what the
    importer reads, a database application is recognised by its image and linked by the variable that
    holds its ``srv-captain--`` name, and the dump command it prints works against the service.
    """
    run.require_docker(sc)
    ensure_inner_dns(sc)
    password = (
        "captain42"  # CapRover's documented first-run password, valid only until it is changed
    )
    sc.run(
        f"{run.INNER_DOCKER_CLEAR}; docker swarm leave --force >/dev/null 2>&1; rm -rf /captain; true",
        timeout=300,
        check=False,
        label="(clean start) no container, no swarm, no CapRover data",
    )
    try:
        sc.run(
            "systemctl disable --now nginx; docker swarm init --advertise-addr 127.0.0.1",
            timeout=120,
            check=False,
            label="(a CapRover server) no nginx; a swarm of one node",
        )
        started = sc.run(
            "mkdir -p /captain && docker run -d --name captain-boot -p 80:80 -p 443:443 -p 3000:3000 "
            "-e ACCEPTED_TERMS=true -v /var/run/docker.sock:/var/run/docker.sock "
            f"-v /captain:/captain {CAPROVER_IMAGE}",
            timeout=900,
            check=False,
            label=f"docker run -d {CAPROVER_IMAGE} (CapRover's own image)",
        )
        if started.returncode != 0:
            raise ScenarioSkipped(f"CapRover's image did not start here: {started.stderr[-300:]}")
        up = sc.run(
            wait_script(
                f"curl -fsS -m 5 -H 'x-namespace: captain' {CAPROVER_URL}/api/v2/user/system/info "
                ">/dev/null 2>&1 || curl -sS -m 5 "
                f"{CAPROVER_URL}/api/v2/login -d '{{}}' -H 'Content-Type: application/json' | grep -q status",
                within=300,
            ),
            timeout=400,
            check=False,
            label="wait for CapRover to answer on :3000",
        )
        if up.returncode != 0:
            raise ScenarioSkipped("CapRover did not answer on :3000 in five minutes")
        login = caprover_api(
            sc,
            "POST",
            "/login",
            body={"password": password},
            label="sign in with the first-run password",
        )
        if login.get("status") != 100:
            raise ScenarioSkipped(
                f"CapRover refused the first-run sign-in: status {login.get('status')}"
            )
        token = str(login["data"]["token"])
        changed = caprover_api(
            sc,
            "POST",
            "/user/system/changerootdomain",
            body={"rootDomain": CAPROVER_ROOT},
            token=token,
            label="the root domain (a name that resolves to this machine)",
        )
        if changed.get("status") != 100:
            raise ScenarioSkipped(
                f"CapRover would not take the root domain {CAPROVER_ROOT}: status {changed.get('status')}"
            )
        for name in ("shop", "shopdb"):
            registered = caprover_api(
                sc,
                "POST",
                "/user/apps/appDefinitions/register",
                body={"appName": name, "hasPersistentData": name == "shopdb"},
                token=token,
                label=f"register the app {name}",
            )
            sc.check(
                registered.get("status") == 100, f"registering {name}: {registered.get('status')}"
            )
        # The two apps' definitions are changed through the same document the dashboard sends.
        definitions = caprover_api(
            sc, "GET", "/user/apps/appDefinitions", token=token, label="the apps as they are"
        )["data"]["appDefinitions"]
        for definition in definitions:
            if definition["appName"] == "shop":
                definition["envVars"] = [
                    {"key": "MODE", "value": "live"},
                    {
                        "key": "DATABASE_URL",
                        "value": "postgres://shop@srv-captain--shopdb:5432/shop",
                    },
                ]
                definition["volumes"] = [
                    {"containerPath": "/app/uploads", "volumeName": "shop-uploads"}
                ]
                definition["customDomain"] = []
            if definition["appName"] == "shopdb":
                definition["envVars"] = [
                    {"key": "POSTGRES_USER", "value": "shop"},
                    {"key": "POSTGRES_DB", "value": "shop"},
                    {"key": "POSTGRES_HOST_AUTH_METHOD", "value": "trust"},
                ]
                definition["volumes"] = [
                    {"containerPath": "/var/lib/postgresql/data", "volumeName": "shopdb-data"}
                ]
                definition["notExposeAsWebApp"] = True
            if definition["appName"] in ("shop", "shopdb"):
                updated = caprover_api(
                    sc,
                    "POST",
                    "/user/apps/appDefinitions/update",
                    body=definition,
                    token=token,
                    label=f"update the definition of {definition['appName']}",
                )
                sc.check(
                    updated.get("status") == 100, f"updating {definition['appName']}: {updated}"
                )
        deployed = caprover_api(
            sc,
            "POST",
            "/user/apps/appData/shopdb/?detached=1",
            body={
                "captainDefinitionContent": json.dumps(
                    {"schemaVersion": 2, "imageName": "postgres:16-alpine"}
                ),
                "gitHash": "",
            },
            token=token,
            label="deploy postgres:16-alpine as the app shopdb",
        )
        sc.check(deployed.get("status") == 100, f"deploying shopdb: {deployed.get('status')}")
        sc.run(
            wait_script(
                "docker ps --filter name=srv-captain--shopdb --format '{{.Names}}' | grep -q srv-captain--shopdb",
                within=400,
            ),
            timeout=500,
            label="wait for the service of shopdb",
        )
        sc.run(
            wait_script(
                "docker exec $(docker ps -qf name=^srv-captain--shopdb\\.) pg_isready -U shop -d shop "
                ">/dev/null 2>&1",
                within=200,
            ),
            timeout=300,
            label="wait for CapRover's Postgres to accept connections",
        )
        sc.run(
            "docker exec $(docker ps -qf name=^srv-captain--shopdb\\.) psql -U shop -d shop -c "
            '"create table orders(id int); insert into orders values (1), (2), (3);"',
            timeout=60,
            label="three rows in the database",
        )
        secret_file(f"{WORK}/caprover-password", password)
        proposal = import_proposal(
            sc,
            f"--from caprover --url {CAPROVER_URL} --token-file {WORK}/caprover-password --app shop",
            "the application",
        )
        sc.check(
            any(s["mount_path"] == "/app/uploads" for s in proposal["storage"]),
            f"storage: {[s['mount_path'] for s in proposal['storage']]}",
        )
        pg = database_of(sc, proposal, "postgresql")
        sc.check(pg["linked"] is True, "the database is not marked as linked by DATABASE_URL")
        dumped = run_printed_dump(sc, str(pg["dump"]), "run on the CapRover server")
        contents = sc.run(
            f"docker exec -i $(docker ps -qf name=^srv-captain--shopdb\\.) pg_restore --list < {dumped} "
            "| grep -i orders",
            timeout=60,
            label="pg_restore --list of that dump (it names the table)",
        )
        sc.check("orders" in contents.stdout, "the dump does not hold the table")
    finally:
        sc.run(
            f"docker service ls -q | xargs -r docker service rm >/dev/null 2>&1; {run.INNER_DOCKER_CLEAR}; "
            "docker swarm leave --force >/dev/null 2>&1; rm -rf /captain; systemctl enable --now nginx; true",
            timeout=600,
            check=False,
            label="cleanup: services, containers, swarm, CapRover's data, nginx back",
        )


SCENARIOS: list[tuple[str, Callable[[Any], None]]] = [
    ("coolify_import", scenario_coolify),
    ("dokku_import", scenario_dokku),
    ("caprover_import", scenario_caprover),
]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@dataclass
class Outcome:
    """What a run did."""

    attempted: int = 0
    failures: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--keep", action="store_true", help="Leave the container running.")
    parser.add_argument(
        "--scenario",
        action="append",
        default=None,
        metavar="SUBSTRING",
        help="Run only the scenarios whose name contains this. May repeat.",
    )
    parser.add_argument("--skip-wheel-build", action="store_true", help="Reuse the newest wheel.")
    parser.add_argument(
        "--skip-image-build", action="store_true", help="Reuse the image if it exists."
    )
    parser.add_argument("--remove-image", action="store_true", help="Remove the image afterwards.")
    return parser.parse_args()


def run_scenarios(to_run: list[tuple[str, Callable[[Any], None]]]) -> Outcome:
    """
    Run scenarios and print each one's evidence.

    Args:
        to_run: The scenarios.

    Returns:
        What happened.
    """
    outcome = Outcome()
    for name, fn in to_run:
        print(f"\n=== {name} ===")
        outcome.attempted += 1
        sc = Scenario(container=CONTAINER_NAME)
        try:
            fn(sc)
        except ScenarioSkipped as exc:
            outcome.skipped.append((name, str(exc)))
            print(f"SKIP: {name}: {exc}")
        except AssertionError as exc:
            outcome.failures += 1
            outcome.failed.append(name)
            print(f"FAIL: {name}: {exc}")
        except (HarnessError, subprocess.TimeoutExpired) as exc:
            outcome.failures += 1
            outcome.failed.append(name)
            print(f"FAIL: {name} (command error): {exc}")
        else:
            outcome.passed.append(name)
            print(f"PASS: {name}")
        finally:
            if sc.evidence:
                print("--- evidence ---")
                print("\n\n".join(sc.evidence))
    return outcome


def main() -> int:
    args = parse_args()
    started_at = time.monotonic()
    workdir = INTEGRATION_DIR / ".build"
    workdir.mkdir(exist_ok=True)

    if args.skip_wheel_build:
        wheels = sorted((workdir / "dist").glob("noust-*.whl"))
        if not wheels:
            raise HarnessError("--skip-wheel-build given but no wheel exists yet")
        wheel = wheels[-1]
        print(f"[setup] reusing existing wheel {wheel.name}")
    else:
        wheel = run.build_wheel(workdir)

    have = sh(["docker", "image", "inspect", run.IMAGE_TAG], check=False)
    if have.returncode != 0 or not args.skip_image_build:
        run.build_image()

    wanted = args.scenario
    to_run = [(n, fn) for n, fn in SCENARIOS if not wanted or any(part in n for part in wanted)]

    outcome = Outcome()
    setup_error: str | None = None
    run.remove_container(CONTAINER_NAME)
    try:
        try:
            run.start_container(CONTAINER_NAME)
            run.wait_for_systemd(CONTAINER_NAME)
            run.install_noust(CONTAINER_NAME, wheel)
            run.prepare_inner_docker(CONTAINER_NAME)
            docker_exec(CONTAINER_NAME, f"mkdir -p {WORK}", timeout=15)
        except HarnessError as exc:
            setup_error = str(exc)
        else:
            print(f"\n[scenarios] running {len(to_run)}: {[n for n, _ in to_run]}")
            outcome = run_scenarios(to_run)
    finally:
        if args.keep:
            print(f"\n[teardown] --keep given, leaving {CONTAINER_NAME} running")
        else:
            print(f"\n[teardown] removing {CONTAINER_NAME}")
            run.remove_container(CONTAINER_NAME)
        if args.remove_image:
            subprocess.run(
                ["docker", "rmi", run.IMAGE_TAG], capture_output=True, text=True, timeout=120
            )

    elapsed = time.monotonic() - started_at
    if setup_error is not None:
        print(f"\n[setup] FAILED before any scenario ran: {setup_error}")
        return 1
    for name, reason in outcome.skipped:
        print(f"[skipped] {name}: {reason}")
    print(f"\n[passed] {outcome.passed}")
    print(f"[failed] {outcome.failed}")
    print(
        f"\n[summary] {outcome.attempted} scenario(s) run, {outcome.failures} failure(s), "
        f"{len(outcome.skipped)} skipped, {elapsed:.1f}s total"
    )
    return 1 if outcome.failures else 0


if __name__ == "__main__":
    sys.exit(main())
