# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Command line interface.

Deliberately empty of imports. The package used to pull in the argparse tree,
which imported every command module, so one broken optional dependency
anywhere took down the whole CLI including ``noust --version``. The command
tree lives in :mod:`noust.cli.app` and loads each command module only when that
command is invoked.
"""
