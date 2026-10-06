# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Recipes: well-known applications deployed from a declarative file.

A recipe is data shipped with the package (``src/noust/recipes/<name>.yaml``),
never code. Two kinds share one schema (version 2, ``schema: 2``):

- ``native``: a pinned source (a git tag, an archive with its SHA-256, or files
  rendered from the recipe), built by one of the deployers, run by a systemd
  unit behind nginx, with the databases it needs provisioned by Noust.
- ``compose``: an inline Docker Compose document of images pinned by tag and
  digest, deployed by the Compose deployer behind nginx. The catalogue marks it
  "runs in Docker".

Every file is validated strictly when it is loaded - an unknown key is an
error, not something ignored - and every shipped recipe is loaded by the
tests, so a typo cannot reach an operator. A file without ``schema`` is the 2.3
format (version 1) and still loads, as a native recipe.

The format (``docs/recipes.md`` is the contributor's guide)::

    schema: 2
    name: vaultwarden                # the file's own name
    kind: compose                    # native | compose
    title: Vaultwarden
    slogan: One line for the catalogue card.
    description: One or two sentences.
    category: security               # a closed list: see model.CATEGORIES
    tags: [passwords, bitwarden]
    logo: logo.svg                   # an SVG in assets/<name>/, held to the CSP
    version: "1.37.4"                # the upstream version this recipe pins
    revision: 1                      # raised when only the recipe changed
    updated: 2026-10-06
    homepage: https://example.org
    docs: https://example.org/docs
    repository: https://github.com/o/r
    license: AGPL-3.0-only
    port: 8080                       # the default host port nginx proxies to
    needs: [Docker Engine with the Compose plugin]    # prose, for the operator
    requires: [postgres]             # native only: postgres, mysql, mariadb, redis,
                                     # valkey, mongodb (provisioned), s3 (operator's)
    variables:                       # what ends up in the application's .env
      ADMIN_TOKEN: {generate: password64, description: Opens /admin.}
      PUBLIC_URL: {from: url}
      DATABASE_URL: {from: postgres.url}
      SMTP_HOST: {default: ""}
      LICENSE_KEY: {required: true}
      GREETING: {value: "hello ${PUBLIC_URL}"}
    health: {path: /alive, expect: "200"}
    notes:
      - "Open {{ url }} now to create the first account."
    changelog:
      - {version: "1.37.4", summary: What changed.}

    # kind: compose
    compose: |                       # images by tag AND digest; web port 127.0.0.1:${PORT}:N
      services: ...
    files: {config/app.conf: app.conf}   # shipped beside the document
    digest_verified: true            # every image carries a digest read from its registry
    public_ports: [{port: 22000, protocol: tcp, purpose: Sync}]

    # kind: native
    app_type: nodejs                 # a registered deployer
    source:                          # exactly one of:
      git: https://github.com/o/r    #   a repository, with a fixed ref (tag)
      ref: 1.2.3
      archive: https://h/app.tar.gz  #   an archive, with sha256 or checksum_url
      sha256: ...
      template:                      #   files rendered from assets/<name>/
        package.json: package.json
    layout: releases                 # or inplace
    build: {install: ..., build: ..., start: ..., base_directory: ..., runtime: node@22}
    persistent_paths: [data]
    php: {webroot: ".", deny: [...], max_upload: 64m, shared_from_release: [...]}

:mod:`noust.recipes.deploy` turns a recipe into the arguments of an ordinary
deployment; the deployers do the rest. :mod:`noust.recipes.updates` says which
installed recipes have a newer version and updates one through
:func:`noust.deployers.lifecycle.update_app`, so the health gate judges it.
"""

from __future__ import annotations

from noust.recipes.assets import read_asset
from noust.recipes.catalog import (
    categories,
    get_recipe,
    list_recipes,
    logo_svg,
    search_recipes,
)
from noust.recipes.model import (
    CATEGORIES,
    DATABASE_ENGINES,
    KINDS,
    LAYOUTS,
    NAME_PATTERN,
    REQUIREMENTS,
    SCHEMA_VERSION,
    SOURCE_KINDS,
    Recipe,
    RecipeBuild,
    RecipeError,
    RecipeHealth,
    RecipeNotFoundError,
    RecipeSource,
    RecipeVariable,
)
from noust.recipes.schema import parse_recipe

__all__ = [
    "CATEGORIES",
    "DATABASE_ENGINES",
    "KINDS",
    "LAYOUTS",
    "NAME_PATTERN",
    "REQUIREMENTS",
    "SCHEMA_VERSION",
    "SOURCE_KINDS",
    "Recipe",
    "RecipeBuild",
    "RecipeError",
    "RecipeHealth",
    "RecipeNotFoundError",
    "RecipeSource",
    "RecipeVariable",
    "categories",
    "get_recipe",
    "list_recipes",
    "logo_svg",
    "parse_recipe",
    "read_asset",
    "search_recipes",
]
