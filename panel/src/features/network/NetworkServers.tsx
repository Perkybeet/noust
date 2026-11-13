import { queryOptions, useQueries } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { request } from "../../api/client";
import { runOnNode } from "../../api/nodeScope";
import { DetailPage } from "../../components/page/DetailPage";
import type { Column } from "../../components/ui/DataTable";
import { DataTable } from "../../components/ui/DataTable";
import { buttonClassName } from "../../components/ui/Button";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { Mono } from "../../components/ui/Mono";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import { NODE_STATE, nodeStatus, useServerList } from "../../nodes/servers";

interface Row {
  /** The node's name; null is this central itself. */
  name: string | null;
  label: string;
  /** Whether the server answers, so its network can be read at all. */
  reachable: boolean;
  status: (typeof NODE_STATE)[keyof typeof NODE_STATE];
}

/** What the network of one server says of itself, read through the central's proxy: three cheap reads, none of them a list of the fleet. */
function summaryQueries(name: string | null, enabled: boolean) {
  const key = ["fleet", "network", name ?? "central"] as const;
  return [
    queryOptions({ queryKey: [...key, "published"], queryFn: () => runOnNode(name, () => request("get", "/api/streams")), enabled, retry: false, staleTime: 30_000 }),
    queryOptions({ queryKey: [...key, "tunnels"], queryFn: () => runOnNode(name, () => request("get", "/api/tunnels")), enabled, retry: false, staleTime: 30_000 }),
    queryOptions({ queryKey: [...key, "firewall"], queryFn: () => runOnNode(name, () => request("get", "/api/server/security/firewall")), enabled, retry: false, staleTime: 30_000 }),
  ] as const;
}

/**
 * Network from "All servers": the servers, each with what its network is (the ports it publishes, its tunnels and whether
 * its firewall filters), because what Network holds belongs to one machine and there is nothing to merge across them.
 * Opening one is choosing it in the selector, and the area opens on it. A server that does not answer says so, and a
 * server whose Noust is older than a read says nothing in that cell and not an error of the page.
 */
export function NetworkServers() {
  const t = useT();
  const servers = useServerList();
  // The central is a server of the list: it has a network of its own whatever it deploys (a hub included).
  const rows: Row[] = [
    { name: null, label: servers.hostname ?? t("fleet.selector.thisServer"), reachable: true, status: NODE_STATE.reachable },
    ...servers.nodes.map((node) => ({ name: node.name, label: node.name, reachable: nodeStatus(node) === "reachable", status: NODE_STATE[nodeStatus(node)] })),
  ];
  const results = useQueries({ queries: rows.flatMap((row) => summaryQueries(row.name, row.reachable)) });
  const read = (index: number, offset: number) => results[index * 3 + offset];

  const published = (index: number): string | null => {
    const found = read(index, 0)?.data as { exposures: readonly unknown[] } | undefined;
    return found === undefined ? null : String(found.exposures.length);
  };
  const tunnels = (index: number): string | null => {
    const found = read(index, 1)?.data as { tunnels: readonly unknown[] } | undefined;
    return found === undefined ? null : String(found.tunnels.length);
  };

  const cell = (row: Row, index: number, offset: 0 | 1 | 2, value: () => string | null) => {
    if (!row.reachable) return <EmptyCell />;
    const result = read(index, offset);
    if (result?.isError === true) return <EmptyCell reason={t("network.servers.unread")} />;
    const text = value();
    return text === null ? <Skeleton className="h-4 w-10" /> : <span className="tabular-nums">{text}</span>;
  };

  const firewall = (row: Row, index: number) => {
    if (!row.reachable) return <EmptyCell />;
    const result = read(index, 2);
    if (result?.isError === true) return <EmptyCell reason={t("network.servers.unread")} />;
    const found = result?.data as { firewall: { active: boolean; installed: boolean; backend: string } } | undefined;
    if (found === undefined) return <Skeleton className="h-4 w-16" />;
    if (found.firewall.backend === "none" || !found.firewall.installed) return <StatusPill state="warning" label={t("network.servers.firewallMissing")} appearance="inline" size="sm" />;
    return found.firewall.active ? <StatusPill state="running" label={t("network.firewall.on")} appearance="inline" size="sm" /> : <StatusPill state="stopped" label={t("network.firewall.off")} appearance="inline" size="sm" />;
  };

  const columns: Column<Row>[] = [
    {
      id: "server",
      header: t("fleet.machineArea.server"),
      card: "title",
      cell: (row) => (
        <span className="flex min-w-0 items-center gap-2">
          <OBJECT_ICONS.server aria-hidden="true" className="size-icon-md shrink-0 text-fg-faint" />
          <Mono truncate className="font-medium">
            {row.label}
          </Mono>
          {row.name === null ? <span className="text-12 text-fg-muted">{t("fleet.machineArea.central")}</span> : null}
        </span>
      ),
    },
    { id: "state", header: t("network.servers.stateColumn"), card: "status", width: "w-40", cell: (row) => <StatusPill state={row.status.state} label={t(row.status.label)} appearance="inline" size="sm" /> },
    { id: "published", header: t("domains.sections.ports.tab"), hideBelow: "md", card: "meta", cell: (row) => cell(row, rows.indexOf(row), 0, () => published(rows.indexOf(row))) },
    { id: "tunnels", header: t("domains.sections.tunnels.tab"), hideBelow: "md", card: "meta", cell: (row) => cell(row, rows.indexOf(row), 1, () => tunnels(rows.indexOf(row))) },
    { id: "firewall", header: t("serverPorts.views.firewall"), hideBelow: "sm", card: "meta", cell: (row) => firewall(row, rows.indexOf(row)) },
  ];

  return (
    <DetailPage header={{ title: t("nav.network.label"), description: t("network.servers.line") }}>
      <DataTable
        caption={t("fleet.machineArea.server")}
        columns={columns}
        rows={rows}
        getRowId={(row) => row.name ?? "central"}
        rowActions={(row) => (
          <Link
            to="/network"
            search={{ node: row.name ?? undefined }}
            aria-label={t("fleet.machineArea.open", { area: t("nav.network.label"), server: row.label })}
            className={buttonClassName("secondary", "sm")}
          >
            {t("network.servers.open")}
          </Link>
        )}
      />
    </DetailPage>
  );
}
