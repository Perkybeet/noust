import { createFileRoute } from "@tanstack/react-router";

import { TeamOverview } from "../../../../features/teams/TeamOverview";

export const Route = createFileRoute("/_console/teams/$team/")({
  component: TeamOverview,
});
