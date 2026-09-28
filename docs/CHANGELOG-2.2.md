# WASM 2.2 changelog

Changes since 2.1.0. Upgrade notes are in [UPGRADING-2.0.md](UPGRADING-2.0.md#22).

## Deploys without a gap

- **Blue/green activation**, opt-in per application (`wasm app zero-downtime DOMAIN on`,
  Settings → Releases, `PUT /api/apps/{domain}/zero-downtime`). The application runs as two
  instances of one systemd template unit, each on its own port and release, behind an nginx
  upstream. An activation starts the idle instance on the new release, passes the health gate
  on its port, moves the upstream with `nginx -t` and a reload, and stops the old instance
  after a drain (10 s by default). A failed gate stops the new instance; the old one never
  stopped. Deploys, updates, release rollbacks and limits applied with a restart all switch
  this way. Measured in the integration harness: a request every 50 ms through an update, a
  rollback, a release that crashes at start and switching the mode off, and none failed.
- Only for applications on releases, with a process, behind nginx: in-place, static,
  Docker Compose, monorepo and Apache applications are refused with the reason. The
  application must tolerate two copies running for a few seconds; the console says so before
  turning it on.

## Pull request previews

- `wasm preview enable DOMAIN --domain previews.example.com`, or the application's
  Settings: every pull request gets `pr-<n>-<app>.previews.example.com`, deployed on
  releases with 256 MB and half a CPU, rebuilt on each push, and removed when the request
  closes or after its time without a push (7 days by default), up to a quota per application.
- Events come from the application's own webhook (GitHub, GitLab, Gitea) or from the GitHub
  App. Pull requests from forks are refused. On GitHub, a comment on the pull request carries
  the link and the state.
- A preview gets the application's environment variables, production secrets included, and
  uses its databases: the console says so where previews are turned on.
- Deleting an application removes its previews.

## GitHub

- **A GitHub App per server**, created from Settings → Integrations with GitHub's manifest
  flow: it lives in your account, its private key stays on the server (0600), and you choose
  the repositories when you install it.
- Private repositories clone with one-hour installation tokens passed through git's
  environment only: never in a URL, `.git/config`, a command line or a log. `github:owner/repo`
  works as a source everywhere.
- The new-application wizard lists your repositories and branches.
- Pushes and pull requests arrive at `/hooks/github`, verified with the App's secret.
- Each deployment shows on GitHub as in progress, success or failure.
- `wasm web expose-hooks DOMAIN` publishes only `/hooks/` of the console on a domain of its
  own, so code hosts can reach a console that listens on loopback.

## Backups off the server

- **Remote destinations through rclone**: SFTP, SMB, WebDAV, S3 and compatibles (AWS,
  Cloudflare R2, Backblaze, Wasabi, MinIO, Hetzner, Scaleway), B2, Google Drive, OneDrive,
  Dropbox, pCloud, and mounted paths. `wasm backup destination add|test|list|update|remove`,
  or the Backups page. Credentials are secret files and reach rclone through its environment
  only. Optional encryption per destination, with the key shown once to keep.
- Each scheduled backup is copied to the schedule's destinations, verified (size, and hash
  where the backend has one) and pruned per destination. A failure sends `backup_failed` with
  rclone's own words and keeps the local backup.
- Copy any backup to a destination (`wasm backup push`), list a destination's backups and
  restore from one (`wasm backup restore --from NAME ID`).
- **Schedules keep their word**: they live in the store, and their retention (count and
  days) is applied. Before 2.2 the timer ignored it. Existing schedules are adopted as they are.

## Notifications

- Deployment started (off by default), succeeded, failed and **rolled back** (a new version
  failed its health check and the previous one serves again). Sent for every deployment:
  from the CLI, the console, webhooks and previews, once each.
- A failure carries the health gate's evidence; with `web.public_url` set, a link to the
  deployment's page.
- Chat messages are cut to what each channel accepts.

## Environment variables

- Why each variable is hidden, in words: its value looks like a Stripe, GitHub, AWS... key, a
  private key, a JWT, credentials in a URL or a random secret; its name suggests one; or you
  marked it. `NEXT_PUBLIC_`, `VITE_` and similar public prefixes are no longer hidden by name.
- Mark a variable secret or not secret (`wasm env mark`, the Environment tab); masking and
  log scrubbing follow the mark.

## Fixes

- A per-application webhook delivery that was not a push (a ping, a pull request) triggered
  an update when the application followed no branch. Now only a push updates.
- Every port picker skips the ports applications own, including a stopped one's and the
  second port of a blue/green application.
- A destination named with a dash, an absolute destination folder and testing a destination
  nobody pushed to yet all work (found by the integration harness before release).
