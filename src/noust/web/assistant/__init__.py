# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The part of the built-in assistant that lives in the console's layer (item 171).

What does not need the console is in :mod:`noust.core.assistant`. What is here is what knows the
API's tokens, roles and audit trail: the identity the assistant acts with
(:mod:`noust.web.assistant.identity`). Nothing here is imported until the assistant is used.
"""
