// Copyright (c) 2024-2026 Yago López Prado
// SPDX-License-Identifier: AGPL-3.0-or-later

//go:build tools

// Package tools pins the development tools in go.mod, so that every machine and CI generate the reference
// with the same version of tfplugindocs.
package tools

import (
	_ "github.com/hashicorp/terraform-plugin-docs/cmd/tfplugindocs"
)
