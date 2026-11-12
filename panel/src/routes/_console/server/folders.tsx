import { createFileRoute } from "@tanstack/react-router";

import { GeneralSettings } from "../../../features/settings/GeneralSettings";

/** Where this machine keeps its applications and its backups, the web server new sites are served with, and how much it builds at once: what was General about the machine. */
export const Route = createFileRoute("/_console/server/folders")({
  component: FoldersRoute,
});

function FoldersRoute() {
  return <GeneralSettings part="folders" />;
}
