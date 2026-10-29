# Keyboard

Part of [The console, page by page](console.md).

| Keys | Does |
|---|---|
| `Ctrl K` / `Cmd K` | Open the command palette, from anywhere |
| `g` `o` | Go to Overview |
| `g` `p` | Go to Projects |
| `g` `a` | Go to Applications |
| `g` `b` | Go to Databases |
| `g` `n` | Go to Domains |
| `g` `k` | Go to Backups |
| `g` `c` | Go to Cron |
| `g` `l` | Go to Logs |
| `g` `y` | Go to Activity |
| `g` `v` | Go to the server (on **All servers**, to the fleet's Servers) |
| `g` `f` | Go to **All servers** (a central, from a server) |
| `g` `u` | Go to the fleet's Updates (on **All servers**) |
| `g` `s` | Go to Settings |
| `g` `d` | Go to the application's Deployments tab; outside an application, to Activity |
| `/` | Focus this page's search, or open the palette when it has none |
| `?` | Show the keyboard shortcuts |

Two-key sequences are typed one after the other within about a second. They do nothing while
you type in a field or while a dialog is open. Inside a log viewer, `Ctrl F` / `Cmd F`
focuses its search; `Enter` and `Shift Enter` step through matches.

The **command palette** searches anything the console lists, by any name it answers to, and says
where each page lives: pages and each Settings section (an old name still finds it); applications (domain, name and type, with their state); databases;
sites (by every name they answer on); certificates (by every name they cover, with the time
left); services; the other servers; the newest deployments (by application, commit or
message); and actions, the ones that create something first: new application, new application
from a recipe, import an application, adopt a stack, new database, add a server, then change
theme, keyboard shortcuts and sign out. `>` at the start lists only actions, and `@` changes the
scope (a server, **All servers** or **This central**). The last three places it opened come first. It
reads its lists the first time it is opened, only
the ones your account may read, and reads them again after a job ends. Arrow keys or
`Ctrl N` / `Ctrl P` move, `Enter` runs, `Esc` closes.

The palette also **does** things to an application, not only opens it: type the verb
(`restart shop`, `stop shop`, `start`, `roll back shop`, `update shop`) and the result is
"Restart shop.example.com" under Operations. A verb is the application's own button, run by the
same code: stopping asks the same one question, going back opens the same choice of version, an
update with nothing new asks the same "rebuild anyway", and "Confirm it's you" appears when the
server asks for it, exactly as for the button. A verb the account may not do in the
application's team is not offered, a stack running outside its unit has no Start or Stop (the
banner's "Hand it back to Noust" is the way), and typing only an application's name finds the
application, never every verb. On an application's own page its verbs are listed before anything
is typed. Deleting is not in the palette: it needs the typed name, on the application's page.

Every list of more than 20 rows is paged: 20, 50, 100 or all rows to a page, chosen under the
list, remembered for that list in the browser, with "Showing 21–40 of 57". An application's
page keeps its daily verbs in its header: Restart, Stop or Start (by its state) and Roll back
beside Update, and on a phone as icons that keep their names; Diagnose and Delete are in
More actions.
