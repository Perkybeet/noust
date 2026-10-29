# A build or a deploy failed

A build that fails changes nothing: the release that was serving keeps serving, and the deployment
is recorded as failed with its whole output. The command ends with:

```text
Deployment failed: ...
```

followed by what failed, in the words of the program that failed.

## Where to read it

- **The build's log:** in the console, the application's **Deployments** tab; open the deployment.
  From a terminal, `noust app deploy-log wrenfield.io`.
- **The application's own log** (what it printed once it ran): `noust logs wrenfield.io`. It is not
  the build's.
- **A failed health check** carries its own evidence, below:

```text
The application did not answer the health check.
```

followed by every probe that failed and the last lines of the application's journal. They say
whether it did not start, did not listen, or answered with an error.

## The causes, in the order you meet them

### The wrong version of Node.js, Python or PHP

The build fails on syntax or a dependency that needs a newer runtime. Pin the version in the
repository: `runtime: node@22` in the `build:` block of `noust.yaml`, or a `.nvmrc`, a
`.python-version`, or `require.php` in `composer.json` ([Runtimes](build.md#runtimes)). Or set it
for this server only: `noust app build set wrenfield.io --runtime node@22`.

### A variable the build needs is missing

The framework says which (Next.js, Vite and Nuxt read some variables at build time). Add it, for
the build only if that is all it is for, and deploy again:

```bash
noust env set wrenfield.io --build-only --stdin    # then type NAME=value, and Ctrl+D
noust update wrenfield.io
```

In the console: the application's **Environment** page.

### The build ran out of memory

```text
FATAL ERROR: Reached heap limit Allocation failed - JavaScript heap out of memory
```

or the build is simply killed. On a small server, add swap and deploy again:
`noust server swap create --size 2G`.

### A command uses `&&`, a pipe or `$VARIABLE`

A build, start or hook command is one program and its arguments. It never runs in a shell, so
`npm ci && npm run build` passes `&&` to npm as an argument. Commit a script and run it instead:
`build: ./scripts/build.sh` ([The build block](build.md#the-build-block)).

### `noust.yaml` has a key Noust does not know

```text
Unknown key 'pre_deploys' in hooks
```

The file is read strictly, because a misspelt phase would be a migration that silently never runs.
Every refusal names the field. Fix the spelling and push.

### The sandbox

Builds run as the unprivileged account `noust-build`, in a systemd sandbox that cannot read the
rest of the machine. A build that writes outside its directory, or expects root, fails there.
`noust app sandbox status wrenfield.io` says whether the sandbox is on, and
`noust app sandbox test wrenfield.io` builds what the next update would build, in the sandbox,
without activating it: the failure can be reproduced without touching what serves.

### A migration failed

A `pre_deploy` hook that fails stops the deploy before anything serves the new version, and its
output is the error ([What a hook failing means](compose-hooks.md#what-a-hook-failing-means)). Fix the
migration and deploy again.

### The framework builds for a platform this server is not

```text
SvelteKit builds for the platform its adapter names, and adapter-auto builds for none this server is.
```

Use `@sveltejs/adapter-node` to run it as a server, or `@sveltejs/adapter-static` to serve files,
set it in `svelte.config.js` and deploy again. The message ends with the command for a build you
run some other way ([SvelteKit](frameworks.md#sveltekit)).

### The start script is missing

```text
Missing script: "start"
```

npm found no `start` script in `package.json`, so the application never started. Add the script,
or tell Noust what to run: `noust app build set wrenfield.io --start 'node server.js'`.

## Start again from nothing

`noust update wrenfield.io --no-cache` empties the build cache, installs the dependencies again
instead of reusing the serving release's, and rebuilds even when the branch has nothing new.
