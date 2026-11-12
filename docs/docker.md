# Docker: new versions, stacks and their Compose

Noust looks after all of the Docker on a server, also what it did not create, and says whose each thing is
(an application of Noust, a database engine of Noust, Noust itself, or nobody's: "Noust did not create it").
This page is about what changes it: updating the images in use with a net, taking a stack that already runs
into Noust, and editing the Compose of a stack so that a bad edit goes back by itself. The console shows it in
the server's Docker section; every step has a command, and both are the same implementation.

## Which images have a new version

```bash
noust docker images check          # ask the registries, with the accounts Noust keeps for them
noust docker images list           # what the last look found, and what updating each would do
noust docker images plan nginx:1.27
```

Nothing is asked of a registry unless you ask (the console's **Look for new versions**, or `check`): the
summary and the images page say what was found the last time, with when. A look asks each registry the question
`docker pull` asks, without taking the layers: the digest behind the tag, compared with the digest the running
image came with. A Docker without the buildx plugin cannot ask that way and says so; `check --pull` downloads
what moved and compares (what the existing image update does), and nothing is downloaded without it. The
account Noust keeps for a registry (`noust registry add`) is used for that registry and for no other, in a
private configuration removed afterwards. An image built on this machine has no registry to ask, and one pinned by
a digest never moves: both are said, not dropped.

A new version says what it is to the one that runs:

| | |
|---|---|
| `patch`, `minor`, `major` | where the two versions first differ; read from the images' own labels and `*_VERSION` variables, not guessed from a tag |
| floating | the tag is `latest`, `stable`, `alpine`: no number says what it will be |

A tag that carries a major (`postgres:16`, `redis:7-alpine`) cannot jump to another major by moving. A
database whose new version cannot be read is treated as a major one.

## Updating with a net

```bash
noust docker images update status.example.com/image   # an image of an application
```

What belongs to an application is updated by the application's update (`noust update`): the health gate, and
the images it keeps to go back to. Every image of it that has a new version moves with the update, and the plan
says which before it starts. What is not an application's is not updated blind:

- **A stack Noust did not create** is brought to Noust first (below), which changes nothing of what runs.
- **A container on its own** is not updated: Noust does not know how it was created, so it cannot recreate it the
  same way. It is not brought to Noust in this version.
- **A database engine of Noust** is upgraded through the engine (`noust db instance upgrade NAME`).

**A major version is a decision.** It is taken with `--accept-major` (the console asks), and a database's
also needs a copy of the application's databases that was proven to restore within the last day (a restore drill
that loaded a database, `noust backup create DOMAIN` then `noust backup drill DOMAIN`). Until there is one the
update is refused and says which command makes it. The rule looks at everything that moves with the update, not
at the image you clicked: an application's update pulls all its images, so the update of a web service is not a
way for its database to change major.

## Starting, stopping and restarting a stack

```bash
noust docker stack list
noust docker stack stop nextcloud      # and start, restart
```

A stack nobody of Noust made is started, stopped and restarted as a whole, in the order Compose gives (what a
service waits for starts after it and stops before it). One an application owns is refused with its own command
(`noust stop shop.example.com`), and a database engine's with the engine's.

## Bringing a stack to Noust

```bash
noust docker stack bring nextcloud --preview     # what would be registered; nothing is
noust docker stack bring nextcloud --name cloud  # do it
```

This is `noust app adopt` offered where the stack is seen. Noust registers the stack as it runs: no container is
recreated, restarted or changed, and its compose file is not rewritten. The folder and the file are what its
containers say; the only thing you type is the name it will have in the applications' list. Before it does, it
says what it checked (the folder is on this server, the rehearsed `docker compose up --dry-run` changes nothing),
how many containers, jobs that already ended, volumes and networks it has, and what would not come back after a
restart. It is refused first, with the reason, for a stack that is already Noust's, one whose folder is not on this
server (Portainer keeps its stacks in its own volume), one started from several compose files, and a name that is
taken. Compose's own words, when starting it the way Noust would changes something, are shown as they are.

## Editing the Compose of a stack

```bash
noust docker stack compose show nextcloud
noust docker stack compose check nextcloud new.yml           # judge it, write nothing
noust docker stack compose apply nextcloud new.yml --reason "Limit the web service's memory"
noust docker stack compose history nextcloud
noust docker stack compose diff nextcloud 3                  # what putting version 3 back would change
noust docker stack compose restore nextcloud 3
```

- **Judged by what Compose resolves it to.** The text goes to `docker compose config` on its standard input
  (it is written nowhere), and the guard of privileges reads the model that comes back, so an `include`, an
  `extends` or a `volumes_from` cannot hide a privileged service or the Docker socket. When Compose does not read
  it you see Compose's own words and the line. What the stack already ran with is not new, and an exception
  recorded for the stack lets it through with its reason said.
- **What changes is said first**, from Compose's own rehearsal (`up --dry-run`): which services are recreated,
  which are not touched, whether a volume would go. The images the change brings in are looked for in their
  registries, and a tag that does not exist stops it; one that could not be asked does not.
- **Applying is the application's update**, behind its health gate. When the stack does not come up the document
  before is back where the next update reads it and the stack runs it again; the error says whether it answers.
- **Every saved text is a version**: who saved it, when and why, the latest 30. The first save also keeps what the
  stack had, so there is always a document to go back to. Putting one back is an edit like any other (judged, kept,
  applied behind the gate), and a document that no longer passes is not put back.

Two kinds of stack have a document to edit here: one deployed from a document (Noust keeps it), and one brought to
Noust where it already ran that is not a repository (its file, in its own folder, keeps its mode). A stack from a
repository has its document in git, a recipe's is the recipe's (`noust recipe update`), a container
application's is generated from its settings (`noust app container set`), and one Noust did not make is brought
to it first; each says so, with the command, and nothing is changed. A link is never written through.

Reading a document, or a version of it, is a reveal (it can hold a value somebody typed into it) and is recorded;
judging one asks for the same permission, because Compose resolves it with the stack's own variables;
applying needs sudo mode and is in the audit trail. The console and the API do the same:
`/api/docker/images`, `/api/docker/stacks/{name}/...` (see the API reference).
