/**
 * Who can reach a listening port, said in the console's words: the verdict of the firewall on the port, coloured only
 * when it is a problem. Pure, so the table of listening ports and its tests share it.
 */

import type { Status } from "../../components/ui/StatusPill";
import type { T } from "../../i18n";
import type { ListeningPort } from "../server/queries";

/** What the firewall does about a port: coloured only when it is a problem. */
export function verdictView(t: T, port: ListeningPort): { state: Status | null; label: string } {
  if (port.verdict === "docker_bypass") return { state: "failed", label: t("server.firewall.verdict.dockerBypass") };
  if (port.risky !== "" && port.reachable) return { state: "failed", label: t("server.firewall.verdict.exposed") };
  switch (port.verdict) {
    case "local":
      return { state: null, label: t("server.firewall.verdict.local") };
    case "blocked":
      return { state: null, label: t("server.firewall.verdict.blocked") };
    case "open":
      return { state: null, label: t("server.firewall.verdict.open") };
    case "open_to":
      return { state: null, label: t("server.firewall.verdict.openTo", { sources: port.sources.join(", ") }) };
    case "no_firewall":
      return { state: "warning", label: t("server.firewall.verdict.noFirewall") };
    default:
      return { state: null, label: port.verdict };
  }
}

/** A port as the firewall writes it: `5432/tcp`. */
export function portText(port: Pick<ListeningPort, "port" | "proto">): string {
  return `${String(port.port)}/${port.proto}`;
}

/** Whether the firewall (or its absence) keeps a listening port from the network: nothing to open. */
export function isShut(port: ListeningPort | null): boolean {
  return port !== null && !port.reachable && port.verdict !== "docker_bypass";
}
