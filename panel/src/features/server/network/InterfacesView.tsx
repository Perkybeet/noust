import { useQuery } from "@tanstack/react-query";

import { networkQuery } from "../../../api/queries/system";
import type { NetworkInfo } from "../../../api/queries/system";
import { Card } from "../../../components/ui/Card";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Mono } from "../../../components/ui/Mono";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import { formatBytes } from "../../../lib/format";
import { ServerQueryState } from "../errors";

type Interface = NetworkInfo["interfaces"][number];

/** The network interfaces of the machine, with their addresses and what went through them (read only). */
export function InterfacesView() {
  const t = useT();
  const network = useQuery(networkQuery());
  const columns: Column<Interface>[] = [
    { id: "name", header: t("server.network.interface"), card: "title", cell: (row) => <Mono>{row.name}</Mono> },
    {
      id: "state",
      header: t("server.network.state"),
      width: "w-28",
      card: "status",
      cell: (row) => <StatusPill state={row.is_up ? "running" : "stopped"} label={row.is_up ? t("server.network.up") : t("server.network.down")} appearance="inline" size="sm" />,
    },
    {
      id: "addresses",
      header: t("server.network.addresses"),
      cell: (row) =>
        (row.addresses?.length ?? 0) > 0 ? (
          <span className="flex min-w-0 flex-col">
            {(row.addresses ?? []).map((address) => (
              <Mono key={address.address} tone="muted" truncate>
                {address.address}
              </Mono>
            ))}
          </span>
        ) : (
          <EmptyCell reason={t("server.network.noAddress")} />
        ),
    },
    { id: "sent", header: t("server.network.sent"), align: "end", hideBelow: "md", mono: true, cell: (row) => formatBytes(row.bytes_sent, t.locale) },
    { id: "received", header: t("server.network.received"), align: "end", hideBelow: "md", mono: true, cell: (row) => formatBytes(row.bytes_recv, t.locale) },
  ];
  return (
    <ServerQueryState query={network} label={t("server.network.label")} skeleton={<SkeletonCard lines={2} />}>
      {(data) => (
        <Card level={2} title={t("server.network.title")} padding="none">
          <DataTable
            columns={columns}
            rows={data.interfaces}
            getRowId={(row) => row.name}
            caption={t("server.network.caption")}
            density="compact"
            empty={<EmptyState variant="inline" title={t("server.network.none")} />}
          />
        </Card>
      )}
    </ServerQueryState>
  );
}
