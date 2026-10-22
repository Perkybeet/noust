# Contributing a recipe

A recipe is a ready-made application in the catalogue: one YAML file, a logo and, sometimes, the files
it mounts. Noust has one maintainer and the catalogue is the part of it that grows fastest with help, so
this page is the whole path from "I would like X in the catalogue" to a pull request that is merged.
[recipes.md](recipes.md) is the reference for the format and the pinning policy; this page is the
order to do things in, and what a reviewer looks for.

## 1. Is it eligible

A project is eligible when all of these hold:

- it has at least **1,000 stars** on the forge that hosts it (GitHub, or Codeberg for projects that live
  there);
- it has made **a release in the last year** and is **not archived**;
- its **licence allows running it for others**. The licence goes in the recipe's `license`; a
  source-available one is said so in the description;
- it ships **container instructions** (an image, and a Compose file or a page that names the variables)
  that you can cite, or a release archive to build from.

A project that publishes a build per commit and no release is pinned to the newest dated build by tag
and digest, and says so. A project that publishes only a floating tag (`latest`, `stable`) cannot be a
recipe: a tag names a release or it is not a pin. It is listed as unavailable, with the reason.

Look in the catalogue first: `noust recipe list -q NAME` (or the Recipes view of Applications). One that is listed
as unavailable names the reason it is.

## 2. Ask for it

Open a **Recipe request** in the repository's issues
([the form](https://github.com/Perkybeet/noust/issues/new?template=recipe_request.yml)). It asks for the
project, why it is wanted, the page with its container instructions, and whether you will write it. A
request nobody writes stays open and visible to others; a request with a person behind it is the
fastest way to a merged recipe. There is no voting board: the requests are the issues, and reacting to
one with a thumbs-up is the vote.

## 3. Write it

Start from a recipe of the same shape (`src/noust/recipes/gotify.yaml` is a small one: one container, a
SQLite file; `ghost.yaml` has a database). Put your file in a folder of your own while you work:

```
my-recipes/
  myapp.yaml
  assets/myapp/logo.svg      # and any file the recipe mounts
```

The rules that decide whether it is accepted, each with the command that checks it:

1. **Every image is pinned to a release tag and to the digest the registry serves for it**
   (`image: repo:1.2.3@sha256:...`). Read the digest, never type it:
   `python scripts/recipe_pins.py resolve ghcr.io/org/app:1.2.3`.
2. **It starts closed.** No default credential: generate one (`generate: password32`) and say where it
   is. A first-visitor-wins page says so at the top of `notes`.
3. **Ports are on the loopback address**: `127.0.0.1:${PORT}:CONTAINER_PORT`. The web server is the
   public face.
4. **Its data is declared.** Every volume is named, and the notes say where the data is and that it
   wants a copy before an update.
5. **Its health check is real.** `health.path` is an endpoint of the application that answers 200
   without a login; the gate asks `http://127.0.0.1:PORT/path` with no forwarded headers, so an
   application that checks the host header must accept `127.0.0.1`.
6. **Its comments cite where its configuration comes from**: the project's own compose file or
   documentation page, not memory.

## 4. Check it

```bash
noust recipe check ./my-recipes                    # the closed schema and the lint, adds nothing
python scripts/recipe_lint.py ./my-recipes         # the same lint CI runs
python scripts/recipe_pins.py check myapp          # every pin against where it is published
python scripts/recipe_smoke.py myapp --evidence /tmp/smoke   # start it on your Docker and ask the gate
noust recipe add ./my-recipes/myapp.yaml           # keep it on a test server and install it for real
```

`recipe_smoke.py` is the check that matters most. It resolves the variables the way a deployment does,
starts the stack as a throwaway Compose project on a loopback port, makes the health gate's request
exactly as the gate makes it, makes it again the way nginx does, looks for a restart loop and an
out-of-memory kill, and removes the project and its volumes. It touches nothing it did not create. Keep
its evidence folder for the pull request. A container that restarted once and left with status 0 is
reported as a note, not a failure: some applications apply their first-run configuration and stop
themselves once (Hoppscotch's backend), and the restart policy brings them back; a restart with any other
status, or a second one, fails the recipe. A file the recipe marks `render: true` is written with its
variables filled in, as a deployment writes it.

## 5. Open the pull request

Put the recipe in `src/noust/recipes/`, its logo and files in `src/noust/recipes/assets/NAME/`, and say
in the description:

- the project, its licence and its release;
- where each setting comes from (the links the comments cite);
- the output of `recipe_pins.py check NAME` and the evidence folder of `recipe_smoke.py`;
- what the application needs that Noust does not provide, if anything (the recipe's `needs`).

The tests load every recipe through the same schema and lint, so a typo cannot ship; run them with
`pytest tests/recipes`. A pull request is built against `main`, and commit messages say what changes
for the operator ("recipes: Gotify, a simple push-notification server"), never who or what wrote them.

## 6. What the review checks

- The five rules above, then the **notes**: someone installing it for the first time reads them, so they
  say where to sign in, what the first account's password is called, and what to back up.
- **Nothing is fetched at install** except what a pin names.
- **The name and the logo.** A project's name and logo are its own: the description does not claim an
  endorsement, and the logo ships only if the project's licence or brand page allows it ([recipes.md](recipes.md#names-marks-licences-and-logos-of-the-projects)).
- **Whether it can be kept up to date.** The lint runs in CI on every pull request; the pins check is run
  by hand, by you before the request and by the maintainer before a release. When upstream publishes a
  newer version, `noust health` says so on every server that installed the recipe
  ([recipes.md](recipes.md#updating-what-you-installed)), and updating the catalogue is
  [a short procedure](recipes.md#updating-the-catalogue).

## Converting a template from another catalogue

Coolify's and Dokploy's catalogues are directories of Compose files. A template there is a good start,
not a recipe: `noust compose import` converts one into a Compose stack
([coolify.md](coolify.md#a-service-template-from-coolifys-catalogue),
[dokploy.md](dokploy.md#a-template-from-dokploys-catalogue)), and pasting that stack into a recipe's
`compose:` is a fair shortcut. It then needs the work above: pin the images, close the defaults, read
the project's own documentation instead of the template's guess, and run the smoke test. Both catalogues
are permissively licensed (Apache-2.0 and MIT); a template's own authors keep their rights in it, so
cite the template as a source in the recipe's comments, not as the author.
