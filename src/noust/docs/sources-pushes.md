# Which pushes deploy

Part of [Sources: where the code comes from and what deploys it](sources.md).

Two filters, applied where every delivery lands (`/hooks/deploy/{domain}` and `/hooks/github`), so
the forge cannot matter:

- **Skip markers** (`skip_markers`, on by default). A push whose commits *all* say `[skip ci]` or
  `[skip cd]` (in any case) is not deployed; one marked commit among unmarked ones deploys, and a
  push whose commit list is cut short by the forge deploys. A pull request is skipped when its title or its head commit
  is marked, so no preview is built for it. The delivery answers `200
  {"status": "ignored", "reason": "skip"}`.
- **Watch paths** (`watch_paths`, empty by default). Globs a push must touch: `apps/web/**`,
  `!**/*.md`. `*` stays inside a directory, `**` crosses them, a plain directory covers what is
  beneath it, the last pattern that matches a file decides, and a list of only exclusions means
  "everything but". A push that touches none answers `200 {"status": "ignored", "reason":
  "paths"}`. GitHub, Gitea and GitLab list the files of each commit; a push whose list is cut
  short, and any push from a forge that lists none (Bitbucket), cannot be filtered: it deploys. A pull request is not filtered by path either, because its payload lists no
  files.

Both are recorded in the webhook delivery log (`ignored_skip`, `ignored_paths`), and a manual
deploy (`noust update`, `POST /api/apps/{domain}/deploy`) ignores them. In a monorepo, `noust
workspace create` sets the watch paths of each application.
