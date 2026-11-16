# Overview, charts, activity and logs

Part of [The console, page by page](console.md).

## Overview

Home, the Overview, is a panel of widgets: each one answers a question about the server and links to the page that has
the whole of it. **This server** follows the server chosen in the selector and comes with what needs
attention (worst first, a line each, with the system's own words and the way to fix it), the six key
figures (applications, deploys today, certificates, backups in the last 24 hours, disk and system
updates), the machine's meters, the work in progress (jobs and the long operations that are not jobs),
the certificates and backups, the applications, shortcuts to what you do every day, the latest
deployments and what happened lately. **Fleet** is every server at once, from the central's own
answers: what needs attention anywhere (each with its server), a row for each server with its
applications, the updates, the work, the certificates, the backups, the activity and the alerts.

**Customize** turns the second column into the gallery of widgets and puts a handle, a size and a
cross on each widget of the panel. A widget is dragged to move it: where it will land is drawn as an
outline of its real size, with the name of that size ("Medium, 2 x 1": quarters of the panel wide and
bands of two rows high), and the page scrolls under it for as long as you hold it near the top or the
bottom of the window. Its right edge, its bottom edge and its corner change its size: while you hold one
an outline shows the size the widget will have when you let go, and **Escape** puts it back. The
gallery's cards are dragged to the place you want them, or added at the end with their **+**. Moving,
resizing and removing a widget each offer **Undo** in the message that says it was done (the last change
only). Nothing needs the pointer: Space on the handle lifts a widget, the arrow keys move it, Shift with
the arrow keys makes it wider, narrower, taller or shorter, and Space drops it (Escape puts it back);
the button with its size has the sizes, drawn as their proportion, and **Move earlier** and **Move
later**. On a touch screen you press and hold a widget to lift it. **Reset** puts the panel back as it
came; **Done** leaves the gallery. On a phone the gallery is a sheet from **Add a widget**.

You can make panels of your own (**New panel**: a name, whether it follows the chosen server or the
whole fleet, and whether it starts as a copy or empty), rename them and delete them. An account's
panels are kept by the server and are the same on every server of the fleet; the access token is no
account, so its panels are kept in that browser. What a role may not see is not offered to it: a team
member's gallery has the applications' widgets and none of the machine's. The charts of the machine's
history are in Alerts, under Metrics. A server with nothing deployed shows one card instead of the
panel, **Deploy your first app**: **Try a demo** (a tiny page on a temporary address), **Use a
repository**, four popular recipes and **All recipes**, and under it **Then make it safe to leave
alone** (backups, a copy off the server, notifications and, while the account is not protected,
**Protect your account**). The map of the server is no longer a page (`/map` opens Home); `noust map`
and `GET /api/map` stay.

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
