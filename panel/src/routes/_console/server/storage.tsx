import { createFileRoute } from "@tanstack/react-router";

import { StorageTab } from "../../../features/server/storage/StorageTab";

export const Route = createFileRoute("/_console/server/storage")({
  component: StorageTab,
});
