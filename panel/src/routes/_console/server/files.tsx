import { createFileRoute } from "@tanstack/react-router";

import { StorageTab } from "../../../features/server/storage/StorageTab";

/** Files: the disks, what takes their space, clean-ups, swap, and where each application keeps what must survive a deploy. */
export const Route = createFileRoute("/_console/server/files")({
  component: StorageTab,
});
