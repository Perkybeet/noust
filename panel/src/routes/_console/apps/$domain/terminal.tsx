import { createFileRoute } from "@tanstack/react-router";

import { AppTerminalPage } from "../../../../features/terminal/AppTerminalPage";

export const Route = createFileRoute("/_console/apps/$domain/terminal")({
  component: AppTerminal,
});

function AppTerminal() {
  const { domain } = Route.useParams();
  // Keyed: another application's terminal is another session, from an empty screen.
  return <AppTerminalPage key={domain} domain={domain} />;
}
