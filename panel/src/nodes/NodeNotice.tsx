import { useQuery } from "@tanstack/react-query";

import { Notice } from "../components/ui/Notice";
import { accessOf, fleetViewQuery, originOf } from "../features/fleet/data";
import { useT } from "../i18n";
import { compareVersions, useServerList } from "./servers";
import { NodeLink, useNode } from "./useNode";

/**
 * What the shell says while on a node, above every page: that the central has no node by that
 * name, that the node runs another Noust than this central (so a page may say "Not
 * available"), or that the node lets this central only read, or only operate its
 * applications (so an action may be refused, by the node). Information, so achromatic: colour
 * is for state. Nothing on this server, nor on a node on the same version that lets the
 * central do everything.
 */
export function NodeNotice() {
  const t = useT();
  const { node } = useNode();
  const servers = useServerList();
  // The ceiling each node published to this central: the fleet's own read, shared and cached.
  const fleet = useQuery({ ...fleetViewQuery("servers"), enabled: node !== null, refetchInterval: false });
  if (node === null || !servers.loaded) return null;

  const record = servers.nodes.find((candidate) => candidate.name === node);
  if (record === undefined) {
    return (
      <Notice
        variant="banner"
        className="mb-6"
        action={
          <NodeLink node={null} className="rounded-chip text-13 font-medium text-accent-fg underline underline-offset-2">
            {t("fleet.shell.backToThisServer")}
          </NodeLink>
        }
      >
        {t("fleet.shell.unknownNode", { node })}
      </Notice>
    );
  }

  const row = fleet.data?.items.find((item) => !originOf(item).local && originOf(item).node === node);
  const access = row === undefined ? null : accessOf(row);
  const order = compareVersions(record.version, servers.version);
  const words = { node, nodeVersion: record.version ?? "", version: servers.version ?? "" };
  const version =
    order === null || order === 0 || record.version === null || record.version === undefined || servers.version === null
      ? null
      : order < 0
        ? t("fleet.shell.olderNode", words)
        : t("fleet.shell.newerNode", words);
  const ceiling = access === null || access.level === "admin" ? null : access.level === "read" ? t("fleet.shell.readOnly", { node }) : t("fleet.shell.deployOnly", { node });
  if (version === null && ceiling === null) return null;
  return (
    <section aria-label={t("fleet.shell.noticeLabel", { node })} className="mb-6 flex flex-col gap-2">
      {ceiling !== null ? <Notice variant="banner">{ceiling}</Notice> : null}
      {version !== null ? <Notice variant="banner">{version}</Notice> : null}
    </section>
  );
}
