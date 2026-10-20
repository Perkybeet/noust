import { LibraryBig } from "lucide-react";

import { area, strip, tab } from "../../app/navCore";

/** The catalog of recipes and the ones installed. The register is app/nav.ts; this is one group of it. */
export const CATALOG_SCREENS = strip(
  area("catalog", "/catalog", "nav.catalog.label", LibraryBig, {
    group: "runs",
    shortcut: ["g", "c"],
    goTo: "nav.catalog.goTo",
    keywords: "nav.catalog.keywords",
    rootLabel: "catalog.tabs.browse",
  }),
  (catalog) => [tab(catalog, "installed", "catalog.tabs.installed")],
);
