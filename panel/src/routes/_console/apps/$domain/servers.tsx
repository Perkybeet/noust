import { createFileRoute } from "@tanstack/react-router";

import { ServersPage } from "../../../../features/app/pages/ServersPage";

/** Several servers: whether the application could run on more than one, and what stops it. */
export const Route = createFileRoute("/_console/apps/$domain/servers")({
  component: ServersRoute,
});

function ServersRoute() {
  const { domain } = Route.useParams();
  return <ServersPage domain={domain} />;
}
