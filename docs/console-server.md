# Server

Part of [The console, page by page](console.md).

Six tabs, each with its URL (the system's journal is in [Logs](console-overview.md#logs)):

| Tab | What it has |
|---|---|
| Overview | What needs attention, then the machine at a glance; fits one screen |
| Updates | Pending updates, security first; apply all or only security; refresh; "a reboot is due" and why; services on replaced libraries; automatic updates; the history of runs |
| Security | Hardening checks with their fix or exact steps, accepted risks; SSH (effective settings, keys, safe fixes that revert unless confirmed); the firewall against the ports that really answer; fail2ban |
| Storage | Real filesystems, what takes their space, clean-ups, swap; **Automatic cleanup**, the policy this server runs by itself (schedule, how full a disk must be, which actions) with a Run now, and the history of every cleanup |
| Services | Every systemd unit Noust manages ("Show all units" lists the rest, read-only); a service's page has its live journal, controls and a unit editor checked with `systemd-analyze verify`. A unit carries tags like an application (**Edit tags...** in its menu, a Tag filter, chips under its name) |
| System | Time and synchronisation, host name, the operating system and its end of life, processes, network, reboot and shut down (now or scheduled), and the resource monitor |

`/services` redirects to Server > Services.

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

**Automatic cleanup.** The Storage tab's **Automatic cleanup** card sets what this server cleans by
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
