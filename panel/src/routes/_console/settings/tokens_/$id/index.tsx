import { createFileRoute } from "@tanstack/react-router";

import { CredentialActivityPage } from "../../../../../features/settings/credentials/CredentialActivityPage";

/** Settings > API tokens > one credential: what it did, as interventions. */
export const Route = createFileRoute("/_console/settings/tokens_/$id/")({
  component: CredentialRoute,
});

function CredentialRoute() {
  const { id } = Route.useParams();
  return <CredentialActivityPage id={Number(id)} />;
}
