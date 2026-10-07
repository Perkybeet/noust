import { createFileRoute } from "@tanstack/react-router";

import { InstalledRecipesPage } from "../../../features/catalog/InstalledRecipesPage";

/** What was installed from a recipe, which version it runs, and whether the catalogue is ahead. */
export const Route = createFileRoute("/_console/catalog/installed")({
  component: InstalledRecipesPage,
});
