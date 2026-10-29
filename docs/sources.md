# Sources: where the code comes from and what deploys it

How a repository reaches Noust, and which pushes become deployments. For deploying from a pipeline
see [ci.md](ci.md); for the build see [build.md](build.md); for several applications from one
repository see [workspaces.md](workspaces.md).

- [Addresses git understands](#addresses-git-understands)
- [Submodules, Git LFS and shallow clones](#submodules-git-lfs-and-shallow-clones)
- [Which pushes deploy](sources-pushes.md#which-pushes-deploy)
- [GitHub, and GitHub Enterprise](#github-and-github-enterprise)
- [GitLab](#gitlab)
- [Bitbucket](#bitbucket)
- [Deploy with git push](sources-push-or-archive.md#deploy-with-git-push)
- [Deploy from an archive](sources-push-or-archive.md#deploy-from-an-archive)
- [Changing where the code comes from](#changing-where-the-code-comes-from)
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

## GitHub, and GitHub Enterprise

The [GitHub App](api.md#configuration-integrations-and-recipes) clones private repositories with a token of its own and
delivers pushes and pull requests. It works against **GitHub Enterprise Server** and **Enterprise
Cloud with data residency** too:

```bash
noust config set notifications.allow_private_hosts '[ghe.corp.example]'   # a server in a private network
noust github host set ghe.corp.example      # or acme.ghe.com
noust github status                          # Host: ghe.corp.example
```

In the console, Settings, Integrations asks for the GitHub address before the App is created, and
shows the host it lives on afterwards. Then create the App as usual: it is created, installed and
listed on that host.
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

In the console the sign-in is **Connect GitLab**, **Sign in with GitLab**: it registers the
application the first time, shows the redirect address to give GitLab, and the callback page
finishes it. The New application wizard then offers **From GitLab**, with a connection, a group, a
search and the branches of the project chosen.

The sign-in uses PKCE and a state that is used once and expires in ten minutes; the tokens it
returns are kept as secrets, refreshed before they expire and rotated.

A connection also **clones private projects** of its host that have no credential of their own: an
https credential kept for the repository (`noust app git-credential`) wins. Its token therefore
reaches every project of that host: give it a user that can read only what should be deployed.
Webhooks are as before ([api.md](api.md#deploy-webhooks)); GitLab's push events list the files a
push touched, so watch paths filter them.

## Gitea, Forgejo and Gogs

They deliver pushes, tags and (Gitea and Forgejo) pull requests through the deploy webhook
([api.md](api.md#deploy-webhooks)), signed with the application's secret: `X-Gitea-Signature`,
`X-Forgejo-Signature` or `X-Gogs-Signature`, the HMAC-SHA256 of the body in hex. Forgejo is a fork of
Gitea and Gogs the project Gitea was forked from: Noust reads their payloads with one parser, and the
delivery log names which of the three signed. Add the webhook at the repository's Settings > Webhooks
(`noust app webhook show DOMAIN` prints the page: `/settings/hooks/gitea/new`, `/forgejo/new` or
`/gogs/new`), content type `application/json`, events Push (and Pull request for previews).

A **Gogs** pull request names its branches in flat fields (`head_branch`, `head_repo`) and gives no
commit: a preview is built from the branch, as a push to it would be. Gogs has no commit statuses.

A host that does not say what it is (`git.example.com`) is named once, so Noust knows how to link to its
settings and where its API is:

```bash
noust config set integrations.forge_hosts git.example.com=gitea,code.example.com=forgejo
```

Hosts named `gitlab...`, `gitea...`, `forgejo...`, `gogs...` or `bitbucket...`, and `codeberg.org` and
`gitea.com`, are known without it.

## Commit statuses and comments on GitLab, Gitea, Forgejo and Bitbucket

A deployment marks the commit it deploys, the way the [GitHub App](#github-and-github-enterprise) does:
**running** when it starts, then **success**, **failure** (with "Rolled back" when what served before was
put back) or **cancelled**, as the check `noust/deploy` (`noust/preview` for a preview), with a link to
the application (or, when it failed, to its page in the console if `web.public_url` is set). A
preview's pull request (merge request on GitLab) gets **one comment** that is edited in place as the
preview changes, and made again if somebody deleted it.

| Host | Status | Comment | What signs in |
|---|---|---|---|
| GitLab | `POST /projects/:id/statuses/:sha` | merge request notes | the repository's token (scope **`api`**), else the GitLab connection of its host |
| Gitea, Forgejo | `POST /repos/{o}/{r}/statuses/{sha}` | issue comments | the repository's token (write access) |
| Gogs | none (the host has no such API) | issue comments | the repository's token |
| Bitbucket Cloud | `commit/{sha}/statuses/build` | pull request comments | a repository access token (user `x-token-auth`, scope `repository:write`) or an API token |

What signs in is what already clones the repository: its [https credential](api.md#deploy-keys)
(`noust app git-credential DOMAIN`), or a GitLab connection for its host. **A token that can clone may
not be allowed to write**: a GitLab connection has `read_api read_repository`, and a status needs `api`, so
give the repository a credential of its own with the scope the host asks for. The refusal is in the log
with that hint; nothing else about the deployment changes. A repository with no https credential (a
source reached with a deploy key) gets no status and no comment, because there is nothing to write them
with. Writing happens on a worker of its own, in order, and never delays or fails a deployment.

## Bitbucket

Bitbucket Cloud delivers pushes and, for previews, pull requests through the deploy webhook
([api.md](api.md#deploy-webhooks)): signed with the webhook's secret (`X-Hub-Signature`), the
event in `X-Event-Key` (`repo:push`, `pullrequest:created|updated|fulfilled|rejected`). Skip
markers apply; watch paths do not (Bitbucket does not list the files of a push). A private
repository is cloned with a [deploy key or an https credential](api.md#deploy-keys). Bitbucket
Data Center's event payloads differ and are not read.

## The allowed origins

`security.allowed_sources` names the places code may be deployed from (ENS, and anyone who wants it).
Empty allows everything, as every earlier version did; once it has an entry, a source outside the list
is refused before anything is fetched, whether it comes from a deploy, an update, a preview or a webhook.

An entry is one of:

| Entry | Allows |
|---|---|
| `git.example.com`, `*.example.com` | Any repository on that host, or on any subdomain of the domain |
| `github.com/acme` | Every repository of that organisation (not `acme-evil`) |
| `github.com/acme/shop` | That repository |
| `local` | Directories on this server |
| `push` | Code that arrives with `git push` to this server (`noust push enable`) |
| `upload` | An archive somebody uploaded |
| `recipes` | The code Noust prepares from a recipe of the catalogue |

`local` also covers the recipes, as it did before `recipes` existed. Each entry is checked when it is
saved: a user or a token in it, a port, the `user@host:path` form, a path on disk or a bracket kept from
a list is refused with the shapes that work, because an entry that cannot match looks like protection
that is not there. A list is written `a, b` or `[a, b]`:

```
noust config set security.allowed_sources 'github.com/acme, recipes'
```

The list is written whole each time. A refusal says which entry would allow the source and prints the
command that adds it to the list the server has, and `GET /api/recipes` says (`recipes_allowed`)
whether the catalogue may be deployed here.

## Changing where the code comes from

A repository moves to another organisation, a team leaves one code host for another, an application
that was deployed from a directory gets a repository. Recreating the application for that would lose
its history, variables, domain and database wiring, so its source is changed in place:

```bash
noust app source change shop.example.com https://git.example.org/team/shop.git --branch release
```

`POST /api/apps/{domain}/source/plan` and `PUT /api/apps/{domain}/source` do the same, and the
console's Source settings has a **Change the source** card. It is two steps and the first writes
nothing:

- **The plan** judges the new address the way a deploy does (no password or token in it, the allowed
  origins of `security.allowed_sources`, a directory that exists), asks the remote whether it can be
  read and has the branch (`git ls-remote`, nothing downloaded), and lists what the change does not
  carry with it: the webhook configured on the old repository, history the new one may lack (`noust
  update --commit` and **Redeploy** on an old deployment only reach commits the new repository has),
  the previews built from the old source, and tag following.
- **The change** repeats the plan under the application's lock, so it is refused while an update runs,
  and writes the source and the branch together. A pinned branch follows the change. It is audited
  (`apps.source`). Through the API it needs sudo mode, like pinning a branch, and a directory on the
  server cannot be named by an API token.

Nothing is fetched or built. The repository cache points its remote at the new address on the next
update, the release that serves keeps serving, and the releases built so far stay on disk and can be
rolled back to. `noust update shop.example.com` (the console's **Update now**) builds the new source.

`--no-check` skips the question to the remote, for one this server cannot reach yet; the branch must
then be named. Only an application on the release layout can change its source: an in-place one is
moved first (`noust app migrate`), a Compose stack or a container application has no source of this
kind, a preview follows its parent, and one that deploys from `git push` turns that off first.

## Bringing applications from other platforms

`noust import --from vercel|railway|render|heroku PATH` reads a configuration file. Coolify, Dokploy
and CapRover keep it in their database, so they are read through their API, and Dokku, which has no
API, through its own commands (on its server, or over SSH):

```bash
export NOUST_IMPORT_TOKEN=...                                                       # or --token-file FILE
noust import --from coolify --url https://coolify.example.com                       # list the applications
noust import --from coolify --url https://coolify.example.com --app web             # propose one
noust import --from dokploy --url https://dokploy.example.com --token-file ./token --app web --deploy
noust import --from caprover --url https://captain.apps.example.com --token-file ./password --app web
noust import --from dokku --ssh dokku@dokku.example.com --app web                   # no token: an SSH key
```

A platform that answers on this machine's own network (a private address, or `127.0.0.1` when it runs
here) is only contacted once its host is listed, so that a typed address cannot make the server read
its own services: `noust config set notifications.allow_private_hosts 10.0.0.5 --list`. The refusal
says so, with that command.

The proposal carries the repository, branch, base directory, build commands, watch paths, ports,
health check, limits, scheduled tasks and variables (secrets withheld unless the platform gave them
in the clear), and lists what it could not carry. **Storage and databases are part of it**: the
mounts and volumes of the application, and the databases it connects to (found by a variable that
names them, or in the same environment), each with the commands that copy its data, run on the
source server (a dump read inside the container, a `tar` of the volume) and loaded here with
`noust db import` and a `tar -xzf` under the application's `shared/` directory. Nothing is copied by the importer itself; the data
travels in the commands you run. `--deploy` creates the application through the same path as
`noust app import`. An image, a Dockerfile or a Compose source is shown as the `noust create`
command that makes it. The server's address must be https (loopback excepted), is not followed
through a redirect, passes the same guard as every address an operator types, and the token is used
for the length of the command and kept nowhere. The console does the same from the New application
wizard. Each platform has its own guide: [Coolify](coolify.md), [Dokploy](dokploy.md),
[CapRover](caprover.md) and [Dokku](dokku.md).

A **template** of Coolify or Dokploy is not an application but a recipe, and becomes a Compose stack
with its variables and files: `noust compose import templates/blueprints/ghost --from dokploy
--domain blog.example.com --out ./blog` ([compose.md](compose.md), [dokploy.md](dokploy.md)).
