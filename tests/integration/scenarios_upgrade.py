"""
The upgrade from Noust 3.3.1 to this tree, on a server that carries every legacy name.

``python tests/integration/run.py --upgrade-from-3.3`` starts a container, installs the ``v3.3.1`` tag
from a git checkout (not from PyPI: it is the tag's own tree), and seeds a server the way the
production servers look after years of releases: an application whose unit still has the ``wasm-``
prefix of before 0.14.1, a blue/green application whose upstream is ``wasm_bg_<app>`` and an
operator's hand-written site that proxies to it, a Docker Compose stack on the relay, a
``wasm_ro_`` read-only database role, a ``wasm-backup`` archive in the ``wasm-backups`` folder of
an S3 bucket, and an API token. It then installs this tree's wheel over it, as the packages do, and
checks that every application stays healthy.

Two kinds of check:

- the **hard** ones are what the upgrade must not break: every application serves, nothing is
  restarted by the upgrade, the operator's site still loads, the old backup restores, the old role
  still reads, the old token still signs in, an update of each application works. The cookies of a
  browser signed in under 3.3.1 are the one thing that does not survive, on purpose: the console
  asks to sign in once;
- the **wave 5** ones state what the rename (4.0) must leave: the unit renamed at its next deploy,
  the generated upstreams under their new name with the old one defined beside it only for the
  operator's site that names it (never rewritten, named by ``noust setup doctor`` with its line),
  the whole thing completed by ``noust setup rename`` once the operator has changed their line,
  the read-only role renamed, the next backup in the new folder with the old ones still listed,
  the tree and the image tags renamed, ``noust health`` naming what only the operator can finish.
  They were expected failures until the rename landed (:data:`WAVE5_DONE`); they are hard now.

Docker: MinIO and the Compose stack run in the harness container's own Docker daemon.
"""

from __future__ import annotations

import re
import secrets
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import run
import scenarios_34
from run import (
    DEPLOY_TIMEOUT,
    REPO_ROOT,
    HarnessError,
    Scenario,
    commit_to,
    json_of,
    make_node_repo,
    sh,
    start_container,
    wait_for_systemd,
)

#: The tag the server is seeded with.
FROM_TAG = "v3.3.1"

#: Flip to True when wave 5 (the rename of every ``wasm`` name) has landed: the expectations
#: that are reported as expected failures until then become failures of the run.
WAVE5_DONE = True

LEGACY_DOMAIN = "legacy.test"
LEGACY_APP = "legacy-test"
LEGACY_UNIT = f"wasm-{LEGACY_APP}"
LEGACY_DB = "legacydb"

BG_DOMAIN = "bgapp.test"
BG_APP = "bgapp-test"
BG_UPSTREAM = "wasm_bg_bgapp_test"
BG_UPSTREAM_NEW = "noust_bg_bgapp_test"
STACK_UPSTREAM = "wasm_bg_stack_test_web"

#: An application created after the upgrade from the repository that has wasm.nginx.yaml.
YAML_DOMAIN = "yamlnote.test"

EDGE_DOMAIN = "edge.test"
EDGE_SITE = "/etc/nginx/sites-available/edge"
EDGE_TEXT = f"""# Written by hand, long before the applications behind it were deployed by Noust.
server {{
    listen 80;
    listen [::]:80;
    server_name {EDGE_DOMAIN};

    location / {{
        proxy_pass http://{BG_UPSTREAM};
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
    }}
}}
"""

STACK_DOMAIN = "stack.test"
STACK_PORT = 18301
STACK_APP = "stack-test"

S3_NAME = "noust-it-minio-legacy"
S3_PORT = 19001
S3_USER = "noustit"
S3_DESTINATION = "legacy-s3"
S3_BUCKET = "wasm-backups"

TOKEN_NAME = "legacy-admin-token"
WASM_TOKEN_NAME = "legacy-wasm-token"
COOKIE_JAR = "/root/legacy.cookies"

#: Mints a token the way 2.x did (``wasm_tok_`` prefix, stored hashed), with the code of the
#: installed Noust: 3.x only ever issues ``noust_tok_`` and still accepts the old prefix, so the
#: only way to have one on a seeded server is to store one. It is written to a file, not printed.
MAKE_WASM_TOKEN = f"""/opt/noust/bin/python -I - <<'PY'
import secrets, time
from noust.cli.web_state import token_manager
manager = token_manager()
token = "wasm_tok_" + secrets.token_urlsafe(32)
manager.sessions.create_api_token(
    "{WASM_TOKEN_NAME}", manager._hash_token(token), "admin", time.time(), None, created_by="seed"
)
open("/root/legacy-wasm-token", "w").write(token)
PY
chmod 600 /root/legacy-wasm-token"""

#: The operator's site for the Compose stack, the way Proggest's is: its own upstream block that
#: includes the servers file of Noust's relay instead of naming a port.
STACK_EDGE_DOMAIN = "stack-edge.test"
STACK_EDGE_SITE = "/etc/nginx/sites-available/stack-edge"
STACK_EDGE_TEXT = f"""# Proggest-style: an upstream of the operator's own that includes the relay's servers file.
upstream stack_edge_upstream {{
    include /etc/nginx/noust-upstreams/{STACK_APP}/web.servers;
}}

server {{
    listen 80;
    listen [::]:80;
    server_name {STACK_EDGE_DOMAIN};

    location / {{
        proxy_pass http://stack_edge_upstream;
        proxy_set_header Host $host;
    }}
}}
"""


@dataclass
class Wave5:
    """What the rename must leave, judged without failing the run until wave 5 has landed."""

    results: list[tuple[bool, str]] = field(default_factory=list)

    def expect(self, holds: bool, what: str, detail: str = "") -> None:
        """Record one expectation.

        Args:
            holds: Whether it holds now.
            what: The expectation, as a sentence.
            detail: What was found, for a failure.
        """
        self.results.append((holds, what + (f" ({detail})" if detail and not holds else "")))

    def report(self, sc: Scenario) -> list[str]:
        """Say how the expectations stand; return the ones that fail when wave 5 is done.

        Args:
            sc: The scenario, whose evidence gets the lines.

        Returns:
            The failures to raise: empty until :data:`WAVE5_DONE`.
        """
        lines = []
        for holds, what in self.results:
            if holds:
                lines.append(f"{'PASS' if WAVE5_DONE else 'XPASS'}: {what}")
            else:
                lines.append(f"{'FAIL' if WAVE5_DONE else 'XFAIL'}: {what}")
        sc.evidence.append("[wave 5] what the rename must leave:\n" + "\n".join(lines))
        held = sum(1 for holds, _ in self.results if holds)
        print(
            f"[wave 5] {held} of {len(self.results)} expectation(s) already hold; "
            f"{'the rest fail the run' if WAVE5_DONE else 'the rest are expected to fail until it runs'}"
        )
        for line in lines:
            print(f"[wave 5]   {line}")
        if not WAVE5_DONE:
            return []
        return [what for holds, what in self.results if not holds]


def install_from_git(name: str, tag: str) -> str:
    """Install a tag of this repository into ``/opt/noust`` from a git checkout in the container.

    Args:
        name: The container.
        tag: The tag.

    Returns:
        The commit the tag names, as the container's checkout says it.
    """
    with tempfile.TemporaryDirectory(prefix="noust-it-upgrade-") as tmp:
        source = Path(tmp) / "noust-src"
        sh(
            [
                "git",
                "clone",
                "-q",
                "--depth",
                "1",
                "--branch",
                tag,
                f"file://{REPO_ROOT}",
                str(source),
            ],
            timeout=300,
        )
        sh(["docker", "cp", str(source), f"{name}:/root/noust-src"], timeout=300)
    run.docker_exec(name, "chown -R root:root /root/noust-src", timeout=60)
    commit = run.docker_exec(
        name, "git -c safe.directory='*' -C /root/noust-src rev-parse HEAD", timeout=30
    ).stdout.strip()
    described = run.docker_exec(
        name, "git -c safe.directory='*' -C /root/noust-src describe --tags", timeout=30
    ).stdout.strip()
    if described != tag:
        raise HarnessError(f"the checkout is at {described!r}, not {tag}")
    print(f"[setup] installing {tag} ({commit[:10]}) from the git checkout in the container")
    run.docker_exec(name, "python3 -m venv /opt/noust", timeout=60)
    run.docker_exec(name, "/opt/noust/bin/pip install --quiet --upgrade pip", timeout=120)
    run.docker_exec(
        name,
        "/opt/noust/bin/pip install --quiet '/root/noust-src[all]'",
        timeout=run.PIP_INSTALL_TIMEOUT,
    )
    run.docker_exec(name, "ln -sf /opt/noust/bin/noust /usr/local/bin/noust", timeout=15)
    run.docker_exec(name, "ln -sf /opt/noust/bin/wasm /usr/local/bin/wasm", timeout=15)
    version = run.docker_exec(name, "noust --version", timeout=30).stdout.strip()
    print(f"[setup] installed {version}")
    return commit


def listed_domains(listing: dict[str, Any]) -> list[str]:
    """The domains ``noust list --json`` lists, sorted."""
    return sorted(str(item["domain"]) for item in listing["items"])


def body_of(sc: Scenario, host: str, label: str) -> str:
    """What nginx answers ``host`` on ``/``."""
    return sc.run(
        f"curl -sS -m 10 -H 'Host: {host}' http://127.0.0.1/",
        timeout=30,
        check=False,
        label=f"{label}: curl -H 'Host: {host}' http://127.0.0.1/",
    ).stdout.strip()


def units_state(sc: Scenario, units: list[str], label: str) -> dict[str, tuple[str, str]]:
    """``(ActiveState, MainPID)`` of units: a pid that changes is a restart."""
    state: dict[str, tuple[str, str]] = {}
    for unit in units:
        out = sc.run(
            f"systemctl show -p ActiveState -p MainPID --value {unit} | paste -sd' '",
            timeout=15,
            check=False,
            label=f"{label}: {unit}",
        ).stdout.split()
        state[unit] = (out[0], out[1]) if len(out) == 2 else ("unknown", "0")
    return state


def wait_page(sc: Scenario, host: str, expected: str, label: str, seconds: int = 120) -> None:
    """Wait for a page to say ``expected``, or fail with what it said."""
    deadline = time.monotonic() + seconds
    body = ""
    while time.monotonic() < deadline:
        body = run.docker_exec(
            sc.container,
            f"curl -sS -m 10 -H 'Host: {host}' http://127.0.0.1/",
            timeout=30,
            check=False,
        ).stdout.strip()
        if expected in body:
            break
        time.sleep(2)
    sc.evidence.append(f"$ {label}: until {host} says {expected!r}\n{body[:300]}")
    sc.check(expected in body, f"{label}: {host} answered {body[:200]!r}, not {expected!r}")


@dataclass
class Seeded:
    """What the seeding left behind that the checks need."""

    node_repo: str
    bg_repo: str
    stack_repo: str
    backup_ids: list[str]
    token: str
    wasm_token: str
    master: str
    units: list[str]


def main_pid(sc: Scenario, unit: str) -> str:
    """The MainPID systemd reports for a unit: a different one after a restart."""
    return sc.run(
        f"systemctl show -p MainPID --value {unit}",
        timeout=15,
        check=False,
        label=f"MainPID of {unit}",
    ).stdout.strip()


def resolves_legacy_unit(sc: Scenario, who: str) -> None:
    """Check that ``noust restart`` finds an application whose unit has the legacy prefix.

    The command is asked to act on the application and the effect is read in systemd: the
    ``wasm-`` unit is the one that restarts, and no unit of the plain name appears beside it.

    Args:
        sc: The scenario.
        who: Which Noust is asked, for the evidence.
    """
    before = main_pid(sc, LEGACY_UNIT)
    sc.run(
        f"noust restart {LEGACY_DOMAIN}",
        timeout=120,
        label=f"{who} noust restart {LEGACY_DOMAIN} (its unit is {LEGACY_UNIT})",
    )
    after = main_pid(sc, LEGACY_UNIT)
    sc.check(
        before not in ("", "0") and after not in ("", "0") and before != after,
        f"{who} did not restart {LEGACY_UNIT}: MainPID {before} -> {after}",
    )
    plain = sc.run(
        f"ls /etc/systemd/system/{LEGACY_APP}.service 2>&1 | grep -vc 'No such'; true",
        timeout=15,
        check=False,
        label=f"{who} no unit of the plain name beside the legacy one",
    ).stdout.strip()
    sc.check(plain == "0", f"{who} made a unit named {LEGACY_APP} beside {LEGACY_UNIT}")


def jar_login(sc: Scenario, master: str, label: str) -> set[str]:
    """Sign in with the access token, keeping the cookies in :data:`COOKIE_JAR`.

    The token goes to curl on its standard input (a config), never an argument.

    Args:
        sc: The scenario.
        master: The access token.
        label: What the evidence calls it.

    Returns:
        The names of the cookies the console set.
    """

    def quoted(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    config = "\n".join(
        [
            f"url = {quoted(run.PANEL_URL + '/api/auth/login')}",
            'request = "POST"',
            "silent",
            "show-error",
            'write-out = "%{http_code}"',
            'output = "/dev/null"',
            f"cookie-jar = {quoted(COOKIE_JAR)}",
            f"header = {quoted('Content-Type: application/json')}",
            f"data = {quoted(run.json.dumps({'token': master}))}",
        ]
    )
    proc = run.subprocess.run(
        ["docker", "exec", "-i", sc.container, "curl", "-K", "-"],
        input=config + "\n",
        capture_output=True,
        text=True,
        timeout=30,
    )
    sc.evidence.append(f"$ {label}\n(response withheld: it holds the session)")
    if proc.returncode != 0 or proc.stdout.strip() != "200":
        raise AssertionError(
            f"the sign-in answered {proc.stdout.strip()!r}: {proc.stderr.strip()[:200]}"
        )
    jar = run.docker_exec(
        sc.container, f"awk '!/^#/ || /HttpOnly/ {{print $6}}' {COOKIE_JAR}", timeout=15
    ).stdout.split()
    return set(jar)


def jar_get(sc: Scenario, path: str, label: str) -> tuple[int, Any]:
    """GET a path of the console with the cookies of :data:`COOKIE_JAR`.

    Args:
        sc: The scenario.
        path: The path under the console.
        label: What the evidence calls it.

    Returns:
        The status code and the parsed body.
    """
    proc = run.docker_exec(
        sc.container,
        f"curl -sS -m 15 -b {COOKIE_JAR} -w '\\n%{{http_code}}' {run.PANEL_URL}{path}",
        timeout=30,
        check=False,
    )
    text, _, code = proc.stdout.rpartition("\n")
    try:
        payload: Any = run.json.loads(text) if text.strip() else None
    except ValueError:
        payload = text
    sc.evidence.append(f"$ {label}\n{code} {str(payload)[:400]}")
    return int(code or 0), payload


def seed_legacy_server(sc: Scenario) -> Seeded:
    """Make every legacy name on a 3.3.1 server.

    Args:
        sc: The scenario, in a container that runs Noust 3.3.1.

    Returns:
        What the checks need of it.
    """
    old = "[noust 3.3.1]"
    run.require_docker(sc)
    run.pull_images(sc, run.COMPOSE_BASE_IMAGE, scenarios_34.S3_IMAGE)

    # -- An in-place application whose unit has the prefix of before 0.14.1 -------------------
    node_repo = "/root/fixtures/node-app"
    sc.run(
        f"noust create -d {LEGACY_DOMAIN} -s {node_repo} -t nodejs --no-ssl --layout inplace",
        timeout=DEPLOY_TIMEOUT,
        label=f"{old} noust create -d {LEGACY_DOMAIN} (in place)",
    )
    sc.run(
        f"systemctl stop {LEGACY_APP} && systemctl disable {LEGACY_APP} 2>&1 && "
        f"mv /etc/systemd/system/{LEGACY_APP}.service /etc/systemd/system/{LEGACY_UNIT}.service && "
        f"sed -i 's/Generated by Noust/Generated by WASM/' /etc/systemd/system/{LEGACY_UNIT}.service && "
        f"systemctl daemon-reload && systemctl enable --now {LEGACY_UNIT}",
        timeout=120,
        label=f"{old} the unit of {LEGACY_DOMAIN} as an application from before 0.14.1 has it",
    )
    wait_page(sc, LEGACY_DOMAIN, "ok ", f"{old} {LEGACY_DOMAIN}")
    resolves_legacy_unit(sc, old)

    # -- A blue/green application: wasm_bg_<app> --------------------------------------------
    bg_repo, bg_url = make_node_repo(sc, "bgapp")
    # A dependency, so the install leaves a node_modules and in it the runtime stamp 3.3.1 names
    # `.wasm-runtime.json`. A local one: npm links it from the tree and never touches the network.
    commit_to(
        sc,
        bg_repo,
        'mkdir local-dep && echo \'{"name": "local-dep", "version": "1.0.0"}\' > '
        "local-dep/package.json && echo 'module.exports = 1;' > local-dep/index.js && "
        "npm pkg set dependencies.local-dep=file:./local-dep && npm install --package-lock-only",
        "a local dependency",
    )
    sc.run(
        f"noust create -d {BG_DOMAIN} -s {bg_url} -t nodejs --no-ssl --layout releases",
        timeout=DEPLOY_TIMEOUT,
        label=f"{old} noust create -d {BG_DOMAIN} (releases, without downtime)",
    )
    wait_page(sc, BG_DOMAIN, "ok 1", f"{old} {BG_DOMAIN}")
    # 3.3.1 makes a plain application; blue/green is the command that follows (the default only
    # from 3.4), and it is what writes the wasm_bg_ upstream and the instances of a template.
    sc.run(
        f"noust app zero-downtime {BG_DOMAIN} on --drain 2",
        timeout=300,
        label=f"{old} noust app zero-downtime {BG_DOMAIN} on",
    )
    wait_page(sc, BG_DOMAIN, "ok 1", f"{old} {BG_DOMAIN} on its instances")
    named = sc.run(
        f"grep -rl '{BG_UPSTREAM}' /etc/nginx",
        timeout=15,
        check=False,
        label=f"{old} where nginx knows {BG_UPSTREAM}",
    ).stdout.split()
    sc.check(bool(named), f"3.3.1 did not write the {BG_UPSTREAM} upstream")

    # -- The operator's site, which names that upstream --------------------------------------
    sc.run(
        f"printf '%s' '{EDGE_TEXT}' > {EDGE_SITE} && ln -sf {EDGE_SITE} /etc/nginx/sites-enabled/edge && "
        "nginx -t && systemctl reload nginx",
        timeout=30,
        label=f"{old} the operator's site {EDGE_DOMAIN}, proxying to {BG_UPSTREAM}",
    )
    wait_page(sc, EDGE_DOMAIN, "ok 1", f"{old} {EDGE_DOMAIN}")

    # -- A Compose stack on the relay: wasm_bg_<app>_<service> -------------------------------
    stack_repo, stack_url = run.make_compose_repo(sc, "legacy-stack", "web", port=STACK_PORT)
    run.compose_create(sc, STACK_DOMAIN, stack_url, port=STACK_PORT)
    run.wait_served(sc, STACK_DOMAIN, "web v1")
    sc.run(
        f"noust app zero-downtime {STACK_DOMAIN} on --drain 2",
        timeout=120,
        label=f"{old} noust app zero-downtime {STACK_DOMAIN} on",
    )
    sc.check(
        sc.run(
            f"grep -rl 'wasm_bg_{STACK_APP.replace('-', '_')}_web' /etc/nginx",
            timeout=15,
            check=False,
            label=f"{old} the stack's relay upstream",
        ).stdout.strip()
        != "",
        "3.3.1 did not write the stack's wasm_bg_ upstream",
    )
    # The operator's own site for the stack, the way Proggest's is: an upstream block of its own
    # that includes the relay's servers file. Noust never wrote it and never rewrites it.
    sc.run(
        f"printf '%s' '{STACK_EDGE_TEXT}' > {STACK_EDGE_SITE} && "
        f"ln -sf {STACK_EDGE_SITE} /etc/nginx/sites-enabled/stack-edge && nginx -t && "
        "systemctl reload nginx",
        timeout=30,
        label=f"{old} the operator's site {STACK_EDGE_DOMAIN}, with the relay's include",
    )
    wait_page(sc, STACK_EDGE_DOMAIN, "web v1", f"{old} {STACK_EDGE_DOMAIN}")

    # A server that has lived: one update of each application under 3.3.1, so the trees hold a
    # previous release, the runtime stamps and the image tags that an update leaves.
    # A repository that carries its overrides file under the name it had before 4.0.
    commit_to(
        sc,
        node_repo,
        "echo 1b > VERSION && printf 'max_body_size: 5m\\n' > wasm.nginx.yaml",
        "version 1b",
    )
    sc.run(
        f"noust update {LEGACY_DOMAIN}",
        timeout=DEPLOY_TIMEOUT,
        label=f"{old} noust update {LEGACY_DOMAIN}",
    )
    commit_to(sc, bg_repo, "echo 1b > VERSION", "version 1b")
    sc.run(
        f"noust update {BG_DOMAIN}",
        timeout=DEPLOY_TIMEOUT,
        label=f"{old} noust update {BG_DOMAIN}",
    )
    commit_to(sc, stack_repo, "printf 'v1b\\n' > VERSION", "version 1b")
    sc.run(
        f"noust update {STACK_DOMAIN}",
        timeout=DEPLOY_TIMEOUT,
        label=f"{old} noust update {STACK_DOMAIN}",
    )
    wait_page(sc, STACK_DOMAIN, "web v1b", f"{old} {STACK_DOMAIN}")
    wait_page(sc, STACK_EDGE_DOMAIN, "web v1b", f"{old} {STACK_EDGE_DOMAIN}")
    wait_page(sc, BG_DOMAIN, "ok 1b", f"{old} {BG_DOMAIN}")
    wait_page(sc, EDGE_DOMAIN, "ok 1b", f"{old} {EDGE_DOMAIN}")
    wait_page(sc, LEGACY_DOMAIN, "ok 1b", f"{old} {LEGACY_DOMAIN}")
    stamps = sc.run(
        "find /var/www/apps -maxdepth 6 -name '.wasm*'",
        timeout=30,
        label=f"{old} the files named .wasm* in the applications' trees",
    ).stdout.split()
    sc.check(
        bool(stamps), "3.3.1 left no .wasm-runtime.json in a release: the seed is not faithful"
    )
    previous = sc.run(
        "docker images --format '{{.Repository}}:{{.Tag}}' | grep ':wasm-previous$' || true",
        timeout=30,
        label=f"{old} the images tagged wasm-previous",
    ).stdout.split()
    sc.check(bool(previous), "3.3.1 left no wasm-previous image after a Compose update")

    # -- A database, and the read-only role the first read makes ------------------------------
    sc.run(
        f"noust db create {LEGACY_DB} --engine mysql --app {LEGACY_DOMAIN}",
        timeout=120,
        label=f"{old} noust db create {LEGACY_DB} --engine mysql",
    )
    scenarios_34.db_query(
        sc,
        LEGACY_DB,
        "CREATE TABLE seed (id int PRIMARY KEY, note varchar(40))",
        engine="mysql",
        write=True,
        label=f"{old} CREATE TABLE seed",
    )
    scenarios_34.db_query(
        sc,
        LEGACY_DB,
        "INSERT INTO seed VALUES (1, 'written under 3.3.1')",
        engine="mysql",
        write=True,
        label=f"{old} INSERT a row",
    )
    read = scenarios_34.db_query(
        sc,
        LEGACY_DB,
        "SELECT note FROM seed",
        engine="mysql",
        label=f"{old} a read, which makes the read-only account",
    )
    sc.check(
        "written under 3.3.1" in read.stdout, f"3.3.1 cannot read its own row: {read.stdout!r}"
    )
    role = sc.run(
        "mysql -N -e \"SELECT User FROM mysql.user WHERE User LIKE 'wasm\\_ro\\_%'\"",
        timeout=30,
        label=f"{old} the read-only accounts of the engine",
    ).stdout.split()
    sc.check(f"wasm_ro_{LEGACY_DB}" in role, f"3.3.1 made no wasm_ro_ account: {role!r}")

    # -- A backup in the wasm-backups folder of an S3 bucket ------------------------------------
    secret = secrets.token_urlsafe(18)
    scenarios_34.start_minio(
        sc,
        S3_NAME,
        S3_PORT,
        S3_USER,
        secret,
        f"{old} MinIO, for a destination whose folder is the default one",
    )
    sc.run(
        "noust config set security.allow_private_hosts 127.0.0.1 --list",
        timeout=30,
        label=f"{old} noust config set security.allow_private_hosts 127.0.0.1 --list",
    )
    add = (
        f"printf '%s' '{secret}' | noust backup destination add {S3_DESTINATION} --type s3 "
        f"--field provider=Minio --field endpoint=http://127.0.0.1:{S3_PORT} "
        f"--field access_key_id={S3_USER} --stdin"
    )
    sc.run(add, timeout=60, label=add.replace(secret, "<secret key>"))
    sc.run(
        f"noust backup destination test {S3_DESTINATION}",
        timeout=120,
        label=f"{old} noust backup destination test {S3_DESTINATION}",
    )
    sc.run(
        f"noust backup schedule create {LEGACY_DOMAIN} --schedule daily --retention-count 2 "
        f"--destination {S3_DESTINATION}:5",
        timeout=60,
        label=f"{old} noust backup schedule create {LEGACY_DOMAIN} --destination {S3_DESTINATION}",
    )
    sc.run(
        f"noust backup run-schedule {LEGACY_DOMAIN}",
        timeout=300,
        label=f"{old} noust backup run-schedule {LEGACY_DOMAIN}",
    )
    listing = json_of(
        sc.run(
            f"noust backup remote-list {S3_DESTINATION} --app {LEGACY_APP} --json",
            timeout=120,
            label=f"{old} noust backup remote-list {S3_DESTINATION}",
        ),
        "remote-list",
    )
    ids = run.remote_ids(listing)
    sc.check(len(ids) == 1, f"the bucket should hold the one backup: {ids!r}")
    folders = sc.run(
        f"docker exec {S3_NAME} sh -c 'ls {scenarios_34.S3_DATA}'",
        timeout=30,
        label=f"{old} the buckets MinIO holds",
    ).stdout.split()
    sc.check(S3_BUCKET in folders, f"the backup is not in the {S3_BUCKET} bucket: {folders!r}")

    # -- The console, an API token, and the access token ----------------------------------------
    enable = run.docker_exec(sc.container, "noust web enable", timeout=60, check=False)
    sc.evidence.append(f"$ {old} noust web enable\n(output withheld: it holds the access token)")
    master_match = re.search(r"Access Token:\s*(\S+)", enable.stdout)
    sc.check(master_match is not None, "noust web enable printed no access token")
    # `noust web enable` installs noust-web.service and starts it: it only has to be waited for.
    sc.run(
        f"for i in $(seq 1 60); do curl -fsS -o /dev/null {run.PANEL_URL}/health && exit 0; "
        "sleep 1; done; exit 1",
        timeout=90,
        label=f"{old} wait for the console (noust-web.service)",
    )
    issued = run.docker_exec(
        sc.container, f"noust token create {TOKEN_NAME} --scope admin", timeout=30
    )
    sc.evidence.append(f"$ {old} noust token create {TOKEN_NAME} --scope admin\n(withheld)")
    token_match = re.search(r"Token:\s*(\S+)", issued.stdout)
    sc.check(token_match is not None, "noust token create printed no token")
    token = token_match.group(1) if token_match else ""
    code, session = run.api_secret(
        sc, "GET", "/api/auth/session", f"{old} GET /api/auth/session", bearer=token
    )
    sc.check(
        code == 200 and isinstance(session, dict) and session.get("authenticated") is True,
        f"the API token does not authenticate on 3.3.1: {code} {session!r}",
    )
    # A token the way 2.x issued it: the prefix 3.x still accepts but never issues.
    sc.run(
        MAKE_WASM_TOKEN,
        timeout=60,
        label=f"{old} a wasm_tok_ API token, as 2.x stored them (written to a file, not shown)",
    )
    wasm_token = run.docker_exec(sc.container, "cat /root/legacy-wasm-token", timeout=15).stdout
    wasm_token = wasm_token.strip()
    sc.check(wasm_token.startswith("wasm_tok_"), "the seeded token has not the legacy prefix")
    code, session = run.api_secret(
        sc,
        "GET",
        "/api/auth/session",
        f"{old} GET /api/auth/session (wasm_tok_)",
        bearer=wasm_token,
    )
    sc.check(
        code == 200 and isinstance(session, dict) and session.get("authenticated") is True,
        f"the wasm_tok_ token does not authenticate on 3.3.1: {code} {session!r}",
    )
    # A signed-in browser: the session and CSRF cookies the console sets, kept in a jar.
    master = master_match.group(1) if master_match else ""
    cookies = jar_login(
        sc, master, f"{old} POST /api/auth/login (the cookie jar of a signed-in browser)"
    )
    sc.check(
        "wasm_session" in cookies and "wasm_csrf" in cookies,
        f"3.3.1 did not set its session and CSRF cookies: {sorted(cookies)}",
    )
    code, session = jar_get(sc, "/api/auth/session", f"{old} GET /api/auth/session (the cookies)")
    sc.check(
        code == 200 and isinstance(session, dict) and session.get("authenticated") is True,
        f"the session cookie does not authenticate on 3.3.1: {code} {session!r}",
    )
    # The console stays up through the upgrade, as on a production server: the packages restart it.

    units = [LEGACY_UNIT, f"{BG_APP}@blue", f"{BG_APP}@green", "nginx", "mariadb", "docker"]
    return Seeded(
        node_repo=node_repo,
        bg_repo=bg_repo,
        stack_repo=stack_repo,
        backup_ids=ids,
        token=token,
        wasm_token=wasm_token,
        master=master,
        units=units,
    )


def run_upgrade_rehearsal(sc: Scenario, wheel: Path) -> None:
    """Seed a 3.3.1 server with every legacy name, upgrade it, check it stays healthy.

    Args:
        sc: The scenario, in a container that runs Noust 3.3.1.
        wheel: This tree's wheel, on the host.
    """
    seeded = seed_legacy_server(sc)
    new = "[this tree]"
    wave5 = Wave5()

    pages_before = {
        LEGACY_DOMAIN: body_of(sc, LEGACY_DOMAIN, "before"),
        BG_DOMAIN: body_of(sc, BG_DOMAIN, "before"),
        EDGE_DOMAIN: body_of(sc, EDGE_DOMAIN, "before"),
        STACK_DOMAIN: body_of(sc, STACK_DOMAIN, "before"),
        STACK_EDGE_DOMAIN: body_of(sc, STACK_EDGE_DOMAIN, "before"),
    }
    sc.check(
        all(body for body in pages_before.values()),
        f"an application did not serve: {pages_before!r}",
    )
    state_before = units_state(sc, seeded.units, "before the upgrade")
    list_before = json_of(
        sc.run("noust list --json", timeout=60, label="[noust 3.3.1] noust list --json"), "list"
    )

    # -- The upgrade: the wheel over the venv, as the packages do ---------------------------------
    run.upgrade_noust_venv(sc, wheel)
    # The first command that opens the store is the one that migrates it.
    list_after = json_of(
        sc.run(
            "noust list --json", timeout=60, label=f"{new} noust list --json (migrates the store)"
        ),
        "list",
    )

    # -- Nothing the upgrade did restarted anything -------------------------------------------------
    state_after = units_state(sc, seeded.units, "after the upgrade")
    sc.check(
        state_after == state_before,
        f"the upgrade restarted or stopped something: {state_before!r} -> {state_after!r}",
    )
    for domain, before in pages_before.items():
        sc.check(
            body_of(sc, domain, "after") == before,
            f"{domain} no longer answers what it did before the upgrade",
        )
    sc.run("nginx -t", timeout=30, label=f"{new} nginx -t")

    # -- The same applications, listed and resolved ----------------------------------------------------
    sc.check(
        listed_domains(list_after) == listed_domains(list_before),
        f"the applications listed changed: {listed_domains(list_before)} -> "
        f"{listed_domains(list_after)}",
    )
    resolves_legacy_unit(sc, new)

    # -- The old backup restores -----------------------------------------------------------------------
    listing = json_of(
        sc.run(
            f"noust backup remote-list {S3_DESTINATION} --app {LEGACY_APP} --json",
            timeout=120,
            label=f"{new} noust backup remote-list {S3_DESTINATION}",
        ),
        "remote-list",
    )
    sc.check(
        run.remote_ids(listing) == seeded.backup_ids,
        f"the backup written under 3.3.1 is not listed: {listing!r}",
    )
    sc.run(
        f"rm -rf /var/www/apps/{LEGACY_APP}/VERSION && echo tampered > /var/www/apps/{LEGACY_APP}/VERSION",
        timeout=15,
        label=f"{new} tamper with {LEGACY_DOMAIN}",
    )
    sc.run(
        f"noust backup restore {seeded.backup_ids[-1]} --from {S3_DESTINATION} -f",
        timeout=300,
        label=f"{new} noust backup restore {seeded.backup_ids[-1]} --from {S3_DESTINATION}",
    )
    wait_page(sc, LEGACY_DOMAIN, "ok 1b", f"{new} after the restore")
    sc.check(
        body_of(sc, LEGACY_DOMAIN, "restored") == "ok 1b",
        "the restore of the backup written under 3.3.1 did not bring its version back",
    )

    # -- The old read-only role still reads -----------------------------------------------------------
    read = scenarios_34.db_query(
        sc,
        LEGACY_DB,
        "SELECT note FROM seed",
        engine="mysql",
        label=f"{new} a read of a database whose read-only account 3.3.1 made",
    )
    sc.check("written under 3.3.1" in read.stdout, f"the old role no longer reads: {read.stdout!r}")

    # -- The console, restarted as the packages do, still knows who is signed in ------------------------
    # It ran 3.3.1 through the upgrade (as on a production server); the packages restart it.
    sc.run(
        "systemctl restart noust-web.service; for i in $(seq 1 30); do "
        f"curl -fsS -o /dev/null {run.PANEL_URL}/health && exit 0; sleep 1; done; exit 1",
        timeout=120,
        label=f"{new} systemctl restart noust-web (the new code answers)",
    )
    # The one thing that does not survive on purpose: the cookies keep their old names on the
    # browser, the console looks for the new ones, and asks to sign in once.
    code, session = jar_get(
        sc, "/api/auth/session", f"{new} GET /api/auth/session (the cookies of 3.3.1)"
    )
    sc.check(
        code == 200 and isinstance(session, dict) and session.get("authenticated") is False,
        f"the cookies of 3.3.1 must not be read by 4.0: {code} {session!r}",
    )
    for what, bearer in (
        ("the noust_tok_ token made under 3.3.1", seeded.token),
        ("the wasm_tok_ token made under 2.x", seeded.wasm_token),
    ):
        code, session = run.api_secret(
            sc, "GET", "/api/auth/session", f"{new} GET /api/auth/session ({what})", bearer=bearer
        )
        sc.check(
            code == 200 and isinstance(session, dict) and session.get("authenticated") is True,
            f"{what} does not authenticate after the upgrade: {code} {session!r}",
        )
    code, login = run.api_secret(
        sc,
        "POST",
        "/api/auth/login",
        f"{new} POST /api/auth/login (the access token)",
        body={"token": seeded.master},
    )
    sc.check(code == 200, f"the access token does not sign in after the upgrade: {code} {login!r}")
    fresh = jar_login(sc, seeded.master, f"{new} POST /api/auth/login (a new cookie jar)")
    wave5.expect(
        "noust_session" in fresh and "noust_csrf" in fresh and "wasm_session" not in fresh,
        "the console sets noust_session and noust_csrf, not the wasm_ cookies",
        f"cookies: {sorted(fresh)}",
    )
    code, session = jar_get(
        sc, "/api/auth/session", f"{new} GET /api/auth/session (the new cookies)"
    )
    sc.check(
        code == 200 and isinstance(session, dict) and session.get("authenticated") is True,
        f"the session of the sign-in under 4.0 does not authenticate: {code} {session!r}",
    )

    # -- The doctor and the health report answer ---------------------------------------------------------
    doctor = sc.run(
        "noust setup doctor", timeout=300, check=False, label=f"{new} noust setup doctor"
    )
    sc.check("Traceback" not in doctor.stdout + doctor.stderr, "noust setup doctor crashed")
    health = sc.run("noust health --json", timeout=120, check=False, label=f"{new} noust health")
    sc.check("Traceback" not in health.stdout + health.stderr, "noust health crashed")
    try:
        said = " | ".join(json_of(health, "health").get("warnings", []))
    except AssertionError:
        said = health.stdout
    wave5.expect(
        WASM_TOKEN_NAME in said,
        "noust health names the API token a script still signs in with under the old prefix",
        said[-300:],
    )
    wave5.expect(
        "wasm.nginx.yaml" in said,
        "noust health names the repository that still has its overrides file under the old name",
        said[-300:],
    )
    wave5.expect(
        BG_UPSTREAM in said,
        "noust health names the upstream a site the operator wrote still names",
        said[-300:],
    )

    # -- An update of each application is a deploy like any other ------------------------------------------
    commit_to(sc, seeded.node_repo, "echo 2 > VERSION", "version 2")
    sc.run(
        f"noust update {LEGACY_DOMAIN}",
        timeout=DEPLOY_TIMEOUT,
        label=f"{new} noust update {LEGACY_DOMAIN} (the unit is the legacy one)",
    )
    wait_page(sc, LEGACY_DOMAIN, "ok 2", f"{new} after its update")
    # A deployment is what reads a repository's overrides file: it says the rename.
    created = sc.run(
        f"noust create -d {YAML_DOMAIN} -s {seeded.node_repo} -t nodejs --no-ssl --layout inplace",
        timeout=DEPLOY_TIMEOUT,
        check=False,
        label=f"{new} noust create -d {YAML_DOMAIN} from a repository with wasm.nginx.yaml",
    )
    wave5.expect(
        "git mv wasm.nginx.yaml noust.nginx.yaml" in created.stdout + created.stderr,
        "a deployment that reads wasm.nginx.yaml says the git mv that renames it",
        (created.stdout + created.stderr)[-300:],
    )
    wait_page(sc, YAML_DOMAIN, "ok 2", f"{new} {YAML_DOMAIN}")
    commit_to(sc, seeded.bg_repo, "echo 2 > VERSION", "version 2")
    sc.run(
        f"noust update {BG_DOMAIN}",
        timeout=DEPLOY_TIMEOUT,
        label=f"{new} noust update {BG_DOMAIN} (without downtime)",
    )
    wait_page(sc, BG_DOMAIN, "ok 2", f"{new} after its update")
    wait_page(sc, EDGE_DOMAIN, "ok 2", f"{new} the operator's site after the update")
    commit_to(sc, seeded.stack_repo, "printf 'v2\\n' > VERSION", "version 2")
    sc.run(
        f"noust update {STACK_DOMAIN}",
        timeout=DEPLOY_TIMEOUT,
        label=f"{new} noust update {STACK_DOMAIN} (the relay)",
    )
    wait_page(sc, STACK_DOMAIN, "web v2", f"{new} after its update")
    sc.run("nginx -t", timeout=30, label=f"{new} nginx -t (after the updates)")

    # -- Wave 5: what the rename must leave ------------------------------------------------------------------
    units = sc.run(
        f"ls /etc/systemd/system | grep -E '^(wasm-)?{LEGACY_APP}.service$'",
        timeout=15,
        check=False,
        label=f"{new} the unit files of {LEGACY_DOMAIN}",
    ).stdout.split()
    wave5.expect(
        units == [f"{LEGACY_APP}.service"],
        "the legacy unit is renamed by the application's next deploy, and only by it",
        f"unit files: {units}",
    )
    stack_still = sc.run(
        f"grep -rl '{STACK_UPSTREAM}' /etc/nginx || true",
        timeout=15,
        check=False,
        label=f"{new} where nginx still knows {STACK_UPSTREAM}",
    ).stdout.split()
    wave5.expect(
        stack_still == [],
        "the relay upstream of the Compose stack is renamed by its update",
        f"still in {stack_still}",
    )
    bg_site = sc.run(
        "cat /etc/nginx/sites-available/bgapp.test",
        timeout=15,
        check=False,
        label=f"{new} the application's own site",
    ).stdout
    bg_upstream = sc.run(
        "cat /etc/nginx/noust-upstreams/bgapp-test.conf",
        timeout=15,
        check=False,
        label=f"{new} the upstream file",
    ).stdout
    wave5.expect(
        BG_UPSTREAM not in bg_site and f"proxy_pass http://{BG_UPSTREAM_NEW};" in bg_site,
        "the site of the blue/green application proxies to the new upstream",
        bg_site[-300:],
    )
    wave5.expect(
        f"upstream {BG_UPSTREAM_NEW} " in bg_upstream and f"upstream {BG_UPSTREAM} " in bg_upstream,
        "the old upstream stays defined beside the new one while the operator's site names it",
        bg_upstream[-300:],
    )
    operator = sc.run(
        f"grep -c '{BG_UPSTREAM}' {EDGE_SITE}",
        timeout=15,
        check=False,
        label=f"{new} the operator's site is as it was",
    ).stdout.strip()
    sc.check(operator == "1", "an operator's site was rewritten by the upgrade or by a deploy")
    tested = sc.run("nginx -t 2>&1", timeout=30, check=False, label=f"{new} nginx -t").returncode
    wave5.expect(tested == 0, "nginx still accepts the configuration with the operator's site")
    wave5.expect(
        EDGE_SITE in doctor.stdout and BG_UPSTREAM in doctor.stdout,
        "noust setup doctor names the operator's site and the line that names the old upstream",
    )
    roles = sc.run(
        "mysql -N -e \"SELECT User FROM mysql.user WHERE User LIKE '%\\_ro\\_%'\"",
        timeout=30,
        label=f"{new} the read-only accounts of the engine",
    ).stdout.split()
    wave5.expect(
        roles == [f"noust_ro_{LEGACY_DB}"], "the read-only role is renamed", f"roles: {roles}"
    )

    # -- The next backup goes to the new folder; the old ones stay listed and restorable ------------
    sc.run(
        f"noust backup run-schedule {LEGACY_DOMAIN}",
        timeout=300,
        label=f"{new} a backup after the upgrade",
    )
    listing_after = json_of(
        sc.run(
            f"noust backup remote-list {S3_DESTINATION} --app {LEGACY_APP} --json",
            timeout=120,
            label=f"{new} the destination's listing after the backup",
        ),
        "remote-list",
    )
    ids_after = run.remote_ids(listing_after)
    buckets = sc.run(
        f"docker exec {S3_NAME} sh -c 'ls {scenarios_34.S3_DATA}'",
        timeout=30,
        label=f"{new} the buckets MinIO holds now",
    ).stdout.split()
    wave5.expect(
        "noust-backups" in buckets and S3_BUCKET in buckets,
        "the next backup goes to the new folder and the old one stays",
        f"buckets: {buckets}",
    )
    wave5.expect(
        set(seeded.backup_ids) <= set(ids_after) and len(ids_after) > len(seeded.backup_ids),
        "the backup made under 3.3.1 is still listed beside the new one",
        f"listed: {ids_after}; seeded: {seeded.backup_ids}",
    )

    # -- noust setup rename: what can be finished without restarting anything -------------------
    finished = sc.run(
        "noust setup rename", timeout=300, check=False, label=f"{new} noust setup rename"
    )
    sc.check("Traceback" not in finished.stdout + finished.stderr, "noust setup rename crashed")
    wave5.expect(
        finished.returncode == 0,
        "noust setup rename succeeds",
        (finished.stdout + finished.stderr)[-400:],
    )
    wave5.expect(
        sc.run(
            "find /var/www/apps -maxdepth 6 -name '.wasm*' | wc -l",
            timeout=30,
            label=f"{new} files named .wasm* in the applications' trees",
        ).stdout.strip()
        == "0",
        "no application tree keeps a .wasm* file",
    )
    tags = sc.run(
        "docker images --format '{{.Repository}}:{{.Tag}}' | grep ':wasm-previous$' || true",
        timeout=30,
        label=f"{new} the images tagged wasm-previous",
    ).stdout.split()
    wave5.expect(
        tags == [],
        "no image is tagged wasm-previous: the stack's way back has the new name",
        f"{tags}",
    )
    wave5.expect(
        "These wait for you" in finished.stdout and EDGE_SITE in finished.stdout,
        "noust setup rename says what waits for the operator, with the operator's site",
        finished.stdout[-400:],
    )
    still = sc.run(
        "cat /etc/nginx/noust-upstreams/bgapp-test.conf",
        timeout=15,
        check=False,
        label=f"{new} the upstream file while the operator's line is as it was",
    ).stdout
    wave5.expect(
        f"upstream {BG_UPSTREAM} " in still,
        "the old upstream is not taken away while the operator's site names it",
    )

    # -- The operator changes their line; nothing is down at any moment -----------------------------
    sc.run(
        f"sed -i 's/{BG_UPSTREAM}/{BG_UPSTREAM_NEW}/' {EDGE_SITE} && nginx -t && "
        "systemctl reload nginx",
        timeout=60,
        label=f"{new} the operator changes the line the doctor named, and reloads nginx",
    )
    wait_page(sc, EDGE_DOMAIN, "ok 2", f"{new} {EDGE_DOMAIN} on the new name")
    sc.run(
        "noust setup rename",
        timeout=300,
        label=f"{new} noust setup rename (the old name is not named by anyone now)",
    )
    generated = sc.run(
        "grep -rl 'Generated by Noust' /etc/nginx | xargs -r grep -l 'wasm_bg_' || true",
        timeout=15,
        check=False,
        label=f"{new} generated nginx files that still say wasm_bg_",
    ).stdout.split()
    wave5.expect(
        generated == [], "no file Noust generated still says wasm_bg_", f"still in {generated}"
    )
    wait_page(sc, EDGE_DOMAIN, "ok 2", f"{new} {EDGE_DOMAIN} after the alias went")
    wait_page(sc, BG_DOMAIN, "ok 2", f"{new} {BG_DOMAIN} after the alias went")
    sc.run("nginx -t", timeout=30, label=f"{new} nginx -t (after the alias went)")
    doctor_after = sc.run(
        "noust setup doctor", timeout=300, check=False, label=f"{new} noust setup doctor (after)"
    )
    wave5.expect(
        BG_UPSTREAM not in doctor_after.stdout,
        "noust setup doctor has nothing to say about the upstream once the line is changed",
    )

    # -- The backup of before still restores, from the folder it is in ----------------------------------
    sc.run(
        f"noust backup restore {seeded.backup_ids[-1]} --from {S3_DESTINATION} -f",
        timeout=300,
        label=f"{new} restore the backup made under 3.3.1 (it is in the old folder)",
    )
    wait_page(sc, LEGACY_DOMAIN, "ok 1b", f"{new} after restoring the backup of 3.3.1")

    failed = wave5.report(sc)
    if failed:
        raise AssertionError("wave 5 left these undone:\n- " + "\n- ".join(failed))


def run_upgrade_33_mode(wheel: Path, *, keep: bool) -> int:
    """Run the 3.3.1 -> this tree rehearsal in its own container.

    Args:
        wheel: This tree's wheel, on the host.
        keep: Do not remove the container when done.

    Returns:
        0 when every hard check held (the wave 5 expectations that do not yet hold are reported,
        not failed), 1 otherwise.
    """
    started_at = time.monotonic()
    label = "upgrade_noust_3_3_1_with_every_legacy_name"
    name = run.random_container_name()
    sc = Scenario(container=name)
    failures = 0
    print(f"\n=== {label} ({name}) ===")
    try:
        try:
            start_container(name)
            wait_for_systemd(name)
            run.prepare_inner_docker(name)
            run.install_fixtures(name)
            install_from_git(name, FROM_TAG)
            run_upgrade_rehearsal(sc, wheel)
        except run.ScenarioSkipped as exc:
            # Nothing was checked, and this rehearsal is the one thing the run is for: not a PASS.
            failures += 1
            print(f"FAIL: {label}: it could not run here: {exc}")
        except (AssertionError, HarnessError) as exc:
            failures += 1
            print(f"FAIL: {label}: {exc}")
        except Exception:
            # The harness's error boundary: the evidence below and the teardown still happen.
            failures += 1
            print(f"FAIL: {label} (the rehearsal itself raised)")
            traceback.print_exc(file=sys.stdout)
        else:
            print(f"PASS: {label}")
    finally:
        if sc.evidence:
            print("--- evidence ---")
            print("\n\n".join(sc.evidence))
        run.docker_exec(name, f"docker rm -f {S3_NAME}", timeout=60, check=False)
        if keep:
            print(f"\n[teardown] --keep given, leaving container {name} running")
        else:
            print(f"\n[teardown] removing container {name}")
            run.remove_container(name)
    elapsed = time.monotonic() - started_at
    print(f"\n[summary] 1 scenario(s) run, {failures} failure(s), {elapsed:.1f}s total")
    return 1 if failures else 0


# ---------------------------------------------------------------------------------------------
# Going back: this tree on a store it migrated, then the release before it (checklist N-27)
# ---------------------------------------------------------------------------------------------

STATIC_BEFORE = "static-before.test"
STATIC_AFTER = "static-after.test"

#: The directory of the store, wherever this install put it.
STORE_DIR = "$(dirname $(ls /var/lib/noust/noust.db /root/.local/share/noust/noust.db 2>/dev/null | head -1))"


def schema_on_disk(sc: Scenario, label: str) -> str:
    """The schema version the store records, read with sqlite3 and not through Noust."""
    return sc.run(
        run.store_query("SELECT MAX(version) FROM schema_version"), timeout=15, label=label
    ).stdout.strip()


def put_released_noust_back(sc: Scenario) -> None:
    """Install the 3.3.1 checkout the rehearsal kept in ``/root/noust-src`` over the venv."""
    sc.run(
        "/opt/noust/bin/pip install --quiet --force-reinstall --no-deps /root/noust-src",
        timeout=run.PIP_INSTALL_TIMEOUT,
        label="pip install --force-reinstall --no-deps /root/noust-src  (3.3.1 again)",
    )
    schema = sc.run(
        "/opt/noust/bin/python -c 'import noust.core.store as s; print(s.SCHEMA_VERSION)'",
        timeout=30,
        label="the installed package's SCHEMA_VERSION",
    ).stdout.strip()
    sc.check(schema == "13", f"the venv does not run 3.3.1 again: SCHEMA_VERSION {schema!r}")


def run_downgrade_rehearsal(sc: Scenario, wheel: Path) -> None:
    """Upgrade a 3.3.1 server to this tree and go back, the way docs/downgrading.md says.

    What is proven with the real releases, on a real store: the upgrade keeps a copy of the store
    it migrates; `noust store compat --to 3.3.1` says that the release cannot open the new store
    and which copy can be put back, changing nothing; the release itself refuses the new store
    (in its own words); putting the copy back, as the guide's steps say, brings 3.3.1 up with the
    applications it had; what the new release created meanwhile is the loss the guide names, and
    everything those applications serve keeps serving the whole time; going forward again migrates
    the restored store once more.

    Args:
        sc: The scenario, in a container that runs Noust 3.3.1.
        wheel: This tree's wheel, on the host.
    """
    new = "[this tree]"
    old = "[noust 3.3.1]"
    sc.run(
        f"noust create -d {STATIC_BEFORE} -s /root/fixtures/static-site -t static --no-ssl",
        timeout=DEPLOY_TIMEOUT,
        label=f"{old} noust create -d {STATIC_BEFORE} -s /root/fixtures/static-site -t static",
    )
    wait_page(sc, STATIC_BEFORE, "Noust Integration Static Fixture", f"{old} the first application")
    sc.check(schema_on_disk(sc, f"{old} schema on disk") == "13", "3.3.1 does not write schema 13")
    sc.run(
        "printf '%s\\n' 'before-upgrade-Passw0rd-xyz' | "
        "noust user create before-acct --role viewer --stdin",
        timeout=60,
        label=f"{old} noust user create before-acct --role viewer --stdin",
    )
    token = sc.run(
        "noust token create before-upgrade --scope admin",
        timeout=60,
        check=False,
        label=f"{old} noust token create before-upgrade",
    )
    sc.check(token.returncode == 0, f"3.3.1 could not make a token: {token.stdout}{token.stderr}")

    # -- The upgrade keeps a copy of what it migrates ----------------------------------------------
    run.upgrade_noust_venv(sc, wheel)
    sc.run("noust list --json", timeout=60, label=f"{new} noust list --json (migrates the store)")
    sc.check(
        schema_on_disk(sc, f"{new} schema on disk") == str(run.SCHEMA_VERSION),
        f"the store was not migrated to {run.SCHEMA_VERSION}",
    )
    copies = sc.run(
        f"ls -l {STORE_DIR}/noust.db.v13-*.bak", timeout=15, label="the copies the upgrade kept"
    ).stdout
    sc.check(copies.count(".bak") == 1, f"expected one copy of the schema 13 store:\n{copies}")
    sc.check(copies.split()[0] == "-rw-------", f"the copy is not owner-only: {copies.split()[0]}")
    copy = copies.split()[-1]
    kept = sc.run(
        run.store_query_at(
            copy, "SELECT MAX(version) FROM schema_version; PRAGMA integrity_check;"
        ),
        timeout=15,
        label="the copy: its schema, and SQLite's own check",
    ).stdout.split()
    sc.check(kept == ["13", "ok"], f"the copy is not a sound schema 13 store: {kept!r}")

    # -- Work done on the new release: what going back loses ----------------------------------------
    sc.run(
        f"noust create -d {STATIC_AFTER} -s /root/fixtures/static-site -t static --no-ssl",
        timeout=DEPLOY_TIMEOUT,
        label=f"{new} noust create -d {STATIC_AFTER} (after the upgrade)",
    )
    sc.run(
        "printf '%s\\n' 'after-upgrade-Passw0rd-xyz' | "
        "noust user create after-acct --role viewer --stdin",
        timeout=60,
        label=f"{new} noust user create after-acct --role viewer --stdin",
    )
    sc.run(
        "noust token create after-upgrade --scope admin",
        timeout=60,
        label=f"{new} noust token create after-upgrade",
    )
    before_compat = sc.run(
        f"sha256sum {STORE_DIR}/noust.db | cut -d' ' -f1", timeout=15, label="checksum of the store"
    ).stdout.strip()

    # -- `store compat` reads, says, and changes nothing --------------------------------------------
    said = json_of(
        sc.run(
            "noust store compat --to 3.3.1 --json",
            timeout=60,
            label=f"{new} noust store compat --to 3.3.1 --json",
        ),
        "store compat",
    )
    sc.evidence.append(f"# store compat said: {str(said)[:900]}")
    sc.check(
        "3.3.1" in str(said) and ".bak" in str(said),
        f"`store compat --to 3.3.1` does not name the release and the copy to put back: {said!r}",
    )
    text = sc.run(
        "noust store compat --to 3.3.1", timeout=60, label=f"{new} noust store compat --to 3.3.1"
    ).stdout
    sc.check(
        "cannot open" in text and "stop" in text.lower(),
        f"the human text does not say that 3.3.1 cannot open the store, and the steps:\n{text}",
    )
    after_compat = sc.run(
        f"sha256sum {STORE_DIR}/noust.db | cut -d' ' -f1", timeout=15, label="checksum of the store"
    ).stdout.strip()
    sc.check(before_compat == after_compat, "`noust store compat` changed the store")

    # -- 3.3.1 on the new store: refused, in its own words, with everything still serving ------------
    page_before = body_of(sc, STATIC_BEFORE, "before going back")
    put_released_noust_back(sc)
    refused = sc.run(
        "noust list --json",
        timeout=60,
        check=False,
        label=f"{old} noust list --json (the store is schema {run.SCHEMA_VERSION})",
    )
    refusal = refused.stdout + refused.stderr
    sc.check(refused.returncode != 0, "3.3.1 opened a store written by a newer release")
    sc.check("newer Noust" in refusal, f"3.3.1 does not say why it refuses:\n{refusal[-600:]}")
    sc.check(
        schema_on_disk(sc, "schema on disk (3.3.1 refused, so nothing was written)")
        == str(run.SCHEMA_VERSION),
        "3.3.1 changed the store it refused",
    )
    sc.check(
        body_of(sc, STATIC_BEFORE, "while 3.3.1 refuses") == page_before,
        "an application stopped answering because the release was changed",
    )

    # -- The steps `store compat` printed, run as printed ----------------------------------------------
    steps = [str(step) for step in said["plan"]["steps"]]
    commands = [step.split(": ", 1)[1] for step in steps[1:4]]
    sc.check(
        len(commands) == 3 and all("noust" in c or "mv " in c or "cp " in c for c in commands),
        f"`store compat` did not print the three commands I expected: {steps!r}",
    )
    sc.run(commands[0], timeout=60, check=False, label="1. " + commands[0])
    aside = sc.run(
        commands[1],
        timeout=30,
        check=False,
        label="2. " + commands[1],
    )
    sc.check(
        aside.returncode == 0,
        "the command that keeps the store aside fails when the write-ahead log is not there, "
        f"although the text says that is fine (exit {aside.returncode}): {aside.stderr!r}",
    )
    sc.run(commands[2], timeout=30, label="3. " + commands[2])
    # 4. the earlier release is already installed (put_released_noust_back)
    listing = json_of(
        sc.run(
            "noust list --json", timeout=60, label=f"{old} noust list --json (the copy is back)"
        ),
        "list",
    )
    domains = listed_domains(listing)
    sc.check(STATIC_BEFORE in domains, f"3.3.1 does not know the application it had: {domains}")
    sc.check(
        STATIC_AFTER not in domains,
        "3.3.1 knows an application created after the copy was taken: the loss the guide names "
        "is not the one that happened",
    )
    sc.check(
        schema_on_disk(sc, f"{old} schema on disk") == "13", "the store is not schema 13 again"
    )
    sc.check(
        body_of(sc, STATIC_BEFORE, "after going back") == page_before,
        "the application changed what it serves",
    )
    sc.check(
        "Noust Integration Static Fixture"
        in body_of(sc, STATIC_AFTER, "the one the new release made"),
        "an application created on the new release stopped serving when the store went back",
    )
    accounts = sc.run("noust user list", timeout=60, label=f"{old} noust user list").stdout
    sc.check(
        "before-acct" in accounts and "after-acct" not in accounts,
        f"the accounts are not those of the copy (they live in the store): {accounts[:400]}",
    )
    tokens = sc.run("noust token list", timeout=60, label=f"{old} noust token list").stdout
    sc.check(
        "before-upgrade" in tokens and "after-upgrade" in tokens,
        f"the tokens are not all still there (they live in the sessions database, "
        f"which no release's store holds): {tokens[:400]}",
    )
    sc.run(
        f"noust update {STATIC_BEFORE}",
        timeout=DEPLOY_TIMEOUT,
        label=f"{old} noust update {STATIC_BEFORE} (3.3.1 works on what it had)",
    )
    kept_aside = sc.run(
        run.store_query_at(
            f"{STORE_DIR}/before-downgrade/noust.db",
            "PRAGMA integrity_check; SELECT MAX(version) FROM schema_version;",
        ),
        timeout=15,
        label=f"the store kept aside is whole, and is schema {run.SCHEMA_VERSION}",
    ).stdout.split()
    sc.check(
        kept_aside == ["ok", str(run.SCHEMA_VERSION)],
        f"the store kept aside is not intact: {kept_aside!r}",
    )

    # -- And forward again ---------------------------------------------------------------------------------
    run.upgrade_noust_venv(sc, wheel)
    again = json_of(
        sc.run("noust list --json", timeout=60, label=f"{new} noust list --json (migrates again)"),
        "list",
    )
    sc.check(STATIC_BEFORE in listed_domains(again), "the application is gone after going forward")
    sc.check(
        schema_on_disk(sc, f"{new} schema on disk") == str(run.SCHEMA_VERSION),
        "the store was not migrated again",
    )
    copies_now = sc.run(
        f"ls {STORE_DIR}/noust.db.v13-*.bak | wc -l", timeout=15, label="the copies now kept"
    ).stdout.strip()
    sc.check(int(copies_now) >= 2, f"the second migration kept no copy of its own: {copies_now}")
    sc.check(
        body_of(sc, STATIC_BEFORE, "after going forward") == page_before,
        "the application changed what it serves",
    )


def run_downgrade_mode(wheel: Path, *, keep: bool) -> int:
    """Run the going-back rehearsal in its own container.

    Args:
        wheel: This tree's wheel, on the host.
        keep: Do not remove the container when done.

    Returns:
        0 when it held, 1 otherwise.
    """
    started_at = time.monotonic()
    label = "downgrade_noust_to_3_3_1_over_a_migrated_store"
    name = run.random_container_name()
    sc = Scenario(container=name)
    failures = 0
    print(f"\n=== {label} ({name}) ===")
    try:
        try:
            start_container(name)
            wait_for_systemd(name)
            run.prepare_inner_docker(name)
            run.install_fixtures(name)
            install_from_git(name, FROM_TAG)
            run_downgrade_rehearsal(sc, wheel)
        except run.ScenarioSkipped as exc:
            failures += 1
            print(f"FAIL: {label}: it could not run here: {exc}")
        except (AssertionError, HarnessError) as exc:
            failures += 1
            print(f"FAIL: {label}: {exc}")
        except Exception:
            failures += 1
            print(f"FAIL: {label} (the rehearsal itself raised)")
            traceback.print_exc(file=sys.stdout)
        else:
            print(f"PASS: {label}")
    finally:
        if sc.evidence:
            print("--- evidence ---")
            print("\n\n".join(sc.evidence))
        if keep:
            print(f"\n[teardown] --keep given, leaving container {name} running")
        else:
            print(f"\n[teardown] removing container {name}")
            run.remove_container(name)
    elapsed = time.monotonic() - started_at
    print(f"\n[summary] 1 scenario(s) run, {failures} failure(s), {elapsed:.1f}s total")
    return 1 if failures else 0


__all__ = ["WAVE5_DONE", "run_downgrade_mode", "run_upgrade_33_mode"]
