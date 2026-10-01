# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Move an application's variables out of its systemd unit and into its ``.env``.

WASM 1.x wrote every variable of an application into its unit as
``Environment=`` lines. A unit file is 0644 and ``systemctl show <unit>``
prints its environment to any local user, so every ``DATABASE_URL`` and secret
of those applications was readable without privileges. Since 2.x a unit
carries inline only what is Noust's to decide (:data:`INLINE`) and loads the
rest with ``EnvironmentFile=-`` from the application's ``.env``
(:func:`~noust.deployers.helpers.layout.env_file_for`, 0600, owned by the
service account). An update does not rewrite the unit, so an application
created before that kept its secrets inline, and its builds ran without them:
an update reads the variables from the ``.env``, which those applications did
not have.

:func:`migrate` puts one such application where every other already is: its
inline variables are merged into its ``.env`` (the unit's value wins where both
have one, because it is what the process runs with), the unit loses them and
gains the ``EnvironmentFile=`` line, and the application restarts through the
same :class:`~noust.deployers.helpers.health_gate.HealthGate` as a deploy. When
it does not come back, the previous unit and ``.env`` are put back and it is
restarted on them. :func:`inline_variables` is what the security check
``svc.inline_secrets`` reads.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from noust.core.exceptions import DeploymentError, ValidationError
from noust.core.fs import get_fs
from noust.core.logger import Logger
from noust.core.runner import get_runner
from noust.core.secret_detection import classify
from noust.core.store import App, NoustStore, get_store
from noust.deployers.helpers.env_manager import EnvManager
from noust.deployers.helpers.layout import app_root, env_file_for
from noust.managers.service_manager import ServiceManager

#: What a unit keeps inline: not secret, and Noust's to decide (the port, the
#: mode, which Compose file a stack runs). systemd lets ``EnvironmentFile=``
#: override ``Environment=``, so they stay out of the file.
INLINE = frozenset({"PORT", "NODE_ENV", "COMPOSE_FILE", "COMPOSE_PROJECT_NAME"})

#: Variables a framework copies into the code when it builds. For them the
#: value that was in use is the ``.env``'s, which every build read, not the
#: unit's, which the process got after the build had already inlined the other:
#: on the owner's central a unit said ``NEXT_PUBLIC_URL=http://localhost:3000``
#: while every build had baked in the real URL from the ``.env``.
BUILD_TIME_PREFIXES = (
    "NEXT_PUBLIC_",
    "VITE_",
    "REACT_APP_",
    "PUBLIC_",
    "NUXT_PUBLIC_",
    "GATSBY_",
    "EXPO_PUBLIC_",
)


@dataclass(frozen=True)
class Migration:
    """
    What moving one application's variables did.

    Attributes:
        domain: The application.
        unit: Its unit, without ``.service``.
        env_file: The file the variables went to.
        moved: The names moved, sorted.
        replaced: Names the ``.env`` had with another value, now the unit's.
        kept: Build-time names (:data:`BUILD_TIME_PREFIXES`) where both had
            a value and the ``.env``'s stayed, because builds used it.
        previous: Where the ``.env`` as it was is kept, or None when there
            was none.
    """

    domain: str
    unit: str
    env_file: Path
    moved: tuple[str, ...]
    replaced: tuple[str, ...] = ()
    kept: tuple[str, ...] = ()
    previous: Path | None = None


def _items(value: str) -> list[str]:
    """
    Split the value of one ``Environment=`` line the way systemd does.

    Args:
        value: What follows ``Environment=``.

    Returns:
        Each ``NAME=value`` assignment, unquoted and unescaped; ``%%`` is ``%``.
    """
    items: list[str] = []
    current: list[str] = []
    quoted = False
    index = 0
    while index < len(value):
        char = value[index]
        if quoted and char == "\\" and index + 1 < len(value):
            current.append(value[index + 1])
            index += 2
            continue
        if char == '"':
            quoted = not quoted
        elif char.isspace() and not quoted:
            if current:
                items.append("".join(current))
                current = []
        else:
            current.append(char)
        index += 1
    if current:
        items.append("".join(current))
    return [item.replace("%%", "%") for item in items]


def inline_variables(unit_text: str) -> dict[str, str]:
    """
    Read the variables a unit sets with ``Environment=``.

    Args:
        unit_text: The unit file.

    Returns:
        Name to value, in the order systemd applies them (a later one wins).
    """
    found: dict[str, str] = {}
    for line in unit_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("Environment="):
            continue
        for item in _items(stripped.removeprefix("Environment=")):
            name, sep, value = item.partition("=")
            if sep and name:
                found[name] = value
    return found


def movable(unit_text: str) -> dict[str, str]:
    """
    Name the variables a unit should not carry inline.

    Args:
        unit_text: The unit file.

    Returns:
        Every inline variable but :data:`INLINE`.
    """
    return {
        name: value for name, value in inline_variables(unit_text).items() if name not in INLINE
    }


def secret_names(variables: Mapping[str, str]) -> list[str]:
    """
    Pick the variables Noust treats as secrets, by name or by value.

    Args:
        variables: Name to value.

    Returns:
        The secret ones' names, sorted: a password-looking name, or a value
        such as a URL with credentials in it (``DATABASE_URL``).
    """
    return sorted(name for name, value in variables.items() if classify(name, value).secret)


def _escape(value: str) -> str:
    """Quote a value for an ``Environment=`` line, as the unit template does."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return escaped.replace("\n", " ").replace("\r", " ").replace("\x00", "")


def rewritten(unit_text: str, env_file: Path) -> str:
    """
    Give a unit its inline variables' new home.

    Args:
        unit_text: The unit file.
        env_file: The ``.env`` it loads from now.

    Returns:
        The unit without ``Environment=`` lines but for :data:`INLINE`, and
        with ``EnvironmentFile=-<env_file>`` before ``ExecStart=`` unless it
        already loads that file.
    """
    kept = {name: value for name, value in inline_variables(unit_text).items() if name in INLINE}
    target = f"EnvironmentFile=-{str(env_file).replace('%', '%%')}"
    has_file = any(
        line.strip() in (target, target.replace("=-", "=", 1)) for line in unit_text.splitlines()
    )
    out: list[str] = []
    placed = False
    for line in unit_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Environment="):
            if not placed:
                out.extend(
                    f'Environment="{_escape(name)}={_escape(value)}"'
                    for name, value in kept.items()
                )
                placed = True
            continue
        if stripped.startswith("ExecStart=") and not has_file:
            out.append(target)
            has_file = True
        out.append(line)
    return "\n".join(out) + ("\n" if unit_text.endswith("\n") else "")


def _unit_of(app: App, store: NoustStore) -> str:
    """
    Name the unit an application runs as, the way its health gate does.

    Args:
        app: The application.
        store: Where its service row is.

    Returns:
        The unit, without ``.service``.

    Raises:
        ValidationError: It has no unit of its own (static, PHP-FPM, Compose)
            or runs as zero-downtime instances, whose unit is a template.
    """
    from noust.deployers.lifecycle import PHP_FPM

    if app.is_static or app.app_type in (PHP_FPM, "docker-compose"):
        raise ValidationError(f"{app.domain} has no unit of its own to carry variables")
    if app.zero_downtime:
        raise ValidationError(
            f"{app.domain} runs as zero-downtime instances",
            details="Their unit is written by every deploy; redeploy it to move its variables.",
        )
    service = store.get_service_by_app_id(app.id) if app.id is not None else None
    return service.name if service is not None else app_root(app).name


def _owner(unit_text: str) -> str:
    """The ``user:group`` a unit runs as, for its ``.env``."""
    user = group = "root"
    for line in unit_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("User="):
            user = group = stripped.removeprefix("User=").strip() or "root"
        elif stripped.startswith("Group="):
            group = stripped.removeprefix("Group=").strip() or group
    return f"{user}:{group}"


def _keep_previous(app: App, text: str) -> Path:
    """
    Keep an application's ``.env`` as it was before its variables moved in.

    The migration decides, for each name both had, which value stays; the
    file as it was is the way back from a wrong call. Kept under Noust's state
    directory, root-only, not in the application's tree.

    Args:
        app: The application.
        text: The file's content.

    Returns:
        Where it is.
    """
    from datetime import datetime, timezone

    from noust.core import paths

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = paths.state_dir() / "env-migrations"
    fs = get_fs()
    fs.make_dir(directory, mode=0o700)
    copy = directory / f"{app_root(app).name}-{stamp}.env"
    fs.write_text(copy, text, mode=0o600)
    return copy


def migrate(
    domain: str,
    *,
    store: NoustStore | None = None,
    services: ServiceManager | None = None,
    logger: Logger | None = None,
) -> Migration | None:
    """
    Move one application's inline variables into its ``.env``, behind its health gate.

    Args:
        domain: The application.
        store: The store.
        services: The service manager.
        logger: Where progress goes.

    Returns:
        What moved, or None when the unit carries nothing to move.

    Raises:
        ValidationError: No such application, or none with a unit of its own.
        DeploymentError: It did not come back on its new environment; the
            previous unit and ``.env`` are back and it was restarted on them.
    """
    from noust.deployers.lifecycle import health_gate_for

    store = store or get_store()
    services = services or ServiceManager()
    log = logger or Logger()
    app = store.get_app(domain)
    if app is None:
        raise ValidationError(f"No application named {domain}")
    unit = _unit_of(app, store)
    path = Path(ServiceManager.SYSTEMD_DIR) / f"{unit}.service"
    before = path.read_text(encoding="utf-8")
    moving = movable(before)
    if not moving:
        return None

    env_file = env_file_for(app)
    env = EnvManager()
    previous_env = env_file.read_text(encoding="utf-8") if env_file.exists() else None
    current = env.read_env_file(env_file)
    differing = [
        name for name, value in moving.items() if name in current and current[name] != value
    ]
    kept = sorted(name for name in differing if name.startswith(BUILD_TIME_PREFIXES))
    replaced = sorted(name for name in differing if name not in kept)
    merged = {**current, **{name: value for name, value in moving.items() if name not in kept}}
    previous_copy = _keep_previous(app, previous_env) if previous_env is not None else None
    after = rewritten(before, env_file)

    log.substep(f"Moving {len(moving)} variable(s) of {domain} from {unit}.service to {env_file}")
    env.write_env_file(env_file, merged)
    get_runner().run(["chown", _owner(before), str(env_file)], timeout=30, check=True)
    services.rewrite_unit(unit, after)

    healthy, evidence = health_gate_for(app, store, log).restart_and_probe()
    if healthy:
        return Migration(
            domain,
            unit,
            env_file,
            tuple(sorted(moving)),
            tuple(replaced),
            tuple(kept),
            previous_copy,
        )

    log.warning(f"{domain} did not come back; putting its previous unit and .env back")
    services.rewrite_unit(unit, before)
    fs = get_fs()
    if previous_env is None:
        fs.remove(env_file)
    else:
        fs.write_text(env_file, previous_env, mode=0o600)
        get_runner().run(["chown", _owner(before), str(env_file)], timeout=30, check=True)
    restored, _ = health_gate_for(app, store, log).restart_and_probe()
    state = (
        "it runs on its previous unit again"
        if restored
        else "it is not answering on its previous unit either"
    )
    raise DeploymentError(
        f"{domain} did not pass its health check on its variables from {env_file}; {state}",
        details=evidence,
    )


def candidates(store: NoustStore | None = None) -> list[tuple[str, list[str]]]:
    """
    Find the applications whose unit still carries variables inline.

    Args:
        store: The store.

    Returns:
        Each such application's domain and the names its unit carries.
    """
    store = store or get_store()
    found: list[tuple[str, list[str]]] = []
    for app in store.list_apps():
        try:
            unit = _unit_of(app, store)
        except ValidationError:
            continue
        path = Path(ServiceManager.SYSTEMD_DIR) / f"{unit}.service"
        try:
            names = sorted(movable(path.read_text(encoding="utf-8")))
        except OSError:
            continue
        if names:
            found.append((app.domain, names))
    return found
