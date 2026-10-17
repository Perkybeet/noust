# Holding, freezing and scheduling deploys

A deploy is not only the moment an application is built. What decides when production changes is
as important: a push that undoes a rollback an hour after somebody fixed the incident, a deploy
on Black Friday, an update at three in the morning with nobody to read a warning. This page covers
the controls Noust gives around a deploy: [pausing what deploys by itself](#pausing-what-deploys-by-itself),
[freezing deploys](#freezing-deploys), [how much history is kept](#how-much-history-is-kept),
[how an application is told to stop](#how-an-application-is-told-to-stop),
[updating on a schedule](#updating-on-a-schedule) and
[building now and serving later](#building-now-and-serving-later). They are in an application's
**Deploys** settings in the console, in `noust deploy ...` on the terminal and under
`/api/apps/{domain}/rollout` in the API; the server-wide freeze is in **Settings > General**.

## What holds a deploy, and what does not

Two things hold deploys and they are held for different reasons.

| | A pause | A freeze |
|---|---|---|
| Says | what deploys the application **by itself** stops | **nobody** changes production now |
| Holds | pushes, new tags, the GitHub App, schedules, and a deploy asked for by an API token | all of those, and an update somebody asks for |
| A person's update | still runs | refused with the reason, unless they say it must go ahead |
| Covers | one application | one application, or the whole server |
| Ends | when somebody resumes | at its end time, or when somebody lifts it |

Two things are never held by either:

- **A rollback.** Going back is how an incident ends.
- **A preview.** It is a throwaway copy of the application, not production.

A pause is about what happens without anyone asking, so an update a person starts runs through it;
only a freeze stops that.

The same answer is given everywhere a deploy can start (`lifecycle.update_app`, and the queues in
front of it), so the console, the command line, the webhooks and the schedules cannot disagree.

## Pausing what deploys by itself

```bash
noust deploy pause shop.example.com --reason "looking into the checkout bug"
noust deploy held shop.example.com          # what holds it, if anything
noust deploy resume shop.example.com
```

While an application is paused a push, a tag or a schedule is not deployed. Each push and tag is
counted instead, and resuming says how many arrived meanwhile, so nothing is lost without a word: the
next push deploys the newest, or `noust update` does now. A schedule's held run is not counted, because
nothing may have arrived: it is the schedule's last outcome (`noust deploy schedule show`), and its
next run after resuming deploys whatever is new. The console shows who paused it and why in the
application's **Deployments** and **Deploys** pages.

### After a rollback

An application something deploys by itself (a push webhook, the GitHub App or followed tags) is
paused by Noust **when it is rolled back**, so that the next push does not deploy again the code
that was rolled back from. The pause says it was the rollback that made it. Resume when the cause
is fixed. An application nothing deploys by itself is not paused, because there is nothing to hold.

```bash
noust deploy pause-on-rollback shop.example.com          # show
noust deploy pause-on-rollback shop.example.com off      # keep deploying after a rollback
```

## Freezing deploys

```bash
noust deploy freeze --reason "Black Friday: nothing ships" --until 2026-11-29T06:00
noust deploy freeze shop.example.com --reason "incident 412" --hours 4
noust deploy freeze --from 2026-12-24T18:00 --until 2026-12-26T08:00 --reason "holiday"
noust deploy freezes                         # what holds now, or will
noust deploy unfreeze shop.example.com       # lift the application's; the server's stays
```

Without a domain the freeze covers the whole server (that one is the instance's to decide, not a
team's). A freeze can start later and can have no end; one with an end lasts at most 90 days. While
it holds, a deploy somebody asks for is refused with the reason and who set it, and the hint says
how to go ahead:

```bash
noust update shop.example.com --during-freeze     # recorded in the audit log as deploys.frozen_override
```

The console asks the same question in a dialog ("Deploy anyway"), and the API answers
`409 deploys_frozen` with `freeze` in the body; `frozen_ok: true` in the request goes ahead. A push
or a schedule during a freeze waits and is counted like one during a pause.

## How much history is kept

An application keeps the last **20** deploys of history, with their build logs. The server's number
is `deploy.history_keep`; an application can have its own, from 1 to 1000:

```bash
noust config set deploy.history_keep 50
noust deploy keep shop.example.com 100       # this application keeps 100
noust deploy keep shop.example.com default   # follow the server again
```

Older deploys and their logs are deleted at the application's next deploy. This is the history the
console and `noust app deploylog` show. The builds kept on disk to go back to are another thing:
[`noust releases keep`](releases.md).

## How an application is told to stop

systemd waits 90 seconds and sends `SIGTERM`. An application that needs longer to finish what it is
doing, or a different signal, says so:

```bash
noust app stop-grace shop.example.com                       # show
noust app stop-grace shop.example.com --seconds 300 --signal sigint
noust app stop-grace shop.example.com --reset
```

The policy (`TimeoutStopSec` and `KillSignal`) is written into the unit now and read by systemd the
next time it stops it: nothing restarts. A redeploy writes it into the new unit too, and a
zero-downtime application or a monorepo gets it on every unit it runs as. A Docker Compose stack or
a container uses its own (`stop_grace_period` in the compose file), and a static site has no unit.

## Updating on a schedule

```bash
noust deploy schedule set shop.example.com --at weekly
noust deploy schedule set shop.example.com --at "Sun *-*-* 04:30" --no-backup
noust deploy schedule show shop.example.com
noust deploy schedule list
noust deploy schedule remove shop.example.com
```

`--at` is `daily` (03:00), `weekly` (Sunday 03:00), `monthly` (the first at 03:00) or any systemd
calendar expression. The timer is `noust-update-<app>.timer`; deleting the application removes it.
Each run:

1. asks whether the branch (or the tags it follows) has anything new, and does nothing if not
   (`--always` updates anyway);
2. takes the backup an update needs and **refuses to update when it does not happen**
   (`--no-backup` stops the schedule asking for that; an application with a database keeps its own
   rule, which is the same refusal, until `noust app backup-required DOMAIN off`), because nobody is
   there at three in the morning to read a warning;
3. is held by a pause or a freeze, and says so;
4. updates through the same code as `noust update`, behind the same health gate, which puts back what
   served when the new version does not answer.

How the last run ended (`updated`, `nothing new`, `held`, `backup failed`, `failed`) is kept with the
schedule and shown by `schedule show`, in the console and by `GET /api/apps/{domain}/update-schedule`.
The timer's unit fails when an update failed or was refused, so `systemctl --failed` shows it. A
clone of an application does not get the schedule of the original: it is another application.

## Building now and serving later

```bash
noust update shop.example.com --build-only     # build the branch into a release that waits
noust releases list shop.example.com           # it is listed as waiting
noust releases promote shop.example.com        # make the newest waiting release serve
```

A release built with `--build-only` is complete and is not served. Promoting it links it to the
application's own `.env` and shared folders, runs its pre-deploy hooks and migrations against the
application's database, and switches to it behind the health gate; nothing is built again, so what
goes live is what was built. Going back to a release that **was** served is `noust releases
rollback`, which runs no hook. A release that was built and never served is refused by `rollback`
and `activate` with the way to promote it, because going back to code nobody has run is not a
rollback.

The console shows it in **Deployments** as "Built, not serving" with **Make it serve**, and builds it
from **More actions > Build without serving**. A promotion during a freeze asks the same question as
an update does.

## API

| Method and path | Permission | What it does |
|---|---|---|
| `GET /api/apps/{domain}/rollout` | `apps.read` | history, stop policy, pause, `pause_on_rollback`, what deploys it by itself, the freeze over it |
| `PUT /api/apps/{domain}/rollout` | `apps.manage` | change `history_keep`, `stop_timeout`, `stop_signal` or `pause_on_rollback`; `null` goes back to the default |
| `POST /api/apps/{domain}/rollout/pause` | `apps.manage` | pause, with an optional `reason` |
| `POST /api/apps/{domain}/rollout/resume` | `apps.manage` | resume; answers how many pushes and tags were held |
| `GET /api/deploy-freezes` | `apps.read` | the freezes that hold now or start later (`include_past` for the rest) |
| `POST /api/deploy-freezes` | `apps.deploy` | freeze: `reason`, optional `domain`, `starts_at`, `ends_at` or `hours` |
| `DELETE /api/deploy-freezes/{id}` | `apps.deploy` | lift one |
| `GET`, `PUT`, `DELETE /api/apps/{domain}/update-schedule` | `apps.read`, `apps.manage` | when it updates itself, and how the last run ended |
| `POST /api/apps/{domain}/releases/stage` | `apps.deploy` | build without serving (a job) |
| `POST /api/apps/{domain}/releases/{id}/promote` | `apps.deploy` | make a waiting release serve (a job) |

A freeze of the whole server is the instance's to make and lift: a team member who tries gets a
403. Applications in a team's projects are told apart by the same scoping as everything else
([teams.md](teams.md)).

## What travels with an application

`noust app export` writes this in a `delivery` section (the history, the stop policy, whether a
rollback pauses, the schedule), and `noust app import` puts it back; so do clones and, between
servers, the copies the central makes. A pause and a freeze are states of the moment and are not
exported: a copy starts running. The preview options (how a preview is named, what it is given, a
copy of the database, forks) travel in the `previews` section ([previews.md](previews.md)).
