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
- [Deploy with git push](#deploy-with-git-push)
- [Deploy from an archive](#deploy-from-an-archive)
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

## Bitbucket

Bitbucket Cloud delivers pushes and, for previews, pull requests through the deploy webhook
([api.md](api.md#deploy-webhooks)): signed with the webhook's secret (`X-Hub-Signature`), the
event in `X-Event-Key` (`repo:push`, `pullrequest:created|updated|fulfilled|rejected`). Skip
markers apply; watch paths do not (Bitbucket does not list the files of a push). A private
repository is cloned with a [deploy key or an https credential](api.md#deploy-keys). Bitbucket
Data Center's event payloads differ and are not read.

## Deploy with git push

For code that lives on a laptop or on a git server you run yourself, with no forge and no webhook:
a repository on this server that `git push` deploys, the way Dokku and Piku do. `noust push` is the
command line, `/api/push` and `/api/apps/{domain}/push` the API, and **Deploy on push**, in an
application's settings, the console.

```bash
sudo noust push setup                                   # once per server
noust push enable shop.example.com --key-file ~/.ssh/id_ed25519.pub --label laptop
# on your computer
git remote add noust noust-git@shop.example.com:shop.example.com.git
git push noust main
```

- **Setup** creates the account pushes log in as, `noust-git` (a system account, `git-shell` for
  its shell, no password), the directory that holds the repositories (`/var/lib/noust-git`), and
  a `Match User` block in a fragment of sshd's configuration that allows public keys only and takes
  away the terminal, every kind of forwarding, the agent and X11. It reuses what the fleet's tunnel
  account does: `sshd -t` before the reload, `sshd -T` after it, and the change undone when sshd does
  not apply it.
- **A key can run `git-receive-pack` on its application's repository and nothing else.** The keys
  are lines of `/etc/ssh/noust/noust-git.keys`, owned by root and outside the account's reach, each
  with `restrict` and a forced command: whatever the client asks for, that is what runs. RSA keys
  must have 2048 bits or more. sshd reads the first line a key has, so **a key serves one
  application**: make one for each (`ssh-keygen -t ed25519 -f ~/.ssh/noust-shop`) and choose it in
  `~/.ssh/config` (`noust push show DOMAIN` prints the lines).
- **The hook tells the console, the way a code host does.** `post-receive` is written by Noust, owned
  by root and not writable by the push account. It sends a GitHub-shaped `push` event, signed with
  the application's webhook secret, to the console on its loopback address (`/hooks/deploy/DOMAIN`).
  So a push is filtered (only the deployed branch; `[skip ci]`; watch paths), recorded in the delivery
  log, queued behind the deploy queue and built and activated behind the health gate like any
  update. The pusher sees what happened (`queued the update of ... (job abc)`), and a push is never
  refused because the console was down: the code is stored and `noust update DOMAIN` deploys it.
  Rotating the webhook secret rewrites the hook; `noust push refresh DOMAIN` does it by hand.
- **The application deploys from the repository** (`push:DOMAIN` is its source), on the branch
  chosen (`--branch`, else the pinned one, else `main`). A push to another branch is stored and says
  how to deploy it. The first push replaces what runs now; the previous release stays one command
  away (`noust releases rollback`). `noust push disable DOMAIN` puts the source and the branch
  back, takes the keys away and keeps the repository (`--delete-repository` removes it).
- **Only an application on the release layout** whose type Noust builds can be turned on; a Compose
  stack and a container application are built from their own files. The repository is the
  application's own: its cache is filled from that one repository and from no other, whoever typed
  the source into another application. Reading it is done as the push account; the one fill of the
  cache is root's, with objects checked as they arrive, no hooks and no submodule transport.
- **The allowed origins** (`security.allowed_sources`, ENS) name it with `push`: once a list is
  set, code that arrives by git push is refused until `push` is in it.
- `noust app export` writes no source for such an application (the repository exists only here),
  and deleting the application removes its repository and its keys.

## Deploy from an archive

For code that is in no repository: a `.zip`, `.tar.gz`, `.tgz`, `.tar.bz2` or `.tar.xz` of the
project. The console's **New application** (From a path or address) and the **Change the source**
card take a file from the browser, and `noust upload add` takes one that is already on the server:

```bash
noust upload add ./shop.tar.gz            # prints upload:<id>
noust create --domain shop.example.com --source upload:<id>
noust app source change shop.example.com upload:<id>   # a new version of the code
```

- **The archive is sent in chunks**, with the protocol of the database dumps (`/api/uploads`): the
  server holds the bytes it has and says where to resume, so a dropped connection goes on from
  there, a chunk at any other offset is refused with the offset, and the size is capped by
  `uploads.source_max_bytes` (512 MiB unless raised). Only the one who started an upload can
  continue it, and one nobody finished is swept after a day.
- **It is extracted once, with the checks every archive gets** (the same as an archive URL): no
  absolute path and no `..`, nothing written through a link, links that point out of the project,
  device files refused, setuid and setgid bits dropped, and caps on members (50,000) and bytes
  (2 GiB). A directory wrapping everything is taken off. An archive with no files, or an unsafe
  one, leaves nothing behind. The archive itself is thrown away; the project is what is kept.
- **The result is a directory, and `upload:<id>` is its address.** It is a local source for
  everything that reads one (inspection, building, redeploying), but not a path anybody typed: the
  identifier is 128 random bits, only what Noust extracted exists at that name, and an API token
  may use it where it may not name a directory on the server. The tree is root's alone (0700).
- **Updating rebuilds the same code.** A new version is a new upload and a change of source. The
  tree an application no longer names is removed with it; one nobody ever used is removed after a
  week (`noust upload prune`, which also runs when an upload starts). `noust upload list` shows what
  there is and whether an application uses it.
- **The allowed origins** (`security.allowed_sources`, ENS) name it with `upload`: once a list is
  set, code that arrives as an archive is refused until `upload` is in it.

### A pasted Dockerfile

For an application that is a Dockerfile and nothing else, the wizard's **Or paste a Dockerfile**,
`POST /api/uploads/dockerfile` and `noust upload add Dockerfile` (or `--dockerfile -` to read it from
standard input) keep the text on the server as a project of that one file, and its `upload:<id>` is
the source of a container application like any other. The build context holds nothing else, so a
`COPY` finds nothing: the Dockerfile fetches what it needs itself (`ADD` of a URL, `RUN git clone`, a
base image that has the program). Put anything larger in an archive or a repository. The text is at
most 256 KiB, must have a `FROM`, and is stored, removed and audited like an uploaded archive.

A container application keeps the text it was made from: `noust update` rebuilds that text (and keeps
the previous image to go back to), and a different Dockerfile is another application, because the
source of a container application is not changed in place (`noust app source change` refuses it and
says so).

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
