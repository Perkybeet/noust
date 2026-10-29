# Building on one server and running on another

Part of [Releases](releases.md).

A small VPS that runs an application is a poor place to compile it: a Next.js build wants more
memory than the application ever will. A release is immutable once it is built, which is what lets
it be built somewhere else and carried to the server that serves it as one checksummed file:

```bash
# on the server that builds
noust releases bundle shop.example.com -o shop.noust-release.tar.gz
#   Packed release 20261017-101500-9f8e7d6 of shop.example.com into shop.noust-release.tar.gz
#   SHA-256  4b1c...  (also in shop.noust-release.tar.gz.sha256)

# carry it with scp, rsync or a CI artifact, then on the server that runs it
noust releases receive shop.example.com shop.noust-release.tar.gz --sha256 4b1c...
```

- **`bundle` builds like an update, up to the switch.** The source is fetched, the dependencies
  installed and the project built in the build sandbox, as `noust-build`; the system packages the
  application declares are installed first. It stops there: no migration, no `pre_deploy` or
  `post_deploy` hook, no switch and no deployment in the history, and the release it built is
  removed once packed, so it cannot be activated or rolled back to on the builder. `--commit SHA`
  builds that commit, `--release ID` packs a release that is already built (it is not removed),
  and `--keep` keeps what it built.
- **`receive` is the promotion of a release from another application.** It activates the release
  behind the health gate, after the receiving application's own `pre_deploy` hooks, with that
  application's own `.env`, database and persistent paths and after its own system packages are
  installed. A release that does not answer is put back and removed; going back past a deployment
  that changed the schema asks for `--schema-changed-ok`, like a rollback. It is a deployment of
  the receiver, and `noust releases rollback` goes back.
- **The checksum is mandatory and the file is untrusted.** `receive` reads nothing from a bundle
  that is not the file the checksum names (a truncated transfer and a swapped file are the same
  refusal), judges the manifest (the application type must be the receiver's, the release id must
  be one) before it extracts a byte, and extracts with the checks every archive gets: no absolute
  path, no `..`, nothing written through a link, a link only where it stays inside the release, no
  device file, no setuid bit, and caps on members (2,000,000) and bytes (16 GiB). Give the checksum
  from a channel the file did not travel in; the `.sha256` beside the bundle protects against a
  damaged transfer and no more.
- **What a bundle carries.** The built tree, without the links a release has to its application's
  `shared/` (its `.env` and persistent paths are the builder's own; the receiver links its own).
  A tree with a link that points outside the release cannot be packed: a Python virtual
  environment links to the system's interpreter, so build such an application where it runs. The
  builder's application directory, if the tree names it in a text file, is rewritten to the
  receiver's.
- **The application is registered on both servers**, with the same type, the builder with its
  source, variables and build settings and the receiver with its domain, site and database.
  Prisma's automatic migration belongs to an update: for a bundled application declare the
  migration as a `pre_deploy` hook of the receiver.

A central can drive both ends: it picks the builder, asks it for the bundle and carries it to the
servers that run the application over the fleet's own tunnels, in chunks, with the same checksum and
the same health gate. That is [building on one server and running on others](CENTRAL.md#building-on-one-server-and-running-on-others)
(`noust fleet placement ship`, `/api/fleet/placements/{domain}/ship` and Fleet > Applications > On several servers).
The file is still the whole interface when no central is involved, so anything that moves a file
(scp, a CI artifact store) carries it as well.

On a server, the same operations are in the API for whoever carries the file: `POST
/api/apps/{domain}/releases/bundle` queues the build and leaves the bundle in a spool (owner-only, swept
after a day), `GET .../releases/bundles/{id}/data` reads it by ranges and `DELETE` lets go of it, and
`POST .../releases/uploads` (chunks at offsets, the digest checked when it completes) and `POST
.../releases/receive` activate one that arrived. All of them are `apps.deploy`, and a freeze of deploys
holds the receiving. The largest bundle a server takes is `releases.bundle_max_bytes` (8 GiB).
