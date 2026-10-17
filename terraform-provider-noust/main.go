// Copyright (c) 2024-2026 Yago Lopez Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

// Command terraform-provider-noust is the Terraform and OpenTofu provider for Noust servers.
package main

// The reference under docs/ is what the registry shows. It is generated from the schemas and examples/;
// CI fails when it differs from what this writes.
//go:generate go run github.com/hashicorp/terraform-plugin-docs/cmd/tfplugindocs generate --provider-name noust

import (
	"context"
	"flag"
	"log"

	"github.com/hashicorp/terraform-plugin-framework/providerserver"

	"github.com/Perkybeet/terraform-provider-noust/internal/provider"
)

// version is set by the release build (-ldflags "-X main.version=...").
var version = "dev"

func main() {
	var debug bool
	flag.BoolVar(&debug, "debug", false, "start the provider with support for debuggers such as delve")
	flag.Parse()

	err := providerserver.Serve(context.Background(), provider.New(version), providerserver.ServeOpts{
		Address: "registry.terraform.io/perkybeet/noust",
		Debug:   debug,
	})
	if err != nil {
		log.Fatal(err.Error())
	}
}
