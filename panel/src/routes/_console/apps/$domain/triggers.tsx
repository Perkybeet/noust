import { createFileRoute } from "@tanstack/react-router";

import { ConfigPage } from "../../../../features/app/pages/ConfigPage";

/** Deploys and triggers: how a version reaches production, what deploys it and what runs around it. */
export const Route = createFileRoute("/_console/apps/$domain/triggers")({
  component: TriggersRoute,
});

function TriggersRoute() {
  const { domain } = Route.useParams();
  return <ConfigPage page="triggers" domain={domain} />;
}
