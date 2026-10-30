# Server

Part of [The console, page by page](console.md).

The page of a server is its name, and ten sections in the second column of the bar (the tabs under the
header on one bar), each with its URL. The system's journal is in [Logs](console-overview.md#logs).

| Section | What it has |
|---|---|
| Status | What needs attention, then the machine at a glance; fits one screen |
| System and updates | Four views. **Updates**: Noust's own update (which Noust runs here, whether a newer one is out, **Update Noust** as a job) above the pending system updates, security first; apply all or only security; refresh; "a reboot is due" and why; services on replaced libraries; automatic updates; the history of runs. **This server**: time and synchronisation, host name, the operating system and its end of life, reboot and shut down (now or scheduled). **Processes**. **Resource monitor** |
| Security and SSH | Hardening checks with their fix or exact steps, accepted risks; SSH (effective settings, keys, safe fixes that revert unless confirmed, and **the server's SSH key**: the key Noust signs in to code hosts with, made from here when the server has none and shown whole to copy); fail2ban |
| Network and ports | Three views. **Listening ports**: every port with what holds it in the product's words (an application, a published port, a database, the web server, SSH), who can reach it, a search that answers "who uses port 5432?" and **Open a port**. **Firewall**: the firewall against the ports that really answer, with its rules. **Interfaces**. A line leads to **Domains > Published ports**, where an application is put on a port of its own (the TCP and UDP ports nginx carries for a service that is not a website): that is a way to reach it, so it stays with the names, and its page leads back here |
| Services | Every systemd unit Noust manages ("Show all units" lists the rest, read-only); a service's page has its live journal, controls and a unit editor checked with `systemd-analyze verify`. A unit carries tags like an application (**Edit tags...** in its menu, a Tag filter, chips under its name) |
| Web server | Which of nginx and Apache answers on ports 80 and 443 and what is behind each; the one new applications are put behind (it moves none of the ones already there); and **Move to Apache** (or nginx): the plan first (what happens, what stops it, what the new sites will not do, protections first), the agreement it asks for, and the job that does it, put back if a site does not answer. See [Moving to the other web server](network-webserver.md) |
| Terminal | A shell on the server, for whoever holds its permission |
| Files | Real filesystems, what takes their space, clean-ups, swap; **Kept between deployments**, the folders and files each application keeps beside its releases, with where they are and how big, each linking to that application's Storage; **Automatic cleanup**, the policy this server runs by itself (schedule, how full a disk must be, which actions) with a Run now, and the history of every cleanup |
| Central's permissions | On a fleet, for a server a central manages: what the central may do there (read only, operate and update, everything, and whether it may also change how the server is reached), as the server publishes it. Only the server can change it: the page builds the `noust fleet access` command for the level chosen, and **Check again** reads the server's answer |

The 4.0 addresses go to where each thing is now: `/server/updates` to System and updates, `/server/storage` to Files,
`/server/security?view=firewall` to Network and ports (Firewall), `/server/system?view=network` to
Network and ports (Interfaces), and `/services` to Services. `/domains/ports` stays where it was, now called Published ports. A database server on the machine is a
[Databases](console-databases.md) page filtered by server, not a section here.

**Searching the logs.** The search box of Logs takes a query, judged by the server against what
the journal returns (`GET /api/server/logs/search`, `noust server logs -Q`): nothing typed is part of a
command line, and no regular expression is run.

| Write | It finds |
|---|---|
| `error timeout` | entries whose message has both words, ignoring case |
| `"connection refused"` | that exact phrase |
| `@unit:nginx` | entries of that service (`.service` optional; `*` is a wildcard, as in `@unit:noust-*`) |
| `@app:shop.example.com` | entries of the services that application runs as |
| `@level:err` | that level or worse (`err`, `warning`, `info`, or 0 to 7) |
| `@pid:4821` | entries of that process |
| `-debug`, `NOT debug` | leave out what says that |
| `a OR b`, `(a OR b) c` | either; grouping (`AND`, `OR` and `NOT` in capitals) |

On the System source, the Service and Level pickers are shortcuts for `@unit:` and `@level:`, and the page says the query as the
server understood it ("Understood as `@unit:nginx AND @level:err AND NOT timeout`") with how many entries
it looked through. A search examines the newest 5,000 entries of the range (20,000 on request) and says
when older ones were not examined, so a narrow range or `@unit:` finds what a wide one cannot. A query
the server cannot read is answered in its own words, with where. The range can also be a stretch carried
from elsewhere: **What happened here** opens the timeline of the stretch being searched, and the
timeline's **Search these logs** brings its stretch (and its application, as `@app:`) back to Logs. **Click a line, or select it with the arrow keys and press Enter, to see it in context**: the entries
the journal logged just before and after it, from every service or only its own, with the one chosen marked
(`noust server logs --around CURSOR`, with the cursors `--json` prints).

**Time zone.** On the System tab, **Change time zone** opens a dialog with a searchable list of the
zones the managed server itself knows (`GET /api/server/clock/timezones`; on a central, the
selected server computes them, not your browser). Type a name, a city, an abbreviation or an
offset (`madrid`, `cest`, `+2`, `utc+1`) and the list narrows; zones are grouped by region, each
option shows the name (`Europe/Madrid`), the city and its offset and abbreviation now
(`UTC+02:00 · CEST`), and the zone in use is marked **In use**. Under the list, the time the
server's clock will read with the zone you chose. Cron jobs and scheduled backups run at local
times, so their timers move with the zone, and the toast says how many moved. A server whose Noust
is older than 3.2 does not list its zones: the dialog says so and the name is typed, as before.

**Processes.** Click a column header (Process, PID, CPU, Memory) to sort; the order is applied by
the server, each column in its natural direction (CPU and Memory busiest first, PID and Process in order),
and the CPU and Memory headers sort the view grouped by service. The button under the table says how many there are ("Show the first 50 of 213") and keeps
the rows already listed while the longer list loads. The console never signals a process.

**Automatic cleanup.** The Files section's **Automatic cleanup** card sets what this server cleans by
itself: a schedule (a systemd timer, `noust-storage-clean.timer`), a threshold (it runs only when the
fullest writable disk is at least that full, 80% by default; 0 runs it every time) and the actions, from
the same closed list as a manual cleanup: the journal, the package cache, the old deploys kept for
rollback and, only if the policy says it accepts that once, the Docker build cache older than a week and the
images with no name and no container. Removing one named Docker image and Docker volumes (which hold
databases) are never scheduled. **Run now** runs the policy at once, as a job, whatever the disks hold.
Every cleanup, by hand or by the timer, leaves one row in the history beside it: when, what started it, the
fullest disk before and after, what each action freed or removed, and the tool's own words for one that
failed, so a cleanup that took something you missed can be reconstructed. From a terminal:
`noust server cleanup-schedule show|set|run|history` (`set --enable --schedule daily --threshold 80
--action journal --action pkg-cache`).
