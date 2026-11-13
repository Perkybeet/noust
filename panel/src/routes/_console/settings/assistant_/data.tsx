import { createFileRoute } from "@tanstack/react-router";

import { AssistantDataPage } from "../../../../features/settings/assistant/AssistantDataPage";

/** Settings > AI assistant > what leaves the server and the limits. */
export const Route = createFileRoute("/_console/settings/assistant_/data")({
  component: AssistantDataPage,
});
