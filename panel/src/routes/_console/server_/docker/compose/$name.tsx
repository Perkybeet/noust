import { createFileRoute } from "@tanstack/react-router";

import { ComposePage } from "../../../../../features/docker/ComposePage";

/** The Compose document of one stack, judged before it runs and kept as versions: opened from the stack's page. */
export const Route = createFileRoute("/_console/server_/docker/compose/$name")({
  component: ComposeRoute,
});

function ComposeRoute() {
  const { name } = Route.useParams();
  return <ComposePage key={name} name={name} />;
}
