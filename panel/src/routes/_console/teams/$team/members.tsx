import { createFileRoute } from "@tanstack/react-router";

import { TeamMembers } from "../../../../features/teams/TeamMembers";

export const Route = createFileRoute("/_console/teams/$team/members")({
  component: TeamMembers,
});
