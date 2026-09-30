import { createFileRoute } from "@tanstack/react-router";

import { SchedulesPage } from "../../../features/backups/SchedulesPage";

/** What backs each application up by itself. */
export const Route = createFileRoute("/_console/backups/schedules")({
  component: SchedulesPage,
});
