# See what it will do: the plan of an operation

Before you update an application, go back, restart it, change its limits, renew a certificate, add a
domain, clean up Docker or delete something, you can see what Noust is going to do. A **plan** is the
commands in the order they would run, the files they would write and what changes in each, what the
plan found out by looking, what it does **not** know, and what applying the operation will ask of you.
Nothing is executed to make it, and it leaves nothing behind: no file, no database row, no job.

In the console it is the **See what it will do** fold in the dialog of the operation (the question before an
application is stopped, before it goes back to a deployment, before the approval policy is saved). It is drawn only
where the API says the operation has a plan (`x-noust-plan`), and not at all, rather than empty, on a server whose
Noust does not have it; what the plan does not know is shown whether the fold is open or not. On the terminal it
is `--plan`. In the API it is a `POST` that changes nothing. The built-in assistant shows you the same
plan when it proposes something: the plan is Noust's, never the assistant's.

```
$ noust update shop.example.com --plan
Builds shop.example.com from main as a new release and switches to it once it answers the startup check.
The release that serves now stays on disk to go back to.

Commands, in this order:
    1. npm install
    2. chown -R www-data:www-data /var/www/apps/shop-example-com/releases/20261113-101500-d2008e0
    3. systemctl restart shop-example-com.service
    4. GET http://127.0.0.1:3000/  (a check, up to 30 s)

What the plan found:
  - main has moved on: shop.example.com runs 1a03bb5 and the repository is at d2008e0

What it does not know:
  - Which install and build commands run, whether they pass and how long they take: they come from
    the repository's own files and scripts, which making a plan does not fetch or run
```

## What a plan says

- **What changes**, in one sentence.
- **Commands**, in order. The ones that only look (a `systemctl is-active`, a `git ls-remote`) run for
  real, with a short deadline, because a plan that cannot look at the machine reports fiction; they are
  marked as looks. A startup check is shown in its place and is not asked: what it would ask about does
  not exist yet.
- **Files** that would be written, moved, linked or removed, with the diff. A file of variables shows
  names and never a value; a file only its owner can read shows nothing of its content.
- **Steps**, when the operation announces phases (an update does).
- **What it found**, and **what it does not know**. A plan without its "what I do not know" would be a
  promise nobody can keep: whether a build passes, how long it takes, what a migration does and what a
  script of the repository runs are never in a plan.
- **What applying it will ask for**: sudo mode, a second person, and what is confirmed in the request (a
  freeze of deploys, going back past a deployment that changed the database schema).
- **Why it would be refused now**, when it would: nothing new to deploy, an operation already running on
  the application, a deployment that cannot be gone back to.

## What a plan does not do

It never executes a command that is not declared as one that only looks. It never shows a secret: the
values an application holds, Noust's own credentials and every shape of a credential are masked in the
commands and in the diff. It never asks for sudo mode: it is read with the permission to read what the
operation would change (`apps.read`, or `docker.read` for Docker). A plan of an application of another
team does not exist for you, as the application does not.

A plan is made by running the code that applies the operation in a rehearsal of its own, so what it lists
is what the operation does. What the operation records along the way (a name added to a site, a row in the
history) is not visible to its later steps in a plan, and the plan says so when it matters.

## In the API

An operation that has a plan says where it is in the OpenAPI, in the operation itself:

```json
"x-noust-plan": {"method": "POST", "path": "/api/apps/{domain}/update/plan", "operation": "app.update"}
```

A `{name}` in the plan's path is the operation's own path parameter or the field of its body of that name;
the plan's body is the operation's body and query without those, and a field the plan has no use for is
accepted and ignored, so the body of the operation can be sent as it is. The answer is always the same
shape (`PlanOut`): `effect`, `commands`, `files`, `steps`, `findings`, `unknown`, `needs` and `blocked`,
each sentence with its code and values for the console to say in its language, beside the English.

| Operation | Plan |
|---|---|
| Update an application | `POST /api/apps/{domain}/update/plan` |
| Go back to a deployment | `POST /api/apps/{domain}/deployments/{id}/rollback/plan` |
| Restart, stop or start | `POST /api/apps/{domain}/{restart,stop,start}/plan` |
| Change limits | `POST /api/apps/{domain}/limits/plan` |
| Renew a certificate | `POST /api/certs/{domain}/renew/plan` |
| Add or remove a domain | `POST /api/apps/{domain}/domains/plan`, `.../domains/{name}/plan` |
| Delete an application | `POST /api/apps/{domain}/delete/plan` |
| Move an application onto releases | `POST /api/apps/{domain}/migrate/plan` |
| Apply a cleanup of Docker | `POST /api/docker/cleanup/plan` |

The list only grows. On a central, the plan of an operation of a server is asked of that server; one whose
server is older has no plan, and its `x-noust-plan` is not in its OpenAPI, so the console offers none.

## On the terminal

`noust update DOMAIN --plan` and `noust delete DOMAIN --plan` print the same plan and do nothing.
`--dry-run` stays what it was: it runs the command with every change refused.
