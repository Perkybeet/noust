import { Boxes } from "lucide-react";

import { area, hidden, strip, tab } from "../../app/navCore";
import type { Strip } from "../../app/navCore";

const apps = area("apps", "/apps", "nav.apps.label", Boxes, { group: "runs", shortcut: ["g", "a"], goTo: "nav.apps.goTo", keywords: "nav.apps.keywords" });

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
 * Applications, the wizard that makes one, and the recipes it can start from. The register is
 * app/nav.ts; this is one group of it.
 */
export const APPS_SCREENS: Strip = strip(apps, () => [hidden("apps.new", "/apps/new", "newApp.page.title", "apps", { parent: "apps" }), recipes, installed]);

/** The two recipe views as the catalog's own frame draws them, until the list of applications draws them for it. */
export const RECIPE_SCREENS: Strip = [recipes, installed];
