#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Write ``panel/src/api/serverTextCodes.gen.ts`` from the catalog of ``noust.core.server_text``.

The console translates the sentences the server says by their code, and a code it has no entry for
is shown in English. This table is what the console's catalogs are held to: a test fails for a code
that English or Spanish lacks, or that says it with other values. ``--check`` fails when the
committed file is not what the catalog generates (a test and CI run it); without it the file is
written.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from noust.core.server_text import typescript_codes  # noqa: E402

DEFAULT_OUTPUT = ROOT / "panel" / "src" / "api" / "serverTextCodes.gen.ts"


def main(argv: list[str] | None = None) -> int:
    """
    Args:
        argv: The arguments; ``sys.argv`` by default.

    Returns:
        0 when the file was written or is current, 1 when ``--check`` finds it stale.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail when the file is stale.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Where to write.")
    args = parser.parse_args(argv)
    wanted = typescript_codes()
    present = args.output.read_text(encoding="utf-8") if args.output.exists() else None
    if args.check:
        if present != wanted:
            print(f"{args.output} is stale: run python scripts/generate_server_text.py")
            return 1
        return 0
    args.output.write_text(wanted, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
