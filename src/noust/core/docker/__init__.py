# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one door from Noust to Docker.

Noust talks to Docker with its command line, through :class:`~noust.core.runner.CommandRunner`
and as an argument list, never a shell and never the socket (rule 1). What this package adds is
that every object Docker has (a container, an image, a volume, a network, a Compose project) is
resolved to **whose it is** before anything is allowed to be done to it, in one place (rule 4):

- :mod:`~noust.core.docker.engine`: which Docker there is and what does not fit in it (the
  engine's doctor).
- :mod:`~noust.core.docker.reading`: Docker's own JSON, read into typed objects.
- :mod:`~noust.core.docker.owners`: the owner of each object, decided by what Noust registered and
  never by what the object says of itself.
- :mod:`~noust.core.docker.judge`: an action is (owner, action, object), and what is an
  application's is changed through the application.
- :mod:`~noust.core.docker.gate`: :class:`DockerGate`, which the console, the command line and the
  jobs ask.
- :mod:`~noust.core.docker.names`: what Noust calls the things it makes.
- :mod:`~noust.core.docker.states`, :mod:`~noust.core.docker.inspect`, :mod:`~noust.core.docker.stats`
  and :mod:`~noust.core.docker.events`: a container from the inside (its state as one word, how it ended,
  what it uses, how often it restarted lately).
- :mod:`~noust.core.docker.summary`, :mod:`~noust.core.docker.restart_loss` and
  :mod:`~noust.core.docker.cleanup`: what asks for attention, what would not come back after a restart,
  and a cleanup as a plan that is applied as it was shown.

Importing the package loads none of them: a name is imported when it is first read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from noust.core.lazy_exports import lazy_exports

if TYPE_CHECKING:
    from noust.core.docker.cleanup import CleanupPlan
    from noust.core.docker.engine import EngineInfo, EngineReport, diagnose, read_engine
    from noust.core.docker.errors import (
        DockerNotFoundError,
        DockerOwnedError,
        DockerPlanChangedError,
        DockerRefusedError,
        DockerUnavailableError,
    )
    from noust.core.docker.gate import ContainerDetail, DockerGate
    from noust.core.docker.judge import Can, can, judge
    from noust.core.docker.models import (
        NOBODY,
        Action,
        ComposeProject,
        Container,
        Image,
        Network,
        ObjectKind,
        Owner,
        OwnerKind,
        Page,
        Removal,
        Volume,
    )
    from noust.core.docker.owners import OwnerIndex, Registry, registry_from_store

__getattr__, __dir__ = lazy_exports(
    globals(),
    {
        "Action": "noust.core.docker.models",
        "Can": "noust.core.docker.judge",
        "CleanupPlan": "noust.core.docker.cleanup",
        "ComposeProject": "noust.core.docker.models",
        "Container": "noust.core.docker.models",
        "ContainerDetail": "noust.core.docker.gate",
        "DockerGate": "noust.core.docker.gate",
        "DockerNotFoundError": "noust.core.docker.errors",
        "DockerOwnedError": "noust.core.docker.errors",
        "DockerPlanChangedError": "noust.core.docker.errors",
        "DockerRefusedError": "noust.core.docker.errors",
        "DockerUnavailableError": "noust.core.docker.errors",
        "EngineInfo": "noust.core.docker.engine",
        "EngineReport": "noust.core.docker.engine",
        "Image": "noust.core.docker.models",
        "NOBODY": "noust.core.docker.models",
        "Network": "noust.core.docker.models",
        "ObjectKind": "noust.core.docker.models",
        "Owner": "noust.core.docker.models",
        "OwnerIndex": "noust.core.docker.owners",
        "OwnerKind": "noust.core.docker.models",
        "Page": "noust.core.docker.models",
        "Registry": "noust.core.docker.owners",
        "Removal": "noust.core.docker.models",
        "Volume": "noust.core.docker.models",
        "can": "noust.core.docker.judge",
        "diagnose": "noust.core.docker.engine",
        "judge": "noust.core.docker.judge",
        "read_engine": "noust.core.docker.engine",
        "registry_from_store": "noust.core.docker.owners",
    },
)

__all__ = [
    "NOBODY",
    "Action",
    "Can",
    "CleanupPlan",
    "ComposeProject",
    "Container",
    "ContainerDetail",
    "DockerGate",
    "DockerNotFoundError",
    "DockerOwnedError",
    "DockerPlanChangedError",
    "DockerRefusedError",
    "DockerUnavailableError",
    "EngineInfo",
    "EngineReport",
    "Image",
    "Network",
    "ObjectKind",
    "Owner",
    "OwnerIndex",
    "OwnerKind",
    "Page",
    "Registry",
    "Removal",
    "Volume",
    "can",
    "diagnose",
    "judge",
    "read_engine",
    "registry_from_store",
]
