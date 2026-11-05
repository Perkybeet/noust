import { createFileRoute } from "@tanstack/react-router";

import { ExportPage } from "../../../../features/app/pages/ExportPage";

/** Export and declaration: the whole application as a file, and the declaration that describes it. */
export const Route = createFileRoute("/_console/apps/$domain/export")({
  component: ExportRoute,
});

function ExportRoute() {
  const { domain } = Route.useParams();
  return <ExportPage domain={domain} />;
}
