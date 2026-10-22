# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Elixir and Phoenix applications: a Mix release, run as the unit (checklist A-40).

Until 4.0 a repository with a ``mix.exs`` was only ever a container. A Mix release is what the
BEAM community deploys: a directory with the Erlang runtime inside, started by one script, so
the server needs Elixir only to build it and a rollback is a symlink to a release that carries
everything it runs on.

What is specific to Elixir:

- **The build is ``MIX_ENV=prod``, whatever ``.env`` says**, as Rails' is production: a release
  compiled for development is a different program.
- **Hex and Rebar are installed for the application** (``mix local.hex --force``), in its build
  cache, as the build account; the dependencies come from ``mix deps.get --only prod``, which is
  the install phase and so has the network when the strict profile is on.
- **Phoenix assets** are built when the project has the alias (``assets.setup`` in the install
  phase, which downloads esbuild and Tailwind, and ``assets.deploy`` in the build).
- **No Erlang distribution by default.** A release started with ``start`` runs ``epmd`` and
  listens on a node name; ``RELEASE_DISTRIBUTION=none`` in ``.env`` keeps the application off
  the network until an operator who clusters nodes says otherwise.
- **Production defaults are written once to ``.env``**: ``PHX_SERVER=true`` (a release does not
  start the endpoint without it), ``PHX_HOST`` (the domain, which Phoenix checks WebSocket
  origins against) and a generated ``SECRET_KEY_BASE`` for Phoenix.
- **Migrations are a decision**, as with Rails: declare them as a ``pre_deploy`` hook
  (``bin/<name> eval MyApp.Release.migrate``, ``migrates: true``), never guessed.
- **A Dockerfile means the container**, for the reason :mod:`noust.deployers.compiled` gives.

The Elixir and Erlang are the server's own (distribution packages); the release carries the
Erlang runtime it was built with, so the running application does not depend on later upgrades
of it.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar

from noust.core.exceptions import DeploymentError
from noust.core.fs import FileSystem
from noust.core.package_family import PackageFamily
from noust.core.runner import CommandResult, CommandRunner
from noust.deployers.base import COMMAND_TIMEOUT, BaseDeployer
from noust.deployers.compiled import has_container_file, install_suggestion_for
from noust.deployers.helpers.sandbox import BuildPhase
from noust.deployers.helpers.unit_launch import systemd_command
from noust.deployers.registry import DeployerRegistry

#: What a build is given: the release is built as production.
BUILD_ENVIRONMENT: dict[str, str] = {"MIX_ENV": "prod"}

#: Written to ``.env`` when it has no value for them. The distribution is off: see the module.
ENV_DEFAULTS: dict[str, str] = {"PHX_SERVER": "true", "RELEASE_DISTRIBUTION": "none"}

#: Where ``mix release`` leaves its releases, relative to the project.
RELEASE_ROOT = "_build/prod/rel"

_PHOENIX = re.compile(r":phoenix\b")
_APPLICATION_MODULE = re.compile(r"\bmod\s*:\s*\{")
_RELEASES_KEYWORD = re.compile(r"\breleases\s*:")
_APP_NAME = re.compile(r"\bapp\s*:\s*:([a-z_][a-zA-Z0-9_]*)")
_RELEASES_OPEN = re.compile(r"\breleases\s*:\s*\[")
_KEYWORD_KEY = re.compile(r"([a-z_][a-zA-Z0-9_]*):(?!:)")
_ASSETS_SETUP = re.compile(r"""["']assets\.setup["']\s*:|\bassets\.setup\b""")
_ASSETS_DEPLOY = re.compile(r"""["']assets\.deploy["']\s*:|\bassets\.deploy\b""")
_ELIXIR_REQUIREMENT = re.compile(r"""\belixir\s*:\s*["']\s*(?:~>|>=|==)?\s*(\d+)\.(\d+)""")
_ELIXIR_VERSION_OUTPUT = re.compile(r"\bElixir\s+(\d+)\.(\d+)")
#: What Mix says when a dependency needs a newer Elixir than the server has: it compiles on and
#: fails somewhere inside the dependency, with an error that does not mention the version.
_DEPENDENCY_NEEDS_ELIXIR = re.compile(
    r'the dependency :(?P<name>\w+) requires Elixir "(?P<need>[^"]+)" but you are running on '
    r"v(?P<have>[\d.]+)"
)


def mix_text(project: Path) -> str:
    """
    Read a project's ``mix.exs``.

    Args:
        project: The project.

    Returns:
        Its text; empty when there is none or it is a link. Only read, never evaluated: it is
        Elixir code of an untrusted repository.
    """
    path = project / "mix.exs"
    if path.is_symlink() or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def is_phoenix(text: str) -> bool:
    """
    Args:
        text: The ``mix.exs``.

    Returns:
        Whether the project depends on Phoenix.
    """
    return _PHOENIX.search(text) is not None


def has_application(text: str) -> bool:
    """
    Tell whether a Mix project is a program and not a library.

    Args:
        text: The ``mix.exs``.

    Returns:
        True for an OTP application (``mod: {...}`` starts it), a release configuration, or
        Phoenix. A library has none of the three.
    """
    return bool(
        _APPLICATION_MODULE.search(text) or _RELEASES_KEYWORD.search(text) or is_phoenix(text)
    )


def app_name(text: str) -> str | None:
    """
    Args:
        text: The ``mix.exs``.

    Returns:
        The OTP application's name (``app: :shop``), which names the release by default.
    """
    found = _APP_NAME.search(text)
    return found.group(1) if found else None


def _release_keys(text: str) -> list[str]:
    """
    Read the names a ``releases:`` keyword list defines.

    Args:
        text: The ``mix.exs``.

    Returns:
        The keys at the first level of ``releases: [...]`` (``web`` and ``jobs`` in
        ``releases: [web: [...], jobs: [...]]``); empty when there is no such list, or it is a
        function call (``releases: releases()``), which is not read.
    """
    opened = _RELEASES_OPEN.search(text)
    if opened is None:
        return []
    keys: list[str] = []
    depth = 1
    index = opened.end()
    while index < len(text) and depth > 0:
        char = text[index]
        if char == '"':
            end = text.find('"', index + 1)
            index = len(text) if end == -1 else end + 1
            continue
        if char in "[({":
            depth += 1
        elif char in "])}":
            depth -= 1
        elif depth == 1 and (
            index == 0 or not (text[index - 1].isalnum() or text[index - 1] in "_:.")
        ):
            key = _KEYWORD_KEY.match(text, index)
            if key:
                keys.append(key.group(1))
                index = key.end()
                continue
        index += 1
    return keys


def release_names(text: str) -> list[str]:
    """
    Args:
        text: The ``mix.exs``.

    Returns:
        The names a ``releases:`` block defines, then the application's own; the order in which
        :func:`choose_release` prefers them.
    """
    names = _release_keys(text)
    own = app_name(text)
    if own and own not in names:
        names.append(own)
    return names


def requested_elixir(text: str) -> tuple[int, int] | None:
    """
    Args:
        text: The ``mix.exs``.

    Returns:
        ``(major, minor)`` of the ``elixir:`` requirement; None when it names none.
    """
    found = _ELIXIR_REQUIREMENT.search(text)
    return (int(found.group(1)), int(found.group(2))) if found else None


def newer_elixir_hint(output: str) -> str:
    """
    Explain a compilation that failed because the server's Elixir is older than a dependency's.

    The distributions ship an Elixir that lags the Hex packages (Ubuntu 24.04 has 1.14, and a
    current ``plug`` wants 1.15), and the failure Mix reports is an error from inside the
    dependency, far from the line that says why.

    Args:
        output: What the failed compilation printed.

    Returns:
        A paragraph that names the dependency and the two versions and says what to do; empty when
        the output has no such warning.
    """
    found = _DEPENDENCY_NEEDS_ELIXIR.search(output)
    if found is None:
        return ""
    return (
        f"\n\n{found.group('name')} asks for Elixir {found.group('need')} and this server has "
        f"{found.group('have')}, which is the likely cause. Install a newer Elixir system-wide "
        "(https://elixir-lang.org/install.html), or pin an older version of the dependency in "
        "mix.exs."
    )


def installed_elixir(output: str) -> tuple[int, int] | None:
    """
    Args:
        output: What ``elixir --version`` printed.

    Returns:
        ``(major, minor)`` of the Elixir; None when the output is not recognisable.
    """
    found = _ELIXIR_VERSION_OUTPUT.search(output)
    return (int(found.group(1)), int(found.group(2))) if found else None


def choose_release(built: Sequence[str], *, hints: Sequence[str]) -> str:
    """
    Pick the release an application runs among those ``mix release`` built.

    Args:
        built: The directory names under ``_build/prod/rel``.
        hints: Names that point at the right one, best first: the ``releases:`` names, the
            application's, Noust's own name for it.

    Returns:
        The release's name.

    Raises:
        DeploymentError: Nothing was built, or several releases were and nothing says which.
    """
    if not built:
        raise DeploymentError(
            "mix release built nothing",
            details=f"Look for a release under {RELEASE_ROOT}: the build's output above says "
            "why there is none. A release is defined by `releases:` in mix.exs, or by the "
            "application itself.",
        )
    if len(built) == 1:
        return built[0]
    for hint in hints:
        if hint in built:
            return hint
    raise DeploymentError(
        f"mix release built {len(built)} releases: {', '.join(sorted(built))}",
        details="Say which one runs in the build block, for example "
        "build.start: [./_build/prod/rel/NAME/bin/NAME, start].",
    )


class ElixirDeployer(BaseDeployer):
    """
    Deployer for Mix projects: ``mix release`` in the sandbox, its start script as the unit.

    Phoenix is recognised by its dependency, and gets its assets built and its production
    defaults; any other OTP application with a ``mod:`` or a ``releases:`` block deploys the
    same way.
    """

    APP_TYPE = "elixir"
    DISPLAY_NAME = "Elixir (Phoenix)"
    DETECTION_FILES: ClassVar[list[str]] = ["mix.exs"]
    DEFAULT_PORT = 4000

    #: With the other native stacks: below Python, above Node.
    DETECTION_PRIORITY = 45

    SYSTEM_DEPS: ClassVar[list[str]] = ["elixir", "mix"]

    #: ``runtime`` is for the languages Noust installs per application; Elixir is the server's.
    BUILD_KEYS: ClassVar[frozenset[str]] = frozenset(
        {"base_directory", "install", "build", "start"}
    )

    #: Elixir pulls Erlang in; the build also needs a C compiler for NIFs.
    TOOLCHAIN_PACKAGES: ClassVar[dict[PackageFamily, str]] = {
        PackageFamily.APT: "elixir erlang-dev build-essential",
        PackageFamily.DNF: "elixir erlang gcc make",
        PackageFamily.ZYPPER: "elixir erlang gcc make",
    }

    def __init__(
        self,
        verbose: bool = False,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
    ):
        """
        Args:
            verbose: Enable verbose logging.
            runner: Command runner used for installs and builds. Defaults to the
                process-wide runner.
            fs: Filesystem every change goes through. Defaults to the process-wide one.
        """
        super().__init__(verbose=verbose, runner=runner, fs=fs)
        #: The release ``mix release`` built, once the build has run.
        self.release_name: str | None = None

    # Detection -------------------------------------------------------------------------

    def detect(self, path: Path) -> bool:
        """
        Detect a Mix project that is a program.

        Args:
            path: Path to check.

        Returns:
            True for a ``mix.exs`` that starts an OTP application, defines a release or depends
            on Phoenix, and no Dockerfile: a library has nothing to run, and a Dockerfile is how
            the repository says it runs.
        """
        if has_container_file(path):
            return False
        return has_application(mix_text(path))

    def _text(self) -> str:
        """
        Returns:
            The project's ``mix.exs``.
        """
        return mix_text(self.build_path)

    def _is_phoenix(self) -> bool:
        """
        Returns:
            Whether the project is a Phoenix application.
        """
        return is_phoenix(self._text())

    # Commands --------------------------------------------------------------------------

    def get_install_command(self) -> list[str]:
        """
        Get the command that fetches the production dependencies.

        Returns:
            ``mix deps.get --only prod``. Hex and Rebar are set up by :meth:`pre_install`.
        """
        return ["mix", "deps.get", "--only", "prod"]

    def get_build_command(self) -> list[str]:
        """
        Get the command that builds the release.

        Returns:
            ``mix release --overwrite``; ``mix compile`` and the assets run before it
            (:meth:`pre_build`).
        """
        return ["mix", "release", "--overwrite"]

    def _release_directory(self) -> Path:
        """
        Returns:
            Where the releases are, in the project.
        """
        return self.build_path / RELEASE_ROOT

    def _built_releases(self) -> list[str]:
        """
        Returns:
            The names of the releases ``mix release`` left, those with a start script.
        """
        root = self._release_directory()
        try:
            candidates = sorted(entry for entry in root.iterdir() if entry.is_dir())
        except OSError:
            return []
        return [entry.name for entry in candidates if (entry / "bin" / entry.name).is_file()]

    def get_start_command(self) -> str:
        """
        Get the command the unit runs.

        Returns:
            The release's start script with ``start``, through ``current`` on releases. The
            release is the one built; before a build (``noust inspect``) it is the
            application's name, which is what Mix names it unless ``releases:`` says otherwise.
        """
        name = self.release_name or (release_names(self._text()) or [self.app_name or "app"])[0]
        script = self._at_runtime(self._release_directory() / name / "bin" / name)
        return systemd_command([str(script), "start"])

    def get_health_check(self) -> str:
        """
        Get the health check endpoint.

        Returns:
            ``/``: any answer but a server error means the endpoint is up.
        """
        return "/"

    def _unit_environment(self) -> dict[str, str]:
        """
        Say what the unit sets: the port, and to listen on the loopback address.

        Returns:
            ``PORT`` and ``HOST``. The proxy reaches the program on the loopback address;
            Phoenix's generated ``runtime.exs`` binds every interface whatever ``HOST`` says,
            which is the firewall's to close, as any application's is.
        """
        return {"PORT": str(self.port), "HOST": "127.0.0.1"}

    def _run(
        self,
        command: Sequence[str],
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = COMMAND_TIMEOUT,
        *,
        stream: bool = False,
        phase: BuildPhase | None = None,
        tools: bool = False,
    ) -> CommandResult:
        """
        Run a command, with the release built as production whatever ``.env`` says.

        Args:
            command: Program and arguments.
            cwd: Working directory.
            env: Extra environment, which wins over what this adds.
            timeout: Deadline in seconds.
            stream: Print output while the command runs.
            phase: What the command does.
            tools: Put the project's tool directories on ``PATH``.

        Returns:
            The command outcome.
        """
        effective = phase or (self._phases[-1] if self._phases else BuildPhase.BUILD)
        if effective in (BuildPhase.INSTALL, BuildPhase.BUILD):
            given = {key: value for key, value in self.env_vars.items() if key in BUILD_ENVIRONMENT}
            env = {**BUILD_ENVIRONMENT, **given, **(env or {})}
        return super()._run(command, cwd, env, timeout, stream=stream, phase=phase, tools=tools)

    # Steps -----------------------------------------------------------------------------

    def pre_install(self) -> bool:
        """
        Set up Hex and Rebar for the application, and warn when the Elixir is too old.

        Returns:
            True.

        Raises:
            DeploymentError: Hex or Rebar cannot be installed.
        """
        self._warn_about_the_elixir_version()
        for tool in ("local.hex", "local.rebar"):
            result = self._run(["mix", tool, "--force"], timeout=300)
            if not result.success:
                raise DeploymentError(
                    f"mix {tool} --force failed",
                    details=(result.stderr or result.stdout or "No output.").strip()
                    + "\nHex and Rebar are downloaded on the first build: the install phase "
                    "needs the network.",
                )
        return True

    def _warn_about_the_elixir_version(self) -> None:
        """Say when this server's Elixir is older than the one ``mix.exs`` asks for."""
        wanted = requested_elixir(self._text())
        if wanted is None:
            return
        probe = self._run(["elixir", "--version"], timeout=60)
        have = installed_elixir(probe.stdout or "")
        if have is not None and have < wanted:
            self.logger.warning(
                f"mix.exs asks for Elixir {wanted[0]}.{wanted[1]} and this server has "
                f"{have[0]}.{have[1]}: the compilation may be refused."
            )

    def post_install(self) -> bool:
        """
        Download Phoenix's asset tools, and say that migrations are a decision.

        Returns:
            True.
        """
        text = self._text()
        if self._is_phoenix() and _ASSETS_SETUP.search(text):
            result = self._run(["mix", "assets.setup"], timeout=900)
            if not result.success:
                output = (result.stderr or result.stdout or "No output.").strip()
                raise DeploymentError(
                    "mix assets.setup failed", details=output + newer_elixir_hint(output)
                )
        if self._is_phoenix() and not self._hook_set().declared:
            self.logger.substep(
                "Ecto migrations are not run for you: declare them as a pre_deploy hook "
                "(run: bin/<name> eval MyApp.Release.migrate, migrates: true); see docs/build.md"
            )
        return True

    def pre_build(self) -> bool:
        """
        Compile the project and build Phoenix's assets before the release is assembled.

        Returns:
            True.

        Raises:
            DeploymentError: The compilation or the assets failed.
        """
        for command in self._before_release():
            self.logger.substep(f"Running: {' '.join(command)}")
            result = self._run(command, timeout=2700, stream=True)
            if not result.success:
                output = (result.stderr or result.stdout or "No output.").strip()
                raise DeploymentError(
                    f"{' '.join(command)} failed", details=output + newer_elixir_hint(output)
                )
        return True

    def _before_release(self) -> list[list[str]]:
        """
        Returns:
            The commands that run before ``mix release``: the compilation and, for Phoenix with
            the alias, the assets. None when the build block replaces the build.
        """
        if self.build_config().build:
            return []
        commands = [["mix", "compile"]]
        if self._is_phoenix() and _ASSETS_DEPLOY.search(self._text()):
            commands.append(["mix", "assets.deploy"])
        return commands

    def post_build(self) -> bool:
        """
        Find which release was built, and check that it can be started.

        Returns:
            True.

        Raises:
            DeploymentError: Nothing was built, or several releases were and nothing says which.
        """
        if self.build_config().build:
            return True
        built = self._built_releases()
        if not built and not self._release_directory().is_dir():
            # A dry run builds nothing.
            return True
        self.release_name = choose_release(
            built, hints=[*release_names(self._text()), self.app_name or ""]
        )
        self.logger.substep(f"Release: {self.release_name}")
        return True

    def _prepare_env(self) -> None:
        """
        Write the defaults ``.env`` has no value for: production switches and a Phoenix secret.

        Run on every deployment and writing only what is absent, so an operator's value is
        never replaced and the secret an application already runs on never changes.
        """
        super()._prepare_env()
        current = self._env_manager.read_env_file(self._env_file())
        known = {**current, **self.env_vars}
        wanted = {key: value for key, value in ENV_DEFAULTS.items() if key not in known}
        if self._is_phoenix():
            if "PHX_HOST" not in known and self.domain:
                wanted["PHX_HOST"] = self.domain
            if "SECRET_KEY_BASE" not in known:
                wanted["SECRET_KEY_BASE"] = secrets.token_hex(64)
        if not wanted:
            return
        self._write_env_file({**current, **wanted})
        self.logger.substep(f"Set {', '.join(sorted(wanted))} in .env")

    # Reporting -------------------------------------------------------------------------

    @classmethod
    def install_suggestion(cls, runner: CommandRunner) -> str | None:
        """
        Say how to get the toolchain, for ``noust inspect``.

        Args:
            runner: Used to tell which package manager this server has.

        Returns:
            The command, with the next step; None when no package is known for this server.
        """
        return install_suggestion_for(cls.TOOLCHAIN_PACKAGES, runner)

    def _missing_dependencies_issue(self, missing: list[str]) -> str:
        """
        Say which programs are missing and how to install them.

        Args:
            missing: The programs of :attr:`SYSTEM_DEPS` this server lacks.

        Returns:
            The pre-flight line, with the install command for this server's package manager.
        """
        line = super()._missing_dependencies_issue(missing)
        suggestion = install_suggestion_for(self.TOOLCHAIN_PACKAGES, self.runner, sentence=False)
        return f"{line}. Install it with: {suggestion}" if suggestion else line

    def get_template_context(self) -> dict[str, Any]:
        """
        Get the template context.

        Returns:
            The generic one, marked as an Elixir application.
        """
        context = super().get_template_context()
        context["is_elixir"] = True
        return context


DeployerRegistry.register(ElixirDeployer)
