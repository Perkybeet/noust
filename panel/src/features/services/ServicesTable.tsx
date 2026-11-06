import type { ReactNode } from "react";

import { STATE_RANK } from "../../components/page/status";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { RelativeTime } from "../../components/page/RelativeTime";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { Mono } from "../../components/ui/Mono";
import { NameWithTags } from "../../components/ui/NameWithTags";
import { StatusPill } from "../../components/ui/StatusPill";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatBytes } from "../../lib/format";
import type { ServiceInfo } from "./data";
import { serviceState } from "./data";

function memoryOf(service: ServiceInfo): number | null {
  if (!service.memory) return null;
  const bytes = Number(service.memory);
  return Number.isFinite(bytes) && bytes > 0 ? bytes : null;
}

export interface ServicesTableProps {
  services: readonly ServiceInfo[];
  caption: string;
  loading?: boolean;
  empty?: ReactNode;
  rowActions?: (service: ServiceInfo) => ReactNode;
  /** The application a unit runs, when it runs one: said in the column of whose it is. */
  appOf?: (service: ServiceInfo) => string | null;
  className?: string;
}

/** Whose a unit is, in the words a person uses: the application it runs, what a person made by hand, or the system's. */
function ownerOf(t: T, service: ServiceInfo, appOf: ((service: ServiceInfo) => string | null) | undefined): { text: string; domain: boolean } {
  const app = appOf?.(service) ?? null;
  if (app !== null) return { text: app, domain: true };
  return { text: service.managed ? t("services.table.byHand") : t("services.table.system"), domain: false };
}

/** The table's columns; a function of `t` so every header and cell speaks the active language. */
function columnsFor(t: T, memory: boolean, appOf: ((service: ServiceInfo) => string | null) | undefined): Column<ServiceInfo>[] {
  const columns: Column<ServiceInfo>[] = [
    {
      id: "name",
      header: t("services.table.unit"),
      card: "title",
      // The tags follow the unit's name on its line, the first ones and "+N" for the rest.
      cell: (row) => (
        <NameWithTags
          name={
            <TextLink variant="row" to="/server/services/$name" params={{ name: row.name }} translate="no" className="-mx-1 w-fit max-w-full truncate px-1 py-0.5">
              <Mono>{row.name}</Mono>
            </TextLink>
          }
          tags={row.tags ?? []}
        />
      ),
      sortValue: (row) => row.name,
    },
    {
      id: "owner",
      header: t("services.table.belongsTo"),
      hideBelow: "sm",
      // The application's domain is a name of the machine; "by hand" and "the system's" are words.
      cell: (row) => {
        const owner = ownerOf(t, row, appOf);
        return owner.domain ? <Mono tone="muted">{owner.text}</Mono> : <span className="text-13 text-fg-muted">{owner.text}</span>;
      },
      sortValue: (row) => ownerOf(t, row, appOf).text,
    },
    {
      id: "state",
      header: t("services.table.state"),
      width: "w-32",
      card: "status",
      cell: (row) => {
        const view = serviceState(row);
        return (
          <span className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
            <StatusPill state={view.state} label={t(view.label)} appearance="inline" size="sm" />
            {view.detail !== undefined ? <Mono tone="faint">{view.detail}</Mono> : null}
          </span>
        );
      },
      sortValue: (row) => STATE_RANK[serviceState(row).state],
    },
    {
      id: "since",
      header: t("services.table.since"),
      hideBelow: "md",
      card: "meta",
      // Since it started running; a unit that is not running has no such moment.
      cell: (row) => (row.active && row.uptime ? <RelativeTime value={row.uptime} className="text-13 text-fg-muted" /> : <EmptyCell reason={t("services.table.notRunning")} />),
    },
  ];
  if (!memory) return columns;
  // A column no row has a reading for says nothing: it is left out until one does.
  return [
    ...columns,
    {
      id: "memory",
      header: t("services.table.memory"),
      align: "end",
      mono: true,
      width: "w-24",
      hideBelow: "lg",
      cell: (row) => {
        const bytes = memoryOf(row);
        return bytes === null ? <EmptyCell reason={t("services.table.noReading")} /> : formatBytes(bytes, t.locale);
      },
      sortValue: (row) => memoryOf(row),
    },
  ];
}

/**
 * Systemd units: their state, whether they start at boot and their memory. Noust's own by
 * default; with every unit listed, a column says which are Noust's and which are foreign
 * (read-only: see `./data`).
 */
export function ServicesTable({ services, caption, loading = false, empty, rowActions, appOf, className }: ServicesTableProps) {
  const t = useT();
  const memory = services.some((service) => memoryOf(service) !== null);
  const columns = columnsFor(t, memory, appOf);

  return (
    <DataTable
      columns={columns}
      rows={services}
      getRowId={(row) => row.name}
      caption={caption}
      loading={loading}
      pagination="services"
      {...(empty !== undefined ? { empty } : {})}
      {...(rowActions ? { rowActions } : {})}
      // The ones that fail come first: that is what the list is opened for.
      defaultSort={{ column: "state", direction: "ascending" }}
      {...(className !== undefined ? { className } : {})}
    />
  );
}
