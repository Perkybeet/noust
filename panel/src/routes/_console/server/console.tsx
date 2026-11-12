import { createFileRoute } from "@tanstack/react-router";

import { ConsoleAccessSettings } from "../../../features/settings/console/ConsoleAccessSettings";

/** This machine's console on a name of its own over TLS, or private: what publishing it does, and taking it down again. */
export const Route = createFileRoute("/_console/server/console")({
  component: ConsoleAccessSettings,
});
