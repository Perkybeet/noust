import { createFileRoute } from "@tanstack/react-router";

import { ServerVariables } from "../../../features/variables/ServerVariables";

export const Route = createFileRoute("/_console/settings/variables")({
  component: ServerVariables,
});
