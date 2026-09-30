import { createFileRoute } from "@tanstack/react-router";

import { ExportSettings } from "../../../../../features/app/settings/ExportSection";

/** An application's settings, Export: everything that defines it, as a file. */
export const Route = createFileRoute("/_console/apps/$domain/settings/export")({
  component: ExportSettings,
});
