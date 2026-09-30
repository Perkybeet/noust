import { createFileRoute } from "@tanstack/react-router";

import { DeleteSettings } from "../../../../../features/app/settings/DeleteSettings";

/** An application's settings, Delete: the last subsection, set apart. */
export const Route = createFileRoute("/_console/apps/$domain/settings/delete")({
  component: DeleteSettings,
});
