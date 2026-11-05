import { createFileRoute } from "@tanstack/react-router";

import { ConfigPage } from "../../../../features/app/pages/ConfigPage";

/** Access and protection: who may reach the application, how much it may ask and how it is protected. */
export const Route = createFileRoute("/_console/apps/$domain/access")({
  component: AccessRoute,
});

function AccessRoute() {
  const { domain } = Route.useParams();
  return <ConfigPage page="access" domain={domain} />;
}
