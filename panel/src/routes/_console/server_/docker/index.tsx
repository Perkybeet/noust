import { createFileRoute } from "@tanstack/react-router";

import { SummaryPage } from "../../../../features/docker/SummaryPage";

/** Docker on this machine, in one screen: what asks for the operator first, how big it is and what runs, with whose each thing is. */
export const Route = createFileRoute("/_console/server_/docker/")({
  component: SummaryPage,
});
