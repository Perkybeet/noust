import { area, strip, tab } from "../../app/navCore";
import type { Strip } from "../../app/navCore";

const apps = area("apps", "/apps", "nav.apps.label", { group: "runs", shortcut: ["g", "a"], goTo: "nav.apps.goTo", keywords: "nav.apps.keywords" });

// The recipes are a view of what Applications makes (D-2), not a destination of the bar: they keep
// the addresses they always had, `/catalog`, and are one click from the list that makes applications.
const recipes = tab(apps, "recipes", "nav.screens.recipes.label", {
  to: "/catalog",
  exact: true,
  keywords: "nav.catalog.keywords",
  command: "nav.screens.recipes.command",
});
const installed = tab(apps, "installed", "nav.screens.installedRecipes.label", {
  to: "/catalog/installed",
  exact: true,
  keywords: "nav.screens.installedRecipes.keywords",
  command: "nav.screens.installedRecipes.command",
});

/**
 * The three views of Applications the list and the catalogue draw as tabs. Kept apart from the
 * register's group (nav/apps.ts, which also names the wizard) so that a page drawing the strip does
 * not ask for the texts of the screens it does not draw.
 */
export const APPS_TABS: Strip = strip(apps, () => [recipes, installed]);
