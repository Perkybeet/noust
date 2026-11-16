# Deploy with git push, or from an archive

Part of [Sources: where the code comes from and what deploys it](sources.md).

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
