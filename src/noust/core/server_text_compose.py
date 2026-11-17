# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The sentences the guard of a Compose stack says for the console to translate.

A table of :mod:`noust.core.server_text`, which merges it into its catalog: one sentence is its
entry here and a code to ask for it, and the console's own catalogs say it in Spanish. Each line
is what a compose file asks of the server (``{who}`` is ``service web`` or ``job migrate``); the
two errors at the end are what a deployment is refused with, with the lines in ``{lines}`` and the
way out in the fix. Kept apart so the catalog does not grow past the size a module may have."""

from __future__ import annotations

SENTENCES: dict[str, str] = {
    # -- A path the file names, judged by what Docker will mount ------------------------------------
    "compose_guard.mount_link": "{who} mounts {source} from this server, through the link {link} that leads to {target}, outside the application",
    "compose_guard.path_unresolved": "{who} names {source}, which cannot be followed to a place on this server (a loop of links, a directory that cannot be read or a name that is not a path): what it would give cannot be judged",
    "compose_guard.runtime_socket": "{who} mounts the socket of a container runtime ({source}): it can start a container with this server's root filesystem",
    "compose_guard.door_noust": "{who} names {source} in {key}: that is Noust's own configuration and secrets, which no stack may read",
    "compose_guard.door_contains_noust": "{who} names {source} in {key}: that contains {directory}, where Noust keeps its configuration and secrets, which no stack may read",
    "compose_guard.door_all_apps": "{who} names {source} in {key}: that contains every application's files, the other applications' included, which no stack may read",
    "compose_guard.door_other_app": "{who} names {source} in {key}: that is another application's files, which no stack may read",
    "compose_guard.door_outside": "{who} names {source} in {key}, outside the application: Docker would read a file of this server that is not part of the repository",
    "compose_guard.door_link": "{who} names {source} in {key}, through the link {link} that leads to {target}, outside the application",
    "compose_guard.remote_context": "{who} builds from {source}, which Docker would download from the network: nothing on this server checks where that goes",
    "compose_guard.remote_include": "the compose file includes {source}, which Compose would download from the network: nothing on this server checks where that goes",
    # -- What a service is given of the machine ------------------------------------------------------
    "compose_guard.devices": "{who} is given the host device {device}",
    "compose_guard.capabilities_machine": "{who} adds the capability {capability}, which gives it this server",
    "compose_guard.cgroup_parent": "{who} runs in the cgroup {parent} of this server, outside the limits Docker gives it",
    "compose_guard.runtime": "{who} runs with the runtime {runtime}, not Docker's own: what that does with the container is not what Docker's runtime does",
    "compose_guard.build_privileged": "{who} builds with elevated privileges (build.privileged): the build can reach this server",
    "compose_guard.build_host_network": "{who} builds on this server's own network (build.network: host)",
    "compose_guard.provider": "{who} runs the program {provider} of this server when the stack starts, outside any container",
    # -- Whose each thing is ---------------------------------------------------------------------------
    "compose_guard.project_names": "the compose file names its own project {name} (name: or COMPOSE_PROJECT_NAME) and this application's is {project}: everything the stack makes is named after its project",
    "compose_guard.project_taken": "the compose file runs the stack as the project {name}, which belongs to {owner}: Compose would take over what that project has",
    "compose_guard.volume_taken": "{who} uses the volume {name}, which belongs to {owner}, not to this application",
    "compose_guard.network_taken": "{who} uses the network {name}, which belongs to {owner}, not to this application",
    "compose_guard.container_taken": "{who} names its container {name}, which belongs to {owner}, not to this application",
    "compose_guard.image_taken": "{who} runs the image {name}, which {owner} built: its code is not this application's to run",
    "compose_guard.external_volume": "{who} uses the volume {name}, which this stack does not make: it gives the stack whatever somebody else keeps there",
    "compose_guard.external_network": "{who} uses the network {name}, which this stack does not make: it joins the stack to whatever else is on that network",
    "compose_guard.paths_unresolved": "the compose file names paths of this server through variables and Docker Compose could not resolve the stack ({problem}): where they lead cannot be judged",
    # -- Adopting a stack that already runs with what the guard refuses ---------------------------------
    "compose_guard.adopt_host_access": "Adopting {domain} puts under Noust a stack whose containers have what is on this server",
    "compose_guard.adopt_host_access.hint": "{lines}\n\nEach line above gives a container something of this server, and adopting the stack is equivalent to root: it is recorded as accepted, with who and when, as the compose exception of {domain}. If you still want it adopted: {command}",
    "compose_guard.adopted_host_access": "Recorded as accepted by {who}: the containers of {domain} keep what they have of this server ({count}). To take it back: {command}",
    # -- What a deployment is refused with -------------------------------------------------------------
    "compose_guard.asks_for_root": "{domain}'s compose file asks for root on this server",
    "compose_guard.asks_for_root.hint": "{lines}\n\nThe compose file comes from the repository and Docker runs it as root: what is listed above gives a container this server, and the stack did not run with it before. Remove it, or record why this stack needs it and deploy again: {command}",
    "docker.grants_unreadable": "Docker did not say what the containers of the stack were given",
    "docker.grants_unreadable.hint": "Check that the daemon runs: systemctl status docker",
    "compose_guard.cannot_tell_what_it_has": "Docker did not say what {domain}'s containers already have of this server",
    "compose_guard.cannot_tell_what_it_has.hint": "{lines}\n\nThe compose file asks for what a new stack is refused, and whether this stack already ran with it is read from its containers, which could not be asked. Check that Docker runs (systemctl status docker) and deploy again; or record why this stack needs it: {command}",
    "compose_guard.asks_for_what_no_stack_may": "{domain}'s compose file reaches what no stack may reach",
    "compose_guard.asks_for_what_no_stack_may.hint": "{lines}\n\nWhat belongs to another application or to Noust (its files, its volumes, its images, its project) is not for a stack to use, and no exception allows it. Remove it from the compose file.",
}
