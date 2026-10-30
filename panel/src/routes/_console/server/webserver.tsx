import { createFileRoute } from "@tanstack/react-router";

import { WebServerPage } from "../../../features/server/webserver/WebServerPage";

/** The web server in front of the applications, and moving them to the other one. */
export const Route = createFileRoute("/_console/server/webserver")({
  component: WebServerPage,
});
