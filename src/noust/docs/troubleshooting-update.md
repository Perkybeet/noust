# An update says it is on the way, or the package index is behind

A release exists on GitHub the moment it is published, but its packages are built afterwards, one
repository per distribution. For a while, some distributions serve the new version and others the
old one. Noust says which case you are in instead of offering an update that would install nothing.

## The version is on the way

```text
Version 4.1.0 is on the way
It is published, but the package for this server is not available yet. Packages usually follow a release within 15 to 30 minutes. Nothing to do now.
```

Nothing to do: check again later. On a central, **Fleet > Updates** shows the target version and
how many servers are on it.

## The package index is behind

```text
4.1.0 is published; this server's package index has not seen it yet
```

The repository has the package, and this server's package manager has not refreshed its list since.
Refresh it, then update:

```bash
noust server updates refresh
```

In the console: **Refresh the package index**, beside the message. The update refreshes it by
itself as well.

## The update stopped before installing anything

Noust rehearses the update with the package manager first, and checks the free disk space and the
store. When one of them refuses, the update stops before any package changes, and the message
carries the package manager's or the check's own words. Fix what it names and update again.

## Going back to an earlier version

[Going back to an earlier Noust](downgrading.md) says which releases can open which store, and how.
