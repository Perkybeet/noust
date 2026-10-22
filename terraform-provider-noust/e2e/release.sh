#!/bin/sh
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# The release the registry would ingest, made for real and signed with a key thrown away at the end: the
# staged repository is committed and tagged, checked the way the public repository's own workflow checks it,
# and built by GoReleaser with the same .goreleaser.yml, which signs the checksums. Nothing is published:
# there is no token, no upload and no key of the owner's. e2e/verify_release.py then reads what came out.
#
#   release.sh STAGE VERSION
#
# Run by tests/integration/terraform_run.py --stage release inside a golang container (git, gpg and python3 are
# in the image).
set -eu

STAGE=${1:?usage: release.sh STAGE VERSION}
VERSION=${2:?usage: release.sh STAGE VERSION}
GORELEASER=${GORELEASER:-github.com/goreleaser/goreleaser/v2@v2.18.3}
cd "$STAGE"

step() { printf '\n==> %s\n' "$*"; }

step "the staged repository, committed and tagged as the owner would"
git init -q -b main
git config user.name "release-test"
git config user.email "release-test@example.invalid"
git add -A
git commit -q -m "terraform-provider-noust $VERSION"
git tag "v$VERSION"
# GoReleaser reads the repository's owner and name from the remote; nothing is fetched from it.
git remote add origin git@github.com:Perkybeet/terraform-provider-noust.git

step "what the public repository's test workflow runs"
test -z "$(gofmt -l .)" || { gofmt -l .; exit 1; }
go vet ./...
go build ./...
go test ./...

step "the reference under docs/ is current in the staged copy too"
go generate ./...
if [ -n "$(git status --porcelain)" ]; then
  git status --short
  git diff | head -80
  echo "go generate changed the staged repository: docs/ or go.sum are not current" >&2
  exit 1
fi

step "a signing key for this run only"
export GNUPGHOME
GNUPGHOME=$(mktemp -d)
chmod 700 "$GNUPGHOME"
gpg --batch --quiet --passphrase '' --quick-generate-key "noust release test <release-test@example.invalid>" rsa4096 sign 1d
GPG_FINGERPRINT=$(gpg --list-secret-keys --with-colons | awk -F: '/^fpr:/ { print $10; exit }')
export GPG_FINGERPRINT
gpg --armor --export "$GPG_FINGERPRINT" > "$GNUPGHOME/public.asc"

step "goreleaser release (publishing skipped)"
# GoReleaser is built with a newer Go than the provider needs: the go command fetches that toolchain to build
# the tool, and the provider is then built with the module's own Go, as the workflow's setup-go does.
TOOLS=$(mktemp -d)
GOTOOLCHAIN=auto GOBIN="$TOOLS" go install "$GORELEASER"
"$TOOLS/goreleaser" release --clean --skip=publish

step "what came out"
python3 -I "$(dirname "$0")/verify_release.py" "$STAGE/dist" "$VERSION" "$GNUPGHOME/public.asc"
