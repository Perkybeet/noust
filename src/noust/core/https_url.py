# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The rule for an address of the console that is put in front of a person: https, absolute, or unset.

``web.public_url`` (where the console is reachable from a browser) and
``notifications.console_url`` (where a server's pages are inside the console of the central that
manages it) are the same kind of value and are judged by the same rule, here and nowhere else: an
address is handed to an operator's phone or chat client, and an ``http://`` link there is a
credential-carrying session cookie away from being read in the clear, the outcome the console's own
cookies are marked Secure to avoid. This module is below ``config.py``, which applies it, so that
the rule does not live in the module that is already the largest of the package.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from noust.core.exceptions import ConfigError

__all__ = ["https_url_validator"]


def https_url_validator(key: str) -> Callable[[Any], str]:
    """
    Make the rule for one setting.

    Args:
        key: The setting, which the error names (``web.public_url``).

    Returns:
        A validator that accepts an absolute ``https://`` URL, returned without a trailing slash
        so that every caller that joins a path onto it produces exactly one, or leaves the setting
        unset (``""``); it raises :class:`~noust.core.exceptions.ConfigError` for anything else.
    """

    def validate(value: Any) -> str:
        text = "" if value is None else str(value).strip()
        if not text:
            return ""
        parsed = urlparse(text)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ConfigError(
                f"{key} must be an absolute https:// URL",
                details=f"Got {text!r}. Use an address such as https://console.example.com.",
            )
        return text.rstrip("/")

    return validate
