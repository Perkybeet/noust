import { createFileRoute } from "@tanstack/react-router";

import { AccessPage } from "../../../features/server/security/SecurityPages";

/** Security > access and sessions: who has entered the machine, by SSH or through the console's terminal, and the terminal's recordings. */
export const Route = createFileRoute("/_console/security/access")({
  component: AccessPage,
});
