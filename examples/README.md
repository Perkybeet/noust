# Examples

A minimal application for every type Noust deploys. Each one answers `Hello from Noust (<type>)` at
`/` and has as few files as the stack allows. `python tests/integration/examples_run.py` (below)
deploys every one on a clean machine, so that what is here is what [first-application.md](../docs/first-application.md)
and [frameworks.md](../docs/frameworks.md) describe, not a sketch of it.

Deploy one from a clone of this repository (`-s` takes a repository, an archive or a directory):

```bash
git clone https://github.com/Perkybeet/noust.git
noust create -d hello.example.com -s noust/examples/nodejs
```

An application deployed from a directory has no repository to update from; to go on with one, make a
repository of the folder (`cp -r noust/examples/nodejs ~/hello && cd ~/hello && git init -q && git add .
&& git commit -qm hello`) and deploy that, or give the directory a repository later with `noust app
source change` ([sources.md](../docs/sources.md#changing-where-the-code-comes-from)).

Before DNS points at the server, add `--no-ssl` to try it on the server's own address, or use a
temporary one ([domains.md](../docs/domains.md#a-temporary-address)).

| Folder | Type | What it is | What the server needs |
|---|---|---|---|
| [`static/`](static) | `static` | An `index.html` and a style sheet, served as files | nothing |
| [`nodejs/`](nodejs) | `nodejs` | A server with no dependencies, `npm start` | Node.js |
| [`python/`](python) | `python` | A WSGI application, run with Gunicorn | `python3-venv` |
| [`php/`](php) | `php-fpm` | An `index.php`, one PHP-FPM pool of its own | PHP-FPM |
| [`ruby/`](ruby) | `ruby` | A Rack application, run with Puma | Ruby, Bundler, a compiler |
| [`go/`](go) | `go` | The standard library's HTTP server, built with `go build` | Go |
| [`rust/`](rust) | `rust` | The standard library's `TcpListener`, built with Cargo | Rust, Cargo |
| [`java/`](java) | `java` | A Maven program with the JDK's own HTTP server | a JDK, Maven |
| [`dotnet/`](dotnet) | `dotnet` | An ASP.NET Core application | the .NET SDK |
| [`elixir/`](elixir) | `elixir` | A Plug application as a Mix release | Elixir, Erlang |
| [`deno/`](deno) | `deno` | `Deno.serve`, run by its `start` task | Deno |
| [`nextjs/`](nextjs) | `nextjs` | The App Router, `next build` and `next start` | Node.js 22 (the folder's `.nvmrc`) |
| [`nuxt/`](nuxt) | `nuxt` | Nuxt 4, Nitro's server | Node.js 22 |
| [`sveltekit/`](sveltekit) | `sveltekit` | Svelte 5 with `adapter-node` | Node.js 22 |
| [`astro/`](astro) | `astro` | A static Astro site | Node.js 22 |
| [`remix/`](remix) | `remix` | React Router 7 in framework mode | Node.js 22 |
| [`angular/`](angular) | `angular` | A client-side Angular 20 application | Node.js 22 |
| [`vite/`](vite) | `vite` | A Vite page, built and served as files | Node.js 22 |
| [`monorepo/`](monorepo) | `monorepo` | A Turborepo/pnpm workspace with two applications | Node.js, pnpm |
| [`docker-compose/`](docker-compose) | `docker-compose` | A Compose project with one web service | Docker, the Compose plugin |
| [`container/`](container) | `container` | A Dockerfile, one image per release | Docker |

Noust says what it is missing before it builds and gives the command that installs it
(`noust setup doctor`, and the first lines of a deploy's log): [requirements.md](../docs/requirements.md).
The Node.js frameworks pin their dependencies exactly, with a lock file, and ask for Node.js 22 in
`.nvmrc`; Noust installs the Node it is asked for for the application when the server's is older
([build.md](../docs/build.md#runtimes)).

## What is proved

`tests/test_examples.py` reads every folder the way a deploy begins: detection must name the type in
the table above, and nothing in a folder may leave it or hold a credential. `tests/integration/examples_run.py`
does the rest on a clean machine with systemd: it creates each as an application, asks the web server
for `/` and looks for the sentence, and updates it to a second commit and goes back with
`noust releases rollback`. It needs Docker and the network, it is not part of `pytest`, and it names
every resource it makes with the label `noust.test=1`.

Rails, Phoenix and Jekyll are not folders here: their generators write the project (`rails new`,
`mix phx.new`, `jekyll new`), so the harness runs the generator in the container and deploys what it
wrote (`--example rails`, `--example phoenix`, `--example jekyll`). What `mix phx.new` writes today needs
an Elixir newer than Ubuntu 24.04's, so the harness installs one first, the way
[frameworks.md](../docs/frameworks.md#phoenix-and-elixir) says. `--example from_a_directory` deploys the
first example from a plain directory, the form shown at the top of this page.

```bash
python tests/integration/examples_run.py                    # all of them
python tests/integration/examples_run.py --example go --example rust
```

An example is a starting point, not a template to keep: each pins what it uses so it keeps building,
and a real project updates its dependencies.
