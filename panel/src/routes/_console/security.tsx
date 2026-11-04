import { createFileRoute } from "@tanstack/react-router";

import { MachineProviders } from "../../features/server/MachinePage";

/** Security: the machine's hardening checks, SSH and the blocks, each a section of its column. */
export const Route = createFileRoute("/_console/security")({
  component: MachineProviders,
});
