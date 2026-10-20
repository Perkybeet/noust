import { createFileRoute } from "@tanstack/react-router";

import { TeamNotifications } from "../../../../features/teams/TeamNotifications";

export const Route = createFileRoute("/_console/teams/$team/notifications")({
  component: TeamNotifications,
});
