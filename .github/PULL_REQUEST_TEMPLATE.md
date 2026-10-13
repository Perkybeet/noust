## What changes for the operator

<!-- One or two sentences, in the words of someone who runs a server. -->

## Why

<!-- The problem, with the issue number if there is one: "Closes #123". -->

## How it was checked

<!-- Tests added or changed, and anything you ran by hand. -->

## Before asking for review

- [ ] `pytest`, `ruff check src/noust tests`, `ruff format --check src/noust tests` and `mypy` pass
- [ ] A change under `panel/src` also has `npm run lint`, `npm run typecheck` and `npm test` passing, and `src/noust/web/static` rebuilt
- [ ] A new API route declares its permission, a new audit event is in the catalog, and a new dependency is declared in the four places (`CONTRIBUTING.md`)
- [ ] Nothing runs a process except `CommandRunner`, and no `except Exception` outside an error boundary
- [ ] Text a person reads is in both languages (`panel/src/i18n`), and the documentation that describes the behaviour is updated
