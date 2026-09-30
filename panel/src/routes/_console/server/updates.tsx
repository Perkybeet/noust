import { createFileRoute } from "@tanstack/react-router";

import { UpdatesTab } from "../../../features/server/updates/UpdatesTab";

export const Route = createFileRoute("/_console/server/updates")({
  component: UpdatesTab,
});
