# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The words of the notification about a change to how the server is reached that was undone (item 138).

A change to the firewall or to SSH waits for somebody to confirm it from a new session and is undone
by a timer when nobody does. They belong to :data:`noust.core.messages.MESSAGES` (which merges this
table in, so there is one catalog and one test of it); they live in their own module so the catalog
does not grow a section per lane. The same rules hold: a whole sentence with placeholders, never a
fragment, in both languages with the same placeholders, and nothing here for a program's own words
(what the undo printed travels as an excerpt).

The ``change.*`` sentences say *what the change was*, from what it is (a code and its values, kept
in the ledger beside the change: :mod:`noust.core.access_changes`) and not from an English sentence
somebody wrote when it was made, which a Spanish notice could only paste in. The English of the
ledger's ``title`` is the English of these sentences, so there is one place that says it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from noust.core.messages import Locale

ACCESS_MESSAGES: dict[str, dict[Locale, str]] = {
    "title.access.firewall_reverted": {
        "en": "Firewall change undone",
        "es": "Cambio del cortafuegos deshecho",
    },
    "title.access.ssh_reverted": {
        "en": "SSH change undone",
        "es": "Cambio de SSH deshecho",
    },
    # The summary says what the title and the facts do not: what stands now and what to do. The
    # change itself is the first fact, so it is not said twice.
    "summary.access.firewall_reverted": {
        "en": (
            "Nobody confirmed it within {minutes} {unit}, so the firewall is as it was before the "
            "change. Make the change again and confirm it from a new session."
        ),
        "es": (
            "Nadie lo confirmó en {minutes} {unit}, así que el cortafuegos está como antes del "
            "cambio. Vuelve a hacerlo y confírmalo desde una sesión nueva."
        ),
    },
    "summary.access.ssh_reverted": {
        "en": (
            "Nobody confirmed it within {minutes} {unit}, so sshd uses the configuration it had "
            "before the change. Apply the change again and confirm it from a new login."
        ),
        "es": (
            "Nadie lo confirmó en {minutes} {unit}, así que sshd usa la configuración que tenía "
            "antes del cambio. Aplícalo otra vez y confírmalo desde un inicio de sesión nuevo."
        ),
    },
    "fact.change": {"en": "Change", "es": "Cambio"},
    "fact.change_by": {"en": "Applied by", "es": "Lo aplicó"},
    "fact.change_at": {"en": "Applied", "es": "Aplicado"},
    "excerpt.access.undo": {"en": "What the undo printed", "es": "Lo que imprimió al deshacerlo"},
    # -- What a change was. The port is ``5432/tcp`` (``5432`` for both protocols) and the source an
    # address or a network: values, never words. A rule the tool printed is its own text.
    "change.firewall_allow": {
        "en": "Allow {port} from {source}",
        "es": "Permitir {port} desde {source}",
    },
    "change.firewall_allow_any": {
        "en": "Allow {port} from anywhere",
        "es": "Permitir {port} desde cualquier origen",
    },
    "change.firewall_deny": {
        "en": "Deny {port} from {source}",
        "es": "Denegar {port} desde {source}",
    },
    "change.firewall_deny_any": {
        "en": "Deny {port} from anywhere",
        "es": "Denegar {port} desde cualquier origen",
    },
    "change.firewall_allow_replacing": {
        "en": "Allow {port} from {source}, and delete {rules}",
        "es": "Permitir {port} desde {source} y eliminar {rules}",
    },
    "change.firewall_allow_any_replacing": {
        "en": "Allow {port} from anywhere, and delete {rules}",
        "es": "Permitir {port} desde cualquier origen y eliminar {rules}",
    },
    "change.firewall_deny_replacing": {
        "en": "Deny {port} from {source}, and delete {rules}",
        "es": "Denegar {port} desde {source} y eliminar {rules}",
    },
    "change.firewall_deny_any_replacing": {
        "en": "Deny {port} from anywhere, and delete {rules}",
        "es": "Denegar {port} desde cualquier origen y eliminar {rules}",
    },
    "change.firewall_delete": {
        "en": "Delete the firewall rule “{rule}”",
        "es": "Eliminar la regla «{rule}» del cortafuegos",
    },
    "change.firewall_enable": {
        "en": "Turn on ufw, denying incoming by default",
        "es": "Activar ufw, denegando la entrada por defecto",
    },
    "change.firewall_disable": {"en": "Turn off ufw", "es": "Desactivar ufw"},
    "change.firewall_stop": {"en": "Stop firewalld", "es": "Detener firewalld"},
    "change.ssh_disable_passwords": {
        "en": "Turn off SSH password logins",
        "es": "Desactivar el acceso por contraseña en SSH",
    },
    "change.ssh_root_prohibit_password": {
        "en": "Let root log in over SSH with a key only",
        "es": "Permitir que root entre por SSH solo con una clave",
    },
    "change.ssh_root_no": {
        "en": "Refuse root logins over SSH",
        "es": "Rechazar los accesos de root por SSH",
    },
    "change.ssh_no_empty_passwords": {
        "en": "Refuse logins with an empty password",
        "es": "Rechazar los accesos con una contraseña vacía",
    },
    "change.ssh_sensible_defaults": {
        "en": "Apply sensible SSH limits",
        "es": "Aplicar límites razonables a SSH",
    },
    "change.ssh_verbose_logging": {
        "en": "Log the key fingerprint of every SSH login",
        "es": "Registrar la huella de la clave en cada acceso por SSH",
    },
    # A change recorded before the ledger kept what it was: only the sentence somebody wrote then
    # is left, and it is shown as the system's own text, between quotes, in whatever language it
    # was written.
    "change.recorded": {"en": "“{text}”", "es": "«{text}»"},
}

#: Nouns the new sentences count; merged into :data:`noust.core.messages._PLURAL_FORMS`.
ACCESS_PLURALS: dict[str, dict[Locale, tuple[str, str]]] = {
    "minute": {"en": ("minute", "minutes"), "es": ("minuto", "minutos")},
}
