import { createFileRoute } from "@tanstack/react-router";

import { TeamLayout } from "../../../features/teams/TeamLayout";

/** One team: its header and the tabs that divide it. Each tab is a URL. */
export const Route = createFileRoute("/_console/teams/$team")({
  component: TeamRoute,
});

function TeamRoute() {
  const { team } = Route.useParams();
  // Keyed: another team is another page, with none of this one's dialogs.
  return <TeamLayout key={team} team={team} />;
}
