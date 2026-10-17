#!/usr/bin/env python3
"""
The native language recipes of 3.4, built with the real toolchains (checklist A-40).

The JVM (Maven and Gradle, Java and Kotlin), .NET, Elixir/Mix and Deno deployers were written
and unit-tested against a fake runner: they decide what to run, and a fake runner only ever
agrees. This harness runs them for real. It builds the wheel from the working tree, layers the
toolchains onto ``run.py``'s systemd image (``Dockerfile.toolchains``, installed the way each
recipe's own advice says), starts one privileged container, installs Noust into it, and for each
stack creates an application from a real sample project, built by the recipe in the build
sandbox as ``noust-build``, run as the unit, updated, and put back by an instant rollback.

It is not part of pytest (nothing here is named ``test_*``): it drives Docker and the network
(Maven Central, Gradle's services, nuget.org, hex.pm and JSR). ``run.py`` is loaded as a module
and reused, never edited; the fixtures are ``tests/integration/fixtures/toolchains``.

Usage:
    .venv/bin/python tests/integration/toolchains_run.py
    .venv/bin/python tests/integration/toolchains_run.py --scenario maven
    .venv/bin/python tests/integration/toolchains_run.py --keep --skip-image-build

Everything it creates is named ``<prefix>-...`` (``NOUST_IT_TOOLCHAINS_PREFIX``, default
``noust-it-toolchains``) and carries the label ``noust.test=1``: the image, the container.
Nothing of the host's Docker is shared with the container (no socket, no volume), and the
containers, volumes, networks and images of anything else are never listed, stopped or removed.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

INTEGRATION_DIR = Path(__file__).resolve().parent
REPO_ROOT = INTEGRATION_DIR.parents[1]
TOOLCHAINS_DOCKERFILE = INTEGRATION_DIR / "Dockerfile.toolchains"

if TYPE_CHECKING:
    # The real run.py on disk, for the checker; the module itself is loaded below.
    from run import HarnessError as HarnessError
    from run import Scenario as Scenario
    from run import ScenarioSkipped as ScenarioSkipped


def _load_run() -> Any:
    """
    Load tests/integration/run.py as a module, without touching sys.path.

    Registered in ``sys.modules`` before it runs: its plain dataclasses look their module up
    there while they are built.

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
build_wheel = run.build_wheel
wait_for_systemd = run.wait_for_systemd
install_fixtures = run.install_fixtures
install_noust = run.install_noust
wait_served = run.wait_served
json_of = run.json_of
GIT_IDENTITY = run.GIT_IDENTITY
if not TYPE_CHECKING:
    Scenario = run.Scenario
    HarnessError = run.HarnessError
    ScenarioSkipped = run.ScenarioSkipped

# ---------------------------------------------------------------------------
# Names. A prefix of its own, so that nothing here can be taken for (or removed with)
# anything else on the machine, and one label everything carries.
# ---------------------------------------------------------------------------

PREFIX = os.environ.get("NOUST_IT_TOOLCHAINS_PREFIX", "noust-it-toolchains")
LABEL = "noust.test=1"
BASE_IMAGE_TAG = f"{PREFIX}-base:latest"
IMAGE_TAG = f"{PREFIX}-image:latest"
CONTAINER_NAME = f"{PREFIX}-systemd"
MEMORY_LIMIT = os.environ.get("NOUST_IT_TOOLCHAINS_MEMORY", "8g")

#: Where the fixtures sit in the container (``install_fixtures`` copies the whole folder).
FIXTURES_IN_CONTAINER = "/root/fixtures/toolchains"

#: A first build downloads a toolchain's world (Gradle's distribution, the Kotlin compiler,
#: Spring's dependencies): generous, and the same for every stack.
FIRST_DEPLOY_TIMEOUT = 2400
UPDATE_TIMEOUT = 1200

#: How long a JVM or a BEAM node gets to answer after the unit starts.
SERVED_WITHIN = 120

NOUST_DB = run.NOUST_DB


# ---------------------------------------------------------------------------
# Images and the container
# ---------------------------------------------------------------------------


def build_images(*, rebuild: bool) -> None:
    """
    Build the harness image and the toolchain layer on it, both labelled.

    The first is ``Dockerfile.systemd`` under a tag of this harness (its layers are the ones
    ``run.py`` builds, so a machine that ran it builds this in a moment); the second is
    ``Dockerfile.toolchains`` on top of it.

    Args:
        rebuild: Build even when the toolchain image already exists.
    """
    have = sh(["docker", "image", "inspect", IMAGE_TAG], check=False)
    if have.returncode == 0 and not rebuild:
        print(f"[setup] reusing {IMAGE_TAG}")
        return
    print(f"[setup] building {BASE_IMAGE_TAG}")
    sh(
        [
            "docker",
            "build",
            "--label",
            LABEL,
            "-t",
            BASE_IMAGE_TAG,
            "-f",
            str(run.DOCKERFILE),
            str(INTEGRATION_DIR),
        ],
        timeout=1800,
    )
    print(f"[setup] building {IMAGE_TAG} (the toolchains: a long download the first time)")
    sh(
        [
            "docker",
            "build",
            "--label",
            LABEL,
            "--build-arg",
            f"BASE_IMAGE={BASE_IMAGE_TAG}",
            "-t",
            IMAGE_TAG,
            "-f",
            str(TOOLCHAINS_DOCKERFILE),
            str(INTEGRATION_DIR),
        ],
        timeout=3600,
    )


def start_container(name: str) -> None:
    """Start the privileged, systemd-as-PID-1 container, with nothing of the host mounted."""
    print(f"[setup] starting container {name}")
    sh(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--label",
            LABEL,
            "--privileged",
            "--cgroupns=host",
            # The machine is shared: a JVM or a BEAM build that runs away is stopped here and
            # does not take the Docker VM down with it.
            "--memory",
            MEMORY_LIMIT,
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


def container_running(name: str) -> bool:
    """Whether a container of this name is running."""
    probe = sh(["docker", "inspect", "-f", "{{.State.Running}}", name], check=False)
    return probe.returncode == 0 and probe.stdout.strip() == "true"


def reinstall_noust(name: str, wheel: Path) -> None:
    """
    Put the working tree's wheel over the Noust a kept container already has.

    Args:
        name: The container.
        wheel: The wheel, on the host.
    """
    print(f"[setup] reinstalling {wheel.name} in {name}")
    sh(["docker", "cp", str(wheel), f"{name}:/tmp/{wheel.name}"], timeout=60)
    docker_exec(
        name,
        f"/opt/noust/bin/pip install --quiet --force-reinstall --no-deps '/tmp/{wheel.name}'",
        timeout=run.PIP_INSTALL_TIMEOUT,
    )


def forget_every_application(name: str) -> None:
    """
    Delete the applications an earlier run left in a kept container.

    Their ports and units are the next run's to find free: an application that kept the port a
    new one needs makes a zero-downtime start fall back, and a result that depends on what ran
    before it proves nothing.

    Args:
        name: The container.
    """
    domains = [stack.domain for stack in STACKS.values()] + [MONO_DOMAIN]
    for domain in domains:
        docker_exec(
            name,
            f"noust delete {domain} --force >/dev/null 2>&1; true",
            timeout=300,
            check=False,
        )


def remove_container(name: str) -> None:
    """Remove the container (and the anonymous volumes it made); never raises."""
    subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True, text=True, timeout=120)


def remove_images() -> None:
    """Remove the two images this harness built, by exact tag; never raises."""
    for tag in (IMAGE_TAG, BASE_IMAGE_TAG):
        subprocess.run(["docker", "rmi", tag], capture_output=True, text=True, timeout=120)


# ---------------------------------------------------------------------------
# One stack, end to end
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Stack:
    """
    One sample project and what deploying it must show.

    Attributes:
        key: The fixture's folder and the scenario's name.
        app_type: The type Noust must detect for it.
        served_v1: What the first release answers (a substring of the body).
        served_v2: What the second one answers.
        change_v2: A shell snippet, run in the repository, that makes the second version.
        prepare: A shell snippet run in the repository before the first commit: a wrapper, a
            lock file. Empty for none.
        start_marker: A substring of the unit's ``ExecStart``.
        gitignore: What the repository ignores (build output a ``prepare`` leaves).
        generate: A shell snippet that makes the project in an empty directory, for a sample a
            generator writes (Phoenix) instead of a folder of fixtures.
    """

    key: str
    app_type: str
    served_v1: str
    served_v2: str
    change_v2: str
    start_marker: str
    prepare: str = ""
    gitignore: str = ""
    generate: str = ""

    @property
    def domain(self) -> str:
        """The application's domain."""
        return f"tc-{self.key}.test"

    @property
    def app(self) -> str:
        """The application's name: the domain with dots as dashes."""
        return self.domain.replace(".", "-")

    @property
    def repo(self) -> str:
        """The repository in the container."""
        return f"/root/fixtures/tc-{self.key}"

    @property
    def url(self) -> str:
        """Where git daemon serves it."""
        return f"git://127.0.0.1/tc-{self.key}"

    @property
    def root(self) -> str:
        """The application's directory."""
        return f"/var/www/apps/{self.app}"


#: Phoenix as the generator of the Elixir that Ubuntu 24.04 ships writes it. That Elixir is 1.14.0 on
#: Erlang/OTP 25, and the current generator's project does not compile on it: LiveView 1.0 wants
#: Elixir 1.14.1, Plug 1.15 wants 1.15, Phoenix Template 1.1 wants 1.16 and Cowlib wants OTP 26, each
#: found by a build that stopped inside the dependency. So the sample is the 1.7.10 generator's (LiveView
#: 0.20, Cowboy) with the four dependencies that moved on pinned back, which is what an operator on that
#: distribution has to do too (the failure now says so).
PHOENIX_PINS = (
    '{:plug_cowboy, "~> 2.6.2"},\\n      {:plug, "~> 1.14.2", override: true},\\n'
    '      {:cowboy, "== 2.10.0", override: true},\\n      {:cowlib, "== 2.12.1", override: true},\\n'
    '      {:phoenix_template, "== 1.0.4", override: true}'
)
PHOENIX_GENERATE = (
    "mix local.hex --force >/dev/null && mix local.rebar --force >/dev/null && "
    "mix archive.install hex phx_new 1.7.10 --force >/dev/null && "
    "yes | mix phx.new . --app hello_phx --no-ecto --no-mailer --no-gettext --no-dashboard "
    "--no-install && "
    f"sed -i 's/{{:plug_cowboy, \"~> 2.5\"}}/{PHOENIX_PINS}/' mix.exs && "
    "mix deps.get >/dev/null && mix compile >/dev/null && "
    "printf 'phoenix v1\\n' > lib/hello_phx_web/controllers/page_html/home.html.heex"
)

STACKS: dict[str, Stack] = {
    stack.key: stack
    for stack in (
        # The server's own mvn, no wrapper; a jar whose manifest names the main class.
        Stack(
            key="maven-plain",
            app_type="java",
            served_v1="jvm-maven v1",
            served_v2="jvm-maven v2",
            change_v2="echo v2 > src/main/resources/version.txt",
            start_marker="dist/app.jar",
        ),
        # Spring Boot through the project's own Maven wrapper.
        Stack(
            key="maven-spring",
            app_type="java",
            served_v1="spring-maven v1",
            served_v2="spring-maven v2",
            change_v2="sed -i 's/spring-maven v1/spring-maven v2/' src/main/java/app/Application.java",
            start_marker="dist/app.jar",
            prepare="mvn -B -ntp -q wrapper:wrapper",
            gitignore="target/\n",
        ),
        # Kotlin through the project's Gradle wrapper; the application plugin's start script.
        Stack(
            key="gradle-kotlin",
            app_type="java",
            served_v1="kotlin-gradle v1",
            served_v2="kotlin-gradle v2",
            change_v2='sed -i \'s/VERSION = "v1"/VERSION = "v2"/\' src/main/kotlin/app/Main.kt',
            start_marker="bin/hello-kotlin",
            prepare="gradle wrapper --gradle-version 8.10.2 --no-daemon --console=plain",
            gitignore=".gradle/\nbuild/\n.kotlin/\n",
        ),
        # Ktor's fat jar with the server's own gradle: no wrapper in the repository.
        Stack(
            key="gradle-ktor",
            app_type="java",
            served_v1="ktor-gradle v1",
            served_v2="ktor-gradle v2",
            change_v2="sed -i 's/ktor-gradle v1/ktor-gradle v2/' src/main/kotlin/app/Application.kt",
            start_marker="dist/app.jar",
            gitignore=".gradle/\nbuild/\n.kotlin/\n",
        ),
        Stack(
            key="dotnet-web",
            app_type="dotnet",
            served_v1="dotnet-web v1",
            served_v2="dotnet-web v2",
            change_v2="echo v2 > wwwroot/version.txt",
            start_marker="HelloWeb.dll",
            gitignore="bin/\nobj/\n.publish/\n",
        ),
        Stack(
            key="elixir-plug",
            app_type="elixir",
            served_v1="elixir-plug v1",
            served_v2="elixir-plug v2",
            change_v2='sed -i \'s/@version "v1"/@version "v2"/\' lib/hello/router.ex',
            start_marker="bin/hello start",
            gitignore="_build/\ndeps/\n",
        ),
        # Phoenix as `mix phx.new` writes it, with assets built by the tools it downloads. Its
        # Hex archive and dependencies are the network phase; the home page is replaced by a line
        # that tells the versions apart.
        Stack(
            key="elixir-phoenix",
            app_type="elixir",
            served_v1="phoenix v1",
            served_v2="phoenix v2",
            change_v2="sed -i 's/phoenix v1/phoenix v2/' "
            "lib/hello_phx_web/controllers/page_html/home.html.heex",
            start_marker="bin/hello_phx start",
            generate=PHOENIX_GENERATE,
        ),
        Stack(
            key="deno-app",
            app_type="deno",
            served_v1="deno-app v1",
            served_v2="deno-app v2",
            change_v2="echo v2 > VERSION",
            start_marker="deno task start",
            gitignore=".deno/\n",
        ),
    )
}


def forget(sc: Scenario, stack: Stack) -> None:
    """
    Delete the application of an earlier run in a kept container, so a run starts clean.

    Args:
        sc: The scenario.
        stack: The sample project.
    """
    sc.run(
        f"noust delete {stack.domain} --force >/dev/null 2>&1; rm -rf {stack.root}",
        timeout=300,
        check=False,
        label=f"(clean start) noust delete {stack.domain}",
    )


def make_repository(sc: Scenario, stack: Stack) -> None:
    """
    Turn a fixture into a repository git daemon serves, with the stack's preparation done.

    Args:
        sc: The scenario.
        stack: The sample project.
    """
    ignore = f"printf '%s' '{stack.gitignore}' > .gitignore && " if stack.gitignore else ""
    prepare = f"{stack.prepare} && " if stack.prepare else ""
    source = (
        f"mkdir -p {stack.repo} && cd {stack.repo} && {stack.generate}"
        if stack.generate
        else f"cp -a {FIXTURES_IN_CONTAINER}/{stack.key} {stack.repo} && cd {stack.repo}"
    )
    sc.run(
        f"rm -rf {stack.repo} && {source} && {prepare}{ignore}git init -q -b main && "
        f"{GIT_IDENTITY} && git add -A && git commit -q -m 'version 1'",
        timeout=FIRST_DEPLOY_TIMEOUT,
        label=f"(fixture repo) {stack.repo}: the {stack.key} sample at version 1, "
        f"served at {stack.url}",
    )


def app_column(sc: Scenario, column: str, domain: str) -> str:
    """
    Read one column of an application's row, the domain bound as a parameter.

    Args:
        sc: The scenario.
        column: ``app_type`` or ``layout``.
        domain: The application.

    Returns:
        The value.
    """
    queries = {
        "app_type": "SELECT app_type AS value FROM apps WHERE domain = @domain",
        "layout": "SELECT layout AS value FROM apps WHERE domain = @domain",
    }
    rows = run.store_rows(sc, queries[column], domain=domain, label=f"{column} of {domain}")
    sc.check(bool(rows), f"{domain} is not in the store")
    return str(rows[0]["value"])


def deploy_end_to_end(sc: Scenario, stack: Stack) -> None:
    """
    Create an application from the sample, update it, and go back.

    What this proves for real, in this order: the type is detected from the files; the first
    build runs the recipe's commands in the sandbox, as ``noust-build``, with the build tool the
    server has (or the project's wrapper), and the program it produced is what the unit starts;
    the site answers through nginx; an update builds a second release that answers differently
    and leaves the first on disk; and a rollback makes the first one answer again without
    rebuilding anything.

    Args:
        sc: The scenario.
        stack: The sample project.
    """
    forget(sc, stack)
    make_repository(sc, stack)

    created = sc.run(
        f"noust create -d {stack.domain} -s {stack.url} --no-ssl",
        timeout=FIRST_DEPLOY_TIMEOUT,
        check=False,
        label=f"noust create -d {stack.domain} -s {stack.url} --no-ssl",
    )
    sc.check(
        created.returncode == 0,
        f"noust create failed ({created.returncode}):\n{(created.stdout + created.stderr)[-3000:]}",
    )

    app_type = app_column(sc, "app_type", stack.domain)
    sc.check(app_type == stack.app_type, f"detected as {app_type!r}, not {stack.app_type!r}")
    layout = app_column(sc, "layout", stack.domain)
    sc.check(layout == "releases", f"the layout is {layout!r}, not releases")

    body = wait_served_within(sc, stack.domain, stack.served_v1)
    sc.check(stack.served_v1 in body, f"the site answered {body!r}")

    # A new application is zero-downtime from its first release, so what serves is an instance
    # of the template (<app>@green), not <app>.service.
    serving = sc.run(
        f"systemctl list-units --type=service --state=active --plain --no-legend "
        f"'{stack.app}*' | awk '{{print $1}}'",
        timeout=15,
        label=f"the active units of {stack.app}",
    ).stdout.split()
    sc.check(len(serving) == 1, f"expected one active unit for {stack.app}, found {serving}")
    unit = sc.run(
        f"systemctl show {serving[0]} -p ExecStart -p User -p WorkingDirectory -p ActiveState "
        "--no-pager",
        timeout=15,
        label=f"systemctl show {serving[0]}",
    ).stdout
    sc.check(stack.start_marker in unit, f"the unit does not start {stack.start_marker!r}:\n{unit}")
    sc.check("ActiveState=active" in unit, f"the unit is not active:\n{unit}")
    sc.check(
        f"WorkingDirectory={stack.root}/" in unit,
        f"the unit does not run from the application's releases:\n{unit}",
    )

    # The build's caches belong to the build account: the proof that the install and the build
    # ran as noust-build and not as root.
    cache_owner = sc.run(
        f"stat -c %U /var/cache/noust/build/{stack.app}",
        timeout=15,
        check=False,
        label=f"stat -c %U /var/cache/noust/build/{stack.app}",
    ).stdout.strip()
    sc.check(cache_owner == "noust-build", f"the build cache is {cache_owner!r}'s")

    first = sc.run(
        f"readlink {stack.root}/current", timeout=15, label=f"readlink {stack.root}/current"
    ).stdout.strip()

    sc.run(
        f"cd {stack.repo} && {stack.change_v2} && git add -A && git commit -q -m 'version 2'",
        timeout=30,
        label=f"(fixture repo) version 2: {stack.change_v2}",
    )
    updated = sc.run(
        f"noust update {stack.domain}",
        timeout=UPDATE_TIMEOUT,
        check=False,
        label=f"noust update {stack.domain}",
    )
    sc.check(
        updated.returncode == 0,
        f"noust update failed ({updated.returncode}):\n{(updated.stdout + updated.stderr)[-3000:]}",
    )
    body = wait_served_within(sc, stack.domain, stack.served_v2)
    sc.check(stack.served_v2 in body, f"after the update the site answered {body!r}")
    second = sc.run(
        f"readlink {stack.root}/current", timeout=15, label=f"readlink {stack.root}/current"
    ).stdout.strip()
    sc.check(first != second, f"the update did not switch release: {first!r}")
    kept = sc.run(
        f"ls {stack.root}/releases | wc -l", timeout=15, label=f"ls {stack.root}/releases | wc -l"
    ).stdout.strip()
    sc.check(kept == "2", f"expected two releases on disk, found {kept}")

    rolled = sc.run(
        f"noust releases rollback {stack.domain}",
        timeout=UPDATE_TIMEOUT,
        check=False,
        label=f"noust releases rollback {stack.domain}",
    )
    sc.check(
        rolled.returncode == 0,
        f"the rollback failed ({rolled.returncode}):\n{(rolled.stdout + rolled.stderr)[-3000:]}",
    )
    body = wait_served_within(sc, stack.domain, stack.served_v1)
    sc.check(stack.served_v1 in body, f"after the rollback the site answered {body!r}")
    back = sc.run(
        f"readlink {stack.root}/current", timeout=15, label=f"readlink {stack.root}/current"
    ).stdout.strip()
    sc.check(back == first, f"the rollback is on {back!r}, not {first!r}")


def wait_served_within(sc: Scenario, host: str, expected: str) -> str:
    """
    Wait for the site to answer with ``expected``, as long as a JVM or a BEAM node may take.

    Args:
        sc: The scenario.
        host: The ``Host`` header, which picks the site.
        expected: What the body must contain.

    Returns:
        The body that contained it.
    """
    deadline = time.monotonic() + SERVED_WITHIN
    body = ""
    while time.monotonic() < deadline:
        body = docker_exec(
            sc.container,
            f"curl -sS -m 5 -H 'Host: {host}' http://127.0.0.1/",
            timeout=30,
            check=False,
        ).stdout.strip()
        if expected in body:
            break
        time.sleep(2)
    sc.evidence.append(f"$ (until it answers) curl -H 'Host: {host}' http://127.0.0.1/\n{body}")
    return body


ScenarioFn = Callable[[Scenario], None]


def stack_scenario(key: str) -> ScenarioFn:
    """
    Make the scenario of one stack.

    Args:
        key: The stack's key in :data:`STACKS`.

    Returns:
        A scenario function.
    """

    def scenario(sc: Scenario) -> None:
        deploy_end_to_end(sc, STACKS[key])

    scenario.__doc__ = f"Real {key} project: create, serve, update, instant rollback."
    return scenario


SCENARIOS: list[tuple[str, ScenarioFn]] = [
    (f"{key.replace('-', '_')}_end_to_end", stack_scenario(key)) for key in STACKS
]


# ---------------------------------------------------------------------------
# A monorepo on releases: instant rollback (B-16, A-53)
# ---------------------------------------------------------------------------

MONO_DOMAIN = "mono.test"
MONO_APP = "mono-test"
MONO_ROOT = f"/var/www/apps/{MONO_APP}"
MONO_REPO = "/root/fixtures/tc-monorepo"
MONO_URL = "git://127.0.0.1/tc-monorepo"
MONO_WEB = f"app.{MONO_DOMAIN}"
MONO_API = f"api.{MONO_DOMAIN}"

#: A rollback is a swap and a restart behind the gate, not a build. Generous for a loaded machine,
#: and far below what installing and building two workspaces takes.
MONO_ROLLBACK_SECONDS = 90


def mono_commit(sc: Scenario, change: str, message: str) -> None:
    """Change the monorepo's repository and commit it."""
    sc.run(
        f"cd {MONO_REPO} && {change} && git add -A && git commit -q -m '{message}'",
        timeout=60,
        label=f"(fixture repo) {change}; git commit -m '{message}'",
    )


def mono_serves(sc: Scenario, web: str, api: str) -> None:
    """Wait until both workspaces answer, on their own subdomains, with what is expected."""
    wait_served_within(sc, MONO_WEB, web)
    wait_served_within(sc, MONO_API, api)


def mono_releases(sc: Scenario) -> list[str]:
    """The releases on disk, newest first."""
    listed = sc.run(
        f"ls -1 {MONO_ROOT}/releases | sort -r",
        timeout=15,
        label=f"ls {MONO_ROOT}/releases",
    ).stdout.split()
    return listed


def scenario_monorepo_instant_rollback(sc: Scenario) -> None:
    """
    A monorepo created in place moves onto releases, updates there, and goes back instantly.

    What this proves for real: the migration stops, rewrites and restarts both workspaces' units
    (not the first); an update builds a release in the sandbox and switches both workspaces to it,
    carrying each one's environment file; `noust releases rollback` is a swap and a restart, with
    no build; and an update whose api does not start is refused behind the health gate with
    what served still serving, both workspaces.
    """
    sc.run(
        f"noust delete {MONO_DOMAIN} --force >/dev/null 2>&1; rm -rf {MONO_ROOT}",
        timeout=300,
        check=False,
        label=f"(clean start) noust delete {MONO_DOMAIN}",
    )
    sc.run(
        f"rm -rf {MONO_REPO} && cp -a {FIXTURES_IN_CONTAINER}/monorepo {MONO_REPO} && "
        f"cd {MONO_REPO} && printf 'built.txt\\nnode_modules/\\n' > .gitignore && "
        f"pnpm install --lockfile-only >/dev/null && git init -q -b main && {GIT_IDENTITY} && "
        "git add -A && git commit -q -m 'version 1'",
        timeout=300,
        label=f"(fixture repo) {MONO_REPO}: two workspaces at version 1, served at {MONO_URL}",
    )

    created = sc.run(
        f"noust create -d {MONO_DOMAIN} -s {MONO_URL} -t monorepo --no-ssl --no-database",
        timeout=900,
        check=False,
        label=f"noust create -d {MONO_DOMAIN} -t monorepo",
    )
    sc.check(
        created.returncode == 0, f"create failed:\n{(created.stdout + created.stderr)[-3000:]}"
    )
    layout = app_column(sc, "layout", MONO_DOMAIN)
    sc.check(layout == "inplace", f"a new monorepo is created in place, not {layout!r}")

    # What the operator set in a workspace's environment file, which the unit loads.
    sc.run(
        f"echo 'WS_SECRET=carried-1' >> {MONO_ROOT}/apps/web/.env.production && "
        f"systemctl restart {MONO_APP}-web",
        timeout=60,
        label="(operator) WS_SECRET=carried-1 in apps/web/.env.production; restart the web unit",
    )
    mono_serves(sc, "web v1 built v1 secret carried-1", "api v1 built v1")

    migrated = sc.run(
        f"noust app migrate {MONO_DOMAIN} --yes",
        timeout=600,
        check=False,
        label=f"noust app migrate {MONO_DOMAIN} --yes",
    )
    sc.check(
        migrated.returncode == 0,
        f"the migration failed:\n{(migrated.stdout + migrated.stderr)[-3000:]}",
    )
    sc.check(app_column(sc, "layout", MONO_DOMAIN) == "releases", "the layout is not releases")
    mono_serves(sc, "web v1 built v1 secret carried-1", "api v1 built v1")
    first = sc.run(
        f"readlink {MONO_ROOT}/current", timeout=15, label=f"readlink {MONO_ROOT}/current"
    ).stdout.strip()
    for workspace in ("web", "api"):
        unit = sc.run(
            f"systemctl show {MONO_APP}-{workspace}.service -p WorkingDirectory -p ActiveState",
            timeout=15,
            label=f"systemctl show {MONO_APP}-{workspace}.service",
        ).stdout
        sc.check(
            f"WorkingDirectory={MONO_ROOT}/current/apps/{workspace}" in unit,
            f"the {workspace} unit was not rewritten to run from current:\n{unit}",
        )
        sc.check("ActiveState=active" in unit, f"the {workspace} unit is not active:\n{unit}")

    # An update builds a release of its own and switches both workspaces to it.
    mono_commit(sc, "echo v2 > apps/web/VERSION && echo v2 > apps/api/VERSION", "version 2")
    updated = sc.run(
        f"noust update {MONO_DOMAIN}",
        timeout=900,
        check=False,
        label=f"noust update {MONO_DOMAIN}",
    )
    sc.check(
        updated.returncode == 0,
        f"the update failed:\n{(updated.stdout + updated.stderr)[-3000:]}",
    )
    # v2 was built and the environment file of the web workspace came along by copy.
    mono_serves(sc, "web v2 built v2 secret carried-1", "api v2 built v2")
    second = sc.run(
        f"readlink {MONO_ROOT}/current", timeout=15, label=f"readlink {MONO_ROOT}/current"
    ).stdout.strip()
    sc.check(first != second, "the update did not switch release")
    sc.check(len(mono_releases(sc)) == 2, f"expected two releases: {mono_releases(sc)}")
    owner = sc.run(
        f"stat -c %U {MONO_ROOT}/current/apps/web/built.txt",
        timeout=15,
        label="stat -c %U current/apps/web/built.txt",
    ).stdout.strip()
    sc.check(owner != "root", f"the release is {owner}'s: it was not handed to the units' account")

    # Going back is a swap: no install, no build, both workspaces restarted on the old release.
    started = time.monotonic()
    rolled = sc.run(
        f"noust releases rollback {MONO_DOMAIN}",
        timeout=MONO_ROLLBACK_SECONDS * 2,
        check=False,
        label=f"noust releases rollback {MONO_DOMAIN}",
    )
    took = time.monotonic() - started
    sc.check(
        rolled.returncode == 0,
        f"the rollback failed:\n{(rolled.stdout + rolled.stderr)[-3000:]}",
    )
    sc.check(took < MONO_ROLLBACK_SECONDS, f"the rollback took {took:.0f}s: it rebuilt something")
    mono_serves(sc, "web v1 built v1 secret carried-1", "api v1 built v1")
    back = sc.run(
        f"readlink {MONO_ROOT}/current", timeout=15, label=f"readlink {MONO_ROOT}/current"
    ).stdout.strip()
    sc.check(back == first, f"the rollback is on {back!r}, not {first!r}")
    sc.check(len(mono_releases(sc)) == 2, "going back created or removed a release")

    # An update whose api does not start never takes the traffic: both workspaces keep serving.
    mono_commit(sc, "echo v3 > apps/web/VERSION && echo crash > apps/api/VERSION", "api crashes")
    refused = sc.run(
        f"noust update {MONO_DOMAIN}",
        timeout=900,
        check=False,
        label=f"noust update {MONO_DOMAIN}  (the api of this release does not start)",
    )
    output = refused.stdout + refused.stderr
    sc.check(refused.returncode != 0, "an update that does not start was accepted")
    sc.check("health check" in output, f"the refusal does not say why:\n{output[-2000:]}")
    sc.check("refusing to start a release marked crash" in output, "the api's own words are lost")
    mono_serves(sc, "web v1 built v1 secret carried-1", "api v1 built v1")
    kept = sc.run(
        f"readlink {MONO_ROOT}/current", timeout=15, label=f"readlink {MONO_ROOT}/current"
    ).stdout.strip()
    sc.check(kept == first, f"the failed release took the link: {kept!r}")
    sc.check(
        len(mono_releases(sc)) == 2, f"the failed release was left on disk: {mono_releases(sc)}"
    )


SCENARIOS.append(("monorepo_instant_rollback", scenario_monorepo_instant_rollback))


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--keep", action="store_true", help="Leave the container running.")
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
        help="Reuse the newest wheel already built instead of building one.",
    )
    parser.add_argument(
        "--skip-image-build",
        action="store_true",
        help="Reuse the toolchain image when it exists.",
    )
    parser.add_argument(
        "--reuse",
        action="store_true",
        help="Use the container a --keep run left (and reinstall the wheel in it): the "
        "toolchain caches stay warm, which is most of a build's time.",
    )
    parser.add_argument(
        "--remove-images",
        action="store_true",
        help="Remove the two images this harness built once the run ends.",
    )
    return parser.parse_args()


def selected_scenarios(names: list[str] | None) -> list[tuple[str, ScenarioFn]]:
    if not names:
        return list(SCENARIOS)
    selected = [(n, fn) for n, fn in SCENARIOS if any(pattern in n for pattern in names)]
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
            raise HarnessError("--skip-wheel-build given but no wheel exists yet")
        wheel = wheels[-1]
        print(f"[setup] reusing existing wheel {wheel.name}")
    else:
        wheel = build_wheel(workdir)
    build_images(rebuild=not args.skip_image_build)

    failures = 0
    attempted = 0
    skipped: list[tuple[str, str]] = []
    setup_error: str | None = None
    reusing = args.reuse and container_running(CONTAINER_NAME)
    if not reusing:
        remove_container(CONTAINER_NAME)
    try:
        try:
            if reusing:
                reinstall_noust(CONTAINER_NAME, wheel)
                forget_every_application(CONTAINER_NAME)
                docker_exec(CONTAINER_NAME, "rm -rf /root/fixtures/toolchains", timeout=30)
                sh(
                    [
                        "docker",
                        "cp",
                        str(run.FIXTURES_DIR / "toolchains"),
                        f"{CONTAINER_NAME}:/root/fixtures/toolchains",
                    ],
                    timeout=60,
                )
            else:
                start_container(CONTAINER_NAME)
                wait_for_systemd(CONTAINER_NAME)
                install_noust(CONTAINER_NAME, wheel)
                install_fixtures(CONTAINER_NAME)
            to_run = selected_scenarios(args.scenario)
        except HarnessError as exc:
            setup_error = str(exc)
        else:
            print(f"\n[scenarios] running {len(to_run)} scenario(s): {[n for n, _ in to_run]}")
            for name, fn in to_run:
                print(f"\n=== {name} ===")
                attempted += 1
                sc = Scenario(container=CONTAINER_NAME)
                try:
                    fn(sc)
                except ScenarioSkipped as exc:
                    skipped.append((name, str(exc)))
                    print(f"SKIP: {name}: {exc}")
                except AssertionError as exc:
                    failures += 1
                    print(f"FAIL: {name}: {exc}")
                except (HarnessError, subprocess.TimeoutExpired) as exc:
                    failures += 1
                    print(f"FAIL: {name} (command error): {exc}")
                else:
                    print(f"PASS: {name}")
                finally:
                    if sc.evidence:
                        print("--- evidence ---")
                        print("\n\n".join(sc.evidence))
    finally:
        if args.keep:
            print(f"\n[teardown] --keep given, leaving {CONTAINER_NAME} running")
        else:
            print(f"\n[teardown] removing {CONTAINER_NAME}")
            remove_container(CONTAINER_NAME)
        if args.remove_images:
            remove_images()

    elapsed = time.monotonic() - started_at
    if setup_error is not None:
        print(f"\n[setup] FAILED before any scenario ran: {setup_error}")
        return 1
    for name, reason in skipped:
        print(f"[skipped] {name}: {reason}")
    print(
        f"\n[summary] {attempted} scenario(s) run, {failures} failure(s), {len(skipped)} skipped, "
        f"{elapsed:.1f}s total"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
