# PHP frameworks

Part of [Deploying by framework](frameworks.md).

PHP applications are detected by a `composer.json` with a front controller, or an `index.php` at the
root; Noust runs one PHP-FPM pool per application on its own socket, runs `composer install` when
there is a `composer.json`, finds `public/` (or the web root you give), and writes a fastcgi site
(nginx only). The PHP version is the one in `composer.json` (`require.php`) or `runtime: php@8.3`.

## Laravel

```bash
noust create -d shop.example.com -s https://github.com/you/shop.git --persist storage
```

- `--persist storage` keeps `storage/` (logs, uploads, the cache) across releases instead of losing
  it with each new release directory.
- `.env` is generated from `.env.example`, with random values for the secrets; read the
  application's **Environment** page after the first deploy and check that `APP_KEY` and the
  database settings are what the application needs.
- Migrations are a hook, run before the new release serves:

  ```yaml
  hooks:
    pre_deploy:
      - run: php artisan migrate --force
        migrates: true
  ```

  `migrates: true` makes Noust record that the deployment changed the schema, and ask before going
  back past it ([releases.md](releases.md)).
- A database for the app, with its connection string written into `.env`:
  `noust db provision shop.example.com -e mysql` (or `--database mysql` at `noust create`).

## Symfony

Same as Laravel: `public/` is the web root, `var/` is what to persist
(`--persist var`), and the migration is a hook (`php bin/console doctrine:migrations:migrate
--no-interaction`). Set `APP_ENV=prod` in the environment.

## WordPress and other PHP applications

WordPress, with its MariaDB, is in the catalog: `noust recipe install wordpress -d blog.example.com`
([recipes.md](recipes.md)). Any other PHP application with a front controller deploys like Laravel.
