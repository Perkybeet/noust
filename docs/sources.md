# Sources: where the code comes from and what deploys it

How a repository reaches Noust, and which pushes become deployments. For deploying from a pipeline
see [ci.md](ci.md); for the build see [build.md](build.md); for several applications from one
repository see [workspaces.md](workspaces.md).

- [Addresses git understands](#addresses-git-understands)
- [Submodules, Git LFS and shallow clones](#submodules-git-lfs-and-shallow-clones)
- [Which pushes deploy](#which-pushes-deploy)
- [GitHub, and GitHub Enterprise](#github-and-github-enterprise)
- [GitLab](#gitlab)
- [Bitbucket](#bitbucket)
- [Bringing applications from other platforms](#bringing-applications-from-other-platforms)

## Addresses git understands

A source may be any of these, with any user and any port:

```
https://git.example.com/group/subgroup/project.git
git@git.example.com:group/project.git         # scp-like, any user: deploy@host:path
ssh://deploy@git.example.com:2222/group/project.git
github:owner/repo                             # shorthand for github.com
```

The owner of a path with subgroups is everything before the last element (`group/subgroup`), and a
non-standard SSH port is part of the host a deploy key and a pinned host key are kept for
(`[host]:2222`). A user other than `git` is kept, so `ssh://deploy@host/...` clones as `deploy`.

## Submodules, Git LFS and shallow clones

`noust app source show|set|reset DOMAIN`, `GET|PUT|DELETE /api/apps/{domain}/source-options` and
the console's Source card:

| Option | Default | |
|---|---|---|
| `submodules` | off | Initialise submodules (`--recursive`, shallow) and put their files in the release. A release is normally a `git archive`, which leaves submodules empty. |
| `lfs` | `auto` | `auto` fetches LFS files when `.gitattributes` tracks any (and refuses, saying how to install `git-lfs`, when it is missing); `on` always; `off` never. A pointer file in a release is a broken deployment. |
| `shallow` | on | Fetch the latest commit only. Turn it off when a build reads history (`git describe`). |

With submodules or LFS the release is exported from a checkout (`git ls-files --recurse-submodules`)
and not with `git archive`; without them nothing changes.

## Which pushes deploy

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

## GitHub, and GitHub Enterprise

The [GitHub App](api.md#integrations) clones private repositories with a token of its own and
delivers pushes and pull requests. It works against **GitHub Enterprise Server** and **Enterprise
Cloud with data residency** too:

```bash
noust config set notifications.allow_private_hosts '[ghe.corp.example]'   # a server in a private network
noust github host set ghe.corp.example      # or acme.ghe.com
noust github status                          # Host: ghe.corp.example
```

Then create the App in the console as usual: it is created, installed and listed on that host.
Enterprise Server's API is `https://HOST/api/v3`; Cloud with data residency's is
`https://api.SUBDOMAIN.ghe.com`. The host passes the guard every address an operator types
passes (a name that resolves inside the machine's own networks is refused unless it is listed under
`notifications.allow_private_hosts`).

- One server has **one** GitHub App and so one host. A server that has an App keeps its host unless
  `--force`; to move, `noust github remove` and create the App again.
- A repository of the Enterprise host is the App's (token, statuses, comments, pushes); a source on
  github.com is then an ordinary git source.
- The repository list returns `https://HOST/owner/repo.git` as the source to deploy, since the
  `github:` shorthand always means github.com.

## GitLab

Connect a GitLab (gitlab.com or self-hosted) to pick projects and branches in the console and to
clone private projects.

**With a token** (any host):

```bash
echo "$GITLAB_TOKEN" | noust gitlab connect work --host git.example.com --token-stdin
noust gitlab projects work --group acme --search shop
noust gitlab branches work 42
```

Create a personal, group or project access token with the scopes `read_api` and `read_repository`.
GitLab is asked who it belongs to before it is kept; it is a secret, never an argument, a log line
or an answer of the API.

**With OAuth**, so people do not handle tokens: register an application in GitLab (Admin area,
Applications; scopes `read_api` and `read_repository`; the redirect URI `noust gitlab oauth-app set`
prints, `<web.public_url>/integrations/gitlab/callback`), give its id and secret to Noust, and
anyone who can use the console signs in from Integrations, GitLab:

```bash
echo "$CLIENT_SECRET" | noust gitlab oauth-app set --host git.example.com --client-id 1a2b... --client-secret-stdin
```

The sign-in uses PKCE and a state that is used once and expires in ten minutes; the tokens it
returns are kept as secrets, refreshed before they expire and rotated.

A connection also **clones private projects** of its host that have no credential of their own: an
https credential kept for the repository (`noust app git-credential`) wins. Its token therefore
reaches every project of that host: give it a user that can read only what should be deployed.
Webhooks are as before ([api.md](api.md#deploy-webhooks)); GitLab's push events list the files a
push touched, so watch paths filter them.

## Bitbucket

Bitbucket Cloud delivers pushes and, for previews, pull requests through the deploy webhook
([api.md](api.md#deploy-webhooks)): signed with the webhook's secret (`X-Hub-Signature`), the
event in `X-Event-Key` (`repo:push`, `pullrequest:created|updated|fulfilled|rejected`). Skip
markers apply; watch paths do not (Bitbucket does not list the files of a push). A private
repository is cloned with a [deploy key or an https credential](api.md#deploy-keys). Bitbucket
Data Center's event payloads differ and are not read.

## Bringing applications from other platforms

`noust import --from vercel|railway|render|heroku PATH` reads a configuration file. Coolify and
Dokploy keep it in their database, so they are read through their API, with a read-only token:

```bash
export NOUST_IMPORT_TOKEN=...                                                       # or --token-file FILE
noust import --from coolify --url https://coolify.example.com                       # list the applications
noust import --from coolify --url https://coolify.example.com --app web             # propose one
noust import --from dokploy --url https://dokploy.example.com --token-file ./token --app web --deploy
```

The proposal carries the repository, branch, base directory, build commands, watch paths, ports,
health check, limits, scheduled tasks and variables (secrets withheld unless the platform gave them
in the clear), and lists what it could not carry. `--deploy` creates the application through the
same path as `noust app import`. An image, a Dockerfile or a Compose source is shown as the
`noust create` command that makes it. The server's address must be https (loopback excepted), is
not followed through a redirect, passes the same guard as every address an operator types, and the
token is used for the length of the command and kept nowhere. The console does the same from the
New application wizard.
