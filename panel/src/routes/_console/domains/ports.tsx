import { createFileRoute } from "@tanstack/react-router";

import { StreamsPage } from "../../../features/domains/StreamsPage";

/** The TCP and UDP ports published through nginx for services that are not HTTP. */
export const Route = createFileRoute("/_console/domains/ports")({
  component: StreamsPage,
});
