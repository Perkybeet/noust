import { createFileRoute } from "@tanstack/react-router";

import { TeamVariables } from "../../../../features/variables/TeamVariables";

export const Route = createFileRoute("/_console/teams/$team/variables")({
  component: TeamVariables,
});
