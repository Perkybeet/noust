# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Real-machine scenarios for the application work of the 4.0 release (checklist B-05, B-43, B-46,
B-83, A-14 and the gaps of the applications audit), which unit tests could only fake.

They are scenarios of :mod:`run` (same container, same helpers, same report): ``run.py`` imports this
module before it picks what to run, so ``--scenario container_command_and_entrypoint`` selects one
here like any other. Each one drives the real CLI against real nginx, systemd and Docker, and
removes what it made.

Docker: everything a scenario needs a container for runs in the harness container's own Docker
daemon, never the machine's. How to run them while sharing a machine's Docker with other work (every
name carries ``noust-test-w6a`` and the ``noust.test=1`` label, nothing is pruned)::

    NOUST_IT_PREFIX=noust-test-w6a NOUST_IT_IMAGE=noust-test-w6a-integration:latest \\
    NOUST_IT_DOCKER_VOLUME=noust-test-w6a-docker NOUST_IT_MEMORY=4g \\
    python tests/integration/run.py --scenario container_command_and_entrypoint
"""

from __future__ import annotations

import base64
import hashlib
import json
import shlex
import time
from typing import Any

import run
from run import (
    DEPLOY_TIMEOUT,
    Scenario,
    compose_clean,
    compose_create,
    container_clean,
    curl_host,
    json_of,
    pull_images,
    require_cli,
    require_docker,
    scenario,
    store_rows,
    wait_served,
)

#: What the git daemon of the harness container serves repositories from.
FIXTURES = "/root/fixtures"

#: Identity every fixture repository commits as.
GIT_ID = "git config user.email noust-it@example.com && git config user.name 'Noust Integration'"


def put_file(sc: Scenario, path: str, text: str) -> None:
    """
    Write a file inside the harness container, whatever the text holds.

    Args:
        sc: The scenario.
        path: Where, inside the container.
        text: Its content; sent as base64 so that no quote of it reaches a shell.
    """
    encoded = base64.b64encode(text.encode()).decode()
    sc.run(
        f"mkdir -p $(dirname {path}) && echo {encoded} | base64 -d > {path}",
        timeout=30,
        label=f"(fixture) write {path}:\n{text.rstrip()}",
    )


def commit_all(sc: Scenario, repo: str, message: str) -> str:
    """
    Commit everything in a fixture repository and give back the commit.

    Args:
        sc: The scenario.
        repo: The repository's directory.
        message: The commit message.

    Returns:
        The commit's full id.
    """
    sc.run(
        f"cd {repo} && git add -A && git commit -q -m {shlex.quote(message)}",
        timeout=30,
        label=f"(fixture repo) commit: {message}",
    )
    return sc.run(
        f"git -C {repo} rev-parse HEAD", timeout=15, label=f"{repo}: its newest commit"
    ).stdout.strip()


def init_repo(sc: Scenario, repo: str) -> None:
    """Start a fixture repository on ``main`` that the harness's git daemon serves."""
    sc.run(
        f"rm -rf {repo} && mkdir -p {repo} && cd {repo} && git init -q -b main && {GIT_ID}",
        timeout=30,
        label=f"(fixture repo) git init {repo}",
    )


def container_config(sc: Scenario, domain: str) -> tuple[Any, Any]:
    """
    Read the entrypoint and the command a container application's container really has.

    Args:
        sc: The scenario.
        domain: The application.

    Returns:
        ``(Config.Entrypoint, Config.Cmd)`` as Docker reports them: a list, or None.
    """
    shown = sc.run(
        "docker inspect --format '{{json .Config.Entrypoint}}|{{json .Config.Cmd}}' "
        f"$(docker ps -q -f label=io.noust.app={domain} | head -1)",
        timeout=30,
        label=f"docker inspect the container of {domain}: its entrypoint and command",
    ).stdout.strip()
    entrypoint, command = shown.split("|", 1)
    return json.loads(entrypoint), json.loads(command)


def container_of(domain: str, service: str) -> str:
    """A shell fragment that names the running container of one service of a stack."""
    return (
        "$(docker ps -q "
        f"-f label=com.docker.compose.project={run.compose_project(domain)} "
        f"-f label=com.docker.compose.service={service} "
        "-f label=com.docker.compose.oneoff=False | head -1)"
    )


# ---------------------------------------------------------------------------------------------
# A container application's command and entrypoint, on the real engine
# ---------------------------------------------------------------------------------------------

CMD_DOMAIN = "w6a-cmd.test"
CMD_PORT = 18151
CMD_IMAGES = "noust-w6a-cmd-test"
CMD_BASE = "nginx:alpine"


def served_by(script: str) -> str:
    """A line of ``sh -c`` that writes a page and then runs nginx in the foreground."""
    return f"echo {script} > /usr/share/nginx/html/index.html && exec nginx -g 'daemon off;'"


@scenario("container_command_and_entrypoint")
def scenario_container_command(sc: Scenario) -> None:
    """
    What the image's ENTRYPOINT and CMD do when Noust overrides them, on the real engine.

    Three rules came out of running Docker 29 and Compose 5 on the files Noust writes, and the
    command and entrypoint settings are built on them: a command is the entrypoint's arguments;
    setting an entrypoint drops the image's CMD by itself; and an empty entrypoint is the only way
    to clear anything (the CMD is then the program), because an empty ``command:`` means the
    image's own. Nothing here is a mock: nginx is started three different ways and answers
    through nginx.
    """
    require_cli(sc, "app container set", option="--command", what="command and entrypoint")
    require_docker(sc)
    pull_images(sc, CMD_BASE)
    one = shlex.quote("sh -c " + shlex.quote(served_by("cmd-v1")))
    sc.run(
        f"noust create -d {CMD_DOMAIN} --image {CMD_BASE} -p {CMD_PORT} --no-ssl --command {one}",
        timeout=DEPLOY_TIMEOUT,
        label=f"noust create -d {CMD_DOMAIN} --image {CMD_BASE} --command 'sh -c ...'",
    )
    try:
        # A command is the arguments of the image's entrypoint: its script receives them.
        wait_served(sc, CMD_DOMAIN, "cmd-v1")
        entrypoint, command = container_config(sc, CMD_DOMAIN)
        sc.check(
            entrypoint == ["/docker-entrypoint.sh"],
            f"the image's own entrypoint is kept: {entrypoint!r}",
        )
        sc.check(
            command == ["sh", "-c", served_by("cmd-v1")],
            f"the command reaches the container as the words that were given: {command!r}",
        )

        # An entrypoint replaces the program, and Docker drops the image's CMD with it.
        two = shlex.quote("sh -c " + shlex.quote(served_by("ep-v2")))
        sc.run(
            f"noust app container set {CMD_DOMAIN} --clear command --entrypoint {two}",
            timeout=30,
            label=f"noust app container set {CMD_DOMAIN} --clear command --entrypoint 'sh -c ...'",
        )
        sc.run(
            f"noust update {CMD_DOMAIN} --force",
            timeout=DEPLOY_TIMEOUT,
            label=f"noust update {CMD_DOMAIN} --force",
        )
        wait_served(sc, CMD_DOMAIN, "ep-v2")
        entrypoint, command = container_config(sc, CMD_DOMAIN)
        sc.check(
            entrypoint == ["sh", "-c", served_by("ep-v2")],
            f"the entrypoint was not replaced: {entrypoint!r}",
        )
        sc.check(
            command is None, f"Docker drops the image's CMD with a new entrypoint: {command!r}"
        )

        # An empty entrypoint clears the image's, and the image's CMD is the program: nginx.
        sc.run(
            f"noust app container set {CMD_DOMAIN} --entrypoint ''",
            timeout=30,
            label=f"noust app container set {CMD_DOMAIN} --entrypoint ''",
        )
        sc.run(
            f"noust update {CMD_DOMAIN} --force",
            timeout=DEPLOY_TIMEOUT,
            label=f"noust update {CMD_DOMAIN} --force",
        )
        wait_served(sc, CMD_DOMAIN, "Welcome to nginx!")
        entrypoint, command = container_config(sc, CMD_DOMAIN)
        sc.check(entrypoint == [], f"an empty entrypoint is no entrypoint: {entrypoint!r}")
        sc.check(
            command == ["nginx", "-g", "daemon off;"],
            f"the image's CMD runs as the program: {command!r}",
        )

        # A CMD cannot be cleared on its own: it is refused, and nothing changed.
        refused = sc.run(
            f"noust app container set {CMD_DOMAIN} --command ''",
            timeout=30,
            check=False,
            label=f"noust app container set {CMD_DOMAIN} --command ''",
        )
        sc.check(refused.returncode != 0, "an empty command must be refused")
        sc.check(
            "cannot be cleared" in refused.stdout + refused.stderr,
            f"the refusal does not say why: {refused.stdout + refused.stderr!r}",
        )
        shown = json_of(
            sc.run(
                f"noust app container show {CMD_DOMAIN} --json",
                timeout=30,
                label=f"noust app container show {CMD_DOMAIN} --json",
            ),
            "app container show",
        )
        sc.check(shown["command"] is None, f"the refused change was kept: {shown['command']!r}")
    finally:
        container_clean(sc, CMD_DOMAIN, CMD_IMAGES)


# ---------------------------------------------------------------------------------------------
# A Compose stack: what Noust says about the deployment, a build-only secret, a cancelled build
# ---------------------------------------------------------------------------------------------

STACK_DOMAIN = "w6a-stack.test"
STACK_REPO = f"{FIXTURES}/w6a-stack"
STACK_URL = "git://127.0.0.1/w6a-stack"
STACK_PORT = 18161
STACK_ROOT = "/var/www/apps/w6a-stack-test"
STACK_TOKEN = "s3cret-w6a-build-token"

STACK_DOCKERFILE = """\
FROM nginx:alpine
COPY html /usr/share/nginx/html
RUN --mount=type=secret,id=build_token,required=false \\
    sh -c 'if [ -s /run/secrets/build_token ]; then \\
      sha256sum /run/secrets/build_token | cut -c1-12 > /usr/share/nginx/html/build-hash; \\
    else echo none > /usr/share/nginx/html/build-hash; fi'
"""

STACK_COMPOSE = f"""\
services:
  web:
    build:
      context: .
      secrets:
        - build_token
    command:
      - sh
      - -c
      - >-
        echo "commit=$${{NOUST_COMMIT}} release=$${{NOUST_RELEASE}} url=$${{NOUST_URL}}
        domain=$${{NOUST_DOMAIN}} branch=$${{NOUST_BRANCH}}" > /usr/share/nginx/html/env.txt
        && exec nginx -g 'daemon off;'
    environment:
      NOUST_COMMIT: ${{NOUST_COMMIT:-}}
      NOUST_RELEASE: ${{NOUST_RELEASE:-}}
      NOUST_URL: ${{NOUST_URL:-}}
      NOUST_DOMAIN: ${{NOUST_DOMAIN:-}}
      NOUST_BRANCH: ${{NOUST_BRANCH:-}}
    ports:
      - "127.0.0.1:{STACK_PORT}:80"
secrets:
  build_token:
    environment: BUILD_TOKEN
"""


def env_txt(sc: Scenario, label: str) -> dict[str, str]:
    """
    Read what the stack's container said about its deployment, through nginx.

    Args:
        sc: The scenario.
        label: Which moment it is, for the evidence.

    Returns:
        The ``name=value`` pairs the container wrote at start.
    """
    body = curl_host(sc, STACK_DOMAIN, "/env.txt").strip()
    sc.evidence.append(f"({label}) /env.txt: {body}")
    return dict(part.split("=", 1) for part in body.split() if "=" in part)


@scenario("compose_deployment_variables_build_secret_and_cancel")
def scenario_compose_variables(sc: Scenario) -> None:
    """
    The same variables and the same stopping a process application has, for a Compose stack.

    A stack is told which deployment it is (``NOUST_COMMIT``, ``NOUST_RELEASE``, ``NOUST_URL``,
    ``NOUST_DOMAIN``, ``NOUST_BRANCH``) through the ``.env`` Compose interpolates; a variable
    only the build is given reaches ``docker compose build`` and nothing else (no file, no
    container, no image layer), which a BuildKit secret makes true and a build argument would not;
    and an update that is building can be cancelled, which kills the real build and puts back the
    tree and the ``.env`` exactly as they were while the containers that served never notice.
    """
    require_cli(sc, "deploy cancel", what="stopping a build")
    require_docker(sc)
    pull_images(sc, "nginx:alpine")
    init_repo(sc, STACK_REPO)
    put_file(sc, f"{STACK_REPO}/Dockerfile", STACK_DOCKERFILE)
    put_file(sc, f"{STACK_REPO}/docker-compose.yml", STACK_COMPOSE)
    put_file(sc, f"{STACK_REPO}/html/index.html", "w6a v1\n")
    first = commit_all(sc, STACK_REPO, "v1")
    compose_create(sc, STACK_DOMAIN, STACK_URL, port=STACK_PORT)
    try:
        wait_served(sc, STACK_DOMAIN, "w6a v1")

        # The deployment tells the stack which one it is.
        said = env_txt(sc, "after the first deploy")
        sc.check(said.get("domain") == STACK_DOMAIN, f"NOUST_DOMAIN: {said!r}")
        sc.check(STACK_DOMAIN in said.get("url", ""), f"NOUST_URL: {said!r}")
        sc.check(said.get("branch") == "main", f"NOUST_BRANCH: {said!r}")
        sc.check(
            bool(said.get("commit")) and first.startswith(said["commit"]),
            f"NOUST_COMMIT is not the commit that was deployed ({first}): {said!r}",
        )
        sc.check(bool(said.get("release")), f"NOUST_RELEASE is empty: {said!r}")
        sc.check(curl_host(sc, STACK_DOMAIN, "/build-hash").strip() == "none", "no secret yet")

        # A variable only the build is given reaches the build and nothing else.
        sc.run(
            f"noust env set {STACK_DOMAIN} BUILD_TOKEN={STACK_TOKEN} --build-only",
            timeout=30,
            label=f"noust env set {STACK_DOMAIN} BUILD_TOKEN=... --build-only",
        )
        sc.run(
            f"noust update {STACK_DOMAIN} --no-cache",
            timeout=DEPLOY_TIMEOUT,
            label=f"noust update {STACK_DOMAIN} --no-cache",
        )
        wait_served(sc, STACK_DOMAIN, "w6a v1")
        expected = hashlib.sha256(STACK_TOKEN.encode()).hexdigest()[:12]
        seen = curl_host(sc, STACK_DOMAIN, "/build-hash").strip()
        sc.check(seen == expected, f"the build did not see the secret: {seen!r}, not {expected!r}")
        in_files = sc.run(
            f"grep -rl {shlex.quote(STACK_TOKEN)} {STACK_ROOT} --include='.env*' 2>/dev/null; true",
            timeout=30,
            label="grep the token in the application's .env files",
        ).stdout.strip()
        sc.check(not in_files, f"a build-only variable was written to a file: {in_files!r}")
        in_container = sc.run(
            f"docker inspect --format '{{{{json .Config.Env}}}}' {container_of(STACK_DOMAIN, 'web')}",
            timeout=30,
            label="the container's environment",
        ).stdout
        sc.check(
            "BUILD_TOKEN" not in in_container and STACK_TOKEN not in in_container,
            "a build-only variable reached the running container",
        )
        history = sc.run(
            "docker history --no-trunc "
            f"$(docker inspect --format '{{{{.Image}}}}' {container_of(STACK_DOMAIN, 'web')})",
            timeout=60,
            label="docker history of the image the stack runs",
        ).stdout
        sc.check(STACK_TOKEN not in history, "the secret is in the image's history")

        # Stop an update that is building: the real build dies, the old stack never notices.
        before = sc.run(
            f"cd {STACK_ROOT} && git rev-parse HEAD && sha256sum .env docker-compose.yml",
            timeout=30,
            label="the tree and the .env that serve, before the update",
        ).stdout
        slow = STACK_DOCKERFILE + "RUN echo w6a-slow-step && sleep 150\n"
        put_file(sc, f"{STACK_REPO}/Dockerfile", slow)
        put_file(sc, f"{STACK_REPO}/html/index.html", "w6a v2\n")
        commit_all(sc, STACK_REPO, "v2: a build that takes two minutes")
        sc.run(
            f"rm -f /tmp/w6a-update.code; nohup setsid bash -c "
            f"'noust update {STACK_DOMAIN} > /tmp/w6a-update.log 2>&1; echo $? > /tmp/w6a-update.code' "
            ">/dev/null 2>&1 &",
            timeout=15,
            label=f"(in the background) noust update {STACK_DOMAIN}",
        )
        deadline = time.monotonic() + 120
        building = False
        while time.monotonic() < deadline and not building:
            building = (
                run.docker_exec(
                    sc.container, "pgrep -f '[s]leep 150' >/dev/null", timeout=15, check=False
                ).returncode
                == 0
            )
            if not building:
                time.sleep(1)
        sc.check(building, "the slow step of the build never started")
        cancelled = sc.run(
            f"noust deploy cancel {STACK_DOMAIN} --wait 90",
            timeout=150,
            check=False,
            label=f"noust deploy cancel {STACK_DOMAIN}",
        )
        sc.check(
            cancelled.returncode == 0, f"the cancel failed: {cancelled.stdout}{cancelled.stderr}"
        )
        code = ""
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not code:
            code = run.docker_exec(
                sc.container, "cat /tmp/w6a-update.code 2>/dev/null", timeout=15, check=False
            ).stdout.strip()
            if not code:
                time.sleep(1)
        log = sc.run("cat /tmp/w6a-update.log", timeout=15, label="what the update printed").stdout
        sc.check(code not in ("", "0"), f"a cancelled update must not succeed: exit {code!r}")
        sc.check("cancel" in log.lower(), f"the update did not say it was cancelled: {log!r}")
        alive = run.docker_exec(
            sc.container, "pgrep -f '[s]leep 150' >/dev/null", timeout=15, check=False
        )
        sc.check(alive.returncode != 0, "the cancelled build is still running")
        wait_served(sc, STACK_DOMAIN, "w6a v1")
        after = sc.run(
            f"cd {STACK_ROOT} && git rev-parse HEAD && sha256sum .env docker-compose.yml",
            timeout=30,
            label="the tree and the .env that serve, after the cancel",
        ).stdout
        sc.check(after == before, f"the cancel did not put the tree back:\n{before}\n{after}")
        rows = store_rows(
            sc,
            "SELECT status, cancelled_at IS NOT NULL AS cancelled FROM deployments "
            "WHERE domain = @domain ORDER BY id DESC LIMIT 1",
            domain=STACK_DOMAIN,
            label="the update's row in the history",
        )
        sc.check(
            bool(rows) and rows[0]["status"] == "failed" and rows[0]["cancelled"] == 1,
            f"the update's row (status, cancelled): {rows!r}",
        )
    finally:
        sc.run(
            "rm -f /tmp/w6a-update.code /tmp/w6a-update.log; true",
            timeout=15,
            check=False,
            label="cleanup: the update's log",
        )
        compose_clean(sc, STACK_DOMAIN)


# ---------------------------------------------------------------------------------------------
# The Compose document of a stack deployed from one, and what deleting it keeps
# ---------------------------------------------------------------------------------------------

DOC_DOMAIN = "w6a-doc.test"
DOC_APP = "w6a-doc-test"
DOC_PORT = 18171


def document_of(version: str, *, command: str | None = None) -> str:
    """
    A Compose document that serves one word, from nginx, with a named volume.

    Args:
        version: What the page says.
        command: The shell line the service runs; a page and nginx by default.

    Returns:
        The document's text.
    """
    line = command or served_by(version)
    return f"""\
services:
  web:
    image: nginx:alpine
    command: [sh, -c, {json.dumps(line)}]
    ports:
      - "127.0.0.1:{DOC_PORT}:80"
    volumes:
      - data:/data
volumes:
  data: {{}}
"""


def source_directory(sc: Scenario) -> str:
    """Where Noust keeps the document of the stack ``DOC_APP``: the stack's source."""
    return sc.run(
        "/opt/noust/bin/python -c "
        f"\"from noust.recipes.deploy import recipe_source_dir; print(recipe_source_dir('{DOC_APP}'))\"",
        timeout=30,
        label="where Noust keeps a stack's document",
    ).stdout.strip()


@scenario("compose_document_edit_and_delete_keeps_volumes")
def scenario_compose_document(sc: Scenario) -> None:
    """
    A stack made from a document is edited without deleting it, and deleting it keeps its data.

    The document is the stack's source: it is read, judged (a document that asks for root on the
    host is refused whatever the server), kept without rebuilding anything, run by the next
    update, and put back when the one applied does not answer. Deleting the stack removes its
    containers and keeps its named volume unless it is asked for, and asks for the application's
    name to be typed, not a ``y``.
    """
    require_cli(sc, "app compose replace", what="editing a stack's document")
    require_docker(sc)
    pull_images(sc, "nginx:alpine")
    directory = source_directory(sc)
    put_file(sc, f"{directory}/docker-compose.yml", document_of("doc-v1"))
    create = (
        f"noust create -d {DOC_DOMAIN} -s {directory} -t docker-compose --port {DOC_PORT} --no-ssl"
    )
    sc.run(create, timeout=DEPLOY_TIMEOUT, label=create)
    try:
        wait_served(sc, DOC_DOMAIN, "doc-v1")
        printed = sc.run(
            f"noust app compose document {DOC_DOMAIN}",
            timeout=30,
            label=f"noust app compose document {DOC_DOMAIN}",
        ).stdout
        sc.check(
            printed == document_of("doc-v1"), f"the document is not what was deployed: {printed!r}"
        )

        # A document that asks for root on the host is refused, and nothing is written.
        put_file(sc, "/tmp/w6a-hostile.yml", document_of("doc-v2") + "    privileged: true\n")
        hostile = sc.run(
            f"noust app compose replace {DOC_DOMAIN} --from /tmp/w6a-hostile.yml",
            timeout=60,
            check=False,
            label=f"noust app compose replace {DOC_DOMAIN} --from <a privileged document>",
        )
        sc.check(hostile.returncode != 0, "a privileged document must be refused")
        sc.check(
            "privileged" in (hostile.stdout + hostile.stderr),
            f"the refusal does not name the problem: {hostile.stdout + hostile.stderr!r}",
        )

        # A good one is kept, judged first, and not run until the stack is updated.
        put_file(sc, "/tmp/w6a-v2.yml", document_of("doc-v2"))
        checked = sc.run(
            f"noust app compose replace {DOC_DOMAIN} --from /tmp/w6a-v2.yml --check",
            timeout=60,
            label=f"noust app compose replace {DOC_DOMAIN} --from /tmp/w6a-v2.yml --check",
        )
        sc.check(
            "Service web" in checked.stdout,
            f"--check did not list the services: {checked.stdout!r}",
        )
        sc.run(
            f"noust app compose replace {DOC_DOMAIN} --from /tmp/w6a-v2.yml",
            timeout=60,
            label=f"noust app compose replace {DOC_DOMAIN} --from /tmp/w6a-v2.yml",
        )
        body = curl_host(sc, DOC_DOMAIN)
        sc.check("doc-v1" in body, f"writing a document must not run it: {body!r}")
        pending = sc.run(
            f"noust app pending {DOC_DOMAIN} --json",
            timeout=30,
            label=f"noust app pending {DOC_DOMAIN} --json",
        )
        areas = {item["area"] for item in json_of(pending, "app pending")["items"]}
        sc.check("compose" in areas, f"the saved document is not a pending change: {areas!r}")
        sc.run(
            f"noust update {DOC_DOMAIN} --force",
            timeout=DEPLOY_TIMEOUT,
            label=f"noust update {DOC_DOMAIN} --force",
        )
        wait_served(sc, DOC_DOMAIN, "doc-v2")

        # A document whose service dies at once is applied, refused by the health gate, and the
        # previous document is back, running.
        put_file(sc, "/tmp/w6a-dead.yml", document_of("doc-v3", command="exit 1"))
        applied = sc.run(
            f"noust app compose replace {DOC_DOMAIN} --from /tmp/w6a-dead.yml --apply",
            timeout=DEPLOY_TIMEOUT,
            check=False,
            label=f"noust app compose replace {DOC_DOMAIN} --from <a service that dies> --apply",
        )
        sc.check(applied.returncode != 0, "a document that does not come up must fail the update")
        wait_served(sc, DOC_DOMAIN, "doc-v2")
        kept = sc.run(
            f"cat {directory}/docker-compose.yml", timeout=15, label="the document kept"
        ).stdout
        sc.check(kept == document_of("doc-v2"), f"the previous document was not put back: {kept!r}")

        # Deleting keeps the named volume, and the name has to be typed.
        sc.run(
            f"docker exec {container_of(DOC_DOMAIN, 'web')} sh -c 'echo kept > /data/marker'",
            timeout=30,
            label="write a marker into the stack's named volume",
        )
        refused = sc.run(
            f"printf 'y\\n' | noust delete {DOC_DOMAIN}",
            timeout=60,
            check=False,
            label=f"printf 'y' | noust delete {DOC_DOMAIN}",
        )
        sc.check(
            "Type its name" in refused.stdout + refused.stderr,
            f"delete did not ask for the name: {refused.stdout + refused.stderr!r}",
        )
        sc.check(
            curl_host(sc, DOC_DOMAIN).strip() != "",
            "a delete whose name was not typed removed something",
        )
        sc.run(
            f"printf '{DOC_DOMAIN}\\n' | noust delete {DOC_DOMAIN}",
            timeout=180,
            label=f"printf '{DOC_DOMAIN}' | noust delete {DOC_DOMAIN}",
        )
        volumes = sc.run(
            f"docker volume ls -q -f label=com.docker.compose.project={DOC_APP}",
            timeout=30,
            label="docker volume ls: what the deleted stack left",
        ).stdout.split()
        sc.check(
            volumes == [f"{DOC_APP}_data"], f"the volume must stay unless asked for: {volumes!r}"
        )

        # Bring it back from the same document (the last one kept: v2) and delete it asking for
        # the data to go too.
        sc.run(create, timeout=DEPLOY_TIMEOUT, label=create)
        wait_served(sc, DOC_DOMAIN, "doc-v2")
        marker = sc.run(
            f"docker exec {container_of(DOC_DOMAIN, 'web')} cat /data/marker",
            timeout=30,
            label="read the marker back from the kept volume",
        ).stdout.strip()
        sc.check(marker == "kept", f"the volume's data was lost: {marker!r}")
        sc.run(
            f"noust delete {DOC_DOMAIN} -y --remove-volumes",
            timeout=180,
            label=f"noust delete {DOC_DOMAIN} -y --remove-volumes",
        )
        left = sc.run(
            f"docker volume ls -q -f label=com.docker.compose.project={DOC_APP}",
            timeout=30,
            label="docker volume ls: after --remove-volumes",
        ).stdout.split()
        sc.check(not left, f"--remove-volumes left volumes behind: {left!r}")
    finally:
        sc.run("rm -f /tmp/w6a-hostile.yml /tmp/w6a-v2.yml /tmp/w6a-dead.yml; true", check=False)
        compose_clean(sc, DOC_DOMAIN)


# ---------------------------------------------------------------------------------------------
# A monorepo on releases: its units are hardened, going back is instant, a build can be cancelled
# ---------------------------------------------------------------------------------------------

MONO_DOMAIN = "w6a-mono.test"
MONO_APP = "w6a-mono-test"
MONO_REPO = f"{FIXTURES}/w6a-mono"
MONO_URL = "git://127.0.0.1/w6a-mono"
MONO_ROOT = f"/var/www/apps/{MONO_APP}"
MONO_WORKSPACES = ("web", "api")
#: The subdomain each workspace is served on: the deployer gives `web` the name `app`.
MONO_SUBDOMAINS = {"web": "app", "api": "api"}

MONO_SERVER = """\
const http = require("http");
const fs = require("fs");
const version = fs.readFileSync(__dirname + "/VERSION", "utf8").trim();
http
  .createServer((request, response) => response.end(`${NAME} ${version}\\n`))
  .listen(process.env.PORT || 3000, "127.0.0.1");
"""

MONO_BUILD = """\
const fs = require("fs");
const { execSync } = require("child_process");
if (fs.existsSync(__dirname + "/SLOW")) execSync("sleep 150");
fs.writeFileSync(__dirname + "/built.txt", "ok\\n");
"""


def make_monorepo(sc: Scenario) -> str:
    """
    Build a two-workspace pnpm monorepo that needs no network, and give back its first commit.

    Args:
        sc: The scenario.

    Returns:
        The commit.
    """
    init_repo(sc, MONO_REPO)
    put_file(
        sc,
        f"{MONO_REPO}/package.json",
        json.dumps({"name": "w6a-mono", "private": True, "scripts": {"build": "pnpm -r run build"}})
        + "\n",
    )
    put_file(sc, f"{MONO_REPO}/pnpm-workspace.yaml", 'packages:\n  - "apps/*"\n')
    put_file(sc, f"{MONO_REPO}/turbo.json", '{"tasks": {}}\n')
    for name in MONO_WORKSPACES:
        package = {
            "name": name,
            "version": "1.0.0",
            "scripts": {"build": "node build.js", "start": "node server.js"},
        }
        put_file(sc, f"{MONO_REPO}/apps/{name}/package.json", json.dumps(package) + "\n")
        put_file(sc, f"{MONO_REPO}/apps/{name}/server.js", MONO_SERVER.replace("${NAME}", name))
        put_file(sc, f"{MONO_REPO}/apps/{name}/build.js", MONO_BUILD)
        put_file(sc, f"{MONO_REPO}/apps/{name}/VERSION", "v1\n")
    sc.run(
        f"cd {MONO_REPO} && pnpm install --lockfile-only",
        timeout=120,
        label="(fixture repo) pnpm install --lockfile-only",
    )
    return commit_all(sc, MONO_REPO, "v1")


def check_sandboxed(sc: Scenario, when: str) -> None:
    """Every workspace's unit is active and runs in the systemd sandbox."""
    for name in MONO_WORKSPACES:
        unit = f"{MONO_APP}-{name}"
        shown = sc.run(
            f"systemctl show {unit} -p ActiveState -p ProtectSystem -p NoNewPrivileges "
            "-p PrivateTmp",
            timeout=30,
            label=f"systemctl show {unit} ({when})",
        ).stdout
        settings = dict(line.split("=", 1) for line in shown.split() if "=" in line)
        sc.check(settings.get("ActiveState") == "active", f"{unit} {when}: {settings!r}")
        sc.check(
            settings.get("ProtectSystem") == "strict",
            f"{unit} is not in the sandbox {when}: {settings!r}",
        )
        sc.check(
            settings.get("NoNewPrivileges") == "yes" and settings.get("PrivateTmp") == "yes",
            f"{unit} {when}: {settings!r}",
        )


def mono_answers(sc: Scenario, version: str) -> None:
    """Wait until both workspaces answer with ``version`` on their own subdomain."""
    for name in MONO_WORKSPACES:
        wait_served(sc, f"{MONO_SUBDOMAINS[name]}.{MONO_DOMAIN}", f"{name} {version}")


@scenario("monorepo_releases_hardened_units_and_cancel")
def scenario_monorepo(sc: Scenario) -> None:
    """
    A monorepo has what a process application has: a hardened unit, an instant way back, a stop.

    Created in place (a monorepo is never created on releases) from 4.0 it runs every workspace in
    the systemd sandbox (``ProtectSystem=strict`` and the rest) from its first deploy and answers
    behind it; the explicit migration onto releases rewrites each unit and keeps the sandbox; going
    back to the previous release is a link and a restart; and an update whose build is cancelled
    leaves the release it was making removed, the one that serves untouched, and the deployment
    recorded as cancelled.
    """
    require_cli(sc, "deploy cancel", what="stopping a build")
    require_cli(sc, "app hardening", what="the systemd sandbox")
    first = make_monorepo(sc)
    refused = sc.run(
        f"noust create -d {MONO_DOMAIN} -s {MONO_URL} -t monorepo --layout releases --no-ssl",
        timeout=DEPLOY_TIMEOUT,
        check=False,
        label=f"noust create -d {MONO_DOMAIN} -s {MONO_URL} -t monorepo --layout releases --no-ssl",
    )
    sc.check(
        refused.returncode != 0
        and "cannot use the releases layout" in refused.stdout + refused.stderr,
        "a monorepo is created in place and a request for releases says why not",
    )
    sc.run(
        f"noust create -d {MONO_DOMAIN} -s {MONO_URL} -t monorepo --no-ssl",
        timeout=DEPLOY_TIMEOUT,
        label=f"noust create -d {MONO_DOMAIN} -s {MONO_URL} -t monorepo --no-ssl",
    )
    try:
        mono_answers(sc, "v1")
        check_sandboxed(sc, "in place")
        sc.run(
            f"noust app hardening status {MONO_DOMAIN}",
            timeout=60,
            label=f"noust app hardening status {MONO_DOMAIN}",
        )

        # The explicit migration rewrites every unit and keeps the sandbox.
        sc.run(
            f"noust app migrate {MONO_DOMAIN} --yes",
            timeout=DEPLOY_TIMEOUT,
            label=f"noust app migrate {MONO_DOMAIN} --yes",
        )
        mono_answers(sc, "v1")
        check_sandboxed(sc, "after the migration onto releases")

        # A second release, then back: a link and a restart.
        for name in MONO_WORKSPACES:
            put_file(sc, f"{MONO_REPO}/apps/{name}/VERSION", "v2\n")
        commit_all(sc, MONO_REPO, "v2")
        sc.run(
            f"noust update {MONO_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            label=f"noust update {MONO_DOMAIN}",
        )
        mono_answers(sc, "v2")
        releases = run.json_of(
            sc.run(
                f"noust releases list {MONO_DOMAIN} --json",
                timeout=30,
                label=f"noust releases list {MONO_DOMAIN} --json",
            ),
            "releases list",
        )["items"]
        sc.check(len(releases) == 2, f"two releases are kept: {releases!r}")
        sc.run(
            f"noust releases rollback {MONO_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            label=f"noust releases rollback {MONO_DOMAIN}",
        )
        mono_answers(sc, "v1")

        # A build that is cancelled leaves nothing behind.
        before = sc.run(
            f"ls {MONO_ROOT}/releases | sort; readlink {MONO_ROOT}/current",
            timeout=30,
            label="the releases and the one that serves, before",
        ).stdout
        put_file(sc, f"{MONO_REPO}/apps/web/SLOW", "slow\n")
        commit_all(sc, MONO_REPO, "v3: a build that takes two minutes")
        sc.run(
            f"rm -f /tmp/w6a-mono.code; nohup setsid bash -c "
            f"'noust update {MONO_DOMAIN} > /tmp/w6a-mono.log 2>&1; echo $? > /tmp/w6a-mono.code' "
            ">/dev/null 2>&1 &",
            timeout=15,
            label=f"(in the background) noust update {MONO_DOMAIN}",
        )
        deadline = time.monotonic() + 180
        building = False
        while time.monotonic() < deadline and not building:
            building = (
                run.docker_exec(
                    sc.container, "pgrep -f '[s]leep 150' >/dev/null", timeout=15, check=False
                ).returncode
                == 0
            )
            if not building:
                time.sleep(1)
        sc.check(building, "the slow step of the build never started")
        cancelled = sc.run(
            f"noust deploy cancel {MONO_DOMAIN} --wait 90",
            timeout=150,
            check=False,
            label=f"noust deploy cancel {MONO_DOMAIN}",
        )
        sc.check(
            cancelled.returncode == 0, f"the cancel failed: {cancelled.stdout}{cancelled.stderr}"
        )
        code = ""
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not code:
            code = run.docker_exec(
                sc.container, "cat /tmp/w6a-mono.code 2>/dev/null", timeout=15, check=False
            ).stdout.strip()
            if not code:
                time.sleep(1)
        sc.check(code not in ("", "0"), f"a cancelled update must not succeed: exit {code!r}")
        mono_answers(sc, "v1")
        after = sc.run(
            f"ls {MONO_ROOT}/releases | sort; readlink {MONO_ROOT}/current",
            timeout=30,
            label="the releases and the one that serves, after",
        ).stdout
        sc.check(after == before, f"the cancelled release was not removed:\n{before}\n{after}")
        rows = store_rows(
            sc,
            "SELECT status, cancelled_at IS NOT NULL AS cancelled FROM deployments "
            "WHERE domain = @domain ORDER BY id DESC LIMIT 1",
            domain=MONO_DOMAIN,
            label="the update's row in the history",
        )
        sc.check(
            bool(rows) and rows[0]["status"] == "failed" and rows[0]["cancelled"] == 1,
            f"the update's row (status, cancelled): {rows!r}",
        )
        sc.check(bool(first), "the fixture has a first commit")
    finally:
        sc.run(
            "rm -f /tmp/w6a-mono.code /tmp/w6a-mono.log; true",
            timeout=15,
            check=False,
            label="cleanup: the update's log",
        )
        sc.run(
            f"noust delete {MONO_DOMAIN} -f",
            timeout=180,
            check=False,
            label=f"cleanup: noust delete {MONO_DOMAIN}",
        )
