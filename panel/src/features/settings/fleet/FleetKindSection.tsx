import { useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useState } from "react";

import { ErrorBlock } from "../../../components/page/QueryState";
import { RowMenu } from "../../../components/page/RowMenu";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyState } from "../../../components/ui/EmptyState";
import { ICONS } from "../../../components/ui/icons";
import { MenuItem, MenuSeparator } from "../../../components/ui/Menu";
import { Mono } from "../../../components/ui/Mono";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import { reportActionError } from "../../apps/useAppActions";
import { ApplyDialog } from "./ApplyDialog";
import { fleetSettingsKeys, removeSetting, restoreOnServer } from "./data";
import { isWaiting } from "./faces";
import { FollowLine } from "./FollowLine";
import { isNamedKind } from "./onlyHere";
import { OnlyHereList } from "./OnlyHereList";
import { SettingPanel } from "./SettingPanel";
import type { PanelActions } from "./SettingPanel";
import { ShareDialog } from "./ShareDialog";
import { useValueText } from "./valueText";
import { useFleetKind } from "./useFleetKind";
import type { FleetRow } from "./useFleetKind";

export interface FleetKindSectionProps {
  /** The kind of setting: `channel`, `alert_rule`, `backup_destination`, `registry`, `variable`, `console_link`. */
  resource: string;
  title: string;
  icon: LucideIcon;
  description?: string;
  /** What the value of this kind is called: "Destination", "Threshold". */
  valueLabel: string;
  /** The head's button, "Share a channel". */
  add: string;
  /** Said where the fleet holds nothing of this kind yet. */
  emptyTitle: string;
  emptyDescription: string;
  /** Draw the on/off state of each row (a channel, a rule). */
  onOff?: boolean;
  /** The icon of a row. */
  rowIcon?: (row: FleetRow) => LucideIcon;
  /** A table (a name, a value, the servers that follow it) instead of a list of rows: for a kind of which there are many. */
  table?: boolean;
  /** Names the column of the names in a table. */
  nameLabel?: string;
}

/** The kinds there is one setting of, which is edited and never added to. */
const SINGLE: ReadonlySet<string> = new Set(["console_link", "policy"]);

type Dialog =
  | { kind: "share"; row: FleetRow | null; node: string | null }
  | { kind: "apply"; only: string[]; nodes?: string[] }
  | { kind: "remove"; row: FleetRow; scope: string }
  | null;

/**
 * What the central carries to every server, in the section where it belongs: one row for each setting of the kind, its
 * value, a line that says how many servers follow it and which differ, and a panel with the table of servers where a
 * value of one server alone is made, changed or taken away. Nothing at all on a lone server and for a person who may
 * not read the fleet: they see the section as it always was.
 */
export function FleetKindSection({ resource, title, icon, description, valueLabel, add, emptyTitle, emptyDescription, onOff = false, rowIcon, table = false, nameLabel }: FleetKindSectionProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const fleet = useFleetKind(resource);
  const valueText = useValueText(resource);
  const [open, setOpen] = useState<string | null>(null);
  const [dialog, setDialog] = useState<Dialog>(null);
  if (!fleet.active) return null;

  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: fleetSettingsKeys.all });
  };
  const opened = fleet.rows.find((row) => row.name === open);
  const key = (row: FleetRow): string => `${resource}/${row.name}`;
  const actions = (row: FleetRow): PanelActions => ({
    edit: () => {
      setDialog({ kind: "share", row, node: null });
      setOpen(null);
    },
    own: (node) => {
      setDialog({ kind: "share", row, node });
      setOpen(null);
    },
    removeOwn: (node) => {
      setDialog({ kind: "remove", row, scope: `node:${node}` });
      setOpen(null);
    },
    putBack: (node) => {
      void restoreOnServer(node, resource, row.name).then(refresh, (error: unknown) => {
        reportActionError(t("fleetSettings.actions.putBackFailed", { name: row.name, server: node }), error);
      });
    },
    apply: (node) => {
      setDialog({ kind: "apply", only: [key(row)], ...(node !== undefined ? { nodes: [node] } : {}) });
      setOpen(null);
    },
  });

  const menuFor = (row: FleetRow, waiting: boolean) => (
    <RowMenu name={row.name}>
      {fleet.canManage ? <MenuItem onClick={() => setDialog({ kind: "share", row, node: null })}>{t("fleetSettings.list.edit")}</MenuItem> : null}
      <MenuItem onClick={() => setOpen(row.name)}>{t("fleetSettings.panel.open")}</MenuItem>
      {fleet.canManage && waiting ? <MenuItem onClick={() => setDialog({ kind: "apply", only: [key(row)] })}>{t("fleetSettings.panel.applyToServers")}</MenuItem> : null}
      {fleet.canManage ? <MenuSeparator /> : null}
      {fleet.canManage ? (
        <MenuItem icon={<ICONS.delete />} destructive onClick={() => setDialog({ kind: "remove", row, scope: "fleet" })}>
          {t("fleetSettings.list.remove")}
        </MenuItem>
      ) : null}
    </RowMenu>
  );
  const columns: Column<FleetRow>[] = [
    {
      id: "name",
      header: nameLabel ?? title,
      card: "title",
      cell: (row) => (
        <Mono tone="default" truncate>
          {row.name}
        </Mono>
      ),
    },
    {
      id: "value",
      header: valueLabel,
      card: "meta",
      cell: (row) => <span className="text-13 text-fg-muted">{row.fleet !== null ? valueText(row.fleet.value) || t("fleetSettings.list.noValue") : t("fleetSettings.list.noValue")}</span>,
    },
    {
      id: "servers",
      header: t("fleetSettings.panel.servers"),
      card: "meta",
      cell: (row) => <FollowLine row={row.status} onOpen={() => setOpen(row.name)} />,
    },
  ];

  return (
    <div className="flex flex-col" data-slot="fleet-kind">
      <SurfaceSection
        title={title}
        icon={icon}
        {...(description !== undefined ? { description } : {})}
        flush
        {...(fleet.canManage && fleet.kind?.available !== false && !(SINGLE.has(resource) && fleet.rows.length > 0)
          ? {
              actions: (
                <Button size="sm" variant="secondary" icon={<Plus aria-hidden="true" />} disabled={fleet.kinds.length === 0} onClick={() => setDialog({ kind: "share", row: null, node: null })}>
                  {add}
                </Button>
              ),
            }
          : {})}
      >
        {fleet.error !== null && fleet.error !== undefined && fleet.rows.length === 0 ? (
          <div className="px-5 py-4">
            <ErrorBlock compact error={fleet.error} title={t("fleetSettings.loading")} onRetry={fleet.refetch} />
          </div>
        ) : fleet.loading ? (
          <div className="px-5 py-4">
            <Skeleton className="h-16 w-full rounded-card" />
          </div>
        ) : fleet.rows.length === 0 ? (
          <div className="flex min-h-24 items-center px-5 py-4">
            <EmptyState variant="inline" title={emptyTitle} description={emptyDescription} />
          </div>
        ) : table ? (
          <DataTable
            caption={t("fleetSettings.list.caption", { title })}
            columns={columns}
            rows={fleet.rows}
            getRowId={(row) => row.name}
            embedded
            {...(fleet.canManage || fleet.rows.length > 0 ? { rowActions: (row: FleetRow) => menuFor(row, row.status.servers.some((cell) => isWaiting(cell))) } : {})}
          />
        ) : (
          <ul aria-label={title} className="m-0 flex list-none flex-col divide-y divide-border p-0">
            {fleet.rows.map((row) => {
              const Icon = rowIcon?.(row);
              const value = row.fleet !== null ? valueText(row.fleet.value) : "";
              const enabled = row.fleet?.value["enabled"] !== false;
              const waiting = row.status.servers.some((cell) => isWaiting(cell));
              return (
                <li key={row.name} className="flex flex-col gap-2.5 px-5 py-4">
                  <div className="flex items-start gap-3">
                    {Icon !== undefined ? <Icon aria-hidden="true" className="mt-0.5 size-icon-md shrink-0 text-fg-muted" /> : null}
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                        <Mono tone="default" className="text-14 font-medium">
                          {row.name}
                        </Mono>
                        {onOff ? <StatusPill state={enabled ? "running" : "stopped"} label={enabled ? t("fleetSettings.list.on") : t("fleetSettings.list.off")} size="sm" /> : null}
                      </div>
                      <p className="mt-0.5 text-13 text-pretty text-fg-muted">{value !== "" ? value : t("fleetSettings.list.noValue")}</p>
                    </div>
                    <div className="-my-1 flex shrink-0 items-center gap-2">
                      {menuFor(row, waiting)}
                    </div>
                  </div>
                  <div className={Icon !== undefined ? "pl-8" : undefined}>
                    <FollowLine row={row.status} onOpen={() => setOpen(row.name)} />
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        {isNamedKind(resource) ? <OnlyHereList resource={resource} /> : null}
      </SurfaceSection>

      {opened !== undefined ? <SettingPanel row={opened} resource={resource} valueLabel={valueLabel} canManage={fleet.canManage} actions={actions(opened)} onClose={() => setOpen(null)} /> : null}
      {dialog?.kind === "share" ? (
        <ShareDialog
          kinds={fleet.kinds}
          kind={resource}
          setting={dialog.row?.fleet ?? null}
          servers={fleet.servers.map((server) => server.node)}
          node={dialog.node}
          {...(dialog.row !== null ? { plan: dialog.row.status } : {})}
          onClose={() => setDialog(null)}
        />
      ) : null}
      {dialog?.kind === "apply" ? <ApplyDialog only={dialog.only} {...(dialog.nodes !== undefined ? { nodes: dialog.nodes } : {})} onClose={() => setDialog(null)} /> : null}
      {dialog?.kind === "remove" ? (
        <ConfirmDialog
          friction="simple"
          destructive
          open
          onOpenChange={(next) => {
            if (!next) setDialog(null);
          }}
          title={dialog.scope === "fleet" ? t("fleetSettings.remove.title", { name: dialog.row.name }) : t("fleetSettings.remove.exceptionTitle", { name: dialog.row.name, server: dialog.scope.replace(/^node:/, "") })}
          description={dialog.scope === "fleet" ? t("fleetSettings.remove.description") : t("fleetSettings.remove.exceptionDescription", { server: dialog.scope.replace(/^node:/, "") })}
          actionLabel={t("common.words.remove")}
          onConfirm={async () => {
            await removeSetting(resource, dialog.row.name, dialog.scope);
            refresh();
          }}
        />
      ) : null}
    </div>
  );
}
