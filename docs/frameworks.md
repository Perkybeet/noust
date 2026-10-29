# Deploying by framework

Noust deploys the stacks below natively: it detects them from the repository, installs and builds
them, runs them as a systemd unit (or serves their files) behind nginx or Apache with a
certificate, and keeps every deploy as a release behind a health gate. No Dockerfile and no
`Procfile` are needed. This guide says, for each, what is detected, what Noust does, and the one or
two things the project may need to say itself.

Two things are the same for all of them:

```bash
noust create -d app.example.com -s https://github.com/you/app.git     # detect, build, run, serve, certificate
```

To see what Noust would do before it does it, the console's **New application** wizard inspects the
repository first (the type, the commands it will run, the port, the variables in `.env.example`
with the secrets flagged), and `noust --dry-run create ...` rehearses the command without changing
anything.

- When the repository has an `.env.example`, Noust generates the application's `.env` from it, with
  random values for the secrets. `--env KEY=value` and `--env-file` set variables of your own.
- When Noust does not detect what you mean, `--type` chooses it, and a `build:` block in
  `noust.yaml` (or `noust app build set`) replaces any of the install, build and start commands, the
  base directory, the directory a static build leaves and the runtime: [build.md](build.md).
- A command that must run on every deploy before the new version serves (a database migration)
  is a `pre_deploy` hook in `noust.yaml`: [compose.md](compose-hooks.md#noustyaml). Hooks run as the
  application's own account with its `.env`, and a failing `pre_deploy` stops the deploy with what
  served before still serving.
- Go, Rust, Ruby, Java and Kotlin, .NET, Elixir and Deno are built on the server with its own
  toolchain: [build.md](build-runtimes.md#java-and-kotlin-net-elixir-and-deno) (Rails and Phoenix have
  [a page of their own](frameworks-ruby-elixir-static.md)). A stack Noust has no native runtime for (Swift, Zig, Crystal...) is deployed from its
  `Dockerfile`, or with Railpack, as a container application: [containers.md](containers.md). Every stack in
  this guide has a minimal example in [`examples/`](../examples/README.md) that deploys as it is.

One page per family of stacks:

- [Node.js frameworks](frameworks-node.md)
- [PHP frameworks](frameworks-php.md)
- [Python frameworks](frameworks-python.md)
- [Ruby on Rails, Phoenix, and static site generators](frameworks-ruby-elixir-static.md)
- [Stacks without a native runtime](frameworks-other.md)

## When it did not go right

Read the deployment's log in the console (**Deployments** of the application) or with `noust app
deploy-log DOMAIN`; `noust logs DOMAIN` is what the application itself printed once it ran. If the
application deployed but does not answer, `noust diagnose DOMAIN` says why, most likely cause
first: [Troubleshooting](troubleshooting.md).
