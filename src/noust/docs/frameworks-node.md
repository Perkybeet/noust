# Node.js frameworks

Part of [Deploying by framework](frameworks.md).

| Framework | Detected by | What Noust runs |
|---|---|---|
| [Next.js](#nextjs) | `next.config.{js,mjs,ts}`, or `next` in `package.json` | install, `build`, then `start` (or the standalone server) |
| [Nuxt](#nuxt) | `nuxt` in `package.json`, or a `nuxt.config` | install, `build`, then Nitro's server (`.output/server/index.mjs`), or the generated files |
| [SvelteKit](#sveltekit) | `@sveltejs/kit` in `package.json` | install, `build`, then `node build` with `adapter-node`, or the files with `adapter-static` |
| [Astro](#astro) | `astro` in `package.json`, or an `astro.config` | install, `build`, then the files, or `dist/server/entry.mjs` with `@astrojs/node` |
| [Remix and React Router](#remix-and-react-router) | `@remix-run/*` or `@react-router/*` | install, `build`, then the `start` script or `remix-serve` (the client bundle in SPA mode) |
| [Angular](#angular) | `angular.json` with `@angular/core` | install, `build`, then the client bundle, or the SSR server |
| [Vite, Vue and React](#vite-vue-and-react) | `vite.config.*`, or `vite` in `package.json` | install, `build`, served as static files (`preview` when it uses SSR) |
| [Express, Fastify, Koa and NestJS](#express-fastify-koa-and-nestjs) | `package.json` with the framework, a `main` or a `start` script | install, `build` if present, then `start:prod`, `start:production` or `start` |

The package manager (npm, pnpm, Yarn, Bun) is detected from the lockfile; `--pm` forces one. The
Node version is whatever the project pins (`.nvmrc`, `.node-version`, `engines`, or `runtime:
node@22` in the `build:` block): [build.md](build-runtimes.md#runtimes).

## Next.js

```bash
noust create -d shop.example.com -s https://github.com/you/shop.git
```

Noust runs `build` and then `start` on a port it chooses (`PORT` is in the unit). With
`output: "standalone"` in `next.config.js` it runs the standalone server, which is the smallest
thing to keep per release. Server-side secrets go in `.env` (generated from `.env.example`);
`NEXT_PUBLIC_*` variables are baked into the bundle at build time, so changing one needs an
update, not just a restart. A project that uses Prisma has its migration applied before the new
release serves, and a failed one aborts the update; declaring the migration as a hook takes it
over ([compose.md](compose-hooks.md#prisma)).

## Nuxt

```bash
noust create -d site.example.com -s https://github.com/you/site.git
```

Nuxt 3 and 4 build a Nitro server; Noust starts `node .output/server/index.mjs`. A project that
generates static files (`nuxt generate`, or `nitro.preset: 'static'`) is served as files and has no
process.

## SvelteKit

```bash
noust create -d app.example.com -s https://github.com/you/app.git
```

With `@sveltejs/adapter-node` Noust runs `node build` (with the forwarded-header variables a
SvelteKit server needs behind a proxy, without which a form submission is refused as cross-site);
with `adapter-static` it serves the output as files. `adapter-auto` and the platform adapters produce
nothing a server can run, and Noust refuses them with the way out: choose one of those two adapters
in `svelte.config.js`, or say what to run with `start:` in a `build:` block.

## Astro

Static output (the default) is served as files. With `@astrojs/node` in standalone mode Noust runs
`dist/server/entry.mjs`.

## Remix and React Router

Noust runs the project's `start` script (`remix-serve ./build/server/index.js`, or the React Router
equivalent). A project in SPA mode, whose build leaves only a client bundle, is served as files.

## Angular

A client-side Angular application is served from its build output
(`dist/<project>/browser`); one with server-side rendering (`@angular/ssr`) runs its server. Say
`publish_directory` in the `build:` block when the workspace has several projects.

## Vite, Vue and React

```bash
noust create -d spa.example.com -s https://github.com/you/spa.git
```

A Vite project (Vue, React, Svelte, Solid...) is built and its `dist` is served by the web server as
files, with `index.html` as the fallback so client-side routes work. If the project uses SSR, Noust
runs `preview` instead. A different output directory is `publish_directory:` in the `build:` block.
Variables prefixed `VITE_` are public: they are baked into the bundle at build time.

## Express, Fastify, Koa and NestJS

A Node server needs to listen on the port in the `PORT` environment variable (Noust sets it) and to
bind `127.0.0.1` or all interfaces; nginx is the public face. NestJS: its `build` produces `dist`, and
`start:prod` runs it. Put the database migration in a `pre_deploy` hook:

```yaml
# noust.yaml, at the root of the repository
hooks:
  pre_deploy:
    - run: npx prisma migrate deploy
      migrates: true
```
