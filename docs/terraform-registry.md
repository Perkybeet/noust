# Publishing the provider to the Terraform Registry

The provider is written, built and tested in this repository ([terraform.md](terraform.md)). Publishing it
to the Terraform Registry (and to OpenTofu's) needs three things that are the owner's and that nothing in
this repository creates: a **public repository of its own**, a **GPG signing key**, and an **account on the
registry**. This page is the guide and `scripts/prepare_terraform_registry.py` is the script that makes the
first of them easy. The script writes a directory on your machine; it creates no repository, pushes nothing,
tags nothing and touches no key.

## Why a repository of its own

The registry publishes a provider only from a public GitHub repository named exactly
`terraform-provider-<name>` (here `terraform-provider-noust`), under the account or organisation that
becomes the provider's namespace, and only from its releases. It cannot read a subdirectory of another
repository, and its release files have a fixed shape. So the provider is developed here, where the API it
is a client of lives and where its tests find the server they run against, and published from a copy.

## What is already done

* The code: `terraform-provider-noust/`, with its tests, its generated reference (`docs/`, written by
  `tfplugindocs` and checked fresh by CI) and its examples (`examples/`), which are the three things the
  registry shows.
* The release machinery, in the directory: `.goreleaser.yml` builds a zip per platform, writes
  `SHA256SUMS` and signs it with the key in the environment; `terraform-registry-manifest.json` says the
  provider speaks protocol 6; `.github/workflows/release.yml` runs GoReleaser when a `v*` tag is pushed,
  and `.github/workflows/test.yml` runs the unit tests on every push. They are templates here (GitHub only
  runs workflows at the root of a repository) and become live in the staged repository.
* The module path (`github.com/Perkybeet/terraform-provider-noust`) and the registry address
  (`registry.terraform.io/perkybeet/noust`, so `source = "perkybeet/noust"`) are written for the account
  `Perkybeet`. For another account or an organisation, stage with `--owner` and they are rewritten.

## What needs you

### 1. Decide

* **The namespace.** The registry namespace is the lower-cased GitHub account that owns the repository. The
  default is `Perkybeet`; an organisation (`noust`?) is a better home for a product, and its name is the one
  users will write in `source`. It cannot be moved afterwards without breaking them.
* **The licence.** The provider is AGPL-3.0-or-later like the rest of Noust. A provider runs as its own
  process and talks to Terraform over gRPC, so the licence does not reach the configurations that use it, but
  the Terraform ecosystem is mostly MPL-2.0 and a copyleft licence is a friction some companies will not
  accept. As the copyright holder you may license this directory differently; change `LICENSE` before the
  first release.
* **The first version.** `0.1.0` is what the examples say.

### 2. Make the repository

```bash
python scripts/prepare_terraform_registry.py                 # or --owner SomeOrg
cd build/terraform-registry/terraform-provider-noust
```

Create the repository on GitHub, public and empty (no README, no licence, nothing), named
`terraform-provider-noust`, and push the staged directory as its first commit. The script prints these
commands for the owner it staged for; it does not run them:

```bash
gh repo create Perkybeet/terraform-provider-noust --public --description "Terraform and OpenTofu provider for Noust"
git init -b main && git add -A && git commit -m "terraform-provider-noust 0.1.0"
git remote add origin git@github.com:Perkybeet/terraform-provider-noust.git
git push -u origin main
```

`--check` on its own (`python scripts/prepare_terraform_registry.py --check`) says whether the provider has
everything the registry needs without writing anything. The staged copy leaves out `e2e/` (it drives this
repository's harness) and makes the links of its README absolute.

### 3. Make the signing key

The registry only accepts a release whose `SHA256SUMS` file is signed, with a key whose public half you give
it. Make one for the provider, with a passphrase, on a machine you trust:

```bash
gpg --full-generate-key                    # RSA and RSA, 4096 bits, a long validity, a passphrase
gpg --list-secret-keys --keyid-format=long # note the key id after "rsa4096/"
gpg --armor --export <KEY-ID>              # the public half: for the registry
gpg --armor --export-secret-keys <KEY-ID>  # the private half: for the repository's secrets
```

The private half goes to the **public repository's** Actions secrets and nowhere else: `GPG_PRIVATE_KEY`
(the armoured block) and `PASSPHRASE`. It is never committed, never put in this repository's secrets, and
never pasted into a chat or an issue. Keep an offline copy: a lost key means a new key and a new version
that people must trust again. RSA is the type the registry documents as accepted; check its page on signing
keys if you prefer another.

### 4. Register with the registry

1. Sign in at <https://registry.terraform.io> with GitHub, and authorise it for the account (or the
   organisation) that owns the repository.
2. Settings, Signing Keys: add the **public** key you exported, under the namespace.
3. Publish, Provider: pick the repository. The registry installs a webhook on it so that every release is
   ingested; nothing is published until a release exists.

### 5. Release

```bash
git tag v0.1.0
git push origin v0.1.0
```

The `release` workflow imports the key from the secrets, runs GoReleaser, and the GitHub release it
creates carries the files the registry reads: `terraform-provider-noust_0.1.0_<os>_<arch>.zip` for each
platform, `..._SHA256SUMS`, `..._SHA256SUMS.sig` and `..._manifest.json`. The registry ingests it within a
few minutes; `terraform init` with `source = "perkybeet/noust"` then installs it.

### 6. OpenTofu

OpenTofu has a registry of its own, `registry.opentofu.org`, which reads the same GitHub release. A provider
is added to it by a submission to <https://github.com/opentofu/registry> (an issue from its provider
template, naming the repository), and its signing key by a second one. Follow the instructions that
repository publishes: they are the source of truth and change.

## Releasing again

The provider is developed here, so a release is a refresh of the public repository:

```bash
python scripts/prepare_terraform_registry.py --out /tmp/stage/terraform-provider-noust
rsync -a --delete --exclude .git /tmp/stage/terraform-provider-noust/ ../terraform-provider-noust/
cd ../terraform-provider-noust && git add -A && git commit -m "0.2.0" && git tag v0.2.0 && git push && git push origin v0.2.0
```

Versions follow the provider, not Noust: a release that changes nothing a configuration can see is a patch;
a new resource or attribute is a minor; an attribute renamed, removed or made to replace the resource is a
major (and the plan shows `must be replaced`). The API is versioned separately (`/api/v1`), and the provider
refuses a server that speaks another version, so a Noust that adds `/api/v2` next to `/api/v1` does not break
a provider built for 1.

## Checking a release

```bash
curl -sL https://github.com/<owner>/terraform-provider-noust/releases/download/v0.1.0/terraform-provider-noust_0.1.0_SHA256SUMS -o SUMS
curl -sL https://github.com/<owner>/terraform-provider-noust/releases/download/v0.1.0/terraform-provider-noust_0.1.0_SHA256SUMS.sig -o SUMS.sig
gpg --verify SUMS.sig SUMS
```

and `terraform init` in an empty directory with the `required_providers` block of the README, which verifies
the same signature against the key the registry holds.
