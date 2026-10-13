# Monorepos: one application per workspace

A JavaScript monorepo is several applications that share a repository. Noust deploys each as an
application of its own, on the releases layout, so each has instant rollback, health-gated
deployments, previews and its own domain, and a push deploys only the applications it touched.

This replaces the `monorepo` type for new work. That type (a `turbo.json`, one workspace
configuration and at least two apps under `apps/`, deployed in place with one unit and one
subdomain per workspace) keeps working for what is deployed with it, and is not changed. It has no
instant rollback, no previews, and it only knows nginx; the applications below have all three.

- [Read the workspaces](#read-the-workspaces)
- [Create the applications](#create-the-applications)
- [What each application gets](#what-each-application-gets)
- [The API](#the-api)
- [Limits](#limits)

## Read the workspaces

```bash
noust workspace detect https://github.com/acme/mono.git
```

```
Workspaces: turborepo (pnpm)
  • apps/api  @acme/api  [nodejs]
      builds with: pnpm exec turbo run build --filter=@acme/api...
      watches: apps/api/**, packages/config/**, packages/db/**, packages/ui/**, package.json, pnpm-lock.yaml, turbo.json
  • apps/web  web  [nextjs]
  • packages/ui  @acme/ui  (not an application: a library: it has no start script and no server framework)
```

It reads the workspace lists of `package.json` (npm, Yarn and Bun), `pnpm-workspace.yaml`,
`lerna.json`, and names Nx and Turborepo when `nx.json` or `turbo.json` is there. Only the
configuration files and each workspace's `package.json` are fetched, and read as data: nothing of the
repository runs, a link is never followed, and a file over 256 KiB is not read.

A workspace is an **application** when a Noust type deploys it: a framework (Next.js, Nuxt, Vite,
SvelteKit, Astro, Remix, Angular), or a Node package with a `start` script or a server framework
(Express, Fastify, Koa, Hono, NestJS...). Anything else is a **library**, and is left out.

## Create the applications

```bash
noust workspace create https://github.com/acme/mono.git \
  --app web=web.example.com \
  --app apps/api=api.example.com
```

Each pair is a workspace (its name or its directory) and the domain to deploy it on. Every plan
is built and checked before anything is created (a library, a taken domain, an unknown workspace or a
domain given twice stops all of it), then the applications are created one after the other through
the path `noust app import` takes. `--dry-run` shows the plans. `--branch` picks the branch.

## What each application gets

- **The releases layout**, with the workspace as its `build.base_directory`: the build, the unit and
  the web server work from `apps/web`, with the whole tree handed to the build account so the
  workspace's links to its siblings resolve.
- **Watch paths**: the workspace's own directory, the directories of every workspace it depends on
  (directly or through another), and the files that change every workspace (`package.json`, the lock
  file, `turbo.json`, `pnpm-workspace.yaml`, `nx.json`, `lerna.json`, `.npmrc`). A push that touches only
  `apps/api` deploys the API, not the web app, and a change to `packages/ui` deploys both if both
  depend on it. They are the application's source options (`noust app source show DOMAIN`).
- **In a Turborepo**, the build runs `turbo run build --filter=<package>...`, which builds the
  workspace and what it depends on, with the package manager's own way of running turbo.
- **Otherwise** the type's own commands, run in the workspace directory.

Everything is the operator's own, and editable afterwards like any application: the build block
(`noust app build`), the watch paths, the domains, the limits.

## The API

| | |
|---|---|
| `POST /api/workspaces/inspect` | `{source, branch}`: the workspaces, with each one's type, why it is a library, what it depends on, its watch paths and build. |
| `POST /api/workspaces/deploy` | `{source, branch, apps: [{workspace, domain}]}`: queues one import job per application, in order. Sudo mode. Everything is checked first, so a mistake queues nothing. |

The client names workspaces and domains and never sends a build block: a build block is code that
runs as the application, so it is made here from what the repository says.

## Limits

- Only JavaScript workspaces: other languages' monorepos (Cargo and Go workspaces, Gradle) are deployed
  from a base directory with `--build-file`, one application at a time.
- A workspace is found down to four directories deep, and a repository lists at most 100.
- An Nx repository without `package.json` files in its projects is not read: Nx's `project.json` is
  not interpreted.
- The install runs in the workspace directory, with the type's own command. pnpm and Yarn find the
  workspace root from there; if a package manager does not (an install that fails for want of a lock
  file next to the workspace), give the application an install command in its build block.
- Each application builds the tree on its own: two applications of one monorepo do not share
  `node_modules`. The build cache (`/var/cache/noust/build/<app>`) is per application too.
