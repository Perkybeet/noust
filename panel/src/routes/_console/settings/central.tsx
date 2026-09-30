import { createFileRoute } from "@tanstack/react-router";

import { CentralSettings } from "../../../features/central/CentralSettings";

/** Settings > Central: this central's name, role and the seal on the secrets that reach its servers. */
export const Route = createFileRoute("/_console/settings/central")({
  component: CentralSettings,
});
