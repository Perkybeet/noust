#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Run the real Noust console against a seeded, sandboxed machine.

This is the path the end-to-end suites, CI and the documentation start; the code is the package
``scripts/console_server/`` beside it (``python scripts/console_server.py --help``).
"""

from __future__ import annotations

import sys
from pathlib import Path

# The package is named like this file and beats it on the path; the directory is put first because
# running a file by path (runpy, an isolated interpreter) does not.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from console_server.entrypoint import main

if __name__ == "__main__":
    sys.exit(main())
