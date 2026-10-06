import { createFileRoute } from "@tanstack/react-router";

import { NewAppWizard } from "../../../features/new-app/NewAppWizard";
import { validateNewAppSearch } from "../../../features/new-app/search";

/** The new-app wizard: source, review, deploy. `?start=recipe` opens it on the recipes. */
export const Route = createFileRoute("/_console/apps/new")({
  validateSearch: validateNewAppSearch,
  component: NewAppWizard,
});
