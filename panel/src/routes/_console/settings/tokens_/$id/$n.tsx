import { createFileRoute } from "@tanstack/react-router";

import { InterventionPage } from "../../../../../features/settings/credentials/InterventionPage";

/** Settings > API tokens > one credential > one intervention: what it did, in order, and leaving it as it was. */
export const Route = createFileRoute("/_console/settings/tokens_/$id/$n")({
  component: InterventionRoute,
});

function InterventionRoute() {
  const { id, n } = Route.useParams();
  return <InterventionPage id={Number(id)} n={Number(n)} />;
}
