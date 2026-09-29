import { Info } from "lucide-react";

import { useT } from "../i18n";
import { compareVersions, useServerList } from "./servers";
import { NodeLink, useNode } from "./useNode";

const NOTICE = "mb-6 flex items-start gap-3 rounded-card border border-border bg-surface-raised px-4 py-3.5";

/**
 * What the shell says while on a node, above every page: that the central has no node by
 * that name, or that the node runs another Noust than this server, so the operator knows
 * why a page may say "Not available". Informational, so achromatic: colour is for state.
 * Nothing on this server, nor on a node on the same version.
 */
export function NodeNotice() {
  const t = useT();
  const { node } = useNode();
  const servers = useServerList();
  if (node === null || !servers.loaded) return null;

  const record = servers.nodes.find((candidate) => candidate.name === node);
  if (record === undefined) {
    return (
      <div role="status" className={NOTICE}>
        <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-fg-muted" />
        <div className="flex min-w-0 flex-col gap-1">
          <p className="max-w-[72ch] text-13 text-pretty text-fg">{t("fleet.shell.unknownNode", { node })}</p>
          <NodeLink node={null} className="self-start text-13 font-medium text-accent-fg underline underline-offset-2">
            {t("fleet.shell.backToThisServer")}
          </NodeLink>
        </div>
      </div>
    );
  }

  const order = compareVersions(record.version, servers.version);
  if (order === null || order === 0 || record.version === null || record.version === undefined || servers.version === null) return null;
  const words = { node, nodeVersion: record.version, version: servers.version };
  return (
    <section aria-label={t("fleet.shell.noticeLabel", { node })} className={NOTICE}>
      <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-fg-muted" />
      <p className="max-w-[72ch] text-13 text-pretty text-fg">
        {order < 0 ? t("fleet.shell.olderNode", words) : t("fleet.shell.newerNode", words)}
      </p>
    </section>
  );
}
