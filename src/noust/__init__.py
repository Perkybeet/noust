# Copyright (c) 2024-2025 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Noust - deploy and manage web applications on Linux servers.

Called WASM (Web App System Management) until 3.0.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _installed_version

#: Fallback used when running from a source tree that was never installed, such
#: as an OBS build directory. The single source of truth is the ``version``
#: field of pyproject.toml; ``scripts/release.py`` keeps this literal and the
#: distribution packaging files in step with it.
_FALLBACK_VERSION = "3.1.14"

#: The distribution names this package has been published under: ``noust``
#: from 3.0, ``wasm-cli`` before (and as the transitional package after).
_DISTRIBUTIONS = ("noust", "wasm-cli")


def _read_version() -> str:
    """
    Read the installed version, whichever name the distribution has.

    Returns:
        The version of the first distribution found, else the fallback.
    """
    for distribution in _DISTRIBUTIONS:
        try:
            return _installed_version(distribution)
        except PackageNotFoundError:
            continue
    return _FALLBACK_VERSION  # pragma: no cover - only hit in uninstalled trees


__version__ = _read_version()

__author__ = "Yago López Prado"
__license__ = "AGPL-3.0-or-later"

from noust.core.config import Config
from noust.core.exceptions import NoustError
from noust.core.logger import Logger

__all__ = [
    "Config",
    "Logger",
    "NoustError",
    "__version__",
]
