import { createFileRoute } from "@tanstack/react-router";

import { DeletePage } from "../../../../features/app/pages/DeletePage";

/** Delete: what is deleted, alone at the end of the column. */
export const Route = createFileRoute("/_console/apps/$domain/delete")({
  component: DeleteRoute,
});

function DeleteRoute() {
  const { domain } = Route.useParams();
  return <DeletePage domain={domain} />;
}
