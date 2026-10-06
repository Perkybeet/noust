/**
 * What the catalogue's tests answer for the API: a catalogue of recipes in the 3.4 shape, the
 * server's own filtering over it, one recipe in full, and what was installed from them. Test
 * data only.
 */

import type { InstalledRecipe, Recipe, RecipeFilters, RecipeSummary } from "../../api/queries/recipes";
import { json, problem } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { generatedVariable, recipeVariable } from "../new-app/testFixtures";

const BASE: Omit<RecipeSummary, "name" | "title"> = {
  slogan: "",
  description: "A recipe.",
  homepage: "https://example.org",
  docs: null,
  repository: null,
  license: null,
  min_noust: null,
  architectures: ["amd64", "arm64"],
  category: "other",
  tags: [],
  logo: null,
  version: "1.0.0",
  revision: 1,
  updated: "2026-10-06",
  kind: "compose",
  runs_in_docker: true,
  available: true,
  unavailable_reason: null,
  app_type: "docker-compose",
  database: null,
  dependencies: [],
  requires: ["Docker Engine with the Compose plugin"],
  digest_verified: true,
  schema_version: 2,
};

function summary(name: string, title: string, patch: Partial<RecipeSummary> = {}): RecipeSummary {
  return { ...BASE, name, title, logo: `/api/recipes/${name}/logo`, ...patch };
}

export const VAULTWARDEN = summary("vaultwarden", "Vaultwarden", {
  slogan: "A lightweight Bitwarden-compatible password manager server.",
  description: "An unofficial Bitwarden server written in Rust.",
  homepage: "https://github.com/dani-garcia/vaultwarden",
  docs: "https://github.com/dani-garcia/vaultwarden/wiki",
  license: "AGPL-3.0-only",
  category: "security",
  tags: ["passwords", "bitwarden", "vault", "self-hosted"],
  version: "1.37.4",
});

export const WORDPRESS = summary("wordpress", "WordPress", {
  slogan: "The website and blogging platform.",
  description: "WordPress on PHP-FPM with a MariaDB or MySQL database.",
  homepage: "https://wordpress.org",
  category: "cms",
  tags: ["blog", "website", "php"],
  version: "7.1.2",
  kind: "native",
  runs_in_docker: false,
  app_type: "php-fpm",
  database: "mysql",
  dependencies: ["mysql"],
  requires: ["PHP-FPM 7.4 or newer with the mysqli, curl, gd, mbstring, xml, zip and intl extensions"],
  architectures: [],
  digest_verified: null,
});

export const FRESHRSS = summary("freshrss", "FreshRSS", {
  slogan: "A free, self-hosted feed aggregator.",
  homepage: "https://freshrss.org",
  category: "productivity",
  tags: ["rss", "feeds", "reader"],
  version: "1.30.1",
});

export const SYNCTHING = summary("syncthing", "Syncthing", {
  slogan: "Continuous file synchronisation, with no cloud in between.",
  homepage: "https://syncthing.net",
  category: "files",
  tags: ["sync", "files", "backup", "p2p", "mesh"],
  version: "2.1.6",
  revision: 2,
});

export const GHOST = summary("ghost", "Ghost", {
  slogan: "A publishing platform for blogs and newsletters.",
  homepage: "https://ghost.org",
  category: "cms",
  tags: ["blog", "newsletter"],
  version: null,
  updated: null,
  available: false,
  unavailable_reason: "Ghost supports only MySQL 8 in production, not MariaDB. Not available in this release.",
  app_type: null,
  kind: "native",
  runs_in_docker: false,
  requires: [],
});

/** Twenty recipes more, so the catalogue is longer than the smallest page. */
const FILLERS: RecipeSummary[] = Array.from({ length: 20 }, (_, index) => {
  const name = `tool-${String(index + 1).padStart(2, "0")}`;
  return summary(name, `Tool ${String(index + 1).padStart(2, "0")}`, { category: index % 2 === 0 ? "utilities" : "development", tags: ["tools"] });
});

export const CATALOGUE: readonly RecipeSummary[] = [VAULTWARDEN, WORDPRESS, FRESHRSS, SYNCTHING, ...FILLERS, GHOST];

/** What the recipes of this fixture are filtered by, as the server does: every filter given must hold. */
export function filterCatalogue(recipes: readonly RecipeSummary[], filters: RecipeFilters): RecipeSummary[] {
  const words = (filters.q ?? "").toLowerCase().split(/\s+/).filter((word) => word !== "");
  return recipes.filter((recipe) => {
    if (filters.category !== undefined && recipe.category !== filters.category) return false;
    if (filters.tag !== undefined && !recipe.tags.includes(filters.tag)) return false;
    if (filters.kind !== undefined && recipe.kind !== filters.kind) return false;
    const haystack = [recipe.name, recipe.title, recipe.slogan, recipe.description, recipe.category, ...recipe.tags].join(" ").toLowerCase();
    return words.every((word) => haystack.includes(word));
  });
}

/** `GET /api/recipes` over the fixture catalogue, filtering by what the query says. */
export const recipesRoute: RouteHandler = (call) => {
  const filters: RecipeFilters = {};
  const q = call.search.get("q");
  const category = call.search.get("category");
  const tag = call.search.get("tag");
  const kind = call.search.get("kind");
  if (q !== null) filters.q = q;
  if (category !== null) filters.category = category;
  if (tag !== null) filters.tag = tag;
  if (kind === "native" || kind === "compose") filters.kind = kind;
  const items = filterCatalogue(CATALOGUE, filters);
  return json(200, { items, total: items.length });
};

/** `GET /api/recipes/categories`: the categories that hold a recipe. */
export const categoriesRoute: RouteHandler = () => {
  const counts = new Map<string, number>();
  for (const recipe of CATALOGUE) counts.set(recipe.category, (counts.get(recipe.category) ?? 0) + 1);
  return json(200, { items: [...counts].map(([category, count]) => ({ category, count })).sort((a, b) => a.category.localeCompare(b.category)) });
};

/** One recipe in full: what its page and its install form read. */
export function full(recipe: RecipeSummary, patch: Partial<Recipe> = {}): Recipe {
  return {
    ...recipe,
    source: null,
    layout: "inplace",
    port: 8000,
    build: null,
    variables: [],
    env: [],
    images: [],
    public_ports: [],
    persistent_paths: [],
    health: { path: "/alive", expect: "200", timeout: null },
    notes: [],
    changelog: [],
    ...patch,
  };
}

export const VAULTWARDEN_FULL = full(VAULTWARDEN, {
  variables: [
    recipeVariable("SIGNUPS_ALLOWED", { default: "true", description: "Whether anybody can create an account. Set it to false once yours exists." }),
    generatedVariable("ADMIN_TOKEN", { description: "Opens the /admin page." }),
    recipeVariable("DOMAIN", { origin: "deployment", input: false, description: "The address people use." }),
  ],
  env: [
    { name: "SIGNUPS_ALLOWED", generated: false },
    { name: "ADMIN_TOKEN", generated: true },
    { name: "DOMAIN", generated: true },
  ],
  images: [{ service: "vaultwarden", repository: "ghcr.io/dani-garcia/vaultwarden", tag: "1.37.4", digest: "sha256:efb3cde962015fcc036b2ea625242248611943b27212ebf4392841fb30fad055" }],
  notes: ["Open {{ url }} and create your account now: anybody who reaches it can create one.", "The admin page is {{ url }}/admin; run noust env show {{ domain }} --unmask."],
  changelog: [{ version: "1.37.4", summary: "Pinned to the 1.37.4 image." }],
});

export const FRESHRSS_FULL = full(FRESHRSS, {
  variables: [
    recipeVariable("ADMIN_EMAIL", { origin: "required", required: true, description: "The e-mail address of the administrator account." }),
    recipeVariable("ADMIN_USER", { default: "admin", description: "The administrator's user name." }),
    generatedVariable("ADMIN_PASSWORD", { description: "The administrator's password." }),
  ],
  env: [
    { name: "ADMIN_EMAIL", generated: false },
    { name: "ADMIN_USER", generated: false },
    { name: "ADMIN_PASSWORD", generated: true },
  ],
});

export const SYNCTHING_FULL = full(SYNCTHING, {
  public_ports: [
    { port: 22000, protocol: "tcp", purpose: "Syncthing's sync protocol, so other devices can connect directly." },
    { port: 22000, protocol: "udp", purpose: "The same protocol over QUIC." },
  ],
});

export const WORDPRESS_FULL = full(WORDPRESS, {
  source: { kind: "archive", url: "https://wordpress.org/latest.tar.gz", ref: null, sha256: null, checksum_url: "https://wordpress.org/latest.tar.gz.sha1", files: [] },
  layout: "releases",
  health: { path: "/", expect: "200-399", timeout: null },
  variables: [generatedVariable("WORDPRESS_DB_PASSWORD", { origin: "provisioned", generate: null }), generatedVariable("WORDPRESS_AUTH_KEY")],
  env: [
    { name: "WORDPRESS_DB_PASSWORD", generated: true },
    { name: "WORDPRESS_AUTH_KEY", generated: true },
  ],
});

export const GHOST_FULL = full(GHOST);

/** `GET /api/recipes/{name}` for the recipes that have a full fixture; a 404 for any other. */
export function detailRoutes(): Record<string, RouteHandler> {
  const all = [VAULTWARDEN_FULL, FRESHRSS_FULL, SYNCTHING_FULL, WORDPRESS_FULL, GHOST_FULL];
  return Object.fromEntries(all.map((recipe) => [`GET /api/recipes/${recipe.name}`, () => json(200, recipe)]));
}

/** What the catalogue's pages read besides the recipes themselves. */
export function catalogueRoutes(): Record<string, RouteHandler> {
  return {
    "GET /api/recipes": recipesRoute,
    "GET /api/recipes/categories": categoriesRoute,
    ...detailRoutes(),
    "GET /api/recipes/installed": () => json(200, { items: [] }),
  };
}

export const NOT_FOUND: RouteHandler = (call) => problem(404, "not_found", `No recipe called ${call.path.split("/").at(-1) ?? ""}`);

/** An application installed from a recipe, as the API lists it. */
export function installed(patch: Partial<InstalledRecipe> = {}): InstalledRecipe {
  return {
    domain: "vault.example.com",
    recipe: "vaultwarden",
    kind: "compose",
    installed_version: "1.36.0",
    installed_revision: 1,
    latest_version: "1.37.4",
    latest_revision: 1,
    update_available: true,
    reason: null,
    changes: [{ version: "1.37.4", summary: "Pinned to the 1.37.4 image." }],
    ...patch,
  };
}
