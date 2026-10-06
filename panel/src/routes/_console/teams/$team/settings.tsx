import { createFileRoute } from "@tanstack/react-router";

import { TeamSettings } from "../../../../features/teams/TeamSettings";

export const Route = createFileRoute("/_console/teams/$team/settings")({
  component: TeamSettings,
});
