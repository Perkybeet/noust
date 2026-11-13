import { area, strip, tab } from "../../app/navCore";
import type { Strip } from "../../app/navCore";

/**
 * The catalog: the recipes an application can start from, and the ones installed. An area of its
 * own (it was the second and third view of Applications in 4.0), at the addresses it always had. The
 * register is app/nav.ts; this is one group of it.
 */
export const CATALOG_TABS: Strip = strip(
  area("catalog", "/catalog", "nav.catalog.label", {
    scope: "both",
    group: "runs",
    shortcut: ["g", "t"],
    goTo: "nav.catalog.goTo",
    keywords: "nav.catalog.keywords",
    rootLabel: "nav.screens.recipes.label",
    command: "nav.screens.recipes.command",
  }),
  (catalog) => [
    tab(catalog, "installed", "nav.screens.installedRecipes.label", {
      exact: true,
      feature: "recipes",
      keywords: "nav.screens.installedRecipes.keywords",
      command: "nav.screens.installedRecipes.command",
    }),
  ],
);

export const CATALOG_SCREENS: Strip = CATALOG_TABS;
