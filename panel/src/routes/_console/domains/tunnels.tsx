import { createFileRoute } from "@tanstack/react-router";

import { TunnelsPage } from "../../../features/domains/TunnelsPage";

/** Cloudflare Tunnels: an application's way in with no public address and no open port. */
export const Route = createFileRoute("/_console/domains/tunnels")({
  component: TunnelsPage,
});
