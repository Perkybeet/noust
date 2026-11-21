import { createFileRoute, redirect } from "@tanstack/react-router";

import { PageError } from "../app/ErrorBoundary";
import { nodeFromSearch } from "../app/nodeRoute";
import { FramedRoutePending } from "../app/RoutePending";
import { Shell } from "../app/Shell";
import { SessionGate } from "../features/auth/SessionGate";
import { requireSession } from "../features/auth/requireSession";
import { hubStartsHere, readServerCount, startsOnFleet } from "../features/central/central";
import { CentralGate } from "../features/central/CentralGate";
import { lastServer } from "../nodes/lastServer";
import { NodeScope } from "../nodes/useNode";

/** Everything behind sign-in: the shell around every page of the console. */
export const Route = createFileRoute("/_console")({
  beforeLoad: async ({ context, location }) => {
    const session = await requireSession(context.queryClient, location.href);
    // A hub that deploys nothing has no server of its own to begin on: with nothing chosen yet it starts on All servers. One that
    // deploys on its own machine has a server, itself, and starts there while it has no other: the list is read only for that question.
    const node = nodeFromSearch(location.search);
    const chosen = lastServer() !== null;
    const servers = hubStartsHere(session, location.pathname, node, chosen) ? await readServerCount(context.queryClient) : null;
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    if (startsOnFleet(session, location.pathname, node, chosen, servers)) throw redirect({ to: "/fleet", replace: true });
    return session;
  },
  component: ConsoleLayout,
  // Before the shell exists (the first load, the session being read) there is no page around
  // the placeholder: it brings the page's margins.
  pendingComponent: FramedRoutePending,
  // Drawn without the shell (an error in what a route loads), so it has its own ground, the page's, across the window:
  // what is behind a dialog (the report of a problem opens from here) is then the page under the veil, as it is with the
  // shell, and not the body's own tone, which a contrast would be measured against.
  errorComponent: ({ error }) => (
    <main className="min-h-dvh bg-bg">
      <div className="mx-auto max-w-3xl px-6 py-16">
        <PageError error={error} />
      </div>
    </main>
  ),
});

function ConsoleLayout() {
  return (
    <SessionGate>
      <CentralGate>
        <NodeScope>
          <Shell />
        </NodeScope>
      </CentralGate>
    </SessionGate>
  );
}
