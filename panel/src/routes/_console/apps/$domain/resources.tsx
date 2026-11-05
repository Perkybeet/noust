import { createFileRoute } from "@tanstack/react-router";

import { ConfigPage } from "../../../../features/app/pages/ConfigPage";

/** Resources and running: what the application may use of the server and how it runs. */
export const Route = createFileRoute("/_console/apps/$domain/resources")({
  component: ResourcesRoute,
});

function ResourcesRoute() {
  const { domain } = Route.useParams();
  return <ConfigPage page="resources" domain={domain} />;
}
