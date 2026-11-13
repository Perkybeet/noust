/**
 * What a person can do to a database server, in one place: the menu of its slab, the job it queues
 * (followed in the page's notice slot), and the dialogs and questions behind each entry. A server is
 * one of three things, and the menu says only what its kind can do: the system's own engine (start,
 * stop, settings, uninstall), a container of Noust's (also its image, its TLS and its limits, and
 * removing it) and an application's own container, which is listed and nothing more.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpFromLine, CopyPlus, Lock, Play, Square, Star } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";

import { request } from "../../../api/client";
import { instancesQuery } from "../../../api/queries/databaseInstances";
import type { DbInstance } from "../../../api/queries/databaseInstances";
import { databaseKeys } from "../../../api/queries/databases";
import type { DbServer } from "../../../api/queries/databases";
import { jobKeys } from "../../../api/queries/jobs";
import { useNavigate } from "@tanstack/react-router";
import { useCan } from "../../../components/page/permissions";
import { RowMenu } from "../../../components/page/RowMenu";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { ICONS } from "../../../components/ui/icons";
import { MenuItem, MenuSeparator } from "../../../components/ui/Menu";
import { OBJECT_ICONS, VERB_ICONS } from "../../../components/ui/objectIcons";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { InstallEngineDialog } from "../InstallEngineDialog";
import { useInstanceActions } from "../instances/useInstanceActions";
import type { InstanceActions } from "../instances/useInstanceActions";
import { canMoveUp, serverTitle } from "../servers";
import { AddEngineDialog } from "./AddEngineDialog";
import { ServerLogs } from "./ServerLogs";

export interface ServerActions {
  /** The menu at the end of a slab. */
  menu: (server: DbServer) => ReactNode;
  /** Opens the wizard that moves a server's databases to a newer major version. */
  upgrade: (server: DbServer) => void;
  /** The job in hand, or null. */
  notice: ReactNode;
  /** The dialogs and questions, drawn once anywhere in the page. */
  dialogs: ReactNode;
  /** "Another version", on the engine of the server whose menu opened it, if any. */
  other: InstanceActions["openCreate"];
  /** "Install an engine" of the system's own. */
  install: () => void;
  /** "Add an engine": asks which way (the system's, or a container) and goes on in the dialog of that one. */
  add: () => void;
  /** Starts a stopped engine: the system's own at once, a container as a job the notice follows. */
  start: (server: DbServer) => void;
  /** An engine is starting: the button that asked says so and is not pressed twice. */
  starting: boolean;
  /** Whether a job is followed: another is not offered until it is dismissed. */
  busy: boolean;
  /** Follows a job a menu queued, in the notice slot. */
  follow: InstanceActions["follow"];
}

/** The kind of slab a menu is drawn for. */
function isNoustContainer(server: DbServer): boolean {
  return server.runs_in === "noust_container";
}

export function useServerActions(): ServerActions {
  const t = useT();
  const { node } = useNode();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const instanceActions = useInstanceActions();
  const instances = useQuery(instancesQuery());
  // What a menu offers is what the person may do: starting and stopping take `apps.operate`, the rest of an engine `databases.manage`.
  const operate = useCan("apps.operate");
  const manage = useCan("databases.manage");
  const [logsOf, setLogsOf] = useState<DbServer | null>(null);
  const [uninstalling, setUninstalling] = useState<DbServer | null>(null);
  const [removing, setRemoving] = useState<DbServer | null>(null);
  const [refreshing, setRefreshing] = useState<DbServer | null>(null);
  // `null`: closed; otherwise open, on the engine of the row it was opened from, if any.
  const [installing, setInstalling] = useState<{ engine?: string } | null>(null);
  const [adding, setAdding] = useState(false);

  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: databaseKeys.servers });
    void queryClient.invalidateQueries({ queryKey: databaseKeys.engines });
    void queryClient.invalidateQueries({ queryKey: databaseKeys.lists });
  };
  const instanceOf = (server: DbServer): DbInstance | undefined => instances.data?.instances.find((item) => item.name === server.name);

  const setDefault = useMutation({
    mutationFn: (server: DbServer) => request("put", "/api/databases/servers/{key}/default", { params: { key: server.key } }),
    onSuccess: refresh,
    onError: (error, server) => reportActionError(t("dbServers.defaultFailed", { name: serverTitle(server) }), error),
  });
  const clearDefault = useMutation({
    mutationFn: (server: DbServer) => request("delete", "/api/databases/servers/{key}/default", { params: { key: server.key } }),
    onSuccess: refresh,
    onError: (error, server) => reportActionError(t("dbServers.clearDefaultFailed", { name: serverTitle(server) }), error),
  });

  const stop = useMutation({
    mutationFn: (server: DbServer) =>
      isNoustContainer(server)
        ? request("post", "/api/databases/instances/{name}/stop", { params: { name: server.name ?? "" } })
        : request("post", "/api/databases/engines/{engine}/stop", { params: { engine: server.key } }),
    // The slab says it: it is stopped now, in its state. A toast would repeat what is on screen.
    onSuccess: refresh,
    onError: (error, server) => reportActionError(t("databases.engines.stopFailed", { engine: serverTitle(server) }), error),
  });
  const start = useMutation({
    mutationFn: async (server: DbServer) => {
      if (isNoustContainer(server)) {
        const accepted = await request("post", "/api/databases/instances/{name}/start", { params: { name: server.name ?? "" } });
        return accepted.job_id;
      }
      await request("post", "/api/databases/engines/{engine}/start", { params: { engine: server.key } });
      return null;
    },
    onSuccess: (jobId, server) => {
      refresh();
      if (jobId !== null) {
        instanceActions.follow(jobId, {
          running: t("databases.instances.starting", { name: server.name ?? server.key }),
          succeeded: t("databases.instances.started", { name: server.name ?? server.key }),
          failed: t("databases.instances.startFailed", { name: server.name ?? server.key }),
        });
      }
    },
    onError: (error, server) => reportActionError(t("databases.engines.startFailed", { engine: serverTitle(server) }), error),
  });

  const upgrade = (server: DbServer): void =>
    instanceActions.openMigrate({
      // A container is named by its instance; the system's own engine by the engine.
      name: isNoustContainer(server) ? (server.name ?? server.key) : server.engine,
      flavour: server.flavour,
      version: server.version ?? null,
      label: serverTitle(server),
    });

  const menu = (server: DbServer): ReactNode => {
    const actions = new Set(server.actions ?? []);
    const title = serverTitle(server);
    const system = server.runs_in === "system";
    const container = isNoustContainer(server);
    const instance = container ? instanceOf(server) : undefined;
    const busy = instanceActions.busy;
    return (
      <RowMenu name={title}>
        {actions.has("set_default") && manage ? (
          <MenuItem icon={<Star />} description={t("dbServers.makeDefaultDescription")} disabled={setDefault.isPending} onClick={() => setDefault.mutate(server)}>
            {t("dbServers.makeDefault")}
          </MenuItem>
        ) : null}
        {actions.has("clear_default") && manage ? (
          <MenuItem icon={<Star />} disabled={clearDefault.isPending} onClick={() => clearDefault.mutate(server)}>
            {t("dbServers.clearDefault")}
          </MenuItem>
        ) : null}
        {actions.has("other_version") && system && manage ? (
          <MenuItem icon={<CopyPlus />} disabled={busy} onClick={() => instanceActions.openCreate(server.flavour)}>
            {t("dbServers.otherVersion", { engine: server.display_name })}
          </MenuItem>
        ) : null}
        {canMoveUp(server) && manage ? (
          <MenuItem icon={<ArrowUpFromLine />} disabled={busy} onClick={() => upgrade(server)}>
            {t("dbServers.upgradeVersion")}
          </MenuItem>
        ) : null}
        {system && server.running ? (
          <MenuItem icon={<OBJECT_ICONS.settings />} onClick={() => void navigate({ to: "/databases/engines/$engine/settings", params: { engine: server.key } })}>
            {t("dbServers.settings")}
          </MenuItem>
        ) : null}
        {container && actions.has("upgrade") && manage ? (
          <MenuItem icon={<VERB_ICONS.refresh />} disabled={busy || !server.running} onClick={() => setRefreshing(server)}>
            {t("dbServers.updateImage")}
          </MenuItem>
        ) : null}
        {instance !== undefined && manage ? (
          <>
            <MenuItem icon={<Lock />} disabled={busy || !server.running} onClick={() => instanceActions.openTls(instance)}>
              {t("databases.tls.open")}
            </MenuItem>
            <MenuItem icon={<OBJECT_ICONS.limits />} disabled={busy || !server.running} onClick={() => instanceActions.openTuning(instance)}>
              {t("databases.tuning.open")}
            </MenuItem>
          </>
        ) : null}
        <MenuItem icon={<OBJECT_ICONS.log />} onClick={() => setLogsOf(server)}>
          {t("dbServers.logs")}
        </MenuItem>
        {actions.has("start") && operate ? (
          <MenuItem icon={<Play />} disabled={start.isPending || busy} onClick={() => start.mutate(server)}>
            {t("common.words.start")}
          </MenuItem>
        ) : null}
        {actions.has("stop") && operate ? (
          <MenuItem icon={<Square />} disabled={stop.isPending} onClick={() => stop.mutate(server)}>
            {t("common.words.stop")}
          </MenuItem>
        ) : null}
        {actions.has("remove") && container && manage ? (
          <>
            <MenuSeparator />
            <MenuItem icon={<ICONS.delete />} destructive disabled={busy} onClick={() => setRemoving(server)}>
              {t("databases.instances.remove")}
            </MenuItem>
          </>
        ) : null}
        {system && manage ? (
          <>
            <MenuSeparator />
            <MenuItem icon={<ICONS.delete />} destructive disabled={busy} onClick={() => setUninstalling(server)}>
              {t("databases.engines.uninstall")}
            </MenuItem>
          </>
        ) : null}
      </RowMenu>
    );
  };

  const dialogs = (
    <>
      {instanceActions.dialogs}
      {adding ? <AddEngineDialog onClose={() => setAdding(false)} onSystem={() => setInstalling({})} onContainer={() => instanceActions.openCreate()} /> : null}
      {logsOf !== null ? <ServerLogs server={logsOf} onClose={() => setLogsOf(null)} /> : null}
      {installing !== null ? (
        <InstallEngineDialog
          open
          onOpenChange={(open) => (open ? undefined : setInstalling(null))}
          {...(installing.engine !== undefined ? { engine: installing.engine } : {})}
          onQueued={(accepted, name) => {
            void queryClient.invalidateQueries({ queryKey: jobKeys.active });
            instanceActions.follow(accepted.job_id, {
              running: t("databases.engines.installing", { engine: name }),
              succeeded: t("databases.engines.installed", { engine: name }),
              failed: t("databases.engines.installFailed", { engine: name }),
            });
          }}
        />
      ) : null}
      {uninstalling !== null ? (
        <ConfirmDialog
          friction="type"
          open
          onOpenChange={(open) => (open ? undefined : setUninstalling(null))}
          title={t("databases.engines.uninstallTitle", { engine: uninstalling.display_name })}
          description={t("databases.engines.uninstallDescription", { engine: uninstalling.display_name, count: uninstalling.databases ?? 0 })}
          actionLabel={t("databases.engines.uninstallAction", { engine: uninstalling.display_name })}
          confirmText={uninstalling.key}
          server={node}
          onConfirm={async () => {
            const accepted = await request("post", "/api/databases/engines/{engine}/uninstall", { params: { engine: uninstalling.key }, query: { purge: false } });
            void queryClient.invalidateQueries({ queryKey: jobKeys.active });
            instanceActions.follow(accepted.job_id, {
              running: t("databases.engines.uninstalling", { engine: uninstalling.display_name }),
              succeeded: t("databases.engines.uninstalled", { engine: uninstalling.display_name }),
              failed: t("databases.engines.uninstallFailed", { engine: uninstalling.display_name }),
            });
          }}
        />
      ) : null}
      {refreshing !== null ? (
        <ConfirmDialog
          open
          friction="simple"
        destructive={false}
          onOpenChange={(open) => (open ? undefined : setRefreshing(null))}
          server={node}
          title={t("databases.instances.upgradeTitle", { name: refreshing.name ?? refreshing.key })}
          description={t("databases.instances.upgradeDescription", { version: refreshing.version ?? "" })}
          actionLabel={t("databases.instances.upgradeAction")}
          onConfirm={async () => {
            const name = refreshing.name ?? refreshing.key;
            const accepted = await request("post", "/api/databases/instances/{name}/upgrade", { params: { name }, query: { backup: true }, body: null });
            instanceActions.follow(accepted.job_id, {
              running: t("databases.instances.upgrading", { name }),
              succeeded: t("databases.instances.upgraded", { name }),
              failed: t("databases.instances.upgradeFailed", { name }),
            });
          }}
        />
      ) : null}
      {removing !== null ? (
        <ConfirmDialog
          friction="type"
          open
          onOpenChange={(open) => (open ? undefined : setRemoving(null))}
          server={node}
          title={t("common.words.removeNamedQuestion", { name: removing.name ?? removing.key })}
          description={t("databases.instances.removeDescription")}
          actionLabel={t("databases.instances.removeAction")}
          confirmText={removing.name ?? removing.key}
          onConfirm={async () => {
            const name = removing.name ?? removing.key;
            const accepted = await request("delete", "/api/databases/instances/{name}", { params: { name }, query: { confirm: true } });
            instanceActions.follow(accepted.job_id, {
              running: t("databases.instances.removing", { name }),
              succeeded: t("databases.instances.removed", { name }),
              failed: t("databases.instances.removeFailed", { name }),
            });
          }}
        />
      ) : null}
    </>
  );

  return {
    menu,
    upgrade,
    notice: instanceActions.notice,
    dialogs,
    other: instanceActions.openCreate,
    install: () => setInstalling({}),
    add: () => setAdding(true),
    start: (server) => start.mutate(server),
    starting: start.isPending,
    busy: instanceActions.busy,
    follow: instanceActions.follow,
  };
}
