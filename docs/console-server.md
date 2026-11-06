# Server

Part of [The console, page by page](console.md).

The Server is one area of the bar with a column of its own, in three groups: **This machine** (Status, Machine,
Processes, Resource monitor, Updates), **What runs** (Services, Web server, Software) and **Tools** (Files, Terminal).
Status is titled with the machine's name; every other section is titled with its own name and says, on the line
under it, which machine it is about. Each section puts its own actions at the right of its title. On a fleet, from
"All servers", the area is the list of servers (see [Fleet](console-fleet.md)); choosing one there is choosing it
in the selector. The system's journal is in [Logs](console-overview.md#logs). Security is an area of its own, below.

| Section | What it has |
|---|---|
| Status | Four readings (processor, memory, disk with when it fills, how long it has been up and whether it asks for a reboot), what uses the most now with whose it is (an application or a service) and a way to every process, and what is not well, each with the action that deals with it. The header says how many ports are open to the outside and leads to Network |
| Machine | **This machine**: its name and its time zone, each one press from its value (rename, change zone), what the zone is used by (scheduled tasks, backups) and the clock's synchronisation. **Hardware**: processor, memory, disk, the system and its end of support, kernel, load. **Reboot and shut down**, now or scheduled |
| Processes | What runs and what it costs, read only. Each process says the application or the service it belongs to; the search looks at the name and at that; a link leads to Listening ports for "which process has a port?" |
| Resource monitor | The processor, the memory, the disk and the network over a window (1 hour, 24 hours, 7 days), the same charts as Alerts > Metrics, and the **Resource watcher**: whether its unit runs and what it noted |
| Updates | One page for Noust and the system. **Noust**: which one runs here, what is new and **Update Noust** as a job. **Automatic updates**, with its switch beside the name. **From the system**: pending packages, security first; install all or only security (the plan is read first); a reboot that is due and why; services on replaced libraries; the history of runs |
| Services | Every systemd unit Noust manages ("Show all units" lists the rest, read-only), the ones that fail first, each with whose it is (an application, made by hand or the system's) and since when. A service's page has its live journal, controls and a unit editor checked with `systemd-analyze verify`; **a new service written by hand** is checked the same way before it is created, by the server (a refusal says systemd's own words and writes nothing). A unit Noust did not create has no Logs section: the server does not follow its journal |
| Web server | Which of nginx and Apache answers on ports 80 and 443 and what is behind each; the one new applications are put behind (it moves none of the ones already there); and **Move to Apache** (or nginx): the plan first, the agreement it asks for, and the job that does it, put back if a site does not answer. See [Moving to the other web server](network-webserver.md) |
| Software | What the server is made of as far as Noust is concerned: what each piece is used for, whether it is there, whether Noust installed it (and who, on hover), what depends on it, and whether it can be removed. The seven pieces of the machine itself, which Noust never removes, are one line until you open it |
| Terminal | A shell on the server, for whoever holds its permission |
| Files | The disks (with the fullest said in the section's line), what takes their space with what can be given back, **Kept between deployments**, swap, **Automatic cleanup** with its history. Each slab says one line, and the long rule is behind its "?" |
| Central's permissions | On a fleet, for a server a central manages: what the central may do there (the level, and whether it may also change how the server is reached), as the server publishes it. Only the server can change it: the page builds the `noust fleet access` command for the level chosen, and **Check again** reads the server's answer |

## Security

Security is an area of its own, with a column: **Checks**, **SSH**, **Blocks**, **Web firewall (WAF)** and **Access and
sessions**. From "All servers" it is the list of servers, each with how many of its checks need a look, how many
addresses it blocks now and whether a password opens a session there.

| Section | What it has |
|---|---|
| Checks | What is not right comes first, each as a line (its state, its name, a "?" with why it was found and what the system said, and the one action that deals with it: the fix, or the way to the page where it is put right); accepted risks with who accepted them and until when; what is well, each with the page that looks after it; a link to Compliance for a framework such as ENS or NIS2 |
| SSH | **Keys that can sign in** (who they enter as, when each was last seen, **Add a key**, **Remove**), and **How people sign in**: two switches (a password, root) that ask for the host's name to be typed before they take a way in away, with sshd's effective settings and the other recommended changes. The server's own SSH key for code hosts is here as well. Every change reverts unless confirmed from a new SSH login |
| Blocks | fail2ban: what is blocked **now** first, with a search by address and **Unblock** on each row; what each jail watches and how much it has done; watching nginx as well. When fail2ban is not installed the page says so with the same notice every missing piece uses, and installs it |
| Web firewall (WAF) | Whether the server can run ModSecurity and, for each site, the level it was given (block, log only, off) and what the rules refused today, with the way to the site's protection settings, where the level is chosen |
| Access and sessions | Who has entered, by SSH (from the server's journal, with who is there now) or through the console's terminal, with the terminal's recordings (they take `audit.read`) opened in a panel |

The 4.0 addresses go to where each thing is now: `/server/updates` to Updates, `/server/storage` to Files,
`/server/security?view=firewall` to Network (Firewall), `/server/security?view=bans` to Security > Blocks,
`/server/system?view=network` to Network (Interfaces), and `/services` to Services. `/domains/ports` stays where it was, now called Published ports. A database server on the machine is a
[Databases](console-databases.md) page filtered by server, not a section here. What the 4.1 console called "This
server" is now **Machine**, "System updates" is **Updates** and "Brute force" is **Blocks**; the palette still finds
them by their old names.

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

**Time zone.** In Machine, **Change time zone** opens a dialog with a searchable list of the
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
