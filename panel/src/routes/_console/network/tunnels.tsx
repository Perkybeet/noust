import { createFileRoute } from "@tanstack/react-router";

import { TunnelsPage } from "../../../features/network/TunnelsPage";

/** Cloudflare Tunnels: an application's way in with no public address and no open port (Domains' in 4.1). */
export const Route = createFileRoute("/_console/network/tunnels")({
  component: TunnelsPage,
});
