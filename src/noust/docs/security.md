# Security

Noust runs as root and deploys code from repositories onto the machine it manages. This guide
states what it defends against, what it does not, and every control involved, so an operator
can decide how to expose it. [SECURITY-MODEL.md](SECURITY-MODEL.md) is its companion: each guarantee
with the failure it answers, the tests that fail if it stops holding, the numbers generated from
the code, and how to verify a release.

The rest of this guide is on pages of its own:

- [Expose the console safely](security-console.md)
- [Authentication](security-sign-in.md)
- [Roles, permissions and the fleet](security-roles.md)
- [Sudo mode and four-eyes approvals](security-approvals.md)
- [The audit log, and an incident](security-audit.md)
- [Secrets, databases and backups](security-secrets.md)
- [Threat model: what Noust defends, and what it does not](threat-model.md)

## Compliance (ENS)

`security.profile: ens-medium` fixes the values Spain's Esquema Nacional de Seguridad, category
MEDIUM, expects (sessions, lockout, passwords, token lifetimes, audit retention, approvals,
`--reason`, backup encryption, the console's certificate), stated once in
`noust.core.ens.profile` and read by every area. `noust ens check` compares the server with it
(exit 0, 1 or 2), `noust ens report` writes the evidence bundle, and the console shows both under
Settings > ENS report. Code can be restricted to named origins with
`security.allowed_sources`. The full mapping to the RD 311/2022 measures, and what stays the
organisation's, is in [ENS.md](ENS.md).

## Reporting a vulnerability

Please report security issues privately, through a GitHub private advisory or by email to
yago.lopez.adeje@gmail.com, rather than in a public issue. The process, the timeline and what
is in scope are in [SECURITY.md](../SECURITY.md).
