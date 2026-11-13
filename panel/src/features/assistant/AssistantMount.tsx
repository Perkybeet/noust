import { Suspense, lazy } from "react";

import { usePanel } from "./store";
import { useAssistantSession } from "./useAssistantSession";

// The panel is its own chunk: nothing of it is fetched until somebody opens it.
const AssistantHost = lazy(() => import("./AssistantHost"));

/** Where the frame holds the panel: nothing at all until it is opened, and nothing ever for a person who may not use the assistant. */
export function AssistantMount() {
  const panel = usePanel();
  const session = useAssistantSession();
  if (!panel.open || session === null) return null;
  return (
    <Suspense fallback={null}>
      <AssistantHost />
    </Suspense>
  );
}
