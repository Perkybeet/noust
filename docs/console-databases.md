# Databases

Part of [The console, page by page](console.md).

Two parts of one page: **Your databases**, every database on every running server with the engine and
version it runs on, its application, its size and when it was last backed up, with **Add to Noust** on the ones
an engine holds and Noust does not (they are not a kind of database: the button is the whole difference) and a
button to copy the connection an application uses; and **Database servers**, each server as a slab with its
engine and version, where it stands in its project's support (a server whose support is ending or ended is
tinted, says so in words and offers **Upgrade version**), whether it is the default of its engine (a new
database is made there unless you choose another), where it runs and how many databases it holds. The side
column has the two sections and one view per engine. **Another version** makes a new server, in a container, of
the engine and version you choose; a version that no longer gets security fixes is made only after you say you
know it. Database ports open beyond the machine are flagged here.

One database's page has tabs: **Overview** (size, owner, application, backups, health),
**Data** (tables and rows, read-only, paged and filtered, with the columns' types; Redis keys;
editing one row needs sudo mode), **Query** (the SQL console: read-only by default, enforced
by the database server; history, saved statements, export, `EXPLAIN`; write mode needs sudo
mode), **Backups** (its policy with schedule, retention and destinations; restore as a new
database or over this one after a safety copy), **Users** (with owner, read-write and read-only
profiles, and password rotation), **Connect** (an SSH tunnel from your computer, and **Public
access**, below) and **Metrics**.

A **database server** that Noust runs is another version of an engine, or one the machine lacks, as a
container from the engine's image pinned by digest, on 127.0.0.1 and held to a memory limit
(**Another version**, with the plan before the sudo-mode job; under **More options**, for PostgreSQL, the
**Image** chooses the engine's own or a variant with PostGIS or pgvector installed). The menu of its slab
makes it the default of its engine, moves it to the newest image of its version, **Upgrade version** (every
database goes to a new server of the newer
major version, with the plan first and a way back listed under **Moves to a newer version**), **TLS**
(the mode, the certificate and the authority applications trust, with the applications that lose their
connection while the engine restarts shown before it is applied; renew the certificate), **Limits and
health** (memory, CPUs, swap and the health probe's interval, timeout and failures, with what Docker says
of the engine now) and removes it. A database's menu has **Move to another engine**, which dumps it, makes
its accounts on the target, loads it and points each application that uses it at the target behind its
health gate, leaving the source untouched. On a PostgreSQL database's **Overview**, **Extensions** lists
the ones loaded and loads or unloads one from what the server offers (one that is not trusted is the whole
server's to load). Linking an existing database offers **Connection security** (encrypt, check the
authority, check the authority and the name); the application's Database tab says how each is reached.
See [databases.md](databases.md).

A database is **private** until you open it: **Public access** on **Connect** makes it reachable
from your own computer on a port you choose, from the addresses you list, with TLS required where
the engine can (a PostgreSQL account is refused when its client does not use TLS), through a
proxy socket so the engine itself stays on loopback. The first step names the port, who may reach
it and what the account can do (owner, read and write, read only); the second says what Noust will
open, the firewall rule, and every warning the server has, in its own words, and asks you to tick
that you accept them before **Make it public** (sudo mode). The account's password is shown once
(**Show the connection** shows it again after confirming it is you), and **Make it private**
removes exactly what opening made. A new database for an application (**Application > Database**)
asks which engine and which version: the one that runs, or, for an engine that is not installed,
the versions Noust can install first, which it does only when you tick it.

**Backups** has two scopes: a policy for one database, and a policy for **every database of an
engine**, the ones created later included, with the databases, schemas and tables (collections in
MongoDB) it leaves out. A database with a policy of its own keeps it. **Import a dump** (on the
list, and on a database's Backups tab) uploads a file from your browser in chunks of 8 MiB that
survive a dropped connection (it waits, tells you, and goes on from what the server holds; **Try
now** does not wait), up to the size the server allows (`databases.import_max_bytes`), reads what
the file is from its bytes, offers to drop and recreate the database or keep the owners the dump
names, asks you to type the database's name before it replaces anything, and follows the import
as a job. A file over the limit is imported from a terminal by its path (`noust db import`).
