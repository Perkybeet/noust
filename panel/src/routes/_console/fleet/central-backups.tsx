import { createFileRoute } from "@tanstack/react-router";

import { CentralCopiesPage } from "../../../features/backups/central/CentralCopiesPage";

/** Backups > Copies on the central: what the central took from its servers through its tunnels, and how it is going with each. */
export const Route = createFileRoute("/_console/fleet/central-backups")({
  component: CentralCopiesPage,
});
