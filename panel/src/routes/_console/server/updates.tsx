import { createFileRoute } from "@tanstack/react-router";

import { SystemTab } from "../../../features/server/system/SystemTab";

/** What is waiting to be updated, and Noust's own version: the system's updates, which the 4.1 System section opened on. */
export const Route = createFileRoute("/_console/server/updates")({
  component: UpdatesRoute,
});

function UpdatesRoute() {
  return <SystemTab view="updates" />;
}
