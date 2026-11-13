# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the operator chose for the built-in assistant, and the one door to its key (item 171).

The assistant is off until an administrator turns it on, and what it needs to be on is here: a
provider (an address, a model), a key when the provider asks for one, limits that always have a
number, a price table, and what the probe last found out. It lives in the store, in one row of
``assistant_settings``, and not in ``config.yaml``: nothing writes it but this module, so the
generic configuration endpoint cannot turn the assistant on or point it somewhere without the
checks below (rule 4).

**The provider is judged when it is chosen** (:func:`noust.core.assistant.client.check_address`),
and again on every request the client makes. An address on this machine or the operator's network
is refused until the operator consents, by name, to this server calling it: ``allow_private_host``
adds the host to ``security.allow_private_hosts``, which is the list that already says so for every
other feature. Where the data ends up (``location``) is told by the address when it is the
operator's own (loopback is this machine, a private network is theirs) and declared by the
operator when it is public: a public address can never be "this machine".

**The key** is sealed with :mod:`noust.core.at_rest` under a domain of its own, bound to this row,
beside the configuration's other secrets. It is opened by :meth:`AssistantSettings.api_key` for
the client and by nothing else, and what is read back says only that there is one and since when.

**Personal data is disguised for any provider that is not the operator's**
(``mask_personal_effective``): the operator may turn the disguise off for a model of their own and
for no other, and moving to a provider outside turns it on again.
"""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from noust.core import at_rest, channel_secrets, outbound
from noust.core import config as config_module
from noust.core.assistant import presets as preset_table
from noust.core.assistant.client import ProviderError, check_address
from noust.core.assistant.limits import Limits, Price
from noust.core.config import Config
from noust.core.exceptions import ValidationError
from noust.core.secret_detection import mask_secret_shapes
from noust.core.timeutil import utc_now_iso

if TYPE_CHECKING:
    from noust.core.store import NoustStore

__all__ = ["AssistantKeyError", "AssistantSettings", "Settings"]

#: The one row.
_ROW = 1

_WORD = re.compile(r"[^\s\x00-\x1f\x7f]+")
_CURRENCY = re.compile(r"[^\s\x00-\x1f\x7f]{1,8}")

_UNSET: Any = object()


class AssistantKeyError(at_rest.AtRestError):
    """The provider's key cannot be opened: altered, moved, or the key beside the file is not its."""


_DOMAIN = at_rest.Domain(
    prefix="noust-assistant:v1:",
    label="noust-assistant",
    noun="assistant key",
    error=AssistantKeyError,
)

#: What the sealed value is bound to, so a copy of it in another row or column is refused.
_CONTEXT = f"assistant_settings:{_ROW}:api_key"


@dataclass(frozen=True)
class Settings:
    """
    The assistant's settings, as read. The key is never in it.

    Attributes:
        enabled: Whether the assistant is on.
        preset: The provider preset it was chosen from, or ``""``.
        base_url: The provider's address.
        model: The model's name.
        has_key: Whether a key is kept.
        key_set_at: When the key was set.
        location: Where the data ends up (:data:`noust.core.assistant.presets.LOCATIONS`).
        flow_reason: Why a provider outside is an authorized flow, under the ENS profile.
        mask_personal: What the operator chose for the disguise of personal data.
        limits: The ceilings.
        prices: What each model costs.
        currency: What the prices and the spending limits are in.
        probe: What the probe last found (a dictionary of facts and sentences), or ``{}``.
        probed_at: When.
        updated_at: When the settings last changed.
        updated_by: Who changed them.
    """

    enabled: bool = False
    preset: str = ""
    base_url: str = ""
    model: str = ""
    has_key: bool = False
    key_set_at: str | None = None
    location: str = "unknown"
    flow_reason: str = ""
    mask_personal: bool = True
    limits: Limits = field(default_factory=Limits)
    prices: dict[str, Price] = field(default_factory=dict)
    currency: str = "USD"
    probe: dict[str, Any] = field(default_factory=dict)
    probed_at: str | None = None
    updated_at: str | None = None
    updated_by: str = ""

    @property
    def configured(self) -> bool:
        """Whether a provider and a model were chosen."""
        return bool(self.base_url and self.model)

    @property
    def mask_personal_effective(self) -> bool:
        """Whether emails and addresses are disguised: always, but for a provider of one's own."""
        return self.mask_personal or self.location not in preset_table.OWN_SIDE

    @property
    def calls_tools(self) -> bool:
        """Whether the probe found that the model calls tools well: what lets it investigate."""
        return self.probe.get("tools") is True

    def to_public(self) -> dict[str, Any]:
        """
        Returns:
            What the API shows: everything but the key, and the sentence of the last probe as it
            was found.
        """
        return {
            "enabled": self.enabled,
            "configured": self.configured,
            "preset": self.preset,
            "base_url": self.base_url,
            "model": self.model,
            "has_key": self.has_key,
            "key_set_at": self.key_set_at,
            "location": self.location,
            "flow_reason": self.flow_reason,
            "mask_personal": self.mask_personal_effective,
            "limits": self.limits.to_dict(),
            "prices": {name: price.to_dict() for name, price in sorted(self.prices.items())},
            "currency": self.currency,
            "probe": self.probe,
            "probed_at": self.probed_at,
            "calls_tools": self.calls_tools,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


def _word(name: str, value: Any, *, longest: int, low: int = 1) -> str:
    """
    Args:
        name: The field, for the message.
        value: What was given.
        longest: The most characters.
        low: The fewest.

    Returns:
        The value, one word with no space or control character in it.

    Raises:
        ValidationError: It is anything else.
    """
    text = value if isinstance(value, str) else ""
    if not low <= len(text) <= longest or not _WORD.fullmatch(text):
        raise ValidationError(
            f"The {name} must be one word of {low} to {longest} characters, with no spaces",
            field=name,
        )
    return text


def _own_side_location(addresses: tuple[str, ...]) -> str | None:
    """
    Args:
        addresses: What a provider's name resolved to.

    Returns:
        ``machine`` when they are all this machine, ``network`` when none is public, None when one
        is public.
    """
    parsed = [ipaddress.ip_address(address) for address in addresses]
    if parsed and all(address.is_loopback for address in parsed):
        return "machine"
    # The networks the outbound guard refuses by default are the operator's own.
    if parsed and all(outbound.is_forbidden(address) for address in parsed):
        return "network"
    return None


class AssistantSettings:
    """The row of ``assistant_settings``, and everything that may be done to it."""

    def __init__(self, store: NoustStore | None = None) -> None:
        """
        Args:
            store: The store; the process-wide one when None.
        """
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store, resolved on first use."""
        if self._store is None:
            from noust.core.store import get_store

            self._store = get_store()
        return self._store

    # -- reading ---------------------------------------------------------------------------------

    def read(self) -> Settings:
        """
        Returns:
            The settings; the defaults (off, nothing chosen) when nothing was ever written.
        """
        with self.store._transaction() as cursor:
            cursor.execute("SELECT * FROM assistant_settings WHERE id = ?", (_ROW,))
            row = cursor.fetchone()
        if row is None:
            return Settings()
        return Settings(
            enabled=bool(row["enabled"]),
            preset=row["preset"],
            base_url=row["base_url"],
            model=row["model"],
            has_key=bool(row["api_key"]),
            key_set_at=row["key_set_at"],
            location=row["location"],
            flow_reason=row["flow_reason"],
            mask_personal=bool(row["mask_personal"]),
            limits=Limits.from_dict(_loads(row["limits"])),
            prices={
                name: Price.from_dict(values) for name, values in _loads(row["prices"]).items()
            },
            currency=row["currency"],
            probe=_loads(row["probe"]),
            probed_at=row["probed_at"],
            updated_at=row["updated_at"],
            updated_by=row["updated_by"],
        )

    def api_key(self) -> str:
        """
        Open the provider's key. The one place that does.

        Returns:
            The key; ``""`` when there is none.

        Raises:
            AssistantKeyError: It cannot be opened: altered, moved, or sealed under another key.
        """
        with self.store._transaction() as cursor:
            cursor.execute("SELECT api_key FROM assistant_settings WHERE id = ?", (_ROW,))
            row = cursor.fetchone()
        stored = row["api_key"] if row is not None else ""
        if not stored:
            return ""
        path = _key_path()
        try:
            key = at_rest.load_key(path, create=False)
        except OSError as exc:
            raise AssistantKeyError(
                f"The key of the assistant's provider cannot be opened: {exc}. "
                f"Restore {path}, or give the key again."
            ) from exc
        return _DOMAIN.open(stored, key, _CONTEXT, missing_key=f"Restore {path}, or give it again.")

    # -- writing ---------------------------------------------------------------------------------

    def update(
        self,
        *,
        actor: str,
        preset: str | None = _UNSET,
        base_url: str = _UNSET,
        model: str = _UNSET,
        api_key: str = _UNSET,
        location: str | None = _UNSET,
        flow_reason: str = _UNSET,
        mask_personal: bool = _UNSET,
        limits: Mapping[str, Any] = _UNSET,
        prices: Mapping[str, Mapping[str, Any]] = _UNSET,
        currency: str = _UNSET,
        allow_private_host: bool = False,
    ) -> Settings:
        """
        Change some of the settings; what is not given stays.

        Args:
            actor: Who changes them, for the record.
            preset: A provider preset, which fills the address and the place the data goes.
            base_url: The provider's address, up to its version.
            model: The model's name.
            api_key: The key; ``""`` takes it away.
            location: Where the data ends up, for a public provider the operator knows.
            flow_reason: Why a provider outside is an authorized flow.
            mask_personal: Whether emails and addresses are disguised (only a provider of one's
                own may have it off).
            limits: Limits to change, by name.
            prices: The whole price table, by model.
            currency: What the prices are in.
            allow_private_host: Consent that this server calls the provider's host though it is on
                this machine or the operator's network: the host is added to
                ``security.allow_private_hosts``.

        Returns:
            The settings as they are now.

        Raises:
            ValidationError: A value is not acceptable; nothing is written.
            ProviderError: The address cannot be called, with the code that says why.
        """
        current = self.read()
        values: dict[str, Any] = {}
        chosen = preset_table.preset(preset) if isinstance(preset, str) and preset else None
        if preset is not _UNSET:
            values["preset"] = chosen.id if chosen else ""
        elif base_url is not _UNSET:
            values["preset"] = ""
        address = base_url if base_url is not _UNSET else (chosen.base_url if chosen else None)
        declared = location if location is not _UNSET else (chosen.location if chosen else None)
        resulting_location = current.location
        if address is not None:
            endpoint = self._judge(address, allow_private_host)
            own = _own_side_location(endpoint.addresses)
            if own is not None:
                resulting_location = own
            else:
                resulting_location = self._public_location(declared or "unknown")
            values.update(base_url=address, location=resulting_location, probe="{}", probed_at=None)
        elif declared is not None and location is not _UNSET:
            if current.location in preset_table.OWN_SIDE:
                resulting_location = current.location
            else:
                resulting_location = self._public_location(declared)
            values["location"] = resulting_location
        if model is not _UNSET:
            values.update(model=_word("model", model, longest=200), probe="{}", probed_at=None)
        if flow_reason is not _UNSET:
            values["flow_reason"] = mask_secret_shapes(str(flow_reason).strip())[:500]
        if mask_personal is not _UNSET:
            if not mask_personal and resulting_location not in preset_table.OWN_SIDE:
                raise ValidationError(
                    "A provider that is not on this machine or your network always gets personal "
                    "data disguised",
                    details="Turn it off only for a model of your own.",
                    field="mask_personal",
                )
            values["mask_personal"] = int(bool(mask_personal))
        elif resulting_location not in preset_table.OWN_SIDE and not current.mask_personal:
            values["mask_personal"] = 1
        if limits is not _UNSET:
            values["limits"] = json.dumps(
                Limits.from_dict({**current.limits.to_dict(), **dict(limits)}).to_dict()
            )
        if prices is not _UNSET:
            values["prices"] = json.dumps(_prices(prices))
        if currency is not _UNSET:
            text = currency if isinstance(currency, str) else ""
            if not _CURRENCY.fullmatch(text):
                raise ValidationError(
                    "The currency must be a short word, such as EUR",
                    field="currency",
                )
            values["currency"] = text
        if api_key is not _UNSET:
            values.update(self._sealed(api_key))
        self._write(values, actor)
        return self.read()

    def enable(self, *, actor: str) -> Settings:
        """
        Turn the assistant on.

        Args:
            actor: Who turns it on.

        Returns:
            The settings as they are now.

        Raises:
            ValidationError: No provider was chosen, or the probe has not found it answering.
            ProviderError: The provider would be refused now.
        """
        current = self.read()
        if not current.configured:
            raise ValidationError(
                "Choose a provider and a model before turning the assistant on",
                details="Give an address and a model, or a preset and a model.",
            )
        if current.probe.get("reachable") is not True:
            raise ValidationError(
                "Run the probe first: it has not found the provider answering",
                details="The probe asks the provider for its models and for a call to a toy tool.",
            )
        self._judge(current.base_url, False)
        self._require_declared_flow(current)
        self._write({"enabled": 1}, actor)
        return self.read()

    @staticmethod
    def _require_declared_flow(current: Settings) -> None:
        """
        Hold a provider outside to the security profile, which lists every flow beforehand.

        Args:
            current: The settings about to be turned on.

        Raises:
            ValidationError: The profile asks for it, the provider is not on this machine or the
                operator's network, and no reason is recorded for the flow.
        """
        from noust.core.ens.profile import assistant_needs_declared_flow

        if (
            assistant_needs_declared_flow()
            and current.location not in preset_table.OWN_SIDE
            and not current.flow_reason.strip()
        ):
            raise ValidationError(
                "The security profile lets the assistant send what it is told only to a model on "
                "this machine or your network, or to a provider declared an authorized flow",
                details="Say why this provider is one (the contract, the data processing "
                "agreement): noust assistant configure --flow-reason TEXT. It is kept and is "
                "on the record.",
                field="flow_reason",
            )

    def disable(self, *, actor: str) -> Settings:
        """
        Turn the assistant off. Revoking its tokens is the console's to do, and is done with this.

        Args:
            actor: Who turns it off.

        Returns:
            The settings as they are now.
        """
        self._write({"enabled": 0}, actor)
        return self.read()

    def record_probe(self, result: Mapping[str, Any]) -> None:
        """
        Keep what the probe found.

        Args:
            result: The probe's result as a dictionary (``ProbeResult.to_dict()``).
        """
        self._write({"probe": json.dumps(dict(result)), "probed_at": utc_now_iso("seconds")}, None)

    # -- the work --------------------------------------------------------------------------------

    @staticmethod
    def _public_location(declared: str) -> str:
        """
        Args:
            declared: What the operator or a preset says for a public address.

        Returns:
            The place, never one that is the operator's own.

        Raises:
            ValidationError: It is not a place, or is one a public address cannot be.
        """
        if declared not in preset_table.LOCATIONS:
            raise ValidationError(
                f"There is no place called {declared!r}",
                details=f"Use one of: {', '.join(preset_table.LOCATIONS)}.",
                field="location",
            )
        if declared in preset_table.OWN_SIDE:
            raise ValidationError(
                "A public address is not on this machine or on your network",
                details="Say eu or outside, or leave it unsaid.",
                field="location",
            )
        return declared

    @staticmethod
    def _judge(address: str, allow_private_host: bool) -> Any:
        """
        Judge a provider's address, adding its host to the allowed ones when the operator consents.

        Args:
            address: The provider's address.
            allow_private_host: The operator's consent.

        Returns:
            The judged endpoint.

        Raises:
            ProviderError: The address cannot be called.
        """
        try:
            return check_address(address)
        except ProviderError as refusal:
            if refusal.code != "assistant.provider.destination_refused" or not allow_private_host:
                raise
        from urllib.parse import urlsplit

        host = urlsplit(address).hostname or ""
        config = Config()
        listed = [str(entry) for entry in (config.get("security.allow_private_hosts") or [])]
        if host.lower() not in {entry.lower() for entry in listed}:
            config.set("security.allow_private_hosts", [*listed, host])
            config.write()
        return check_address(address)

    @staticmethod
    def _sealed(api_key: str) -> dict[str, Any]:
        """
        Args:
            api_key: The key, or ``""`` to take it away.

        Returns:
            The columns that hold it.

        Raises:
            ValidationError: It is not one word of a reasonable length.
        """
        if api_key == "":
            return {"api_key": "", "key_set_at": None, "probe": "{}", "probed_at": None}
        text = _word("key", api_key, longest=512, low=8)
        key = channel_secrets.ensure_key(_key_path())
        return {
            "api_key": _DOMAIN.seal(text, key, _CONTEXT),
            "key_set_at": utc_now_iso("seconds"),
            "probe": "{}",
            "probed_at": None,
        }

    def _write(self, values: dict[str, Any], actor: str | None) -> None:
        """
        Write columns of the row, making it first.

        Args:
            values: Column names (code, never input) and what they are set to.
            actor: Who, or None for what a probe records.
        """
        now = utc_now_iso("seconds")
        columns = dict(values)
        columns["updated_at"] = now
        if actor is not None:
            columns["updated_by"] = actor
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT OR IGNORE INTO assistant_settings (id, updated_at) VALUES (?, ?)",
                (_ROW, now),
            )
            names = sorted(columns)
            cursor.execute(
                "UPDATE assistant_settings SET "  # noqa: S608 - names come from this module
                + ", ".join(f"{name} = ?" for name in names)
                + " WHERE id = ?",
                [*(columns[name] for name in names), _ROW],
            )


def _loads(text: str) -> dict[str, Any]:
    """
    Args:
        text: A JSON object as the store keeps it.

    Returns:
        The object, empty for anything else.
    """
    try:
        decoded = json.loads(text or "{}")
    except ValueError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _prices(prices: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, float]]:
    """
    Args:
        prices: The table the operator wrote.

    Returns:
        It, checked.

    Raises:
        ValidationError: A name is not a model's, a price is not one, or there are too many.
    """
    if len(prices) > 50:
        raise ValidationError("The price table holds at most 50 models", field="prices")
    return {
        _word("model", name, longest=200): Price.from_dict(values).to_dict()
        for name, values in prices.items()
    }


def _key_path() -> Path:
    """
    Returns:
        Where the key of the sealed secrets beside the configuration lives.
    """
    return channel_secrets.key_path(config_module.DEFAULT_CONFIG_PATH)
