import { createFileRoute } from "@tanstack/react-router";

import { ServerTerminalPage } from "../../../features/terminal/ServerTerminalPage";

export const Route = createFileRoute("/_console/server/terminal")({
  component: ServerTerminalPage,
});
