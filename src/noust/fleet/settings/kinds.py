# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What can be a setting of the fleet: the closed list, how each is named, and which fields are secrets.

The value of a setting is the body of the node's own typed route for it (a channel's is what
``PUT /api/notifications/channels/{name}`` takes, a server's policy what ``PUT /api/config/policy``
takes), so the central never says anything a node does
not already know how to read, and the node judges the content with its own validators (rule 3).
What the central checks is the shape: which fields a value may have and which a server needs to
make one, so that a typo is found when the setting is saved and not once on every server.

The secrets of a value are told apart from the rest here and nowhere else: the central keeps them
in its secret store (sealed on a sealed central), the table holds the rest, and what is shown
carries :data:`REDACTED` where one is stored. The machine's own settings (the web server, the
firewall, SSH, ports, runtimes) are not on the list and never will be: they are the machine's
(``docs/CENTRAL.md``).
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from noust.core.config import REDACTED, is_secret_key
from noust.core.exceptions import NoustError, ValidationError
from noust.core.server_text import say
from noust.fleet.models import validate_node_name
from noust.fleet.settings import policy
from noust.fleet.settings.reach import check_route

__all__ = [
    "AVAILABLE",
    "CONSOLE_LINK_NAME",
    "KINDS",
    "MAX_VALUE_BYTES",
    "NODE_SCOPE_PREFIX",
    "PLANNED",
    "REDACTED",
    "PlannedKind",
    "SettingKind",
    "check_scope",
    "join_secrets",
    "kind_of",
    "split_secrets",
]

#: The most a setting's value may weigh once written as JSON.
MAX_VALUE_BYTES = 64 * 1024

#: Kinds the design names (D1) and a later release will manage; asking for one says so.
PLANNED: tuple[str, ...] = ("secret_manager", "dns_provider", "source_integration")

#: Where a setting is set for one server: ``node:`` and the server's name.
NODE_SCOPE_PREFIX = "node:"

#: The one name of the console link: there is one for the fleet.
CONSOLE_LINK_NAME = "console"


class PlannedKind(ValidationError):
    """A kind of setting the fleet will manage in a later release."""


def _name_of(kind: str) -> Callable[[str], str]:
    """
    Args:
        kind: A key of :data:`noust.validators.object_names.RULES`.

    Returns:
        The check of that kind of name.
    """

    def check(name: str) -> str:
        from noust.validators.object_names import check_name

        return check_name(kind, name)

    return check


def _registry_name(name: str) -> str:
    """
    Args:
        name: A registry login's name.

    Returns:
        The name, when the registry store accepts it.
    """
    from noust.core.registry_store import check_name

    return check_name(name)


def _destination_name(name: str) -> str:
    """
    Args:
        name: A backup destination's name.

    Returns:
        The name, when the destinations accept it.
    """
    from noust.managers.backup_destinations import validate_destination_name

    return validate_destination_name(name)


def _variable_name(name: str) -> str:
    """
    Args:
        name: A shared variable's name.

    Returns:
        The name, when it is one an environment can have.

    Raises:
        ValidationError: When it is not.
    """
    from noust.validators.environment import ENV_NAME_PATTERN

    if not isinstance(name, str) or not ENV_NAME_PATTERN.fullmatch(name) or len(name) > 128:
        raise ValidationError(
            f"Invalid variable name: {name!r}",
            details="A variable is named like an environment variable: letters, digits and '_', "
            "not starting with a digit.",
            field="name",
        )
    return name


def _channel_secrets(name: str, value: Mapping[str, Any]) -> tuple[str, ...]:
    """
    Args:
        name: The channel.
        value: Its value.

    Returns:
        The settings the configuration seals, by the name it seals by.
    """
    settings = value.get("settings")
    if not isinstance(settings, Mapping):
        return ()
    return tuple(f"settings.{key}" for key in settings if is_secret_key(str(key)))


def _destination_secrets(name: str, value: Mapping[str, Any]) -> tuple[str, ...]:
    """
    Args:
        name: The destination.
        value: Its value.

    Returns:
        The backend's secret fields and the passphrases of its encryption.
    """
    from noust.managers.backup_destinations import backend_fields

    found: list[str] = []
    fields = value.get("fields")
    backend = value.get("backend")
    if isinstance(fields, Mapping) and isinstance(backend, str):
        try:
            secret_keys = {spec.key for spec in backend_fields(backend) if spec.secret}
        except (KeyError, ValidationError):
            secret_keys = set()
        found.extend(f"fields.{key}" for key in fields if key in secret_keys)
    key = value.get("encryption_key")
    if isinstance(key, Mapping):
        found.extend(f"encryption_key.{part}" for part in ("password", "password2") if part in key)
    return tuple(found)


def _registry_secrets(name: str, value: Mapping[str, Any]) -> tuple[str, ...]:
    """
    Args:
        name: The login.
        value: Its value.

    Returns:
        The password.
    """
    return ("password",) if "password" in value else ()


def _variable_secrets(name: str, value: Mapping[str, Any]) -> tuple[str, ...]:
    """
    Args:
        name: The variable.
        value: Its value.

    Returns:
        ``value`` when the operator marked the variable a secret, or left it to its name.
    """
    from noust.deployers.helpers.env_manager import is_secret_env_name

    mark = value.get("secret")
    secret = mark if isinstance(mark, bool) else is_secret_env_name(name)
    return ("value",) if secret and "value" in value else ()


def _no_secrets(name: str, value: Mapping[str, Any]) -> tuple[str, ...]:
    """Returns: Nothing: an alert rule or an address holds no credential."""
    return ()


def _the_one_name(kind: str, only: str) -> Callable[[str], str]:
    """
    Args:
        kind: A kind there is one of for the fleet.
        only: The one name it has.

    Returns:
        The check of that name: anything else is refused, naming the field.
    """

    def check(name: str) -> str:
        if name != only:
            raise ValidationError.from_text(
                say("fleet_settings.single_name", kind=kind, name=only), field="name"
            )
        return name

    return check


def _console_link_check(value: Mapping[str, Any]) -> None:
    """
    Args:
        value: The console link's value.

    Raises:
        ValidationError: The address is not text. Whether it is an address a server accepts is
            the server's to judge.
    """
    if not isinstance(value.get("url"), str):
        raise ValidationError.from_text(
            say("fleet_settings.field_not_text", kind="console_link", field="url"), field="url"
        )


def _console_link_for(value: Mapping[str, Any], node: str) -> dict[str, Any]:
    """
    Args:
        value: The central's own address.
        node: The server, as the central's registry names it.

    Returns:
        The address of that server inside the central's console, which is what its links open:
        only the central knows the server by that name.
    """
    base = str(value["url"]).strip().rstrip("/")
    return {**value, "url": f"{base}/n/{quote(node, safe='')}"}


@dataclass(frozen=True)
class SettingKind:
    """
    One kind of setting the fleet manages.

    Attributes:
        key: Its name in the API and the table (``channel``).
        title: What it is called.
        fields: The fields a value may have: those of the node's own route.
        required: The fields a server needs to make one.
        permission: The permission the node's own route asks, which a person needs on the
            central to set one for the fleet.
        operations: ``(METHOD, template)`` of the node's routes the reconciler uses; a node that
            lacks one cannot take this kind (an older Noust).
        name_check: The validator of the object's name.
        secrets: Which paths of a value hold a credential.
        check: A judgement of the whole value beyond its shape, for what the central itself reads
            of it (a channel's route decides which servers it is sent to); None for none.
        per_node: Makes the value one server is sent from the one the central keeps, when it
            says something only the central knows (the server's name in its console); None when
            every server is sent the value as it is.
    """

    key: str
    title: str
    fields: frozenset[str]
    required: tuple[str, ...]
    permission: str
    operations: tuple[tuple[str, str], ...]
    name_check: Callable[[str], str]
    secrets: Callable[[str, Mapping[str, Any]], tuple[str, ...]]
    check: Callable[[Mapping[str, Any]], None] | None = None
    per_node: Callable[[Mapping[str, Any], str], dict[str, Any]] | None = None

    def check_name(self, name: str) -> str:
        """
        Args:
            name: What the setting is called.

        Returns:
            The name, when the object it names would accept it.

        Raises:
            ValidationError: When it would not, whatever class the object's own validator
                raises: the API answers a bad name as a bad request.
        """
        try:
            return self.name_check(name)
        except ValidationError:
            raise
        except NoustError as exc:
            raise ValidationError(exc.message, exc.details, field="name") from exc

    def check_value(self, value: object) -> dict[str, Any]:
        """
        Check the shape of a value: an object with only the fields the node's route takes and
        the ones a server needs to make one. The content is the server's to judge.

        Args:
            value: What was given.

        Returns:
            The value as a dict.

        Raises:
            ValidationError: Naming the field, with a sentence of the catalog.
        """
        if not isinstance(value, dict):
            raise ValidationError.from_text(
                say("fleet_settings.value_not_object", kind=self.key), field="value"
            )
        for field in value:
            if field not in self.fields:
                raise ValidationError.from_text(
                    say(
                        "fleet_settings.unknown_field",
                        kind=self.key,
                        field=str(field),
                        fields=", ".join(sorted(self.fields)),
                    ),
                    field=str(field),
                )
        for field in self.required:
            if field not in value:
                raise ValidationError.from_text(
                    say("fleet_settings.missing_field", kind=self.key, field=field), field=field
                )
        if len(json.dumps(value, default=str)) > MAX_VALUE_BYTES:
            raise ValidationError.from_text(
                say("fleet_settings.value_too_large", kind=self.key, limit=MAX_VALUE_BYTES),
                field="value",
            )
        if self.check is not None:
            self.check(value)
        return value

    def for_node(self, value: Mapping[str, Any], node: str) -> dict[str, Any]:
        """
        Args:
            value: The value the central keeps, with its credentials in it.
            node: The server it is sent to.

        Returns:
            The value that server is sent. The one given is not changed.
        """
        return self.per_node(value, node) if self.per_node is not None else dict(value)

    def secret_paths(self, name: str, value: Mapping[str, Any]) -> tuple[str, ...]:
        """
        Args:
            name: The setting's name.
            value: Its value.

        Returns:
            The dotted paths of the value that hold a credential.
        """
        return self.secrets(name, value)

    def shown(self, public: Mapping[str, Any], secret_paths: set[str] | frozenset[str]) -> dict:
        """
        Args:
            public: A value without its secrets.
            secret_paths: The paths that have one stored.

        Returns:
            The value as it is shown: each stored secret is :data:`REDACTED`, never what it is.
        """
        shown = copy.deepcopy(dict(public))
        for path in sorted(secret_paths):
            _put(shown, path, REDACTED)
        return shown


def _walk(document: Any, path: str) -> tuple[Any, str] | None:
    """
    Args:
        document: A value.
        path: A dotted path in it.

    Returns:
        The mapping that holds the last step, and that step; None when the path is not there.
    """
    *parents, last = path.split(".")
    node = document
    for step in parents:
        if not isinstance(node, dict) or step not in node:
            return None
        node = node[step]
    return (node, last) if isinstance(node, dict) and last in node else None


def _put(document: dict[str, Any], path: str, value: Any) -> None:
    """
    Set the value at a dotted path, making the objects on the way.

    Args:
        document: The value, changed in place.
        path: The dotted path.
        value: What to put there.
    """
    *parents, last = path.split(".")
    node = document
    for step in parents:
        child = node.get(step)
        if not isinstance(child, dict):
            child = {}
            node[step] = child
        node = child
    node[last] = value


def split_secrets(
    kind: SettingKind,
    name: str,
    value: Mapping[str, Any],
    keep: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """
    Take the credentials out of a value.

    Args:
        kind: The kind of setting.
        name: Its name.
        value: The value, with its secrets in it.
        keep: The secrets already stored for this setting: a secret sent back as
            :data:`REDACTED` means "keep that one", as it does for a server's own settings.

    Returns:
        The value without its credentials, and the credentials by dotted path. A credential
        that is empty is not one: it stays in the value, and means what it means to the node
        (a blank secret keeps what the node has).

    Raises:
        ValidationError: A secret was sent back as :data:`REDACTED` and nothing is stored for it.
    """
    public = copy.deepcopy(dict(value))
    secrets: dict[str, str] = {}
    for path in kind.secret_paths(name, value):
        found = _walk(public, path)
        if found is None:
            continue
        holder, last = found
        stored = holder[last]
        if stored == REDACTED:
            if keep is None or path not in keep:
                raise ValidationError.from_text(
                    say("fleet_settings.secret_missing", field=path, redacted=REDACTED),
                    field=path,
                )
            secrets[path] = keep[path]
            del holder[last]
        elif isinstance(stored, str) and stored:
            secrets[path] = stored
            del holder[last]
    return public, secrets


def join_secrets(
    kind: SettingKind, public: Mapping[str, Any], secrets: Mapping[str, str]
) -> dict[str, Any]:
    """
    Put the credentials back into a value, to push it.

    Args:
        kind: The kind of setting.
        public: The value without its credentials.
        secrets: The credentials by dotted path.

    Returns:
        The whole value.
    """
    whole = copy.deepcopy(dict(public))
    for path, secret in secrets.items():
        _put(whole, path, secret)
    return whole


def check_scope(scope: str) -> str:
    """
    Args:
        scope: ``fleet``, or ``node:<server>``.

    Returns:
        The scope.

    Raises:
        ValidationError: When it is not one.
    """
    if scope == "fleet":
        return scope
    if isinstance(scope, str) and scope.startswith(NODE_SCOPE_PREFIX):
        try:
            validate_node_name(scope[len(NODE_SCOPE_PREFIX) :])
        except NoustError:
            pass
        else:
            return scope
    raise ValidationError.from_text(
        say("fleet_settings.bad_scope", scope=str(scope)), field="scope"
    )


#: Every kind this release manages, in the order they are listed.
KINDS: dict[str, SettingKind] = {
    kind.key: kind
    for kind in (
        SettingKind(
            key="channel",
            title="Notification channel",
            fields=frozenset(
                {"type", "enabled", "settings", "events", "route", "quiet", "flood", "summary"}
            ),
            required=("type",),
            permission="settings.manage",
            operations=(
                ("GET", "/api/notifications/channels"),
                ("PUT", "/api/notifications/channels/{name}"),
            ),
            name_check=_name_of("channel"),
            secrets=_channel_secrets,
            check=check_route,
        ),
        SettingKind(
            key="alert_rule",
            title="Alert rule",
            fields=frozenset(
                {
                    "metric",
                    "comparator",
                    "threshold",
                    "app_domain",
                    "duration_s",
                    "hysteresis",
                    "cooldown_s",
                    "severity",
                    "enabled",
                    "remind",
                    "note",
                }
            ),
            required=("metric", "threshold"),
            permission="server.manage",
            operations=(("GET", "/api/alerts/rules"), ("PUT", "/api/alerts/rules/{name}")),
            name_check=_name_of("alert_rule"),
            secrets=_no_secrets,
        ),
        SettingKind(
            key="backup_destination",
            title="Backup destination",
            fields=frozenset({"backend", "fields", "encrypted", "encryption_key", "verify"}),
            required=("backend",),
            permission="backups.manage",
            operations=(
                ("GET", "/api/backup-destinations"),
                ("POST", "/api/backup-destinations"),
                ("PUT", "/api/backup-destinations/{name}"),
            ),
            name_check=_destination_name,
            secrets=_destination_secrets,
        ),
        SettingKind(
            key="registry",
            title="Registry login",
            fields=frozenset({"kind", "server", "username", "password"}),
            required=("kind", "username", "password"),
            permission="apps.manage",
            operations=(
                ("GET", "/api/registries"),
                ("POST", "/api/registries"),
                ("PATCH", "/api/registries/{name}"),
            ),
            name_check=_registry_name,
            secrets=_registry_secrets,
        ),
        SettingKind(
            key="variable",
            title="Shared variable",
            fields=frozenset({"value", "secret", "audience"}),
            required=("value",),
            permission="settings.manage",
            operations=(
                ("GET", "/api/variables/server"),
                ("PUT", "/api/variables/server/{name}"),
            ),
            name_check=_variable_name,
            secrets=_variable_secrets,
        ),
        SettingKind(
            key="console_link",
            title="Console link",
            fields=frozenset({"url"}),
            required=("url",),
            permission="settings.manage",
            operations=(
                ("GET", "/api/notifications/console-url"),
                ("PUT", "/api/notifications/console-url"),
            ),
            name_check=_the_one_name("console_link", CONSOLE_LINK_NAME),
            secrets=_no_secrets,
            check=_console_link_check,
            per_node=_console_link_for,
        ),
        SettingKind(
            key="policy",
            title="Server policy",
            fields=frozenset(policy.FIELDS),
            required=policy.FIELDS,
            permission="settings.manage",
            operations=(("GET", "/api/config/policy"), ("PUT", "/api/config/policy")),
            name_check=_the_one_name("policy", policy.NAME),
            secrets=_no_secrets,
        ),
    )
}

#: The keys of :data:`KINDS`.
AVAILABLE: tuple[str, ...] = tuple(KINDS)


def kind_of(key: str) -> SettingKind:
    """
    Args:
        key: A kind's key.

    Returns:
        The kind.

    Raises:
        PlannedKind: For one the fleet will manage in a later release.
        ValidationError: For anything else.
    """
    found = KINDS.get(key) if isinstance(key, str) else None
    if found is not None:
        return found
    if key in PLANNED:
        raise PlannedKind.from_text(say("fleet_settings.kind_not_yet", kind=key), field="resource")
    raise ValidationError.from_text(
        say("fleet_settings.unknown_kind", kind=str(key), kinds=", ".join(AVAILABLE)),
        field="resource",
    )
