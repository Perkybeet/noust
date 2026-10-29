# Overview, map, charts, activity and logs

Part of [The console, page by page](console.md).

## Overview

How this server is doing, what needs you and what happened lately. Six key figures:
applications running and failed, deploys today, certificates due, backups in the last 24 hours,
disk, and pending system updates. Below them, what needs attention (worst first, each with the
way to fix it) beside recent activity as a timeline, then the machine's charts and quick actions.
When the charts' history is not being recorded, it says why and what to run. The Overview has a
second view, the [Map](#map). A server with nothing deployed shows one card instead, **Deploy your
first app**: **Try a demo** (a tiny page on a temporary address), **Use a repository**, four popular
recipes and **All recipes**, and under it **Then make it safe to leave alone** (backups, a copy off
the server, notifications and, while the account is not protected, **Protect your account**). On a
hub, the home page is the fleet summary.

## Map

`/map` (the Overview's second tab, or the palette: "Open the map of this server") draws how this server fits
together: the names that reach each application, the applications with their state, the databases they use,
the scheduled work they run and the data they keep (persistent paths and mounted paths), in fixed columns
from left to right, with the lines that connect them. It is **read from the server on every visit** and
carries no coordinates, so there is no canvas to arrange, nothing to save and nothing that moves when you
look again: the same server is the same picture. Pointing at or focusing an element lights its whole way,
as in a site's diagram; the same connections are a table with a link to each thing, and on a phone each
column is a list. A team member's map has its team's applications and databases and nothing else, and
a project is drawn apart from another when there is more than one to tell apart. The same relations
are `noust map` (and `noust map --json`, the document `GET /api/map` answers).

## Charts

The charts drawn from the [metrics history](MONITOR.md#metrics-history) (the Overview's machine
charts, an application's Metrics tab and a database's Overview and Metrics tabs) behave the same
way everywhere.

- **A group of charts** shares one crosshair: pointing at one marks the same moment on the others,
  but only the chart under the pointer says the moment and its readings, and the rest keep the
  newest values in their row. A click no longer freezes anything: until 3.2 it froze that chart and,
  through the group, every other one until the next click. The keyboard still steps through the
  readings, and Escape hides the card.
- **Expanded**, a chart has the range selector and the zoom. Dragging across it selects a stretch
  of time, drawn as a translucent tint with solid edges so the readings under it stay visible, and
  zooms into it: the stretch is read again at a finer step.
- **Investigate this stretch** is a button of the expanded chart. It acts on the stretch on screen
  (the one you zoomed to, or the whole window) and opens a panel, "What happened in this stretch",
  with what Noust knows about it in one place, up to 31 days:
  - **Busiest processes**: the five processes with the most CPU and the five with the most memory
    in each minute, with the service, application or container each belongs to, the account, and
    (with `secrets.reveal`) its command line. A row selects that minute and lists its processes below; when the stretch is long,
    only its busiest minutes are listed, and the panel says so. This is what answers "why this
    peak". The monitor takes these samples once a minute while it runs, so there are none from
    before it started: the panel says "No process samples before ..." with the time they begin.
  - **Events**, oldest first, filtered by source, level and service: the system journal from
    warnings up, the audit log (commands, console and central actions, sign-ins, with who), what
    the monitor saw (processes over a threshold, units that failed or recovered, server boots),
    and the deployments and jobs that ran.
  - **Deployments and jobs**, each opening its page.

  On an application's Metrics tab the stretch is narrowed to that application: its journal (every
  priority), the audit events that name it, its deployments and jobs, and its processes. On a
  database's charts and the Overview it covers the whole server. A source you may not read is
  named with the permission it needs, never left out silently: the journal needs `secrets.reveal`,
  the audit log `audit.read`. A source that failed shows the system's own words. On a central,
  each server answers for what it saw. The same data is `GET /api/timeline`, see [api.md](api.md).

## Activity

Every job and every audited action on this server in one timeline, newest first, filterable
by kind, result and actor. A job opens its log. What builds and what waits leads the page as the
[deploy queue](console-applications.md#the-deploy-queue-and-cancelling).

## Logs

One search over the logs, from one toolbar: a query box (`/` focuses it), **Where from** (the
applications, with the journal's lines and the containers' output, or the **System**: the journal of
any service of the machine), **How far back** (**Live** first; **This boot**, **Previous boot** and
**Since the last deploy** are ranges too), the syntax help and **Copy link to this search**. Credentials
are masked, and everything is in the address so a search can be sent to somebody, with how many lines
matched and how many were examined. An application's Logs tab has the same toolbar. On a central,
**Every server** searches all of them, each line naming its server. `/server/logs` opens the System
source. See [logs.md](logs.md).
