import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState } from "react";

import { request } from "../../api/client";
import { networkKeys, streamsQuery } from "../../api/queries/network";
import type { StreamExposure } from "../../api/queries/network";
import { ErrorBlock } from "../../components/page/QueryState";
import { RowMenu } from "../../components/page/RowMenu";
import { GuardedButton, ReadOnlyLine, useCan } from "../../components/page/permissions";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import type { Column } from "../../components/ui/DataTable";
import { DataTable } from "../../components/ui/DataTable";
import { FirstUse } from "../../components/ui/FirstUse";
import { ICONS } from "../../components/ui/icons";
import { MenuItem } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { StatusPill } from "../../components/ui/StatusPill";
import { TextLink } from "../../components/ui/TextLink";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { useOnArrival } from "../../lib/useArrival";
import { useNode } from "../../nodes/useNode";
import { MachinePage } from "../server/MachinePage";
import { OpenPortDialog } from "./OpenPortDialog";
import type { OpenPortPreset } from "./OpenPortDialog";
import { PortsLack } from "./PortsLack";

function stateOf(exposure: StreamExposure): "running" | "deploying" | "warning" {
  if (exposure.state === "open") return "running";
  return exposure.state === "opening" ? "deploying" : "warning";
}

/**
 * Published ports, the first section of Network: what can be reached from outside this server and is not a web page,
 * with where it goes, who may come in and what it is for. A port is opened from here, from the firewall, from a row of
 * the listening ports and from a database's Connect section, always by the same dialog. Closing one removes the firewall
 * rules Noust made for it and nothing else.
 */
export function PublishedPortsPage({ opening = false, onOpened }: { opening?: boolean; onOpened?: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const { node } = useNode();
  const mayChange = useCan("server.host_access");
  const streams = useQuery(streamsQuery());
  const [dialog, setDialog] = useState<OpenPortPreset | null>(null);
  // Arriving with `?new` (the shortcut of Home, the palette): the dialog opens, and the link is spent.
  useOnArrival(opening, () => {
    setDialog({});
    onOpened?.();
  });
  const [closing, setClosing] = useState<StreamExposure | null>(null);
  const rows = streams.data?.exposures ?? [];
  const support = streams.data?.support;
  const empty = streams.data !== undefined && rows.length === 0;

  const columns: Column<StreamExposure>[] = [
    {
      id: "port",
      header: t("network.ports.portColumn"),
      card: "title",
      cell: (row) => (
        <span className="flex items-center gap-2">
          <Mono>{String(row.listen_port)}</Mono>
          <span className="text-12 text-fg-muted">{row.protocol.toUpperCase()}</span>
        </span>
      ),
      sortValue: (row) => row.listen_port,
    },
    { id: "target", header: t("network.ports.targetColumn"), hideBelow: "sm", cell: (row) => <Mono>{row.target}</Mono> },
    {
      id: "sources",
      header: t("network.ports.sourcesColumn"),
      hideBelow: "md",
      cell: (row) => (row.sources.length === 0 ? t("network.ports.anyone") : <Mono>{row.sources.join(", ")}</Mono>),
    },
    {
      id: "for",
      header: t("network.ports.forColumn"),
      hideBelow: "md",
      card: "meta",
      cell: (row) =>
        row.app_domain != null ? (
          <TextLink to="/apps/$domain" params={{ domain: row.app_domain }} onClick={(event) => event.stopPropagation()} translate="no">
            {row.app_domain}
          </TextLink>
        ) : (
          <span translate="no">{row.name}</span>
        ),
    },
    {
      id: "state",
      header: t("network.ports.stateColumn"),
      card: "status",
      cell: (row) => <StatusPill state={stateOf(row)} label={t(`network.ports.state.${row.state === "open" ? "open" : row.state === "opening" ? "opening" : "closing"}`)} appearance="inline" size="sm" />,
    },
  ];

  return (
    <MachinePage
      title={t("domains.sections.ports.title")}
      description={t("domains.sections.ports.description")}
      header={{
        primaryAction: (
          <GuardedButton permission="server.host_access" variant="primary" icon={<Plus aria-hidden="true" />} onClick={() => setDialog({})}>
            {t("network.openPort.title")}
          </GuardedButton>
        ),
      }}
    >
      <div className="flex min-w-0 flex-col gap-4">
        <ReadOnlyLine permission="server.host_access" />
        {support !== undefined && !support.available ? <PortsLack support={support} /> : null}
        {/* design-exception: hand-error-ternary the table draws its own skeleton rows; the failure replaces only what it would have listed */}
        {streams.isError && streams.data === undefined ? (
          <ErrorBlock error={streams.error} title={t("network.ports.listFailed")} onRetry={() => void streams.refetch()} retrying={streams.isRefetching} />
        ) : empty ? (
          <FirstUse>{t("network.ports.emptyDescription")}</FirstUse>
        ) : (
          <DataTable
            caption={t("network.ports.tableCaption")}
            columns={columns}
            rows={rows}
            getRowId={(row) => row.name}
            loading={streams.isPending}
            {...(mayChange
              ? {
                  rowActions: (row: StreamExposure) => (
                    <RowMenu name={row.name}>
                      <MenuItem icon={<ICONS.delete />} destructive onClick={() => setClosing(row)}>
                        {t("network.ports.close")}
                      </MenuItem>
                    </RowMenu>
                  ),
                }
              : {})}
          />
        )}
      </div>
      {dialog !== null ? <OpenPortDialog preset={dialog} onClose={() => setDialog(null)} /> : null}
      <ConfirmDialog
        open={closing !== null}
        onOpenChange={(open) => {
          if (!open) setClosing(null);
        }}
        friction="simple"
        server={node}
        destructive
        title={t("network.ports.closeTitle", { name: closing?.name ?? "" })}
        description={t("network.ports.closeDescription")}
        actionLabel={t("network.ports.close")}
        onConfirm={async () => {
          if (closing === null) return;
          await request("delete", "/api/streams/{name}", { params: { name: closing.name } });
          void queryClient.invalidateQueries({ queryKey: networkKeys.streams });
          toast.success(t("network.ports.closedToast", { name: closing.name }));
        }}
      />
    </MachinePage>
  );
}
