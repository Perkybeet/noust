/**
 * The search params of the new-application wizard, apart from the page so a route's
 * `validateSearch` does not pull in the whole wizard.
 */

/** The ways in the wizard can be opened on, other than its default (GitHub, or a typed source). */
export const WIZARD_STARTS = ["recipe", "import", "adopt"] as const;

export type WizardStart = (typeof WIZARD_STARTS)[number];

import { slugParam } from "../teams/search";

export interface NewAppSearch {
  /**
   * Opens the wizard on a way in: the Applications page's "From a recipe" and the command
   * palette's "New application from a recipe" land on the recipes (`/apps/new?start=recipe`),
   * without the click that would choose them.
   */
  start?: WizardStart | undefined;
  /**
   * With `start=recipe`: the recipe already chosen (`/apps/new?start=recipe&recipe=wordpress`,
   * from the catalog), so the wizard opens on its install form instead of on the recipes.
   */
  recipe?: string | undefined;
  /**
   * With a recipe: `now` deploys it as soon as it is open and has nothing left to ask (the
   * Overview's "Try a demo"). The wizard spends it, replacing the address, so a reload or a Back
   * never deploys twice.
   */
  deploy?: "now" | undefined;
  /**
   * `1` comes back from connecting GitHub (`/apps/new?resume=1`): the wizard opens where the
   * operator left it, from the draft the tab kept. The wizard spends it like `deploy`.
   */
  resume?: 1 | undefined;
  /**
   * Where it goes (a project's page starts it on that project): the team, project and environment
   * by their short names. Only asked about where there is more than one place to choose.
   */
  team?: string | undefined;
  project?: string | undefined;
  environment?: string | undefined;
}

/** `value` as a start the wizard knows, or undefined: a stale or invented link opens the default. */
export function wizardStart(value: unknown): WizardStart | undefined {
  return typeof value === "string" && (WIZARD_STARTS as readonly string[]).includes(value) ? (value as WizardStart) : undefined;
}

/** What a recipe's name looks like (the backend's own pattern): anything else is no recipe. */
const RECIPE_NAME = /^[a-z0-9][a-z0-9-]{0,39}$/;

/** `value` as the name of a recipe to open, or undefined for anything that cannot be one. */
export function wizardRecipe(value: unknown): string | undefined {
  return typeof value === "string" && RECIPE_NAME.test(value) ? value : undefined;
}

/**
 * Reads the search. A start it does not know is set to undefined, not left out: the router
 * keeps the keys a route's validation does not name, so only an explicit undefined replaces one.
 * The same goes for the team, project and environment. A recipe is the exception: it is only
 * named when the link carries one.
 */
export function validateNewAppSearch(search: Record<string, unknown>): NewAppSearch {
  return {
    start: wizardStart(search["start"]),
    team: slugParam(search["team"]),
    project: slugParam(search["project"]),
    environment: slugParam(search["environment"]),
    ...("recipe" in search ? { recipe: wizardRecipe(search["recipe"]) } : {}),
    // Like the recipe: named only when the link carries it, so clearing it is an explicit undefined.
    ...("deploy" in search ? { deploy: search["deploy"] === "now" ? ("now" as const) : undefined } : {}),
    ...("resume" in search ? { resume: search["resume"] === 1 || search["resume"] === "1" ? (1 as const) : undefined } : {}),
  };
}
