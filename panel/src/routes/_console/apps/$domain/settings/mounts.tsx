import { createFileRoute } from "@tanstack/react-router";

import { MountsSettings } from "../../../../../features/app/settings/MountsSettings";

/** An application's settings, Mounts: the files kept for it and the folders of the server bound into it. */
export const Route = createFileRoute("/_console/apps/$domain/settings/mounts")({
  component: MountsSettings,
});
