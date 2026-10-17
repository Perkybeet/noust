# What a developer with Go and Terraform on the PATH runs. Nothing needs installing, though: the Noust
# repository's tests/integration/terraform_run.py runs all of this in containers.

default: build

build:
	go build ./...

install:
	go install .

fmt:
	gofmt -s -w .

vet:
	go vet ./...

# Unit tests, with the contract tests that hold the provider to panel/openapi.json when it is found.
test:
	go test ./...

# Acceptance tests need a Noust API and its tokens (see tests/integration/terraform_run.py).
testacc:
	TF_ACC=1 go test -count=1 -timeout 40m -v ./internal/provider/

# The reference under docs/ is generated from the schemas and the examples/ directory.
docs:
	go generate ./...

.PHONY: default build install fmt vet test testacc docs
