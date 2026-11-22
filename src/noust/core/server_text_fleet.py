# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The sentences of the fleet that the server says for the console to translate (item 151).

A table of :mod:`noust.core.server_text`, which merges it into its catalog: one sentence is its entry
here and a code to ask for it, and the console's own catalogs say it in Spanish. These are what a
central says when a server it manages presents another SSH host key and a person decides whether to take
it (:mod:`noust.fleet.host_keys`). Kept apart so the catalog does not grow past the size a module may
have."""

from __future__ import annotations

SENTENCES: dict[str, str] = {
    "fleet.host_key.bastion": (
        "{node} is reached through a bastion, so this central cannot ask which host key it presents"
    ),
    "fleet.host_key.bastion.hint": (
        "Its address answers only through the bastion. Read the key on the server itself "
        "(ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub) and, if the server really changed, "
        "remove it and add it again: noust node remove {node}"
    ),
    "fleet.host_key.fingerprint_malformed": "That is not a host key fingerprint",
    "fleet.host_key.fingerprint_malformed.hint": (
        "A fingerprint is SHA256: followed by 43 letters, digits, + and /, as ssh-keygen prints it "
        "on the server itself: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub"
    ),
    "fleet.host_key.not_what_answers": (
        "{node} presents the key {presented}, not the key {given} that you gave, so nothing was "
        "changed"
    ),
    "fleet.host_key.not_what_answers.hint": (
        "Either the key of the server changed again, or the address answers for another server. "
        "Read the key on the server itself (ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub) and "
        "ask again."
    ),
}
