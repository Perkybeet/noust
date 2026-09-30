import { createFileRoute } from "@tanstack/react-router";

import { DestinationsPage } from "../../../features/backups/DestinationsPage";

/** The other places each backup can be copied to. */
export const Route = createFileRoute("/_console/backups/destinations")({
  component: DestinationsPage,
});
