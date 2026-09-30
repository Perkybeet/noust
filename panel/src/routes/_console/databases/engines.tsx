import { createFileRoute } from "@tanstack/react-router";

import { EnginesPage } from "../../../features/databases/EnginesPage";

/** The database engines: installed or not, their state, versions and controls. */
export const Route = createFileRoute("/_console/databases/engines")({
  component: EnginesPage,
});
