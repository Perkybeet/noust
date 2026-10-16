# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Ruby on Rails and Rack applications: Bundler, Puma and precompiled assets (checklist A-39).

Until 3.4 a repository with a ``Gemfile`` was only ever a container. Rails is the framework a
good part of the people who look for a self-hosted PaaS write, and it deploys the way every
other type here does: dependencies are installed in the release by the unprivileged build
account (``vendor/bundle``, so a rollback is the release's own gems and not a shared set), the
assets are precompiled there, and the unit runs Puma on the loopback address.

What is specific to Ruby:

- **Bundler is configured in the release** (``.bundle/config``: deployment, no development or
  test gems, ``vendor/bundle``), not through the environment. The unit, a deploy hook and
  ``noust app exec`` all start in the release and read the same file, so none of them has to
  be told where the gems are.
- **Production is a default in ``.env``, not a fixture of the unit.** ``RAILS_ENV`` and
  ``RACK_ENV`` are written once, only when absent, so a hook that runs ``rails db:migrate`` migrates
  the production database and an operator who really wants another environment sets it.
  ``SECRET_KEY_BASE`` is generated the same way for a Rails application.
- **The build compiles assets as production**, whatever ``.env`` says: a precompile in the
  development environment produces different files.
- **Migrations are a decision**, as with Django: they are declared as a ``pre_deploy`` hook
  (``bundle exec rails db:migrate``, ``migrates: true``), never guessed.
- **A Dockerfile means the container**, for the reason :mod:`noust.deployers.compiled` gives.

The Ruby is the server's own (a distribution package); Bundler reports a mismatch with the
project's ``.ruby-version`` in its own words, and the log warns first.
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
from noust.deployers.registry import DeployerRegistry

#: What a build is given: assets are compiled as production, with a throwaway secret so that an
#: application that reads ``secret_key_base`` while it loads (Rails 7.1 and later) does not need
#: the real one to be in the build's environment.
BUILD_ENVIRONMENT: dict[str, str] = {"RAILS_ENV": "production", "SECRET_KEY_BASE_DUMMY": "1"}

#: Written to ``.env`` when it has no value for them.
ENV_DEFAULTS: dict[str, str] = {"RAILS_ENV": "production", "RACK_ENV": "production"}

#: Bundler's gems, relative to the project: the release's own.
BUNDLE_PATH = "vendor/bundle"

#: Parallel downloads and compiles of ``bundle install``.
BUNDLE_JOBS = "4"

_RUBY_VERSION_FILE = re.compile(r"^(?:ruby-)?(\d+)\.(\d+)(?:\.\d+)?")
_GEMFILE_RUBY = re.compile(r"""^\s*ruby\s+["'](\d+)\.(\d+)""", re.MULTILINE)
_TOOL_VERSIONS_RUBY = re.compile(r"^ruby\s+(\d+)\.(\d+)", re.MULTILINE)
_RUBY_VERSION_OUTPUT = re.compile(r"\bruby\s+(\d+)\.(\d+)")


def requested_ruby(tree: Path) -> tuple[int, int] | None:
    """
    Read the Ruby release a project asks for.

    Args:
        tree: The project.

    Returns:
        ``(major, minor)`` from ``.ruby-version``, else ``.tool-versions``, else the ``ruby``
        line of the ``Gemfile``; None when the project says nothing.
    """

    def read(name: str) -> str:
        try:
            return (tree / name).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""

    for text, pattern in (
        (read(".ruby-version").strip(), _RUBY_VERSION_FILE),
        (read(".tool-versions"), _TOOL_VERSIONS_RUBY),
        (read("Gemfile"), _GEMFILE_RUBY),
    ):
        found = pattern.search(text)
        if found:
            return int(found.group(1)), int(found.group(2))
    return None


def installed_ruby(output: str) -> tuple[int, int] | None:
    """
    Read the release of the Ruby this server has.

    Args:
        output: What ``ruby --version`` printed.

    Returns:
        ``(major, minor)``; None when the output is not recognisable.
    """
    found = _RUBY_VERSION_OUTPUT.search(output)
    return (int(found.group(1)), int(found.group(2))) if found else None


class RubyDeployer(BaseDeployer):
    """
    Deployer for Rails and Rack applications.

    Puma is the server when the lock file has it (Rails always does); ``rackup`` otherwise; a
    project that starts differently says how with ``start`` in its build block.
    """

    APP_TYPE = "ruby"
    DISPLAY_NAME = "Ruby (Rails/Rack)"
    DETECTION_FILES: ClassVar[list[str]] = ["Gemfile"]
    #: What :meth:`detect` looks for beside the Gemfile, so that inspecting a remote repository
    #: (which fetches only the files detection reads) recognises a Rails or Rack application.
    #: The asset pipeline's own files are listed too, because whether `assets:precompile` runs
    #: depends on them.
    FRAMEWORK_CONFIG_FILES: ClassVar[list[str]] = [
        "config.ru",
        "config/application.rb",
        "app/assets/config/manifest.js",
        "app/assets/stylesheets/application.css",
        "app/javascript/application.js",
    ]
    DEFAULT_PORT = 3000

    #: With Go and Rust: below Python, above Node.
    DETECTION_PRIORITY = 45

    SYSTEM_DEPS: ClassVar[list[str]] = ["ruby", "bundle"]

    #: ``runtime`` is for the languages Noust installs per application; Ruby is the server's.
    BUILD_KEYS: ClassVar[frozenset[str]] = frozenset(
        {"base_directory", "install", "build", "start"}
    )

    #: Natively compiled gems need a compiler and the headers of what they bind to (libpq, ...):
    #: the hint names the toolchain, the gems' own errors name the rest.
    TOOLCHAIN_PACKAGES: ClassVar[dict[PackageFamily, str]] = {
        PackageFamily.APT: "ruby ruby-dev bundler build-essential",
        PackageFamily.DNF: "ruby ruby-devel rubygem-bundler gcc make redhat-rpm-config",
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

    # Detection -------------------------------------------------------------------------

    def detect(self, path: Path) -> bool:
        """
        Detect a Rails or Rack application.

        Args:
            path: Path to check.

        Returns:
            True for a ``Gemfile`` next to a ``config.ru`` or a Rails ``config/application.rb``,
            and no Dockerfile: a gem, a Jekyll site or a script has nothing to serve, and a
            Dockerfile is how the repository says it runs.
        """
        if not (path / "Gemfile").is_file() or has_container_file(path):
            return False
        return (path / "config.ru").is_file() or (path / "config" / "application.rb").is_file()

    def _is_rails(self) -> bool:
        """
        Returns:
            Whether the project is a Rails application (it has ``config/application.rb``).
        """
        return (self.build_path / "config" / "application.rb").is_file()

    def _lock_text(self) -> str:
        """
        Returns:
            ``Gemfile.lock``, empty when there is none.
        """
        try:
            return (self.build_path / "Gemfile.lock").read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""

    def _uses_puma(self) -> bool:
        """
        Returns:
            Whether the bundle includes Puma (an entry of the lock file, or of the Gemfile when
            there is no lock yet).
        """
        text = self._lock_text()
        if not text:
            try:
                text = (self.build_path / "Gemfile").read_text(encoding="utf-8", errors="ignore")
            except OSError:
                return False
            return re.search(r"""gem\s+["']puma["']""", text) is not None
        return re.search(r"^\s+puma \(", text, re.MULTILINE) is not None

    def _has_assets(self) -> bool:
        """
        Returns:
            Whether a Rails application has an asset pipeline to compile (an API-only one has
            neither directory, and ``assets:precompile`` has nothing to do for it).
        """
        return self._is_rails() and any(
            (self.build_path / "app" / name).is_dir() for name in ("assets", "javascript")
        )

    def _has_migrations(self) -> bool:
        """
        Returns:
            Whether the application has migrations of its own.
        """
        directory = self.build_path / "db" / "migrate"
        return directory.is_dir() and any(directory.glob("*.rb"))

    # Commands --------------------------------------------------------------------------

    def get_install_command(self) -> list[str]:
        """
        Get the command that installs the gems into the release.

        Returns:
            ``bundle install``, configured by :meth:`pre_install`.
        """
        return ["bundle", "install", "--jobs", BUNDLE_JOBS, "--retry", "3"]

    def get_build_command(self) -> list[str]:
        """
        Get the command that compiles the assets.

        Returns:
            ``bundle exec rails assets:precompile`` for a Rails application with an asset
            pipeline; nothing otherwise. The build runs as production
            (:data:`BUILD_ENVIRONMENT`).
        """
        if self._has_assets():
            return ["bundle", "exec", "rails", "assets:precompile"]
        return []

    def get_start_command(self) -> str:
        """
        Get the command the unit runs.

        Returns:
            Puma bound to the loopback address and the application's port, or ``rackup`` for a
            bundle without Puma. Both run through ``bundle exec``, which finds the gems in the
            release the unit starts in.
        """
        if self._uses_puma():
            return f"bundle exec puma -b tcp://127.0.0.1:{self.port}"
        return f"bundle exec rackup -o 127.0.0.1 -p {self.port} config.ru"

    def _unit_environment(self) -> dict[str, str]:
        """
        Say what the unit sets: the port, and where Rails writes its log and serves files from.

        Returns:
            ``PORT``, ``RAILS_LOG_TO_STDOUT`` (so the journal has the application's log) and
            ``RAILS_SERVE_STATIC_FILES`` (the web server proxies everything to Puma, which
            serves the precompiled assets). ``RAILS_ENV`` is a default of ``.env`` instead.
        """
        return {
            "PORT": str(self.port),
            "RAILS_LOG_TO_STDOUT": "1",
            "RAILS_SERVE_STATIC_FILES": "1",
        }

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
        Run a command, with assets compiled as production when it is the build.

        Args:
            command: Program and arguments.
            cwd: Working directory.
            env: Extra environment, which wins over what this adds, as do the variables the
                application was created with.
            timeout: Deadline in seconds.
            stream: Print output while the command runs.
            phase: What the command does.
            tools: Put the project's tool directories on ``PATH``.

        Returns:
            The command outcome.
        """
        effective = phase or (self._phases[-1] if self._phases else BuildPhase.BUILD)
        if effective is BuildPhase.BUILD:
            # What the operator gave when creating the application is theirs to decide; what
            # ``.env`` holds is not read here, so it cannot turn the build into a development one.
            given = {key: value for key, value in self.env_vars.items() if key in BUILD_ENVIRONMENT}
            env = {**BUILD_ENVIRONMENT, **given, **(env or {})}
        return super()._run(command, cwd, env, timeout, stream=stream, phase=phase, tools=tools)

    # Steps -----------------------------------------------------------------------------

    def pre_install(self) -> bool:
        """
        Configure Bundler for the release, and warn when the Ruby is not the project's.

        Returns:
            True.

        Raises:
            DeploymentError: Bundler is too old to be configured this way, or refuses.
        """
        self._warn_about_the_ruby_version()
        settings = [("without", "development:test"), ("path", BUNDLE_PATH)]
        if (self.build_path / "Gemfile.lock").is_file():
            settings.insert(0, ("deployment", "true"))
        else:
            self.logger.warning(
                "There is no Gemfile.lock: gems are resolved on this server, and two deployments "
                "may differ. Commit it."
            )
        for key, value in settings:
            result = self._run(["bundle", "config", "set", "--local", key, value], timeout=60)
            if not result.success:
                raise DeploymentError(
                    f"bundle config set --local {key} failed",
                    details=(result.stderr or result.stdout or "No output.").strip()
                    + "\nBundler 2.1 or later is needed: gem install bundler.",
                )
        return True

    def _warn_about_the_ruby_version(self) -> None:
        """Say when this server's Ruby is not the release the project asks for."""
        wanted = requested_ruby(self.build_path)
        if wanted is None:
            return
        probe = self._run(["ruby", "--version"], timeout=60)
        have = installed_ruby(probe.stdout or "")
        if have is not None and have != wanted:
            self.logger.warning(
                f"The project asks for Ruby {wanted[0]}.{wanted[1]} and this server has "
                f"{have[0]}.{have[1]}: Bundler may refuse the bundle."
            )

    def post_install(self) -> bool:
        """
        Say that migrations are a decision, once the project is known to have some.

        Returns:
            True.
        """
        if self._is_rails() and self._has_migrations() and not self._hook_set().declared:
            self.logger.substep(
                "Rails migrations are not run for you: declare them as a pre_deploy hook "
                "(run: bundle exec rails db:migrate, migrates: true); see docs/build.md"
            )
        return True

    def _prepare_env(self) -> None:
        """
        Write the defaults ``.env`` has no value for: the environment, and a Rails secret.

        Run on every deployment and writing only what is absent, so an operator's value is
        never replaced and the secret an application already runs on never changes.
        """
        super()._prepare_env()
        current = self._env_manager.read_env_file(self._env_file())
        wanted = {
            key: value
            for key, value in ENV_DEFAULTS.items()
            if key not in current and key not in self.env_vars
        }
        if (
            self._is_rails()
            and "SECRET_KEY_BASE" not in current
            and "SECRET_KEY_BASE" not in self.env_vars
        ):
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
            The generic one, marked as a Ruby application.
        """
        context = super().get_template_context()
        context["is_ruby"] = True
        return context


DeployerRegistry.register(RubyDeployer)
