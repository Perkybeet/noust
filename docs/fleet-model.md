# How a central drives a server

Part of [The Noust central](CENTRAL.md).

## How far a central may go on a server

Each server sets a **ceiling** for every central it trusts, and enforces it itself where the
central's token is admitted, whatever the central claims for its operator:

| Ceiling | A central may |
|---------|---------------|
| `read` | read everything, change nothing |
| `deploy` | also start, stop, restart, update and roll back the applications already there, and take backups (not delete them or change where they go) |
| `admin` (default) | everything, except the server's own accounts, security settings and audit log, and what reaches the host (below) |

`host access` (off by default) additionally lets a central with `admin` reach the host itself:
change how the server is reached (SSH keys, sshd, the firewall, system accounts) and make
root-equivalent changes (raw systemd units, cron commands, backup hooks, raw site
configuration, an application's deploy hooks, moving an application to its own account), each of
which runs as root, or with an application's secrets, and so is host access by another name. A
compromised central is held to the ceiling of each server; the server's own accounts, tokens
and two-factor sign-in are never a central's to change.

When a server requires four-eyes approvals (`approval.enabled`, or the ENS profile), a
central's call that needs one is let through only with the central's word that it was approved
there: `X-Noust-Approval` (the central's request number) and `X-Noust-Approved-By` (who
approved it). The server cannot check the central's decision, only that one was taken: it
refuses the call when either header is missing or malformed, or when the approver named is the
operator who asked, and its audit log records both people and the central's token.

Sudo mode is confirmed on the central, not on the server. When a call needs it, the central asks
its own operator and vouches for the answer with `X-Noust-Elevated`; the server accepts that and
does not apply its own `auth.sudo.*` policy to the call, whatever profile the server runs
(`ens-medium` included). The central's policy is therefore the one in force for what its
operators do through it: a central that manages servers on the `ens-medium` profile has to run
the `ens-medium` profile itself (see [ENS.md](ENS.md), section 5.2).

```bash
noust fleet access                           # on the server: the ceiling in force
noust fleet access --level read              # takes effect on the next request
noust fleet access --host-access on
```

It is set only on the server's command line (or with `noust fleet authorize --access ...
--allow-host-access`); there is no API that raises it. The server publishes it at
`GET /api/auth/fleet/self`, and the central records it when it adds or tests the server
(`noust node show` and `noust node list` show it) to grey out what the server would refuse. A
3.0 server publishes nothing and shows as `unknown`.

## What the fleet is not

These are decisions, not gaps waiting for a release. Each one follows from what the fleet is: a
central that reaches independent servers through tunnels it opens outward, drives them through their
own API and holds no shell and no copy of what they run.

- **It is not a cluster.** There is no Swarm, no Kubernetes and no scheduler that decides where a
  workload runs. Docker Swarm is on its way out upstream, and Kubernetes is a different product with
  a different operator; Noust will not manage either. Each application lives on the servers you chose,
  as systemd units behind nginx or as the Compose stack the server runs, and each server stays useful
  on its own if the central is gone. Spreading an application is [a placement](central-operate.md#an-application-on-several-servers):
  the same commit on each server, behind a balancer of yours.
- **How that differs from the others.** Komodo is a Compose-first tool with an agent on every server
  that the core reaches; Uncloud is a multi-machine Docker orchestrator with no control plane, a
  WireGuard mesh between the machines and its own ingress. Both treat the containers as the unit and
  need Docker on every machine. Noust's fleet is built around the server instead: an application is a
  native service or a Compose stack on a server that does not need Docker for the rest, the server runs
  no agent beyond Noust itself, the central never gets a shell, and each server sets a ceiling for it.
  A mesh between servers, service discovery across machines and moving a running workload by itself
  are not part of it; a private network between your servers is yours to provide (a bastion in front
  of a server is [supported](central-servers.md#a-server-behind-a-bastion)).
- **It does not run a registry between a build server and the rest.** By default builds run on the server
  that serves the application, as an unprivileged account in a sandbox, so the build and the run see
  the same machine. A dedicated build server is optional
  ([building on one server and running on others](central-operate.md#building-on-one-server-and-running-on-others)): the
  release it builds travels to the servers as a bundle checked against its SHA-256, through the tunnels, not as an
  image through a registry, which is the shape other platforms need because their builds are images.
  For an application that already ships an image, **build it once and roll the tag out** (`noust fleet
  placement rollout --image-tag`): your CI builds and pushes, and every server pulls the same tag. Noust
  does not run the registry. The per-server side of building (how many builds at once, how many may
  wait, how long one may take) is each server's own: `deploy.max_concurrent_builds`,
  `deploy.queue_limit` and `deploy.max_minutes` (**Settings > General**, [CONFIG.md](CONFIG.md)).
- **It does not run your CI.** A runner executes code from a repository you may not control, with
  network and often the container socket, on a machine that also serves production. Noust keeps that
  out of the fleet: your CI runs where you trust it and calls Noust to deploy ([ci.md](ci.md)) with a
  token of scope `deploy`, which can update and roll back applications that exist and nothing else.
