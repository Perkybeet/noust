#!/bin/sh
# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# The provider binary under the real Terraform CLI, against a live Noust API:
# apply the complete example, see that the next plan is empty, change a variable in place, see the plan
# empty again, and destroy. NOUST_URL and NOUST_TOKEN name the server and the credential.
#
#   run.sh DIST      DIST is the directory holding the terraform-provider-noust binary
#
# Run by tests/integration/terraform_run.py inside a hashicorp/terraform container (or on a machine that has
# Terraform or OpenTofu, with TF_BIN naming the CLI).
set -eu

DIST=${1:?usage: run.sh DIST}
EXAMPLE=${NOUST_E2E_EXAMPLE:-/example}
TF=${TF_BIN:-terraform}
WORK=${NOUST_E2E_WORK:-/work}
mkdir -p "$WORK/stack"

# The provider is used straight from DIST, as a developer would: no registry, no init of the provider.
cat > "$WORK/terraformrc" <<EOF
provider_installation {
  dev_overrides {
    "perkybeet/noust" = "$DIST"
  }
  direct {}
}
EOF
export TF_CLI_CONFIG_FILE="$WORK/terraformrc"

cp "$EXAMPLE"/*.tf "$WORK/stack/"
cd "$WORK/stack"

SUFFIX=$(tr -dc 'a-z' < /dev/urandom | head -c 6)
VARS="-var suffix=$SUFFIX -var domain=e2e-$SUFFIX.example.net -var repository=https://code.example.net/acme/site.git -var app_type=static"

step() { printf '\n==> %s\n' "$*"; }

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    step "failed (status $status): destroying what was made"
    # shellcheck disable=SC2086
    $TF destroy -input=false -auto-approve $VARS >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap cleanup EXIT

$TF version

step "validate"
# shellcheck disable=SC2086
$TF validate $VARS

step "plan"
# shellcheck disable=SC2086
$TF plan -input=false -out=first.plan $VARS

step "apply"
$TF apply -input=false -auto-approve first.plan

step "the plan after an apply is empty"
set +e
# shellcheck disable=SC2086
$TF plan -input=false -detailed-exitcode $VARS
code=$?
set -e
if [ "$code" -ne 0 ]; then
  echo "expected an empty plan (exit 0), got exit $code" >&2
  exit 1
fi

step "a change in place"
set +e
# shellcheck disable=SC2086
$TF plan -input=false -detailed-exitcode -var greeting=bonjour $VARS >plan.out
code=$?
set -e
cat plan.out
if [ "$code" -ne 2 ]; then
  echo "expected a plan with changes (exit 2), got exit $code" >&2
  exit 1
fi
if grep -q "must be replaced" plan.out; then
  echo "a variable must change in place, not replace the application" >&2
  exit 1
fi
# shellcheck disable=SC2086
$TF apply -input=false -auto-approve -var greeting=bonjour $VARS

step "empty again"
set +e
# shellcheck disable=SC2086
$TF plan -input=false -detailed-exitcode -var greeting=bonjour $VARS
code=$?
set -e
if [ "$code" -ne 0 ]; then
  echo "expected an empty plan (exit 0), got exit $code" >&2
  exit 1
fi

step "outputs"
$TF output -json | tee outputs.json
grep -q '"status"' outputs.json
grep -q 'ssh-ed25519' outputs.json

step "destroy"
# shellcheck disable=SC2086
$TF destroy -input=false -auto-approve -var greeting=bonjour $VARS

echo
echo "e2e: ok"
