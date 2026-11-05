import { createFileRoute } from "@tanstack/react-router";

import { ConfigPage } from "../../../../features/app/pages/ConfigPage";

/** Code and build: where the code comes from, how it is built and the container it runs in. */
export const Route = createFileRoute("/_console/apps/$domain/code")({
  component: CodeRoute,
});

function CodeRoute() {
  const { domain } = Route.useParams();
  return <ConfigPage page="code" domain={domain} />;
}
