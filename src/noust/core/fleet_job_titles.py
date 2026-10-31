# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The title of a fleet job, as a sentence the console says in its own language.

A central's bulk action runs as a console job whose name is its title and the number of servers
("Apply the fleet's settings on 2 servers"). That name is shown as it is by whatever lists jobs
(the Overview's recent activity, the notice that says the job ended), and in a Spanish console
it was an English sentence. The name is now one of the sentences of :mod:`noust.core.server_text`,
and what shows it asks for the code beside it.

The job's name is all a stored job keeps of its title, so :func:`coded` reads the sentence back
from the name: the English text and the code are one thing (the text is the catalog entry with
the values put in), and a name made by :func:`title` reads back as the sentence that made it,
for a job queued before the code existed as well as one queued now.
"""

from __future__ import annotations

import re

from noust.core.server_text import CATALOG, ServerText, say

#: Each bulk action's sentence for one server and for several. A table of literals, not a code built
#: from the action's name: a sentence that nothing asks for is a bug the catalog's test finds.
_TITLES: dict[str, tuple[str, str]] = {
    "certs_renew": ("fleet_job.certs_renew.single", "fleet_job.certs_renew.several"),
    "backups_run": ("fleet_job.backups_run.single", "fleet_job.backups_run.several"),
    "backups_verify": ("fleet_job.backups_verify.single", "fleet_job.backups_verify.several"),
    "apps_update": ("fleet_job.apps_update.single", "fleet_job.apps_update.several"),
    "apps_restart": ("fleet_job.apps_restart.single", "fleet_job.apps_restart.several"),
    "noust_update": ("fleet_job.noust_update.single", "fleet_job.noust_update.several"),
    "index_refresh": ("fleet_job.index_refresh.single", "fleet_job.index_refresh.several"),
    "os_updates": ("fleet_job.os_updates.single", "fleet_job.os_updates.several"),
    "settings_apply": ("fleet_job.settings_apply.single", "fleet_job.settings_apply.several"),
    "placement_rollout": (
        "fleet_job.placement_rollout.single",
        "fleet_job.placement_rollout.several",
    ),
    "placement_copy": ("fleet_job.placement_copy.single", "fleet_job.placement_copy.several"),
    "placement_ship": ("fleet_job.placement_ship.single", "fleet_job.placement_ship.several"),
    "dns_cluster": ("fleet_job.dns_cluster.single", "fleet_job.dns_cluster.several"),
}


def title(action: str, servers: int) -> ServerText | None:
    """
    Args:
        action: The bulk action's name (``certs_renew``).
        servers: How many servers it runs on.

    Returns:
        The sentence that names the job, or None for an action nobody gave one (a job of an area that
        has not been given codes yet keeps its English name).
    """
    codes = _TITLES.get(action)
    if codes is None:
        return None
    single, several = codes
    return say(single) if servers == 1 else say(several, count=servers)


def _pattern(template: str) -> re.Pattern[str]:
    """
    Args:
        template: A catalog entry whose only value is ``{count}``.

    Returns:
        The expression that reads the count back out of the entry's text.
    """
    return re.compile(re.escape(template).replace(re.escape("{count}"), r"(?P<count>\d+)"))


#: What reads a name back as its sentence: the text of the sentence for one server, and the
#: pattern of the one for several.
_READ_BACK: tuple[tuple[str, str, re.Pattern[str] | None], ...] = tuple(
    (code, CATALOG[code], None if "{count}" not in CATALOG[code] else _pattern(CATALOG[code]))
    for codes in _TITLES.values()
    for code in codes
)


def coded(name: str | None) -> ServerText | None:
    """
    Args:
        name: A job's name, as it is stored.

    Returns:
        The sentence the name is, when it is the title of a fleet job; None for any other name.
    """
    if not name:
        return None
    for code, template, pattern in _READ_BACK:
        if pattern is None:
            if name == template:
                return say(code)
            continue
        found = pattern.fullmatch(name)
        if found is not None:
            return say(code, count=int(found.group("count")))
    return None
