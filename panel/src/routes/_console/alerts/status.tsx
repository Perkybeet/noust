import { createFileRoute } from "@tanstack/react-router";

import { StatusPageSettings } from "../../../features/settings/probes/StatusPageSettings";

/** Alerts > Status page: the probes marked public as one static page, and where it is published. */
export const Route = createFileRoute("/_console/alerts/status")({
  component: StatusPageSettings,
});
