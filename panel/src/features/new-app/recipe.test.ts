import { describe, expect, it } from "vitest";

import { recipeNotesOf } from "../../api/queries/recipes";
import type { Recipe } from "../../api/queries/recipes";
import { editableVariables, generatedVariables, initialRecipeForm, noteParts, recipeBody, recipeProblems } from "./recipe";

const WORDPRESS: Recipe = {
  name: "wordpress",
  title: "WordPress",
  description: "The website and blogging platform, on PHP-FPM with a MariaDB or MySQL database.",
  homepage: "https://wordpress.org",
  available: true,
  app_type: "php-fpm",
  database: "mysql",
  requires: ["MariaDB or MySQL (wasm db install mysql)"],
  layout: "releases",
  port: null,
  env: [
    { name: "WORDPRESS_DB_PASSWORD", generated: true },
    { name: "WORDPRESS_AUTH_KEY", generated: true },
    { name: "WP_DEBUG", generated: false },
  ],
  persistent_paths: ["wp-content"],
  health: { path: "/", expect: "200-399" },
  notes: [],
  source: { kind: "archive", url: "https://wordpress.org/latest.tar.gz", checksum_url: "https://wordpress.org/latest.tar.gz.sha1", files: [] },
};

describe("a recipe's form", () => {
  it("asks only for the variables the recipe does not generate", () => {
    expect(generatedVariables(WORDPRESS)).toEqual(["WORDPRESS_DB_PASSWORD", "WORDPRESS_AUTH_KEY"]);
    expect(editableVariables(WORDPRESS)).toEqual(["WP_DEBUG"]);
    expect(initialRecipeForm(WORDPRESS)).toEqual({ domain: "", includeWww: false, ssl: true, env: { WP_DEBUG: "" } });
  });

  it("keeps where it answers, and the values both recipes set, when another recipe is chosen", () => {
    const typed = { domain: "blog.example.com", includeWww: true, ssl: false, env: { WP_DEBUG: "1" } };
    const other = { env: [{ name: "WP_DEBUG", generated: false }, { name: "TZ", generated: false }] };
    expect(initialRecipeForm(other, typed)).toEqual({ domain: "blog.example.com", includeWww: true, ssl: false, env: { WP_DEBUG: "1", TZ: "" } });
  });

  it("refuses a domain already deployed here", () => {
    const form = { ...initialRecipeForm(WORDPRESS), domain: "shop.example.com" };
    expect(recipeProblems(form, new Set(["shop.example.com"]))["domain"]).toMatch(/already deployed/);
    expect(recipeProblems(form, new Set())).toEqual({});
  });
});

describe("a recipe's request", () => {
  it("names the recipe, sends no source, leaves the type on auto, and only the values set", () => {
    const form = { domain: " Blog.Example.com ", includeWww: false, ssl: true, env: { WP_DEBUG: "" } };
    expect(recipeBody(WORDPRESS, form)).toEqual({
      domain: "blog.example.com",
      recipe: "wordpress",
      app_type: "auto",
      webserver: "nginx",
      ssl: true,
      include_www: false,
      env_vars: {},
      skip_database: false,
    });
    expect(recipeBody(WORDPRESS, { ...form, env: { WP_DEBUG: "1" } }).env_vars).toEqual({ WP_DEBUG: "1" });
  });

  it("sends www only where it means something", () => {
    expect(recipeBody(WORDPRESS, { domain: "example.com", includeWww: true, ssl: true, env: {} }).include_www).toBe(true);
    expect(recipeBody(WORDPRESS, { domain: "blog.example.com", includeWww: true, ssl: true, env: {} }).include_www).toBe(false);
  });
});

describe("a recipe's notes", () => {
  it("are read off the job's result, and nothing else is", () => {
    expect(recipeNotesOf({ notes: ["Open https://blog.example.com/wp-admin/install.php now.", "", 7] })).toEqual([
      "Open https://blog.example.com/wp-admin/install.php now.",
    ]);
    expect(recipeNotesOf({ deployment_id: 3 })).toBeNull();
    expect(recipeNotesOf(null)).toBeNull();
  });

  it("turn every address into a link, and leave the sentence's punctuation out of it", () => {
    expect(noteParts("Open https://blog.example.com/wp-admin/install.php now to choose the title.")).toEqual([
      { kind: "text", text: "Open " },
      { kind: "link", href: "https://blog.example.com/wp-admin/install.php" },
      { kind: "text", text: " now to choose the title." },
    ]);
    expect(noteParts("See https://example.com/docs.")).toEqual([
      { kind: "text", text: "See " },
      { kind: "link", href: "https://example.com/docs" },
      { kind: "text", text: "." },
    ]);
    expect(noteParts("Plugins live in shared/wp-content.")).toEqual([{ kind: "text", text: "Plugins live in shared/wp-content." }]);
    // Markup is text: nothing in a note is ever read as HTML.
    expect(noteParts("<b>bold</b> javascript:alert(1)")).toEqual([{ kind: "text", text: "<b>bold</b> javascript:alert(1)" }]);
  });
});
