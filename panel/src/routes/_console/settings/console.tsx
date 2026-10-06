import { createFileRoute } from "@tanstack/react-router";

import { ConsoleAccessSettings } from "../../../features/settings/console/ConsoleAccessSettings";

/** Settings > Console access: this server's console on a name of its own over TLS, or private. */
export const Route = createFileRoute("/_console/settings/console")({
  component: ConsoleAccessSettings,
});
