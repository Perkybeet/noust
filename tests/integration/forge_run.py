#!/usr/bin/env python3
"""
Gitea and Forgejo against Noust's code-host integrations (A-21, A-07).

The unit tests (``tests/test_forge_statuses.py``, ``test_web_hooks_forgejo_gogs.py``) drive the
forge clients and the webhook receiver against doubles written by the same author, which can
only agree with the code. This harness holds them to the real programs:

- ``gitea`` and ``forgejo``: a server in a container, a user, a token, a repository with a pull
  request. Then, through :class:`noust.integrations.forges.api.GiteaAPI` and the guarded
  :class:`noust.deployers.importers.remote.RemoteClient`:

  * a deployment's start and end appear as ``noust/deploy`` on the commit, the last state
    replacing the one before it (``pending`` while it runs, then ``success``, ``failure`` or
    ``error`` for a cancelled one), and a wrong token is a 401 with the fix in its hint;
  * a preview's comment is created once on the pull request and edited, not repeated, by the
    next call; a comment someone deleted is created again;
  * the server's own webhook, signed with a secret, is verified by the receiver
    (``web/api/hooks.py``) under the header the server sent, refused with another secret or
    a changed body, and its push and pull request payloads are read to the branch, the
    commit and the pull request number the server holds.

Noust's own clients are HTTPS-only for a code host and never follow a redirect; to reach a
server on loopback the harness lists it under ``notifications.allow_private_hosts`` (as an
operator does), and checks that without that the guard refuses it.

Nothing here touches the machine's configuration: ``Config`` points at a throwaway file.

This file is intentionally excluded from pytest's default collection (it does not match
``test_*.py``): it drives Docker.

Usage:
    .venv/bin/python tests/integration/forge_run.py
    .venv/bin/python tests/integration/forge_run.py --scenario gitea
    .venv/bin/python tests/integration/forge_run.py --scenario forgejo --keep

Docker safety, because the machine it runs on also holds containers that are not this
harness's: it creates only containers named ``noust-test-w6x-*``, labelled ``noust.test=1``,
publishes one loopback port that it found free, removes them by exact name, and never prunes
anything. The images are pinned by digest (``NOUST_TEST_GITEA_IMAGE`` and
``NOUST_TEST_FORGEJO_IMAGE`` replace them); one that is not on the machine is pulled. A scenario
that cannot run (no Docker, no image, no network) reports SKIP with the reason, never PASS.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.error import HTTPError

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))


def _load(name: str) -> ModuleType:
    """
    Load a sibling harness as a module, without touching ``sys.path``.

    Args:
        name: The file, without ``.py``.

    Returns:
        The module.
    """
    path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"_noust_it_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# The one implementation of "start a labelled container, run in it, remove it by name".
identity = _load("identity")
Containers = identity.Containers
Skip = identity.Skip
REPORT = identity.REPORT
docker = identity.docker
free_port = identity.free_port

#: Pinned by digest: what the registry served when this was written. Both are Alpine images
#: with a ``git`` account that owns the data and a busybox ``nc``.
GITEA_IMAGE = (
    "docker.gitea.com/gitea@sha256:c168e7ccb767164793a67e1e874639488260795567337452b06292d1515bea12"
)
FORGEJO_IMAGE = "codeberg.org/forgejo/forgejo@sha256:5effb7305584aca479b29fde6f9631a6dbe86ae798ae02eeea33a3666f0c0bf8"

USER = "noust"
PASSWORD = "correct horse battery staple"
REPO = "shop"
SECRET = "w6x-webhook-secret"
SINK_PORT = 9000


# ------------------------------------------------------------------------------------ http


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A setup call that is redirected is a setup that went wrong."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str):
        raise HTTPError(req.full_url, code, f"redirected to {newurl}", headers, fp)


def call(
    base: str,
    method: str,
    path: str,
    *,
    token: str | None = None,
    basic: tuple[str, str] | None = None,
    body: Any = None,
) -> tuple[int, Any]:
    """
    Make one request of the server's API, as a person setting it up would.

    Args:
        base: ``http://127.0.0.1:PORT``.
        method: The HTTP method.
        path: Under ``/api/v1``.
        token: An access token.
        basic: ``(user, password)``, for the call that makes the token.
        body: A JSON body.

    Returns:
        The status and the decoded JSON (None for no body).
    """
    headers = {"Accept": "application/json"}
    if token is not None:
        headers["Authorization"] = f"token {token}"
    if basic is not None:
        pair = base64.b64encode(f"{basic[0]}:{basic[1]}".encode()).decode()
        headers["Authorization"] = f"Basic {pair}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{base}/api/v1{path}", data=data, method=method, headers=headers
    )
    try:
        with urllib.request.build_opener(_NoRedirects).open(request, timeout=30) as response:
            raw = response.read()
            status = response.status
    except HTTPError as exc:
        raw = exc.read()
        status = exc.code
    return status, (json.loads(raw) if raw.strip() else None)


def must(result: tuple[int, Any], *ok: int) -> Any:
    """
    Args:
        result: What :func:`call` returned.
        *ok: The statuses that are success; 200, 201 and 204 by default.

    Returns:
        The decoded body.

    Raises:
        RuntimeError: Any other status, with what the server said.
    """
    status, body = result
    if status not in (ok or (200, 201, 204)):
        raise RuntimeError(f"unexpected {status}: {str(body)[:300]}")
    return body


# ----------------------------------------------------------------------------- the servers


def image_for(env: str, default: str) -> str:
    """
    Find the image to run, and pull it when this machine has not got it.

    Args:
        env: The environment variable that replaces the default.
        default: The pinned image.

    Returns:
        The image reference.

    Raises:
        Skip: Docker is not usable, or the image cannot be had.
    """
    if shutil.which("docker") is None or docker("version", check=False).returncode != 0:
        raise Skip("Docker is not available")
    image = os.environ.get(env, default)
    if docker("image", "inspect", image, check=False).returncode != 0:
        pulled = docker("pull", image, check=False, timeout=900)
        if pulled.returncode != 0:
            raise Skip(f"the image {image} is not here and cannot be pulled")
    return image


def start_server(
    containers: Containers, *, kind: str, image: str, env_prefix: str
) -> tuple[str, str, int]:
    """
    Start a Gitea or a Forgejo, make its first user and a token for it.

    Args:
        containers: This run's containers.
        kind: ``gitea`` or ``forgejo``; also the binary and the container's name.
        image: The image.
        env_prefix: How the image reads settings from the environment.

    Returns:
        The container's name, the token, and the loopback port it is published on.

    Raises:
        RuntimeError: The server did not come up, or refused to make the user.
    """
    port = free_port()
    name = f"noust-test-w6x-{kind}"
    settings = {
        "security__INSTALL_LOCK": "true",
        "database__DB_TYPE": "sqlite3",
        "server__ROOT_URL": f"http://127.0.0.1:{port}/",
        "server__HTTP_PORT": "3000",
        "service__DISABLE_REGISTRATION": "true",
        # The webhook goes to a sink inside the same container, on loopback.
        "webhook__ALLOWED_HOST_LIST": "loopback",
        "log__LEVEL": "Warn",
    }
    environment: list[str] = []
    for key, value in settings.items():
        environment += ["-e", f"{env_prefix}__{key}={value}"]
    containers.run(name, image, "-p", f"127.0.0.1:{port}:3000", *environment)
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            if call(base, "GET", "/version")[0] == 200:
                break
        except OSError:
            pass
        time.sleep(1.5)
    else:
        raise RuntimeError(f"{kind} did not answer: {containers.logs(name)[-600:]}")
    made = docker(
        "exec",
        "-u",
        "git",
        name,
        kind,
        "admin",
        "user",
        "create",
        "--admin",
        "--username",
        USER,
        "--password",
        PASSWORD,
        "--email",
        "noust@example.test",
        "--must-change-password=false",
        check=False,
    )
    if made.returncode != 0:
        raise RuntimeError(f"{kind} would not make its first user: {made.stderr.strip()[-300:]}")
    token = must(
        call(
            base,
            "POST",
            f"/users/{USER}/tokens",
            basic=(USER, PASSWORD),
            body={"name": "noust", "scopes": ["all"]},
        )
    )["sha1"]
    return name, token, port


def make_pull_request(base: str, token: str) -> tuple[str, int]:
    """
    Make a repository with a main branch, a branch with one more commit, and a pull request.

    Args:
        base: The server.
        token: A token of its user.

    Returns:
        The commit the branch ends at and the pull request's number.
    """
    must(
        call(
            base,
            "POST",
            "/user/repos",
            token=token,
            body={"name": REPO, "auto_init": True, "default_branch": "main"},
        )
    )
    must(
        call(
            base,
            "POST",
            f"/repos/{USER}/{REPO}/branches",
            token=token,
            body={"new_branch_name": "feature", "old_branch_name": "main"},
        )
    )
    committed = must(
        call(
            base,
            "POST",
            f"/repos/{USER}/{REPO}/contents/page.txt",
            token=token,
            body={
                "branch": "feature",
                "message": "Add a page",
                "content": base64.b64encode(b"hello\n").decode(),
            },
        )
    )
    sha = committed["commit"]["sha"]
    pull = must(
        call(
            base,
            "POST",
            f"/repos/{USER}/{REPO}/pulls",
            token=token,
            body={"head": "feature", "base": "main", "title": "Add a page"},
        )
    )
    return sha, int(pull["number"])


# --------------------------------------------------------------------------- the scenario


#: What answers each connection of the sink: the request is kept whole (its header block as it
#: came, then exactly Content-Length bytes) in a file that appears atomically, then answered.
SINK_HANDLER = r"""#!/bin/sh
f=/tmp/hook.$(date +%s).$$
len=0
: > "$f.part"
while IFS= read -r line; do
  printf '%s\n' "$line" >> "$f.part"
  case "$line" in
    [Cc]ontent-[Ll]ength:*) len=$(printf '%s' "${line#*:}" | tr -d ' \r') ;;
  esac
  [ -z "${line%$(printf '\r')}" ] && break
done
[ "$len" -gt 0 ] && head -c "$len" >> "$f.part"
mv "$f.part" "$f"
printf 'HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n'
"""


class Sink:
    """
    A webhook receiver inside the server's own container: busybox ``nc`` as a persistent
    server (one process per connection, so two deliveries at once are both kept), writing each
    request to a file so the exact bytes the server signed can be read back.

    Args:
        containers: This run's containers.
        name: The container.

    Raises:
        Skip: The image has no ``nc`` that can serve.
    """

    def __init__(self, containers: Containers, name: str) -> None:
        self.containers = containers
        self.name = name
        installed = docker(
            "exec",
            "-i",
            name,
            "sh",
            "-c",
            "cat > /tmp/handler.sh && chmod 755 /tmp/handler.sh",
            input_text=SINK_HANDLER,
            check=False,
        )
        started = docker(
            "exec",
            "-d",
            name,
            "nc",
            "-lk",
            "-p",
            str(SINK_PORT),
            "-e",
            "/tmp/handler.sh",
            check=False,
        )
        if installed.returncode != 0 or started.returncode != 0:
            raise Skip(
                f"the image has no nc to receive a webhook with: {started.stderr.strip()[-200:]}"
            )
        self.seen = 0

    def next(self, timeout: float = 90) -> tuple[dict[str, str], bytes]:
        """
        Wait for the next delivery, in the order they arrived.

        Args:
            timeout: Seconds.

        Returns:
            Its headers (lower-case names) and its body, byte for byte.

        Raises:
            RuntimeError: Nothing arrived.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(1.5)
            listed = docker(
                "exec",
                self.name,
                "sh",
                "-c",
                "ls /tmp | grep -E '^hook\\.[0-9]+\\.[0-9]+$' | sort -t. -k2,2n -k3,3n",
                check=False,
            )
            files = listed.stdout.split()
            if len(files) <= self.seen:
                continue
            done = docker("exec", self.name, "base64", f"/tmp/{files[self.seen]}", check=False)
            raw = base64.b64decode(done.stdout) if done.returncode == 0 else b""
            head, _, body = raw.partition(b"\r\n\r\n")
            headers = {}
            for line in head.decode("latin-1").split("\r\n")[1:]:
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()
            length = int(headers.get("content-length", "0"))
            if len(body) >= length:
                self.seen += 1
                return headers, body[:length]
        raise RuntimeError("the server delivered no webhook")


class FakeRequest:
    """The one thing the receiver reads of a request here: its headers."""

    def __init__(self, headers: dict[str, str]) -> None:
        from starlette.datastructures import Headers

        self.headers = Headers(headers)


def isolated_config(workdir: Path, *, allow: list[str]) -> None:
    """
    Point ``Config`` at a throwaway file that lists the hosts an operator trusts.

    Args:
        workdir: A directory this run owns.
        allow: ``notifications.allow_private_hosts``.
    """
    from noust.core import config as config_module
    from noust.core.config import Config

    config_module.DEFAULT_CONFIG_PATH = workdir / "etc" / "config.yaml"
    Config.reset_instance()
    Config().set("notifications.allow_private_hosts", allow)


def forge_scenario(
    workdir: Path,
    containers: Containers,
    keep: bool,
    *,
    kind: str,
    image_env: str,
    image_default: str,
    env_prefix: str,
) -> None:
    """
    Everything Noust writes to, and reads from, one Gitea or Forgejo.

    Args:
        workdir: A directory this run owns.
        containers: This run's containers.
        keep: Whether the containers are left running.
        kind: ``gitea`` or ``forgejo``.
        image_env: The variable that replaces the image.
        image_default: The pinned image.
        env_prefix: How the image reads settings from the environment.

    Raises:
        Skip: Docker or the image is not usable.
    """
    from noust.core.exceptions import IntegrationError
    from noust.core.forge_events import parse_pull_request, parse_push_change
    from noust.deployers.importers.remote import RemoteClient, RemoteRefused
    from noust.integrations.forges import api as forge_api
    from noust.web.api import hooks

    image = image_for(image_env, image_default)
    name, token, port = start_server(containers, kind=kind, image=image, env_prefix=env_prefix)
    base = f"http://127.0.0.1:{port}"
    sha, number = make_pull_request(base, token)
    platform = kind.capitalize()
    api_url = f"{base}/api/v1"

    # The guard: a server on this machine is only contacted when it is listed.
    isolated_config(workdir, allow=[])
    guarded = RemoteClient(api_url, ("Authorization", f"token {token}"), platform=platform)
    refused = False
    try:
        guarded.get("/version")
    except IntegrationError as exc:
        refused = "allow_private_hosts" in (exc.details or "")
    REPORT.check(f"{kind}: a server on this machine is refused until it is listed", refused)

    isolated_config(workdir, allow=["127.0.0.1"])
    client = RemoteClient(
        api_url,
        ("Authorization", f"token {token}"),
        platform=platform,
        hints=forge_api._GITEA_HINTS,
    )
    repo = forge_api.GiteaAPI(client, f"{USER}/{REPO}")

    # --- commit statuses
    def state_of_commit() -> tuple[str, list[dict[str, Any]]]:
        # The check's own latest state, not the server's combined one: Gitea shows a lone
        # "error" as a combined "failure", and the check is what the operator's host displays.
        combined = must(
            call(base, "GET", f"/repos/{USER}/{REPO}/commits/{sha}/status", token=token)
        )
        statuses = combined.get("statuses") or []
        own = [s["status"] for s in statuses if s["context"] == "noust/deploy"]
        return (own[0] if own else combined["state"]), statuses

    repo.set_status(
        sha,
        forge_api.RUNNING,
        description="Deploying",
        url="https://noust.example.test/apps/shop",
        context="noust/deploy",
    )
    state, statuses = state_of_commit()
    REPORT.check(
        f"{kind}: a deployment that starts marks the commit pending as noust/deploy",
        state == "pending" and [s["context"] for s in statuses] == ["noust/deploy"],
        f"{state} {[s['context'] for s in statuses]}",
    )
    REPORT.check(
        f"{kind}: the status leads to the application",
        statuses[0].get("target_url") == "https://noust.example.test/apps/shop",
        str(statuses[0].get("target_url")),
    )
    repo.set_status(
        sha,
        forge_api.SUCCESS,
        description="Deployed",
        url="https://noust.example.test/apps/shop",
        context="noust/deploy",
    )
    state, statuses = state_of_commit()
    REPORT.check(
        f"{kind}: the end replaces the start under the same name",
        state == "success" and len([s for s in statuses if s["context"] == "noust/deploy"]) == 1,
        f"{state} {len(statuses)} statuses",
    )
    repo.set_status(
        sha,
        forge_api.FAILURE,
        description="Rolled back",
        url="https://noust.example.test/apps/shop",
        context="noust/deploy",
    )
    REPORT.check(
        f"{kind}: a failed deployment marks the commit failed",
        state_of_commit()[0] == "failure",
        state_of_commit()[0],
    )
    repo.set_status(
        sha,
        forge_api.CANCELLED,
        description="Cancelled",
        url="https://noust.example.test/apps/shop",
        context="noust/deploy",
    )
    REPORT.check(
        f"{kind}: a cancelled one is an error, not a failure",
        state_of_commit()[0] == "error",
        state_of_commit()[0],
    )

    wrong = forge_api.GiteaAPI(
        RemoteClient(
            api_url,
            ("Authorization", "token not-a-token"),
            platform=platform,
            hints=forge_api._GITEA_HINTS,
        ),
        f"{USER}/{REPO}",
    )
    try:
        wrong.set_status(
            sha, forge_api.SUCCESS, description="x", url="https://x.test", context="noust/deploy"
        )
        rejected = None
    except RemoteRefused as exc:
        rejected = exc
    REPORT.check(
        f"{kind}: a token the server does not know is a 401 with the fix",
        rejected is not None and rejected.status == 401 and bool(rejected.details),
        repr(rejected),
    )

    # --- the comment on a pull request
    def comments() -> list[dict[str, Any]]:
        return must(
            call(base, "GET", f"/repos/{USER}/{REPO}/issues/{number}/comments", token=token)
        )

    first = repo.upsert_comment(number, "Preview: https://pr-1.example.test", None)
    listed = comments()
    REPORT.check(
        f"{kind}: a preview's comment is created on the pull request",
        first is not None and [c["body"] for c in listed] == ["Preview: https://pr-1.example.test"],
        str([c["body"] for c in listed]),
    )
    second = repo.upsert_comment(number, "Preview: updated", first)
    listed = comments()
    REPORT.check(
        f"{kind}: the next call edits that comment instead of adding another",
        second == first and [c["body"] for c in listed] == ["Preview: updated"],
        f"{first} -> {second}; {[c['body'] for c in listed]}",
    )
    must(call(base, "DELETE", f"/repos/{USER}/{REPO}/issues/comments/{first}", token=token))
    third = repo.upsert_comment(number, "Preview: again", first)
    listed = comments()
    REPORT.check(
        f"{kind}: a comment someone deleted is created again",
        third is not None and third != first and [c["body"] for c in listed] == ["Preview: again"],
        f"{first} -> {third}; {[c['body'] for c in listed]}",
    )

    # --- the server's own webhooks, signed
    sink = Sink(containers, name)
    must(
        call(
            base,
            "POST",
            f"/repos/{USER}/{REPO}/hooks",
            token=token,
            body={
                "type": kind,
                "active": True,
                "events": ["push", "pull_request"],
                "config": {
                    "url": f"http://127.0.0.1:{SINK_PORT}/hook",
                    "content_type": "json",
                    "secret": SECRET,
                },
            },
        )
    )
    must(
        call(
            base,
            "POST",
            f"/repos/{USER}/{REPO}/contents/more.txt",
            token=token,
            body={
                "branch": "feature",
                "message": "More",
                "content": base64.b64encode(b"more\n").decode(),
            },
        )
    )
    # Adding a commit to the branch of an open pull request is two deliveries, in either order:
    # the push, and the pull request being synchronized.
    arrived = [sink.next(), sink.next()]
    own_event = f"x-{kind}-event"
    pushes = [d for d in arrived if d[0].get(own_event) == "push"]
    syncs = [d for d in arrived if d[0].get(own_event) == "pull_request"]
    REPORT.check(
        f"{kind}: a commit on the branch of a pull request is a push and a synchronize",
        len(pushes) == 1 and len(syncs) == 1,
        str([d[0].get(own_event) for d in arrived]),
    )
    headers, body = pushes[0]
    request = FakeRequest(headers)
    verified = hooks._verify_provider(SECRET, body, request)
    signature_headers = sorted(h for h in headers if h.endswith("-signature"))
    REPORT.check(
        f"{kind}: the receiver verifies the signature the server sent",
        verified == kind,
        f"verified as {verified}; headers {signature_headers}",
    )
    REPORT.check(
        f"{kind}: another secret, or a changed body, verifies as nothing",
        hooks._verify_provider("another-secret", body, request) is None
        and hooks._verify_provider(SECRET, body + b" ", request) is None,
    )
    payload = json.loads(body)
    event = hooks._event_kind(verified or kind, request, payload)
    branches = hooks._pushed_branches(payload, verified or kind)
    change = parse_push_change(verified or kind, payload)
    REPORT.check(
        f"{kind}: a push is read to its event, its branch and the commit message",
        event == "push" and branches == ["feature"] and "More" in " ".join(change.messages),
        f"{event} {branches} {change.messages}",
    )

    headers, body = syncs[0]
    request = FakeRequest(headers)
    verified = hooks._verify_provider(SECRET, body, request)
    payload = json.loads(body)
    event = hooks._event_kind(verified or kind, request, payload)
    parsed = parse_pull_request(verified or kind, payload) if event == "pull_request" else None
    REPORT.check(
        f"{kind}: a synchronized pull request is read to its number, branches and commit",
        verified == kind
        and parsed is not None
        and parsed.action.value == "updated"
        and parsed.number == number
        and parsed.branch == "feature"
        and parsed.base_branch == "main"
        and len(parsed.head_sha) == 40
        and not parsed.from_fork,
        f"{verified} {event} {parsed}",
    )

    # Changing the title is not something a preview acts on.
    must(
        call(
            base,
            "PATCH",
            f"/repos/{USER}/{REPO}/pulls/{number}",
            token=token,
            body={"title": "Add a page (edited)"},
        )
    )
    headers, body = sink.next()
    request = FakeRequest(headers)
    payload = json.loads(body)
    REPORT.check(
        f"{kind}: an edit of the title is delivered and left alone",
        payload.get("action") == "edited"
        and parse_pull_request(hooks._verify_provider(SECRET, body, request) or kind, payload)
        is None,
        f"action {payload.get('action')!r}",
    )
    if keep:
        print(f"  kept {name} at {base} (user {USER}, password {PASSWORD})")


SCENARIOS: dict[str, Callable[[Path, Containers, bool], None]] = {
    "gitea": lambda workdir, containers, keep: forge_scenario(
        workdir,
        containers,
        keep,
        kind="gitea",
        image_env="NOUST_TEST_GITEA_IMAGE",
        image_default=GITEA_IMAGE,
        env_prefix="GITEA",
    ),
    "forgejo": lambda workdir, containers, keep: forge_scenario(
        workdir,
        containers,
        keep,
        kind="forgejo",
        image_env="NOUST_TEST_FORGEJO_IMAGE",
        image_default=FORGEJO_IMAGE,
        env_prefix="FORGEJO",
    ),
}


def main() -> int:
    """
    Run the scenarios.

    Returns:
        0 when none failed.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--scenario", action="append", choices=sorted(SCENARIOS), help="Only this one (repeat)."
    )
    parser.add_argument("--keep", action="store_true", help="Leave the containers running.")
    args = parser.parse_args()
    chosen = args.scenario or list(SCENARIOS)
    workdir = Path(tempfile.mkdtemp(prefix="noust-test-forge-"))
    containers = Containers(args.keep)
    try:
        for name in chosen:
            print(f"== {name}")
            try:
                SCENARIOS[name](workdir, containers, args.keep)
            except Skip as why:
                REPORT.skipped.append(f"{name}: {why}")
                print(f"  SKIP  {why}")
            except Exception as exc:
                REPORT.failed.append(f"{name}: {type(exc).__name__}: {str(exc)[:500]}")
                print(f"  FAIL  {type(exc).__name__}: {str(exc)[:500]}")
            finally:
                containers.cleanup()
    finally:
        with contextlib.suppress(OSError):
            shutil.rmtree(workdir)
    print()
    print(
        f"{len(REPORT.passed)} passed, {len(REPORT.failed)} failed, {len(REPORT.skipped)} skipped"
    )
    for line in REPORT.failed:
        print(f"  FAIL  {line}")
    for line in REPORT.skipped:
        print(f"  SKIP  {line}")
    return 1 if REPORT.failed else 0


if __name__ == "__main__":
    sys.exit(main())
