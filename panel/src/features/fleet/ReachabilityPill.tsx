import { StatusPill } from "../../components/ui/StatusPill";
import type { Status } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { PlainKey } from "../../i18n";
import type { Reachability } from "./useFleet";

/**
 * Each reachability told three ways, like every state: answering is green, not answering (or
 * answering "no") is red, locked is grey because nothing is wrong with the server, and not
 * checked yet is the question mark.
 */
const VIEW: Record<Reachability, { state: Status; label: PlainKey }> = {
  // The server selector's words for the same states, so both say it alike.
  reachable: { state: "running", label: "fleet.selector.status.reachable" },
  unreachable: { state: "failed", label: "fleet.selector.status.unreachable" },
  refused: { state: "failed", label: "fleet.selector.status.refused" },
  unknown: { state: "unknown", label: "fleet.selector.status.unknown" },
  locked: { state: "stopped", label: "servers.reachability.locked" },
};

export function ReachabilityPill({ reachability, size = "sm" }: { reachability: Reachability; size?: "sm" | "md" }) {
  const t = useT();
  const view = VIEW[reachability];
  return <StatusPill state={view.state} label={t(view.label)} appearance="inline" size={size} />;
}
