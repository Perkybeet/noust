import { describe, expect, it } from "vitest";

import { recipeNotesOf } from "../../api/queries/recipes";
import type { Recipe } from "../../api/queries/recipes";
import { editableVariables, generatedVariables, initialRecipeForm, noteParts, recipeBody, recipeProblems, requiredVariables } from "./recipe";
import { WORDPRESS as FIXTURE_WORDPRESS, generatedVariable, recipeVariable } from "./testFixtures";

const WORDPRESS: Recipe = { ...FIXTURE_WORDPRESS, requires: ["MariaDB or MySQL (noust db install mysql)"], notes: [] };

describe("a recipe's form", () => {
  it("asks only for the variables the recipe does not generate", () => {
    expect(generatedVariables(WORDPRESS)).toEqual(["WORDPRESS_DB_PASSWORD", "WORDPRESS_AUTH_KEY"]);
    expect(editableVariables(WORDPRESS)).toEqual(["WP_DEBUG"]);
    expect(initialRecipeForm(WORDPRESS)).toEqual({
      domain: "",
      addressMode: "own",
      temporaryDomain: "",
      ownDomain: "",
      includeWww: false,
      ssl: true,
      env: { WP_DEBUG: "" },
      defaultBackups: true,
      installRequirements: false,
      database: "",
    });
  });

  it("keeps where it answers, and the values both recipes set, when another recipe is chosen", () => {
    const typed = {
      domain: "blog.example.com",
      addressMode: "own" as const,
      temporaryDomain: "",
      ownDomain: "",
      includeWww: true,
      ssl: false,
      env: { WP_DEBUG: "1" },
      defaultBackups: false,
      installRequirements: true,
      database: "",
    };
    const other = { variables: [recipeVariable("WP_DEBUG"), recipeVariable("TZ")] };
    expect(initialRecipeForm(other, typed)).toEqual({
      domain: "blog.example.com",
      addressMode: "own",
      temporaryDomain: "",
      ownDomain: "",
      includeWww: true,
      ssl: false,
      env: { WP_DEBUG: "1", TZ: "" },
      defaultBackups: false,
      installRequirements: false,
      database: "",
    });
  });

  it("starts on a temporary address made of its name where the server has no domain of its own yet", () => {
    const form = initialRecipeForm(WORDPRESS, null, { temporary: "wordpress.93-184-216-34.sslip.io" });
    expect(form).toMatchObject({ domain: "wordpress.93-184-216-34.sslip.io", addressMode: "temporary", ssl: true, includeWww: false });
    // The operator has a domain of their own already: it starts empty, the temporary one a choice away.
    expect(initialRecipeForm(WORDPRESS, null, { temporary: "wordpress.93-184-216-34.sslip.io", ownDomains: true })).toMatchObject({
      domain: "",
      addressMode: "own",
      temporaryDomain: "wordpress.93-184-216-34.sslip.io",
    });
  });

  it("is made again for the next recipe, which has a name of its own", () => {
    const first = initialRecipeForm(WORDPRESS, null, { temporary: "wordpress.93-184-216-34.sslip.io" });
    const next = initialRecipeForm({ variables: [] }, first, { temporary: "ghost.93-184-216-34.sslip.io" });
    expect(next).toMatchObject({ domain: "ghost.93-184-216-34.sslip.io", addressMode: "temporary" });
  });

  it("is sent over HTTPS and without www", () => {
    const form = { ...initialRecipeForm(WORDPRESS, null, { temporary: "wordpress.93-184-216-34.sslip.io" }), includeWww: true };
    expect(recipeBody(WORDPRESS, form)).toMatchObject({ domain: "wordpress.93-184-216-34.sslip.io", ssl: true, include_www: false });
  });

  it("refuses a domain already deployed here", () => {
    const form = { ...initialRecipeForm(WORDPRESS), domain: "shop.example.com" };
    expect(recipeProblems(form, new Set(["shop.example.com"]))["domain"]).toMatch(/already deployed/);
    expect(recipeProblems(form, new Set())).toEqual({});
  });
});

describe("the database engine a recipe offers a choice of", () => {
  const OFFERS = { variables: [recipeVariable("WP_DEBUG")], database_choices: ["mysql", "mariadb"] };

  it("is the recipe's own until the operator chooses, and is sent only when chosen", () => {
    const form = initialRecipeForm(OFFERS);
    expect(form.database).toBe("");
    expect(recipeBody(WORDPRESS, { ...form, domain: "blog.example.com" })).not.toHaveProperty("recipe_database");
    expect(recipeBody(WORDPRESS, { ...form, domain: "blog.example.com", database: "mariadb" }).recipe_database).toBe("mariadb");
  });

  it("is kept when the next recipe offers the same choice, and dropped when it does not", () => {
    const chosen = { ...initialRecipeForm(OFFERS), database: "mariadb" };
    expect(initialRecipeForm(OFFERS, chosen).database).toBe("mariadb");
    expect(initialRecipeForm({ variables: [] }, chosen).database).toBe("");
    expect(initialRecipeForm({ variables: [], database_choices: ["mysql"] }, chosen).database).toBe("");
  });
});

describe("a recipe's required variables (checklist M-19)", () => {
  const WITH_REQUIRED = {
    variables: [
      recipeVariable("ADMIN_EMAIL", { origin: "required", required: true }),
      generatedVariable("SESSION_SECRET", { required: true }),
      recipeVariable("TZ"),
      recipeVariable("WP_DEBUG"),
    ],
  };

  it("are the ones the operator must give: required, and not generated by Noust", () => {
    expect(requiredVariables(WITH_REQUIRED)).toEqual(["ADMIN_EMAIL"]);
    expect(requiredVariables(WORDPRESS)).toEqual([]);
    expect(editableVariables(WITH_REQUIRED)).toEqual(["ADMIN_EMAIL", "TZ", "WP_DEBUG"]);
  });

  it("block the deploy until they have a value, each on its own field", () => {
    const form = { ...initialRecipeForm(WITH_REQUIRED), domain: "blog.example.com" };
    expect(recipeProblems(form, new Set(), requiredVariables(WITH_REQUIRED))).toEqual({
      "env:ADMIN_EMAIL": "The recipe has no default for this one: it needs a value.",
    });
    const filled = { ...form, env: { ...form.env, ADMIN_EMAIL: "  ops@example.com " } };
    expect(recipeProblems(filled, new Set(), requiredVariables(WITH_REQUIRED))).toEqual({});
    // A value of only spaces is no value.
    const blank = { ...form, env: { ...form.env, ADMIN_EMAIL: "   " } };
    expect(Object.keys(recipeProblems(blank, new Set(), ["ADMIN_EMAIL"]))).toEqual(["env:ADMIN_EMAIL"]);
  });

  it("are listed after the domain, in the order the recipe names them", () => {
    const form = initialRecipeForm({ variables: [recipeVariable("B", { required: true }), recipeVariable("A", { required: true })] });
    expect(Object.keys(recipeProblems(form, new Set(), ["B", "A"]))).toEqual(["domain", "env:B", "env:A"]);
    expect(Object.keys(recipeProblems({ ...form, domain: "x.example.com" }, new Set(), ["B", "A"]))).toEqual(["env:B", "env:A"]);
  });

  it("are sent like any other value the operator set", () => {
    const form = { ...initialRecipeForm(WORDPRESS), domain: "blog.example.com", env: { ADMIN_EMAIL: " ops@example.com ", TZ: "", WP_DEBUG: "1" } };
    expect(recipeBody({ name: "wordpress" }, form).env_vars).toEqual({ ADMIN_EMAIL: " ops@example.com ", WP_DEBUG: "1" });
  });
});

describe("a recipe's request", () => {
  it("names the recipe, sends no source, leaves the type on auto, and only the values set", () => {
    const form = { ...initialRecipeForm(WORDPRESS), domain: " Blog.Example.com " };
    expect(recipeBody(WORDPRESS, form)).toEqual({
      domain: "blog.example.com",
      recipe: "wordpress",
      app_type: "auto",
      webserver: "nginx",
      ssl: true,
      include_www: false,
      env_vars: {},
      skip_database: false,
      default_backups: true,
      install_requirements: false,
    });
    expect(recipeBody(WORDPRESS, { ...form, env: { WP_DEBUG: "1" } }).env_vars).toEqual({ WP_DEBUG: "1" });
  });

  it("sends www only where it means something", () => {
    const own = { ...initialRecipeForm(WORDPRESS), includeWww: true, ssl: true, defaultBackups: true };
    expect(recipeBody(WORDPRESS, { ...own, domain: "example.com" }).include_www).toBe(true);
    expect(recipeBody(WORDPRESS, { ...own, domain: "blog.example.com" }).include_www).toBe(false);
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
