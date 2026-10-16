# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``git push`` as a way to deploy: a repository on this server per application, and who may push.

An application deployed this way has a bare repository under :func:`noust.core.paths.push_dir`,
owned by an unprivileged account (``noust-git``) whose only way in is SSH with a key an
operator authorized for that one repository, running ``git-receive-pack`` and nothing else.
A hook of root's in the repository tells the console that a branch moved, signed with the
application's webhook secret, and the console queues the update that every other door queues.

- :mod:`noust.pushdeploy.host`: the account and its restrictions in sshd, once per server.
- :mod:`noust.pushdeploy.keys`: the keys that may push, one forced command each.
- :mod:`noust.pushdeploy.hook`: the hook and its configuration.
- :mod:`noust.pushdeploy.repos`: enabling and disabling it for an application.
- :mod:`noust.pushdeploy.source`: filling an application's repository cache from the repository.
"""
