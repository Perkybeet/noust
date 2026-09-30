# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Spain's Esquema Nacional de Seguridad (RD 311/2022), category MEDIUM.

Noust is one component of an organisation's system, not the system, so it
cannot be "ENS certified"; what it can do is enforce what a component can
enforce and produce the evidence an auditor asks for. This package holds that:

- :mod:`~noust.core.ens.profile`: the ``ens-medium`` profile's values, in one
  place every area reads (sign-in, the CLI's ``--reason``, approvals, audit
  retention, backups, TLS).
- :mod:`~noust.core.ens.checks`: ``noust ens check``, the drift against the
  profile, the exposure and the indicators, each mapped to its measure.
- :mod:`~noust.core.ens.report`: ``noust ens report``, the evidence bundle.
- :mod:`~noust.core.ens.inventory`: owner, criticality and classification per
  application (op.exp.1).
- :mod:`~noust.core.ens.access_review`: the periodic review of accounts and
  roles, attested (op.acc.4.4).
- :mod:`~noust.core.ens.origins`: where code may be deployed from (G18).
- :mod:`~noust.core.ens.envelope`: files encrypted and authenticated with the
  sealing primitives, for the central's backup.
- :mod:`~noust.core.ens.incident`: ``noust incident freeze`` and the console
  lockdown.

Importing the package imports nothing but this docstring: the profile is read
by modules the rest of Noust loads early, and the checks import half of it.
"""
