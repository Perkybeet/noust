# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Console 2.2, the GitHub App, and the code host a client that deploys clones from."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import threading
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .constants import CODE_HOST_BRANCHES, CODE_HOST_NAME
from .sandbox import Sandbox

# --- 2.2: the GitHub App -----------------------------------------------------


#: Where this machine receives code hosts' events (``noust web expose-hooks``).
GITHUB_HOOKS_URL = "https://hooks.example.com/hooks"

#: The one-time code GitHub's manifest flow hands back that it no longer honours
#: (codes last an hour and work once): the conversion is refused with GitHub's 404.
GITHUB_SPENT_CODE = "spent-manifest-code"

_GITHUB_PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "Y29uc29sZS1zYW5kYm94LWdpdGh1Yi1hcHAta2V5LW5vdC1hLXJlYWwta2V5\n"
    "-----END RSA PRIVATE KEY-----\n"
)


def seed_github_app(sandbox: Sandbox, store: Any) -> None:
    """
    Connect the machine to its own GitHub App, and answer GitHub from the sandbox.

    The App and its installations are store rows
    (:func:`tests.support.panel_factory.seed_github_app`); its private key, webhook
    secret and client secret are written where the real secret store keeps
    them, the hooks are exposed at :data:`GITHUB_HOOKS_URL` and the App's
    webhook is recorded active there, so Settings > Integrations shows a
    connected, receiving App. Every call to GitHub - the App's token
    exchange, installations, repositories, branches, the manifest
    conversion - is answered by :func:`_github_opener` instead of
    api.github.com, the JWT is "signed" without openssl, and a clone of one
    of the App's repositories checks out the seeded storefront project, so
    the wizard's "From GitHub" path inspects and deploys offline.

    Args:
        sandbox: The sandbox.
        store: The seeded store.
    """
    from noust.core.config import Config
    from noust.core.secrets import SecretStore
    from noust.integrations.github.app import (
        CLIENT_SECRET,
        PRIVATE_KEY_SECRET,
        WEBHOOK_SECRET,
        write_meta,
    )
    from noust.integrations.github.client import GitHubClient, set_client
    from noust.integrations.hooks_site import HOOKS_URL_KEY
    from tests.support.panel_factory import GITHUB_APP
    from tests.support.panel_factory import seed_github_app as record_github_app

    record_github_app(store)
    secrets = SecretStore()
    secrets.write(PRIVATE_KEY_SECRET, _GITHUB_PEM)
    secrets.write(WEBHOOK_SECRET, "console-sandbox-webhook-secret")
    secrets.write(CLIENT_SECRET, "console-sandbox-client-secret")
    config = Config()
    config.set(HOOKS_URL_KEY, GITHUB_HOOKS_URL)
    config.save()
    write_meta(
        owner_type=GITHUB_APP["owner_type"],
        webhook_url=f"{GITHUB_HOOKS_URL}/github",
        webhook_active=True,
    )
    set_client(GitHubClient(opener=_github_opener))
    _github_git_and_openssl(sandbox)


def _github_json(value: Any, status: int = 200) -> Any:
    """
    Answer a GitHub API request the way urllib hands back a response.

    Args:
        value: The JSON body.
        status: The status; anything but 2xx is raised as urllib raises it.

    Returns:
        A readable, closable body.

    Raises:
        HTTPError: For an error status, carrying GitHub's own ``message``.
    """
    import io
    from email.message import Message
    from urllib.error import HTTPError

    body = json.dumps(value).encode("utf-8")
    if status >= 400:
        raise HTTPError("https://api.github.com", status, "error", Message(), io.BytesIO(body))
    return io.BytesIO(body)


def _github_installation(installation_id: int) -> dict[str, Any] | None:
    """
    Describe one installation as GitHub's API does.

    Args:
        installation_id: The installation.

    Returns:
        GitHub's installation object, or None for one the App does not have.
    """
    from tests.support.panel_factory import GITHUB_INSTALLATIONS

    for known, account, account_type, selection in GITHUB_INSTALLATIONS:
        if known == installation_id:
            return {
                "id": known,
                "account": {"login": account, "type": account_type},
                "repository_selection": selection,
                "target_type": account_type,
            }
    return None


def _github_opener(request: Any, timeout: float | None = None) -> Any:
    """
    Answer the GitHub REST API from the seeded App, installations and repositories.

    Args:
        request: The ``urllib.request.Request`` the client built.
        timeout: Ignored: nothing is dialled.

    Returns:
        The response body.

    Raises:
        HTTPError: Where GitHub would answer an error.
    """
    from urllib.parse import urlsplit

    from tests.support.panel_factory import (
        GITHUB_APP,
        GITHUB_BRANCHES,
        GITHUB_INSTALLATIONS,
        GITHUB_REPOSITORIES,
    )

    method = request.get_method()
    parts = urlsplit(str(request.full_url))
    path = parts.path
    page = 1
    for pair in parts.query.split("&"):
        key, _, value = pair.partition("=")
        if key == "page" and value.isdigit():
            page = int(value)
    token = str(request.get_header("Authorization") or "")
    not_found = {"message": "Not Found", "documentation_url": "https://docs.github.com/rest"}

    if method == "POST" and (
        match := re.fullmatch(r"/app/installations/(\d+)/access_tokens", path)
    ):
        if _github_installation(int(match.group(1))) is None:
            return _github_json(not_found, 404)
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return _github_json({"token": f"ghs_console{match.group(1)}", "expires_at": expires}, 201)
    if method == "GET" and path == "/app/installations":
        items = [_github_installation(known) for known, *_ in GITHUB_INSTALLATIONS]
        return _github_json(items if page == 1 else [])
    if method == "GET" and (match := re.fullmatch(r"/app/installations/(\d+)", path)):
        found = _github_installation(int(match.group(1)))
        return _github_json(found if found is not None else not_found, 200 if found else 404)
    if method == "GET" and path == "/installation/repositories":
        installation = int(token.removeprefix("token ghs_console") or 0)
        repositories = [
            {
                "full_name": name,
                "name": name.split("/")[1],
                "private": private,
                "default_branch": branch,
                "clone_url": f"https://github.com/{name}.git",
            }
            for name, private, branch in GITHUB_REPOSITORIES.get(installation, ())
        ]
        listed = repositories if page == 1 else []
        return _github_json({"total_count": len(repositories), "repositories": listed})
    if method == "GET" and (match := re.fullmatch(r"/repos/([^/]+)/([^/]+)/branches", path)):
        full_name = f"{match.group(1)}/{match.group(2)}"
        known = {name for names in GITHUB_REPOSITORIES.values() for name, *_ in names}
        if full_name not in known:
            return _github_json(not_found, 404)
        branches = [
            {"name": name, "protected": protected, "commit": {"sha": sha}}
            for name, protected, sha in GITHUB_BRANCHES
        ]
        return _github_json(branches if page == 1 else [])
    if method == "POST" and (match := re.fullmatch(r"/app-manifests/([^/]+)/conversions", path)):
        if match.group(1) == GITHUB_SPENT_CODE:
            return _github_json(not_found, 404)
        return _github_json(
            {
                "id": GITHUB_APP["app_id"],
                "slug": GITHUB_APP["slug"],
                "name": GITHUB_APP["name"],
                "owner": {"login": GITHUB_APP["owner"], "type": GITHUB_APP["owner_type"]},
                "html_url": GITHUB_APP["html_url"],
                "client_id": GITHUB_APP["client_id"],
                "client_secret": "console-sandbox-client-secret",
                "webhook_secret": "console-sandbox-webhook-secret",
                "pem": _GITHUB_PEM,
            },
            201,
        )
    if method == "PATCH" and path == "/app/hook/config":
        return _github_json(json.loads(request.data or b"{}"))
    # What a deploy or a preview reports back: a pull request's comment, kept updated, and
    # the deployments and commit statuses GitHub shows. Accepted as GitHub accepts them.
    if method == "POST" and re.fullmatch(r"/repos/[^/]+/[^/]+/issues/\d+/comments", path):
        return _github_json(
            {"id": 7700001, "body": json.loads(request.data or b"{}").get("body")}, 201
        )
    if method in ("PATCH", "DELETE") and re.fullmatch(
        r"/repos/[^/]+/[^/]+/issues/comments/\d+", path
    ):
        return _github_json({"id": int(path.rsplit("/", 1)[1])} if method == "PATCH" else None)
    if method == "POST" and re.fullmatch(
        r"/repos/[^/]+/[^/]+/(deployments(/\d+/statuses)?|statuses/[0-9a-f]{7,40})", path
    ):
        return _github_json({"id": 7800001, "state": "success"}, 201)
    return _github_json(not_found, 404)


def _github_git_and_openssl(sandbox: Sandbox) -> None:
    """
    Answer the two commands the App's work runs besides HTTP.

    ``openssl dgst -sign`` signs the App's JWT: answered with a fixed
    signature, since the fake GitHub checks none. ``git clone`` of one of the
    App's repositories checks out the seeded storefront project (a Next.js
    app), and ``git ls-tree`` in such a checkout lists its files, which is
    what the wizard's inspection reads; every other command goes on to the
    runner as before.

    Args:
        sandbox: The sandbox, whose ``/var/www/src/storefront`` is checked out.
    """
    from noust.core.runner import CommandResult, get_runner
    from tests.support.panel_factory import GITHUB_REPOSITORIES

    runner = get_runner()
    original = runner.run
    project = sandbox.apps_dir.parent / "src" / "storefront"
    repositories = {name.lower() for names in GITHUB_REPOSITORIES.values() for name, *_ in names}
    checkouts: set[str] = set()

    def repository_of(url: str) -> str | None:
        match = re.fullmatch(r"https://github\.com/([^/]+/[^/]+?)(?:\.git)?/?", url)
        return match.group(1).lower() if match and match.group(1).lower() in repositories else None

    def git_verb(args: tuple[str, ...]) -> tuple[str, ...]:
        """The git command without the ``-c key=value`` pairs every Noust git call starts with."""
        rest = args[1:]
        while rest[:1] == ("-c",):
            rest = rest[2:]
        return rest

    def run(argv: Sequence[str], **kwargs: Any) -> Any:
        args = tuple(str(a) for a in argv)
        if args[:2] == ("openssl", "dgst") and "-sign" in args:
            runner.calls.append(args)
            return CommandResult(args, 0, "SHA2-256(stdin)= " + "5a" * 256 + "\n", "")
        verb = git_verb(args) if args[:1] == ("git",) else ()
        if verb[:1] == ("clone",) and "--" in verb:
            url, destination = verb[verb.index("--") + 1 :][:2]
            if repository_of(url) is not None:
                runner.calls.append(args)
                shutil.copytree(project, destination, dirs_exist_ok=True)
                checkouts.add(os.path.abspath(destination))
                return CommandResult(args, 0, "", f"Cloning into '{destination}'...\n")
        cwd = kwargs.get("cwd")
        if verb[:1] == ("ls-tree",) and cwd is not None:
            root = os.path.abspath(cwd)
            if root in checkouts:
                runner.calls.append(args)
                names = sorted(
                    str(path.relative_to(root))
                    for path in Path(root).rglob("*")
                    if path.is_file() and ".git" not in path.parts
                )
                return CommandResult(args, 0, "\0".join(names) + "\0", "")
        return original(argv, **kwargs)

    runner.run = run  # type: ignore[method-assign]


def _code_host_commit(repository: str, branch: str) -> str:
    """
    Returns:
        The commit a branch of a modelled repository points at: stable, so a deployment's commit is
        the same on every run, and different for each branch.
    """
    return hashlib.sha1(f"{repository}@{branch}".encode(), usedforsecurity=False).hexdigest()


def _code_host_tree(repository: str, branch: str, destination: Path) -> None:
    """
    Write the project a repository of the modelled code host holds.

    ``acme/site`` is a static site; any other repository is a small Node service. Each carries the
    branch it was cloned from, so a client can tell which branch is deployed.

    Args:
        repository: ``owner/name``.
        branch: The branch checked out.
        destination: The directory the clone writes into.
    """
    destination.mkdir(parents=True, exist_ok=True)
    commit = _code_host_commit(repository, branch)
    for stale in destination.iterdir():
        if stale.name == ".git":
            continue
        if stale.is_dir():
            shutil.rmtree(stale)
        else:
            stale.unlink()
    if repository.endswith("/site"):
        (destination / "index.html").write_text(
            f"<!doctype html><title>{repository}</title><h1>{repository}</h1>"
            f"<p>branch {branch}, commit {commit}</p>\n",
            encoding="utf-8",
        )
        return
    (destination / "package.json").write_text(
        json.dumps(
            {
                "name": repository.rsplit("/", 1)[-1],
                "version": "1.0.0",
                "private": True,
                "scripts": {"build": "true", "start": "node server.js"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (destination / "server.js").write_text(
        "require('http').createServer((req, res) => res.end('ok')).listen(process.env.PORT);\n",
        encoding="utf-8",
    )
    (destination / "BRANCH").write_text(f"{branch}\n{commit}\n", encoding="utf-8")


def model_code_host(sandbox: Sandbox) -> None:
    """
    Model a code host, and an application that listens once it is started.

    Nothing is cloned in the sandbox, so a deployment of a repository ends at "nothing to serve", and
    nothing listens on an application's port, so a deployment that has something to run ends at a failed
    health check. That is what the console's specs want to see, and it is the opposite of what a client
    that provisions applications needs to prove that its create works. With :data:`CODE_HOST_ENV` set:

    - ``git`` answers for ``https://code.example.net/<owner>/<name>.git`` (and the SSH forms): a clone
      writes a small project and a ``.git`` directory, ``ls-remote`` knows the branches ``main`` and
      ``staging``, ``rev-parse`` names a branch's commit, ``reset`` and ``checkout`` follow a branch, and
      ``archive`` followed by ``tar`` exports the tree the way a release is made;
    - ``systemctl start`` and ``restart`` of an application whose unit names a ``PORT`` open a
      listener on it that answers 200, as the process would, and ``stop`` closes it.

    Every other command goes on to the runner as before.

    Args:
        sandbox: The sandbox, whose unit directory the listeners are read from.
    """
    import tarfile
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from noust.core.runner import CommandResult, get_runner

    runner = get_runner()
    original = runner.run
    host = re.escape(CODE_HOST_NAME)
    pattern = re.compile(
        rf"(?:https://{host}/|git@{host}:|ssh://git@{host}/)([\w.-]+/[\w.-]+?)(?:\.git)?/?"
    )
    # Where each modelled clone is, and which repository and branch it holds.
    checkouts: dict[str, list[str]] = {}
    archives: dict[str, str] = {}
    listeners: dict[int, Any] = {}

    class Ok(BaseHTTPRequestHandler):
        """Answers 200 to anything, as an application that is up would."""

        def do_GET(self) -> None:
            body = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_HEAD = do_GET

        def log_message(self, format: str, *args: Any) -> None:
            return

    def git_verb(args: tuple[str, ...]) -> tuple[str, ...]:
        rest = args[1:]
        while rest[:1] == ("-c",):
            rest = rest[2:]
        return rest

    def port_of(unit: str) -> int | None:
        prefix, at, instance = unit.partition("@")
        try:
            text = (
                sandbox.systemd_dir / (f"{prefix}@.service" if at else f"{unit}.service")
            ).read_text(encoding="utf-8")
            if at:
                # A zero-downtime instance (blue/green) reads its port from the file the template names
                # for it: EnvironmentFile=<dir>/%i.env.
                named = re.search(r"^EnvironmentFile=-?(\S+)/%i\.env", text, re.M)
                if named is None:
                    return None
                text = Path(f"{named.group(1)}/{instance}.env").read_text(encoding="utf-8")
                found = re.search(r"^PORT=(\d+)", text, re.M)
                return int(found.group(1)) if found else None
        except OSError:
            return None
        match = re.search(r'^Environment="?PORT=(\d+)', text, re.M)
        return int(match.group(1)) if match else None

    def listen_on(unit: str) -> None:
        port = port_of(unit)
        if port is None or port in listeners:
            return
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Ok)
        except OSError:
            # Something else holds the port: the health gate will say so, as it would on a machine.
            return
        threading.Thread(target=server.serve_forever, name=f"app-{port}", daemon=True).start()
        listeners[port] = server

    def stop_listening(unit: str) -> None:
        port = port_of(unit)
        server = listeners.pop(port, None) if port is not None else None
        if server is not None:
            server.shutdown()
            server.server_close()

    def done(args: tuple[str, ...], stdout: str = "", exit_code: int = 0, stderr: str = "") -> Any:
        runner.calls.append(args)
        return CommandResult(args, exit_code, stdout, stderr)

    def run(argv: Sequence[str], **kwargs: Any) -> Any:
        args = tuple(str(a) for a in argv)
        if os.environ.get("NOUST_E2E_CODE_HOST_TRACE"):
            print("TRACE", args, file=sys.stderr, flush=True)
        if (
            args[:1] == ("systemctl",)
            and len(args) >= 3
            and args[1] in ("start", "restart", "stop")
        ):
            unit = args[-1].removesuffix(".service")
            (stop_listening if args[1] == "stop" else listen_on)(unit)
            return original(argv, **kwargs)
        if args[:2] == ("tar", "-xf") and args[2] in archives and "-C" in args:
            with tarfile.open(args[2]) as bundle:
                bundle.extractall(args[args.index("-C") + 1], filter="data")
            return done(args)
        verb = git_verb(args) if args[:1] == ("git",) else ()
        if not verb:
            return original(argv, **kwargs)
        cwd = kwargs.get("cwd")
        here = checkouts.get(os.path.abspath(cwd)) if cwd is not None else None

        if verb[0] == "clone" and "--" in verb:
            url, destination = verb[verb.index("--") + 1 :][:2]
            found = pattern.fullmatch(url)
            if found is not None:
                branch = verb[verb.index("--branch") + 1] if "--branch" in verb else "main"
                if branch not in CODE_HOST_BRANCHES:
                    return done(
                        args,
                        "",
                        128,
                        f"fatal: Remote branch {branch} not found in upstream origin\n",
                    )
                target = Path(destination)
                (target / ".git").mkdir(parents=True, exist_ok=True)
                _code_host_tree(found.group(1), branch, target)
                checkouts[os.path.abspath(destination)] = [found.group(1), branch, url]
                return done(args, "", 0, f"Cloning into '{destination}'...\n")
        if verb[0] == "ls-remote" and "--" in verb:
            url = verb[verb.index("--") + 1]
            found = pattern.fullmatch(url)
            if found is not None:
                repository = found.group(1)
                wanted = [r for r in verb[verb.index("--") + 2 :] if r.startswith("refs/heads/")]
                if wanted:
                    branch = wanted[0].removeprefix("refs/heads/")
                    if branch not in CODE_HOST_BRANCHES:
                        return done(args, "", 2)
                    return done(args, f"{_code_host_commit(repository, branch)}\t{wanted[0]}\n")
                head = _code_host_commit(repository, "main")
                return done(args, f"ref: refs/heads/main\tHEAD\n{head}\tHEAD\n")
        if verb[0] == "ls-remote" and any(pattern.fullmatch(a) for a in verb):
            # --exit-code with the URL before any "--": the preflight's probe.
            return done(args)
        if here is None:
            return original(argv, **kwargs)

        repository, branch, url = here
        if verb[:2] == ("rev-parse", "HEAD"):
            return done(args, _code_host_commit(repository, branch) + "\n")
        if verb[:3] == ("rev-parse", "--abbrev-ref", "HEAD"):
            return done(args, branch + "\n")
        if verb[:3] == ("remote", "get-url", "origin"):
            return done(args, url + "\n")
        if verb[:2] == ("cat-file", "-e"):
            # No submodules, so a release is exported with git archive.
            return done(args, "", 1)
        if verb[0] == "reset" and "--hard" in verb:
            target = verb[-1].removeprefix("origin/")
            if target in CODE_HOST_BRANCHES:
                here[1] = target
                _code_host_tree(repository, target, Path(cwd))
        if verb[:2] == ("checkout", "--force") and "-B" in verb:
            target = verb[verb.index("-B") + 1]
            if target in CODE_HOST_BRANCHES:
                here[1] = target
        if verb[0] == "archive":
            output = next(a for a in verb if a.startswith("--output=")).removeprefix("--output=")
            with tarfile.open(output, "w") as bundle:
                for entry in sorted(Path(cwd).iterdir()):
                    if entry.name != ".git":
                        bundle.add(entry, arcname=entry.name)
            archives[output] = repository
            return done(args)
        return original(argv, **kwargs)

    runner.run = run  # type: ignore[method-assign]
