# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
PHP deployer for Noust: an application served by nginx through its own PHP-FPM pool.

A PHP application runs no process of its own, so it has no unit. What runs it
is a pool in the distribution's PHP-FPM (``/etc/php/<v>/fpm/pool.d`` on
Debian, ``/etc/php-fpm.d`` on Fedora), written by this deployer from
``templates/php/pool.conf.j2``: its own socket, the service user, small
worker counts, and the application's ``.env`` as ``env[...]`` lines. The site
is ``templates/nginx/fastcgi.conf.j2``, whose root goes through ``current``
on releases and hands PHP ``$realpath_root``, so switching ``current`` is
atomic for every request after it.

Activation reloads FPM (which also drops the opcache of the release that
served before) and probes the pool over FastCGI with the application's health
check; see :mod:`noust.deployers.helpers.php_fpm`.

Settings a recipe gives (the web root, the paths nginx refuses, the upload
size, the directories moved out of the release into ``shared/``) are kept in
``<app>/.wasm-php.json``, so a later update, a domain change re-rendering the
site or a rollback use the same ones. The file is validated every time it is
read: in place it lives in a tree the service user owns.

Apache is refused: the Apache equivalent needs ``mod_proxy_fcgi`` and a site
template of its own, which Noust does not ship.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar

from noust.core.applock import app_lock
from noust.core.config import Config
from noust.core.exceptions import DeploymentError, ValidationError
from noust.core.fs import FileSystem, get_fs
from noust.core.logger import Icons, Logger
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import App
from noust.deployers.base import INSTALL_TIMEOUT, BaseDeployer
from noust.deployers.helpers.env_manager import EnvManager
from noust.deployers.helpers.health import failure_output
from noust.deployers.helpers.health_gate import HealthCheck, HealthGate
from noust.deployers.helpers.layout import app_root, code_path_for, env_file_for
from noust.deployers.helpers.php_fpm import (
    DEFAULT_MAX_CHILDREN,
    PHP_FPM_TYPE,
    FpmInstallation,
    FpmService,
    PoolSpec,
    fastcgi_params,
    fastcgi_probe,
    find_fpm,
    nginx_worker_group,
    pool_tmp_dir,
    prepare_tmp_dir,
    render_pool,
    socket_accepts,
    validate_size,
)
from noust.deployers.helpers.release_build import stage_release
from noust.deployers.interface import StepReporter, UpdateResult
from noust.deployers.pipeline import DeployStep
from noust.deployers.registry import DeployerRegistry
from noust.deployers.releases import first_obstacle, persistent_path

#: Where an application's PHP settings are kept, in its directory.
PHP_SETTINGS_FILE = ".wasm-php.json"

#: Largest request body when nothing says otherwise.
DEFAULT_MAX_UPLOAD = "64m"

#: Most paths a settings file may refuse or move to ``shared/``: in place
#: the file sits in a tree the service user owns, so it is read as untrusted.
MAX_DENY_PATHS = 64
MAX_SHARED_FROM_RELEASE = 16

#: Mode of the settings file: root writes it, nginx's and PHP's users need
#: nothing from it.
PHP_SETTINGS_MODE = 0o644

#: What a seeded file in ``shared/`` is created with: the service user reads
#: it (a wp-config.php), nobody else does.
SEEDED_FILE_MODE = 0o640

#: The filesystem root FPM is looked for under. Tests point it elsewhere.
FPM_ROOT = Path("/")

#: Most workers an application's task limit can give its pool: each one is a
#: PHP process holding its own memory_limit.
MAX_POOL_CHILDREN = 64

#: How long a state probe gives the pool to answer the health check: long
#: enough for a WordPress page, short enough for ``noust list``.
STATE_PROBE_WITHIN = 5.0

#: FPM states in which it serves requests.
_FPM_UP = frozenset({"active", "reloading"})

#: One path segment of a web root or a refused path.
_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")

#: What composer is installed as, per package manager.
COMPOSER_HINT = (
    "Install Composer: apt install composer; dnf install composer; zypper install php-composer2"
)


def _relative(value: str, *, field_name: str, allow_dot: bool = False) -> str:
    """
    Check a path, relative to the application, that ends up in nginx's configuration.

    Args:
        value: The candidate.
        field_name: What it is, for the message.
        allow_dot: Accept ``.`` for the application root itself.

    Returns:
        The path, normalised to forward slashes without surrounding ones.

    Raises:
        ValidationError: When it is absolute, climbs, or holds anything but
            letters, digits, ``.``, ``_`` and ``-`` in its segments.
    """
    if allow_dot and value in (".", "./", ""):
        return "."
    parts = [part for part in str(value).strip("/").split("/") if part not in ("", ".")]
    if (
        not parts
        or str(value).startswith("/")
        or any(part == ".." or not _SEGMENT.match(part) for part in parts)
    ):
        raise ValidationError(
            f"Invalid {field_name}: {value!r}",
            details="Use a path relative to the application, of letters, digits, '.', '_' "
            "and '-', without '..'.",
        )
    return "/".join(parts)


@dataclass(frozen=True)
class PhpSettings:
    """
    How a PHP application is served, beyond what every application has.

    Attributes:
        webroot: The directory nginx serves, relative to the application:
            ``.`` for WordPress, ``public`` for Laravel. None detects it from
            the tree at each deploy (``public/index.php`` means ``public``).
        deny: Paths, relative to the web root, nginx refuses outright
            (``wp-config.php``).
        max_upload: Largest request body, in nginx and PHP alike.
        shared_from_release: Directories moved out of the first release into
            ``shared/`` and replaced by a link in every later one:
            ``wp-content``, which holds plugins and themes installed through
            the admin as well as uploads.
    """

    webroot: str | None = None
    deny: tuple[str, ...] = ()
    max_upload: str = DEFAULT_MAX_UPLOAD
    shared_from_release: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> PhpSettings:
        """
        Build validated settings from a mapping (a recipe, the settings file).

        Args:
            data: ``webroot``, ``deny``, ``max_upload``, ``shared_from_release``;
                anything missing takes its default.

        Returns:
            The settings.

        Raises:
            ValidationError: When a value is not acceptable.
        """
        webroot = data.get("webroot")
        deny = data.get("deny") or []
        shared = data.get("shared_from_release") or []
        for name, value, most in (
            ("deny", deny, MAX_DENY_PATHS),
            ("shared_from_release", shared, MAX_SHARED_FROM_RELEASE),
        ):
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValidationError(f"PHP {name} must be a list of paths", details=repr(value))
            if len(value) > most:
                raise ValidationError(
                    f"PHP {name} lists {len(value)} paths", details=f"At most {most} are accepted."
                )
        return cls(
            webroot=None
            if webroot is None
            else _relative(str(webroot), field_name="web root", allow_dot=True),
            deny=tuple(_relative(item, field_name="refused path") for item in deny),
            max_upload=validate_size(
                str(data.get("max_upload") or DEFAULT_MAX_UPLOAD), field_name="upload size"
            ),
            shared_from_release=tuple(
                str(persistent_path(_relative(item, field_name="shared directory")))
                for item in shared
            ),
        )

    def to_mapping(self) -> dict[str, Any]:
        """
        Say the settings as the settings file stores them.

        Returns:
            A JSON-serialisable mapping.
        """
        return {
            "webroot": self.webroot,
            "deny": list(self.deny),
            "max_upload": self.max_upload,
            "shared_from_release": list(self.shared_from_release),
        }


def load_php_settings(app_path: Path) -> PhpSettings:
    """
    Read an application's PHP settings.

    Args:
        app_path: The application directory.

    Returns:
        What the settings file says, or the defaults without one.

    Raises:
        ValidationError: When the file holds something unacceptable.
    """
    path = app_path / PHP_SETTINGS_FILE
    if path.is_symlink() or not path.is_file():
        return PhpSettings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValidationError(f"Cannot read {path}", details=str(exc)) from exc
    if not isinstance(data, dict):
        raise ValidationError(f"{path} does not hold PHP settings", details=repr(data))
    return PhpSettings.from_mapping(data)


def detect_webroot(tree: Path) -> str:
    """
    Pick the web root of a PHP tree that does not say.

    Args:
        tree: The code.

    Returns:
        ``public`` when ``public/index.php`` exists (Laravel, Symfony), ``.``
        otherwise.
    """
    return "public" if (tree / "public" / "index.php").is_file() else "."


def fpm_service(
    *, runner: CommandRunner | None = None, fs: FileSystem | None = None, logger: Logger
) -> FpmService:
    """
    Find this machine's PHP-FPM and return its controller.

    Args:
        runner: Where commands run. Defaults to the process-wide runner.
        fs: Where files are written. Defaults to the process-wide one.
        logger: Where progress is reported.

    Returns:
        The controller.

    Raises:
        DeploymentError: When PHP-FPM is not installed.
    """
    from noust.core.fs import get_fs

    return FpmService(
        find_fpm(FPM_ROOT),
        runner=runner or get_runner(),
        fs=fs or get_fs(),
        logger=logger,
    )


def build_gate(
    *,
    fpm: FpmService,
    app_name: str,
    domain: str,
    document_root: Path,
    https: bool,
    check: HealthCheck,
    logger: Logger,
) -> HealthGate:
    """
    Build the gate a PHP application passes: reload FPM, then ask the pool.

    Args:
        fpm: The FPM that runs the pool.
        app_name: The application name, which names the pool.
        domain: The primary domain, sent as the Host.
        document_root: The directory nginx serves, through ``current``.
        https: Whether the site is served over TLS.
        check: The application's health check.
        logger: Where the probes are reported.

    Returns:
        The gate. Its evidence is the probes, PHP's own errors among them,
        and FPM's journal.
    """
    installation = fpm.installation
    socket_path = installation.socket(app_name)
    return HealthGate(
        unit=installation.service,
        url=f"unix:{socket_path} {check.path}",
        check=check,
        services=fpm,
        logger=logger,
        probe=fastcgi_probe(
            socket_path,
            lambda: fastcgi_params(
                document_root=document_root, path=check.path, domain=domain, https=https
            ),
        ),
    )


def document_root_for(app: App) -> Path:
    """
    Say what nginx serves for a deployed PHP application, through ``current``.

    Args:
        app: The application's row.

    Returns:
        The running code, or its web root inside it.

    Raises:
        ValidationError: When the settings file holds something unacceptable.
    """
    settings = load_php_settings(app_root(app))
    code = code_path_for(app)
    webroot = settings.webroot or detect_webroot(code)
    return code if webroot == "." else code / webroot


def pool_children(tasks_max: int | None) -> int:
    """
    Say how many workers a pool may grow to under an application's task limit.

    A pool's workers are its processes, which is what ``TasksMax`` limits for
    an application that runs as a unit.

    Args:
        tasks_max: The application's task limit, or None.

    Returns:
        :data:`DEFAULT_MAX_CHILDREN` without one, the limit otherwise, within
        1 and :data:`MAX_POOL_CHILDREN`.
    """
    if tasks_max is None:
        return DEFAULT_MAX_CHILDREN
    return max(1, min(tasks_max, MAX_POOL_CHILDREN))


def pool_spec_for(
    *,
    app_name: str,
    domain: str,
    app_path: Path,
    env: Mapping[str, str],
    installation: FpmInstallation,
    config: Config,
    max_upload: str,
    memory_max_mb: int | None,
    tasks_max: int | None,
) -> PoolSpec:
    """
    Describe an application's pool: the one description a deploy and a limits change share.

    Args:
        app_name: The application name.
        domain: Its domain.
        app_path: Its directory.
        env: Its environment, as the ``.env`` says now.
        installation: The FPM it runs in.
        config: Where the service user comes from.
        max_upload: Largest request body.
        memory_max_mb: Its memory limit, which the workers share.
        tasks_max: Its task limit, which bounds the workers.

    Returns:
        The pool: one per account, run as the application's own when it has
        one (3.2), the shared service account otherwise.
    """
    from noust.managers.app_identity import service_account_for

    user, group = service_account_for(domain, config)
    return PoolSpec(
        app_name=app_name,
        domain=domain,
        user=user,
        group=group,
        # The socket stays the web server's to connect to, whoever the
        # workers run as.
        listen_group=nginx_worker_group(config.service_group, root=FPM_ROOT),
        socket=installation.socket(app_name),
        root=app_path,
        tmp_dir=pool_tmp_dir(app_path),
        env=env,
        max_upload=max_upload,
        memory_max_mb=memory_max_mb,
        max_children=pool_children(tasks_max),
    )


def health_gate_for_app(app: App, log: Logger) -> HealthGate:
    """
    Build the gate for a deployed PHP application from its row alone.

    What an activation outside a deploy (a rollback, a migration) uses, so it
    judges a release exactly as the deploy did.

    Args:
        app: The application's row.
        log: Where the probes are reported.

    Returns:
        The gate.

    Raises:
        DeploymentError: When PHP-FPM is not installed.
    """
    return build_gate(
        fpm=fpm_service(logger=log),
        app_name=app_root(app).name,
        domain=app.domain,
        document_root=document_root_for(app),
        https=bool(app.ssl_enabled),
        check=HealthCheck.for_app(app),
        logger=log,
    )


def remove_pool_of(app_path: Path, log: Logger) -> bool:
    """
    Remove a deleted application's pool and temporary files, so FPM stops running it.

    Args:
        app_path: The application directory, which names the pool.
        log: Where the removal is reported.

    Returns:
        Whether there was a pool to remove. False, too, when PHP-FPM is not
        installed: then there is no pool either.

    Raises:
        DeploymentError: When FPM did not reload after the removal.
    """
    from noust.core.fs import get_fs

    tmp = pool_tmp_dir(app_path)
    if tmp.is_dir() and not tmp.is_symlink():
        get_fs().remove_tree(tmp)
    try:
        fpm = fpm_service(logger=log)
    except DeploymentError:
        return False
    path = fpm.installation.pool_file(app_path.name)
    removed = fpm.remove_pool(path)
    if removed:
        log.substep(f"Removed the PHP-FPM pool {path}")
    return removed


# The running application ------------------------------------------------------


@dataclass(frozen=True)
class PoolReport:
    """
    What is true about a PHP application's pool right now.

    Attributes:
        service: The FPM master's unit.
        binary: The FPM binary, which tests the configuration.
        service_state: What systemd says it is doing (``active``, ``failed``...).
        pool_file: Where the pool is written.
        enabled: Whether the pool file is in place, so FPM runs it.
        disabled: Whether a stop moved it aside.
        socket: Where the pool listens.
        socket_exists: Whether the socket is there.
        answered: Whether the pool answered the health check over FastCGI;
            None when it was not asked.
        detail: Why it did not answer, verbatim from the probe.
    """

    service: str
    binary: str
    service_state: str
    pool_file: Path
    enabled: bool
    disabled: bool
    socket: Path
    socket_exists: bool
    answered: bool | None = None
    detail: str = ""

    @property
    def fpm_up(self) -> bool:
        """Whether the FPM master serves requests."""
        return self.service_state in _FPM_UP


def inspect_pool(
    app: App, *, probe: bool = True, runner: CommandRunner | None = None
) -> PoolReport:
    """
    Read a PHP application's pool, and ask it the health check.

    The probe is the gate's (:func:`health_gate_for_app`): the same socket,
    path, expectation and document root, one attempt.

    Args:
        app: The application's row.
        probe: Ask the pool over FastCGI; otherwise only files and systemd.
        runner: Where systemctl runs. Defaults to the process-wide runner.

    Returns:
        The report.

    Raises:
        DeploymentError: When PHP-FPM is not installed.
        ValidationError: When the settings file holds something unacceptable.
    """
    fpm = fpm_service(runner=runner, logger=Logger(verbose=False))
    installation = fpm.installation
    name = app_root(app).name
    pool = installation.pool_file(name)
    socket_path = installation.socket(name)
    state = fpm.state()
    answered: bool | None = None
    failures: list[str] = []
    if probe and pool.is_file() and state in _FPM_UP:
        check = HealthCheck.for_app(app)
        document_root = document_root_for(app)
        ask = fastcgi_probe(
            socket_path,
            lambda: fastcgi_params(
                document_root=document_root,
                path=check.path,
                domain=app.domain,
                https=bool(app.ssl_enabled),
            ),
        )
        answered = ask(
            "",
            retries=1,
            delay=0.0,
            on_attempt=failures.append,
            accept=check.accepts,
            within=STATE_PROBE_WITHIN,
        )
    return PoolReport(
        service=installation.service,
        binary=installation.binary,
        service_state=state,
        pool_file=pool,
        enabled=pool.is_file(),
        disabled=installation.disabled_pool_file(name).is_file(),
        socket=socket_path,
        socket_exists=socket_path.exists(),
        answered=answered,
        detail=failures[-1] if failures else "",
    )


def pool_serving(app: App) -> str:
    """
    Sort a PHP application into the machine snapshot's buckets, cheaply.

    The snapshot runs on a five-second timer and asks systemd nothing per
    application, so neither does this: the pool file, and whether its socket
    accepts a connection (it does only while FPM runs the pool).

    Args:
        app: The application's row.

    Returns:
        ``"active"``, ``"stopped"`` (a stop moved the pool aside) or
        ``"failed"``.
    """
    try:
        installation = find_fpm(FPM_ROOT)
    except DeploymentError:
        return "failed"
    name = app_root(app).name
    if installation.pool_file(name).is_file():
        return "active" if socket_accepts(installation.socket(name)) else "failed"
    return "stopped" if installation.disabled_pool_file(name).is_file() else "failed"


#: What each service action does to a pool, in the words the callers print.
POOL_ACTIONS = ("start", "stop", "restart")


def control_pool(app: App, action: str, *, logger: Logger | None = None) -> str:
    """
    Start, stop or restart a PHP application: the one place its pool is controlled.

    The pool is the application's, FPM is every PHP application's, so:

    - ``restart`` reloads FPM. A reload restarts every pool's workers
      gracefully (requests in flight finish), which is the only way FPM
      restarts one pool; the other PHP sites keep answering.
    - ``stop`` moves the pool file aside and reloads, so its workers and
      socket go and nginx answers 502 for it; nothing else is touched.
    - ``start`` puts it back, has FPM test it, and reloads (starting FPM
      when it is not running).

    Args:
        app: The application's row.
        action: ``start``, ``stop`` or ``restart``.
        logger: Where progress is reported.

    Returns:
        What was done, in a sentence.

    Raises:
        ValueError: When the action is not one of :data:`POOL_ACTIONS`.
        DeploymentError: When PHP-FPM is not installed, there is no pool,
            or FPM did not reload.
        ValidationError: When FPM refuses the pool on start.
        AppBusyError: Another operation is running on the application.
    """
    if action not in POOL_ACTIONS:
        raise ValueError(f"not a pool action: {action!r}")
    log = logger if logger is not None else Logger(verbose=False)
    fpm = fpm_service(logger=log)
    name = app_root(app).name
    path = fpm.installation.pool_file(name)
    service = fpm.installation.service
    # A deploy rewriting the pool while it moves would leave whichever
    # finished last.
    with app_lock(app.domain, f"PHP-FPM pool {action}"):
        if action == "restart":
            if not path.is_file():
                raise DeploymentError(
                    f"{app.domain} is stopped; there is no pool to restart",
                    details=f"Start it with: noust start {app.domain}",
                )
            fpm.reload()
            return f"Reloaded {service}: the workers of every PHP pool restarted gracefully"
        if action == "stop":
            if fpm.disable_pool(path):
                return f"Disabled the pool {path.name} and reloaded {service}"
            return f"The pool of {app.domain} was already disabled"
        if fpm.enable_pool(path):
            return f"Enabled the pool {path.name} and reloaded {service}"
        return f"The pool of {app.domain} was already enabled; reloaded {service}"


def set_pool_limits(
    app: App, *, memory_max_mb: int | None, tasks_max: int | None, logger: Logger
) -> str:
    """
    Rewrite a PHP application's pool for new limits, behind its health gate.

    The memory limit is shared by the workers (each gets its share as
    ``memory_limit``) and the task limit bounds them (``pm.max_children``).
    FPM tests the new pool before it is kept, and the reload that applies it
    is the gate's restart: when the pool does not answer under the new
    limits, the previous pool is put back and reloaded.

    Args:
        app: The application's row.
        memory_max_mb: The new memory limit, or None for the default.
        tasks_max: The new task limit, or None for the default.
        logger: Where the reload and the probes are reported.

    Returns:
        The FPM service that was reloaded.

    Raises:
        DeploymentError: When PHP-FPM is not installed, the application is
            stopped, or it did not answer under the new limits (the previous
            pool is back).
        ValidationError: When FPM refuses the new pool (the previous one is
            back).
    """
    fpm = fpm_service(logger=logger)
    installation = fpm.installation
    root = app_root(app)
    path = installation.pool_file(root.name)
    if not path.is_file():
        raise DeploymentError(
            f"{app.domain} is stopped; its pool is not running to be limited",
            details=f"Start it first: noust start {app.domain}",
        )
    previous = path.read_text(encoding="utf-8")
    env_file = env_file_for(app)
    spec = pool_spec_for(
        app_name=root.name,
        domain=app.domain,
        app_path=root,
        env=EnvManager(verbose=False).read_env_file(env_file) if env_file.is_file() else {},
        installation=installation,
        config=Config(),
        max_upload=load_php_settings(root).max_upload,
        memory_max_mb=memory_max_mb,
        tasks_max=tasks_max,
    )
    fpm.install_pool(path, render_pool(spec))
    gate = health_gate_for_app(app, logger)
    healthy, evidence = gate.restart_and_probe()
    if not healthy:
        fpm.install_pool(path, previous)
        restored, _ = gate.restart_and_probe()
        state = "answering again" if restored else "back, but it is not answering either"
        raise DeploymentError(
            f"{app.domain} did not answer under the new limits; the previous pool is {state}",
            details=evidence,
        )
    return installation.service


def rewrite_pool(app: App, *, logger: Logger) -> tuple[Path, str]:
    """
    Write a PHP application's pool again from its row, keeping its limits and settings.

    What moving an application to its own account rewrites (the pool's
    ``user`` and ``group``, and its temporary directory's owner); the caller
    restarts it behind its gate and puts the previous pool back when it does
    not answer.

    Args:
        app: The application's row, as it is to run.
        logger: Where FPM's reload is reported.

    Returns:
        The pool file and what it said before.

    Raises:
        DeploymentError: When PHP-FPM is not installed or the application is
            stopped.
        ValidationError: When FPM refuses the new pool (the previous one is
            back).
    """
    fpm = fpm_service(logger=logger)
    installation = fpm.installation
    root = app_root(app)
    path = installation.pool_file(root.name)
    if not path.is_file():
        raise DeploymentError(
            f"{app.domain} is stopped; its pool is not running to be rewritten",
            details=f"Start it first: noust start {app.domain}",
        )
    previous = path.read_text(encoding="utf-8")
    env_file = env_file_for(app)
    spec = pool_spec_for(
        app_name=root.name,
        domain=app.domain,
        app_path=root,
        env=EnvManager(verbose=False).read_env_file(env_file) if env_file.is_file() else {},
        installation=installation,
        config=Config(),
        max_upload=load_php_settings(root).max_upload,
        memory_max_mb=app.memory_max_mb,
        tasks_max=app.tasks_max,
    )
    prepare_tmp_dir(
        spec.tmp_dir, user=spec.user, group=spec.group, fs=get_fs(), runner=get_runner()
    )
    fpm.install_pool(path, render_pool(spec))
    return path, previous


class PhpFpmDeployer(BaseDeployer):
    """
    Deployer for PHP applications: WordPress, Laravel, Symfony, plain PHP.

    Detection sits between the frameworks and generic Node (priority 65): a
    tree with ``composer.json`` and a front controller (``index.php`` or
    ``public/index.php``) is PHP even when it also has a ``package.json`` for
    its assets, which is every Laravel project; a ``package.json`` without
    both is left to the Node deployers, and so is anything a Next.js or
    monorepo detector claims first. A root ``index.php`` alone is PHP.
    """

    APP_TYPE = PHP_FPM_TYPE
    DISPLAY_NAME = "PHP (PHP-FPM)"

    DETECTION_FILES: ClassVar[list[str]] = ["composer.json", "index.php"]

    DEFAULT_PORT = 80  # Not used: nginx talks to the pool's socket.

    DETECTION_PRIORITY = 65

    SYSTEM_DEPS: ClassVar[list[str]] = []

    def __init__(
        self,
        verbose: bool = False,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
    ):
        """
        Initialize the PHP deployer.

        Args:
            verbose: Enable verbose logging.
            runner: Command runner used for composer, FPM and systemctl.
                Defaults to the process-wide runner.
            fs: Filesystem every change goes through. Defaults to the
                process-wide one.
        """
        super().__init__(verbose=verbose, runner=runner, fs=fs)
        self.php = PhpSettings()
        self._seed_files: dict[str, str] = {}
        self._fpm: FpmService | None = None

    # Configuration ------------------------------------------------------

    def configure(self, domain: str, source: str, **options: Any) -> None:
        """
        Configure the deployer, reading the PHP settings as well.

        Args:
            domain: Target domain.
            source: Source URL or path.
            **options: Everything :meth:`BaseDeployer.configure` takes, plus
                ``php_webroot``, ``php_deny``, ``php_max_upload`` and
                ``php_shared_from_release`` (see :class:`PhpSettings`),
                and ``php_files`` (relative path to content, written into
                ``shared/`` once and linked into every release). What is not
                given comes from the settings file. A new application's health
                check is ``initial_health``, as for every type.

        Raises:
            ValidationError: When a PHP setting is not acceptable.
        """
        super().configure(domain, source, **options)
        given = {
            key: options[f"php_{key}"]
            for key in ("webroot", "deny", "max_upload", "shared_from_release")
            if options.get(f"php_{key}") is not None
        }
        stored = load_php_settings(self.app_path).to_mapping()
        self.php = PhpSettings.from_mapping({**stored, **given})
        files = options.get("php_files") or {}
        self._seed_files = {
            str(persistent_path(_relative(path, field_name="seeded file"))): str(content)
            for path, content in dict(files).items()
        }
        self._fpm = None

    # Detection and commands ---------------------------------------------

    def detect(self, path: Path) -> bool:
        """
        Tell whether a tree is a PHP application.

        Args:
            path: The fetched source.

        Returns:
            True for ``composer.json`` with a front controller, or for a
            root ``index.php`` or a lone ``composer.json`` in a tree no other
            runtime claims.
        """
        composer = (path / "composer.json").is_file()
        front = (path / "index.php").is_file() or (path / "public" / "index.php").is_file()
        if composer and front:
            return True
        others = ("package.json", "requirements.txt", "pyproject.toml", "go.mod", "Cargo.toml")
        if any((path / name).exists() for name in others):
            return False
        return composer or (path / "index.php").is_file()

    def get_install_command(self) -> list[str]:
        """
        Install the application's PHP dependencies, when it declares any.

        Returns:
            ``composer install`` without development packages, or nothing
            without a ``composer.json``.
        """
        if not (self.build_path / "composer.json").is_file():
            return []
        return [
            "composer",
            "install",
            "--no-dev",
            "--no-interaction",
            "--prefer-dist",
            "--optimize-autoloader",
        ]

    def get_build_command(self) -> list[str]:
        """
        Nothing to build: PHP runs the files it is given.

        Returns:
            An empty command.
        """
        return []

    def get_start_command(self) -> str:
        """
        No start command: the pool runs in PHP-FPM, not in a unit.

        Returns:
            An empty string.
        """
        return ""

    def get_nginx_template(self) -> str:
        """
        Serve through the FastCGI site.

        Returns:
            ``fastcgi``.
        """
        return "fastcgi"

    def get_apache_template(self) -> str:
        """
        Refuse Apache.

        Returns:
            Never.

        Raises:
            DeploymentError: Always.
        """
        raise self._apache_refusal()

    def _apache_refusal(self) -> DeploymentError:
        """
        Say why a PHP application cannot be served by Apache.

        Returns:
            The error.
        """
        return DeploymentError(
            "PHP applications are served by nginx only",
            details="Noust serves PHP through nginx and a PHP-FPM pool; it has no Apache "
            "template for it. Deploy with --webserver nginx.",
        )

    # Settings -------------------------------------------------------------

    @property
    def webroot(self) -> str:
        """The directory nginx serves, relative to the code."""
        return self.php.webroot or detect_webroot(self.build_path)

    def _document_root(self) -> Path:
        """
        Say what nginx's ``root`` is: the web root through ``current`` on releases.

        Returns:
            The directory.
        """
        return self.runtime_path if self.webroot == "." else self.runtime_path / self.webroot

    def _persist_settings(self) -> None:
        """Write the settings next to the application, for every later deploy."""
        path = self.app_path / PHP_SETTINGS_FILE
        if path.is_symlink():
            raise DeploymentError(
                f"Refusing to write {path}: it is a symlink",
                details="Remove the link; Noust writes this file itself.",
            )
        # Rewritten after the tree was handed over, so it is root's again
        # after every deploy; it is validated on every read regardless.
        self.fs.write_text(
            path, json.dumps(self.php.to_mapping(), indent=2) + "\n", mode=PHP_SETTINGS_MODE
        )

    def get_template_context(self) -> dict:
        """
        Get the site's template context.

        Returns:
            The common context, plus the document root, the pool's socket, the
            upload size and the paths nginx refuses.
        """
        context = super().get_template_context()
        if "document_root" not in context:
            # An advanced wasm.nginx.yaml context does not apply: the site is
            # always the FastCGI one.
            context = {
                "domain": self.domain,
                "server_names": self.domain,
                "port": self.port,
                "app_path": str(self.runtime_path),
                "app_name": self.app_name,
                "ssl": self.ssl,
            }
        context.update(
            {
                "document_root": str(self._document_root()),
                "fastcgi_socket": str(self._fpm_service().installation.socket(self.app_name)),
                "max_upload": self.php.max_upload,
                "deny_paths": list(self.php.deny),
            }
        )
        return context

    def _detect_nginx_config(self) -> None:
        """A PHP site is always the FastCGI one; a wasm.nginx.yaml is not read."""

    # FPM -----------------------------------------------------------------

    def _fpm_service(self) -> FpmService:
        """
        Return the controller of this machine's PHP-FPM, found once.

        Returns:
            The controller.

        Raises:
            DeploymentError: When PHP-FPM is not installed.
        """
        if self._fpm is None:
            self._fpm = fpm_service(runner=self.runner, fs=self.fs, logger=self.logger)
        return self._fpm

    def _pool_spec(self) -> PoolSpec:
        """
        Describe the application's pool from what is deployed now.

        Returns:
            The pool: the ``.env`` as it is on disk, the service user, and the
            application's memory and task limits.
        """
        env_file = self._env_file()
        env = self._env_manager.read_env_file(env_file) if env_file.is_file() else {}
        app = self._app_row()
        return pool_spec_for(
            app_name=self.app_name,
            domain=self.domain,
            app_path=self.app_path,
            env=env,
            installation=self._fpm_service().installation,
            config=self.config,
            max_upload=self.php.max_upload,
            memory_max_mb=app.memory_max_mb if app is not None else self.memory_max_mb,
            tasks_max=app.tasks_max if app is not None else self.tasks_max,
        )

    def write_pool(self) -> None:
        """
        Write the application's pool and settings, and reload FPM when the pool changed.

        Raises:
            ValidationError: When FPM refuses the pool; the previous one is
                back in place.
            DeploymentError: When FPM is missing or did not reload.
        """
        fpm = self._fpm_service()
        path = fpm.installation.pool_file(self.app_name)
        spec = self._pool_spec()
        content = render_pool(spec)
        prepare_tmp_dir(
            spec.tmp_dir, user=spec.user, group=spec.group, fs=self.fs, runner=self.runner
        )
        if fpm.install_pool(path, content):
            self.logger.substep(f"Pool: {path}")
        self._persist_settings()

    def remove_pool(self) -> None:
        """Remove the pool and temporary directory a failed first deploy wrote."""
        remove_pool_of(self.app_path, self.logger)

    @property
    def installation(self) -> FpmInstallation:
        """The PHP-FPM this application runs in."""
        return self._fpm_service().installation

    # Pipeline -------------------------------------------------------------

    def pre_flight_check(self) -> bool:
        """
        Validate the machine, and that it can run PHP, before anything changes.

        Returns:
            True when every check passes.

        Raises:
            DeploymentError: When a check fails.
        """
        if self.webserver != "nginx":
            raise self._apache_refusal()
        self._fpm_service()
        return super().pre_flight_check()

    def pre_install(self) -> bool:
        """
        Nothing to detect before composer: no Node package manager, no Prisma.

        Returns:
            True.
        """
        self.logger.debug(f"Web root: {self.webroot}")
        return True

    def install_dependencies(self) -> bool:
        """
        Run ``composer install`` when the application declares dependencies.

        Returns:
            True.

        Raises:
            DeploymentError: When composer is missing or fails.
        """
        self.pre_install()
        command = self.get_install_command()
        if not command:
            self.logger.substep("No composer.json: nothing to install")
            return True
        if not self.runner.exists("composer"):
            raise DeploymentError(
                "composer.json needs Composer, which is not installed", details=COMPOSER_HINT
            )
        self.logger.substep(f"Running: {' '.join(command)}")
        result = self._run(
            command,
            # The deploy runs as root, as npm's does; without this Composer
            # refuses to run the packages' scripts (Laravel's among them).
            env={"COMPOSER_ALLOW_SUPERUSER": "1", "COMPOSER_NO_INTERACTION": "1"},
            timeout=INSTALL_TIMEOUT,
            stream=True,
        )
        if not result.success:
            raise DeploymentError(
                "composer install failed", details=failure_output(result) or "No output."
            )
        return True

    def _install_release_dependencies(self) -> None:
        """Install into the new release; there is no ``vendor`` reuse yet."""
        self.install_dependencies()

    def _unit_environment(self) -> dict[str, str]:
        """
        Nothing is set by a unit: the whole environment goes to the pool.

        Returns:
            An empty mapping.
        """
        return {}

    def _persistent_paths(self) -> list[str]:
        """
        Return what every release shares, the seeded paths included.

        Returns:
            The configured or recorded paths, then the directories moved out
            of the release and the seeded files.
        """
        paths = super()._persistent_paths()
        for extra in (*self.php.shared_from_release, *self._seed_files):
            if extra not in paths:
                paths.append(extra)
        return paths

    def _step_fetch_release(self) -> None:
        """
        Stage the release, move what ``shared/`` keeps out of it, then link it.

        Raises:
            DeploymentError: When something to move or seed is a symlink, or
                ``shared/`` cannot hold it safely.
        """
        if self._staged is None:
            self.logger.substep(f"Source: {self.source}")
            self._staged = stage_release(
                self.source,
                self.branch,
                releases=self.releases,
                source_manager=self.source_manager,
                logger=self.logger,
            )
        self._seed_shared(self._staged.path)
        super()._step_fetch_release()

    def _seed_shared(self, release: Path) -> None:
        """
        Give ``shared/`` what it keeps for this application, from the release or the recipe.

        A directory in :attr:`PhpSettings.shared_from_release` is moved into
        ``shared/`` from the first release that has it, and removed from every
        later one so the link replaces it: WordPress keeps plugins, themes and
        uploads in ``wp-content``, and a new release must not bring back the
        stock copy. A seeded file is written once and never overwritten.

        Args:
            release: The staged release.

        Raises:
            DeploymentError: When a path is a symlink, or ``shared/`` has
                something other than a directory in the way.
        """
        shared = self.releases.shared_dir
        for raw in self.php.shared_from_release:
            relative = persistent_path(raw)
            in_release = release / relative
            target = shared / relative
            if in_release.is_symlink():
                raise DeploymentError(
                    f"{relative} is a symlink in the release",
                    details="Nothing is moved through a link found in a release; "
                    "remove it from the source.",
                )
            if os.path.lexists(target):
                if in_release.is_dir():
                    self.fs.remove_tree(in_release)
                elif os.path.lexists(in_release):
                    self.fs.remove(in_release)
                continue
            if not in_release.is_dir():
                continue
            self._require_plain_parents(shared, relative)
            self.fs.make_dir(target.parent, parents=True)
            self.fs.move(in_release, target)
            self.logger.substep(f"Moved {relative} into shared/{relative}")

        for raw, content in self._seed_files.items():
            relative = persistent_path(raw)
            target = shared / relative
            if os.path.lexists(target):
                continue
            self._require_plain_parents(shared, relative)
            self.fs.make_dir(target.parent, parents=True)
            self.fs.write_text(target, content, mode=SEEDED_FILE_MODE)
            self.logger.substep(f"Wrote shared/{relative}")

    def _require_plain_parents(self, shared: Path, relative: PurePosixPath) -> None:
        """
        Refuse to create something in ``shared/`` through a link or over a file.

        Args:
            shared: The shared directory.
            relative: The path about to be created in it.

        Raises:
            DeploymentError: When an ancestor is a symlink or a file.
        """
        if shared.is_symlink():
            raise DeploymentError(
                f"Refusing to write into {shared}: it is a symlink",
                details="Replace it with a directory.",
            )
        blocked = first_obstacle(shared, relative)
        if blocked is not None:
            raise DeploymentError(
                f"Refusing to create {shared / relative}: {blocked} is not a plain directory",
                details="A symlink or a file there would send the write elsewhere. Replace "
                "it with a directory.",
            )

    def _step_pool(self) -> None:
        """Write the pool, before the site that points nginx at its socket."""
        if not self.uses_releases and (self.php.shared_from_release or self._seed_files):
            raise DeploymentError(
                "Moving directories into shared/ needs the releases layout",
                details="Deploy this application with --layout releases.",
            )
        self.write_pool()

    def build_pipeline(self) -> list[DeployStep]:
        """
        Describe a PHP deployment: fetch, composer, pool, site, certificate, gate.

        Returns:
            The steps, each with the undo that reverses it.

        Raises:
            DeploymentError: When the web server is not nginx.
        """
        if self.webserver != "nginx":
            raise self._apache_refusal()
        pool = DeployStep(
            title="Configuring the PHP-FPM pool",
            icon=Icons.GEAR,
            run=self._step_pool,
            undo=self.remove_pool if self._is_new_deployment else None,
        )
        if self.uses_releases:
            steps = self._release_pipeline(
                [
                    DeployStep(
                        title="Installing dependencies",
                        icon=Icons.PACKAGE,
                        run=self._install_release_dependencies,
                    )
                ],
                with_service=False,
            )
            at = next(
                (i for i, step in enumerate(steps) if step.run == self._set_permissions),
                len(steps) - 1,
            )
            steps.insert(at + 1, pool)
            return steps
        return [
            DeployStep(
                title="Fetching source code",
                icon=Icons.DOWNLOAD,
                run=self._step_fetch,
                undo=self.remove_source,
            ),
            DeployStep(
                title="Installing dependencies",
                icon=Icons.PACKAGE,
                run=self.install_dependencies,
            ),
            DeployStep(title="Setting permissions", icon=Icons.LOCK, run=self._set_permissions),
            pool,
            DeployStep(
                title="Creating site configuration",
                icon=Icons.GLOBE,
                run=lambda: self.create_site(with_ssl=False),
                undo=self.remove_site,
            ),
            DeployStep(
                title="Obtaining SSL certificate",
                icon=Icons.LOCK,
                run=self._step_certificate,
                skip_if=lambda: not self.ssl,
            ),
            DeployStep(title="Verifying deployment", icon=Icons.ROCKET, run=self._step_start),
        ]

    def _activate_release(self) -> None:
        """Rewrite the pool from the current ``.env``, then activate behind the gate."""
        self.write_pool()
        super()._activate_release()

    def _update_in_place(self, report: StepReporter) -> UpdateResult:
        """
        Rebuild in place, rewrite the pool, and pass the gate.

        Args:
            report: Called as each step begins.

        Returns:
            What was done.

        Raises:
            DeploymentError: When the application does not pass its health check.
        """
        result = super()._update_in_place(report)
        report("Configuring the PHP-FPM pool")
        self.write_pool()
        report("Reloading PHP-FPM behind the health check")
        self.start()
        return result

    # Health ---------------------------------------------------------------

    def _https(self) -> bool:
        """Whether the site is served over TLS right now."""
        app = self._app_row()
        return self._ssl_obtained or bool(app is not None and app.ssl_enabled and self.ssl)

    def _health_gate(self) -> HealthGate:
        """
        Build the gate: reload FPM, then ask the pool over FastCGI.

        Returns:
            The gate.
        """
        return build_gate(
            fpm=self._fpm_service(),
            app_name=self.app_name,
            domain=self.domain,
            document_root=self._document_root(),
            https=self._https(),
            check=self._health_check_settings(),
            logger=self.logger,
        )

    def health_check(self, retries: int = 5, delay: float = 2.0) -> bool:
        """
        Ask the pool whether the application answers, without reloading anything.

        Args:
            retries: Number of attempts.
            delay: Seconds between them.

        Returns:
            Whether it answered as its health check expects.
        """
        check = self._health_check_settings()
        socket_path = self.installation.socket(self.app_name)
        probe = fastcgi_probe(
            socket_path,
            lambda: fastcgi_params(
                document_root=self._document_root(),
                path=check.path,
                domain=self.domain,
                https=self._https(),
            ),
        )
        return probe("", retries=retries, delay=delay, accept=check.accepts)

    def start(self) -> bool:
        """
        Reload FPM and wait for the pool to answer.

        Returns:
            True.

        Raises:
            DeploymentError: When it does not answer; the details are the
                probes, PHP's errors and FPM's journal.
        """
        healthy, evidence = self._health_gate().restart_and_probe()
        if not healthy:
            raise DeploymentError(f"{self.domain} did not pass its health check", details=evidence)
        return True

    def restart(self) -> bool:
        """
        Reload FPM, which restarts the pool's workers.

        Returns:
            True.
        """
        self._fpm_service().reload()
        return True

    def stop(self) -> bool:
        """
        Nothing to stop on its own: the pool runs inside the shared FPM.

        Returns:
            True.
        """
        return True


# Register the deployer
DeployerRegistry.register(PhpFpmDeployer)
