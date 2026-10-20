#!/usr/bin/env python3
"""
Every application of ``examples/`` deployed for real (checklist N-20).

``examples/`` holds a minimal application for each type Noust deploys, and a folder that says "this
deploys" is worth what has been run. This harness builds the wheel from the working tree, builds an
image of its own (``Dockerfile.systemd``, then the toolchains, then ``Dockerfile.examples``: Go, Rust,
Ruby and Docker on top of Java, .NET, Elixir and Deno), starts one privileged container with systemd
as PID 1 and a Docker of its own, installs Noust into it, and for each example:

1. turns the folder into a git repository served by ``git daemon``, as a person's repository would be;
2. creates an application from it (``noust create -d ex-NAME.test -s git://... --no-ssl``), and checks
   that the type Noust stored is the type the folder is for;
3. asks the web server for ``/`` and looks for ``Hello from Noust (NAME)``;
4. changes the sentence, commits, runs ``noust update`` and looks for the new one, and, for the types
   that build releases, goes back with ``noust releases rollback`` and looks for the first again.

The first example is also deployed from a plain directory, the form ``examples/README.md`` shows.

Three more stacks are not folders of ``examples/`` because a generator makes them (Rails, Phoenix and
Jekyll): the container runs the generator the framework guide names, commits what it wrote and
deploys that, so what ``docs/frameworks.md`` says about them is something that was run.

It is not part of pytest (nothing here is named ``test_*``): it drives Docker and the network (npm,
PyPI, crates.io, Go's and Maven's mirrors, nuget.org, hex.pm, JSR, Docker Hub). ``run.py`` is loaded as a
module and reused, never edited. Everything it creates is named ``<prefix>-...`` (``NOUST_IT_PREFIX``)
and carries the label ``noust.test=1``; nothing of the host's Docker is shared with the container, and
what belongs to anything else is never listed, stopped or removed.

Usage:
    .venv/bin/python tests/integration/examples_run.py
    .venv/bin/python tests/integration/examples_run.py --example go --example rust
    .venv/bin/python tests/integration/examples_run.py --keep --skip-image-build
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

INTEGRATION_DIR = Path(__file__).resolve().parent
REPO_ROOT = INTEGRATION_DIR.parents[1]
EXAMPLES_DIR = REPO_ROOT / "examples"
TOOLCHAINS_DOCKERFILE = INTEGRATION_DIR / "Dockerfile.toolchains"
EXAMPLES_DOCKERFILE = INTEGRATION_DIR / "Dockerfile.examples"

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
build_wheel = run.build_wheel
wait_for_systemd = run.wait_for_systemd
install_noust = run.install_noust
GIT_IDENTITY = run.GIT_IDENTITY
if not TYPE_CHECKING:
    Scenario = run.Scenario
    HarnessError = run.HarnessError
    ScenarioSkipped = run.ScenarioSkipped

# Names: the prefix and the label of run.py, so what a run makes can be found and removed by both.
PREFIX = run.RESOURCE_PREFIX
LABEL = run.RESOURCE_LABEL
BASE_TAG = f"{PREFIX}-ex-base:latest"
TOOLCHAINS_TAG = f"{PREFIX}-ex-toolchains:latest"
IMAGE_TAG = run.IMAGE_TAG
CONTAINER_NAME = f"{PREFIX}-examples"

#: Where the examples sit in the container, and where their repositories are served from.
EXAMPLES_IN_CONTAINER = "/root/examples"
REPOS_IN_CONTAINER = "/root/ex-repos"

#: A first build downloads the world of its toolchain; a framework's is a thousand packages.
FIRST_DEPLOY_TIMEOUT = 2400
UPDATE_TIMEOUT = 1500
SERVED_WITHIN = 180
GENERATE_TIMEOUT = 1800


# ---------------------------------------------------------------------------
# Images and the container
# ---------------------------------------------------------------------------


def build_images(*, rebuild: bool) -> None:
    """
    Build the three images this harness stands on, each labelled.

    Args:
        rebuild: Build even when the final image exists.
    """
    have = sh(["docker", "image", "inspect", IMAGE_TAG], check=False)
    if have.returncode == 0 and not rebuild:
        print(f"[setup] reusing {IMAGE_TAG}")
        return
    print(f"[setup] building {BASE_TAG}")
    sh(
        [
            "docker",
            "build",
            "--label",
            LABEL,
            "-t",
            BASE_TAG,
            "-f",
            str(run.DOCKERFILE),
            str(INTEGRATION_DIR),
        ],
        timeout=1800,
    )
    for tag, parent, dockerfile, what in (
        (TOOLCHAINS_TAG, BASE_TAG, TOOLCHAINS_DOCKERFILE, "the toolchains (a long download)"),
        (IMAGE_TAG, TOOLCHAINS_TAG, EXAMPLES_DOCKERFILE, "Go, Rust, Ruby and Docker"),
    ):
        print(f"[setup] building {tag}: {what}")
        sh(
            [
                "docker",
                "build",
                "--label",
                LABEL,
                "--build-arg",
                f"BASE_IMAGE={parent}",
                "-t",
                tag,
                "-f",
                str(dockerfile),
                str(INTEGRATION_DIR),
            ],
            timeout=3600,
        )


def remove_images() -> None:
    """Remove the three images by exact tag; never raises."""
    for tag in (IMAGE_TAG, TOOLCHAINS_TAG, BASE_TAG):
        subprocess.run(["docker", "rmi", tag], capture_output=True, text=True, timeout=120)


def container_running(name: str) -> bool:
    """Whether a container of this name is running."""
    probe = sh(["docker", "inspect", "-f", "{{.State.Running}}", name], check=False)
    return bool(probe.returncode == 0 and probe.stdout.strip() == "true")


def install_examples(name: str) -> None:
    """
    Copy the examples into the container and serve each as a git repository.

    A repository per example, served by ``git daemon`` over ``git://`` on the loopback address (Noust
    refuses ``file://`` on purpose), as the repository of a person who clones this one would be.

    Args:
        name: The container.
    """
    print("[setup] copying examples/ into the container")
    docker_exec(name, f"rm -rf {EXAMPLES_IN_CONTAINER} {REPOS_IN_CONTAINER}", timeout=60)
    sh(["docker", "cp", str(EXAMPLES_DIR), f"{name}:{EXAMPLES_IN_CONTAINER}"], timeout=120)
    docker_exec(name, f"chown -R root:root {EXAMPLES_IN_CONTAINER}", timeout=60)
    docker_exec(name, "git config --global --add safe.directory '*'", timeout=15)
    docker_exec(
        name,
        f"mkdir -p {REPOS_IN_CONTAINER} && "
        "git daemon --reuseaddr --export-all "
        f"--base-path={REPOS_IN_CONTAINER} --listen=127.0.0.1 --detach {REPOS_IN_CONTAINER}",
        timeout=30,
    )


# ---------------------------------------------------------------------------
# One example, end to end
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Example:
    """
    One folder of ``examples/`` and what deploying it must show.

    Attributes:
        name: The folder, and the sentence's parenthesis.
        app_type: The type Noust must store for it.
        create: Extra arguments for ``noust create``.
        hosts: The names the application answers on, with the sentence each must carry; empty
            for one name, ``ex-NAME.test``, and the example's sentence.
        releases: Whether the type builds releases, and so goes back with a rollback.
        docker: Whether the example runs containers, and so needs the inner Docker daemon.
        generate: For a stack made by a generator, the shell script that writes the project into the
            current directory (the folder of ``examples/`` is then not used).
    """

    name: str
    app_type: str
    create: str = ""
    hosts: tuple[tuple[str, str], ...] = ()
    releases: bool = True
    docker: bool = False
    generate: str = ""

    @property
    def domain(self) -> str:
        """The application's domain."""
        return f"ex-{self.name}.test"

    @property
    def app(self) -> str:
        """The application's name: the domain with dots as dashes."""
        return self.domain.replace(".", "-")

    @property
    def root(self) -> str:
        """The application's directory."""
        return f"/var/www/apps/{self.app}"

    @property
    def repo(self) -> str:
        """The repository in the container."""
        return f"{REPOS_IN_CONTAINER}/{self.name}"

    @property
    def url(self) -> str:
        """Where git daemon serves it."""
        return f"git://127.0.0.1/{self.name}"

    def expected(self, version: int) -> list[tuple[str, str]]:
        """
        What the application must answer.

        Args:
            version: 1 for the sentence as the folder has it, 2 after the update.

        Returns:
            ``(host, substring of the body)`` pairs.
        """
        greeting = "Hello from Noust" if version == 1 else "Hello again from Noust"
        if self.hosts:
            return [
                (host, sentence.replace("Hello from Noust", greeting))
                for host, sentence in self.hosts
            ]
        return [(self.domain, f"{greeting} ({self.name}")]


RAILS = """
gem install --no-document rails -v '~> 8.0'
rails new . --api --minimal --skip-active-record --skip-git --skip-test --skip-bundle \\
  --skip-docker --skip-kamal --skip-thruster --skip-solid --skip-ci --skip-brakeman --skip-rubocop --force
cat > config/routes.rb <<'RUBY'
Rails.application.routes.draw do
  root to: proc { [200, { "Content-Type" => "text/plain" }, ["Hello from Noust (rails)\\n"]] }
end
RUBY
bundle install
"""

PHOENIX = """
mix local.hex --force
mix local.rebar --force
mix archive.install hex phx_new 1.7.21 --force
mix phx.new . --app hello --no-ecto --no-mailer --no-gettext --no-dashboard --install
printf '<p>Hello from Noust (phoenix)</p>\\n' > lib/hello_web/controllers/page_html/home.html.heex
"""

JEKYLL = """
gem install --no-document jekyll bundler
jekyll new . --force --skip-bundle
printf -- '---\\nlayout: default\\n---\\nHello from Noust (jekyll)\\n' > index.markdown
rm -f index.md
sed -i '/^.bundle\\//d' .gitignore
mkdir -p .bundle
printf 'BUNDLE_PATH: "vendor/bundle"\\n' > .bundle/config
printf 'build:\\n  install: bundle install\\n  build: bundle exec jekyll build\\n  publish_directory: _site\\n' > noust.yaml
bundle lock
"""

EXAMPLES: dict[str, Example] = {
    example.name: example
    for example in (
        Example("static", "static"),
        Example("nodejs", "nodejs"),
        Example("python", "python"),
        Example("php", "php-fpm"),
        Example("ruby", "ruby"),
        Example("go", "go"),
        Example("rust", "rust"),
        Example("java", "java"),
        Example("dotnet", "dotnet"),
        Example("elixir", "elixir"),
        Example("deno", "deno"),
        Example("nextjs", "nextjs"),
        Example("nuxt", "nuxt"),
        Example("sveltekit", "sveltekit"),
        Example("astro", "astro"),
        Example("remix", "remix"),
        Example("angular", "angular"),
        Example("vite", "vite"),
        Example(
            "monorepo",
            "monorepo",
            create="-t monorepo --no-database",
            hosts=(
                ("app.ex-monorepo.test", "Hello from Noust (monorepo: web)"),
                ("api.ex-monorepo.test", "Hello from Noust (monorepo: api)"),
            ),
            releases=False,
        ),
        Example("docker-compose", "docker-compose", releases=False, docker=True),
        Example("container", "container", docker=True),
        Example("rails", "ruby", generate=RAILS),
        Example("phoenix", "elixir", generate=PHOENIX),
        Example("jekyll", "static", create="--type static", generate=JEKYLL),
    )
}

#: The change that makes version 2: every sentence, except in the lock files.
CHANGE_V2 = (
    "grep -rIl 'Hello from Noust' --exclude=package-lock.json --exclude-dir=node_modules "
    "--exclude-dir=.git . | xargs sed -i 's/Hello from Noust/Hello again from Noust/'"
)


def forget(sc: Scenario, example: Example) -> None:
    """Delete the application of an earlier run in a kept container, so a run starts clean."""
    sc.run(
        f"noust delete {example.domain} --force >/dev/null 2>&1; rm -rf {example.root}",
        timeout=300,
        check=False,
        label=f"(clean start) noust delete {example.domain}",
    )


def make_repository(sc: Scenario, example: Example) -> None:
    """
    Turn an example into a repository git daemon serves, as it is in ``examples/``.

    Args:
        sc: The scenario.
        example: The example.
    """
    if example.generate:
        sc.run(
            f"rm -rf {example.repo} && mkdir -p {example.repo} && cd {example.repo} && set -e && "
            f"{example.generate}",
            timeout=GENERATE_TIMEOUT,
            label=f"(generator) {example.name}: {example.generate.strip().splitlines()[0]} ...",
        )
        sc.run(
            f"cd {example.repo} && git init -q -b main && {GIT_IDENTITY} && git add -A && "
            "git commit -q -m 'version 1'",
            timeout=120,
            label=f"(repository) {example.repo}: what the generator wrote, served at {example.url}",
        )
        return
    sc.run(
        f"rm -rf {example.repo} && cp -a {EXAMPLES_IN_CONTAINER}/{example.name} {example.repo} && "
        f"cd {example.repo} && git init -q -b main && {GIT_IDENTITY} && git add -A && "
        "git commit -q -m 'version 1'",
        timeout=120,
        label=f"(repository) {example.repo}: examples/{example.name} at version 1, served at {example.url}",
    )


def wait_served(sc: Scenario, host: str, expected: str) -> str:
    """
    Wait for the web server to answer a host with a body that holds ``expected``.

    Args:
        sc: The scenario.
        host: The ``Host`` header, which picks the site.
        expected: What the body must contain.

    Returns:
        The last body seen.
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
    sc.evidence.append(
        f"$ (until it answers) curl -H 'Host: {host}' http://127.0.0.1/\n{body[:600]}"
    )
    return body


def serves(sc: Scenario, example: Example, version: int) -> None:
    """Check that every name of the example answers with the sentence of this version."""
    for host, sentence in example.expected(version):
        body = wait_served(sc, host, sentence)
        sc.check(sentence in body, f"{host} answered {body[:300]!r}, not {sentence!r}")


def stored(sc: Scenario, column: str, domain: str) -> str:
    """Read ``app_type`` or ``layout`` of an application's row."""
    queries = {
        "app_type": "SELECT app_type AS value FROM apps WHERE domain = @domain",
        "layout": "SELECT layout AS value FROM apps WHERE domain = @domain",
    }
    rows = run.store_rows(sc, queries[column], domain=domain, label=f"{column} of {domain}")
    sc.check(bool(rows), f"{domain} is not in the store")
    return str(rows[0]["value"])


def tail(proc: subprocess.CompletedProcess[str]) -> str:
    """The end of a command's output, for a failure message."""
    return (proc.stdout + proc.stderr)[-3000:]


def deploy(sc: Scenario, example: Example) -> None:
    """
    Create the example, serve it, update it, and go back when the type has releases.

    Args:
        sc: The scenario.
        example: The example.
    """
    if example.docker:
        run.require_docker(sc)
    forget(sc, example)
    make_repository(sc, example)

    created = sc.run(
        f"noust create -d {example.domain} -s {example.url} --no-ssl {example.create}",
        timeout=FIRST_DEPLOY_TIMEOUT,
        check=False,
        label=f"noust create -d {example.domain} -s {example.url} --no-ssl {example.create}",
    )
    sc.check(
        created.returncode == 0, f"noust create failed ({created.returncode}):\n{tail(created)}"
    )
    found = stored(sc, "app_type", example.domain)
    sc.check(found == example.app_type, f"stored as {found!r}, not {example.app_type!r}")
    serves(sc, example, 1)

    sc.run(
        f"cd {example.repo} && {CHANGE_V2} && git add -A && git commit -q -m 'version 2'",
        timeout=60,
        label="(repository) version 2: every sentence becomes 'Hello again from Noust'",
    )
    first = ""
    if example.releases:
        first = sc.run(
            f"readlink {example.root}/current", timeout=15, label=f"readlink {example.root}/current"
        ).stdout.strip()
    updated = sc.run(
        f"noust update {example.domain}",
        timeout=UPDATE_TIMEOUT,
        check=False,
        label=f"noust update {example.domain}",
    )
    sc.check(
        updated.returncode == 0, f"noust update failed ({updated.returncode}):\n{tail(updated)}"
    )
    serves(sc, example, 2)
    if not example.releases:
        return

    second = sc.run(
        f"readlink {example.root}/current", timeout=15, label=f"readlink {example.root}/current"
    ).stdout.strip()
    sc.check(first != second, f"the update did not switch release: {first!r}")
    rolled = sc.run(
        f"noust releases rollback {example.domain}",
        timeout=UPDATE_TIMEOUT,
        check=False,
        label=f"noust releases rollback {example.domain}",
    )
    sc.check(rolled.returncode == 0, f"the rollback failed ({rolled.returncode}):\n{tail(rolled)}")
    serves(sc, example, 1)
    back = sc.run(
        f"readlink {example.root}/current", timeout=15, label=f"readlink {example.root}/current"
    ).stdout.strip()
    sc.check(back == first, f"the rollback is on {back!r}, not {first!r}")


def from_a_directory(sc: Scenario) -> None:
    """
    Deploy an example from a plain directory, the form ``examples/README.md`` shows.

    Args:
        sc: The scenario.
    """
    domain = "ex-directory.test"
    sc.run(
        f"noust delete {domain} --force >/dev/null 2>&1; true",
        timeout=300,
        check=False,
        label=f"(clean start) noust delete {domain}",
    )
    created = sc.run(
        f"noust create -d {domain} -s {EXAMPLES_IN_CONTAINER}/nodejs --no-ssl",
        timeout=FIRST_DEPLOY_TIMEOUT,
        check=False,
        label=f"noust create -d {domain} -s {EXAMPLES_IN_CONTAINER}/nodejs --no-ssl",
    )
    sc.check(
        created.returncode == 0, f"noust create failed ({created.returncode}):\n{tail(created)}"
    )
    body = wait_served(sc, domain, "Hello from Noust (nodejs)")
    sc.check("Hello from Noust (nodejs)" in body, f"the directory deploy answered {body[:300]!r}")
    sc.run(
        f"noust delete {domain} --force", timeout=300, check=False, label=f"noust delete {domain}"
    )


ScenarioFn = Callable[[Any], None]


def example_scenario(name: str) -> ScenarioFn:
    """
    Make the scenario of one example.

    Args:
        name: The example's key in :data:`EXAMPLES`.

    Returns:
        A scenario function.
    """

    def scenario(sc: Scenario) -> None:
        deploy(sc, EXAMPLES[name])

    scenario.__doc__ = f"examples/{name}: create, serve, update, and go back."
    return scenario


SCENARIOS: list[tuple[str, ScenarioFn]] = [
    (f"example_{key.replace('-', '_')}", example_scenario(key)) for key in EXAMPLES
]
SCENARIOS.insert(0, ("example_from_a_directory", from_a_directory))


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
        "--example",
        action="append",
        default=None,
        metavar="NAME",
        help="Deploy only this example (the folder's name). May repeat.",
    )
    parser.add_argument("--skip-wheel-build", action="store_true", help="Reuse the newest wheel.")
    parser.add_argument(
        "--skip-image-build", action="store_true", help="Reuse the image if it exists."
    )
    parser.add_argument(
        "--reuse",
        action="store_true",
        help="Use the container a --keep run left (and reinstall the wheel in it): the toolchain "
        "caches stay warm.",
    )
    parser.add_argument(
        "--remove-images", action="store_true", help="Remove the images afterwards."
    )
    return parser.parse_args()


def selected(names: list[str] | None) -> list[tuple[str, ScenarioFn]]:
    if not names:
        return list(SCENARIOS)
    unknown = [name for name in names if name not in EXAMPLES]
    if unknown:
        raise HarnessError(f"no example named {unknown!r}; available: {sorted(EXAMPLES)}")
    wanted = {f"example_{name.replace('-', '_')}" for name in names}
    return [(n, fn) for n, fn in SCENARIOS if n in wanted]


def run_scenarios(to_run: list[tuple[str, ScenarioFn]]) -> Outcome:
    """
    Run scenarios in the container and print each one's evidence.

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
        wheel = build_wheel(workdir)
    build_images(rebuild=not args.skip_image_build)

    outcome = Outcome()
    setup_error: str | None = None
    reusing = args.reuse and container_running(CONTAINER_NAME)
    if not reusing:
        run.remove_container(CONTAINER_NAME)
    try:
        try:
            if reusing:
                sh(["docker", "cp", str(wheel), f"{CONTAINER_NAME}:/tmp/{wheel.name}"], timeout=60)
                docker_exec(
                    CONTAINER_NAME,
                    f"/opt/noust/bin/pip install --quiet --force-reinstall --no-deps '/tmp/{wheel.name}'",
                    timeout=run.PIP_INSTALL_TIMEOUT,
                )
            else:
                run.start_container(CONTAINER_NAME)
                wait_for_systemd(CONTAINER_NAME)
                install_noust(CONTAINER_NAME, wheel)
                run.prepare_inner_docker(CONTAINER_NAME)
            install_examples(CONTAINER_NAME)
            to_run = selected(args.example)
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
        if args.remove_images:
            remove_images()

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
