/** What a server's Noust DNS is, in the words the page and the card on the DNS page both use. */

import type { NoustServer } from "../../../api/queries/noustDns";
import type { T } from "../../../i18n";

export type NoustState = "notInstalled" | "notSetUp" | "primary" | "secondary" | "stopped";

/**
 * Where the server stands: not installed, installed and waiting to be set up, or set up as a
 * primary or a secondary (and then stopped when its service does not run, which is an alert, not a
 * way of being set up).
 */
export function noustState(server: Pick<NoustServer, "installed" | "configured" | "running" | "role">): NoustState {
  if (!server.installed) return "notInstalled";
  if (!server.configured) return "notSetUp";
  if (!server.running) return "stopped";
  return server.role === "secondary" ? "secondary" : "primary";
}

/** The StatusPill state of each: on and answering is running; waiting to be set up is half done; stopped is stopped. */
export const NOUST_PILL: Readonly<Record<NoustState, "running" | "queued" | "stopped" | "failed">> = {
  notInstalled: "stopped",
  notSetUp: "queued",
  primary: "running",
  secondary: "running",
  stopped: "failed",
};

/** A state in words: "Stopped" is the console's own word for it, the other four are Noust DNS's. */
export function stateLabel(t: T, state: NoustState): string {
  return state === "stopped" ? t("common.statusPill.stopped") : t(`noustDns.card.${state}`);
}
