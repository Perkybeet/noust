# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The commands a type gives (install, build, start), the web server templates and the checks before a deploy.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Mapping
from pathlib import Path

from noust.core.appdir.permissions import hand_over_file
from noust.core.exceptions import DeploymentError
from noust.core.fs import SECRET_MODE
from noust.core.variables.model import BUILD
from noust.deployers.base.execution import ExecutionMixin
from noust.deployers.helpers import preflight
from noust.managers.apache_manager import ApacheManager
from noust.managers.nginx_manager import NginxManager
from noust.validators.environment import validate_environment


class CommandsMixin(ExecutionMixin):
    """
    The commands a type gives (install, build, start), the web server templates and the checks before a deploy.
    """

    @abstractmethod
    def detect(self, path: Path) -> bool:
        """
        Detect if path contains this type of application.

        Args:
            path: Path to check.

        Returns:
            True if this deployer can handle the application.
        """
        pass

    @abstractmethod
    def get_install_command(self) -> list[str]:
        """
        Get the command to install dependencies.

        Returns:
            Command as list of arguments.
        """
        pass

    @abstractmethod
    def get_build_command(self) -> list[str]:
        """
        Get the command to build the application.

        Returns:
            Command as list of arguments.
        """
        pass

    # What a deployment runs: the type's own commands, or the build block's -----

    def install_command(self) -> list[str]:
        """
        Name the command that installs the dependencies of this deployment.

        Returns:
            The build block's ``install`` when it sets one, which replaces the
            type's; :meth:`get_install_command` otherwise.
        """
        custom = self.build_config().install
        return list(custom) if custom else self.get_install_command()

    def build_command(self) -> list[str]:
        """
        Name the command that builds this deployment.

        Returns:
            The build block's ``build`` when it sets one, which replaces the
            type's; :meth:`get_build_command` otherwise.
        """
        custom = self.build_config().build
        return list(custom) if custom else self.get_build_command()

    def get_health_check(self) -> str:
        """
        Get the health check endpoint.

        Returns:
            Health check path (default: /).
        """
        return "/"

    def get_nginx_template(self) -> str:
        """
        Get the Nginx template name for this app type.

        Returns "advanced" if a noust.nginx.yaml (or the name it had before 4.0) with
        routes of its own was detected, otherwise the default "proxy"
        template, which a file without such routes tunes.

        Returns:
            Template name.
        """
        config = self._nginx_advanced_config
        if config is not None and not config.proxies_the_app:
            return "advanced"
        return "proxy"

    def get_apache_template(self) -> str:
        """
        Get the Apache template name for this app type.

        Returns:
            Template name.
        """
        return "proxy"

    def get_template_context(self) -> dict:
        """
        Get template context for configuration files.

        If an advanced nginx config is detected, returns the
        context built by NginxConfigBuilder instead.

        Returns:
            Context dictionary.
        """
        # Only the primary: every other name the site answers on is a row in
        # the store, and WebServerManager reads the rows where it writes the
        # file, so no caller - this one included - decides them.
        server_names = self.domain

        config = self._nginx_advanced_config
        if config is not None and not config.proxies_the_app:
            ctx = self._nginx_config_builder.build_context(
                config,
                self.domain,
                ssl=self.ssl,
                app_path=str(self.runtime_path),
            )
            ctx["server_names"] = server_names
            return ctx

        ctx = {
            "domain": self.domain,
            "server_names": server_names,
            "port": self.port,
            "app_path": str(self.runtime_path),
            "app_name": self.app_name,
            "ssl": self.ssl,
            "health_check": self.get_health_check(),
        }
        if config is not None:
            # A noust.nginx.yaml without routes of its own tunes the proxy site.
            ctx.update(
                self._nginx_config_builder.proxy_context(
                    config, self.domain, app_path=str(self.runtime_path)
                )
            )
        return ctx

    def _detect_nginx_config(self) -> None:
        """
        Detect and parse noust.nginx.yaml (or the name it had before 4.0) if present.

        Sets self._nginx_advanced_config if a valid config file is found.
        """
        if not self.build_path or not self.build_path.exists():
            return

        config_path = self._nginx_config_builder.detect(self.build_path)
        if config_path:
            self.logger.debug(f"Found advanced nginx config: {config_path}")
            notice = self._nginx_config_builder.rename_notice(config_path)
            if notice:
                self.logger.warning(notice)
            self._nginx_advanced_config = self._nginx_config_builder.parse(config_path)

            errors = self._nginx_config_builder.validate(
                self._nginx_advanced_config, tree=self.build_path
            )
            if errors:
                self.logger.warning(f"Nginx config validation errors: {', '.join(errors)}")
                self._nginx_advanced_config = None

    def _should_configure_env(self) -> bool:
        """
        Check if automatic env configuration should run.

        Returns True if .env.example exists and no --env-file was provided.

        Returns:
            True if env configuration should be performed.
        """
        if not self.build_path or not self.build_path.exists():
            return False

        # Skip if user already provided env vars
        if self.env_vars:
            return False

        # A release shares the .env every earlier release used; generating a
        # new one would replace the secrets the application already runs on.
        if self.uses_releases and self._env_file().exists():
            return False

        # Check for .env.example
        return (self.build_path / ".env.example").exists()

    def _generated_env(self, supplied: Mapping[str, str] | None = None) -> dict[str, str]:
        """
        Fill in what ``.env.example`` asks for, without asking anyone.

        Args:
            supplied: Values Noust already provides (the database created for
                the first deploy); a placeholder for one of them is not
                reported as something the operator must give.

        Returns:
            Defaults from the example, with a generated secret wherever the
            example names one and leaves it empty.
        """
        variables = self._env_manager.discover(self.build_path)
        if not variables:
            return {}
        self.logger.debug(f"Discovered {len(variables)} env variables")
        return self._env_manager.prompt_non_interactive(variables, supplied)

    def _unit_environment(self) -> dict[str, str]:
        """
        The variables the unit sets inline, and the only ones it carries.

        They are not secret and they are Noust's to decide: PORT is the port the
        site proxies to. Everything else - what ``--env-file`` or ``env_vars``
        gave, what ``.env.example`` generated - goes to the env file, because a
        unit is 0644 and ``systemctl show`` prints its ``Environment=`` to any
        local user.

        Returns:
            Variable name to value.
        """
        return {"PORT": str(self.port), "NODE_ENV": "production"}

    def _prepare_env(self) -> None:
        """
        Write the variables given at create time into the env file, before the build.

        The variables from ``--env-file`` or ``POST /api/apps`` are merged over
        what the file already holds; when none were given and the source ships
        a ``.env.example``, it is filled in instead. The build runs after this,
        so a build that reads ``DATABASE_URL`` from ``.env`` finds it, and the
        build commands keep seeing the variables in their own environment too.

        Raises:
            EnvironmentValidationError: When a variable given at create time
                has an unusable name or a control character in its value,
                before anything is written.
        """
        asked = validate_environment({**self.env_vars, **self._database_env})
        given, written_as = self._expand_references(asked)
        generated = self._generated_env(given) if self._should_configure_env() else {}
        if not (given or generated):
            return

        owned = self._unit_environment()
        for key in sorted(given.keys() & owned.keys()):
            if given[key] != owned[key]:
                self.logger.warning(
                    f"{key}={given[key]} is not used: Noust sets {key}={owned[key]} in the unit"
                )

        current = self._env_manager.read_env_file(self._env_file())
        everything = {**current, **generated, **given}
        # A variable only the build is given is in no file the unit loads.
        build_only = {
            name
            for name, kind in self._env_audience.items()
            if kind == BUILD and name in everything
        }
        self._write_env_file({k: v for k, v in everything.items() if k not in build_only})
        if generated:
            self.logger.substep("Created .env from .env.example")
        self.env_vars = {**generated, **given}
        self._record_variables(everything, written_as, names=set(asked))

    def _expand_references(self, asked: Mapping[str, str]) -> tuple[dict[str, str], dict[str, str]]:
        """
        Resolve the ``{{...}}`` references in the variables a deployment was given.

        Args:
            asked: The variables as given, validated.

        Returns:
            The variables with their references resolved, and the text of each
            one that had any (what a later deploy resolves again).

        Raises:
            ValidationError: A reference that is not well formed.
            UnresolvedReferenceError: A reference that cannot be resolved.
            SecretProviderError: A secret manager could not give a secret.
        """
        return self._variables.expand(asked)

    def _record_variables(
        self, everything: Mapping[str, str], written_as: Mapping[str, str], *, names: set[str]
    ) -> None:
        """
        Remember what the variables given at create time are made of and for.

        Args:
            everything: Every variable the application has now.
            written_as: The text of the ones made of references.
            names: The names the create-time set speaks for.
        """
        self._variables.remember(everything, written_as, names=names)

    def _refresh_variables(self) -> None:
        """
        Resolve the application's references again, before anything is built.

        A shared variable that changed, a secret that was rotated, the commit
        and the release being built: they reach the ``.env`` (and the values
        only the build is given) here. Nothing is written when none changed,
        and an application without references costs one query.

        Raises:
            UnresolvedReferenceError: A reference no longer resolves; the
                deployment stops here, with what served before untouched.
            SecretProviderError: A secret manager could not give a secret.
        """
        plan = self._variables.refresh()
        if plan is not None and plan.changed:
            # What this deployment resolved is what its commands run with, and
            # what its log is scrubbed of: the recorder watches this mapping.
            self.env_vars = {
                **self.env_vars,
                **{name: plan.environment.values[name] for name in plan.changed},
            }

    def _write_env_file(self, values: Mapping[str, str]) -> None:
        """
        Replace the env file with these variables, minus the ones the unit sets.

        systemd lets ``EnvironmentFile=`` override ``Environment=``, the
        opposite of dotenv, which never overrode what the unit set. So the file
        must not carry PORT or NODE_ENV: a PORT=3000 copied from
        ``.env.example`` would move the second application on a server onto
        the first one's port.

        Args:
            values: The complete set of variables.
        """
        owned = self._unit_environment()
        kept = {key: value for key, value in values.items() if key not in owned}
        self._env_manager.write_env_file(self._env_file(), kept)

    def _settle_env_file(self) -> Path:
        """
        Make the env file what the unit about to be written expects.

        Two things can be wrong with it on a redeploy. The unit an earlier
        version wrote carried the variables inline, and the application ran
        with them: rewriting the unit without them would drop them, so they
        move into the file first, winning over what the file says, since theirs
        were the values in force. And the file may set PORT or NODE_ENV, which
        the new unit would let override its own. The file is only rewritten
        when one of the two applies.

        Returns:
            The env file the unit references.
        """
        env_file = self._env_file()
        owned = self._unit_environment()
        previous = self.store.get_service(self.app_name) if self.app_name else None
        inline = {
            key: value
            for key, value in (previous.environment if previous else {}).items()
            if key not in owned
        }
        current = self._env_manager.read_env_file(env_file)
        moved = sorted(key for key, value in inline.items() if current.get(key) != value)
        conflicting = sorted(key for key in owned if key in current and current[key] != owned[key])
        if not (moved or conflicting):
            return env_file

        if moved:
            self.logger.substep(f"Moved {', '.join(moved)} from the unit to {env_file}")
        if conflicting:
            self.logger.substep(
                f"Removed {', '.join(conflicting)} from {env_file}: the unit sets it"
            )
        self._write_env_file({**current, **inline})
        hand_over_file(
            env_file,
            user=self._service_account()[0],
            group=self._service_account()[1],
            mode=SECRET_MODE,
            fs=self.fs,
            logger=self.logger,
        )
        return env_file

    def _webserver_manager(self) -> NginxManager | ApacheManager:
        """
        Return the manager for the configured web server.

        Returns:
            An NginxManager or an ApacheManager.
        """
        if self.webserver == "nginx":
            return NginxManager(verbose=self.verbose)
        return ApacheManager(verbose=self.verbose)

    def _missing_dependencies_issue(self, missing: list[str]) -> str:
        """
        Say which of the programs a deployment needs are not installed.

        Args:
            missing: The names :attr:`SYSTEM_DEPS` lists that are not on this server.

        Returns:
            The line the pre-flight report shows. A type whose toolchain comes
            from the distribution (Go, Rust, Ruby) adds how to install it.
        """
        return f"Missing system dependencies: {', '.join(missing)}"

    def pre_flight_check(self) -> bool:
        """
        Validate the machine before anything is changed.

        Every check runs, so one command reports every problem instead of the
        first one. The checks themselves live in
        :mod:`noust.deployers.helpers.preflight`.

        Returns:
            True if all checks pass.

        Raises:
            DeploymentError: If any check fails, listing all of them.
        """
        self.logger.debug("Running pre-flight checks...")

        existing_app = self.store.get_app(self.domain) if self.domain else None
        issues: list[str] = []

        missing = preflight.missing_programs(self.runner, self.SYSTEM_DEPS)
        if missing:
            issues.append(self._missing_dependencies_issue(missing))

        if self.source:
            issues += preflight.repository_unreachable(self.runner, self.source)

        issues += preflight.insufficient_disk_space(self.config.apps_directory)
        issues += preflight.over_disk_limit(existing_app, self.runner)

        if self.port:
            issues += preflight.port_taken(
                self.port,
                allowed_owner_port=existing_app.port if existing_app else None,
            )

        issues += preflight.webserver_down(self._webserver_manager(), self.webserver)

        if issues:
            details = "\n".join(f"  - {issue}" for issue in issues)
            raise DeploymentError(
                "Pre-flight checks failed", details=f"The following issues were found:\n{details}"
            )

        self.logger.debug("All pre-flight checks passed")
        return True
