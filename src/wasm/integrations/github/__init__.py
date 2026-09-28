# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
This server's GitHub App (2.2).

One App per server, created from the console with GitHub's manifest flow and
owned by the operator's account. Its installations give WASM short-lived
tokens to clone private repositories, receive push and pull request events at
``/hooks/github``, report deployment statuses and comment preview links.
"""
