import { createFileRoute } from "@tanstack/react-router";

import { ProcessesSettings } from "../../../../../features/app/settings/ProcessesSettings";

/** An application's settings, Processes: the copies of the web process and the background processes beside it. */
export const Route = createFileRoute("/_console/apps/$domain/settings/processes")({
  component: ProcessesSettings,
});
