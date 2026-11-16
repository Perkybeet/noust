# Databases

Part of [The console, page by page](console.md).

**Databases** is one list: every database on every running engine, with whether it answers (**Running**,
**Stopped** when its engine is stopped, **Not found** when the engine no longer has it), the engine and
version it runs on and where ("PostgreSQL 16 (on the system)", "PostgreSQL 15 (container pg15)"), its size,
the application that uses it and when it was last backed up, with **Add to Noust** on the ones an engine holds
and Noust does not (they are not a kind of database: the button is the whole difference) and a button to
copy the connection an application uses. The side column has the list, **Engines**, and one view per kind of
engine. Database ports open beyond the machine are flagged on the list. On a screen narrower than 1536
pixels the list keeps the engine and its release and leaves out its size and where it runs (the whole is
where the pointer rests on the engine, and the size is on the database's Overview), so it never scrolls
sideways; a phone draws each database as a card.

**New database** is the one dialog that makes a database, from the list, from an application's Database section
and from Home: a name, whether it starts **Empty** or **From a dump** (the file goes up in chunks, as below,
and the database is made by loading it), the engine as a card each (where it runs, its port and how many
databases it holds), and the application that will use it. **Another engine or version** goes on to adding
an engine.

**Engines** are the programs that hold the databases, each a card: its engine and version, whether it runs
(**Start** is on the card of one that is stopped), its port, how many databases it holds, where it runs, where
it stands in its project's support (a version whose support is ending or ended says so and offers **Upgrade
version**) and whether it is the default of its engine (a new database is made there unless you choose
another). **Add an engine** asks how first: the system's own (a package), or a container with another version
or an engine the machine lacks. A version that no longer gets security fixes is made only after you say you
know it.

One database's sections are the side column, in three groups. **View**: **Overview**
(size, connections, cache, last backup, the engine it runs on and, for an old one, **Upgrade version**, and
two charts), **Data** (tables and rows, read-only, paged and filtered, with the columns' types; editing one
row needs sudo mode) or **Keys** (a Redis database's keys: it has no query), **Query** (the SQL console:
read-only by default, held so by the engine itself and not by this screen; history, saved statements,
export; **Show the plan** does not run the statement and **Measure it by running it** does, so it waits for
**Allow changes**; write mode needs sudo mode) and **Metrics**. **Manage**: **Access** (accounts with their
profiles, and password rotation), **Connect** (an SSH tunnel from your computer, and **Public access**,
below), **Backups** (its policy with schedule, retention and destinations; restore as a new database or over
this one after a safety copy) and **Settings** (its owner and encoding, its extensions, and **Move to another
engine**). **Danger**: **Delete**, which says what it takes with it (a last dump is the one choice) and asks
for the database's name. A database whose engine is stopped says so on every section, with **Start the
engine**: what needs the engine is not asked of it, and its backups, settings and delete stay. A section the
engine does not have is not listed, and an address that names one leads back to the overview.

A **database server** that Noust runs is another version of an engine, or one the machine lacks, as a
container from the engine's image pinned by digest, on 127.0.0.1 and held to a memory limit
(**Add an engine**, **In a container**, with the plan before the sudo-mode job; under **More options**, for PostgreSQL, the
**Image** chooses the engine's own or a variant with PostGIS or pgvector installed). The menu of its card
makes it the default of its engine, moves it to the newest image of its version, **Upgrade version** (it is not an
upgrade in place: a new instance of the newer major version is made and every database goes to it, in the
steps the plan lists before it is pressed, with a way back listed under **Moves to a newer version**; it is
reached from the engine and from each database that runs on an old one), **TLS**
(the mode, the certificate and the authority applications trust, with the applications that lose their
connection while the engine restarts shown before it is applied; renew the certificate), **Limits and
health** (memory, CPUs, swap and the health probe's interval, timeout and failures, with what Docker says
of the engine now) and removes it. A database's **Settings** has **Move to another engine**, which dumps it, makes
its accounts on the target, loads it and points each application that uses it at the target behind its
health gate, leaving the source untouched. On a PostgreSQL database's **Settings**, **Extensions** lists
the ones loaded and loads or unloads one from what the server offers (one that is not trusted is the whole
server's to load). Linking an existing database offers **Connection security** (encrypt, check the
authority, check the authority and the name); the application's Database section says how each is reached.
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
MongoDB) it leaves out. A database with a policy of its own keeps it. **Import a dump** (on a database's
Backups; a new database from a dump is made in **New database**) uploads a file from your browser in chunks of 8 MiB that
survive a dropped connection (it waits, tells you, and goes on from what the server holds; **Try
now** does not wait), up to the size the server allows (`databases.import_max_bytes`), reads what
the file is from its bytes, offers to drop and recreate the database or keep the owners the dump
names, asks you to type the database's name before it replaces anything, and follows the import
as a job. A file over the limit is imported from a terminal by its path (`noust db import`).
