import { createFileRoute } from "@tanstack/react-router";

import { DiskSettings } from "../../../../../features/app/settings/DiskSettings";

/** An application's settings, Disk: what it takes and the limit its deploys are held to. */
export const Route = createFileRoute("/_console/apps/$domain/settings/disk")({
  component: DiskSettings,
});
