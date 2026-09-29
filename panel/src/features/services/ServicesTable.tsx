import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { RelativeTime } from "../../components/page/RelativeTime";
import { STATE_RANK } from "../../components/page/status";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { StatusPill } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatBytes } from "../../lib/format";
import type { ServiceInfo } from "./data";
import { serviceState } from "./data";

/** A table cell with nothing to report: a dash on screen, the reason for screen readers. */
function Nothing({ reason }: { reason: string }) {
  return (
    <>
      <span aria-hidden="true" className="text-fg-faint">
        -
      </span>
      <span className="sr-only">{reason}</span>
    </>
  );
}

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
  className?: string;
}

/** The table's columns; a function of `t` so every header and cell speaks the active language. */
function columnsFor(t: T, mixed: boolean): Column<ServiceInfo>[] {
  return [
    {
      id: "state",
      header: t("services.table.state"),
      width: "w-28",
      cell: (row) => {
        const view = serviceState(row);
        return (
          <span className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
            <StatusPill state={view.state} label={t(view.label)} appearance="inline" size="sm" />
            {view.detail !== undefined ? <span className="mono text-12 text-fg-faint">{view.detail}</span> : null}
          </span>
        );
      },
      sortValue: (row) => STATE_RANK[serviceState(row).state],
    },
    {
      id: "name",
      header: t("services.table.unit"),
      cell: (row) => (
        <span className="flex min-w-0 items-baseline gap-2">
          <Link
            to="/services/$name"
            params={{ name: row.name }}
            translate="no"
            className="-mx-1 rounded-[4px] px-1 py-0.5 font-medium text-fg mono text-12 hover:underline hover:underline-offset-2 focus-visible:outline-2 focus-visible:outline-focus"
          >
            {row.name}
          </Link>
          {/* On a phone the Managed column is hidden: a foreign unit still says so. */}
          {row.managed ? null : <span className="text-12 text-fg-muted sm:hidden">{t("services.table.foreign")}</span>}
        </span>
      ),
      sortValue: (row) => row.name,
    },
    {
      id: "description",
      header: t("services.table.command"),
      mono: true,
      hideBelow: "md",
      cell: (row) =>
        row.description ? (
          <span className="truncate text-fg-muted" title={row.description}>
            {row.description}
          </span>
        ) : (
          <Nothing reason={t("services.table.noCommand")} />
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
              <span className={row.managed ? "text-fg" : "text-fg-muted"}>
                {row.managed ? t("services.table.wasm") : t("services.table.foreign")}
              </span>
            ),
            sortValue: (row: ServiceInfo) => (row.managed ? 0 : 1),
          },
        ]
      : []),
    {
      id: "enabled",
      header: t("services.table.boot"),
      width: "w-20",
      hideBelow: "sm",
      cell: (row) => <span className="text-fg-muted">{row.enabled ? t("services.table.enabled") : t("services.table.disabled")}</span>,
      sortValue: (row) => (row.enabled ? 0 : 1),
    },
    {
      id: "uptime",
      header: t("services.table.since"),
      width: "w-32",
      hideBelow: "lg",
      cell: (row) => (row.active && row.uptime ? <RelativeTime value={row.uptime} /> : <Nothing reason={t("services.table.notRunning")} />),
    },
    {
      id: "memory",
      header: t("services.table.memory"),
      align: "end",
      mono: true,
      width: "w-24",
      hideBelow: "lg",
      cell: (row) => {
        const bytes = memoryOf(row);
        return bytes === null ? <Nothing reason={t("services.table.noReading")} /> : formatBytes(bytes);
      },
      sortValue: (row) => memoryOf(row),
    },
  ];
}

/**
 * Systemd units: their state, whether they start at boot and their live readings. Noust's own
 * by default; with every unit listed, a column says which are Noust's and which are foreign
 * (read-only: see `./data`).
 */
export function ServicesTable({ services, caption, loading = false, empty, rowActions, className }: ServicesTableProps) {
  const t = useT();
  // Only worth a column when the list mixes both: otherwise every row would say "Noust".
  const mixed = services.some((service) => !service.managed);
  const columns = columnsFor(t, mixed);

  return (
    <DataTable
      columns={columns}
      rows={services}
      getRowId={(row) => row.name}
      caption={caption}
      loading={loading}
      {...(empty !== undefined ? { empty } : {})}
      {...(rowActions ? { rowActions } : {})}
      defaultSort={{ column: "name", direction: "ascending" }}
      {...(className !== undefined ? { className } : {})}
    />
  );
}
