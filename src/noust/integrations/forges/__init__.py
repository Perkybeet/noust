# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Code hosts other than GitHub, written to: commit statuses and pull request comments.

GitHub's deployment statuses and pull request comment go through the App
(:mod:`noust.integrations.github`). GitLab, Gitea, Forgejo, Gogs and Bitbucket Cloud
have no App to speak through; what they have is the credential an operator already gave
Noust to clone the repository (:class:`noust.managers.source_credentials.GitCredentialManager`,
or a GitLab connection), and an API that takes it. This package is how a deployment's start
and end become a status on the commit, and a preview becomes a comment kept up to date on
its pull request or merge request, on those hosts.

- :mod:`~noust.integrations.forges.hosts`: which kind of host an address is.
- :mod:`~noust.integrations.forges.api`: one small client per kind, with the same four calls.
- :mod:`~noust.integrations.forges.statuses`: the subscriber of deployment events, and the
  comment of a preview.
"""
