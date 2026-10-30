import { createFileRoute } from "@tanstack/react-router";

import { CentralPermissionsPage } from "../../../features/server/central/CentralPermissionsPage";

/** What the central that manages this server may do on it. */
export const Route = createFileRoute("/_console/server/central")({
  component: CentralPermissionsPage,
});
