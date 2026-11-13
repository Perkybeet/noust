import { useQuery } from "@tanstack/react-query";

import { sessionQuery } from "../../api/queries/auth";
import type { SessionInfo } from "../../api/queries/auth";

export type AssistantSession = NonNullable<SessionInfo["assistant"]>;

/**
 * What the session says of the assistant for this person, or null when it is not to be drawn. With the assistant off, or for a person
 * who may not use it, there is no assistant in the console: the button in the bar, "Explain this" and the entry of the palette all
 * ask this, and none of them exists when it answers null. Only its own page in Settings is drawn off, and only for who may turn it on.
 *
 * @returns The session's `assistant` when it is on and this person may use it, else null.
 */
export function useAssistantSession(): AssistantSession | null {
  const { data } = useQuery({ ...sessionQuery(), select: (session) => session.assistant ?? null });
  return data !== undefined && data !== null && data.enabled && data.can_use ? data : null;
}
