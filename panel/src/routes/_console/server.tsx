import { createFileRoute } from "@tanstack/react-router";

import { ServerLayout } from "../../features/server/ServerLayout";

/** The machine this console runs on (or the node selected): its header and seven tabs, each a URL. */
export const Route = createFileRoute("/_console/server")({
  component: ServerLayout,
});
