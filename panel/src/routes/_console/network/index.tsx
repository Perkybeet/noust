import { createFileRoute } from "@tanstack/react-router";

import { StreamsPage } from "../../../features/domains/StreamsPage";

/** The TCP and UDP ports published through nginx for services that are not HTTP: the first section of Network (it was Domains' in 4.1). */
export const Route = createFileRoute("/_console/network/")({
  component: StreamsPage,
});
