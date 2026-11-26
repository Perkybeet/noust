# Noust documentation

These are the sources of the documentation published at <https://noust.dev/docs/>, where it has
search, a sidebar and one address per page. Read it there.

- [`site.yaml`](site.yaml) says which page each file is, in which part and under which address.
- A page is edited here. `python scripts/generate_packaged_docs.py` then refreshes the copy the
  package carries in `src/noust/docs/`, and a test fails until it has run.
- Changelogs (`CHANGELOG-*.md`) and upgrade guides (`UPGRADING-*.md`) are one file per minor release.
- Where to ask and how an issue is handled: [SUPPORT.md](SUPPORT.md). How to contribute:
  [CONTRIBUTING.md](../CONTRIBUTING.md).
