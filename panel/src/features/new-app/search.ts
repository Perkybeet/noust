/**
 * The search params of the new-application wizard, apart from the page so a route's
 * `validateSearch` does not pull in the whole wizard.
 */

/** The ways in the wizard can be opened on, other than its default (GitHub, or a typed source). */
export const WIZARD_STARTS = ["recipe", "import", "adopt"] as const;

export type WizardStart = (typeof WIZARD_STARTS)[number];

export interface NewAppSearch {
  /**
   * Opens the wizard on a way in: the Applications page's "From a recipe" and the command
   * palette's "New application from a recipe" land on the recipes (`/apps/new?start=recipe`),
   * without the click that would choose them.
   */
  start?: WizardStart | undefined;
}

/** `value` as a start the wizard knows, or undefined: a stale or invented link opens the default. */
export function wizardStart(value: unknown): WizardStart | undefined {
  return typeof value === "string" && (WIZARD_STARTS as readonly string[]).includes(value) ? (value as WizardStart) : undefined;
}

/**
 * Reads the search. A start it does not know is set to undefined, not left out: the router
 * keeps the keys a route's validation does not name, so only an explicit undefined replaces one.
 */
export function validateNewAppSearch(search: Record<string, unknown>): NewAppSearch {
  return { start: wizardStart(search["start"]) };
}
