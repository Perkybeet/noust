# WASM 2.3 changelog

Changes since 2.2.1. Upgrade notes are in [UPGRADING-2.0.md](UPGRADING-2.0.md#23).

## The console in Spanish

- The whole console speaks English and Spanish: every page, dialog, toast, validation
  message and screen-reader announcement. The language follows the browser the first time and
  is switched in Settings → General or the session menu; it is kept in the browser.
- Dates, numbers, sizes and relative times follow the language.
- What nginx, systemd, certbot, psql, git and WASM's own server print is shown as they wrote
  it, in English: a system message is never paraphrased.
- Notifications speak English or Spanish too: `wasm config set notifications.language es`, or
  Settings → Notifications. The tools' output inside them stays verbatim.
- The CLI stays in English in 2.3.

## Recipes

- `wasm recipe list`, `wasm create --recipe NAME -d DOMAIN`, or "From a recipe" in the
  new-application wizard: WordPress (PHP-FPM and MariaDB), Uptime Kuma, Umami (PostgreSQL) and
  n8n, each with its source pinned or checked against a published checksum, generated
  secrets, its database, persistent paths, a health check and the next steps.
- Ghost (MySQL 8 only, not MariaDB) and Plausible (ClickHouse and an Elixir release) are
  listed with why they are not available yet.

## PHP applications

- A `php-fpm` application type: a pool per application on its own socket, running as the
  service user, a fastcgi site that follows the active release, composer when there is a
  `composer.json`, and a health gate that asks the pool itself. Laravel-style `public/` is
  detected. nginx only.

## Moving applications

- `wasm app export DOMAIN` writes everything that defines an application as versioned JSON
  (secret values only with `--with-secrets`, never WASM's own credentials); an application's
  Settings export it too.
- `wasm app import FILE [--domain NEW]`, or "Import an application" in the wizard, deploys it
  through the normal path and applies the rest (domains, health check, retention, secret
  marks, cron jobs, backup schedule, previews, blue/green), listing what it could not.
- `wasm import --from vercel|railway|render|heroku DIR` reads the repository's configuration
  and proposes WASM's, with a warning for each thing that has no equivalent. The wizard's
  inspection does the same and pre-fills the review, health check included.

## Updates

- The "new version available" notice, in the CLI and the console, now reports the version
  this server can install: from its package repository (apt, dnf, zypper), PyPI (pip), or
  GitHub (a source checkout). When a release is out but its package is still being built,
  it says the version is on the way instead of asking for an upgrade that would do nothing.

## Also

- A new application can start with its health check (`health_path`, `health_expect`,
  `health_timeout` in `POST /api/apps`).
- The development seed, tests and documentation use example domains.
