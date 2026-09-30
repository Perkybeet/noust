import { createFileRoute } from "@tanstack/react-router";

import { AppDatabaseTab } from "../../../../features/app/database/AppDatabaseTab";

export const Route = createFileRoute("/_console/apps/$domain/database")({
  component: AppDatabaseRoute,
});

function AppDatabaseRoute() {
  const { domain } = Route.useParams();
  return <AppDatabaseTab domain={domain} />;
}
