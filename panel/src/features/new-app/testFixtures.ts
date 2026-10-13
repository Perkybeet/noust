/**
 * What the new-app wizard's tests answer for the API: an export document, the recipes, and
 * an inspection that found another platform's configuration. Test data only.
 */

import type { AppExportDocument } from "../../api/queries/appImport";
import type { Recipe, RecipeList, RecipeSummary, RecipeVariableInfo } from "../../api/queries/recipes";

export const EXPORT: AppExportDocument = {
  format: "wasm-app",
  version: 1,
  exported_at: "2026-09-28T10:00:00+00:00",
  wasm_version: "2.3.0",
  secrets_included: false,
  app: {
    domain: "shop.example.com",
    app_type: "nextjs",
    source: "https://***@github.com/acme/shop.git",
    branch: "main",
    layout: "releases",
    port: 3000,
    webserver: "nginx",
    ssl: true,
    include_www: false,
    persistent_paths: ["uploads"],
  },
  domains: { aliases: ["store.example.com"], redirects: [] },
  env: {
    NODE_ENV: { secret: false, value: "production" },
    STRIPE_KEY: { secret: true, value: null },
    DATABASE_URL: { secret: true, value: null },
  },
  env_secret_marks: { STRIPE_KEY: true, DATABASE_URL: true },
  cron: [{ name: "nightly", schedule: "0 3 * * *", command: "npm run nightly", enabled: true }],
  backup: { schedule: "daily", include_databases: true, destinations: [] },
  previews: null,
  github: { installation_linked: false },
  databases: [],
};


/** What every recipe of the catalogue carries besides its name and what it runs: the same for each of these tests. */
const CATALOGUE: Pick<RecipeSummary, "architectures" | "dependencies" | "kind" | "origin" | "revision" | "runs_in_docker" | "schema_version" | "tags"> = {
  architectures: [],
  origin: "shipped",
  dependencies: [],
  kind: "native",
  revision: 1,
  runs_in_docker: false,
  schema_version: 2,
  tags: [],
};

export const RECIPES: RecipeList = {
  items: [
    {
      name: "wordpress",
      title: "WordPress",
      description: "The website and blogging platform, on PHP-FPM with a MariaDB or MySQL database.",
      homepage: "https://wordpress.org",
      slogan: "",
      category: "other",
      ...CATALOGUE,
      available: true,
      app_type: "php-fpm",
      database: "mysql",
      dependencies: ["mysql"],
      requires: ["PHP-FPM 7.4 or newer", "MariaDB or MySQL (noust db install mysql)"],
    },
    {
      name: "uptime-kuma",
      title: "Uptime Kuma",
      description: "Self-hosted monitoring of websites, APIs and servers.",
      homepage: "https://uptime.kuma.pet",
      slogan: "",
      category: "other",
      ...CATALOGUE,
      available: true,
      app_type: "nodejs",
      database: null,
      requires: [],
    },
    {
      name: "ghost",
      title: "Ghost",
      description: "A publishing platform for blogs, newsletters and paid memberships.",
      homepage: "https://ghost.org",
      slogan: "",
      category: "other",
      ...CATALOGUE,
      available: false,
      unavailable_reason: "Ghost supports only MySQL 8 in production, not MariaDB. Not available in 2.3.",
      app_type: null,
      database: null,
      requires: [],
    },
  ],
  total: 3,
  problems: [],
};

/** A variable of a recipe as the API lists it: one the operator may set, unless the patch says otherwise. */
export function recipeVariable(name: string, patch: Partial<RecipeVariableInfo> = {}): RecipeVariableInfo {
  return { name, origin: "default", input: true, required: false, secret: false, description: "", default: null, generate: null, ...patch };
}

/** A variable the deployment fills in itself: asked of nobody. */
export function generatedVariable(name: string, patch: Partial<RecipeVariableInfo> = {}): RecipeVariableInfo {
  return recipeVariable(name, { origin: "generated", input: false, secret: true, generate: "password64", ...patch });
}

export const WORDPRESS: Recipe = {
  name: "wordpress",
  title: "WordPress",
  description: "The website and blogging platform, on PHP-FPM with a MariaDB or MySQL database.",
  homepage: "https://wordpress.org",
  slogan: "",
  category: "cms",
  ...CATALOGUE,
  available: true,
  app_type: "php-fpm",
  database: "mysql",
  dependencies: ["mysql"],
  requires: ["PHP-FPM 7.4 or newer"],
  variables: [generatedVariable("WORDPRESS_DB_PASSWORD", { origin: "provisioned", generate: null }), generatedVariable("WORDPRESS_AUTH_KEY"), recipeVariable("WP_DEBUG")],
  images: [],
  public_ports: [],
  changelog: [],
  layout: "releases",
  port: null,
  env: [
    { name: "WORDPRESS_DB_PASSWORD", generated: true },
    { name: "WORDPRESS_AUTH_KEY", generated: true },
    { name: "WP_DEBUG", generated: false },
  ],
  persistent_paths: ["wp-content"],
  health: { path: "/", expect: "200-399" },
  notes: ["Open {{ url }}/wp-admin/install.php now."],
  source: { kind: "archive", url: "https://wordpress.org/latest.tar.gz", checksum_url: "https://wordpress.org/latest.tar.gz.sha1", files: [] },
};
