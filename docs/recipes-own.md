# Your own recipes, and signed catalogue packages

Part of [Recipes](recipes.md).

## Your own recipes

The catalogue reads three places through one schema: the package, a signed catalogue package
(below) and your folder, `/etc/noust/recipes`.

```bash
noust recipe check ./my-recipes             # the schema and the lint, without adding anything
noust recipe add ./my-recipes/myapp.yaml    # keep it; its logo and files are in assets/myapp/ beside it
noust recipe add ./my-recipes --replace     # overwrite one of yours with the same name
noust recipe remove myapp
```

A private recipe is validated like a shipped one (an unknown key, an image on a floating tag or
without a digest, a link or a logo the console's policy would refuse are errors that name the field),
and a name the package holds is refused: a private recipe has a name of its own. It is shown as yours,
with where it came from, and installed and updated like any other. The folder is read only when root
(or the user running Noust) owns it and nobody else can write it, and no link is followed; one bad file
is named in `noust recipe list` and in the API's `problems` and does not hide the rest.
`scripts/recipe_lint.py` runs the same lint on a folder you are writing, and in CI on the shipped
catalogue. The API is `POST /api/recipes/private/check`, `POST /api/recipes/private` and
`DELETE /api/recipes/private/{name}` (sudo mode, the server's permission); the Recipes view's
**Add a recipe** checks one you paste and keeps it, and a recipe of yours says so on its card and can be
removed from its page.

## Signed catalogue packages

The catalogue can grow between releases from a package, never from a poll: nothing contacts a server
on its own.

```bash
noust recipe catalog trust acme 5d3c...64 hex digits...   # trust a publisher's Ed25519 key
noust recipe catalog import ./recipes-2026-10-13.tar.gz   # reads a file, needs no network
noust recipe catalog fetch https://example.org/recipes.tar.gz   # the one step that downloads (https only)
noust recipe catalog show                                 # which package, who signed it, whose keys are trusted
noust recipe catalog remove                               # back to the recipes this release ships
```

A package is a `.tar.gz` of recipes whose manifest lists every file with its SHA-256 and is signed with
Ed25519 by a key you trust (`/etc/noust/recipe-catalog.keys`, plus the maintainers' keys a release
adds). It is data, never code: the archive is read into memory under size limits, a link or a path
outside the layout is refused, every recipe passes the schema and the lint, the serial must be higher
than the one in place (`--force` accepts one that is not), and nothing in it runs. A recipe of the
package replaces a shipped one only while it is newer; a release that ships a newer one wins again.
`scripts/recipe_catalog.py keygen|pack|verify` is how a publisher makes one; the archive is
deterministic. The API is `GET /api/recipes/catalog`, `POST /api/recipes/catalog/fetch` and
`DELETE /api/recipes/catalog` (sudo mode); the Recipes view's **Catalog package** shows the one in place and
fetches or removes it.

## Updating the catalogue

When upstream releases a version:

```bash
# 1. the new tag's digest, read from the registry
python scripts/recipe_pins.py resolve ghcr.io/dani-garcia/vaultwarden:1.38.0

# 2. in the recipe: the image reference, `version`, `updated`, and a `changelog` entry;
#    keep `revision` at 1 for a new version, raise it for a fix to the recipe itself
# 3. check every pin of the shipped recipes against where it is published
python scripts/recipe_pins.py check --evidence /tmp/pins
# 4. start the stack on your Docker and ask the health gate's question
python scripts/recipe_smoke.py vaultwarden --evidence /tmp/smoke
```

The lookups of writing a recipe are in one tool, `scripts/recipe_draft.py`, so that none is typed
from memory: `tags IMAGE` lists the release tags of an image (newest first), `release OWNER/REPO` the
latest release of a project, `pin FILE --to OUT` adds to every `image:` the digest its registry serves
(reusing the digest another recipe already pins for the same tag, so two recipes on
`postgres:17.11-alpine` are one image on a server) and writes the `architectures:` every image is
published for, `logo NAME TITLE` draws the monogram, and `template NAME` prints a Coolify template to
read next to the project's own documentation.

The smoke test is meant for a machine whose Docker is shared: `--project-prefix noust-test-me-` (for example) names
the Compose project (`<prefix><recipe>`), everything it creates (containers, volumes, the network)
carries the label `noust.test=1` so that a run is found and removed by that label and that prefix and
never by a prune, the loopback port it asks for must be free, and a run stopped with SIGTERM (`timeout`
sends it) still removes what it made. A container that finished with an error is a problem, not only
one that restarts.

`scripts/recipe_docs.py` writes the table at the end of this page from the recipes, and `--check`
fails if it is out of date.

`check` resolves each image's tag in its registry and compares the digest, reads the
architectures it is published for, tests each archive's SHA-256 (or the checksum the project
publishes beside it), and looks for each git tag, failing on any difference; `--evidence` writes what was read,
per recipe, as JSON. It makes only anonymous, read-only requests.

Then read the release notes for what the new version asks of an update (a database migration, a new
variable, a new port) and put it in the recipe: a new variable is declared, and `changelog` says
what changed. The tests load every recipe, so a typo cannot ship.
