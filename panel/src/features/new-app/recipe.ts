/**
 * Starting from a recipe: the form (where it answers, and the values the operator sets over
 * the recipe's own), what is wrong with it, and the `POST /api/apps` it becomes. Pure, like
 * wizard.ts.
 */

import type { Recipe } from "../../api/queries/recipes";
import { translate } from "../../i18n";
import { getLocale } from "../../app/locale";
import { normalizeDomain } from "../domains/names";
import { canIncludeWww, domainError } from "./wizard";
import type { CreateAppBody } from "./wizard";

/**
 * A variable of a recipe. `required` says the recipe has no value for it and cannot run
 * without the operator's (an administrator's address, a licence key): the deploy waits for one.
 * The flag is read as optional here because the API's schema carries it from the release that
 * adds required variables to recipes; a recipe without it is exactly as before.
 */
export type RecipeVariable = Recipe["env"][number] & { required?: boolean | null };

/** What the form reads of a recipe: its variables. */
export interface WithVariables {
  env: readonly RecipeVariable[];
}

export interface RecipeForm {
  domain: string;
  includeWww: boolean;
  ssl: boolean;
  /** Values over the recipe's own, by variable; empty keeps the recipe's. Generated ones are not here. */
  env: Record<string, string>;
}

/** The variables the operator may set: every one the recipe does not generate. */
export function editableVariables(recipe: WithVariables): string[] {
  return recipe.env.filter((variable) => !variable.generated).map((variable) => variable.name);
}

/** The variables Noust generates when it deploys: secrets, database credentials, the address. */
export function generatedVariables(recipe: WithVariables): string[] {
  return recipe.env.filter((variable) => variable.generated).map((variable) => variable.name);
}

/**
 * The variables the deploy cannot go ahead without: required by the recipe, and not ones Noust
 * generates (a generated variable has its value already, whatever the recipe says).
 */
export function requiredVariables(recipe: WithVariables): string[] {
  return recipe.env.filter((variable) => variable.required === true && !variable.generated).map((variable) => variable.name);
}

/**
 * The form a recipe starts from. Choosing another recipe keeps where it answers (domain, www,
 * HTTPS) and the values of the variables both recipes set.
 */
export function initialRecipeForm(recipe: WithVariables, previous?: RecipeForm | null): RecipeForm {
  const env: Record<string, string> = {};
  for (const name of editableVariables(recipe)) env[name] = previous?.env[name] ?? "";
  return {
    domain: previous?.domain ?? "",
    includeWww: previous?.includeWww ?? false,
    ssl: previous?.ssl ?? true,
    env,
  };
}

/** The field name of a recipe variable in the form's errors. */
export function recipeVariableField(name: string): string {
  return `env:${name}`;
}

/**
 * What is wrong with a recipe's form: the domain, then each required variable that has no value,
 * each on its own field so the step can point at it.
 */
export function recipeProblems(form: RecipeForm, deployed: ReadonlySet<string>, required: readonly string[] = []): Record<string, string> {
  const errors: Record<string, string> = {};
  const domain = domainError(form.domain, deployed);
  if (domain !== null) errors["domain"] = domain;
  for (const name of required) {
    if ((form.env[name] ?? "").trim() === "") errors[recipeVariableField(name)] = translate(getLocale(), "newApp.recipes.envRequired");
  }
  return errors;
}

/**
 * The request `POST /api/apps` takes for a recipe: no source and the type left on auto, both
 * come with the recipe (the server refuses either given with one), and only the values the
 * operator actually set, applied over the recipe's.
 */
export function recipeBody(recipe: Pick<Recipe, "name">, form: RecipeForm): CreateAppBody {
  const env: Record<string, string> = {};
  for (const [name, value] of Object.entries(form.env)) {
    if (value.trim() !== "") env[name] = value;
  }
  return {
    domain: normalizeDomain(form.domain),
    recipe: recipe.name,
    app_type: "auto",
    webserver: "nginx",
    ssl: form.ssl,
    include_www: form.includeWww && canIncludeWww(form.domain),
    env_vars: env,
    skip_database: false,
  };
}

export type NotePart = { kind: "text"; text: string } | { kind: "link"; href: string };

/** An http(s) URL inside a sentence, up to the first space; trailing punctuation is the sentence's. */
const URL_IN_TEXT = /https?:\/\/[^\s<>"'`]+/g;

/**
 * A recipe's note as text and links: every http(s) URL in it becomes a link, everything else
 * stays text. The note is the server's; nothing in it is ever read as markup.
 */
export function noteParts(note: string): NotePart[] {
  const parts: NotePart[] = [];
  let last = 0;
  for (const match of note.matchAll(URL_IN_TEXT)) {
    let href = match[0];
    // "Open https://x.example.com/setup." ends the sentence, not the address.
    while (/[.,;:!?)\]]$/.test(href)) href = href.slice(0, -1);
    if (href.length <= "https://".length) continue;
    if (match.index > last) parts.push({ kind: "text", text: note.slice(last, match.index) });
    parts.push({ kind: "link", href });
    last = match.index + href.length;
  }
  if (last < note.length) parts.push({ kind: "text", text: note.slice(last) });
  return parts;
}
