# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The built-in assistant's base: its provider, its settings, its limits and its conversations (item 171).

The assistant is optional and off until an administrator turns it on, and nothing here is loaded
until something asks for it: this file imports nothing, so ``noust --version`` and the console's
start do not pay for a feature that is not on. What the modules are:

- :mod:`~noust.core.assistant.client`: the one client of a model provider, over the Chat
  Completions protocol, with the standard library.
- :mod:`~noust.core.assistant.presets`: a few providers that speak it, as data.
- :mod:`~noust.core.assistant.settings`: what the operator chose, with the key sealed.
- :mod:`~noust.core.assistant.probe`: what a provider turned out to be able to do.
- :mod:`~noust.core.assistant.masking`: what never goes to a model, and the reversible mask of
  emails and addresses for a provider that is not on the operator's side.
- :mod:`~noust.core.assistant.limits`: the ceilings a conversation runs under.
- :mod:`~noust.core.assistant.conversations`: what a person said to it, kept in the store.

The loop that investigates, the tools and the console are built on these (the next lane) and
never reach around them.
"""
