# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Keeping a Compose project alive with Docker, where a server keeps it alive with a unit.

On a server the unit of a stack (``docker-compose.service``) is what starts it, stops it, reports its
state and its journal, and every deployer, the health gate and the lifecycle ask it through
:class:`~noust.managers.service_manager.ServiceManager`. The central of a NAS has no systemd. What keeps
a stack alive there is Docker, through the restart policy its containers carry, and what the unit did
besides is a handful of ``docker compose`` commands, which this class runs through the same deployer a
stack is updated with (``stack_deployer``: the compose file, the project, the profiles and the arguments
the operator chose are read in one place and never a second time):

- **start** is ``up -d``, **restart** recreates the containers (the current environment is picked up,
  as a unit's restart did), and **stop** is Compose's ``stop``: the containers stay, so starting again is
  quick and nothing is lost, which a unit's stop (``down``) never allowed;
- **state** is read from the containers and shaped like a unit's, so what decides an application's state
  reads it without knowing which engine it came from: running is active, a container Docker keeps
  restarting is the crash loop a unit reports, one that died or is unhealthy is failed, none at all is
  stopped;
- **the log** is what the containers printed, masked like every other reading of it;
- there is **no unit to write** and none to read, and a stack that brings its own compose file is not
  rewritten: the file and the profiles are in ``app_compose``, and what would not come back after a
  reboot is what the Docker zone says.

It answers what a ``ServiceManager`` answers for a stack or a container application and nothing else. Any
other public method of a service manager (a timer, a drop-in, the journal of a unit, a resource limit)
is refused in a sentence that says this machine runs no systemd, never run and never reported as an
``AttributeError`` found at the first use: a call that cannot be honoured says so.

It is built by :func:`noust.managers.supervision.service_control` and by nothing else.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any

from noust.core import app_compose_store
from noust.core.docker import reach
from noust.core.exceptions import NoustError, ServiceError
from noust.core.runner import CommandResult, CommandRunner, get_runner
from noust.core.store import App, AppType, get_store
from noust.core.utils import domain_to_app_name
from noust.deployers.docker_compose import container_problem, parse_compose_ps, parse_services
from noust.deployers.helpers import edge as edges
from noust.deployers.helpers.compose_ports import web_root_service

if TYPE_CHECKING:
    from noust.deployers.docker_compose import DockerComposeDeployer

__all__ = ["ComposeSupervisor", "remember_options"]

#: What a service manager answers about a stack's state, keyed as a unit's status is.
_ACTIVE = ("active", "running")
_STOPPED = ("inactive", "dead")
_CRASH_LOOP = ("activating", "auto-restart")
_FAILED = ("failed", "failed")

#: The applications whose stack Docker keeps alive here.
_STACK_TYPES = (AppType.DOCKER_COMPOSE.value, AppType.CONTAINER.value)


class ComposeSupervisor:
    """
    The service manager of a machine without systemd, for what runs in Docker.

    Attributes:
        verbose: Verbosity of the deployer each call is made through.
    """

    def __init__(self, *, verbose: bool = False, runner: CommandRunner | None = None) -> None:
        """
        Args:
            verbose: Verbosity of the deployer each call is made through.
            runner: Runs Docker; the process-wide one by default.
        """
        self.verbose = verbose
        self._runner = runner

    @property
    def runner(self) -> CommandRunner:
        """The runner Docker is asked through."""
        return self._runner or get_runner()

    # -- Finding the stack ----------------------------------------------------

    def _app(self, name: str) -> App | None:
        """
        Find the application a unit's name stands for.

        Args:
            name: The application's name (``domain_to_app_name``), which is what a unit is called.

        Returns:
            Its row, or None when no application of a type that runs in Docker has that name.
        """
        for app in get_store().list_apps():
            if app.app_type in _STACK_TYPES and domain_to_app_name(app.domain) == name:
                return app
        return None

    def _stack(self, app: App) -> DockerComposeDeployer:
        """
        Args:
            app: The application.

        Returns:
            The deployer that addresses its stack, built the one way every reader of a stack is.
        """
        from noust.deployers.docker_compose import stack_deployer

        return stack_deployer(app, runner=self.runner, verbose=self.verbose)

    def _compose(self, app: App, *args: str, timeout: int | None = None) -> CommandResult:
        """
        Run one ``docker compose`` command against the application's stack.

        Args:
            app: The application.
            *args: The subcommand and its arguments.
            timeout: Deadline in seconds; the stack's own compose timeout by default.

        Returns:
            The outcome.
        """
        stack = self._stack(app)
        stack._locate_compose_file()
        if timeout is None:
            return stack._run(stack._compose(*args))
        return stack._run(stack._compose(*args), timeout=timeout)

    def _require(self, name: str, operation: str) -> App:
        """
        Args:
            name: The application's name.
            operation: What is being asked, for the message.

        Returns:
            The application.

        Raises:
            ServiceError: When there is no such application here.
        """
        app = self._app(name)
        if app is None:
            raise ServiceError(
                f"Cannot {operation} {name}: no application of that name runs in Docker here",
                details="Run 'noust list' to see what is deployed.",
            )
        return app

    # -- What a unit's manager does for a stack -------------------------------

    def start(self, name: str) -> None:
        """
        Bring the stack up, as its unit's ``ExecStart`` did.

        Args:
            name: The application's name.

        Raises:
            ServiceError: There is no such application, or Compose refused, with its own words.
        """
        app = self._require(name, "start")
        stack = self._stack(app)
        result = self._compose(
            app,
            "up",
            "-d",
            "--remove-orphans",
            *stack.compose_up_args,
            *stack.compose_services,
            timeout=stack.build_timeout(),
        )
        if not result.success:
            raise self._failed(f"Failed to start {name}", result.stderr)

    def _failed(self, what: str, said: str) -> ServiceError:
        """
        Make the error of a ``docker compose up`` that failed.

        A port of the machine that something else uses is the failure a NAS has most, and a container
        cannot see the machine's sockets, so it is found here, when Docker refuses it, and said with the
        port and another to try. Anything else is Docker's own words, verbatim.

        Args:
            what: What was being done, for the message.
            said: Docker's error output.

        Returns:
            The error to raise.
        """
        taken = edges.port_taken(said)
        if taken is None:
            return ServiceError(what, details=said)
        other = edges.propose_port(taken, self.runner)
        proposal = f"Try {other}: " if other else ""
        return ServiceError(
            f"Docker could not open port {taken} of this machine: something else uses it",
            details=(
                f"{proposal}create the application again with --port, or free the port on the "
                f"machine (the NAS's own services and other containers use ports Noust cannot "
                f"see). Docker said: {said.strip()}"
            ),
        )

    def stop(self, name: str) -> bool:
        """
        Stop the stack's containers and keep them.

        Args:
            name: The application's name.

        Returns:
            True when they are stopped afterwards, including when there was nothing to stop: the
            teardown paths call this before knowing whether the stack was ever there.
        """
        app = self._app(name)
        if app is None:
            return True
        return self._compose(app, "stop").success

    def restart(self, name: str) -> None:
        """
        Recreate the stack's containers, so that its current environment and files are picked up.

        Args:
            name: The application's name.

        Raises:
            ServiceError: There is no such application, or Compose refused, with its own words.
        """
        app = self._require(name, "restart")
        stack = self._stack(app)
        result = self._compose(
            app,
            "up",
            "-d",
            "--force-recreate",
            "--remove-orphans",
            *stack.compose_up_args,
            *stack.compose_services,
            timeout=stack.build_timeout(),
        )
        if not result.success:
            raise self._failed(f"Failed to restart {name}", result.stderr)

    def delete_service(self, name: str, *, keep_record: bool = False) -> None:
        """
        Take the project down, as removing its unit stopped it.

        Args:
            name: The application's name; one that is not there is nothing to remove.
            keep_record: Ignored: there is no record of a unit to keep.
        """
        app = self._app(name)
        if app is not None:
            self._compose(app, "down", "--remove-orphans")

    def create_service(self, **_: Any) -> None:
        """
        Write nothing: there is no unit. The restart policy of the containers is what keeps them up.
        """
        return None

    def get_service_config(self, name: str) -> str | None:
        """
        Returns:
            None: a unit's text is where a stack's file and profiles used to be recorded, and they
            are in ``app_compose`` here.
        """
        return None

    def serving_units(self, app: App) -> list[str]:
        """
        Args:
            app: The application.

        Returns:
            Its one name: a stack is not an application of several instances.
        """
        return [domain_to_app_name(app.domain)]

    def app_units(self, app: App, *, extra: object = None) -> list[str]:
        """
        Args:
            app: The application.
            extra: Ignored.

        Returns:
            The same one name.
        """
        return self.serving_units(app)

    def get_status(self, name: str, *, require_managed: bool = True) -> dict[str, Any]:
        """
        Read the stack's state from its containers, in the shape a unit's status has.

        Args:
            name: The application's name.
            require_managed: Ignored: what Docker runs for an application is Noust's.

        Returns:
            The status; an application that is not here does not exist and is not active.
        """
        app = self._app(name)
        if app is None:
            return self._status(name, exists=False, state=_STOPPED, managed=False)
        result = self._compose(app, "ps", "-a", "--format", "json")
        containers = parse_compose_ps(result.stdout) if result.success else []
        states = {str(item.get("State") or "").lower() for item in containers}
        if any(container_problem(item) is not None for item in containers):
            state = _CRASH_LOOP if "restarting" in states else _FAILED
        elif "running" in states:
            state = _ACTIVE
        else:
            state = _STOPPED
        return self._status(name, exists=True, state=state, managed=True)

    @staticmethod
    def _status(
        name: str, *, exists: bool, state: tuple[str, str], managed: bool
    ) -> dict[str, Any]:
        """
        Args:
            name: The application's name.
            exists: Whether the application is here.
            state: ``(active_state, sub_state)``, as a unit's are.
            managed: Whether Noust manages it.

        Returns:
            The keys a unit's status has, so that whatever reads one reads this.
        """
        return {
            "name": name,
            "exists": exists,
            "active": state == _ACTIVE,
            # Docker brings it back by its restart policy, so it is "enabled" in the sense a unit is.
            "enabled": exists,
            "pid": "",
            "memory": "",
            "uptime": "",
            "active_state": state[0],
            "sub_state": state[1],
            "restarts": "0",
            "result": "",
            "managed": managed,
            "scheduled": False,
            # What says the state is Docker, which judged the containers: a reader that would probe
            # 127.0.0.1 for the port leaves it (it is not where they answer from a container).
            "engine": "docker",
        }

    def logs(self, name: str, lines: int = 50) -> str:
        """
        Read what the stack's containers printed, with its secrets masked.

        Args:
            name: The application's name.
            lines: How many lines of each container.

        Returns:
            The text, or the reason it cannot be shown.
        """
        app = self._app(name)
        if app is None:
            return f"No application named {name} runs in Docker here."
        try:
            return self._stack(app).container_logs(lines)[1]
        except NoustError as exc:
            return f"(the output could not be read: {exc})"

    def address_of(self, name: str) -> str | None:
        """
        Say where the application answers, seen from Noust, which is in a container of its own.

        What the health gate asks once it has restarted the stack: the service the site would
        proxy to, at its address on a Docker network Noust has joined (see
        :mod:`noust.core.docker.reach`) and on the port it listens on, not the one published on the
        machine, which is a port of the NAS and may be taken by something else there.

        Args:
            name: The application's name.

        Returns:
            ``address:port``; None when Noust shares the machine's network and 127.0.0.1 is the
            machine's.

        Raises:
            ServiceError: There is no such application, it publishes no port, or its service has no
                container to ask.
            DockerError: The address or the port cannot be found, with what Docker said.
        """
        app = self._require(name, "reach")
        stack = self._stack(app)
        stack._locate_compose_file()
        root = web_root_service(parse_services(stack._load_compose_document()))
        if root is None:
            raise ServiceError(f"{name} publishes no port, so there is nothing to ask over HTTP")
        listed = stack._run(stack._compose("ps", "-q", root))
        containers = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
        if not containers:
            raise ServiceError(
                f"The service {root} of {name} has no container to ask",
                details=listed.stderr or "Compose lists none: it was not started, or it exited.",
            )
        host = reach.service_address(self.runner, containers[0], me=reach.own_container_id())
        if host is None:
            return None
        return f"{host}:{reach.container_port(self.runner, containers[0], app.port)}"

    def service_exists(self, name: str) -> bool:
        """
        Args:
            name: The application's name.

        Returns:
            Whether an application of that name runs in Docker here.
        """
        return self._app(name) is not None

    unit_exists = service_exists
    is_managed = service_exists

    def enable(self, name: str) -> bool:
        """
        Returns:
            True: what starts it at boot is its containers' restart policy, which is in its file.
        """
        return True

    def disable(self, name: str) -> bool:
        """
        Returns:
            True: see :meth:`enable`; stopping it keeps Docker from bringing it back (``unless-stopped``).
        """
        return True

    def __getattr__(self, name: str) -> Any:
        """
        Refuse, in a sentence, what only systemd can do.

        Args:
            name: An attribute that is not defined here.

        Returns:
            Never, for a public method of a service manager: the helpers that touch no unit (the
            static ones) are the manager's own. Anything else raises.

        Raises:
            ServiceError: The name is a public method of a service manager, which needs systemd.
            AttributeError: It is not a name a service manager has.
        """
        from noust.managers.service_manager import ServiceManager

        declared = inspect.getattr_static(ServiceManager, name, None)
        if name.startswith("_") or declared is None:
            raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}")
        if isinstance(declared, staticmethod | classmethod):
            return getattr(ServiceManager, name)
        if not callable(declared):
            raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}")

        def refuse(*args: Any, **kwargs: Any) -> Any:
            raise ServiceError(
                f"{name} needs systemd, which this machine does not run",
                details=(
                    "This central deploys containers on its machine's Docker, which keeps them "
                    "running with its restart policy: units, timers and journals are not part of "
                    "that. What it does is start, stop, restart, delete, state and logs."
                ),
            )

        return refuse


def remember_options(deployer: DockerComposeDeployer) -> None:
    """
    Record the compose file and the profiles a stack was deployed with, where a unit would have.

    A stack that brings its own document is not rewritten, and what it runs with has to be found again
    by the update, the start and the log: the file and the profiles are written to ``app_compose`` (the
    row ``noust app compose`` edits), once and only when they say something the stack does not already
    say, so a deployment that changes nothing leaves no trace of it.

    Args:
        deployer: The deployer of a stack whose application is registered and whose compose file has
            been found.
    """
    if deployer.compose_path is None:
        return
    wanted = app_compose_store.check_options(
        compose_file=str(deployer.compose_path.relative_to(deployer.app_path)),
        profiles=list(deployer.compose_profiles),
    )
    current = app_compose_store.get_options(deployer.domain, store=deployer.store)
    if wanted["compose_file"] == current.compose_file and tuple(wanted["profiles"]) == tuple(
        current.profiles
    ):
        return
    app_compose_store.set_options(deployer.domain, wanted, actor="deploy", store=deployer.store)
