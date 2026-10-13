import type { T } from "../../../i18n";
import { sshAddress } from "../../fleet/nodes";
import type { NodeRecord } from "../../fleet/nodes";

/**
 * Where this central reaches a server, in words: the SSH account and address of one it dials, or
 * the loopback port of this machine the link of one that dials in leaves (it has no SSH address
 * anyone could use, only the one this central listens on for it).
 */
export function nodeAddress(t: T, node: Pick<NodeRecord, "ssh_user" | "ssh_host" | "ssh_port" | "link">): string {
  return node.link != null ? t("servers.settings.linkedAddress", { port: String(node.link.port) }) : sshAddress(node);
}
