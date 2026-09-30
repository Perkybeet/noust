#!/usr/bin/env python3
"""
Real-machine integration harness for Noust.

Builds the wheel from the working tree, builds a systemd-under-Docker image,
starts a privileged container, installs Noust into it from the wheel exactly
as an operator would (``noust``, with ``wasm`` symlinked alongside it as the
3.x alias), and runs a set of scenarios over the real CLI against real nginx,
real systemd and a real SQLite store.

This file is intentionally excluded from pytest's default collection: it
does not match ``test_*.py`` (see ``[tool.pytest.ini_options]`` in
``pyproject.toml``), and it drives Docker rather than being a unit test.

Usage:
    .venv/bin/python tests/integration/run.py
    .venv/bin/python tests/integration/run.py --keep
    .venv/bin/python tests/integration/run.py --scenario node_app_update

Requires Docker (tested against Docker 29) with a working systemd-in-Docker
setup: cgroup v2, ``--privileged``, ``--cgroupns=host`` and the host cgroup
filesystem bind-mounted. run.py wires all of that itself.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import hmac
import json
import re
import secrets
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
INTEGRATION_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = INTEGRATION_DIR / "fixtures"
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
DOCKERFILE = INTEGRATION_DIR / "Dockerfile.systemd"
IMAGE_TAG = "noust-integration:latest"

#: How long the whole container gets to reach "running" or "degraded".
SYSTEMD_READY_TIMEOUT = 90

#: How long a `pip install '<wheel>[all]'` may take with a cold cache.
PIP_INSTALL_TIMEOUT = 600

#: How long a deploy (fetch + install + build + certbot-less site + start)
#: may take. Node dependency installs are the slow part.
DEPLOY_TIMEOUT = 300

#: The repository the release scenarios deploy from, and its git:// URL.
RELEASE_REPO = "/root/fixtures/node-rel"
RELEASE_URL = "git://127.0.0.1/node-rel"

#: The release scenarios' application.
RELEASE_DOMAIN = "rel.test"
RELEASE_ROOT = "/var/www/apps/rel-test"

#: The store, wherever this install put it. `core/store.py:_resolve_db_path`
#: only prefers the system directory (/var/lib/noust) when it already exists
#: and is writable; nothing in a plain `pip install` creates it, so a fresh
#: container with no legacy WASM directory still gets the per-user path.
NOUST_DB = "$(ls /var/lib/noust/noust.db /root/.local/share/noust/noust.db 2>/dev/null | head -1)"

#: The released version the upgrade rehearsal (--upgrade) starts from: the
#: last release published under the WASM name, before the 3.0.0 rename. This
#: is the most important pre-release scenario for 3.0: a real WASM 2.x
#: server, with real applications, a webhook token and a scheduled backup,
#: upgraded to Noust with nothing but `pip install` over the same venv,
#: exactly as docs/UPGRADING-3.0.md and docs/RENAME.md describe.
UPGRADE_FROM_VERSION = "2.3.0"

UPGRADE_NODE_DOMAIN = "upg-node.test"
UPGRADE_NODE_APP = "upg-node-test"
UPGRADE_NODE_ROOT = f"/var/www/apps/{UPGRADE_NODE_APP}"

UPGRADE_STATIC_DOMAIN = "upg-static.test"
UPGRADE_STATIC_APP = "upg-static-test"
UPGRADE_STATIC_ROOT = f"/var/www/apps/{UPGRADE_STATIC_APP}"

#: The cron job and backup schedule the upgrade rehearsal creates on WASM 2.3,
#: and expects renamed (not recreated) after the upgrade.
UPGRADE_CRON_NAME = "upg-nightly"
UPGRADE_TOKEN_NAME = "upg-admin-token"

#: The store before the migration: a fresh WASM 2.3 install with nothing of
#: Noust's ever having run, so this is the one and only location.
UPGRADE_OLD_DB = "/var/lib/wasm/wasm.db"

#: The query behind :func:`_store_apps_snapshot`: columns that exist in both
#: The schema the working tree's store migrates to, read from its source rather than imported:
#: the harness runs on the host and never imports the package it tests.
SCHEMA_VERSION = int(
    re.search(
        r"^SCHEMA_VERSION = (\d+)$",
        (Path(__file__).resolve().parents[2] / "src/noust/core/store.py").read_text(),
        re.MULTILINE,
    ).group(1)  # type: ignore[union-attr]
)

#: the WASM 2.3 and Noust 3.0 apps table, in a stable order, so the row for
#: each application can be compared byte-for-byte across the upgrade. A plain
#: literal - not built with an f-string - like every other query in this
#: file, so ruff's hardcoded-sql check (S608, which cannot tell a literal
#: from an injection) has nothing to flag.
APPS_SNAPSHOT_QUERY = (
    "SELECT domain, app_type, source, branch, port, webserver, ssl_enabled, status, "
    "is_static, created_at, deployed_at FROM apps ORDER BY domain"
)


class HarnessError(RuntimeError):
    """Raised for failures in the setup phases (build, install), not scenarios."""


class ScenarioSkipped(Exception):
    """Raised by a scenario that cannot run here (no network), with the reason."""


# ---------------------------------------------------------------------------
# Host-side process helpers
# ---------------------------------------------------------------------------


def sh(
    argv: list[str], *, timeout: int = 120, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run a command on the host (not in the container) and capture output."""
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if check and proc.returncode != 0:
        raise HarnessError(
            f"command failed (exit {proc.returncode}): {' '.join(argv)}\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
    return proc


def docker_exec(
    container: str, script: str, *, timeout: int = 120, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run a shell script inside the container through `docker exec`."""
    proc = subprocess.run(
        ["docker", "exec", container, "bash", "-lc", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise HarnessError(
            f"command failed in container (exit {proc.returncode}): {script}\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
    return proc


# ---------------------------------------------------------------------------
# Setup phases
# ---------------------------------------------------------------------------


def build_wheel(workdir: Path) -> Path:
    """Build the wheel with the repo's own venv, installing `build` if needed."""
    python = VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable)
    print(f"[setup] building wheel with {python}")

    probe = subprocess.run([str(python), "-c", "import build"], capture_output=True, text=True)
    if probe.returncode != 0:
        print("[setup] installing the `build` package into the repo venv")
        sh([str(python), "-m", "pip", "install", "build"], timeout=180)

    outdir = workdir / "dist"
    outdir.mkdir(parents=True, exist_ok=True)
    sh(
        [str(python), "-m", "build", "--wheel", "--outdir", str(outdir), str(REPO_ROOT)],
        timeout=180,
    )
    # Named for the current distribution only: `outdir` accumulates wheels
    # across runs (and, for anyone who worked on this tree before 3.0,
    # WASM-era `wasm_cli-*.whl` files too), and "wasm_cli" sorts after
    # "noust" - a bare `*.whl` glob picked up a stale pre-rename wheel here.
    wheels = sorted(outdir.glob("noust-*.whl"))
    if not wheels:
        raise HarnessError(f"`python -m build` produced no noust-*.whl wheel in {outdir}")
    wheel = wheels[-1]
    print(f"[setup] built {wheel.name}")
    return wheel


def build_image() -> None:
    """Build the systemd-under-Docker image scenarios run against."""
    print(f"[setup] building image {IMAGE_TAG}")
    sh(
        ["docker", "build", "-t", IMAGE_TAG, "-f", str(DOCKERFILE), str(INTEGRATION_DIR)],
        timeout=600,
    )


def start_container(name: str) -> None:
    """Start the privileged, systemd-as-PID-1 container."""
    print(f"[setup] starting container {name}")
    sh(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--privileged",
            "--cgroupns=host",
            "-v",
            "/sys/fs/cgroup:/sys/fs/cgroup:rw",
            "--tmpfs",
            "/run",
            "--tmpfs",
            "/run/lock",
            IMAGE_TAG,
        ],
        timeout=60,
    )


def wait_for_systemd(name: str, timeout: int = SYSTEMD_READY_TIMEOUT) -> str:
    """Poll `systemctl is-system-running` until it settles or the deadline passes."""
    print("[setup] waiting for systemd to report running/degraded")
    deadline = time.time() + timeout
    last = "<never answered>"
    while time.time() < deadline:
        result = docker_exec(name, "systemctl is-system-running || true", timeout=15, check=False)
        last = result.stdout.strip() or last
        if last in ("running", "degraded"):
            print(f"[setup] systemd is {last}")
            return last
        time.sleep(2)
    journal = docker_exec(name, "journalctl -xb --no-pager | tail -100", timeout=15, check=False)
    raise HarnessError(
        f"systemd never reached running/degraded in {timeout}s (last state: {last!r})\n"
        f"--- journalctl -xb (tail) ---\n{journal.stdout}"
    )


def install_noust(name: str, wheel: Path) -> None:
    """Install Noust into a dedicated venv, exactly as documented for operators.

    Symlinks both ``noust`` and its 3.x alias ``wasm`` onto ``/usr/local/bin``
    from the same venv, so scenarios can exercise either name.

    Args:
        name: Container name.
        wheel: Path to the wheel built from the working tree, on the host.
    """
    print(f"[setup] copying {wheel.name} into the container")
    sh(["docker", "cp", str(wheel), f"{name}:/tmp/{wheel.name}"], timeout=60)

    print("[setup] creating /opt/noust venv and installing the wheel")
    docker_exec(name, "python3 -m venv /opt/noust", timeout=60)
    docker_exec(name, "/opt/noust/bin/pip install --quiet --upgrade pip", timeout=120)
    docker_exec(
        name,
        f"/opt/noust/bin/pip install --quiet '/tmp/{wheel.name}[all]'",
        timeout=PIP_INSTALL_TIMEOUT,
    )
    docker_exec(name, "ln -sf /opt/noust/bin/noust /usr/local/bin/noust", timeout=15)
    docker_exec(name, "ln -sf /opt/noust/bin/wasm /usr/local/bin/wasm", timeout=15)
    result = docker_exec(name, "noust --help", timeout=30)
    print("[setup] noust --help:")
    print(result.stdout)
    alias = docker_exec(name, "wasm --help", timeout=30)
    print("[setup] wasm --help (the 3.x alias):")
    print(alias.stdout)


def install_fixtures(name: str) -> None:
    """Copy the fixture apps into the container and turn each into a git repo."""
    print("[setup] copying fixtures into the container")
    sh(["docker", "cp", str(FIXTURES_DIR), f"{name}:/root/fixtures"], timeout=60)

    # `docker cp` preserves the numeric uid of the files on the host, which
    # is almost never root's. git then refuses to touch the tree ("detected
    # dubious ownership") and so would Noust's own fetch step were these ever
    # used as a source through anything but a straight directory copy.
    docker_exec(name, "chown -R root:root /root/fixtures", timeout=15)
    docker_exec(name, "git config --global --add safe.directory '*'", timeout=15)

    # The nodejs deployer always installs with `npm ci`
    # (helpers/package_manager.py:187), which refuses to run without a
    # committed lockfile. Real Node projects ship one; this fixture needs one
    # too, generated fresh so it matches whatever npm version the image has.
    docker_exec(
        name,
        "cd /root/fixtures/node-app && npm install --package-lock-only",
        timeout=60,
    )

    for app in ("static-site", "node-app"):
        script = (
            f"cd /root/fixtures/{app} && "
            "git init -q && "
            "git config user.email noust-it@example.com && "
            "git config user.name 'Noust Integration' && "
            "git add -A && "
            "git commit -q -m 'initial fixture'"
        )
        docker_exec(name, script, timeout=30)
    print("[setup] fixtures are local git repositories under /root/fixtures")

    # The release scenarios deploy from a git remote, so the repository cache,
    # the fetch and the export run for real. A copy of the node fixture keeps
    # their commits out of the in-place scenarios' repository, and git daemon
    # serves it over git:// on loopback: Noust refuses file:// on purpose.
    docker_exec(name, f"cp -a /root/fixtures/node-app {RELEASE_REPO}", timeout=30)
    # A dependency, so there is a node_modules for an update to reuse. A local
    # one: npm links it from the tree and never touches the network.
    docker_exec(
        name,
        f"cd {RELEASE_REPO} && mkdir local-dep && "
        'echo \'{"name": "local-dep", "version": "1.0.0"}\' > local-dep/package.json && '
        "echo 'module.exports = 1;' > local-dep/index.js && "
        "npm pkg set dependencies.local-dep=file:./local-dep && "
        "npm install --package-lock-only && "
        "git add -A && git commit -q -m 'add a local dependency'",
        timeout=60,
    )
    docker_exec(
        name,
        "git daemon --reuseaddr --export-all --base-path=/root/fixtures "
        "--listen=127.0.0.1 --detach /root/fixtures",
        timeout=30,
    )
    docker_exec(name, f"git ls-remote --exit-code {RELEASE_URL} HEAD", timeout=30)
    print(f"[setup] {RELEASE_REPO} is served at {RELEASE_URL}")


def build_transitional_wheel(workdir: Path) -> Path:
    """Build the transitional ``wasm-cli`` wheel from ``packaging/transitional/wasm-cli``.

    This is the empty PyPI package a real 3.x release publishes so that an
    operator's ``pip install -U wasm-cli`` keeps working and brings in
    ``noust`` (see ``docs/RENAME.md``). Building it locally, the same way
    :func:`build_wheel` builds the real package, is what lets the upgrade
    rehearsal exercise that exact command against a pre-release tree, without
    a network round trip to a project that has not published 3.0.0 yet.

    Args:
        workdir: Where the harness keeps its build output.

    Returns:
        Path to the built wheel, on the host.
    """
    python = VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable)
    outdir = workdir / "dist-transitional"
    outdir.mkdir(parents=True, exist_ok=True)
    sh(
        [
            str(python),
            "-m",
            "build",
            "--wheel",
            "--outdir",
            str(outdir),
            str(REPO_ROOT / "packaging" / "transitional" / "wasm-cli"),
        ],
        timeout=120,
    )
    wheels = sorted(outdir.glob("wasm_cli-*.whl"))
    if not wheels:
        raise HarnessError(f"`python -m build` produced no wasm_cli-*.whl wheel in {outdir}")
    wheel = wheels[-1]
    print(f"[setup] built the transitional {wheel.name}")
    return wheel


def install_wasm_cli(name: str, version: str) -> None:
    """Install the real, released WASM from PyPI: the upgrade rehearsal's starting point.

    Mirrors :func:`install_noust`, but pulls the package straight from the
    index under its old name, because the point of ``--upgrade`` is to start
    from what an operator running a WASM 2.x server actually has installed
    today.

    Args:
        name: Container name.
        version: The exact ``wasm-cli`` version to install, e.g. "2.3.0".
    """
    print(f"[setup] creating /opt/wasm venv and installing wasm-cli=={version} from PyPI")
    docker_exec(name, "python3 -m venv /opt/wasm", timeout=60)
    docker_exec(name, "/opt/wasm/bin/pip install --quiet --upgrade pip", timeout=120)
    docker_exec(
        name,
        f"/opt/wasm/bin/pip install --quiet 'wasm-cli[all]=={version}'",
        timeout=PIP_INSTALL_TIMEOUT,
    )
    docker_exec(name, "ln -sf /opt/wasm/bin/wasm /usr/local/bin/wasm", timeout=15)
    result = docker_exec(name, "wasm --version", timeout=30)
    print(f"[setup] installed {result.stdout.strip()}")


def upgrade_to_noust(name: str, wheel: Path, transitional_wheel: Path) -> None:
    """Upgrade the venv in place to Noust, the way ``docs/RENAME.md`` tells a pip user to.

    A real operator runs a single ``pip install -U wasm-cli``: the
    transitional package (built by :func:`build_transitional_wheel`) depends
    on ``noust==<same version>``, so pip installs ``noust`` and only then
    removes the real ``wasm-cli`` 2.x, whose file list includes ``bin/wasm``
    (the comment in ``packaging/transitional/wasm-cli/pyproject.toml``
    explains why the transitional package declares that entry point itself).

    This working tree's ``pyproject.toml`` has not been bumped past
    :data:`UPGRADE_FROM_VERSION` yet (see the final report), so the
    transitional wheel built from it carries the *same* version number as
    the real ``wasm-cli`` already installed, and pip's dependency resolver
    treats identical-version requirements as already satisfied - a single
    ``pip install --upgrade`` would do nothing, silently turning this whole
    rehearsal into a no-op. Splitting the one command into two sidesteps it
    without a ``--force-reinstall`` that would also be applied to every one
    of ``noust``'s already-installed dependencies (click, fastapi, ...),
    which ``--no-index`` (deliberate: this is a pre-release tree, and PyPI
    has no matching ``noust`` to resolve against) would then fail to find:

    1. Install ``noust[all]==<version>`` from the two wheels given, with
       nothing forced: its dependencies are already on the venv from the
       ``wasm-cli[all]`` install below, and pip leaves a satisfied
       requirement alone.
    2. Force-reinstall (``--no-deps``, since ``noust`` is already there and
       correct) the empty transitional ``wasm-cli`` over the real one, which
       removes the real package's code and puts back ``bin/wasm`` pointing at
       ``noust``.

    Args:
        name: Container name.
        wheel: Path to the ``noust`` wheel built from the working tree, on
            the host.
        transitional_wheel: Path to the transitional ``wasm-cli`` wheel built
            by :func:`build_transitional_wheel`, on the host.
    """
    print(f"[setup] copying {wheel.name} and {transitional_wheel.name} into the container")
    docker_exec(name, "mkdir -p /tmp/noust-upgrade-wheels", timeout=15)
    for path in (wheel, transitional_wheel):
        sh(["docker", "cp", str(path), f"{name}:/tmp/noust-upgrade-wheels/{path.name}"], timeout=60)
    docker_exec(
        name,
        # The wheel by path, and its dependencies from the index: 3.1 added one
        # (cryptography, for passkeys) that WASM 2.3 never installed, which a
        # real `pip install -U wasm-cli` fetches. Pinning by name instead would
        # let the index's own noust of the same version win over this tree's.
        f"/opt/wasm/bin/pip install --quiet '/tmp/noust-upgrade-wheels/{wheel.name}[all]'",
        timeout=PIP_INSTALL_TIMEOUT,
    )
    docker_exec(
        name,
        "/opt/wasm/bin/pip install --quiet --no-index --find-links=/tmp/noust-upgrade-wheels "
        "--force-reinstall --no-deps wasm-cli",
        timeout=PIP_INSTALL_TIMEOUT,
    )
    # A real operator's `pip install -U wasm-cli` runs inside a venv whose
    # bin/ is already on PATH (or is the system interpreter); /opt/wasm/bin
    # is exposed the same way install_wasm_cli set it up for `wasm`, so
    # `noust` gets the same treatment now that pip has put it there too.
    docker_exec(name, "ln -sf /opt/wasm/bin/noust /usr/local/bin/noust", timeout=15)
    docker_exec(name, "ln -sf /opt/wasm/bin/wasm /usr/local/bin/wasm", timeout=15)
    # Never `noust --version` to check this: cli/app.py's entrypoint() runs
    # the automatic migration before main() ever looks at argv, for every
    # invocation, --version included (should_run_automatically() only skips
    # it for --dry-run, migrate-from-wasm, or a non-root euid). Checking the
    # upgrade with the real CLI would silently perform the very migration
    # run_upgrade_rehearsal means to observe on its own first command, below.
    # Reading noust.__version__ confirms what pip installed without invoking
    # the CLI at all.
    result = docker_exec(
        name, "/opt/wasm/bin/python3 -c 'import noust; print(noust.__version__)'", timeout=30
    )
    print(f"[setup] upgraded to {result.stdout.strip()}")


def wheel_version(wheel: Path) -> str:
    """The version a wheel carries, from its file name (``noust-3.1.0-py3-none-any.whl``).

    The upgrade steps pin ``noust`` to the working tree's version: pinning it to
    :data:`UPGRADE_FROM_VERSION` only held while the tree was still at 2.3.0, and
    against any later tree ``--no-index`` finds nothing to install.
    """
    return wheel.name.split("-")[1]


def remove_container(name: str) -> None:
    """Best-effort container teardown; never raises."""
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True, timeout=60)


# ---------------------------------------------------------------------------
# Scenario framework
# ---------------------------------------------------------------------------


@dataclass
class Scenario:
    """Collaborator scenarios use to run commands and assert on them.

    Every command run through :meth:`run` is kept, in order, as evidence -
    printed verbatim after the scenario's PASS/FAIL line.
    """

    container: str
    evidence: list[str] = field(default_factory=list)

    def run(
        self, script: str, *, timeout: int = 60, check: bool = True, label: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        proc = docker_exec(self.container, script, timeout=timeout, check=False)
        block = [f"$ {label or script}"]
        if proc.stdout:
            block.append(proc.stdout.rstrip("\n"))
        if proc.stderr.strip():
            block.append("--- stderr ---")
            block.append(proc.stderr.rstrip("\n"))
        block.append(f"(exit={proc.returncode})")
        self.evidence.append("\n".join(block))
        if check and proc.returncode != 0:
            raise AssertionError(f"command failed (exit {proc.returncode}): {label or script}")
        return proc

    def check(self, condition: bool, message: str) -> None:
        if not condition:
            raise AssertionError(message)


ScenarioFn = Callable[[Scenario], None]
SCENARIOS: list[tuple[str, ScenarioFn]] = []


def scenario(name: str) -> Callable[[ScenarioFn], ScenarioFn]:
    def decorator(fn: ScenarioFn) -> ScenarioFn:
        SCENARIOS.append((name, fn))
        return fn

    return decorator


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


@scenario("static_site_create_and_serve")
def scenario_static_site(sc: Scenario) -> None:
    """A static site deploys with --no-ssl and nginx serves it and its image."""
    sc.run(
        "noust create -d static.test -s /root/fixtures/static-site -t static --no-ssl",
        timeout=DEPLOY_TIMEOUT,
        label="noust create -d static.test -s /root/fixtures/static-site -t static --no-ssl",
    )

    page = sc.run(
        "curl -sS -H 'Host: static.test' http://127.0.0.1/",
        timeout=30,
        label="curl -H 'Host: static.test' http://127.0.0.1/",
    )
    sc.check(
        "Noust Integration Static Fixture" in page.stdout,
        f"index page missing the fixture marker text: {page.stdout!r}",
    )

    image = sc.run(
        "curl -sS -o /dev/null -w '%{http_code} %{size_download}' "
        "-H 'Host: static.test' http://127.0.0.1/logo.png",
        timeout=30,
        label="curl -H 'Host: static.test' http://127.0.0.1/logo.png",
    )
    code, _, size = image.stdout.strip().partition(" ")
    sc.check(
        code == "200" and int(size or "0") > 0,
        f"image fetch failed or was empty: {image.stdout!r}",
    )

    # Planted in the served tree so this proves nginx itself refuses them, not just
    # that nothing happens to be there. Written here rather than shipped in the fixture
    # because git cannot track a file inside a directory named .git.
    sc.run(
        "root=$(readlink -e /var/www/apps/static-test/current || echo /var/www/apps/static-test) && "
        'echo SECRET=integration > "$root/.env" && '
        'mkdir -p "$root/.git" && echo \'[core]\' > "$root/.git/config"',
        timeout=30,
        label="plant .env and .git/config in the served tree",
    )
    for path in ("/.env", "/.git/config"):
        hidden = sc.run(
            f"curl -sS -o /dev/null -w '%{{http_code}}' -H 'Host: static.test' "
            f"http://127.0.0.1{path}",
            timeout=30,
            label=f"curl -H 'Host: static.test' http://127.0.0.1{path}",
        )
        sc.check(
            hidden.stdout.strip() in ("403", "404"),
            f"{path} must answer 403 or 404, got {hidden.stdout.strip()!r}",
        )

    challenge = sc.run(
        "mkdir -p /var/www/html/.well-known/acme-challenge && "
        "echo noust-integration-token > /var/www/html/.well-known/acme-challenge/probe && "
        "curl -sS -H 'Host: static.test' http://127.0.0.1/.well-known/acme-challenge/probe",
        timeout=30,
        label="curl -H 'Host: static.test' http://127.0.0.1/.well-known/acme-challenge/probe",
    )
    sc.check(
        "noust-integration-token" in challenge.stdout,
        f"ACME's own well-known path must stay reachable: {challenge.stdout!r}",
    )


@scenario("node_app_create_update_and_data_survival")
def scenario_node_app_update(sc: Scenario) -> None:
    """A Node app deploys, is updated to a new commit, and keeps its own data.

    This is the v1.6.3 data-loss regression check: an update must not erase
    ``.env`` or anything the running application wrote into its own tree
    (uploads among them). It deploys with the 1.x in-place layout, which is
    what every application deployed before 2.0 is on, and checks that an
    update leaves it there.
    """
    sc.run(
        "noust create -d node.test -s /root/fixtures/node-app -t nodejs --no-ssl --layout inplace",
        timeout=DEPLOY_TIMEOUT,
        label="noust create -d node.test -s /root/fixtures/node-app -t nodejs --no-ssl "
        "--layout inplace",
    )

    page = sc.run(
        "curl -sS -H 'Host: node.test' http://127.0.0.1/",
        timeout=30,
        label="curl -H 'Host: node.test' http://127.0.0.1/",
    )
    sc.check(page.stdout.strip() == "ok 1", f"expected 'ok 1', got {page.stdout!r}")

    env_before = sc.run(
        "test -f /var/www/apps/node-test/.env && echo present",
        timeout=15,
        check=False,
        label="test -f /var/www/apps/node-test/.env",
    )
    sc.check(
        env_before.stdout.strip() == "present",
        ".env was not created from .env.example on deploy",
    )

    upload = sc.run(
        "curl -sS -H 'Host: node.test' --data-binary 'integration-harness-upload' "
        "http://127.0.0.1/upload",
        timeout=30,
        label="curl -H 'Host: node.test' --data-binary ... http://127.0.0.1/upload",
    )
    sc.check("uploaded" in upload.stdout, f"upload did not succeed: {upload.stdout!r}")

    sc.run(
        "cd /root/fixtures/node-app && echo 2 > VERSION && "
        "git add VERSION && git commit -q -m 'bump version to 2'",
        timeout=30,
        label="(fixture repo) echo 2 > VERSION; git commit",
    )

    sc.run("noust update node.test", timeout=DEPLOY_TIMEOUT, label="noust update node.test")

    page2 = sc.run(
        "curl -sS -H 'Host: node.test' http://127.0.0.1/",
        timeout=30,
        label="curl -H 'Host: node.test' http://127.0.0.1/ (after update)",
    )
    sc.check(
        page2.stdout.strip() == "ok 2",
        f"expected 'ok 2' after update, got {page2.stdout!r}",
    )

    upload_after = sc.run(
        "cat /var/www/apps/node-test/uploads/upload.txt",
        timeout=15,
        check=False,
        label="cat /var/www/apps/node-test/uploads/upload.txt (after update)",
    )
    sc.check(
        upload_after.returncode == 0 and "integration-harness-upload" in upload_after.stdout,
        "the uploaded file did not survive `noust update` - this is the v1.6.3 "
        "data-loss regression `noust update` is supposed to have fixed",
    )

    env_after = sc.run(
        "test -f /var/www/apps/node-test/.env && echo present",
        timeout=15,
        check=False,
        label="test -f /var/www/apps/node-test/.env (after update)",
    )
    sc.check(env_after.stdout.strip() == "present", ".env did not survive `noust update`")

    listing = sc.run(
        "ls -A /var/www/apps/node-test",
        timeout=15,
        label="ls -A /var/www/apps/node-test (after update)",
    )
    layout = sc.run(
        store_query("SELECT layout FROM apps WHERE domain = 'node.test'"),
        timeout=15,
        label="SELECT layout FROM apps WHERE domain = 'node.test' (after update)",
    )
    sc.check(
        not {"releases", "current", "shared", "repo"} & set(listing.stdout.split())
        and layout.stdout.strip() == "inplace",
        f"the in-place app was moved onto releases by an update: {listing.stdout!r} "
        f"{layout.stdout!r}",
    )


@scenario("update_hands_tree_back_to_www_data")
def scenario_ownership(sc: Scenario) -> None:
    """After an update, nothing under the app tree is still owned by root.

    This is the v1.6.2 regression check (EACCES at runtime because the tree
    was handed back to the service user only on deploy, not on update).
    node_modules is excluded per the task brief, though the current
    implementation (helpers/permissions.py:hand_over_tree) chowns the whole
    tree including it.
    """
    result = sc.run(
        "find /var/www/apps/node-test -not -path '*/node_modules/*' "
        "-not -path '*/node_modules' -user root",
        timeout=30,
        check=False,
        label="find /var/www/apps/node-test -not -path '*/node_modules/*' -user root",
    )
    sc.check(result.returncode == 0, f"find failed: {result.stderr}")
    sc.check(
        result.stdout.strip() == "",
        f"root-owned files remain under the app tree after update:\n{result.stdout}",
    )


def store_query(query: str) -> str:
    """Return the shell command that runs a read-only query against the store."""
    return store_query_at(NOUST_DB, query)


def store_query_at(db: str, query: str) -> str:
    """Return the shell command that runs a read-only query against a given store file."""
    return f'sqlite3 {db} "{query}"'


def active_release(sc: Scenario, label: str) -> str:
    """Return what ``current`` points at, as ``releases/<id>``."""
    link = sc.run(f"readlink {RELEASE_ROOT}/current", timeout=15, check=False, label=label)
    return link.stdout.strip()


def commit_fixture(sc: Scenario, script: str, message: str) -> None:
    """Change the release fixture repository and commit it."""
    sc.run(
        f"cd {RELEASE_REPO} && {script} && git add -A && git commit -q -m '{message}'",
        timeout=30,
        label=f"(fixture repo) {script}; git commit -m '{message}'",
    )


@scenario("release_app_create_and_serve")
def scenario_release_create(sc: Scenario) -> None:
    """A Node app created on the release layout builds releases/<id> behind current.

    Deployed from a git remote, so the repository cache, the fetch and the
    export into the release all run for real; ``uploads`` is a persistent
    path that lives in shared/.
    """
    create = (
        f"noust create -d {RELEASE_DOMAIN} -s {RELEASE_URL} -t nodejs --no-ssl "
        "--layout releases --persist uploads"
    )
    sc.run(create, timeout=DEPLOY_TIMEOUT, label=create)

    tree = sc.run(
        f"ls -A {RELEASE_ROOT}; ls {RELEASE_ROOT}/releases; "
        f"test -d {RELEASE_ROOT}/repo/.git && echo repo-is-a-clone; "
        f"test -e {RELEASE_ROOT}/current/.git || echo release-has-no-git",
        timeout=15,
        label=f"ls -A {RELEASE_ROOT}; ls releases; repo/.git; current/.git",
    )
    for expected in ("releases", "current", "shared", "repo", "repo-is-a-clone"):
        sc.check(expected in tree.stdout.split(), f"{expected} missing: {tree.stdout!r}")
    sc.check("release-has-no-git" in tree.stdout, "the release was exported with its .git")

    current = active_release(sc, f"readlink {RELEASE_ROOT}/current")
    sc.check(current.startswith("releases/"), f"current is not a release link: {current!r}")

    links = sc.run(
        f"readlink {RELEASE_ROOT}/current/.env {RELEASE_ROOT}/current/uploads; "
        f"stat -c '%a %U' {RELEASE_ROOT}/shared/.env",
        timeout=15,
        label="readlink current/.env current/uploads; stat shared/.env",
    )
    sc.check(
        links.stdout.split()[:2] == ["../../shared/.env", "../../shared/uploads"],
        f".env and uploads are not linked into shared/: {links.stdout!r}",
    )
    sc.check(links.stdout.split()[2:] == ["600", "www-data"], f"shared/.env: {links.stdout!r}")

    unit = sc.run(
        "systemctl cat rel-test | grep -E '^(WorkingDirectory|ExecStart)='",
        timeout=15,
        label="systemctl cat rel-test | grep WorkingDirectory/ExecStart",
    )
    sc.check(
        f"WorkingDirectory={RELEASE_ROOT}/current" in unit.stdout,
        f"the unit does not run from current: {unit.stdout!r}",
    )

    page = sc.run(
        f"curl -sS -H 'Host: {RELEASE_DOMAIN}' http://127.0.0.1/",
        timeout=30,
        label=f"curl -H 'Host: {RELEASE_DOMAIN}' http://127.0.0.1/",
    )
    sc.check(page.stdout.strip() == "ok 1", f"expected 'ok 1', got {page.stdout!r}")

    rows = sc.run(
        store_query(
            "SELECT a.layout, r.status FROM releases r "
            "JOIN apps a ON a.id = r.app_id WHERE a.domain = 'rel.test'"
        ),
        timeout=15,
        label="SELECT layout, release status FROM the store",
    )
    sc.check(rows.stdout.split() == ["releases|active"], f"store rows: {rows.stdout!r}")


@scenario("release_app_update_keeps_uploads")
def scenario_release_update(sc: Scenario) -> None:
    """An update builds a new release; the upload the old one took is still served.

    The lockfile does not change between the two commits, so the new release
    takes its node_modules from the active one instead of installing.
    """
    before = active_release(sc, f"readlink {RELEASE_ROOT}/current (before update)")

    upload = sc.run(
        f"curl -sS -H 'Host: {RELEASE_DOMAIN}' --data-binary 'release-harness-upload' "
        "http://127.0.0.1/upload",
        timeout=30,
        label=f"curl -H 'Host: {RELEASE_DOMAIN}' --data-binary ... http://127.0.0.1/upload",
    )
    sc.check("uploaded" in upload.stdout, f"upload did not succeed: {upload.stdout!r}")
    sc.run(
        f"cat {RELEASE_ROOT}/shared/uploads/upload.txt",
        timeout=15,
        label="cat shared/uploads/upload.txt (the upload landed in shared/)",
    )

    commit_fixture(sc, "echo 2 > VERSION", "bump version to 2")
    sc.run(
        f"noust update {RELEASE_DOMAIN}",
        timeout=DEPLOY_TIMEOUT,
        label=f"noust update {RELEASE_DOMAIN}",
    )

    # The console shows substeps only with -v; the captured deploy log has all.
    log_path = sc.run(
        store_query(
            "SELECT log_path FROM deployments WHERE domain = 'rel.test' ORDER BY id DESC LIMIT 1"
        ),
        timeout=15,
        label="SELECT the deploy log of the update",
    ).stdout.strip()
    log = sc.run(
        f"grep -E 'Dependencies reused|npm ci|Activated release' {log_path}",
        timeout=15,
        check=False,
        label=f"grep 'Dependencies reused|npm ci|Activated release' {log_path}",
    )
    sc.check(
        f"Dependencies reused from {before.split('/')[-1]}" in log.stdout
        and "npm ci" not in log.stdout,
        "the update installed again although the lockfile did not change",
    )

    after = active_release(sc, f"readlink {RELEASE_ROOT}/current (after update)")
    sc.check(after != before, f"current still points at {before}")
    count = sc.run(f"ls {RELEASE_ROOT}/releases | wc -l", timeout=15, label="ls releases | wc -l")
    sc.check(count.stdout.strip() == "2", f"expected 2 releases, found {count.stdout.strip()}")

    page = sc.run(
        f"curl -sS -H 'Host: {RELEASE_DOMAIN}' http://127.0.0.1/",
        timeout=30,
        label=f"curl -H 'Host: {RELEASE_DOMAIN}' http://127.0.0.1/ (after update)",
    )
    sc.check(page.stdout.strip() == "ok 2", f"expected 'ok 2' after update, got {page.stdout!r}")

    kept = sc.run(
        f"cat {RELEASE_ROOT}/current/uploads/upload.txt",
        timeout=15,
        check=False,
        label="cat current/uploads/upload.txt (through the new release)",
    )
    sc.check(
        kept.returncode == 0 and "release-harness-upload" in kept.stdout,
        "the upload is not reachable from the new release",
    )

    owners = sc.run(
        f"find {RELEASE_ROOT}/{after} {RELEASE_ROOT}/shared -user root "
        "-not -path '*/node_modules/*' -not -path '*/node_modules'",
        timeout=30,
        check=False,
        label=f"find {after} shared -user root (not node_modules)",
    )
    sc.check(owners.stdout.strip() == "", f"root-owned files remain:\n{owners.stdout}")


@scenario("release_app_broken_commit_rolls_back")
def scenario_release_rollback(sc: Scenario) -> None:
    """A release whose server throws on start fails the health gate and is rolled back.

    The previous release serves again without the operator doing anything,
    the update fails with the process's own words, and the store says which
    release failed.
    """
    before = active_release(sc, f"readlink {RELEASE_ROOT}/current (before the broken commit)")

    commit_fixture(
        sc,
        "echo 3 > VERSION && sed -i '1i throw new Error(\"broken on purpose\");' server.js",
        "break the server",
    )
    result = sc.run(
        f"noust update {RELEASE_DOMAIN}",
        timeout=DEPLOY_TIMEOUT,
        check=False,
        label=f"noust update {RELEASE_DOMAIN} (broken commit)",
    )
    output = result.stdout + result.stderr
    sc.check(result.returncode != 0, "noust update reported success for a release that never ran")
    sc.check("did not pass its health check" in output, "the failure does not say what failed")
    sc.check(
        f"{before.split('/')[-1]} is active again" in output,
        "the failure does not say the previous release is back",
    )

    after = active_release(sc, f"readlink {RELEASE_ROOT}/current (after the failed update)")
    sc.check(after == before, f"current moved from {before} to {after}")

    page = sc.run(
        f"curl -sS -H 'Host: {RELEASE_DOMAIN}' http://127.0.0.1/",
        timeout=30,
        label=f"curl -H 'Host: {RELEASE_DOMAIN}' http://127.0.0.1/ (after the rollback)",
    )
    sc.check(page.stdout.strip() == "ok 2", f"the previous release is not serving: {page.stdout!r}")

    history = sc.run(
        store_query(
            "SELECT status, error FROM deployments "
            "WHERE domain = 'rel.test' ORDER BY id DESC LIMIT 1"
        ),
        timeout=15,
        label="SELECT the last deployment of rel.test",
    )
    sc.check(
        history.stdout.startswith("failed|") and "broken on purpose" in history.stdout,
        "the deployment record does not carry the process's own error",
    )
    statuses = sc.run(
        store_query(
            "SELECT r.status FROM releases r JOIN apps a ON a.id = r.app_id "
            "WHERE a.domain = 'rel.test' ORDER BY r.created_at"
        ),
        timeout=15,
        label="SELECT every release status of rel.test",
    )
    sc.check(
        statuses.stdout.split() == ["superseded", "active", "failed"],
        f"release statuses: {statuses.stdout.split()!r}",
    )

    # Leave the fixture deployable for anything that runs after this.
    sc.run(
        f"cd {RELEASE_REPO} && git revert --no-edit HEAD",
        timeout=30,
        label="(fixture repo) git revert --no-edit HEAD",
    )


@scenario("status_and_list")
def scenario_status_and_list(sc: Scenario) -> None:
    """`noust status` and `noust list` succeed and mention the deployed apps.

    Also where the ``wasm`` alias (kept for the whole 3.x series) is
    exercised: it must behave identically, including under ``--json``, where
    nothing but the JSON itself may be on stdout.
    """
    status = sc.run("noust status node.test", timeout=30, label="noust status node.test")
    sc.check(status.returncode == 0, "noust status node.test failed")

    listing = sc.run("noust list", timeout=30, label="noust list")
    sc.check(listing.returncode == 0, "noust list failed")
    sc.check(
        "node.test" in listing.stdout and "static.test" in listing.stdout,
        f"noust list did not mention both deployed apps: {listing.stdout!r}",
    )

    alias_status = sc.run("wasm status node.test", timeout=30, label="wasm status node.test")
    sc.check(alias_status.returncode == 0, "the `wasm` alias failed a command `noust` accepts")

    noust_json = sc.run("noust list --json", timeout=30, label="noust list --json").stdout
    wasm_json = sc.run("wasm list --json", timeout=30, label="wasm list --json").stdout
    sc.check(
        wasm_json == noust_json,
        f"`wasm list --json` differs from `noust list --json`:\n{wasm_json!r}\n{noust_json!r}",
    )
    json.loads(wasm_json)  # raises if the alias printed anything besides the JSON


#: The strict policy the console is served under (plan, Global Constraints).
#: Trusted Types may follow it; nothing may relax it.
CONSOLE_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
    "form-action 'self' https://github.com; object-src 'none'"
)

PANEL_URL = "http://127.0.0.1:8080"


def http_head_and_body(sc: Scenario, path: str) -> tuple[int, dict[str, str], str]:
    """Fetch a panel path inside the container; return status, headers, body."""
    proc = sc.run(
        f"curl -sS -D - {PANEL_URL}{path}",
        timeout=15,
        check=False,
        label=f"curl -D - {PANEL_URL}{path} (body truncated in evidence)",
    )
    # Keep the evidence readable: the console bundle is not something to print.
    if len(sc.evidence[-1]) > 4000:
        sc.evidence[-1] = sc.evidence[-1][:4000] + "\n... (truncated)"
    head, _, body = proc.stdout.replace("\r\n", "\n").partition("\n\n")
    lines = head.splitlines()
    sc.check(bool(lines), f"no response for {path}: {proc.stderr!r}")
    status = int(lines[0].split()[1])
    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    return status, headers, body


@scenario("web_panel_serves_the_console")
def scenario_web_panel(sc: Scenario) -> None:
    """`noust web start` answers /health and serves the console from the wheel.

    The console's Vite build ships inside the package (web/static), so this
    is also the packaging check: a build that was not committed, or a glob
    that stopped matching, is a blank page here and nowhere else before an
    operator opens it.
    """
    sc.run("noust web start --daemon", timeout=60, label="noust web start --daemon")

    health: subprocess.CompletedProcess[str] | None = None
    deadline = time.time() + 30
    while time.time() < deadline:
        health = sc.run(
            f"curl -sS -o /dev/null -w '%{{http_code}}' {PANEL_URL}/health",
            timeout=15,
            check=False,
            label=f"curl -o /dev/null -w '%{{http_code}}' {PANEL_URL}/health",
        )
        if health.stdout.strip() == "200":
            break
        time.sleep(1)

    sc.check(
        health is not None and health.stdout.strip() == "200",
        f"panel /health did not return 200 within 30s: {health.stdout if health else None!r}",
    )

    body = sc.run(
        f"curl -sS {PANEL_URL}/health",
        timeout=15,
        check=False,
        label=f"curl {PANEL_URL}/health",
    )
    sc.check('"status"' in body.stdout, f"unexpected /health body: {body.stdout!r}")

    try:
        # The console, at the root and at a deep link a reload lands on.
        for path in ("/", "/apps/example.com/deployments"):
            status, headers, html = http_head_and_body(sc, path)
            sc.check(status == 200, f"GET {path} answered {status}")
            sc.check('<div id="root">' in html, f"GET {path} is not the console: {html[:200]!r}")
            sc.check(
                headers.get("content-type", "").startswith("text/html"),
                f"GET {path} content-type {headers.get('content-type')!r}",
            )
            sc.check(
                headers.get("cache-control") == "no-store",
                f"GET {path} cache-control {headers.get('cache-control')!r}",
            )
            csp = headers.get("content-security-policy", "")
            sc.check(
                csp.startswith(CONSOLE_CSP) and "unsafe-inline" not in csp,
                f"GET {path} is not served under the strict policy: {csp!r}",
            )

        # Every asset the console names loads, and is cached for good.
        _, _, html = http_head_and_body(sc, "/")
        assets = sorted(set(re.findall(r'(?:src|href)="(/assets/[^"]+)"', html)))
        sc.check(bool(assets), "the console names no /assets/ file")
        status_lines = sc.run(
            "for p in "
            + " ".join(assets)
            + f'; do curl -sS -o /dev/null -w "%{{http_code}} $p\\n" {PANEL_URL}$p; done',
            timeout=60,
            check=False,
            label=f"curl every one of the {len(assets)} assets index.html names",
        ).stdout.splitlines()
        failed = [line for line in status_lines if not line.startswith("200 ")]
        sc.check(
            len(status_lines) == len(assets) and not failed,
            f"assets that did not load: {failed or status_lines}",
        )
        status, headers, _ = http_head_and_body(sc, assets[0])
        sc.check(
            "immutable" in headers.get("cache-control", ""),
            f"{assets[0]} cache-control {headers.get('cache-control')!r}",
        )
        status, _, _ = http_head_and_body(sc, "/favicon.svg")
        sc.check(status == 200, f"/favicon.svg answered {status}")

        # A machine path is never shadowed by the console.
        status, headers, _ = http_head_and_body(sc, "/api/does-not-exist")
        sc.check(
            status in (401, 404) and headers.get("content-type", "").startswith("application/json"),
            f"/api/does-not-exist answered {status} {headers.get('content-type')!r}",
        )
    finally:
        sc.run("noust web stop", timeout=30, check=False, label="noust web stop")


# ---------------------------------------------------------------------------
# Instant rollback, migration and resource limits
# ---------------------------------------------------------------------------

#: The in-place application the migration scenario moves onto releases.
INPLACE_DOMAIN = "node.test"
INPLACE_ROOT = "/var/www/apps/node-test"


def tree_census(sc: Scenario, root: str, label: str) -> str:
    """Count the regular files under a directory and their bytes, following nothing."""
    return sc.run(
        f"find {root} -type f -printf '%s\\n' | awk '{{n++; s+=$1}} END {{print n, s}}'",
        timeout=30,
        label=label,
    ).stdout.strip()


@scenario("release_app_instant_rollback")
def scenario_instant_rollback(sc: Scenario) -> None:
    """`noust releases rollback` serves the previous release at once, with nothing rebuilt.

    Runs after the release scenarios, which leave two releases on disk: the
    first serving "ok 1" and the second, active, serving "ok 2".
    """
    listing = sc.run(
        f"noust releases list {RELEASE_DOMAIN} --json",
        timeout=30,
        label=f"noust releases list {RELEASE_DOMAIN} --json",
    )
    items = json.loads(listing.stdout)["items"]
    on_disk = [item for item in items if item["on_disk"]]
    sc.check(len(on_disk) == 2, f"expected two releases on disk: {items!r}")
    newer, older = on_disk[0]["id"], on_disk[1]["id"]
    sc.check(on_disk[0]["active"], f"the newest release is not the active one: {items!r}")

    timed = sc.run(
        f"start=$(date +%s%N); noust releases rollback {RELEASE_DOMAIN}; "
        'echo "elapsed_ms=$(( ($(date +%s%N) - start) / 1000000 ))"',
        timeout=120,
        label=f"noust releases rollback {RELEASE_DOMAIN} (timed)",
    )
    elapsed = int(timed.stdout.rsplit("elapsed_ms=", 1)[1].strip())
    sc.check(elapsed < 30_000, f"the rollback took {elapsed} ms")
    sc.check(
        active_release(sc, "readlink current (after the rollback)") == f"releases/{older}",
        "current does not point at the previous release",
    )
    page = sc.run(
        f"curl -sS -H 'Host: {RELEASE_DOMAIN}' http://127.0.0.1/",
        timeout=30,
        label=f"curl -H 'Host: {RELEASE_DOMAIN}' http://127.0.0.1/ (after the rollback)",
    )
    sc.check(page.stdout.strip() == "ok 1", f"expected 'ok 1', got {page.stdout!r}")

    row = sc.run(
        store_query(
            "SELECT status, triggered_by, log_path FROM deployments "
            "WHERE domain = 'rel.test' ORDER BY id DESC LIMIT 1"
        ),
        timeout=15,
        label="SELECT the deployment row of the rollback",
    ).stdout.strip()
    status, trigger, log_path = row.split("|")
    sc.check((status, trigger) == ("success", "cli"), f"deployment row: {row!r}")
    rebuilt = sc.run(
        f"grep -cE 'npm (ci|install)|Installing' {log_path} || true",
        timeout=15,
        label="grep the rollback log for an install",
    )
    sc.check(rebuilt.stdout.strip() == "0", "the rollback installed something")
    statuses = dict(
        line.split("=", 1)
        for line in sc.run(
            store_query(
                "SELECT r.id || '=' || r.status FROM releases r JOIN apps a ON a.id = r.app_id "
                "WHERE a.domain = 'rel.test'"
            ),
            timeout=15,
            label="SELECT the status of every release of rel.test",
        ).stdout.split()
    )
    sc.check(
        (statuses.get(older), statuses.get(newer)) == ("active", "rolled_back"),
        f"release statuses: {statuses!r}",
    )

    # And forward again, by id.
    sc.run(
        f"noust releases rollback {RELEASE_DOMAIN} {newer}",
        timeout=120,
        label=f"noust releases rollback {RELEASE_DOMAIN} {newer}",
    )
    page = sc.run(
        f"curl -sS -H 'Host: {RELEASE_DOMAIN}' http://127.0.0.1/",
        timeout=30,
        label=f"curl -H 'Host: {RELEASE_DOMAIN}' http://127.0.0.1/ (forward again)",
    )
    sc.check(page.stdout.strip() == "ok 2", f"expected 'ok 2', got {page.stdout!r}")


@scenario("inplace_app_migrate_keeps_uploads")
def scenario_migrate(sc: Scenario) -> None:
    """An in-place app with an upload moves onto releases, keeps serving and keeps the upload.

    node.test was deployed in place and took an upload into its own tree
    (uploads/, gitignored) in the first Node scenario. A rehearsal changes
    nothing; the migration moves every file, none copied or lost; the upload
    ends in shared/ and is still reachable; an update afterwards builds a
    release that sees it too.
    """
    before_listing = sc.run(f"ls -A {INPLACE_ROOT}", timeout=15, label=f"ls -A {INPLACE_ROOT}")
    before = tree_census(sc, INPLACE_ROOT, "count files and bytes before the migration")

    sc.run(
        f"noust --dry-run app migrate {INPLACE_DOMAIN}",
        timeout=60,
        label=f"noust --dry-run app migrate {INPLACE_DOMAIN}",
    )
    sc.check(
        sc.run(f"ls -A {INPLACE_ROOT}", timeout=15, label="ls -A (after the rehearsal)").stdout
        == before_listing.stdout,
        "the rehearsal changed the application directory",
    )

    sc.run(
        f"noust app migrate {INPLACE_DOMAIN} --yes",
        timeout=DEPLOY_TIMEOUT,
        label=f"noust app migrate {INPLACE_DOMAIN} --yes",
    )
    after = tree_census(sc, INPLACE_ROOT, "count files and bytes after the migration")
    sc.check(after == before, f"files and bytes before {before!r}, after {after!r}")

    layout = sc.run(
        store_query(
            "SELECT layout || ' ' || persistent_paths FROM apps WHERE domain = 'node.test'"
        ),
        timeout=15,
        label="SELECT layout, persistent_paths FROM apps WHERE domain = 'node.test'",
    )
    sc.check(
        layout.stdout.strip().startswith("releases") and "uploads" in layout.stdout,
        f"store row: {layout.stdout!r}",
    )
    tree = sc.run(
        f"ls -A {INPLACE_ROOT}; readlink {INPLACE_ROOT}/current {INPLACE_ROOT}/current/uploads "
        f"{INPLACE_ROOT}/current/.env; cat {INPLACE_ROOT}/shared/uploads/upload.txt",
        timeout=15,
        label="the migrated tree: entries, links and the upload in shared/",
    )
    for expected in ("current", "releases", "shared", "../../shared/uploads", "../../shared/.env"):
        sc.check(expected in tree.stdout.split(), f"{expected} missing: {tree.stdout!r}")
    sc.check("integration-harness-upload" in tree.stdout, "the upload is not in shared/uploads")
    unit = sc.run(
        "systemctl cat node-test | grep -E '^WorkingDirectory='",
        timeout=15,
        label="systemctl cat node-test | grep WorkingDirectory",
    )
    sc.check(
        f"WorkingDirectory={INPLACE_ROOT}/current" in unit.stdout,
        f"the unit does not run from current: {unit.stdout!r}",
    )
    page = sc.run(
        f"curl -sS -H 'Host: {INPLACE_DOMAIN}' http://127.0.0.1/",
        timeout=30,
        label=f"curl -H 'Host: {INPLACE_DOMAIN}' http://127.0.0.1/ (after the migration)",
    )
    sc.check(page.stdout.strip() == "ok 2", f"expected 'ok 2', got {page.stdout!r}")

    # A new upload lands in shared/, through the release.
    sc.run(
        f"curl -sS -H 'Host: {INPLACE_DOMAIN}' --data-binary 'after-migration' "
        "http://127.0.0.1/upload",
        timeout=30,
        label=f"curl -H 'Host: {INPLACE_DOMAIN}' --data-binary ... /upload (after the migration)",
    )
    sc.check(
        "after-migration"
        in sc.run(
            f"cat {INPLACE_ROOT}/shared/uploads/upload.txt",
            timeout=15,
            label="cat shared/uploads/upload.txt",
        ).stdout,
        "an upload after the migration did not land in shared/",
    )

    # The migrated application keeps updating, now as releases.
    sc.run(
        "cd /root/fixtures/node-app && echo 3 > VERSION && "
        "git add VERSION && git commit -q -m 'bump version to 3'",
        timeout=30,
        label="(fixture repo) echo 3 > VERSION; git commit",
    )
    sc.run(
        f"noust update {INPLACE_DOMAIN}",
        timeout=DEPLOY_TIMEOUT,
        label=f"noust update {INPLACE_DOMAIN} (after the migration)",
    )
    count = sc.run(f"ls {INPLACE_ROOT}/releases | wc -l", timeout=15, label="ls releases | wc -l")
    sc.check(count.stdout.strip() == "2", f"expected 2 releases, found {count.stdout.strip()}")
    page = sc.run(
        f"curl -sS -H 'Host: {INPLACE_DOMAIN}' http://127.0.0.1/",
        timeout=30,
        label=f"curl -H 'Host: {INPLACE_DOMAIN}' http://127.0.0.1/ (after the update)",
    )
    sc.check(page.stdout.strip() == "ok 3", f"expected 'ok 3', got {page.stdout!r}")
    kept = sc.run(
        f"cat {INPLACE_ROOT}/current/uploads/upload.txt",
        timeout=15,
        check=False,
        label="cat current/uploads/upload.txt (through the new release)",
    )
    sc.check("after-migration" in kept.stdout, "the new release does not see the upload")


@scenario("resource_limits_in_systemd")
def scenario_limits(sc: Scenario) -> None:
    """`noust app limits` puts the limits in the unit, and systemd reports them.

    ``--restart`` passes the same health gate as a deploy, so the application
    answers again the moment the command returns.
    """
    serving = sc.run(
        f"curl -sS -H 'Host: {RELEASE_DOMAIN}' http://127.0.0.1/",
        timeout=30,
        label=f"curl -H 'Host: {RELEASE_DOMAIN}' http://127.0.0.1/ (before the limits)",
    ).stdout.strip()
    sc.check(serving.startswith("ok "), f"{RELEASE_DOMAIN} is not serving: {serving!r}")
    sc.run(
        f"noust app limits {RELEASE_DOMAIN} --memory 512M --cpu 50% --tasks 256 --restart",
        timeout=60,
        label=f"noust app limits {RELEASE_DOMAIN} --memory 512M --cpu 50% --tasks 256 --restart",
    )
    shown = sc.run(
        "systemctl show rel-test -p MemoryMax,CPUQuotaPerSecUSec,TasksMax",
        timeout=15,
        label="systemctl show rel-test -p MemoryMax,CPUQuotaPerSecUSec,TasksMax",
    )
    properties = dict(line.split("=", 1) for line in shown.stdout.split())
    sc.check(
        properties
        == {"MemoryMax": str(512 * 1024 * 1024), "CPUQuotaPerSecUSec": "500ms", "TasksMax": "256"},
        f"systemd reports {properties!r}",
    )
    sc.check(
        sc.run(
            "systemctl is-active rel-test", timeout=15, check=False, label="is-active"
        ).stdout.strip()
        == "active",
        "the application did not come back under its limits",
    )
    page = sc.run(
        f"curl -sS -H 'Host: {RELEASE_DOMAIN}' http://127.0.0.1/",
        timeout=30,
        label=f"curl -H 'Host: {RELEASE_DOMAIN}' http://127.0.0.1/ (under limits)",
    )
    sc.check(page.stdout.strip() == serving, f"expected {serving!r}, got {page.stdout!r}")
    row = sc.run(
        store_query(
            "SELECT memory_max_mb || ' ' || cpu_quota_percent || ' ' || tasks_max "
            "FROM apps WHERE domain = 'rel.test'"
        ),
        timeout=15,
        label="SELECT the limits of rel.test",
    )
    sc.check(row.stdout.strip() == "512 50 256", f"store row: {row.stdout!r}")

    # Removing one removes its directive.
    sc.run(
        f"noust app limits {RELEASE_DOMAIN} --memory none",
        timeout=60,
        label=f"noust app limits {RELEASE_DOMAIN} --memory none",
    )
    unit = sc.run(
        "systemctl cat rel-test | grep -E '^(MemoryMax|CPUQuota|TasksMax)=' || true",
        timeout=15,
        label="systemctl cat rel-test | grep the limit directives",
    )
    sc.check(
        unit.stdout.split() == ["CPUQuota=50%", "TasksMax=256"],
        f"directives after removing the memory limit: {unit.stdout!r}",
    )


def curl_host(sc: Scenario, host: str, path: str = "/", *, head: bool = False) -> str:
    """Ask nginx on loopback for ``path`` as ``host``; return the body, or the headers."""
    flags = "-sS -I" if head else "-sS"
    return sc.run(
        f"curl {flags} -H 'Host: {host}' 'http://127.0.0.1{path}'",
        timeout=30,
        check=False,
        label=f"curl {flags} -H 'Host: {host}' http://127.0.0.1{path}",
    ).stdout


def check_alias_and_redirect(sc: Scenario, primary: str, alias: str, redirect: str) -> None:
    """Give an app an alias and a redirect, and see nginx serve and redirect them."""
    sc.run(
        f"noust domain add {primary} {alias} --kind alias",
        timeout=60,
        label=f"noust domain add {primary} {alias} --kind alias",
    )
    sc.run(
        f"noust domain add {primary} {redirect} --kind redirect",
        timeout=60,
        label=f"noust domain add {primary} {redirect} --kind redirect",
    )
    listed = sc.run(
        f"noust domain list {primary} --json",
        timeout=30,
        label=f"noust domain list {primary} --json",
    )
    kinds = {item["domain"]: item["kind"] for item in json.loads(listed.stdout)["items"]}
    sc.check(
        kinds == {primary: "primary", alias: "alias", redirect: "redirect"},
        f"noust domain list says {kinds!r}",
    )

    served = curl_host(sc, primary).strip()
    sc.check(served.startswith("ok "), f"{primary} is not serving: {served!r}")
    through_alias = curl_host(sc, alias).strip()
    sc.check(through_alias == served, f"{alias} served {through_alias!r}, not {served!r}")

    headers = curl_host(sc, redirect, "/some/path?q=1", head=True)
    status_line = headers.splitlines()[0] if headers else ""
    location = re.search(r"^location:\s*(\S+)", headers, re.IGNORECASE | re.MULTILINE)
    sc.check(" 301" in status_line, f"{redirect} answered {status_line!r}, not a 301")
    sc.check(
        location is not None and location.group(1) == f"http://{primary}/some/path?q=1",
        f"{redirect} redirected to {location.group(1) if location else None!r}",
    )
    sc.run("nginx -t", timeout=15, label="nginx -t (after the domain changes)")


@scenario("domains_alias_redirect_and_removal")
def scenario_domains(sc: Scenario) -> None:
    """An app answers on an alias and redirects another name; removing the alias stops it.

    Once in place and once on releases: the site of a release app is written
    against ``current``, and a domain change must render it that way too.
    The primary cannot be removed, and a removal takes effect within seconds.
    """
    sc.run(
        "noust create -d dom.test -s /root/fixtures/node-app -t nodejs --no-ssl --layout inplace",
        timeout=DEPLOY_TIMEOUT,
        label="noust create -d dom.test -s /root/fixtures/node-app -t nodejs --no-ssl "
        "--layout inplace",
    )
    check_alias_and_redirect(sc, "dom.test", "alias.dom.test", "old-dom.test")

    sc.run(
        "noust domain remove dom.test alias.dom.test",
        timeout=60,
        label="noust domain remove dom.test alias.dom.test",
    )
    # nginx -s reload signals the master and returns before the new workers
    # take connections, so an old worker may answer for a moment: the removal
    # must take effect within seconds, not before the command returns.
    deadline = time.monotonic() + 5
    after = curl_host(sc, "alias.dom.test").strip()
    while after.startswith("ok ") and time.monotonic() < deadline:
        time.sleep(0.25)
        after = curl_host(sc, "alias.dom.test").strip()
    sc.check(
        not after.startswith("ok "),
        f"alias.dom.test is still served by dom.test 5 s after its removal: {after!r}",
    )
    sc.check(
        curl_host(sc, "dom.test").strip().startswith("ok "),
        "dom.test stopped serving when its alias was removed",
    )
    config = sc.run(
        "cat /etc/nginx/sites-available/dom.test",
        timeout=15,
        label="cat /etc/nginx/sites-available/dom.test (after the removal)",
    )
    sc.check("alias.dom.test" not in config.stdout, "the removed alias is still in the site")

    refused = sc.run(
        "noust domain remove dom.test dom.test",
        timeout=30,
        check=False,
        label="noust domain remove dom.test dom.test (the primary)",
    )
    sc.check(refused.returncode == 1, "removing the primary domain was not refused")

    sc.run(
        f"noust create -d reldom.test -s {RELEASE_URL} -t nodejs --no-ssl --layout releases",
        timeout=DEPLOY_TIMEOUT,
        label=f"noust create -d reldom.test -s {RELEASE_URL} -t nodejs --no-ssl --layout releases",
    )
    check_alias_and_redirect(sc, "reldom.test", "alias.reldom.test", "old-reldom.test")


# ---------------------------------------------------------------------------
# Upgrade rehearsal: 1.6.5 -> 2.0 (--upgrade mode; not part of the default suite)
# ---------------------------------------------------------------------------
#
# This is the most important pre-release scenario for 2.0: a real 1.6.5
# server, with real applications, upgraded in place. It needs a different
# setup sequence to every scenario above (install the released package
# first, upgrade to the working tree's wheel second), so it runs in its own
# container through `run.py --upgrade` instead of being one more entry in
# SCENARIOS: a plain `run.py` invocation, and CI, keep running exactly the
# scenarios they always have.


#: The stack the Compose health gate scenario deploys, served by git daemon.
COMPOSE_GATE_REPO = "/root/fixtures/compose-gate"
COMPOSE_GATE_URL = "git://127.0.0.1/compose-gate"
COMPOSE_GATE_DOMAIN = "compose-gate.test"
COMPOSE_GATE_ROOT = "/var/www/apps/compose-gate-test"
COMPOSE_GATE_PROJECT = "compose-gate-test"


def compose_gate_web(sc: Scenario, script: str, label: str) -> subprocess.CompletedProcess[str]:
    """Run a shell command inside the stack's web container."""
    container = (
        "$(docker ps -q "
        f"-f label=com.docker.compose.project={COMPOSE_GATE_PROJECT} "
        "-f label=com.docker.compose.service=web)"
    )
    return sc.run(f"docker exec {container} sh -c '{script}'", timeout=30, label=label)


@scenario("compose_update_rolls_back")
def scenario_compose_rollback(sc: Scenario) -> None:
    """A stack whose update does not answer goes back to the images and commit that served.

    The web service is built from the repository, so going back needs the
    image the old container ran, not a rebuild. A file written into a named
    volume before the update must still be there after the way back.
    Skipped, with a note, when Docker is not reachable inside the container.
    """
    probe = sc.run(
        "command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1 "
        "&& echo available || echo unavailable",
        timeout=30,
        check=False,
        label="check whether Docker is reachable inside the container",
    )
    if probe.stdout.strip() != "available":
        sc.evidence.append(
            "NOTE: Docker is not reachable inside the integration container "
            "(tests/integration/Dockerfile.systemd does not install it), so this scenario "
            "is SKIPPED. Its behaviour is covered with a fake runner by "
            "tests/test_inplace_health_gate.py."
        )
        return

    sc.run(
        f"mkdir -p {COMPOSE_GATE_REPO}/html && cd {COMPOSE_GATE_REPO} && "
        "printf 'FROM nginx:alpine\\nCOPY html /usr/share/nginx/html\\n' > Dockerfile && "
        "printf 'gate v1' > html/index.html && "
        'printf \'services:\\n  web:\\n    build: .\\n    ports:\\n      - "18091:80"\\n'
        "    volumes:\\n      - data:/data\\nvolumes:\\n  data: {}\\n' > docker-compose.yml && "
        "git init -q && git config user.email noust-it@example.com && "
        "git config user.name 'Noust Integration' && git add -A && git commit -q -m 'gate v1'",
        timeout=30,
        label="(fixture repo) a stack whose web image is built from the repository",
    )
    first = sc.run(
        f"git -C {COMPOSE_GATE_REPO} rev-parse HEAD", timeout=15, label="the first commit"
    ).stdout.strip()

    create = (
        f"noust create -d {COMPOSE_GATE_DOMAIN} -s {COMPOSE_GATE_URL} -t docker-compose --no-ssl"
    )
    sc.run(create, timeout=DEPLOY_TIMEOUT, label=create)
    page = sc.run(
        f"curl -sS -H 'Host: {COMPOSE_GATE_DOMAIN}' http://127.0.0.1/",
        timeout=30,
        label=f"curl -H 'Host: {COMPOSE_GATE_DOMAIN}' http://127.0.0.1/",
    )
    sc.check("gate v1" in page.stdout, f"the stack did not serve v1: {page.stdout!r}")
    compose_gate_web(sc, "echo kept > /data/marker", "write a marker into the named volume")

    # nginx refuses to start on a broken configuration: the container exits.
    sc.run(
        f"cd {COMPOSE_GATE_REPO} && printf 'gate v2' > html/index.html && "
        "printf 'FROM nginx:alpine\\nCOPY html /usr/share/nginx/html\\n"
        "RUN echo broken > /etc/nginx/conf.d/broken.conf\\n' > Dockerfile && "
        "git add -A && git commit -q -m 'gate v2: nginx cannot start'",
        timeout=30,
        label="(fixture repo) commit a version whose container cannot start",
    )
    update = sc.run(
        f"noust update {COMPOSE_GATE_DOMAIN}",
        timeout=DEPLOY_TIMEOUT,
        check=False,
        label=f"noust update {COMPOSE_GATE_DOMAIN}",
    )
    output = update.stdout + update.stderr
    sc.check(update.returncode != 0, "an update that does not answer must fail")
    sc.check(
        "did not pass its health check" in output and "running again" in output,
        f"the update did not say it went back: {output!r}",
    )

    page = sc.run(
        f"curl -sS -H 'Host: {COMPOSE_GATE_DOMAIN}' http://127.0.0.1/",
        timeout=30,
        label=f"curl -H 'Host: {COMPOSE_GATE_DOMAIN}' http://127.0.0.1/",
    )
    sc.check("gate v1" in page.stdout, f"v1 is not serving again: {page.stdout!r}")
    marker = compose_gate_web(sc, "cat /data/marker", "read the marker back from the volume")
    sc.check(marker.stdout.strip() == "kept", f"the named volume lost its data: {marker.stdout!r}")
    head = sc.run(
        f"git -C {COMPOSE_GATE_ROOT} rev-parse HEAD", timeout=15, label="the tree's commit"
    )
    sc.check(head.stdout.strip() == first, f"the tree is not back on v1: {head.stdout!r}")
    kept = sc.run(
        f"docker image ls --format '{{{{.Repository}}}}:{{{{.Tag}}}}' {COMPOSE_GATE_PROJECT}-web",
        timeout=15,
        label=f"docker image ls {COMPOSE_GATE_PROJECT}-web",
    )
    sc.check(
        f"{COMPOSE_GATE_PROJECT}-web:wasm-previous" in kept.stdout,
        f"the image that served was not kept: {kept.stdout!r}",
    )
    row = sc.run(
        store_query(
            "SELECT status FROM deployments WHERE domain = 'compose-gate.test' "
            "ORDER BY id DESC LIMIT 1"
        ),
        timeout=15,
        label="SELECT the update's history row",
    )
    sc.check(row.stdout.strip() == "failed", f"the update's row: {row.stdout!r}")


# ---------------------------------------------------------------------------
# 2.2: blue/green, remote backups, pull request previews, deploy notifications
# ---------------------------------------------------------------------------

#: Scripts the 2.2 scenarios run inside the container: a load generator and a
#: notification recorder. Copied in by :func:`install_tools`.
TOOLS_DIR = INTEGRATION_DIR / "tools"
CONTAINER_TOOLS = "/root/it-tools"

#: The notification recorder: a POST listener the ``webhook`` channel points at.
RECORDER_UNIT = "noust-it-recorder"
NOTIFY_PORT = 9199
NOTIFY_URL = f"http://127.0.0.1:{NOTIFY_PORT}/noust"
NOTIFY_LOG = "/root/it-notifications.jsonl"

#: The load generator: one request through nginx every LOAD_INTERVAL seconds.
LOAD_UNIT = "noust-it-load"
LOAD_LOG = "/root/it-load.log"
LOAD_INTERVAL = 0.05

#: What every repository the 2.2 scenarios create commits as.
GIT_IDENTITY = (
    "git config user.email noust-it@example.com && git config user.name 'Noust Integration'"
)


def install_tools(sc: Scenario) -> None:
    """Copy tests/integration/tools into the container; idempotent."""
    sh(["docker", "exec", sc.container, "mkdir", "-p", CONTAINER_TOOLS], timeout=30)
    sh(["docker", "cp", f"{TOOLS_DIR}/.", f"{sc.container}:{CONTAINER_TOOLS}"], timeout=30)


def make_node_repo(sc: Scenario, name: str) -> tuple[str, str]:
    """
    Create a repository of the Node fixture at VERSION 1 on ``main``, served by git daemon.

    Its own repository, so the commits a scenario makes never reach another
    scenario's application. git daemon exports everything under
    /root/fixtures, so the repository is reachable the moment it exists.

    Args:
        sc: The scenario.
        name: Path under /root/fixtures, such as ``bg-app`` or ``acme/prev-app``.

    Returns:
        The repository's path in the container and its git:// URL.
    """
    repo = f"/root/fixtures/{name}"
    sc.run(
        f"rm -rf {repo} && mkdir -p {repo} && "
        f"git -C /root/fixtures/node-app archive HEAD | tar -x -C {repo} && "
        f"cd {repo} && echo 1 > VERSION && git init -q -b main && {GIT_IDENTITY} && "
        "git add -A && git commit -q -m 'version 1'",
        timeout=30,
        label=f"(fixture repo) {repo}: the Node fixture at VERSION 1, served at "
        f"git://127.0.0.1/{name}",
    )
    return repo, f"git://127.0.0.1/{name}"


def commit_to(sc: Scenario, repo: str, script: str, message: str) -> str:
    """Change a fixture repository, commit it, and return the new commit."""
    return sc.run(
        f"cd {repo} && {script} && git add -A && git commit -q -m '{message}' && "
        "git rev-parse HEAD",
        timeout=30,
        label=f"(fixture repo {repo}) {script}; git commit -m '{message}'",
    ).stdout.strip()


def json_of(proc: subprocess.CompletedProcess[str], what: str) -> Any:
    """Parse a command's JSON output, whatever it printed around it."""
    text = proc.stdout.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
        if start < 0:
            raise AssertionError(f"{what} printed no JSON: {text!r}") from None
        return json.loads(text[start:])


def journal_tail(sc: Scenario, units: str, label: str) -> None:
    """Put the end of some units' journal into the evidence, for a failure."""
    sc.run(
        f"journalctl --no-pager -n 60 {units} || true",
        timeout=30,
        check=False,
        label=label,
    )


# -- Notifications -----------------------------------------------------------


def start_notifications(sc: Scenario, *, started: bool) -> None:
    """
    Start the recorder and point Noust's ``webhook`` channel at it.

    Loopback is inside the SSRF guard's forbidden networks, so the recorder's
    host is listed under ``notifications.allow_private_hosts``, which is
    exactly what an operator with an internal endpoint does.

    Args:
        sc: The scenario.
        started: Whether ``deploy_started`` is switched on.
    """
    install_tools(sc)
    sc.run(
        f"systemctl stop {RECORDER_UNIT} 2>/dev/null; systemctl reset-failed {RECORDER_UNIT} "
        f"2>/dev/null; rm -f {NOTIFY_LOG}; systemd-run --unit {RECORDER_UNIT} --collect "
        f"/usr/bin/python3 {CONTAINER_TOOLS}/recorder.py {NOTIFY_PORT} {NOTIFY_LOG}",
        timeout=30,
        label=f"start the notification recorder (POST listener on 127.0.0.1:{NOTIFY_PORT})",
    )
    deadline = time.time() + 15
    while time.time() < deadline:
        probe = docker_exec(
            sc.container,
            f"curl -sS -o /dev/null -w '%{{http_code}}' -X POST -d '{{}}' {NOTIFY_URL}",
            timeout=15,
            check=False,
        )
        if probe.stdout.strip() == "204":
            break
        time.sleep(0.5)
    else:
        raise AssertionError("the notification recorder never answered")
    sc.run(f": > {NOTIFY_LOG}", timeout=15, label="empty the recorder's log")
    for setting in (
        f"notifications.channels.webhook.webhook_url {NOTIFY_URL}",
        "notifications.allow_private_hosts 127.0.0.1 --list",
        f"notifications.events.deploy_started {'true' if started else 'false'}",
        "notifications.enabled true",
    ):
        sc.run(f"noust config set {setting}", timeout=30, label=f"noust config set {setting}")


def stop_notifications(sc: Scenario) -> None:
    """Turn notifications off and stop the recorder; never raises."""
    sc.run(
        "noust config set notifications.enabled false; "
        "noust config set notifications.events.deploy_started false; "
        f"systemctl stop {RECORDER_UNIT} 2>/dev/null; true",
        timeout=60,
        check=False,
        label="turn notifications off and stop the recorder",
    )


def recorded_notifications(sc: Scenario, label: str) -> list[dict[str, Any]]:
    """Every notification the recorder received, in order."""
    lines = sc.run(f"cat {NOTIFY_LOG}", timeout=15, label=label).stdout.splitlines()
    return [json.loads(line) for line in lines if line.strip()]


# -- Load --------------------------------------------------------------------


def start_load(sc: Scenario, host: str) -> int:
    """
    Start one request through nginx every 50 ms as ``host``, and wait for the first answers.

    Returns:
        The log's line count once it is running: the first phase's start.
    """
    install_tools(sc)
    sc.run(
        f"systemctl stop {LOAD_UNIT} 2>/dev/null; systemctl reset-failed {LOAD_UNIT} 2>/dev/null; "
        f"rm -f {LOAD_LOG}; systemd-run --unit {LOAD_UNIT} --collect /usr/bin/python3 "
        f"{CONTAINER_TOOLS}/load.py {host} {LOAD_LOG} {LOAD_INTERVAL}",
        timeout=30,
        label=f"start the load: GET / as {host} through nginx every {int(LOAD_INTERVAL * 1000)} ms",
    )
    deadline = time.time() + 15
    while time.time() < deadline:
        count = load_mark(sc)
        if count >= 10:
            return count
        time.sleep(0.5)
    raise AssertionError("the load generator is not writing its log")


def load_mark(sc: Scenario) -> int:
    """The number of complete lines in the load log."""
    proc = docker_exec(sc.container, f"wc -l < {LOAD_LOG} 2>/dev/null || echo 0", timeout=15)
    return int(proc.stdout.strip() or 0)


def load_phase(sc: Scenario, phase: str, since: int) -> int:
    """
    Count what the load saw since a mark, put it in the evidence, and fail on any error.

    Args:
        sc: The scenario.
        phase: What was happening, for the evidence.
        since: The mark the phase started at.

    Returns:
        The mark the next phase starts at.
    """
    # Requests in flight when the command returned land within the timeout.
    time.sleep(1)
    proc = docker_exec(sc.container, f"tail -n +{since + 1} {LOAD_LOG}", timeout=30)
    text = proc.stdout
    if not text.endswith("\n"):
        text = text[: text.rfind("\n") + 1]
    lines = text.splitlines()
    failures = [line for line in lines if not line.startswith("OK ")]
    answers: dict[str, int] = {}
    order: list[str] = []
    for line in lines:
        if line.startswith("OK "):
            body = line.split(" ", 3)[3] if line.count(" ") >= 3 else ""
            if body not in answers:
                order.append(body)
            answers[body] = answers.get(body, 0) + 1
    seconds = 0.0
    if lines:
        seconds = float(lines[-1].split(" ", 2)[1]) - float(lines[0].split(" ", 2)[1])
    summary = ", ".join(f"{body!r} x{answers[body]}" for body in order) or "none"
    sc.evidence.append(
        f"[load] {phase}: {len(lines)} requests through nginx over {seconds:.1f}s, "
        f"{len(failures)} failed; answers in order of appearance: {summary}"
        + ("\n" + "\n".join(failures[:30]) if failures else "")
    )
    running = docker_exec(sc.container, f"systemctl is-active {LOAD_UNIT}", check=False)
    sc.check(running.stdout.strip() == "active", f"the load generator died during {phase}")
    sc.check(len(lines) >= 20, f"{phase}: only {len(lines)} requests were made")
    sc.check(
        not failures,
        f"{phase}: {len(failures)} of {len(lines)} requests failed: {failures[:5]}",
    )
    return since + len(lines)


# -- Blue/green ----------------------------------------------------------------

BG_DOMAIN = "bg.test"
BG_APP = "bg-test"
BG_PORT = 3710
BG_UPSTREAM = f"/etc/nginx/noust-upstreams/{BG_APP}.conf"
BG_TEMPLATE = f"/etc/systemd/system/{BG_APP}@.service"
BG_UNIT_FILE = f"/etc/systemd/system/{BG_APP}.service"


def zero_downtime_status(sc: Scenario, label: str) -> dict[str, Any]:
    """``noust app zero-downtime DOMAIN --json``."""
    status: dict[str, Any] = json_of(
        sc.run(f"noust app zero-downtime {BG_DOMAIN} --json", timeout=30, label=label), label
    )
    return status


def unit_states(sc: Scenario, label: str) -> dict[str, str]:
    """systemd's ActiveState of the application's own unit and of both instances."""
    units = [BG_APP, f"{BG_APP}@blue", f"{BG_APP}@green"]
    proc = sc.run(f"systemctl is-active {' '.join(units)}", timeout=15, check=False, label=label)
    return dict(zip(units, proc.stdout.split(), strict=False))


def listening(sc: Scenario, port: int) -> bool:
    """Whether something listens on a loopback TCP port."""
    proc = docker_exec(sc.container, f"ss -ltnH 'sport = :{port}'", timeout=15, check=False)
    return bool(proc.stdout.strip())


@scenario("blue_green_zero_downtime")
def scenario_blue_green(sc: Scenario) -> None:
    """
    Blue/green: an update, a rollback, a broken update and switching off serve every request.

    A Node app on releases is switched to two instances with a 3 s drain.
    While a request goes through nginx every 50 ms, it is updated (the other
    colour serves the new release), rolled back (the first colour serves the
    old one), updated to a commit whose server throws at start (the update
    fails, the colour that served keeps serving and a rolled-back
    notification goes out), and switched off (its own unit serves on its own
    port again). Not one request may fail in any of them.
    """
    repo, url = make_node_repo(sc, "bg-app")
    create = f"noust create -d {BG_DOMAIN} -s {url} -t nodejs --no-ssl --layout releases --port {BG_PORT}"
    sc.run(create, timeout=DEPLOY_TIMEOUT, label=create)
    try:
        _blue_green(sc, repo)
    except AssertionError:
        journal_tail(sc, f"-u '{BG_APP}*'", f"journalctl -u '{BG_APP}*' (on failure)")
        raise
    finally:
        sc.run(f"systemctl stop {LOAD_UNIT}; true", timeout=30, check=False, label="stop the load")
        stop_notifications(sc)
        sc.run(f"noust delete {BG_DOMAIN} -f", timeout=180, check=False, label="cleanup")


def _blue_green(sc: Scenario, repo: str) -> None:
    """The body of :func:`scenario_blue_green`."""
    served = curl_host(sc, BG_DOMAIN).strip()
    sc.check(served == "ok 1", f"expected 'ok 1' before the switch, got {served!r}")

    sc.run(
        f"noust app zero-downtime {BG_DOMAIN} on --drain 3",
        timeout=180,
        label=f"noust app zero-downtime {BG_DOMAIN} on --drain 3",
    )
    status = zero_downtime_status(sc, "noust app zero-downtime --json (after on)")
    sc.check(
        status["enabled"] and status["active_color"] == "green" and status["drain_seconds"] == 3,
        f"the mode after on: {status!r}",
    )
    sc.check(status["upstream_port"] == BG_PORT + 1, f"upstream port: {status!r}")
    states = unit_states(sc, "systemctl is-active (after on)")
    sc.check(states.get(f"{BG_APP}@green") == "active", f"green is not active: {states!r}")
    sc.check(states.get(BG_APP) != "active", f"the old single unit still runs: {states!r}")
    files = sc.run(
        f"cat {BG_UPSTREAM}; test -e {BG_UNIT_FILE} && echo single-unit-file-present; "
        f"test -e {BG_TEMPLATE} && echo template-present; "
        f"grep -c 'noust-upstreams/{BG_APP}.conf' /etc/nginx/sites-available/{BG_DOMAIN}",
        timeout=15,
        check=False,
        label="the upstream, the unit files and the site (after on)",
    )
    sc.check(
        f"server 127.0.0.1:{BG_PORT + 1};" in files.stdout,
        f"the upstream does not name green's port: {files.stdout!r}",
    )
    sc.check("single-unit-file-present" not in files.stdout, f"{BG_UNIT_FILE} is still there")
    sc.check("template-present" in files.stdout, f"{BG_TEMPLATE} was not written")
    sc.check(files.stdout.strip().endswith("1"), "the site does not include the upstream file")
    sc.check(
        listening(sc, BG_PORT + 1) and not listening(sc, BG_PORT),
        "green should listen on port+1 and nothing on the app's own port",
    )
    served = curl_host(sc, BG_DOMAIN).strip()
    sc.check(served == "ok 1", f"expected 'ok 1' through the upstream, got {served!r}")

    mark = start_load(sc, BG_DOMAIN)

    # (a) An update starts the new release on blue and switches to it.
    commit_to(sc, repo, "echo 2 > VERSION", "version 2")
    sc.run(f"noust update {BG_DOMAIN}", timeout=DEPLOY_TIMEOUT, label=f"noust update {BG_DOMAIN}")
    mark = load_phase(sc, "(a) noust update (green -> blue)", mark)
    status = zero_downtime_status(sc, "noust app zero-downtime --json (after the update)")
    sc.check(status["active_color"] == "blue", f"the update did not switch colour: {status!r}")
    sc.check(status["upstream_port"] == BG_PORT, f"upstream after the update: {status!r}")
    states = unit_states(sc, "systemctl is-active (after the update)")
    sc.check(
        states.get(f"{BG_APP}@blue") == "active" and states.get(f"{BG_APP}@green") != "active",
        f"green was not stopped after the drain: {states!r}",
    )
    served = curl_host(sc, BG_DOMAIN).strip()
    sc.check(served == "ok 2", f"expected 'ok 2' after the update, got {served!r}")

    # (b) A rollback puts the previous release back on green.
    sc.run(
        f"noust releases rollback {BG_DOMAIN}",
        timeout=180,
        label=f"noust releases rollback {BG_DOMAIN}",
    )
    mark = load_phase(sc, "(b) noust releases rollback (blue -> green)", mark)
    status = zero_downtime_status(sc, "noust app zero-downtime --json (after the rollback)")
    sc.check(status["active_color"] == "green", f"the rollback did not switch colour: {status!r}")
    served = curl_host(sc, BG_DOMAIN).strip()
    sc.check(served == "ok 1", f"expected 'ok 1' after the rollback, got {served!r}")

    # (c) A release that throws at start never takes the traffic.
    start_notifications(sc, started=False)
    commit_to(
        sc,
        repo,
        "echo 3 > VERSION && sed -i '1i throw new Error(\"broken on purpose\");' server.js",
        "break the server",
    )
    broken = sc.run(
        f"noust update {BG_DOMAIN}",
        timeout=DEPLOY_TIMEOUT,
        check=False,
        label=f"noust update {BG_DOMAIN} (a commit whose server throws at start)",
    )
    mark = load_phase(sc, "(c) noust update to a broken commit (green keeps serving)", mark)
    sc.check(broken.returncode != 0, "the update of a release that never ran reported success")
    sc.check(
        "green instance kept serving" in broken.stdout + broken.stderr,
        "the failure does not say the serving instance kept serving",
    )
    status = zero_downtime_status(sc, "noust app zero-downtime --json (after the broken update)")
    sc.check(status["active_color"] == "green", f"the colour moved: {status!r}")
    sc.check(status["upstream_port"] == BG_PORT + 1, f"the upstream moved: {status!r}")
    states = unit_states(sc, "systemctl is-active (after the broken update)")
    sc.check(states.get(f"{BG_APP}@blue") != "active", f"blue was left running: {states!r}")
    row = sc.run(
        store_query(
            "SELECT status || '|' || COALESCE(error, '') FROM deployments "
            "WHERE domain = 'bg.test' ORDER BY id DESC LIMIT 1"
        ),
        timeout=15,
        label="SELECT the last deployment of bg.test",
    ).stdout.strip()
    sc.check(
        row.split("|", 1)[0] in ("failed", "rolled_back") and "broken on purpose" in row,
        f"the history does not show the failed deployment with its cause: {row!r}",
    )
    served = curl_host(sc, BG_DOMAIN).strip()
    sc.check(served == "ok 1", f"expected 'ok 1' after the failed update, got {served!r}")
    events = [
        event
        for event in recorded_notifications(sc, "the notifications the recorder received")
        if event.get("domain") == BG_DOMAIN
    ]
    kinds = [event.get("event") for event in events]
    sc.check(
        kinds == ["deploy_rolled_back"],
        f"expected exactly one deploy_rolled_back notification for {BG_DOMAIN}, got {kinds!r}",
    )
    sc.check(
        "broken on purpose" in str(events[0].get("body")),
        f"the rolled-back notification does not carry the health gate's cause: {events[0]!r}",
    )
    stop_notifications(sc)
    sc.run(f"cd {repo} && git revert --no-edit HEAD", timeout=30, label="(fixture repo) revert")

    # (d) Switching off hands the traffic back to the app's own unit.
    sc.run(
        f"noust app zero-downtime {BG_DOMAIN} off",
        timeout=180,
        label=f"noust app zero-downtime {BG_DOMAIN} off",
    )
    mark = load_phase(sc, "(d) noust app zero-downtime off (green -> single unit)", mark)
    status = zero_downtime_status(sc, "noust app zero-downtime --json (after off)")
    sc.check(not status["enabled"], f"the mode is still on: {status!r}")
    states = unit_states(sc, "systemctl is-active (after off)")
    sc.check(
        states.get(BG_APP) == "active"
        and states.get(f"{BG_APP}@blue") != "active"
        and states.get(f"{BG_APP}@green") != "active",
        f"units after off: {states!r}",
    )
    files = sc.run(
        f"test -e {BG_TEMPLATE} && echo template-present; "
        f"test -e {BG_UPSTREAM} && echo upstream-present; "
        f"test -e {BG_UNIT_FILE} && echo single-unit-file-present; "
        f"grep -c 'noust-upstreams' /etc/nginx/sites-available/{BG_DOMAIN}; "
        f"ls -A /var/www/apps/{BG_APP}",
        timeout=15,
        check=False,
        label="the template, the upstream, the unit file, the site and the app tree (after off)",
    )
    sc.check("template-present" not in files.stdout, f"{BG_TEMPLATE} is still there")
    sc.check("upstream-present" not in files.stdout, f"{BG_UPSTREAM} is still there")
    sc.check("single-unit-file-present" in files.stdout, f"{BG_UNIT_FILE} was not written")
    sc.check("\n0\n" in f"\n{files.stdout}", "the site still includes the upstream file after off")
    sc.check("colors" not in files.stdout.split(), "the instance links are still there")
    sc.check(
        listening(sc, BG_PORT) and not listening(sc, BG_PORT + 1),
        "the app's own unit should listen on its port and nothing on port+1",
    )
    served = curl_host(sc, BG_DOMAIN).strip()
    sc.check(served == "ok 1", f"expected 'ok 1' after off, got {served!r}")


# -- Remote backups ------------------------------------------------------------

BK_DOMAIN = "bk.test"
BK_APP = "bk-test"
BK_REMOTE_DIR = "/srv/noust-it-remote"
SFTP_IMAGE = "atmoz/sftp:alpine"
SFTP_USER = "noustit"


def local_backup_ids(sc: Scenario, label: str) -> list[str]:
    """The ids of the application's local archives, oldest first."""
    proc = sc.run(
        f"find /var/backups/noust -name '{BK_APP}_*.tar.gz' -printf '%f\\n' | sort",
        timeout=15,
        label=label,
    )
    return [name.removesuffix(".tar.gz") for name in proc.stdout.split()]


def remote_ids(listing: dict[str, Any]) -> list[str]:
    """The backup ids of a ``noust backup remote-list --app`` listing, sorted."""
    return sorted(entry["backup_id"] for entry in listing.get("backups", []))


@scenario("remote_backups_rclone")
def scenario_remote_backups(sc: Scenario) -> None:
    """
    Schedules push to rclone destinations with their own retention, and restore from them.

    A local directory destination takes three scheduled runs and keeps two;
    the local copies keep one; a backup is listed and restored from the
    remote. An SFTP server in a sibling container takes a push; with it
    stopped, a scheduled run exits non-zero, keeps its local backup and
    sends a backup_failed notification.
    """
    probe = sc.run("rclone version | head -1", timeout=30, check=False, label="rclone version")
    sc.check(probe.returncode == 0, "rclone is not installed in the image (Dockerfile.systemd)")
    create = f"noust create -d {BK_DOMAIN} -s /root/fixtures/static-site -t static --no-ssl"
    sc.run(create, timeout=DEPLOY_TIMEOUT, label=create)
    findings: list[str] = []
    sftp = f"{sc.container}-sftp"
    try:
        _remote_backups_local(sc, findings)
        _remote_backups_sftp(sc, sftp, findings)
    finally:
        subprocess.run(["docker", "rm", "-f", sftp], capture_output=True, text=True, timeout=60)
        stop_notifications(sc)
        sc.run(
            f"noust backup schedule delete {BK_DOMAIN}; "
            "noust backup destination remove itlocal -f; "
            "noust backup destination remove itsftp -f; "
            f"noust delete {BK_DOMAIN} -f; rm -rf {BK_REMOTE_DIR}",
            timeout=180,
            check=False,
            label="cleanup",
        )
    sc.check(not findings, "product findings:\n- " + "\n- ".join(findings))


def _remote_backups_local(sc: Scenario, findings: list[str]) -> None:
    """Part (a): a ``local`` destination, retention on both sides, remote-list, restore --from."""
    # A name with a dash is one validate_destination_name accepts, and the
    # one the console's own placeholder suggests. Checked on its own, so the
    # rest of the scenario runs with dashless names whatever it finds.
    dashed = sc.run(
        "noust backup destination add it-dash --type local --field path=/tmp && "
        "noust backup destination test it-dash",
        timeout=60,
        check=False,
        label="noust backup destination add it-dash --type local --field path=/tmp; ... test it-dash",
    )
    if dashed.returncode != 0:
        findings.append(
            "a destination whose name has a dash cannot be reached: rclone reads the remote "
            "`it-dash` from RCLONE_CONFIG_IT-DASH_*, but managers/backup_destinations.py "
            "_env_prefix() turns the dash into an underscore (RCLONE_CONFIG_IT_DASH_*), so "
            "rclone answers `didn't find section in config file` (rclone 1.60.1 from Ubuntu "
            "24.04 and upstream 1.75.1 alike)"
        )
    sc.run(
        "noust backup destination remove it-dash -f",
        timeout=60,
        check=False,
        label="noust backup destination remove it-dash -f",
    )

    sc.run(f"rm -rf {BK_REMOTE_DIR}", timeout=15, label=f"rm -rf {BK_REMOTE_DIR}")
    add = f"noust backup destination add itlocal --type local --field path={BK_REMOTE_DIR}"
    sc.run(add, timeout=60, label=add)
    fresh = sc.run(
        "noust backup destination test itlocal",
        timeout=60,
        check=False,
        label="noust backup destination test itlocal (its folder does not exist yet)",
    )
    if fresh.returncode != 0:
        findings.append(
            "`noust backup destination test` fails on a destination whose folder does not exist "
            "yet, although the path field's help says it is 'created if it does not exist' "
            "(managers/backup_destinations.py test(): `rclone lsf` of a missing directory)"
        )
        sc.run(f"mkdir -p {BK_REMOTE_DIR}", timeout=15, label=f"mkdir -p {BK_REMOTE_DIR}")
        sc.run(
            "noust backup destination test itlocal",
            timeout=60,
            label="noust backup destination test itlocal (folder created)",
        )

    schedule = (
        f"noust backup schedule create {BK_DOMAIN} --schedule daily --retention-count 1 "
        "--destination itlocal:2"
    )
    sc.run(schedule, timeout=60, label=schedule)
    for run in range(1, 4):
        sc.run(
            f"noust backup run-schedule {BK_DOMAIN}",
            timeout=300,
            label=f"noust backup run-schedule {BK_DOMAIN} (run {run} of 3)",
        )

    remote = sc.run(
        f"ls -1 {BK_REMOTE_DIR}/{BK_APP}",
        timeout=15,
        check=False,
        label=f"ls -1 {BK_REMOTE_DIR}/{BK_APP}",
    ).stdout.split()
    archives = sorted(name.removesuffix(".tar.gz") for name in remote if name.endswith(".tar.gz"))
    sidecars = sorted(name.removesuffix(".json") for name in remote if name.endswith(".json"))
    sc.check(
        len(archives) == 2 and archives == sidecars and len(remote) == 4,
        f"the destination should hold 2 archives and their sidecars: {remote!r}",
    )
    local = local_backup_ids(sc, "the local archives of bk-test")
    sc.check(
        len(local) == 1 and local[0] == archives[-1],
        f"local retention 1 should keep only the newest ({archives[-1]}): {local!r}",
    )

    listing = json_of(
        sc.run(
            f"noust backup remote-list itlocal --app {BK_APP} --json",
            timeout=60,
            label=f"noust backup remote-list itlocal --app {BK_APP} --json",
        ),
        "remote-list",
    )
    sc.check(remote_ids(listing) == archives, f"remote-list: {listing!r}")
    apps = json_of(
        sc.run(
            "noust backup remote-list itlocal --json",
            timeout=60,
            label="noust backup remote-list itlocal --json",
        ),
        "remote-list",
    )
    sc.check(BK_APP in apps.get("apps", []), f"remote-list without --app: {apps!r}")

    # The same listing from another working directory: an absolute path must
    # not depend on where the operator happens to stand.
    elsewhere = sc.run(
        f"cd /root && noust backup remote-list itlocal --app {BK_APP} --json",
        timeout=60,
        check=False,
        label=f"cd /root && noust backup remote-list itlocal --app {BK_APP} --json",
    )
    try:
        elsewhere_ids = remote_ids(json_of(elsewhere, "remote-list from /root"))
    except (AssertionError, json.JSONDecodeError):
        elsewhere_ids = []
    if elsewhere.returncode != 0 or elsewhere_ids != archives:
        findings.append(
            f"a `local` destination with path={BK_REMOTE_DIR} resolves relative to the working "
            "directory: run from /root, remote-list sees "
            f"{elsewhere_ids!r} instead of {archives!r} (managers/backup_destinations.py "
            'target(): `.strip("/")` drops the leading slash, so the rclone path becomes '
            f"itlocal:{BK_REMOTE_DIR.lstrip('/')}; a push from /root writes under "
            f"/root{BK_REMOTE_DIR})"
        )

    root = sc.run(
        f"readlink -e /var/www/apps/{BK_APP}/current || echo /var/www/apps/{BK_APP}",
        timeout=15,
        label="the served tree",
    ).stdout.strip()
    sc.run(
        f"echo tampered > {root}/index.html",
        timeout=15,
        label=f"echo tampered > {root}/index.html",
    )
    sc.check(curl_host(sc, BK_DOMAIN).strip() == "tampered", "the tampered page is not served")
    restore = f"noust backup restore {archives[-1]} --from itlocal -f"
    sc.run(restore, timeout=300, label=restore)
    page = curl_host(sc, BK_DOMAIN)
    sc.check(
        "Noust Integration Static Fixture" in page,
        f"restoring from the destination did not bring the page back: {page[:200]!r}",
    )


def _remote_backups_sftp(sc: Scenario, sftp: str, findings: list[str]) -> None:
    """Part (b): an SFTP destination; a push; the server stopped; the failure is reported."""
    image = subprocess.run(
        ["docker", "image", "inspect", SFTP_IMAGE], capture_output=True, text=True, timeout=30
    )
    if image.returncode != 0:
        pulled = subprocess.run(
            ["docker", "pull", SFTP_IMAGE], capture_output=True, text=True, timeout=300
        )
        if pulled.returncode != 0:
            sc.evidence.append(
                f"NOTE: {SFTP_IMAGE} is not cached and could not be pulled, so the SFTP part "
                f"of this scenario is SKIPPED:\n{pulled.stderr.strip()}"
            )
            return
    password = secrets.token_urlsafe(16)
    subprocess.run(["docker", "rm", "-f", sftp], capture_output=True, text=True, timeout=60)
    sh(
        ["docker", "run", "-d", "--name", sftp, SFTP_IMAGE, f"{SFTP_USER}:{password}:::upload"],
        timeout=60,
    )
    address = sh(
        [
            "docker",
            "inspect",
            "-f",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}",
            sftp,
        ],
        timeout=30,
    ).stdout.split()[0]
    sc.evidence.append(f"[host] docker run -d --name {sftp} {SFTP_IMAGE} (at {address})")
    sc.run(
        f"for i in $(seq 1 60); do timeout 1 bash -c '</dev/tcp/{address}/22' && exit 0; "
        "sleep 0.5; done; exit 1",
        timeout=60,
        label=f"wait for sshd on {address}:22",
    )

    sc.run(
        f"printf '%s' '{password}' | noust backup destination add itsftp --type sftp "
        f"--field host={address} --field user={SFTP_USER} --field path=upload/noust --stdin",
        timeout=60,
        label=f"printf '%s' <password> | noust backup destination add itsftp --type sftp "
        f"--field host={address} --field user={SFTP_USER} --field path=upload/noust --stdin",
    )
    stored = sc.run(
        f"grep -rl '{password}' /var/lib/noust /root/.local/share/noust /etc/noust 2>/dev/null; "
        f"ps -eo args | grep -c '{password}' || true",
        timeout=30,
        check=False,
        label="where the password is stored in clear (the secret store only)",
    )
    sc.check(
        all("secrets" in line or line.strip().isdigit() for line in stored.stdout.splitlines()),
        f"the SFTP password is stored in clear outside the secret store: {stored.stdout!r}",
    )
    fresh = sc.run(
        "noust backup destination test itsftp",
        timeout=120,
        check=False,
        label="noust backup destination test itsftp (upload/noust does not exist yet)",
    )
    if fresh.returncode != 0:
        sh(
            [
                "docker",
                "exec",
                sftp,
                "sh",
                "-c",
                f"mkdir -p /home/{SFTP_USER}/upload/noust && "
                f"chown -R {SFTP_USER} /home/{SFTP_USER}/upload/noust",
            ],
            timeout=30,
        )
        sc.evidence.append(f"[host] docker exec {sftp} mkdir -p /home/{SFTP_USER}/upload/noust")
        sc.run(
            "noust backup destination test itsftp",
            timeout=120,
            label="noust backup destination test itsftp (folder created)",
        )

    update = (
        f"noust backup schedule update {BK_DOMAIN} --schedule daily --retention-count 10 "
        "--destination itsftp:5"
    )
    sc.run(update, timeout=60, label=update)
    pushed = sc.run(
        f"noust backup run-schedule {BK_DOMAIN}",
        timeout=300,
        label=f"noust backup run-schedule {BK_DOMAIN} (to SFTP)",
    )
    match = re.search(rf"({BK_APP}_\d{{8}}_\d{{6}})", pushed.stdout + pushed.stderr)
    sc.check(match is not None, "run-schedule did not name the backup it took")
    backup_id = match.group(1) if match else ""
    arrived = sh(
        ["docker", "exec", sftp, "ls", "-l", f"/home/{SFTP_USER}/upload/noust/{BK_APP}"],
        timeout=30,
        check=False,
    )
    sc.evidence.append(
        f"[host] docker exec {sftp} ls -l /home/{SFTP_USER}/upload/noust/{BK_APP}\n"
        f"{arrived.stdout.rstrip()}{arrived.stderr.rstrip()}"
    )
    sc.check(
        f"{backup_id}.tar.gz" in arrived.stdout and f"{backup_id}.json" in arrived.stdout,
        f"{backup_id} did not arrive on the SFTP server",
    )
    listing = json_of(
        sc.run(
            f"noust backup remote-list itsftp --app {BK_APP} --json",
            timeout=120,
            label=f"noust backup remote-list itsftp --app {BK_APP} --json",
        ),
        "remote-list",
    )
    sc.check(backup_id in remote_ids(listing), f"remote-list itsftp: {listing!r}")

    # The server goes away: the run fails, keeps its local backup, and says so.
    sh(["docker", "stop", "-t", "1", sftp], timeout=60)
    sc.evidence.append(f"[host] docker stop {sftp}")
    start_notifications(sc, started=False)
    before = local_backup_ids(sc, "the local archives (before the run with SFTP down)")
    started = time.monotonic()
    failed = sc.run(
        f"noust backup run-schedule {BK_DOMAIN}",
        timeout=600,
        check=False,
        label=f"noust backup run-schedule {BK_DOMAIN} (SFTP server stopped)",
    )
    elapsed = time.monotonic() - started
    sc.evidence.append(f"[timing] the failing run took {elapsed:.1f}s")
    sc.check(failed.returncode != 0, "run-schedule succeeded with the destination down")
    if "dial tcp" not in failed.stdout + failed.stderr:
        findings.append(
            "`noust backup run-schedule` (what the timer runs, so also its journal) does not say "
            "why a destination failed: run_schedule() raises a summary BackupError once any "
            "destination fails (managers/backup_scheduler.py:745-749, 'see the notification for "
            "rclone's own error'), so the CLI's per-destination 'Failed to send to' lines "
            "(cli/commands/backup.py _run_schedule) never run; without a notification channel "
            "rclone's error is not shown anywhere"
        )
    after = local_backup_ids(sc, "the local archives (after the run with SFTP down)")
    sc.check(
        len(after) == len(before) + 1 and set(before) < set(after),
        f"the local backup was not kept: before {before!r}, after {after!r}",
    )
    events = [
        event
        for event in recorded_notifications(sc, "the notifications the recorder received")
        if event.get("event") == "backup_failed" and event.get("domain") == BK_DOMAIN
    ]
    sc.check(
        # 3.1: the title names the application; the destination is one of the facts.
        len(events) == 1
        and any(fact.get("value") == "itsftp" for fact in events[0].get("facts") or []),
        f"expected one backup_failed notification naming itsftp: {events!r}",
    )
    if elapsed > 120:
        findings.append(
            f"a scheduled run with its SFTP destination unreachable took {elapsed:.0f}s to fail"
        )


# -- Pull request previews ---------------------------------------------------------

PREV_DOMAIN = "prev.test"
PREV_APP = "prev-test"
PREV_REPOSITORY = "acme/prev-app"
PREV_BASE = "previews.test"
PREVIEW_DOMAIN = f"pr-1-{PREV_APP}.{PREV_BASE}"
PREVIEW_APP = f"pr-1-{PREV_APP}-previews-test"


def wait_for_console(sc: Scenario) -> None:
    """Start the console and wait for /health."""
    sc.run("noust web start --daemon", timeout=60, label="noust web start --daemon")
    deadline = time.time() + 30
    while time.time() < deadline:
        probe = docker_exec(
            sc.container,
            f"curl -sS -o /dev/null -w '%{{http_code}}' {PANEL_URL}/health",
            timeout=15,
            check=False,
        )
        if probe.stdout.strip() == "200":
            return
        time.sleep(1)
    raise AssertionError("the console did not answer /health within 30s")


def api(sc: Scenario, token: str, method: str, path: str, label: str) -> tuple[int, Any]:
    """Call the console's API with a Bearer token; the token never reaches the evidence."""
    proc = docker_exec(
        sc.container,
        f"curl -sS -X {method} -H 'Authorization: Bearer {token}' "
        f"-w '\\n%{{http_code}}' {PANEL_URL}{path}",
        timeout=30,
        check=False,
    )
    body, _, code = proc.stdout.rpartition("\n")
    try:
        payload: Any = json.loads(body) if body.strip() else None
    except json.JSONDecodeError:
        payload = body
    sc.evidence.append(f"$ {label}\n{code} {str(payload)[:4000]}")
    return int(code or 0), payload


def deliver(sc: Scenario, secret: str, event: str, payload: dict[str, Any]) -> tuple[int, Any]:
    """POST a GitHub-signed delivery to the parent's webhook."""
    body = json.dumps(payload, separators=(",", ":"))
    sc.check("'" not in body, "a delivery body cannot carry a single quote through printf")
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    proc = sc.run(
        f"printf '%s' '{body}' > /root/it-delivery.json && "
        "curl -sS -o /root/it-delivery.out -w '%{http_code}' -X POST "
        f"-H 'Content-Type: application/json' -H 'X-GitHub-Event: {event}' "
        f"-H 'X-GitHub-Delivery: {secrets.token_hex(8)}' "
        f"-H 'X-Hub-Signature-256: sha256={signature}' "
        f"--data-binary @/root/it-delivery.json {PANEL_URL}/hooks/deploy/{PREV_DOMAIN}; "
        "echo; cat /root/it-delivery.out",
        timeout=60,
        label=f"POST /hooks/deploy/{PREV_DOMAIN} (X-GitHub-Event: {event}, signed): {body}",
    )
    code, _, text = proc.stdout.partition("\n")
    try:
        return int(code), json.loads(text)
    except json.JSONDecodeError:
        return int(code), text


def pull_request(
    action: str,
    number: int,
    branch: str,
    sha: str,
    *,
    author: str = "it-owner",
    author_type: str = "User",
    association: str = "OWNER",
) -> dict[str, Any]:
    """
    A GitHub ``pull_request`` delivery for a branch of the preview repository.

    The author fields are GitHub's: previews build only for the repository's
    owners, members and collaborators, and never for a bot unless allowed.
    """
    repository = {
        "full_name": PREV_REPOSITORY,
        "clone_url": f"git://127.0.0.1/{PREV_REPOSITORY}",
    }
    person = {"login": author, "type": author_type}
    return {
        "action": action,
        "number": number,
        "pull_request": {
            "number": number,
            "title": f"Integration pull request {number}",
            "user": person,
            "author_association": association,
            "head": {"ref": branch, "sha": sha, "repo": repository},
            "base": {"ref": "main", "repo": repository},
        },
        "repository": repository,
        "sender": person,
    }


def wait_for_jobs(sc: Scenario, token: str, job_ids: list[str], what: str) -> None:
    """Wait for console jobs to finish; fail with their log when one did not complete."""
    for job_id in job_ids:
        deadline = time.time() + DEPLOY_TIMEOUT
        job: Any = None
        while time.time() < deadline:
            proc = docker_exec(
                sc.container,
                f"curl -sS -H 'Authorization: Bearer {token}' {PANEL_URL}/api/jobs/{job_id}",
                timeout=30,
                check=False,
            )
            try:
                job = json.loads(proc.stdout)
            except json.JSONDecodeError:
                job = {"status": "unreadable", "raw": proc.stdout}
            if job.get("status") in ("completed", "failed", "cancelled"):
                break
            time.sleep(2)
        logs = "\n".join(
            f"  [{entry.get('level')}] {entry.get('message')}" for entry in job.get("logs", [])
        )
        sc.evidence.append(
            f"$ GET /api/jobs/{job_id} ({what})\nstatus={job.get('status')} "
            f"error={job.get('error')!r}\n{logs}"
        )
        if job.get("status") != "completed":
            api(sc, token, "GET", f"/api/jobs/{job_id}/log", f"GET /api/jobs/{job_id}/log")
        sc.check(job.get("status") == "completed", f"{what}: job {job_id} ended {job!r:.300}")


def parent_state(sc: Scenario, label: str) -> tuple[str, str]:
    """The parent's history row count and active release."""
    count = sc.run(
        store_query("SELECT COUNT(*) FROM deployments WHERE domain = 'prev.test'"),
        timeout=15,
        label=label + ": count the deployments of prev.test",
    ).stdout.strip()
    current = sc.run(
        f"readlink /var/www/apps/{PREV_APP}/current",
        timeout=15,
        label=f"{label}: readlink current of {PREV_DOMAIN}",
    ).stdout.strip()
    return count, current


@scenario("pr_preview_webhook")
def scenario_pr_preview(sc: Scenario) -> None:
    """
    A signed pull_request delivery builds a preview; synchronize updates it; closed removes it.

    The parent is on releases, deployed from git daemon; previews are turned
    on under previews.test. Deliveries are signed with the parent's webhook
    secret, minted through the API with an admin token. The preview answers
    through nginx by Host header. Certificates cannot be issued here (no
    certbot, no public DNS), so the scenario also records what a preview does
    when its certificate fails. A ping and the pull request deliveries never
    update the parent.
    """
    repo, url = make_node_repo(sc, PREV_REPOSITORY)
    create = f"noust create -d {PREV_DOMAIN} -s {url} -t nodejs --no-ssl --layout releases"
    sc.run(create, timeout=DEPLOY_TIMEOUT, label=create)
    findings: list[str] = []
    try:
        _pr_preview(sc, repo, findings)
    except AssertionError:
        journal_tail(sc, "-u 'noust*' -u 'pr-*'", "journalctl (on failure)")
        raise
    finally:
        sc.run(
            f"noust web stop; noust preview disable {PREV_DOMAIN} -y; "
            f"noust delete {PREVIEW_DOMAIN} -f 2>/dev/null; noust delete {PREV_DOMAIN} -f",
            timeout=300,
            check=False,
            label="cleanup",
        )
    sc.check(not findings, "product findings:\n- " + "\n- ".join(findings))


def _pr_preview(sc: Scenario, repo: str, findings: list[str]) -> None:
    """The body of :func:`scenario_pr_preview`."""
    enable = f"noust preview enable {PREV_DOMAIN} --domain {PREV_BASE} --max 2 --ttl 1h"
    sc.run(enable, timeout=60, label=enable)

    issued = docker_exec(
        sc.container,
        f"noust token create it-previews-{secrets.token_hex(4)} --scope admin",
        timeout=30,
    )
    token_match = re.search(r"Token:\s*(\S+)", issued.stdout)
    sc.check(token_match is not None, "noust token create printed no token")
    token = token_match.group(1) if token_match else ""
    sc.evidence.append("$ noust token create it-previews-... --scope admin\n(token issued)")

    wait_for_console(sc)
    code, minted = api(
        sc, token, "POST", f"/api/apps/{PREV_DOMAIN}/webhook-secret", "POST webhook-secret"
    )
    sc.check(code == 200 and isinstance(minted, dict), f"minting the secret answered {code}")
    secret = str(minted["secret"])
    sc.evidence[-1] = sc.evidence[-1].replace(secret, "<secret>")

    parent_before = parent_state(sc, "before the deliveries")

    code, answer = deliver(sc, secret, "ping", {"zen": "Keep it logically awesome.", "hook_id": 1})
    sc.check(code == 200 and answer.get("event") == "ping", f"ping answered {code} {answer!r}")
    wrong = deliver(sc, "not-the-secret", "ping", {"zen": "forged"})
    sc.check(wrong[0] == 401, f"a delivery signed with the wrong secret answered {wrong!r}")

    sha = commit_to(
        sc,
        repo,
        "git checkout -q -b feature-1 && echo pr1 > VERSION",
        "pull request 1",
    )
    sc.run(f"git -C {repo} checkout -q main", timeout=15, label="(fixture repo) back to main")

    # A bot's pull request (Dependabot, say) builds nothing unless the settings allow bots:
    # its code would run as root with the application's secrets.
    code, answer = deliver(
        sc,
        secret,
        "pull_request",
        pull_request(
            "opened",
            9,
            "feature-1",
            sha,
            author="dependabot[bot]",
            author_type="Bot",
            association="NONE",
        ),
    )
    sc.check(
        code == 200 and isinstance(answer, dict) and answer.get("status") == "ignored",
        f"a bot's pull request was refused: {code} {answer}",
    )
    code, answer = deliver(sc, secret, "pull_request", pull_request("opened", 1, "feature-1", sha))
    sc.check(code == 202 and answer.get("job_ids"), f"opened answered {code} {answer!r}")
    wait_for_jobs(sc, token, answer["job_ids"], "the preview's first deploy")

    listed = json_of(
        sc.run(
            f"noust preview list {PREV_DOMAIN} --json",
            timeout=30,
            label=f"noust preview list {PREV_DOMAIN} --json",
        ),
        "preview list",
    )
    items = listed.get("items", [])
    sc.check(
        len(items) == 1
        and items[0]["domain"] == PREVIEW_DOMAIN
        and items[0]["status"] == "ready"
        and items[0]["number"] == 1,
        f"preview list: {items!r}",
    )
    row = sc.run(
        store_query(
            "SELECT layout || '|' || ssl_enabled || '|' || COALESCE(preview_parent, '') "
            "FROM apps WHERE domain = 'pr-1-prev-test.previews.test'"
        ),
        timeout=15,
        label="SELECT layout, ssl_enabled, preview_parent of the preview",
    ).stdout.strip()
    sc.check(
        row.startswith("releases|") and row.endswith(f"|{PREV_DOMAIN}"),
        f"the preview's row: {row!r}",
    )
    served = curl_host(sc, PREVIEW_DOMAIN).strip()
    sc.check(served == "ok pr1", f"the preview should serve 'ok pr1', got {served!r}")
    log_path = sc.run(
        store_query(
            "SELECT log_path FROM deployments "
            "WHERE domain = 'pr-1-prev-test.previews.test' ORDER BY id DESC LIMIT 1"
        ),
        timeout=15,
        label="SELECT the preview's deploy log",
    ).stdout.strip()
    sc.run(
        f"grep -iE 'ssl|certif' {log_path}",
        timeout=15,
        check=False,
        label="what the preview's deploy log says about its certificate",
    )
    tls = sc.run(
        f"grep -cE 'listen 443|ssl_certificate' /etc/nginx/sites-available/{PREVIEW_DOMAIN}",
        timeout=15,
        check=False,
        label="TLS lines in the preview's site",
    )
    sc.evidence.append(
        "NOTE (certificates): a preview is always created with ssl=True "
        "(managers/previews.py _deploy). Here certbot is absent and nothing is publicly "
        f"resolvable; the store says ssl_enabled={row.split('|')[1]!r} and the log lines above "
        "show what the deploy did about it."
    )
    sc.check(
        tls.stdout.strip().endswith("0"),
        "the preview's site has TLS server blocks without a certificate",
    )
    advertised = str(items[0].get("url"))
    if row.split("|")[1] == "0" and advertised.startswith("https://"):
        findings.append(
            f"the preview is served over HTTP only (its certificate failed, ssl_enabled=0) but "
            f"`noust preview list`, the API and the pull request comment advertise {advertised} "
            "(managers/previews.py preview_url() always answers https://)"
        )

    sc.check(
        parent_state(sc, "after ping and opened") == parent_before,
        "a ping or pull_request delivery updated the parent",
    )

    sha2 = commit_to(
        sc,
        repo,
        "git checkout -q feature-1 && echo pr1b > VERSION",
        "pull request 1, second push",
    )
    sc.run(f"git -C {repo} checkout -q main", timeout=15, label="(fixture repo) back to main")
    code, answer = deliver(
        sc, secret, "pull_request", pull_request("synchronize", 1, "feature-1", sha2)
    )
    sc.check(code == 202 and answer.get("job_ids"), f"synchronize answered {code} {answer!r}")
    wait_for_jobs(sc, token, answer["job_ids"], "the preview's update")
    served = curl_host(sc, PREVIEW_DOMAIN).strip()
    sc.check(served == "ok pr1b", f"the updated preview should serve 'ok pr1b', got {served!r}")
    releases = sc.run(
        f"ls /var/www/apps/{PREVIEW_APP}/releases | wc -l",
        timeout=15,
        label="ls releases of the preview | wc -l",
    ).stdout.strip()
    sc.check(releases == "2", f"the update did not build a second release: {releases}")

    code, answer = deliver(sc, secret, "pull_request", pull_request("closed", 1, "feature-1", sha2))
    sc.check(code == 202 and answer.get("job_ids"), f"closed answered {code} {answer!r}")
    wait_for_jobs(sc, token, answer["job_ids"], "the preview's removal")
    rows = sc.run(
        store_query("SELECT COUNT(*) FROM apps WHERE domain = 'pr-1-prev-test.previews.test'"),
        timeout=15,
        label="the preview's row (after closed)",
    ).stdout.strip()
    gone = sc.run(
        f"echo {rows}; test -e /etc/systemd/system/{PREVIEW_APP}.service && echo unit-present; "
        f"test -e /etc/nginx/sites-available/{PREVIEW_DOMAIN} && echo site-present; "
        f"test -e /etc/nginx/sites-enabled/{PREVIEW_DOMAIN} && echo site-enabled; "
        f"test -e /var/www/apps/{PREVIEW_APP} && echo tree-present; "
        f"systemctl is-active {PREVIEW_APP} || true",
        timeout=15,
        check=False,
        label="the preview's row, unit, site and tree (after closed)",
    )
    sc.check(
        gone.stdout.split() in (["0", "inactive"], ["0", "unknown"]),
        f"the preview was not removed completely: {gone.stdout!r}",
    )
    listed = json_of(
        sc.run(
            f"noust preview list {PREV_DOMAIN} --json",
            timeout=30,
            label=f"noust preview list {PREV_DOMAIN} --json (after closed)",
        ),
        "preview list",
    )
    sc.check(listed.get("items") == [], f"the preview record is still listed: {listed!r}")

    sc.check(
        parent_state(sc, "after every delivery") == parent_before,
        "a pull_request delivery updated the parent",
    )
    served = curl_host(sc, PREV_DOMAIN).strip()
    sc.check(served == "ok 1", f"the parent should still serve 'ok 1', got {served!r}")


# -- Deploy notifications --------------------------------------------------------

NTF_DOMAIN = "ntf.test"


@scenario("deploy_notifications")
def scenario_deploy_notifications(sc: Scenario) -> None:
    """
    A deploy from the CLI notifies: started and success, then rolled back with the gate's cause.

    The webhook channel points at a recorder inside the container. With
    deploy_started on, ``noust update`` sends started then success for the
    application; an update to a commit whose server throws sends started then
    rolled back, the process's own error in the body.
    """
    repo, url = make_node_repo(sc, "ntf-app")
    create = f"noust create -d {NTF_DOMAIN} -s {url} -t nodejs --no-ssl --layout releases"
    sc.run(create, timeout=DEPLOY_TIMEOUT, label=create)
    try:
        start_notifications(sc, started=True)

        commit = commit_to(sc, repo, "echo 2 > VERSION", "version 2")
        sc.run(
            f"noust update {NTF_DOMAIN}", timeout=DEPLOY_TIMEOUT, label=f"noust update {NTF_DOMAIN}"
        )
        events = [
            event
            for event in recorded_notifications(sc, "the notifications the recorder received")
            if event.get("domain") == NTF_DOMAIN
        ]
        kinds = [event.get("event") for event in events]
        sc.check(
            kinds == ["deploy_started", "deploy_success"],
            f"expected started then success for {NTF_DOMAIN}, got {kinds!r}",
        )
        sc.check(
            commit[:7] in str(events[1].get("title")) + str(events[1].get("body")),
            f"the success notification does not name the commit {commit[:7]}: {events[1]!r}",
        )

        sc.run(f": > {NOTIFY_LOG}", timeout=15, label="empty the recorder's log")
        commit_to(
            sc,
            repo,
            "echo 3 > VERSION && sed -i '1i throw new Error(\"broken on purpose\");' server.js",
            "break the server",
        )
        broken = sc.run(
            f"noust update {NTF_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            check=False,
            label=f"noust update {NTF_DOMAIN} (a commit whose server throws at start)",
        )
        sc.check(broken.returncode != 0, "the broken update reported success")
        events = [
            event
            for event in recorded_notifications(sc, "the notifications the recorder received")
            if event.get("domain") == NTF_DOMAIN
        ]
        kinds = [event.get("event") for event in events]
        sc.check(
            kinds == ["deploy_started", "deploy_rolled_back"],
            f"expected started then rolled back for {NTF_DOMAIN}, got {kinds!r}",
        )
        sc.check(
            "broken on purpose" in str(events[1].get("body")),
            f"the rolled-back notification does not carry the gate's cause: {events[1]!r}",
        )
    finally:
        stop_notifications(sc)
        sc.run(f"noust delete {NTF_DOMAIN} -f", timeout=180, check=False, label="cleanup")


# -- 2.3: recipes, export and import -------------------------------------------------

WP_DOMAIN = "wp.test"
WP_ROOT = "/var/www/apps/wp-test"
WP_TITLE = "Noust Integration Blog"

#: A recipe builds from what it downloads; a whole npm install and a Vite build
#: for Uptime Kuma take minutes on a cold cache.
RECIPE_TIMEOUT = 1500

KUMA_DOMAIN = "kuma.test"
KUMA_ROOT = "/var/www/apps/kuma-test"

EXPORT_DOMAIN = "exp.test"
EXPORT_SECRET = "it-export-secret-value-0123456789"


def http_status(sc: Scenario, host: str, path: str, label: str) -> tuple[str, str]:
    """Return the status code and the Location of one request through nginx."""
    out = sc.run(
        f"curl -sS -o /dev/null -w '%{{http_code}} %{{redirect_url}}' -H 'Host: {host}' "
        f"'http://127.0.0.1{path}'",
        timeout=60,
        check=False,
        label=label,
    ).stdout.strip()
    code, _, location = out.partition(" ")
    return code, location


def require_network(sc: Scenario, url: str, what: str) -> None:
    """Skip the scenario, saying why, when the container cannot reach ``url``."""
    probe = sc.run(
        f"curl -sSI --max-time 20 '{url}'",
        timeout=40,
        check=False,
        label=f"curl -sI {url} (is the network there?)",
    )
    if probe.returncode != 0:
        raise ScenarioSkipped(
            f"{url} is unreachable from the container, so {what} cannot be downloaded: "
            f"{(probe.stderr or probe.stdout).strip()}"
        )


def fpm_unit(sc: Scenario) -> str:
    """Name the PHP-FPM unit of the image (php8.3-fpm on Ubuntu 24.04)."""
    return sc.run(
        "basename /usr/lib/systemd/system/php*-fpm.service .service",
        timeout=15,
        label="the PHP-FPM unit",
    ).stdout.strip()


@scenario("recipe_wordpress")
def scenario_recipe_wordpress(sc: Scenario) -> None:
    """
    WordPress from its recipe: PHP-FPM pool, MariaDB, the installer, updates and removal.

    Real php-fpm and MariaDB behind real nginx. The pool carries the
    database settings as env[] lines and is readable by root only; the
    installer runs through nginx; what nginx must refuse is refused, a PHP
    file planted among the uploads included; a plugin installed into shared/
    survives an update to a new release and a rollback; deleting the site
    removes its pool and leaves FPM running for everyone else.
    """
    require_network(sc, "https://wordpress.org/latest.tar.gz.sha1", "WordPress")
    unit = fpm_unit(sc)
    create = f"noust create --recipe wordpress -d {WP_DOMAIN} --no-ssl"
    sc.run(create, timeout=RECIPE_TIMEOUT, label=create)
    try:
        _recipe_wordpress(sc, unit)
    finally:
        journal_tail(sc, f"-u {unit}", f"journalctl -u {unit} (tail)")
        sc.run(f"noust delete {WP_DOMAIN} --force", timeout=180, check=False, label="cleanup")


def _recipe_wordpress(sc: Scenario, unit: str) -> None:
    version = unit.removeprefix("php").removesuffix("-fpm")
    pool = f"/etc/php/{version}/fpm/pool.d/noust-wp-test.conf"
    stat = sc.run(f"stat -c '%a %U' {pool}", timeout=15, label=f"stat {pool}")
    sc.check(stat.stdout.split() == ["640", "root"], f"pool file mode/owner: {stat.stdout!r}")
    env_line = sc.run(
        f"grep -c '^env\\[WORDPRESS_DB_NAME\\]' {pool}",
        timeout=15,
        check=False,
        label=f"grep env[WORDPRESS_DB_NAME] {pool}",
    )
    sc.check(env_line.stdout.strip() == "1", "the pool does not carry env[WORDPRESS_DB_NAME]")

    code, location = http_status(sc, WP_DOMAIN, "/", f"curl -H 'Host: {WP_DOMAIN}' /")
    sc.check(
        code == "302" and location.endswith("/wp-admin/install.php"),
        f"/ before the installation answered {code} {location!r}, not a 302 to install.php",
    )
    code, _ = http_status(
        sc, WP_DOMAIN, "/wp-admin/install.php", "curl -H 'Host: wp.test' /wp-admin/install.php"
    )
    sc.check(code == "200", f"/wp-admin/install.php answered {code}")

    install = sc.run(
        "curl -sS -o /dev/null -w '%{http_code}' -H 'Host: wp.test' "
        f"--data-urlencode 'weblog_title={WP_TITLE}' --data-urlencode 'user_name=itadmin' "
        "--data-urlencode 'admin_password=it-Admin-Passw0rd' "
        "--data-urlencode 'admin_password2=it-Admin-Passw0rd' --data-urlencode 'pw_weak=1' "
        "--data-urlencode 'admin_email=it@example.com' --data-urlencode 'blog_public=0' "
        "'http://127.0.0.1/wp-admin/install.php?step=2'",
        timeout=120,
        label="POST the installation form to /wp-admin/install.php?step=2",
    )
    sc.check(install.stdout.strip() == "200", f"the installation answered {install.stdout!r}")
    home = sc.run(
        "curl -sS -w '\\n%{http_code}' -H 'Host: wp.test' http://127.0.0.1/",
        timeout=60,
        label="curl -H 'Host: wp.test' / (installed)",
    ).stdout
    sc.check(
        home.rstrip().endswith("200") and WP_TITLE in home,
        f"the installed site does not answer 200 with its title: {home[-300:]!r}",
    )

    sc.run(
        f"mkdir -p {WP_ROOT}/shared/wp-content/uploads && "
        f"echo '<?php echo \"planted\";' > {WP_ROOT}/shared/wp-content/uploads/x.php && "
        f"chown www-data: {WP_ROOT}/shared/wp-content/uploads/x.php",
        timeout=15,
        label="plant wp-content/uploads/x.php",
    )
    for path in ("/wp-config.php", "/.env", "/readme.html", "/wp-content/uploads/x.php"):
        code, _ = http_status(sc, WP_DOMAIN, path, f"curl -H 'Host: wp.test' {path}")
        sc.check(code == "403", f"{path} answered {code}, not 403")

    linked = sc.run(
        store_query(
            "SELECT d.engine || ':' || d.name FROM databases d JOIN apps a ON a.id = d.app_id "
            "WHERE a.domain = 'wp.test'"
        ),
        timeout=15,
        label="the databases the store links to wp.test",
    )
    sc.check(linked.stdout.strip().startswith("mysql:"), f"no linked database: {linked.stdout!r}")

    plugin = f"{WP_ROOT}/shared/wp-content/plugins/it-plugin/it-plugin.php"
    sc.run(
        f"mkdir -p $(dirname {plugin}) && "
        f"printf '<?php\\n/* Plugin Name: IT plugin */\\n' > {plugin} && "
        f"chown -R www-data: $(dirname {plugin})",
        timeout=15,
        label="plant a plugin in shared/wp-content/plugins",
    )
    before = sc.run(f"readlink {WP_ROOT}/current", timeout=15, label="readlink current").stdout
    sc.run(f"noust update {WP_DOMAIN}", timeout=RECIPE_TIMEOUT, label=f"noust update {WP_DOMAIN}")
    after = sc.run(
        f"readlink {WP_ROOT}/current", timeout=15, label="readlink current (after update)"
    ).stdout
    sc.check(
        after.strip() != before.strip(), f"the update did not activate a new release: {after!r}"
    )
    survived = sc.run(
        f"test -f {WP_ROOT}/current/wp-content/plugins/it-plugin/it-plugin.php && echo present",
        timeout=15,
        check=False,
        label="the plugin, through the new release",
    )
    sc.check(survived.stdout.strip() == "present", "the plugin did not survive the update")
    code, _ = http_status(sc, WP_DOMAIN, "/", "curl -H 'Host: wp.test' / (after update)")
    sc.check(code == "200", f"/ answered {code} after the update")

    sc.run(
        f"noust releases rollback {WP_DOMAIN}",
        timeout=300,
        label=f"noust releases rollback {WP_DOMAIN}",
    )
    rolled = sc.run(
        f"readlink {WP_ROOT}/current", timeout=15, label="readlink current (after rollback)"
    ).stdout
    sc.check(rolled.strip() == before.strip(), f"rollback activated {rolled!r}, not {before!r}")
    code, _ = http_status(sc, WP_DOMAIN, "/", "curl -H 'Host: wp.test' / (after rollback)")
    sc.check(code == "200", f"/ answered {code} after the rollback")

    sc.run(f"noust delete {WP_DOMAIN} --force", timeout=180, label=f"noust delete {WP_DOMAIN}")
    gone = sc.run(f"test -e {pool} || echo gone", timeout=15, check=False, label=f"test -e {pool}")
    sc.check(gone.stdout.strip() == "gone", f"{pool} is still there after the delete")
    active = sc.run(
        f"systemctl is-active {unit}", timeout=15, check=False, label=f"systemctl is-active {unit}"
    )
    sc.check(active.stdout.strip() == "active", f"{unit} is {active.stdout.strip()!r}")


@scenario("recipe_uptime_kuma")
def scenario_recipe_uptime_kuma(sc: Scenario) -> None:
    """
    Uptime Kuma from its recipe: built from GitHub, on loopback only, its data in shared/.

    Its Socket.IO endpoint answers through nginx, it listens on 127.0.0.1
    and nothing else, its SQLite database is in shared/data, and an update to
    a new release keeps that database.
    """
    require_network(sc, "https://github.com/louislam/uptime-kuma.git", "Uptime Kuma")
    create = f"noust create --recipe uptime-kuma -d {KUMA_DOMAIN} --no-ssl"
    sc.run(create, timeout=RECIPE_TIMEOUT, label=create)
    try:
        code, location = http_status(sc, KUMA_DOMAIN, "/", f"curl -H 'Host: {KUMA_DOMAIN}' /")
        sc.check(code in ("301", "302"), f"/ answered {code} {location!r}, not a redirect")
        polling = curl_host(sc, KUMA_DOMAIN, "/socket.io/?EIO=4&transport=polling")
        sc.check(polling.startswith("0{"), f"Socket.IO polling answered {polling[:200]!r}")

        # The recipe asks for 3001; on a server where another application
        # holds it, a free port is chosen, so the store says which.
        port = sc.run(
            store_query("SELECT port FROM apps WHERE domain = 'kuma.test'"),
            timeout=15,
            label="the port the store records for kuma.test",
        ).stdout.strip()
        listeners = sc.run(
            f"ss -ltnH '( sport = :{port} )' | awk '{{print $4}}'",
            timeout=15,
            label=f"ss -ltn sport = :{port}",
        ).stdout.split()
        sc.check(listeners == [f"127.0.0.1:{port}"], f"port {port} is bound on {listeners!r}")

        db = f"{KUMA_ROOT}/shared/data/kuma.db"
        sc.run(f"test -f {db} && ls -l {db}", timeout=15, label=f"test -f {db}")
        sum_before = sc.run(f"stat -c %i {db}", timeout=15, label=f"inode of {db}").stdout
        sc.run(
            f"noust update {KUMA_DOMAIN}",
            timeout=RECIPE_TIMEOUT,
            label=f"noust update {KUMA_DOMAIN}",
        )
        sum_after = sc.run(
            f"stat -c %i {db}", timeout=15, label=f"inode of {db} (after update)"
        ).stdout
        sc.check(
            sum_before.strip() == sum_after.strip(),
            f"kuma.db was replaced by the update: {sum_before!r} -> {sum_after!r}",
        )
        code, _ = http_status(sc, KUMA_DOMAIN, "/", f"curl -H 'Host: {KUMA_DOMAIN}' / (after)")
        sc.check(code in ("301", "302"), f"/ answered {code} after the update")
    finally:
        journal_tail(sc, "-u kuma-test", "journalctl -u kuma-test (tail)")
        sc.run(f"noust delete {KUMA_DOMAIN} --force", timeout=180, check=False, label="cleanup")


@scenario("app_export_import")
def scenario_app_export_import(sc: Scenario) -> None:
    """
    An application exported and imported under another domain comes back whole.

    The export without secrets leaves the secret's value out, and the import
    needs it given; aliases and cron job names follow the new domain; the
    health check and the retention are applied; what could not be applied
    (the port, which the original still holds) is listed. An export with
    secrets imports with nothing else given.
    """
    _, url = make_node_repo(sc, "exp-app")
    create = (
        f"noust create -d {EXPORT_DOMAIN} -s {url} -t nodejs --no-ssl --layout releases "
        f"--env APP_SECRET={EXPORT_SECRET} --env GREETING=hello"
    )
    sc.run(create, timeout=DEPLOY_TIMEOUT, label=create)
    try:
        _export_import(sc)
    finally:
        for domain in (EXPORT_DOMAIN, "copy.test", "copy2.test"):
            sc.run(f"noust delete {domain} --force", timeout=180, check=False, label="cleanup")
        for job in ("exp-test-nightly", "copy-test-nightly", "copy2-test-nightly"):
            sc.run(f"noust cron delete {job} --force", timeout=60, check=False, label="cleanup")


def _export_import(sc: Scenario) -> None:
    for script in (
        f"noust domain add {EXPORT_DOMAIN} alias.{EXPORT_DOMAIN} --kind alias",
        f"noust cron create exp-test-nightly '/bin/true' --schedule daily --app {EXPORT_DOMAIN}",
        f"noust app health {EXPORT_DOMAIN} --path / --expect 200-399 --timeout 20",
        f"noust releases keep {EXPORT_DOMAIN} 3",
    ):
        sc.run(script, timeout=120, label=script)

    sc.run(
        f"noust app export {EXPORT_DOMAIN} -o /root/x.json",
        timeout=60,
        label=f"noust app export {EXPORT_DOMAIN} -o /root/x.json",
    )
    document = json.loads(sc.run("cat /root/x.json", timeout=15, label="cat /root/x.json").stdout)
    env = document["env"]
    sc.check(
        env.get("APP_SECRET") == {"secret": True, "value": None},
        f"the export without secrets carries APP_SECRET as {env.get('APP_SECRET')!r}",
    )
    sc.check(env.get("GREETING", {}).get("value") == "hello", f"GREETING: {env.get('GREETING')!r}")
    sc.check(EXPORT_SECRET not in json.dumps(document), "the secret's value is in the export")

    refused = sc.run(
        "noust app import /root/x.json --domain copy.test --yes",
        timeout=120,
        check=False,
        label="noust app import /root/x.json --domain copy.test --yes (no value for the secret)",
    )
    sc.check(
        refused.returncode != 0 and "APP_SECRET" in refused.stdout + refused.stderr,
        "the import without the secret's value was not refused naming APP_SECRET",
    )

    # The document creates a cron job, a command it chose: the import asks first.
    unconfirmed = sc.run(
        f"noust app import /root/x.json --domain copy.test --env APP_SECRET={EXPORT_SECRET}",
        timeout=120,
        check=False,
        label="noust app import /root/x.json --domain copy.test --env APP_SECRET=... (no --yes)",
    )
    sc.check(
        unconfirmed.returncode != 0 and "--yes" in unconfirmed.stdout + unconfirmed.stderr,
        "an import that creates cron jobs ran without being confirmed",
    )
    created = sc.run(
        store_query("SELECT COUNT(*) FROM apps WHERE domain = 'copy.test'"),
        timeout=15,
        label="is copy.test in the store (after the unconfirmed import)",
    )
    sc.check(created.stdout.strip() == "0", "the unconfirmed import created copy.test")

    imported = sc.run(
        f"noust app import /root/x.json --domain copy.test --env APP_SECRET={EXPORT_SECRET} --yes",
        timeout=DEPLOY_TIMEOUT,
        label="noust app import /root/x.json --domain copy.test --env APP_SECRET=... --yes",
    )
    printed = imported.stdout + imported.stderr
    sc.check(
        "not applied" in printed and "port" in printed,
        "the import did not print the parts it did not apply (the port the original holds)",
    )
    served = curl_host(sc, "copy.test").strip()
    sc.check(served == "ok 1", f"copy.test served {served!r}")
    sc.check(curl_host(sc, "alias.copy.test").strip() == "ok 1", "alias.copy.test does not serve")

    domains = json.loads(
        sc.run("noust domain list copy.test --json", timeout=30, label="noust domain list").stdout
    )
    kinds = {item["domain"]: item["kind"] for item in domains["items"]}
    sc.check(kinds == {"copy.test": "primary", "alias.copy.test": "alias"}, f"domains: {kinds!r}")
    cron = sc.run(
        "systemctl cat noust-cron-copy-test-nightly.timer",
        timeout=15,
        check=False,
        label="systemctl cat noust-cron-copy-test-nightly.timer",
    )
    sc.check(cron.returncode == 0, "the cron job was not carried over as copy-test-nightly")
    row = sc.run(
        store_query(
            "SELECT health_path, health_expect, health_timeout, keep_releases FROM apps "
            "WHERE domain = 'copy.test'"
        ),
        timeout=15,
        label="health and retention of copy.test in the store",
    ).stdout.strip()
    sc.check(row == "/|200-399|20|3", f"health and retention of copy.test: {row!r}")
    env_file = sc.run(
        "cat /var/www/apps/copy-test/shared/.env",
        timeout=15,
        label="cat /var/www/apps/copy-test/shared/.env",
    ).stdout
    sc.check(f"APP_SECRET={EXPORT_SECRET}" in env_file, "the secret given with --env is missing")

    sc.run(
        f"noust app export {EXPORT_DOMAIN} --with-secrets -o /root/xs.json",
        timeout=60,
        label=f"noust app export {EXPORT_DOMAIN} --with-secrets -o /root/xs.json",
    )
    mode = sc.run("stat -c %a /root/xs.json", timeout=15, label="stat /root/xs.json").stdout
    sc.check(mode.strip() == "600", f"the export with secrets is mode {mode.strip()}")
    sc.run(
        "noust app import /root/xs.json --domain copy2.test --yes",
        timeout=DEPLOY_TIMEOUT,
        label="noust app import /root/xs.json --domain copy2.test --yes",
    )
    sc.check(curl_host(sc, "copy2.test").strip() == "ok 1", "copy2.test does not serve")
    env_file = sc.run(
        "cat /var/www/apps/copy2-test/shared/.env",
        timeout=15,
        label="cat /var/www/apps/copy2-test/shared/.env",
    ).stdout
    sc.check(f"APP_SECRET={EXPORT_SECRET}" in env_file, "the exported secret did not come back")


@dataclass
class UpgradeApp:
    """One application the upgrade rehearsal deploys with WASM 2.3 and re-checks under Noust."""

    label: str
    domain: str
    app_name: str
    root: str
    #: Static sites have no process and therefore no systemd unit
    #: (deployers/static.py: create_service "No service needed").
    has_unit: bool = True

    @property
    def unit(self) -> str:
        return self.app_name


#: The build sandbox scenario: its repository (tests/integration/fixtures/
#: evil-postinstall, whose postinstall attacks the server), its two
#: applications, and what the postinstall tries to leave on the host.
SANDBOX_REPO = "/root/fixtures/evil-postinstall"
SANDBOX_URL = "git://127.0.0.1/evil-postinstall"
SANDBOX_DOMAIN = "evil.test"
SANDBOX_ROOT = "/var/www/apps/evil-test"
SANDBOX_CONTROL_DOMAIN = "evil-root.test"
SANDBOX_CONTROL_ROOT = "/var/www/apps/evil-root-test"
SANDBOX_TRACES = ("/root/pwned", "/etc/cron.d/noust-evil", "/usr/local/bin/noust-evil")

#: What the postinstall must not manage in the sandbox, and does manage without it.
SANDBOX_FORBIDDEN = (
    "write_root",
    "read_config",
    "read_decoy",
    "read_store",
    "read_other_env",
    "write_cron",
    "write_usr_local",
)


def sandbox_report(sc: Scenario, root: str, label: str) -> dict[str, Any]:
    """Read what the hostile postinstall of an application managed."""
    proc = sc.run(f"cat {root}/current/sandbox-report.json", timeout=15, label=label)
    report = json_of(proc, label)
    if not isinstance(report, dict):
        raise AssertionError(f"{label}: not a report: {report!r}")
    return report


def host_traces(sc: Scenario, label: str) -> list[str]:
    """List what the postinstall left on the host, outside any application."""
    proc = sc.run(
        "; ".join(f"test -e {path} && echo {path}" for path in SANDBOX_TRACES) + "; true",
        timeout=15,
        label=label,
    )
    return proc.stdout.split()


@scenario("build_sandbox_blocks_a_hostile_postinstall")
def scenario_build_sandbox(sc: Scenario) -> None:
    """A postinstall that attacks the server fails in the sandbox, and succeeds without it.

    The repository's postinstall tries to write /root/pwned, /etc/cron.d and
    /usr/local/bin, and to read /etc/noust (config.yaml, and a decoy there that
    is world-readable, so only the sandbox can refuse it), the store and
    another application's .env (world-readable too); it leaves a daemon
    behind. A new application builds in the sandbox: every attempt must fail,
    the build itself must succeed, and nothing may survive the build. Then the
    negative control, without which the first half proves nothing: the same
    postinstall, in an application whose sandbox an operator turned off, must
    manage exactly what the sandbox stopped.
    """
    sc.run(
        f"rm -f {' '.join(SANDBOX_TRACES)} && cd {SANDBOX_REPO} && rm -rf .git && "
        f"git init -q -b main && {GIT_IDENTITY} && "
        "npm install --package-lock-only --ignore-scripts && "
        "git add -A && git commit -q -m 'a hostile postinstall'",
        timeout=60,
        label=f"(fixture repo) {SANDBOX_REPO}, served at {SANDBOX_URL}",
    )
    # Readable by everyone: only the sandbox stands between a build and them.
    sc.run(
        "mkdir -p /etc/noust /var/www/apps/decoy-test/shared && "
        "echo decoy > /etc/noust/sandbox-decoy && chmod 644 /etc/noust/sandbox-decoy && "
        "echo OTHER_APP_SECRET=1 > /var/www/apps/decoy-test/shared/.env && "
        "chmod 644 /var/www/apps/decoy-test/shared/.env && "
        "(test -f /etc/noust/config.yaml || noust config show >/dev/null 2>&1 || true)",
        timeout=30,
        label="(decoys) /etc/noust/sandbox-decoy and another application's .env, mode 644",
    )

    selftest = json_of(
        sc.run(
            "noust app sandbox self-test --json",
            timeout=120,
            check=False,
            label="noust app sandbox self-test --json",
        ),
        "self-test",
    )
    sc.check(selftest.get("passed") is True, f"the sandbox does not hold here: {selftest}")

    create = (
        f"NOUST_SECRET_SENTINEL=leaked noust create -d {SANDBOX_DOMAIN} -s {SANDBOX_URL} "
        "-t nodejs --no-ssl --layout releases"
    )
    sc.run(create, timeout=DEPLOY_TIMEOUT, label=create)

    status = json_of(
        sc.run(
            f"noust app sandbox status {SANDBOX_DOMAIN} --json",
            timeout=30,
            label=f"noust app sandbox status {SANDBOX_DOMAIN} --json",
        ),
        "sandbox status",
    )
    sc.check(status.get("mode") == "on", f"a new application is not sandboxed: {status}")

    report = sandbox_report(sc, SANDBOX_ROOT, "what the postinstall managed in the sandbox")
    for action in SANDBOX_FORBIDDEN:
        sc.check(
            report[action]["ok"] is False,
            f"{action} succeeded in the sandbox: {report[action]}",
        )
    sc.check(
        report["write_release"]["ok"] is True, f"the build could not write its release: {report}"
    )
    sc.check(
        report["interfaces"]["ok"] is True,
        f"os.networkInterfaces() failed (AF_NETLINK): {report['interfaces']}",
    )
    sc.check(report["sentinel"] is None, "the build inherited the CLI's environment")
    build_uid = sc.run("id -u noust-build", timeout=15, label="id -u noust-build").stdout.strip()
    sc.check(str(report["uid"]) == build_uid, f"the build ran as uid {report['uid']}")
    sc.check(
        host_traces(sc, "what the sandboxed postinstall left on the host") == [],
        "the sandboxed postinstall left traces on the host",
    )
    survivors = sc.run(
        "pgrep -u noust-build -a || echo none",
        timeout=15,
        label="pgrep -u noust-build (after the build)",
    )
    sc.check(survivors.stdout.strip() == "none", f"a build process survived: {survivors.stdout!r}")
    owner = sc.run(
        f"stat -c %U {SANDBOX_ROOT}/current/built.txt",
        timeout=15,
        label="stat -c %U current/built.txt",
    )
    sc.check(owner.stdout.strip() == "www-data", f"the release is {owner.stdout.strip()}'s")
    page = sc.run(
        f"curl -sS -H 'Host: {SANDBOX_DOMAIN}' http://127.0.0.1/",
        timeout=30,
        label=f"curl -H 'Host: {SANDBOX_DOMAIN}' http://127.0.0.1/",
    )
    sc.check('"write_release"' in page.stdout, f"the application does not serve: {page.stdout!r}")

    # The negative control: the same postinstall, built as root on purpose.
    control = (
        f"noust create -d {SANDBOX_CONTROL_DOMAIN} -s {SANDBOX_URL} -t nodejs --no-ssl "
        "--layout releases"
    )
    sc.run(control, timeout=DEPLOY_TIMEOUT, label=control)
    sc.check(
        host_traces(sc, "traces after the control's first, sandboxed, build") == [],
        "the control's first build was not sandboxed",
    )
    sc.run(
        f"noust app sandbox disable {SANDBOX_CONTROL_DOMAIN} --reason 'negative control' --yes",
        timeout=30,
        label=f"noust app sandbox disable {SANDBOX_CONTROL_DOMAIN} --reason ... --yes",
    )
    # A new lockfile, so the update installs afresh and the postinstall runs.
    commit_to(
        sc,
        SANDBOX_REPO,
        "npm pkg set version=1.0.1 && npm install --package-lock-only --ignore-scripts",
        "a new lockfile",
    )
    update = f"noust update {SANDBOX_CONTROL_DOMAIN}"
    sc.run(update, timeout=DEPLOY_TIMEOUT, label=update)
    as_root = sandbox_report(sc, SANDBOX_CONTROL_ROOT, "what the postinstall managed as root")
    try:
        sc.check(as_root["uid"] == 0, f"the control did not build as root: {as_root}")
        for action in ("write_root", "read_decoy", "read_other_env", "write_cron"):
            sc.check(
                as_root[action]["ok"] is True,
                f"{action} failed without the sandbox too, so the scenario proves nothing: "
                f"{as_root[action]}",
            )
        sc.check(
            "/root/pwned" in host_traces(sc, "what the control left on the host"),
            "/root/pwned was not written without the sandbox",
        )
    finally:
        sc.run(
            f"rm -f {' '.join(SANDBOX_TRACES)}; pkill -f '^sleep 777$' || true",
            timeout=15,
            check=False,
            label="(cleanup) remove the control's traces and its daemon",
        )


#: Run inside the container by the installed Noust: the runner's sandbox
#: against the real systemd, reporting how each command ended, as JSON.
SANDBOX_RUNNER_PROBE = r"""
import json, subprocess
from noust.core.runner import SandboxSpec, SubprocessRunner

runner = SubprocessRunner()
spec = SandboxSpec(user="noust-build", name="it-runner")
out = {}
failed = runner.run(["sh", "-c", "echo no >&2; exit 3"], sandbox=spec, timeout=60)
out["exit"] = [failed.exit_code, failed.sandbox_result, failed.stderr.strip()]
echoed = runner.run(["echo", "$HOME", "${X}", "%h"], sandbox=spec, timeout=60)
out["echo"] = echoed.stdout.strip()
late = runner.stream(["sleep", "120"], on_line=lambda _l: None, sandbox=spec, timeout=3)
state = subprocess.run(
    ["systemctl", "is-active", f"{late.sandbox_unit}.service"], capture_output=True, text=True
)
out["deadline"] = [late.timed_out, late.stderr, state.stdout.strip()]
swap = subprocess.run(["swapon", "--show", "--noheadings"], capture_output=True, text=True)
if swap.stdout.strip():
    out["memory"] = "skipped: swap is on, so a memory limit swaps instead of killing"
else:
    hungry = runner.run(
        ["python3", "-c", "b = bytearray(512 * 1024 * 1024); print(len(b))"],
        sandbox=SandboxSpec(user="noust-build", name="it-oom", memory_max_mb=64),
        timeout=120,
    )
    out["memory"] = [hungry.exit_code, hungry.out_of_memory, hungry.stderr.strip()]
print(json.dumps(out))
"""


@scenario("build_sandbox_says_how_a_command_ended")
def scenario_sandbox_runner(sc: Scenario) -> None:
    """The runner's sandbox, against systemd as PID 1: exit codes, $ and the limits.

    systemd-run exits 1 both for a deadline and for the memory limit; the
    runner learns which from the result marker its unit's ExecStopPost leaves,
    and a deadline stops the unit, which killing systemd-run alone does not.
    """
    probe = sc.run(
        f"/opt/noust/bin/python - <<'PY'\n{SANDBOX_RUNNER_PROBE}\nPY",
        timeout=300,
        label="the runner's sandbox against systemd (exit 3, $HOME, a 3 s deadline, 64M)",
    )
    ended = json_of(probe, "sandbox runner probe")
    sc.check(ended["exit"][:2] == [3, "exit-code"], f"exit 3 came back as {ended['exit']}")
    sc.check(ended["exit"][2] == "no", f"stderr came back as {ended['exit'][2]!r}")
    sc.check(ended["echo"] == "$HOME ${X} %h", f"$ or % were interpreted: {ended['echo']!r}")
    timed_out, why, unit_state = ended["deadline"]
    sc.check(timed_out is True, f"the deadline was not reported: {ended['deadline']}")
    sc.check("deadline" in why, f"the deadline is not named: {why!r}")
    sc.check(unit_state != "active", f"the unit outlived its deadline: {unit_state!r}")
    if isinstance(ended["memory"], list):
        code, oom, words = ended["memory"]
        sc.check(code == 137 and oom is True, f"the memory kill came back as {ended['memory']}")
        sc.check("MemoryMax=64M" in words, f"the memory kill is not named: {words!r}")


def _app_tree_checksums(sc: Scenario, root: str, label: str) -> str:
    """Sorted sha256sum of every regular file under root, node_modules and .git excluded."""
    return sc.run(
        f"find {root} -type f -not -path '*/node_modules/*' -not -path '*/node_modules' "
        "-not -path '*/.git/*' -not -path '*/.git' "
        "-print0 | sort -z | xargs -0 sha256sum",
        timeout=60,
        label=label,
    ).stdout


def _app_tree_ownership(sc: Scenario, root: str, label: str) -> str:
    """user:group:mode for every entry under root, node_modules and .git excluded."""
    return sc.run(
        f"find {root} -not -path '*/node_modules/*' -not -path '*/node_modules' "
        "-not -path '*/.git/*' -not -path '*/.git' "
        "-printf '%u:%g:%m %P\\n' | sort",
        timeout=30,
        label=label,
    ).stdout


def _unified_diff(before: str, after: str, label: str) -> str:
    """A unified diff between two snapshots, or an explicit statement that there is none."""
    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"wasm-2.3/{label}",
            tofile=f"noust-3.0/{label}",
        )
    )
    return diff if diff else f"(no differences: {label})"


def _snapshot_app(sc: Scenario, app: UpgradeApp, when: str) -> dict[str, str]:
    """Capture the nginx site, the unit (if any), ownership and checksums of one app."""
    nginx = sc.run(
        f"cat /etc/nginx/sites-available/{app.domain}",
        timeout=15,
        label=f"[{when}] cat /etc/nginx/sites-available/{app.domain}",
    ).stdout
    unit = ""
    if app.has_unit:
        unit = sc.run(
            f"systemctl cat {app.unit}",
            timeout=15,
            label=f"[{when}] systemctl cat {app.unit}",
        ).stdout
    ownership = _app_tree_ownership(sc, app.root, f"[{when}] ownership of {app.root}")
    checksums = _app_tree_checksums(
        sc, app.root, f"[{when}] sha256sum of {app.root} (node_modules excluded)"
    )
    return {"nginx": nginx, "unit": unit, "ownership": ownership, "checksums": checksums}


def _store_apps_snapshot(sc: Scenario, db: str, label: str) -> str:
    """A dump of every app row's shared columns, from whichever store file holds them now."""
    return sc.run(store_query_at(db, APPS_SNAPSHOT_QUERY), timeout=15, label=label).stdout


def _unit_state(sc: Scenario, unit: str, label: str) -> tuple[str, str]:
    """(is-enabled, is-active) of a unit; "unknown" rather than failing when it is absent."""
    proc = sc.run(
        f"systemctl is-enabled {unit} 2>/dev/null || echo unknown; "
        f"systemctl is-active {unit} 2>/dev/null || echo unknown",
        timeout=15,
        check=False,
        label=label,
    )
    enabled, _, active = proc.stdout.strip().partition("\n")
    return enabled.strip(), active.strip()


def run_upgrade_rehearsal(sc: Scenario, wheel: Path, transitional_wheel: Path) -> None:
    """Deploy with the real, released WASM 2.3, upgrade to Noust, and check every promise.

    Deploys a Node app on the release layout with an upload persisted in
    ``shared/``, a static site, a cron job and a scheduled local backup (run
    once, so a real archive exists), and enables the console as a systemd
    service with an admin API token - all with the real ``wasm-cli==2.3.0``
    from PyPI, the last release published under the WASM name. Before the
    package is ever touched, the backups directory is put into the exact
    on-disk state a migration crashing between renaming it and leaving its
    symlink in place would leave (see the comment where that happens), so
    that the upgrade below both migrates the server normally and resumes
    that one interrupted step in the same run.

    Upgrades the very same venv to Noust the way ``docs/RENAME.md`` tells an
    operator to (see :func:`upgrade_to_noust`), then runs ``noust status`` -
    an operator's first privileged command - and checks, with real commands
    against real nginx, real systemd and the real store: the migration it
    triggers on its own moves every directory, the store and WASM's own
    units without losing a row or leaving an empty store; the applications'
    own units, sites and trees are byte-for-byte unchanged; the admin token
    still authenticates against the console, now running as
    ``noust-web.service``; and running the migration, or the first command,
    again is a no-op. Finally it exercises the ``wasm`` alias one last time,
    against the upgraded install.

    Args:
        sc: Scenario the rehearsal runs its commands and checks through.
        wheel: Path to the ``noust`` wheel built from the working tree, on
            the host.
        transitional_wheel: Path to the transitional ``wasm-cli`` wheel, on
            the host.
    """
    container = sc.container
    apps = [
        UpgradeApp("node", UPGRADE_NODE_DOMAIN, UPGRADE_NODE_APP, UPGRADE_NODE_ROOT),
        UpgradeApp(
            "static", UPGRADE_STATIC_DOMAIN, UPGRADE_STATIC_APP, UPGRADE_STATIC_ROOT, has_unit=False
        ),
    ]
    legacy_cron_timer = f"wasm-cron-{UPGRADE_CRON_NAME}.timer"
    legacy_backup_timer = f"wasm-backup-{UPGRADE_STATIC_APP}.timer"
    noust_cron_timer = f"noust-cron-{UPGRADE_CRON_NAME}.timer"
    noust_backup_timer = f"noust-backup-{UPGRADE_STATIC_APP}.timer"

    # --- Enable the monitor first: on a real server (systemd's own
    # --- StateDirectory=wasm on its unit, not the package) this is what
    # --- gives /var/lib/wasm a real directory before anything touches the
    # --- store; a bare `pip install` here has nothing else that would (see
    # --- core/store.py:_resolve_db_path and rpm/noust.spec's changelog for
    # --- the exact regression this order avoids: the store falling back to
    # --- ~/.local/share/wasm and the migration having nothing at
    # --- /var/lib/wasm to move).

    sc.run("wasm monitor enable", timeout=60, label="[wasm 2.3] wasm monitor enable")
    sc.check(
        sc.run(
            "test -d /var/lib/wasm && echo present",
            timeout=15,
            check=False,
            label="[wasm 2.3] test -d /var/lib/wasm (systemd's StateDirectory=wasm)",
        ).stdout.strip()
        == "present",
        "/var/lib/wasm was not created by enabling the monitor",
    )

    # --- Deploy every application with the real, released WASM 2.3 CLI ------

    sc.run(
        f"wasm create -d {UPGRADE_NODE_DOMAIN} -s /root/fixtures/node-app -t nodejs --no-ssl "
        "--layout releases --persist uploads",
        timeout=DEPLOY_TIMEOUT,
        label=f"[wasm 2.3] wasm create -d {UPGRADE_NODE_DOMAIN} -s /root/fixtures/node-app "
        "-t nodejs --no-ssl --layout releases --persist uploads",
    )
    page = sc.run(
        f"curl -sS -H 'Host: {UPGRADE_NODE_DOMAIN}' http://127.0.0.1/",
        timeout=30,
        label=f"[wasm 2.3] curl -H 'Host: {UPGRADE_NODE_DOMAIN}' http://127.0.0.1/",
    )
    sc.check(page.stdout.strip() == "ok 1", f"expected 'ok 1', got {page.stdout!r}")
    upload = sc.run(
        f"curl -sS -H 'Host: {UPGRADE_NODE_DOMAIN}' --data-binary 'pre-upgrade-upload' "
        "http://127.0.0.1/upload",
        timeout=30,
        label=f"[wasm 2.3] curl -H 'Host: {UPGRADE_NODE_DOMAIN}' --data-binary ... /upload",
    )
    sc.check("uploaded" in upload.stdout, f"upload did not succeed: {upload.stdout!r}")
    shared_upload = sc.run(
        f"cat {UPGRADE_NODE_ROOT}/shared/uploads/upload.txt",
        timeout=15,
        label="[wasm 2.3] cat shared/uploads/upload.txt (the release layout keeps it in shared/)",
    )
    sc.check(
        "pre-upgrade-upload" in shared_upload.stdout,
        "the upload did not land in shared/ on the release layout",
    )

    sc.run(
        f"wasm create -d {UPGRADE_STATIC_DOMAIN} -s /root/fixtures/static-site -t static --no-ssl",
        timeout=DEPLOY_TIMEOUT,
        label=f"[wasm 2.3] wasm create -d {UPGRADE_STATIC_DOMAIN} -s /root/fixtures/static-site "
        "-t static --no-ssl",
    )
    apex = sc.run(
        f"curl -sS -H 'Host: {UPGRADE_STATIC_DOMAIN}' http://127.0.0.1/",
        timeout=30,
        label=f"[wasm 2.3] curl -H 'Host: {UPGRADE_STATIC_DOMAIN}' http://127.0.0.1/",
    )
    sc.check(
        "Noust Integration Static Fixture" in apex.stdout,
        f"the static site did not serve the fixture: {apex.stdout!r}",
    )

    sc.run(
        f"wasm cron create {UPGRADE_CRON_NAME} '/bin/true' --schedule daily "
        f"--app {UPGRADE_NODE_DOMAIN}",
        timeout=60,
        label=f"[wasm 2.3] wasm cron create {UPGRADE_CRON_NAME} '/bin/true' --schedule daily "
        f"--app {UPGRADE_NODE_DOMAIN}",
    )
    sc.run(
        f"wasm backup schedule create {UPGRADE_STATIC_DOMAIN} --schedule daily --retention-count 2",
        timeout=60,
        label=f"[wasm 2.3] wasm backup schedule create {UPGRADE_STATIC_DOMAIN} --schedule daily "
        "--retention-count 2",
    )
    sc.run(
        f"wasm backup run-schedule {UPGRADE_STATIC_DOMAIN}",
        timeout=300,
        label=f"[wasm 2.3] wasm backup run-schedule {UPGRADE_STATIC_DOMAIN} (one real archive, "
        "for the interrupted-migration rehearsal below)",
    )
    old_backup = sc.run(
        f"ls /var/backups/wasm/{UPGRADE_STATIC_APP}/{UPGRADE_STATIC_APP}_*.tar.gz",
        timeout=15,
        label="[wasm 2.3] the local backup archive run-schedule just took",
    ).stdout.strip()
    sc.check(bool(old_backup), "no local backup archive was created")

    sc.run("wasm web enable", timeout=60, label="[wasm 2.3] wasm web enable")
    health: subprocess.CompletedProcess[str] | None = None
    deadline = time.time() + 30
    while time.time() < deadline:
        health = sc.run(
            f"curl -sS -o /dev/null -w '%{{http_code}}' {PANEL_URL}/health",
            timeout=15,
            check=False,
            label=f"[wasm 2.3] curl -o /dev/null -w '%{{http_code}}' {PANEL_URL}/health",
        )
        if health.stdout.strip() == "200":
            break
        time.sleep(1)
    sc.check(
        health is not None and health.stdout.strip() == "200",
        f"the WASM 2.3 console did not answer /health: {health.stdout if health else None!r}",
    )
    issued = sc.run(
        f"wasm token create {UPGRADE_TOKEN_NAME} --scope admin",
        timeout=30,
        label=f"[wasm 2.3] wasm token create {UPGRADE_TOKEN_NAME} --scope admin",
    )
    token_match = re.search(r"Token:\s*(\S+)", issued.stdout)
    sc.check(token_match is not None, "wasm token create printed no token")
    token = token_match.group(1) if token_match else ""
    sc.evidence[-1] = sc.evidence[-1].replace(token, "<token>")
    code, session = api(sc, token, "GET", "/api/auth/session", "[wasm 2.3] GET /api/auth/session")
    sc.check(
        code == 200 and session.get("authenticated") is True,
        f"the token does not authenticate against the WASM 2.3 console: {code} {session!r}",
    )

    # --- Simulate a migration interrupted between renaming a directory and --
    # --- putting its symlink back, before anything is ever migrated ---------
    #
    # Migrator._move_directory (core/migrate_from_wasm.py) builds a pending
    # symlink, renames the legacy directory onto the new name, and only then
    # renames the pending link over the legacy name; a crash in that last
    # step leaves exactly this on disk, and the module promises that running
    # again finishes it - the same promise
    # tests/test_migrate_from_wasm.py::test_a_run_that_crashed_mid_way_is_completed
    # proves against a fake filesystem. Reproducing the on-disk state by hand
    # here, on the backups directory alone, means the very first
    # `noust status` below both migrates the server normally *and* resumes
    # this one interrupted step, in the same run.
    sc.run(
        f"test -d /var/backups/wasm && test -f {old_backup} && "
        "ln -s /var/backups/noust /var/backups/.wasm.noust-migration && "
        "mv /var/backups/wasm /var/backups/noust",
        timeout=15,
        label="simulate a migration interrupted right after /var/backups/wasm was renamed, "
        "before the symlink was put back in its place",
    )

    # --- Snapshot everything before the package is ever touched -------------

    before_snapshots = {app.label: _snapshot_app(sc, app, "wasm-2.3") for app in apps}
    before_apps_rows = _store_apps_snapshot(
        sc, UPGRADE_OLD_DB, "[wasm 2.3] SELECT every app row's shared columns"
    )
    before_app_count = sc.run(
        store_query_at(UPGRADE_OLD_DB, "SELECT COUNT(*) FROM apps"),
        timeout=15,
        label="[wasm 2.3] SELECT COUNT(*) FROM apps",
    ).stdout.strip()
    sc.check(before_app_count == "2", f"expected 2 apps before the upgrade: {before_app_count!r}")

    cron_before = _unit_state(sc, legacy_cron_timer, f"[wasm 2.3] the state of {legacy_cron_timer}")
    backup_before = _unit_state(
        sc, legacy_backup_timer, f"[wasm 2.3] the state of {legacy_backup_timer}"
    )
    web_before = _unit_state(sc, "wasm-web.service", "[wasm 2.3] the state of wasm-web.service")
    monitor_before = _unit_state(
        sc, "wasm-monitor.service", "[wasm 2.3] the state of wasm-monitor.service"
    )
    for what, state in (
        ("cron", cron_before),
        ("backup", backup_before),
        ("console", web_before),
        ("monitor", monitor_before),
    ):
        sc.check(
            state == ("enabled", "active"),
            f"the WASM 2.3 {what} unit is not enabled and active before the upgrade: {state!r}",
        )

    # --- Upgrade: `pip install -U wasm-cli`, the way docs/RENAME.md tells an -
    # --- operator to (see upgrade_to_noust's docstring for the two-step -----
    # --- workaround this same-version pre-release tree needs) ---------------

    upgrade_to_noust(container, wheel, transitional_wheel)

    # --- The first root command: `noust status` migrates on its own ---------

    first_root = sc.run(
        f"noust status {UPGRADE_NODE_DOMAIN}",
        timeout=30,
        label=f"[noust] noust status {UPGRADE_NODE_DOMAIN} (the first root command)",
    )
    sc.check(first_root.returncode == 0, f"noust status failed:\n{first_root.stdout}")
    sc.check(
        "migrating this server from WASM to Noust's names" in first_root.stderr,
        f"the first root command did not announce the migration: {first_root.stderr!r}",
    )
    sc.check(
        "linked /var/backups/wasm to /var/backups/noust" in first_root.stderr,
        "the interrupted backups migration was not resumed as a `finish linking` step: "
        f"{first_root.stderr!r}",
    )

    # --- The store: no empty one was created, every app row survived --------

    after_app_count = sc.run(
        store_query("SELECT COUNT(*) FROM apps"),
        timeout=15,
        label="[noust] SELECT COUNT(*) FROM apps",
    ).stdout.strip()
    sc.check(
        after_app_count == before_app_count,
        f"expected {before_app_count} apps after the upgrade, found {after_app_count}: "
        "an empty store may have been created instead of the migrated one",
    )
    after_apps_rows = _store_apps_snapshot(sc, NOUST_DB, "[noust] SELECT every app row's columns")
    sc.check(
        after_apps_rows == before_apps_rows,
        "app rows changed across the upgrade in a column shared by both:\n"
        f"before:\n{before_apps_rows}\nafter:\n{after_apps_rows}",
    )
    schema = sc.run(
        store_query("SELECT MAX(version) FROM schema_version"),
        timeout=15,
        label="[noust] SELECT MAX(version) FROM schema_version",
    )
    sc.check(
        schema.stdout.strip() == str(SCHEMA_VERSION),
        f"the store did not migrate to schema v{SCHEMA_VERSION}: {schema.stdout!r}",
    )

    # --- /var/lib/wasm and /etc/wasm are symlinks to the new directories -----

    links = sc.run(
        "readlink /var/lib/wasm; readlink /etc/wasm; readlink /var/backups/wasm",
        timeout=15,
        label="[noust] readlink /var/lib/wasm /etc/wasm /var/backups/wasm",
    ).stdout.split()
    sc.check(
        links == ["/var/lib/noust", "/etc/noust", "/var/backups/noust"],
        f"the legacy directories are not links to their Noust names: {links!r}",
    )
    kept_backup = sc.run(
        f"ls /var/backups/noust/{UPGRADE_STATIC_APP}/{UPGRADE_STATIC_APP}_*.tar.gz",
        timeout=15,
        label="[noust] the backup archive, through the migrated directory",
    ).stdout.strip()
    sc.check(bool(kept_backup), "the backup archive did not survive the interrupted migration")

    # --- WASM's own units were replaced by noust-*, enabled and active ------

    web_after = _unit_state(sc, "noust-web.service", "[noust] the state of noust-web.service")
    cron_after = _unit_state(sc, noust_cron_timer, f"[noust] the state of {noust_cron_timer}")
    backup_after = _unit_state(sc, noust_backup_timer, f"[noust] the state of {noust_backup_timer}")
    monitor_after = _unit_state(
        sc, "noust-monitor.service", "[noust] the state of noust-monitor.service"
    )
    for what, state in (
        ("console", web_after),
        ("cron", cron_after),
        ("backup", backup_after),
        ("monitor", monitor_after),
    ):
        sc.check(
            state == ("enabled", "active"),
            f"the noust-* {what} unit is not enabled and active after the upgrade: {state!r}",
        )
    gone = sc.run(
        f"test -e /etc/systemd/system/wasm-web.service && echo present; "
        f"test -e /etc/systemd/system/wasm-monitor.service && echo present; "
        f"test -e /etc/systemd/system/{legacy_cron_timer} && echo present; "
        f"test -e /etc/systemd/system/{legacy_backup_timer} && echo present; "
        "true",
        timeout=15,
        label="[noust] the old wasm-web/wasm-monitor/wasm-cron/wasm-backup unit files",
    ).stdout
    sc.check(gone.strip() == "", f"a legacy unit file was not removed:\n{gone}")

    # --- The applications' own units, sites and trees are untouched ---------

    after_snapshots = {app.label: _snapshot_app(sc, app, "noust-3.0") for app in apps}
    for app in apps:
        before = before_snapshots[app.label]
        after = after_snapshots[app.label]
        for key in ("nginx", "unit", "ownership", "checksums"):
            if key == "unit" and not app.has_unit:
                continue
            diff = _unified_diff(before[key], after[key], f"{app.label}/{key}")
            sc.evidence.append(f"$ diff {app.label}/{key} (wasm-2.3 vs noust-3.0)\n{diff}")
            sc.check(
                not diff.startswith("---"),
                f"{app.label}'s {key} changed across the upgrade:\n{diff}",
            )

    page = sc.run(
        f"curl -sS -H 'Host: {UPGRADE_NODE_DOMAIN}' http://127.0.0.1/",
        timeout=30,
        label=f"[noust] curl -H 'Host: {UPGRADE_NODE_DOMAIN}' http://127.0.0.1/",
    )
    sc.check(page.stdout.strip() == "ok 1", f"the node app stopped serving: {page.stdout!r}")
    apex = sc.run(
        f"curl -sS -H 'Host: {UPGRADE_STATIC_DOMAIN}' http://127.0.0.1/",
        timeout=30,
        label=f"[noust] curl -H 'Host: {UPGRADE_STATIC_DOMAIN}' http://127.0.0.1/",
    )
    sc.check(
        "Noust Integration Static Fixture" in apex.stdout,
        f"the static site stopped serving: {apex.stdout!r}",
    )
    upload_after = sc.run(
        f"cat {UPGRADE_NODE_ROOT}/shared/uploads/upload.txt",
        timeout=15,
        check=False,
        label="[noust] cat shared/uploads/upload.txt",
    )
    sc.check(
        upload_after.returncode == 0 and "pre-upgrade-upload" in upload_after.stdout,
        "the pre-upgrade upload did not survive the migration",
    )

    # --- The token still authenticates, against the console running as ------
    # --- noust-web.service (the migration started it under its new name) ----

    code, session = api(sc, token, "GET", "/api/auth/session", "[noust] GET /api/auth/session")
    sc.check(
        code == 200 and session.get("authenticated") is True,
        f"the pre-upgrade token no longer authenticates: {code} {session!r}",
    )

    # --- Running the migration, or the first command, again is a no-op ------

    again = sc.run(
        "noust migrate-from-wasm --json",
        timeout=30,
        label="[noust] noust migrate-from-wasm --json (run again)",
    )
    report = json.loads(again.stdout)
    sc.check(
        again.returncode == 0 and report.get("steps") == [],
        f"a second `noust migrate-from-wasm` still had something to do: {report!r}",
    )
    second_root = sc.run(
        f"noust status {UPGRADE_NODE_DOMAIN}",
        timeout=30,
        label=f"[noust] noust status {UPGRADE_NODE_DOMAIN} (a second time)",
    )
    sc.check(
        "migrating this server from WASM" not in second_root.stderr,
        f"a fully migrated server announced another migration: {second_root.stderr!r}",
    )

    # --- The `wasm` alias, one last time, against the upgraded install ------

    alias_status = sc.run(
        f"wasm status {UPGRADE_NODE_DOMAIN}",
        timeout=30,
        label=f"[noust] wasm status {UPGRADE_NODE_DOMAIN} (the alias, after the upgrade)",
    )
    sc.check(alias_status.returncode == 0, "the `wasm` alias failed after the upgrade to Noust")

    version_after = sc.run("noust --version", timeout=30, label="[noust] noust --version").stdout
    sc.evidence.append(
        f"NOTE: `noust --version` after the upgrade: {version_after.strip()!r}. This working "
        f"tree's pyproject.toml is still at {UPGRADE_FROM_VERSION} (not yet bumped for the 3.0.0 "
        "release - see scripts/release.py and the Releasing section of CLAUDE.md); "
        "upgrade_to_noust's docstring explains how the upgrade step compensates for the two "
        "packages (the real wasm-cli and this tree's noust) sharing that version number."
    )


def run_upgrade_mode(wheel: Path, transitional_wheel: Path, *, keep: bool) -> int:
    """Run the WASM 2.3 -> Noust 3.0 upgrade rehearsal (:func:`run_upgrade_rehearsal`) end to end.

    Args:
        wheel: Path to the ``noust`` wheel built from the working tree, on the host.
        transitional_wheel: Path to the transitional ``wasm-cli`` wheel, on the host.
        keep: Do not remove the container when done.

    Returns:
        0 if the rehearsal passed, 1 otherwise.
    """
    started_at = time.monotonic()
    name = random_container_name()
    sc = Scenario(container=name)
    failed = False
    error: str | None = None
    setup_error: str | None = None

    try:
        try:
            start_container(name)
            wait_for_systemd(name)
            install_fixtures(name)
            install_wasm_cli(name, UPGRADE_FROM_VERSION)
        except HarnessError as exc:
            setup_error = str(exc)
        else:
            print("\n=== upgrade_wasm_2_3_to_noust_3_0 ===")
            try:
                run_upgrade_rehearsal(sc, wheel, transitional_wheel)
            except AssertionError as exc:
                failed = True
                error = str(exc)
            except HarnessError as exc:
                failed = True
                error = str(exc)
            else:
                print("PASS: upgrade_wasm_2_3_to_noust_3_0")
    finally:
        if sc.evidence:
            print("--- evidence ---")
            print("\n\n".join(sc.evidence))
        if keep:
            print(f"\n[teardown] --keep given, leaving container {name} running")
        else:
            print(f"\n[teardown] removing container {name}")
            remove_container(name)

    elapsed = time.monotonic() - started_at
    if setup_error is not None:
        print(f"\n[setup] FAILED before the rehearsal ran: {setup_error}")
        print(f"\n[summary] 0/0 scenario(s) run, setup failed, {elapsed:.1f}s total")
        return 1
    if failed:
        print(f"FAIL: upgrade_wasm_2_3_to_noust_3_0: {error}")
    print(f"\n[summary] 1 scenario(s) run, {1 if failed else 0} failure(s), {elapsed:.1f}s total")
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# Upgrade rehearsal: Noust 3.0 -> 3.1 (--upgrade-from-3.0; not in the default suite)
# ---------------------------------------------------------------------------

#: The release the 3.1 rehearsal starts from, installed from PyPI.
UPGRADE_30_VERSION = "3.0.0"

#: Its applications: in place (static and Node, the Node one later moved to
#: another branch with `noust update --branch`) and one on releases.
U30_STATIC_DOMAIN = "u30-static.test"
U30_NODE_DOMAIN = "u30-node.test"
U30_NODE_ROOT = "/var/www/apps/u30-node-test"
U30_NODE_REPO_NAME = "u30-node"
U30_REL_DOMAIN = "u30-rel.test"
U30_REL_REPO_NAME = "u30-rel"
U30_DOMAINS = (U30_STATIC_DOMAIN, U30_NODE_DOMAIN, U30_REL_DOMAIN)
U30_BRANCH = "feature"
U30_TOKEN_NAME = "u30-admin-token"

#: The obsolete setting (the 1.x AI monitor's key) and the operator comment
#: the config.yaml carries into the upgrade.
U30_OBSOLETE_KEY_VALUE = "sk-noust-it-obsolete-openai-key"
U30_COMMENT = "# Operator note (noust-it): keep this comment across the 3.1 upgrade"
U30_CONFIG = "/etc/noust/config.yaml"

#: The password of the first account; the harness never prints it.
U30_ADMIN = "u30admin"


def install_noust_release(name: str, version: str) -> None:
    """Install a released Noust from PyPI into /opt/noust, the way install_noust installs a wheel.

    Args:
        name: Container name.
        version: The exact ``noust`` version, e.g. "3.0.0".
    """
    print(f"[setup] creating /opt/noust venv and installing noust=={version} from PyPI")
    docker_exec(name, "python3 -m venv /opt/noust", timeout=60)
    docker_exec(name, "/opt/noust/bin/pip install --quiet --upgrade pip", timeout=120)
    docker_exec(
        name,
        f"/opt/noust/bin/pip install --quiet 'noust[all]=={version}'",
        timeout=PIP_INSTALL_TIMEOUT,
    )
    docker_exec(name, "ln -sf /opt/noust/bin/noust /usr/local/bin/noust", timeout=15)
    docker_exec(name, "ln -sf /opt/noust/bin/wasm /usr/local/bin/wasm", timeout=15)
    result = docker_exec(name, "noust --version", timeout=30)
    print(f"[setup] installed {result.stdout.strip()}")


def upgrade_noust_venv(sc: Scenario, wheel: Path) -> None:
    """Install the working tree's wheel over a released Noust in /opt/noust.

    ``pip install '<wheel>[all]'`` first, for the dependencies 3.1 added
    (cryptography): pip leaves an installed wheel of the same version alone
    ("already installed with the same version as the provided wheel"), and
    this tree may still carry the version it upgrades from. Then the wheel
    itself, forced and without dependencies, so the code really is replaced.
    Checked by importing the package, never by running the CLI: the first
    command is the one that migrates the store, and that belongs to the
    rehearsal.
    """
    sh(["docker", "cp", str(wheel), f"{sc.container}:/tmp/{wheel.name}"], timeout=60)
    sc.run(
        f"/opt/noust/bin/pip install --quiet '/tmp/{wheel.name}[all]'",
        timeout=PIP_INSTALL_TIMEOUT,
        label=f"pip install '/tmp/{wheel.name}[all]' (dependencies 3.1 added)",
    )
    sc.run(
        f"/opt/noust/bin/pip install --quiet --force-reinstall --no-deps '/tmp/{wheel.name}'",
        timeout=PIP_INSTALL_TIMEOUT,
        label=f"pip install --force-reinstall --no-deps /tmp/{wheel.name}",
    )
    installed = sc.run(
        "/opt/noust/bin/python -c 'import noust.core.store as s; print(s.SCHEMA_VERSION)'",
        timeout=30,
        label="the installed package's SCHEMA_VERSION (read without running the CLI)",
    ).stdout.strip()
    sc.check(
        installed == str(SCHEMA_VERSION),
        f"the venv still runs the old code: SCHEMA_VERSION {installed!r}, "
        f"the tree has {SCHEMA_VERSION}",
    )


def api_secret(
    sc: Scenario,
    method: str,
    path: str,
    label: str,
    *,
    bearer: str | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    """Call the console with a secret that never reaches an argv (curl reads its config on stdin).

    Args:
        sc: The scenario.
        method: HTTP method.
        path: Path under the console.
        label: What the evidence calls it.
        bearer: A token for the Authorization header.
        body: A JSON body.

    Returns:
        The status code and the parsed body.
    """

    def quoted(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines = [
        f"url = {quoted(PANEL_URL + path)}",
        f"request = {quoted(method)}",
        "silent",
        "show-error",
        'write-out = "\\n%{http_code}"',
    ]
    if bearer is not None:
        lines.append(f"header = {quoted('Authorization: Bearer ' + bearer)}")
    if body is not None:
        lines.append(f"header = {quoted('Content-Type: application/json')}")
        lines.append(f"data = {quoted(json.dumps(body))}")
    proc = subprocess.run(
        ["docker", "exec", "-i", sc.container, "curl", "-K", "-"],
        input="\n".join(lines) + "\n",
        capture_output=True,
        text=True,
        timeout=30,
    )
    text, _, code = proc.stdout.rpartition("\n")
    try:
        payload: Any = json.loads(text) if text.strip() else None
    except json.JSONDecodeError:
        payload = text
    shown = str(payload)
    for secret in (bearer, (body or {}).get("token"), (body or {}).get("password")):
        if secret:
            shown = shown.replace(str(secret), "<secret>")
    sc.evidence.append(f"$ {label}\n{code} {shown[:3000]}")
    return int(code or 0), payload


def _u30_curl(sc: Scenario, domain: str, label: str) -> str:
    return sc.run(
        f"curl -sS -H 'Host: {domain}' http://127.0.0.1/",
        timeout=30,
        check=False,
        label=f"{label} curl -H 'Host: {domain}' http://127.0.0.1/",
    ).stdout.strip()


def _u30_serving(sc: Scenario, label: str, node_version: str) -> None:
    """Every application serves what it should."""
    static = _u30_curl(sc, U30_STATIC_DOMAIN, label)
    sc.check("Noust Integration Static Fixture" in static, f"{label} static: {static!r}")
    node = _u30_curl(sc, U30_NODE_DOMAIN, label)
    sc.check(node == f"ok {node_version}", f"{label} node: expected ok {node_version}: {node!r}")
    rel = _u30_curl(sc, U30_REL_DOMAIN, label)
    sc.check(rel == "ok 1", f"{label} releases app: {rel!r}")


def _u30_unit(sc: Scenario, unit: str, label: str) -> tuple[str, str, bool]:
    """(is-enabled, is-active, unit file present) of a unit, one line each, never failing."""
    out = (
        sc.run(
            f"echo $(systemctl is-enabled {unit} 2>/dev/null); "
            f"echo $(systemctl is-active {unit} 2>/dev/null); "
            f"test -e /etc/systemd/system/{unit} && echo present || echo absent",
            timeout=15,
            check=False,
            label=label,
        )
        .stdout.strip()
        .splitlines()
    )
    out += [""] * (3 - len(out))
    return out[0].strip(), out[1].strip(), out[2].strip() == "present"


def run_upgrade_30_rehearsal(sc: Scenario, wheel: Path, *, monitor_installed: bool) -> None:
    """Set a server up with the released Noust 3.0.0, upgrade it to this tree, check 3.1's promises.

    What docs/UPGRADING-3.1.md says holds: the store at v12 with a
    ``noust.db.v11-*.bak`` copy beside it, the applications serving and
    listed, the API token and the console's access token still
    authenticating, the first admin account adopting the tokens, ``config
    clean`` removing the obsolete key with the comment kept and a 0600 copy,
    the monitor installed and enabled only where it was installed (and left
    off where 3.0 uninstalled it, with no marker 3.0 ever wrote), a plain
    update of an application moved with ``update --branch`` staying on that
    branch, the audit chain verifying, and ``ens check --json`` answering.

    The package's own configure steps (obs/debian.postinst) are run by hand
    after the pip upgrade, in its order, since that is what reaches most
    servers and pip runs none of them.

    Args:
        sc: The scenario.
        wheel: The working tree's wheel, on the host.
        monitor_installed: Whether 3.0 leaves the monitor installed (True)
            or enabled and then uninstalled (False).
    """
    v30 = "[noust 3.0]"
    v31 = "[noust 3.1]"
    #: Defects that do not stop the rehearsal; any of them fails it at the end.
    findings: list[str] = []

    # --- The monitor first, as run_upgrade_rehearsal does: its StateDirectory
    # --- gives /var/lib/noust a real directory before the store is created.
    sc.run("noust monitor enable", timeout=90, label=f"{v30} noust monitor enable")
    if not monitor_installed:
        # Let it run, as a real 3.0 monitor did, then remove it: 3.0 wrote no
        # marker, so only the journal remembers it.
        time.sleep(3)
        sc.run(
            "journalctl --unit noust-monitor.service --lines 3 --no-pager --output cat",
            timeout=15,
            label=f"{v30} the monitor's journal before it is uninstalled",
        )
        sc.run("noust monitor uninstall -y", timeout=60, label=f"{v30} noust monitor uninstall -y")
        gone = _u30_unit(
            sc, "noust-monitor.service", f"{v30} noust-monitor.service after uninstall"
        )
        sc.check(not gone[2], f"3.0's uninstall left the unit file: {gone!r}")

    # --- config.yaml: the defaults, then an operator comment and the obsolete
    # --- monitor.openai.api_key inside the existing monitor: section.
    sc.run("noust config upgrade", timeout=30, label=f"{v30} noust config upgrade")
    sc.run(
        "python3 - <<'EOF'\n"
        "import pathlib\n"
        f"p = pathlib.Path('{U30_CONFIG}')\n"
        "lines = p.read_text().splitlines() if p.exists() else []\n"
        f"block = ['  openai:', '    api_key: {U30_OBSOLETE_KEY_VALUE}', '    model: gpt-4']\n"
        "out, done = [], False\n"
        "for line in lines:\n"
        "    out.append(line)\n"
        "    if line.rstrip() == 'monitor:' and not done:\n"
        "        out.extend(block)\n"
        "        done = True\n"
        "if not done:\n"
        "    out += ['monitor:'] + block\n"
        f"p.write_text('{U30_COMMENT}\\n' + '\\n'.join(out) + '\\n')\n"
        "EOF",
        timeout=15,
        label=f"{v30} add '{U30_COMMENT}' and monitor.openai.api_key to {U30_CONFIG}",
    )
    sc.run(
        f"grep -n -B1 -A3 'openai' {U30_CONFIG}; head -3 {U30_CONFIG}",
        timeout=15,
        label=f"{v30} {U30_CONFIG} around the obsolete key",
    )
    sc.run("noust config show >/dev/null", timeout=30, label=f"{v30} noust config show (loads)")

    # --- Applications ------------------------------------------------------
    repo, url = make_node_repo(sc, U30_NODE_REPO_NAME)
    commit_to(
        sc, repo, f"git checkout -q -b {U30_BRANCH} && echo {U30_BRANCH}-1 > VERSION", "feature 1"
    )
    sc.run(f"git -C {repo} checkout -q main", timeout=15, label=f"(fixture repo) {repo}: main")
    rel_repo, rel_url = make_node_repo(sc, U30_REL_REPO_NAME)

    sc.run(
        f"noust create -d {U30_STATIC_DOMAIN} -s /root/fixtures/static-site -t static --no-ssl "
        "--layout inplace",
        timeout=DEPLOY_TIMEOUT,
        label=f"{v30} noust create -d {U30_STATIC_DOMAIN} (static, in place)",
    )
    sc.run(
        f"noust create -d {U30_NODE_DOMAIN} -s {url} -t nodejs --no-ssl --layout inplace",
        timeout=DEPLOY_TIMEOUT,
        label=f"{v30} noust create -d {U30_NODE_DOMAIN} -s {url} (node, in place)",
    )
    sc.run(
        f"noust create -d {U30_REL_DOMAIN} -s {rel_url} -t nodejs --no-ssl --layout releases",
        timeout=DEPLOY_TIMEOUT,
        label=f"{v30} noust create -d {U30_REL_DOMAIN} -s {rel_url} (node, releases)",
    )
    switched = sc.run(
        f"noust update {U30_NODE_DOMAIN} --branch {U30_BRANCH}",
        timeout=DEPLOY_TIMEOUT,
        check=False,
        label=f"{v30} noust update {U30_NODE_DOMAIN} --branch {U30_BRANCH}",
    )
    if switched.returncode != 0:
        # An in-place application is a `git clone --depth 1 --branch main`, which
        # is single-branch: `git fetch origin feature` then only fills
        # FETCH_HEAD, never refs/remotes/origin/feature, so the retried
        # `git checkout feature` still finds nothing (source_manager.pull). The
        # finding is kept and the rest is rehearsed on the state an operator
        # reaches by widening the clone's refspec by hand.
        findings.append(
            f"3.0: `noust update {U30_NODE_DOMAIN} --branch {U30_BRANCH}` failed on an in-place "
            f"git app (exit {switched.returncode}): {switched.stdout.strip().splitlines()[-1:]!r}"
        )
        sc.run(
            f"git -C {U30_NODE_ROOT} remote set-branches --add origin {U30_BRANCH}",
            timeout=15,
            label=f"{v30} (workaround) git remote set-branches --add origin {U30_BRANCH}",
        )
        sc.run(
            f"noust update {U30_NODE_DOMAIN} --branch {U30_BRANCH}",
            timeout=DEPLOY_TIMEOUT,
            label=f"{v30} noust update {U30_NODE_DOMAIN} --branch {U30_BRANCH} (after the workaround)",
        )
    branch_30 = sc.run(
        f"git -C {U30_NODE_ROOT} rev-parse --abbrev-ref HEAD",
        timeout=15,
        label=f"{v30} the in-place checkout's branch",
    ).stdout.strip()
    sc.check(branch_30 == U30_BRANCH, f"3.0 did not switch the checkout: {branch_30!r}")
    _u30_serving(sc, v30, f"{U30_BRANCH}-1")

    # --- Console and tokens ------------------------------------------------
    enable = docker_exec(sc.container, "noust web enable", timeout=60, check=False)
    sc.evidence.append(f"$ {v30} noust web enable\n(output withheld: it holds the access token)")
    master_match = re.search(r"Access Token:\s*(\S+)", enable.stdout)
    sc.check(
        enable.returncode == 0 and master_match is not None,
        f"noust web enable printed no access token (exit {enable.returncode}): {enable.stderr}",
    )
    master = master_match.group(1) if master_match else ""
    deadline = time.time() + 30
    while time.time() < deadline:
        probe = docker_exec(
            sc.container,
            f"curl -sS -o /dev/null -w '%{{http_code}}' {PANEL_URL}/health",
            timeout=15,
            check=False,
        )
        if probe.stdout.strip() == "200":
            break
        time.sleep(1)
    issued = docker_exec(
        sc.container, f"noust token create {U30_TOKEN_NAME} --scope admin", timeout=30
    )
    sc.evidence.append(f"$ {v30} noust token create {U30_TOKEN_NAME} --scope admin\n(withheld)")
    token_match = re.search(r"Token:\s*(\S+)", issued.stdout)
    sc.check(token_match is not None, "noust token create printed no token")
    token = token_match.group(1) if token_match else ""

    code, session = api_secret(
        sc, "GET", "/api/auth/session", f"{v30} GET /api/auth/session (API token)", bearer=token
    )
    sc.check(
        code == 200 and isinstance(session, dict) and session.get("authenticated") is True,
        f"the API token does not authenticate on 3.0: {code} {session!r}",
    )
    code, login = api_secret(
        sc,
        "POST",
        "/api/auth/login",
        f"{v30} POST /api/auth/login (access token)",
        body={"token": master},
    )
    sc.check(code == 200, f"the access token does not sign in on 3.0: {code} {login!r}")

    # --- Before ------------------------------------------------------------
    db = sc.run("noust store path", timeout=30, label=f"{v30} noust store path").stdout.strip()
    db = db.splitlines()[-1].strip() if db else ""
    sc.check(db.endswith("noust.db"), f"unexpected store path: {db!r}")
    schema_30 = sc.run(
        store_query_at(db, "SELECT MAX(version) FROM schema_version"),
        timeout=15,
        label=f"{v30} store schema",
    ).stdout.strip()
    list_30 = sc.run("noust list", timeout=30, label=f"{v30} noust list").stdout
    monitor_30 = _u30_unit(sc, "noust-monitor.service", f"{v30} noust-monitor.service")
    sc.check(
        monitor_30[2] == monitor_installed,
        f"the monitor's unit file is not as this variant set it: {monitor_30!r}",
    )
    sc.run(f"ls -l {db}* 2>&1", timeout=15, check=False, label=f"{v30} ls -l the store")

    # --- Upgrade: the wheel over the same venv, then the package's steps ---
    upgrade_noust_venv(sc, wheel)
    for step in (
        'noust config upgrade --reason "package upgrade"',
        'noust config clean --reason "package upgrade"',
    ):
        sc.run(step, timeout=60, label=f"{v31} (postinst) {step}")
    if _u30_unit(sc, "noust-monitor.service", f"{v31} (postinst) is the monitor enabled?")[0] == (
        "enabled"
    ):
        sc.run(
            'noust monitor install --reason "package upgrade" && systemctl daemon-reload && '
            "systemctl try-restart noust-monitor.service",
            timeout=90,
            label=f"{v31} (postinst) noust monitor install; try-restart noust-monitor",
        )
    auto = sc.run(
        'noust monitor autoenable --reason "package upgrade"',
        timeout=90,
        label=f"{v31} (postinst) noust monitor autoenable",
    )
    sc.run(
        "systemctl is-active --quiet noust-web.service && systemctl restart noust-web.service",
        timeout=60,
        label=f"{v31} (postinst) systemctl restart noust-web.service",
    )
    deadline = time.time() + 30
    while time.time() < deadline:
        probe = docker_exec(
            sc.container,
            f"curl -sS -o /dev/null -w '%{{http_code}}' {PANEL_URL}/health",
            timeout=15,
            check=False,
        )
        if probe.stdout.strip() == "200":
            break
        time.sleep(1)

    # --- The store: v12, with the v11 copy beside it -----------------------
    schema_31 = sc.run(
        store_query_at(db, "SELECT MAX(version) FROM schema_version"),
        timeout=15,
        label=f"{v31} store schema",
    ).stdout.strip()
    sc.check(
        schema_31 == str(SCHEMA_VERSION),
        f"the store is at v{schema_31} (was v{schema_30}), expected v{SCHEMA_VERSION}",
    )
    backups = (
        sc.run(
            f"stat -c '%a %s %n' {db}.v11-*.bak",
            timeout=15,
            check=False,
            label=f"{v31} stat {db}.v11-*.bak",
        )
        .stdout.strip()
        .splitlines()
    )
    sc.check(len(backups) == 1, f"expected one v11 copy of the store: {backups!r}")
    if backups:
        mode, _, path = backups[0].partition(" ")
        bak = path.partition(" ")[2]
        sc.check(mode == "600", f"the v11 copy is mode {mode}, expected 600")
        bak_schema = sc.run(
            " ; ".join(
                (
                    store_query_at(bak, "SELECT MAX(version) FROM schema_version"),
                    store_query_at(bak, "SELECT COUNT(*) FROM apps"),
                )
            ),
            timeout=15,
            label=f"{v31} the copy's schema and app count",
        ).stdout.split()
        sc.check(
            bak_schema == [schema_30, "3"],
            f"the copy is not the 3.0 store: {bak_schema!r} (3.0 was v{schema_30})",
        )

    # --- Applications ------------------------------------------------------
    _u30_serving(sc, v31, f"{U30_BRANCH}-1")
    list_31 = sc.run("noust list", timeout=30, label=f"{v31} noust list").stdout
    for domain in U30_DOMAINS:
        sc.check(domain in list_30, f"3.0's noust list did not show {domain}")
        sc.check(domain in list_31, f"noust list after the upgrade does not show {domain}")

    # --- Tokens, before any account ------------------------------------------
    code, session = api_secret(
        sc, "GET", "/api/auth/session", f"{v31} GET /api/auth/session (API token)", bearer=token
    )
    sc.check(
        code == 200 and isinstance(session, dict) and session.get("authenticated") is True,
        f"the 3.0 API token no longer authenticates: {code} {session!r}",
    )
    code, login = api_secret(
        sc,
        "POST",
        "/api/auth/login",
        f"{v31} POST /api/auth/login (access token)",
        body={"token": master},
    )
    sc.check(code == 200, f"the 3.0 access token no longer signs in: {code} {login!r}")
    web_token = sc.run("noust web token", timeout=30, label=f"{v31} noust web token").stdout
    sc.check("issued" in web_token, f"noust web token does not report the token: {web_token!r}")
    listed = json_of(
        sc.run("noust token list --json", timeout=30, label=f"{v31} noust token list --json"),
        "noust token list --json",
    )
    ours = [t for t in listed.get("tokens", []) if t.get("name") == U30_TOKEN_NAME]
    sc.check(
        len(ours) == 1 and not ours[0].get("revoked_at") and ours[0].get("scope") == "admin",
        f"the 3.0 token is not listed live with its scope: {ours!r}",
    )

    # --- The first account adopts the tokens ------------------------------
    password = "Upgrade-" + secrets.token_hex(8)
    created = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            sc.container,
            "noust",
            "user",
            "create",
            U30_ADMIN,
            "--role",
            "admin",
            "--stdin",
        ],
        input=password + "\n",
        capture_output=True,
        text=True,
        timeout=60,
    )
    sc.evidence.append(
        f"$ {v31} noust user create {U30_ADMIN} --role admin --stdin\n{created.stdout.strip()}\n"
        f"{created.stderr.strip()}\n(exit={created.returncode})"
    )
    sc.check(created.returncode == 0, f"noust user create failed: {created.stderr!r}")
    sc.check(
        "adopted 1 API token" in created.stdout.replace("\n", " "),
        f"the first admin did not report adopting the 3.0 token: {created.stdout!r}",
    )
    listed = json_of(
        sc.run("noust token list --json", timeout=30, label=f"{v31} noust token list --json"),
        "noust token list --json",
    )
    ours = [t for t in listed.get("tokens", []) if t.get("name") == U30_TOKEN_NAME]
    sc.check(
        len(ours) == 1 and ours[0].get("owner_account_id") is not None,
        f"the 3.0 token has no owner after the first admin: {ours!r}",
    )
    code, session = api_secret(
        sc,
        "GET",
        "/api/auth/session",
        f"{v31} GET /api/auth/session (adopted API token)",
        bearer=token,
    )
    sc.check(
        code == 200 and isinstance(session, dict) and session.get("authenticated") is True,
        f"the adopted API token no longer authenticates: {code} {session!r}",
    )
    code, apps_payload = api_secret(
        sc, "GET", "/api/apps", f"{v31} GET /api/apps (adopted API token)", bearer=token
    )
    sc.check(code == 200, f"the adopted admin token cannot list apps: {code}")
    code, login = api_secret(
        sc,
        "POST",
        "/api/auth/login",
        f"{v31} POST /api/auth/login (access token, now break-glass)",
        body={"token": master},
    )
    sc.check(code == 200, f"the access token no longer signs in as break-glass: {code} {login!r}")
    sc.run("noust user list", timeout=30, label=f"{v31} noust user list")

    # --- config clean -------------------------------------------------------
    config_after = sc.run(f"cat {U30_CONFIG}", timeout=15, label=f"{v31} cat {U30_CONFIG}").stdout
    sc.check("openai" not in config_after, "config clean left monitor.openai in config.yaml")
    sc.check(U30_OBSOLETE_KEY_VALUE not in config_after, "the OpenAI key is still in config.yaml")
    sc.check(U30_COMMENT in config_after, "config clean dropped the operator's comment")
    copies = (
        sc.run(
            f"stat -c '%a %n' {U30_CONFIG}.bak-*",
            timeout=15,
            check=False,
            label=f"{v31} stat {U30_CONFIG}.bak-*",
        )
        .stdout.strip()
        .splitlines()
    )
    sc.check(len(copies) >= 1, "config clean wrote no dated copy")
    for line in copies:
        mode, _, path = line.partition(" ")
        sc.check(mode == "600", f"{path} is mode {mode}, expected 600")
        leaked = sc.run(
            f"grep -c '{U30_OBSOLETE_KEY_VALUE}' {path} || true",
            timeout=15,
            check=False,
            label=f"{v31} the OpenAI key in {path}?",
        ).stdout.strip()
        sc.check(leaked == "0", f"the OpenAI key was kept in the copy {path}")
    shown = sc.run("noust config show", timeout=30, label=f"{v31} noust config show").stdout
    sc.check("openai" not in shown.lower(), "config show still lists monitor.openai")

    # --- The monitor --------------------------------------------------------
    monitor_31 = _u30_unit(sc, "noust-monitor.service", f"{v31} noust-monitor.service")
    marker = sc.run(
        "ls /var/lib/noust/monitor-declined 2>/dev/null || true",
        timeout=15,
        check=False,
        label=f"{v31} ls /var/lib/noust/monitor-declined",
    ).stdout.strip()
    sc.run("noust monitor status", timeout=30, check=False, label=f"{v31} noust monitor status")
    if monitor_installed:
        sc.check(
            monitor_31[:2] == ("enabled", "active") and monitor_31[2],
            f"the installed monitor is not enabled and active after the upgrade: {monitor_31!r}",
        )
        sc.check(not marker, f"a declined marker was written for an installed monitor: {marker}")
    else:
        sc.check(
            not monitor_31[2] and monitor_31[1] != "active",
            f"the monitor 3.0 uninstalled came back after the upgrade: {monitor_31!r} "
            f"(autoenable said: {auto.stdout.strip()!r})",
        )
        sc.check(bool(marker), "the removed monitor was not remembered as declined")

    # --- A plain update stays on the branch 3.0 switched to ---------------
    commit_to(sc, repo, f"git checkout -q {U30_BRANCH} && echo {U30_BRANCH}-2 > VERSION", "f2")
    commit_to(sc, repo, "git checkout -q main && echo main-2 > VERSION", "main 2")
    pin = json_of(
        sc.run(
            f"noust app branch {U30_NODE_DOMAIN} --json",
            timeout=30,
            label=f"{v31} noust app branch {U30_NODE_DOMAIN} --json",
        ),
        "noust app branch --json",
    )
    sc.check(not pin.get("pinned"), f"the upgrade pinned a branch by itself: {pin!r}")
    sc.run(
        f"noust update {U30_NODE_DOMAIN}",
        timeout=DEPLOY_TIMEOUT,
        label=f"{v31} noust update {U30_NODE_DOMAIN} (no --branch)",
    )
    branch_31 = sc.run(
        f"git -C {U30_NODE_ROOT} rev-parse --abbrev-ref HEAD",
        timeout=15,
        label=f"{v31} the in-place checkout's branch after a plain update",
    ).stdout.strip()
    sc.check(branch_31 == U30_BRANCH, f"a plain update moved the checkout to {branch_31!r}")
    node = _u30_curl(sc, U30_NODE_DOMAIN, v31)
    sc.check(
        node == f"ok {U30_BRANCH}-2", f"the plain update did not deploy {U30_BRANCH}: {node!r}"
    )

    # --- 3.1 on a branch the in-place clone has never seen ----------------
    commit_to(sc, repo, "git checkout -q -b feature2 && echo feature2-1 > VERSION", "feature2")
    sc.run(f"git -C {repo} checkout -q main", timeout=15, label=f"(fixture repo) {repo}: main")
    fresh = sc.run(
        f"noust update {U30_NODE_DOMAIN} --branch feature2",
        timeout=DEPLOY_TIMEOUT,
        check=False,
        label=f"{v31} noust update {U30_NODE_DOMAIN} --branch feature2 (not in the clone's refspec)",
    )
    if fresh.returncode != 0:
        findings.append(
            f"3.1: `noust update {U30_NODE_DOMAIN} --branch feature2` still fails on an in-place "
            f"git app (exit {fresh.returncode}): {fresh.stdout.strip().splitlines()[-1:]!r}"
        )
        still = _u30_curl(sc, U30_NODE_DOMAIN, v31)
        sc.check(still == f"ok {U30_BRANCH}-2", f"the failed switch broke the app: {still!r}")
    else:
        now = _u30_curl(sc, U30_NODE_DOMAIN, v31)
        sc.check(now == "ok feature2-1", f"update --branch feature2 serves {now!r}")

    # --- Audit and ENS ------------------------------------------------------
    sc.run("noust audit status", timeout=30, check=False, label=f"{v31} noust audit status")
    sc.run("noust audit verify", timeout=60, label=f"{v31} noust audit verify")
    ens = sc.run(
        "noust ens check --json", timeout=180, check=False, label=f"{v31} noust ens check --json"
    )
    sc.check(ens.returncode in (0, 1, 2), f"ens check crashed: exit {ens.returncode}")
    sc.check("Traceback" not in ens.stderr, "ens check raised")
    report = json_of(ens, "noust ens check --json")
    sc.check(
        isinstance(report, dict) and bool(report.get("findings")),
        f"ens check --json has no findings: {str(report)[:500]}",
    )
    sc.check(
        not report.get("errors"),
        f"ens check could not read part of the server: {report.get('errors')!r}",
    )
    sc.run("noust --version", timeout=30, label=f"{v31} noust --version")
    sc.check(not findings, "defects found:\n  - " + "\n  - ".join(findings))


def run_upgrade_30_mode(wheel: Path, *, variants: list[bool], keep: bool) -> int:
    """Run :func:`run_upgrade_30_rehearsal` once per monitor variant, each in its own container.

    Args:
        wheel: The working tree's wheel, on the host.
        variants: ``monitor_installed`` values to run.
        keep: Do not remove the containers when done.

    Returns:
        0 when every variant passed, 1 otherwise.
    """
    started_at = time.monotonic()
    failures = 0
    for monitor_installed in variants:
        label = (
            "upgrade_noust_3_0_to_3_1_monitor_installed"
            if monitor_installed
            else "upgrade_noust_3_0_to_3_1_monitor_uninstalled"
        )
        name = random_container_name()
        sc = Scenario(container=name)
        print(f"\n=== {label} ({name}) ===")
        try:
            try:
                start_container(name)
                wait_for_systemd(name)
                install_fixtures(name)
                install_noust_release(name, UPGRADE_30_VERSION)
                run_upgrade_30_rehearsal(sc, wheel, monitor_installed=monitor_installed)
            except (AssertionError, HarnessError) as exc:
                failures += 1
                print(f"FAIL: {label}: {exc}")
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
                remove_container(name)
    elapsed = time.monotonic() - started_at
    print(
        f"\n[summary] {len(variants)} scenario(s) run, {failures} failure(s), {elapsed:.1f}s total"
    )
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def random_container_name() -> str:
    return f"noust-it-{secrets.token_hex(4)}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep", action="store_true", help="Do not remove the container when done."
    )
    parser.add_argument(
        "--scenario",
        action="append",
        default=None,
        metavar="NAME",
        help="Run only scenarios whose name contains this substring. May repeat.",
    )
    parser.add_argument(
        "--skip-wheel-build",
        action="store_true",
        help="Reuse the newest wheel already in the working directory instead of rebuilding.",
    )
    parser.add_argument(
        "--skip-image-build",
        action="store_true",
        help=f"Reuse the existing {IMAGE_TAG} image instead of rebuilding it.",
    )
    parser.add_argument(
        "--upgrade",
        action="store_true",
        help=(
            "Run only the WASM 2.3 -> Noust 3.0 upgrade rehearsal, in its own container, "
            "instead of the regular scenario suite. --scenario is ignored when this is given."
        ),
    )
    parser.add_argument(
        "--upgrade-from-3.0",
        dest="upgrade_from_30",
        action="store_true",
        help=(
            f"Run only the Noust {UPGRADE_30_VERSION} -> working tree upgrade rehearsal, one "
            "container per monitor variant. --scenario is ignored when this is given."
        ),
    )
    parser.add_argument(
        "--monitor-variant",
        choices=("installed", "uninstalled", "both"),
        default="both",
        help="With --upgrade-from-3.0: which state 3.0 leaves the monitor in (default: both).",
    )
    return parser.parse_args()


def selected_scenarios(names: list[str] | None) -> list[tuple[str, ScenarioFn]]:
    if not names:
        return list(SCENARIOS)
    selected = [(name, fn) for name, fn in SCENARIOS if any(pattern in name for pattern in names)]
    if not selected:
        raise HarnessError(f"no scenario matches {names!r}; available: {[n for n, _ in SCENARIOS]}")
    return selected


def main() -> int:
    args = parse_args()
    started_at = time.monotonic()

    workdir = INTEGRATION_DIR / ".build"
    workdir.mkdir(exist_ok=True)

    if args.skip_wheel_build:
        wheels = sorted((workdir / "dist").glob("noust-*.whl"))
        if not wheels:
            raise HarnessError("--skip-wheel-build given but no noust-*.whl wheel exists yet")
        wheel = wheels[-1]
        print(f"[setup] reusing existing wheel {wheel.name}")
    else:
        wheel = build_wheel(workdir)

    if not args.skip_image_build:
        build_image()
    else:
        print(f"[setup] reusing existing image {IMAGE_TAG}")

    if args.upgrade_from_30:
        variants = {"installed": [True], "uninstalled": [False], "both": [True, False]}
        return run_upgrade_30_mode(wheel, variants=variants[args.monitor_variant], keep=args.keep)

    if args.upgrade:
        transitional_wheel = build_transitional_wheel(workdir)
        return run_upgrade_mode(wheel, transitional_wheel, keep=args.keep)

    name = random_container_name()
    failures = 0
    attempted = 0
    skipped = 0
    setup_error: str | None = None

    try:
        try:
            start_container(name)
            wait_for_systemd(name)
            install_noust(name, wheel)
            install_fixtures(name)
            to_run = selected_scenarios(args.scenario)
        except HarnessError as exc:
            setup_error = str(exc)
        else:
            print(f"\n[scenarios] running {len(to_run)} scenario(s): {[n for n, _ in to_run]}")

            for sc_name, fn in to_run:
                print(f"\n=== {sc_name} ===")
                attempted += 1
                sc = Scenario(container=name)
                try:
                    fn(sc)
                except ScenarioSkipped as exc:
                    skipped += 1
                    print(f"SKIP: {sc_name}: {exc}")
                except AssertionError as exc:
                    failures += 1
                    print(f"FAIL: {sc_name}: {exc}")
                except HarnessError as exc:
                    failures += 1
                    print(f"FAIL: {sc_name} (command error): {exc}")
                else:
                    print(f"PASS: {sc_name}")
                finally:
                    if sc.evidence:
                        print("--- evidence ---")
                        print("\n\n".join(sc.evidence))
    finally:
        if args.keep:
            print(f"\n[teardown] --keep given, leaving container {name} running")
        else:
            print(f"\n[teardown] removing container {name}")
            remove_container(name)

    elapsed = time.monotonic() - started_at
    if setup_error is not None:
        print(f"\n[setup] FAILED before any scenario ran: {setup_error}")
        print(f"\n[summary] 0/0 scenario(s) run, setup failed, {elapsed:.1f}s total")
        return 1

    print(
        f"\n[summary] {attempted} scenario(s) run, {failures} failure(s), {skipped} skipped, "
        f"{elapsed:.1f}s total"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
