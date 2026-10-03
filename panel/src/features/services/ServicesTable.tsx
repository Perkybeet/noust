import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { STATE_RANK } from "../../components/page/status";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { Mono } from "../../components/ui/Mono";
import { StatusPill } from "../../components/ui/StatusPill";
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
  /** The application a unit runs, when it runs one: named under the unit. */
  appOf?: (service: ServiceInfo) => string | null;
  className?: string;
}

/** The table's columns; a function of `t` so every header and cell speaks the active language. */
function columnsFor(t: T, mixed: boolean, memory: boolean, appOf: ((service: ServiceInfo) => string | null) | undefined): Column<ServiceInfo>[] {
  const columns: Column<ServiceInfo>[] = [
    {
      id: "name",
      header: t("services.table.unit"),
      card: "title",
      cell: (row) => {
        const app = appOf?.(row) ?? null;
        return (
          <span className="flex min-w-0 flex-col">
            <Link
              to="/server/services/$name"
              params={{ name: row.name }}
              translate="no"
              className="-mx-1 w-fit max-w-full truncate rounded-chip px-1 py-0.5 font-medium text-fg hover:underline hover:underline-offset-2"
            >
              <Mono>{row.name}</Mono>
            </Link>
            {app !== null ? (
              <span className="truncate text-12 text-fg-muted">{t("services.table.runsApp", { domain: app })}</span>
            ) : !row.managed ? (
              <span className="text-12 text-fg-muted sm:hidden">{t("services.table.foreign")}</span>
            ) : null}
          </span>
        );
      },
      sortValue: (row) => row.name,
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
      id: "description",
      header: t("services.table.command"),
      hideBelow: "md",
      cell: (row) =>
        row.description ? (
          <Mono tone="muted" truncate>
            {row.description}
          </Mono>
        ) : (
          <EmptyCell reason={t("services.table.noCommand")} />
        ),
      sortValue: (row) => row.description ?? null,
    },
    ...(mixed
      ? [
          {
            id: "managed",
            header: t("services.table.managed"),
            width: "w-28",
            hideBelow: "sm" as const,
            // Words, not colour: nothing here is a state or something to act on.
            cell: (row: ServiceInfo) => (
              <span className={row.managed ? "text-fg" : "text-fg-muted"}>{row.managed ? t("services.table.wasm") : t("services.table.foreign")}</span>
            ),
            sortValue: (row: ServiceInfo) => (row.managed ? 0 : 1),
          },
        ]
      : []),
    {
      id: "enabled",
      header: t("services.table.boot"),
      width: "w-28",
      hideBelow: "sm",
      // Whether it starts at boot is an on/off, told at a glance like a state (item 56).
      cell: (row) => (
        <StatusPill
          state={row.enabled ? "running" : "stopped"}
          label={row.enabled ? t("services.table.enabled") : t("services.table.disabled")}
          appearance="inline"
          size="sm"
        />
      ),
      sortValue: (row) => (row.enabled ? 0 : 1),
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
  // Only worth a column when the list mixes both: otherwise every row would say "Noust".
  const mixed = services.some((service) => !service.managed);
  const memory = services.some((service) => memoryOf(service) !== null);
  const columns = columnsFor(t, mixed, memory, appOf);

  return (
    <DataTable
      columns={columns}
      rows={services}
      getRowId={(row) => row.name}
      caption={caption}
      loading={loading}
      mobile="cards"
      {...(empty !== undefined ? { empty } : {})}
      {...(rowActions ? { rowActions } : {})}
      defaultSort={{ column: "name", direction: "ascending" }}
      {...(className !== undefined ? { className } : {})}
    />
  );
}
