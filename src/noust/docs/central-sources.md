# Deploy from GitHub on the servers a central manages

Part of [The Noust central](CENTRAL.md).

A GitHub App has one private key, and it never leaves the machine that holds it. When that machine
is a central, the servers it manages deploy from the same repositories without a copy of the key,
without a deploy key per repository and without an address of their own for GitHub to call. The
central gives each of them three things:

- **Access to clone with.** A token scoped to the repositories that server deploys from and to
  reading their contents, good for an hour and renewed before it ends. The server keeps it sealed.
- **The pushes.** GitHub delivers to the central; the central finds the servers that deploy that
  repository and branch and hands each the event. The server queues the update exactly as it does
  for a webhook of its own.
- **The status.** The central follows what the server started and writes the status of the commit
  and the comment of the pull request, with credentials a server never holds.

Nothing here is needed for a server to deploy from a deploy key or from a credential of its own;
that keeps working, and it is what keeps working while the central is away.

## Set it up

1. **Create the App on the central** (console: Integrations, GitHub; or `noust github setup`). A
   [hub](CENTRAL.md) may have it: it deploys nothing itself, but it receives the pushes and hands
   them on.
2. **Give GitHub an address to call.** The central's webhook is the App's; if GitHub cannot reach
   it, a push to a repository a server deploys deploys nothing. While you set the App up the
   console says so, with the two ways out: on a server with a web server,
   `noust web expose-hooks hooks.example.com`; behind a proxy of your own,
   `noust config set web.hooks_url https://hooks.example.com/hooks`.
3. **Install the App on the account** and select the repositories your servers deploy.
4. **Let the central deploy for the server**: the server's ceiling must be `deploy` or higher
   (`noust fleet access --level deploy` on the server). A server held to `read` takes neither a
   lease nor a push, and says so.

A server on an older Noust than 4.1 cannot hold a lease; the central says it needs updating. A
server marked **independent** is handed nothing.

## The first application of a repository

An application is made by cloning its repository, and a server holds access only to the
repositories its applications deploy from. The console therefore asks the central before it creates
the first one (`POST /api/fleet/sources/leases` with `nodes` and `repositories`); the access is kept
for half an hour, enough for the application to exist and name the repository itself. From then on
the central looks at the server every minute.

## What a server holds

`GET /api/sources/leases` on a server lists the leases it holds: which central made them, for which
repositories, and how many seconds are left. Never the token: it is a sealed secret, it is in no
argument of any process and in no audit record. A lease is renewed with twenty-five minutes left, at
once when an application is created or removed, and not at all for a server that is independent or
no longer in the fleet. One that is not renewed ends within the hour.

### When the central goes quiet

Anything that starts on the server (a scheduled update, a preview) and clones a repository after its
lease ended fails, and says exactly that:

> The GitHub access that nas leased to this server for you/app ended at 2026-10-29 10:12 UTC, and
> nas has not called since 2026-10-29 10:05 UTC.

and not "the repository is private". If the central did call since but did not renew (it cannot
reach GitHub, or the App no longer covers the repository) the sentence says it did not renew. Either
way the server's own ways to clone are untouched: give the application a deploy key
(`noust app deploy-key --help`) and it does not depend on the central.

## The pushes

Everything that GitHub delivers to the central's `/hooks/github` is answered at once; the central
writes the event down for each server that has an application following it and a worker hands it
over. The rule that decides is the one the servers apply to their own webhook: an application follows
a push when its source is the repository and its branch is the one pushed (or the repository's
default); a preview and an application that follows tags are not updated by a push.

- A server that is down when the event arrives gets it when it is back, for up to an hour. After
  that the delivery is `expired`, says so, and can be handed over again by hand
  (`POST /api/fleet/sources/deliveries/{id}/retry`).
- A server that refuses for good (an older Noust, a ceiling that only reads, an event it will not
  take) is not asked again; the delivery is `failed` with the server's own words.
- The server's audit trail names the delivery (`github:<delivery id>`) and the central.

## The status of the commit and the comment of the pull request

The central asks the server's API every few seconds for what it started. A deployment is written on
GitHub as `queued`, `in_progress` and what it ended in (`success`, `failure` with the first line of
why, `failure` "rolled back", or `inactive` for one an operator cancelled). A preview has one
comment per pull request, edited as the preview changes and not said twice. A server that stays
away for three hours is given up on, and GitHub is told, not left saying "in progress".

## What it does not cover

GitLab, Gitea, Forgejo and Bitbucket have no App held by the central: their webhooks are per
application, signed with a secret only the server has, and reach the server directly; their
credentials and statuses are the server's own, as before. Nothing is forwarded for them.

## For scripts and the console

| Route | What |
|---|---|
| `GET /api/fleet/sources` | Each server's leases and what the App does not reach, whether GitHub can reach the central, the events in each state |
| `GET /api/fleet/sources/deliveries` | The events handed or owed to the servers, with what each queued and what is followed |
| `POST /api/fleet/sources/leases` | Lease again now; `repositories` asks ahead of the first application |
| `POST /api/fleet/sources/deliveries/{id}/retry` | Hand over again an event that was given up on |
| `GET`/`PUT`/`DELETE /api/sources/leases` | On a server: what it holds; a central's lease; a central's take-back |
| `POST /api/sources/events` | On a server: an event its central forwards |

Every problem in an answer is a sentence with a code and, beside it, the words of GitHub or of the
server verbatim.
