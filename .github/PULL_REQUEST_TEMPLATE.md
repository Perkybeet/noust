## What changes for the operator

<!-- One or two sentences, in the words of someone who runs a server. -->

## Why

<!-- The problem, with the issue number if there is one: "Closes #123". -->

## How it was checked

<!-- Tests added or changed, and anything you ran by hand. -->

## Before asking for review

- [ ] Tests written first for what changed, and `pytest` passes on the files touched
- [ ] `ruff check src/noust tests`, `ruff format --check src/noust tests` and `mypy` pass
- [ ] Text a person reads is in both languages (`panel/src/i18n`), and the documentation that describes the behaviour is updated
- [ ] A change under `panel/` has `npm run lint`, `npm run typecheck` and `npm test` passing, and `src/noust/web/static` rebuilt with `npm run build`
- [ ] The four rules of `CONTRIBUTING.md` hold: nothing runs a process but `CommandRunner`, no `except Exception` outside an error boundary, one implementation of each thing, guards at the chokepoint
- [ ] The contribution is mine to send under the AGPL-3.0-or-later, anything from elsewhere is named here with its licence, and I have signed the [CLA](https://github.com/Perkybeet/noust/blob/main/CLA.md) (a comment on this pull request, once)
- [ ] No secrets: no token, key or password, and no real customer's name, in the code, the fixtures, the logs or the screenshots
