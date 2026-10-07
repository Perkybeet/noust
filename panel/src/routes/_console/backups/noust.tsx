import { createFileRoute } from "@tanstack/react-router";

import { SelfBackupPage } from "../../../features/backups/noust/SelfBackupPage";

/** Noust's own state, in encrypted bundles: its schedule, its bundles, and how to restore one. */
export const Route = createFileRoute("/_console/backups/noust")({
  component: SelfBackupPage,
});
