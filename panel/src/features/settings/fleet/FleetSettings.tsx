import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { sessionQuery } from "../../../api/queries/auth";
import { useDocumentTitle } from "../../../app/documentTitle";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock, QueryState } from "../../../components/page/QueryState";
import { RowMenu } from "../../../components/page/RowMenu";
import { Section, Sections } from "../../../components/page/Section";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyState } from "../../../components/ui/EmptyState";
import { ICONS } from "../../../components/ui/icons";
import { MenuItem, MenuSeparator } from "../../../components/ui/Menu";
import { Mono } from "../../../components/ui/Mono";
import { RowList } from "../../../components/ui/RowList";
import { ServerText } from "../../../components/ui/ServerText";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { Switch } from "../../../components/ui/Switch";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { reportActionError } from "../../apps/useAppActions";
import { SettingsPrimaryAction } from "../SettingsShell";
import { ApplyDialog } from "./ApplyDialog";
import { cellOf, cellView, fleetSettingsKeys, fleetSettingsQuery, fleetStatusQuery, pendingCount, removeSetting, restoreOnServer, setServerMode } from "./data";
import type { FleetSetting, FleetSettingKind, FleetStatus, StatusServer, StatusSetting } from "./data";
import { ShareDialog, kindWord } from "./ShareDialog";

type Open =
  | { kind: "share"; setting: FleetSetting | null; node: string | null }
  | { kind: "apply" }
  | { kind: "remove"; setting: StatusSetting; scope: string }
  | null;

/** The three levels a server can be on, said once above the table so the words under it are known. */
function Levels({ t }: { t: T }) {
  const levels = ["follows", "exception", "independent"] as const;
  return (
    <ul aria-label={t("fleetSettings.levels.legend")} className="m-0 grid list-none grid-cols-1 gap-x-8 gap-y-3 p-0 sm:grid-cols-3">
      {levels.map((level) => (
        <li key={level} className="flex min-w-0 flex-col gap-0.5">
          <span className="text-13 font-medium text-fg">{t(`fleetSettings.levels.${level}.label`)}</span>
          <span className="text-12 text-pretty text-fg-muted">{t(`fleetSettings.levels.${level}.help`)}</span>
        </li>
      ))}
    </ul>
  );
}

/** How each server follows the fleet: one switch a server, with what the central could read of it. */
function Servers({ t, servers, canManage }: { t: T; servers: readonly StatusServer[]; canManage: boolean }) {
  const queryClient = useQueryClient();
  const mode = useMutation({
    mutationFn: ({ node, follow }: { node: string; follow: boolean }) => setServerMode(node, follow ? "managed" : "independent"),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: fleetSettingsKeys.all });
    },
    onError: (error, { node }) => {
      reportActionError(t("fleetSettings.servers.switchFailed", { name: node }), error);
    },
  });
  // As tall as the skeleton that was there while it loaded, so the list under it does not move when the answer is "none".
  if (servers.length === 0) {
    return (
      <div className="flex min-h-24 items-center">
        <EmptyState variant="inline" title={t("fleetSettings.servers.noneTitle")} description={t("fleetSettings.servers.noneDescription")} />
      </div>
    );
  }
  return (
    <RowList aria-label={t("fleetSettings.servers.caption")}>
      {servers.map((server) => {
        const follows = mode.isPending && mode.variables.node === server.node ? mode.variables.follow : server.mode === "managed";
        return (
          <RowList.Item
            key={server.node}
            actions={
              <Switch
                aria-label={`${t("fleetSettings.servers.mode")}: ${server.node}`}
                checked={follows}
                busy={mode.isPending && mode.variables.node === server.node}
                disabled={!canManage}
                onCheckedChange={(next) => {
                  mode.mutate({ node: server.node, follow: next });
                }}
              />
            }
          >
            <span className="flex min-w-0 flex-col gap-0.5">
              <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
                <Mono tone="default">{server.node}</Mono>
                {server.outcome !== "ok" ? (
                  <StatusPill state={server.outcome === "unreachable" || server.outcome === "error" ? "failed" : "stopped"} label={t("fleetSettings.servers.unreadable")} appearance="inline" size="sm" />
                ) : null}
              </span>
              <span className="text-12 text-fg-muted">{follows ? t("fleetSettings.servers.mode") : t("fleetSettings.levels.independent.label")}</span>
              {server.message !== undefined && server.message !== null ? (
                <span className="text-12 text-fg-muted">
                  <ServerText coded={server.code !== undefined && server.code !== null ? { code: server.code, params: {} } : null} text={server.message} />
                </span>
              ) : null}
            </span>
          </RowList.Item>
        );
      })}
    </RowList>
  );
}

/** One server's cell: where its value comes from, in plain text when it is well and with its state when it is not. */
function CellFor({ t, setting, node, canManage }: { t: T; setting: StatusSetting; node: string; canManage: boolean }) {
  const queryClient = useQueryClient();
  const cell = cellOf(setting, node);
  const put = useMutation({
    mutationFn: () => restoreOnServer(node, setting.resource, setting.name),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: fleetSettingsKeys.all });
    },
    onError: (error) => {
      reportActionError(t("fleetSettings.actions.putBackFailed", { name: setting.name, server: node }), error);
    },
  });
  // On a phone the row is a card whose header is read aloud but not drawn: the server's name leads its value.
  const name = (
    <Mono tone="muted" className="sm:hidden">
      {node}
    </Mono>
  );
  if (cell === undefined) {
    return (
      <span className="inline-flex min-w-0 align-middle flex-wrap items-center gap-x-2 gap-y-1">
        {name}
        <span className="text-13 text-fg-muted">{t("fleetSettings.cell.unknown")}</span>
      </span>
    );
  }
  const view = cellView(cell);
  return (
    <span className="inline-flex min-w-0 align-middle flex-wrap items-center gap-x-2 gap-y-1">
      {name}
      {view.state === null ? (
        view.level === "exception" ? (
          <Badge>{t(view.label)}</Badge>
        ) : (
          <span className="text-13 text-fg">{t(view.label)}</span>
        )
      ) : (
        <StatusPill state={view.state} label={t(view.label)} appearance="inline" size="sm" />
      )}
      {view.restorable && canManage ? (
        <Button size="sm" variant="ghost" loading={put.isPending} aria-label={t("fleetSettings.actions.putBackLabel", { name: setting.name, server: node })} onClick={() => put.mutate()}>
          {t("fleetSettings.actions.putBack")}
        </Button>
      ) : null}
    </span>
  );
}

function columnsFor(t: T, status: FleetStatus, kinds: readonly FleetSettingKind[], canManage: boolean): Column<StatusSetting>[] {
  return [
    {
      id: "setting",
      header: t("fleetSettings.table.setting"),
      card: "title",
      cell: (setting) => (
        <span className="flex min-w-0 flex-col gap-0.5">
          <Mono tone="default" truncate>
            {setting.name}
          </Mono>
          <span className="text-12 text-fg-muted">
            {kindWord(t, setting.resource, kinds.find((kind) => kind.key === setting.resource)?.title ?? setting.resource)}
            {setting.exceptions.length > 0 ? ` (${t("fleetSettings.cell.exception")} ${String(setting.exceptions.length)})` : ""}
          </span>
        </span>
      ),
    },
    ...status.servers.map(
      (server): Column<StatusSetting> => ({
        id: `server-${server.node}`,
        header: server.node,
        card: "meta",
        cell: (setting) => <CellFor t={t} setting={setting} node={server.node} canManage={canManage} />,
      }),
    ),
    {
      id: "changed",
      header: t("fleetSettings.table.changed"),
      hideBelow: "lg",
      card: "hidden",
      cell: (setting) => (
        <span className="flex min-w-0 flex-col text-12 text-fg-muted">
          <span className="truncate">{setting.changed_by}</span>
          <RelativeTime value={setting.changed_at} />
        </span>
      ),
    },
  ];
}

/**
 * Settings > Fleet settings (item 90): what the fleet shares, written once on the central, and where each server
 * stands: it follows the fleet, it has an exception of its own, or it is independent and is pushed nothing. One
 * button, "Apply to the fleet", asks every server what it holds and gives it what the fleet says, with each server's
 * progress. A secret goes in and never comes out. What belongs to the machine (the web server, SSH, ports) is never
 * pushed and is not here.
 */
export function FleetSettings() {
  const t = useT();
  useDocumentTitle(t("fleetSettings.documentTitle"), 1);
  const queryClient = useQueryClient();
  const { data: session } = useQuery(sessionQuery());
  const list = useQuery(fleetSettingsQuery());
  const status = useQuery(fleetStatusQuery());
  const [open, setOpen] = useState<Open>(null);
  const canManage = session?.permissions?.includes("fleet.manage") ?? false;
  const waiting = pendingCount(status.data);
  const items = list.data?.items ?? [];
  const servers = (status.data?.servers ?? []).map((server) => server.node);

  const rows = status.data?.settings ?? [];
  const columns = status.data !== undefined && list.data !== undefined ? columnsFor(t, status.data, list.data.kinds, canManage) : [];

  return (
    <Sections>
      {canManage ? (
        <SettingsPrimaryAction>
          <Button variant="primary" onClick={() => setOpen({ kind: "apply" })}>
            {t("fleetSettings.applyDialog.title")}
            {waiting > 0 ? <Badge>{waiting}</Badge> : null}
          </Button>
        </SettingsPrimaryAction>
      ) : null}
      <div className="flex min-w-0 flex-col gap-4">
        <p className="max-w-measure text-14 text-pretty text-fg-muted">{t("fleetSettings.lead")}</p>
        <Levels t={t} />
      </div>
      <Section title={t("fleetSettings.servers.title")} icon={OBJECT_ICONS.node} description={t("fleetSettings.servers.followHelp")}>
        <QueryState query={status} label={t("fleetSettings.loading")} skeleton={<Skeleton className="h-24 w-full rounded-card" />}>
          {(data) => <Servers t={t} servers={data.servers} canManage={canManage} />}
        </QueryState>
      </Section>
      <Section
        title={t("fleetSettings.table.title")}
        icon={OBJECT_ICONS.fleetSettings}
        {...(canManage
          ? {
              actions: (
                <Button size="sm" variant="secondary" icon={<ICONS.add aria-hidden="true" />} disabled={list.data === undefined} onClick={() => setOpen({ kind: "share", setting: null, node: null })}>
                  {t("fleetSettings.actions.share")}
                </Button>
              ),
            }
          : {})}
      >
        {list.isError ? (
          <ErrorBlock compact error={list.error} title={t("fleetSettings.loading")} onRetry={() => void list.refetch()} />
        ) : status.isError ? null : (
          <DataTable
            caption={t("fleetSettings.table.caption")}
            columns={columns}
            rows={status.data === undefined || list.data === undefined ? undefined : rows}
            getRowId={(setting) => `${setting.resource}/${setting.name}`}
            skeletonRows={2}
            empty={<EmptyState variant="inline" title={t("fleetSettings.empty.title")} description={t("fleetSettings.empty.description")} />}
            {...(canManage
              ? {
                  rowActions: (setting: StatusSetting) => {
                    const fleet = items.find((item) => item.resource === setting.resource && item.name === setting.name && item.scope === "fleet") ?? null;
                    return (
                      <RowMenu name={setting.name}>
                        <MenuItem onClick={() => setOpen({ kind: "share", setting: fleet, node: null })}>{t("fleetSettings.actions.edit")}</MenuItem>
                        {servers.length > 0 ? <MenuItem onClick={() => setOpen({ kind: "share", setting: fleet, node: servers[0] ?? null })}>{t("fleetSettings.actions.exception")}</MenuItem> : null}
                        {setting.exceptions.map((exception) =>
                          exception.node === undefined ? null : (
                            <MenuItem key={exception.node} onClick={() => setOpen({ kind: "remove", setting, scope: `node:${exception.node ?? ""}` })}>
                              {t("fleetSettings.actions.removeException", { server: exception.node })}
                            </MenuItem>
                          ),
                        )}
                        <MenuSeparator />
                        <MenuItem icon={<ICONS.delete />} destructive onClick={() => setOpen({ kind: "remove", setting, scope: "fleet" })}>
                          {t("fleetSettings.actions.remove")}
                        </MenuItem>
                      </RowMenu>
                    );
                  },
                }
              : {})}
          />
        )}
      </Section>
      <CommandHint command="noust fleet status" label={t("common.words.fromTerminal")} />

      {open?.kind === "share" && list.data !== undefined ? <ShareDialog kinds={list.data.kinds} setting={open.setting} servers={servers} node={open.node} onClose={() => setOpen(null)} /> : null}
      {open?.kind === "apply" ? <ApplyDialog onClose={() => setOpen(null)} /> : null}
      {open?.kind === "remove" ? (
        <ConfirmDialog
          friction="simple"
          destructive
          open
          onOpenChange={(next) => {
            if (!next) setOpen(null);
          }}
          title={open.scope === "fleet" ? t("fleetSettings.remove.title", { name: open.setting.name }) : t("fleetSettings.remove.exceptionTitle", { name: open.setting.name, server: open.scope.replace(/^node:/, "") })}
          description={open.scope === "fleet" ? t("fleetSettings.remove.description") : t("fleetSettings.remove.exceptionDescription", { server: open.scope.replace(/^node:/, "") })}
          actionLabel={t("common.words.remove")}
          onConfirm={async () => {
            await removeSetting(open.setting.resource, open.setting.name, open.scope);
            void queryClient.invalidateQueries({ queryKey: fleetSettingsKeys.all });
          }}
        />
      ) : null}
    </Sections>
  );
}
