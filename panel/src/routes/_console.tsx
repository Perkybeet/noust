import { createFileRoute, redirect } from "@tanstack/react-router";

import { PageError } from "../app/ErrorBoundary";
import { nodeFromSearch } from "../app/nodeRoute";
import { FramedRoutePending } from "../app/RoutePending";
import { Shell } from "../app/Shell";
import { SessionGate, requireSession } from "../features/auth/SessionGate";
import { hubRedirect } from "../features/central/central";
import { CentralGate } from "../features/central/CentralGate";
import { NodeScope } from "../nodes/useNode";

/** Everything behind sign-in: the shell around every page of the console. */
export const Route = createFileRoute("/_console")({
  beforeLoad: async ({ context, location }) => {
    const session = await requireSession(context.queryClient, location.href);
    // A hub deploys nothing: its overview and its own deployment pages open the fleet instead.
    const hub = hubRedirect(session, location.pathname, nodeFromSearch(location.search));
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    if (hub !== null) throw redirect({ to: "/fleet", search: hub, replace: true });
    return session;
  },
  component: ConsoleLayout,
  // Before the shell exists (the first load, the session being read) there is no page around
  // the placeholder: it brings the page's margins.
  pendingComponent: FramedRoutePending,
  errorComponent: ({ error }) => (
    <main className="mx-auto min-h-dvh max-w-3xl px-6 py-16">
      <PageError error={error} />
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
