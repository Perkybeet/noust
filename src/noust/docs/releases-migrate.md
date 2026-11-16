# Moving an in-place application onto releases

Part of [Releases](releases.md).

An application deployed by 1.x keeps running and updating in place after the upgrade, exactly
as before. Moving it onto releases is an explicit operation, one application at a time:

```bash
noust --dry-run app migrate shop.example.com    # the plan, and a rehearsal that changes nothing
noust app migrate shop.example.com              # asks for confirmation; -y to skip it
noust app migrate shop.example.com --persist storage --persist public/uploads
```

`--dry-run` is a global option and goes before the command. The console offers the same
operation from the application's Settings tab; the API has `GET /api/apps/{domain}/migrate/plan`
(read only) and `POST /api/apps/{domain}/migrate` (needs sudo mode), which queues the migration
as a job and answers `202` with it.

What the migration does:

1. **The unit is stopped first**, so nothing writes into the tree while it moves. The plan
   states this downtime; it lasts until the health gate passes, usually seconds.
2. **The live tree becomes the first release.** It is moved, not copied: every change is a
   rename inside the application directory, or the creation of a directory or a link.
3. **The environment moves to `shared/`.** `.env` and Noust's inventory of it (`.noust`) are
   moved to `shared/` and linked into the release.
4. **What the application wrote for itself moves to `shared/`.** In a git checkout that is
   every untracked directory, ignored or not (`git status --ignored`), minus build output
   (`node_modules`, `.next`, `dist`, `build`, `.venv`, `__pycache__` and similar). Without
   git, Noust cannot tell uploads from code: it keeps whichever of `uploads`,
   `public/uploads`, `storage` and `data` exist, and warns you to name the rest. `--persist`
   replaces detection entirely. SQLite databases are found by their file header wherever
   they are (outside build output) and always move to `shared/`, with their `-wal`, `-shm`
   and `-journal` files beside them; a `--persist` list that leaves one out is refused.
5. **The unit and the site are rewritten to run from `current`**, when they name the
   application directory. A proxied site only names a port and is left alone.
6. **The file count is verified.** Regular files and their bytes are counted before and after,
   `shared/` included. Any difference fails the migration.
7. **The application goes through the health gate.** If it does not answer, every rename is
   reversed, the unit and site are put back byte for byte, and the application is restarted
   on the tree it had. Every undo step is attempted even when an earlier one fails; anything
   that could not be put back is listed in the error with where it is now.

Read the plan before confirming. Two warnings in it matter:

- **Untracked files outside a persistent path** stay in the first release only. The next
  deploy will not have them. Name the directory that holds them with `--persist`.
- **A tree that is not a git checkout** gets only the usual upload directories. Anything else
  the application writes must be named with `--persist`.

The migration refuses an application that is already on releases, one whose type cannot use the
release layout (Docker Compose), and one whose directory is missing. After it, the first
`noust update` builds a second release from the recorded source through `repo/`.
