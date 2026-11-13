import { useMemo } from "react";

import { entryAt } from "../../app/nav";
import { DetailPage } from "../../components/page/DetailPage";
import { ScopeLine } from "../../components/page/ScopeLine";
import { ServersTable } from "../../components/page/ServersTable";
import type { ServersTableRow } from "../../components/page/ServersTable";
import { EmptyState } from "../../components/ui/EmptyState";
import { useT } from "../../i18n";
import { useFleetServers } from "../../nodes/servers";
import { CatalogFace } from "../catalog/CatalogFace";

/**
 * A section of the console seen from "All servers" that has no page of the fleet of its own yet: the same section, with the
 * servers that have it and the way into each one's page (item 196). Nothing disappears with All servers chosen and no click is a
 * dead end: a section waiting for its fleet view is one table, which the page of the area replaces with a list or a comparison
 * when it has one. The table is not an error and not a promise: it says what it is.
 *
 * @param path The section's address on a server (`/databases/engines`); the page is at `/fleet` followed by it.
 */
export function FleetSectionPage({ path }: { path: string }) {
  const t = useT();
  const entry = entryAt(path);
  const found = entry?.to === path ? entry : undefined;
  const servers = useFleetServers(found?.feature);
  const rows = useMemo<ServersTableRow[]>(() => servers.map(({ server }) => ({ server })), [servers]);
  if (found === undefined) {
    return (
      <DetailPage header={{ title: t("shell.notFound.title") }}>
        <EmptyState variant="firstUse" title={t("shell.notFound.title")} />
      </DetailPage>
    );
  }
  // The recipes are the same on every server: the catalog is its own page here, with no table of servers to stand in for it.
  if (found.id === "catalog") return <CatalogFace />;
  const section = t(found.rootLabel ?? found.label);
  return (
    <DetailPage header={{ title: section }}>
      <div className="flex flex-col gap-4">
        <ScopeLine scope={{ kind: "all", servers: servers.map(({ server }) => server) }} />
        <p className="text-13 text-fg-muted">{t("fleet.section.explain")}</p>
        <ServersTable
          caption={t("fleet.section.caption", { section })}
          rows={rows}
          open={(server) => ({ to: found.to, search: { node: servers.find((candidate) => candidate.server.name === server.name)?.node ?? undefined } })}
        />
      </div>
    </DetailPage>
  );
}
