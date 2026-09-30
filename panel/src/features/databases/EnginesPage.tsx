import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Play, Plus, RotateCw, ScrollText, Square, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import { request } from "../../api/client";
import { databaseKeys, databasesQuery, engineLogsQuery, enginesQuery } from "../../api/queries/databases";
import type { Engine } from "../../api/queries/databases";
import { jobKeys, useFollowedJob } from "../../api/queries/jobs";
import { CommandHint } from "../../components/page/CommandHint";
import { ListPage } from "../../components/page/ListPage";
import { ErrorBlock } from "../../components/page/QueryState";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { Drawer } from "../../components/ui/Drawer";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { IconButton } from "../../components/ui/IconButton";
import { ICONS } from "../../components/ui/icons";
import { LogViewer } from "../../components/ui/LogViewer";
import { Menu, MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { reportActionError } from "../apps/useAppActions";
import { CreateDatabaseDialog } from "./CreateDatabaseDialog";
import { can, engineState, sortEngines, supportText, supportView } from "./engines";
import { DatabaseJobProgress } from "./jobs";
import { useDatabasesHeader } from "./listHeader";

type EngineVerb = "start" | "stop" | "restart";

/** The engine's journal, verbatim, in a drawer: why it will not start is usually there. */
function EngineLogs({ engine, onClose }: { engine: Engine; onClose: () => void }) {
  const t = useT();
  const logs = useQuery(engineLogsQuery(engine.name));
  const lines = useMemo(() => (logs.data?.logs ?? "").split("\n").map((text, id) => ({ id, text })), [logs.data]);
  return (
    <Drawer
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      size="lg"
      title={t("databases.engines.logsTitle", { engine: engine.display_name })}
      description={engine.service ? <Mono>{engine.service}</Mono> : undefined}
    >
      {logs.isError && logs.data === undefined ? (
        <ErrorBlock compact error={logs.error} title={t("databases.engines.logsFailed")} onRetry={() => void logs.refetch()} />
      ) : logs.data === undefined ? (
        <div aria-busy="true">
          <span className="sr-only">{t("databases.engines.logsLoading")}</span>
          <Skeleton className="h-80 w-full rounded-card" />
        </div>
      ) : (
        <LogViewer lines={lines} label={t("databases.engines.logsLabel", { engine: engine.display_name })} filename={`${engine.name}.log`} height={560} />
      )}
    </Drawer>
  );
}

function verbWords(t: T, verb: EngineVerb, engine: string): { done: string; failed: string } {
  switch (verb) {
    case "start":
      return { done: t("databases.engines.started", { engine }), failed: t("databases.engines.startFailed", { engine }) };
    case "stop":
      return { done: t("databases.engines.stopped", { engine }), failed: t("databases.engines.stopFailed", { engine }) };
    case "restart":
      return { done: t("databases.engines.restarted", { engine }), failed: t("databases.engines.restartFailed", { engine }) };
  }
}

/**
 * The engines tab of Databases: every engine Noust can run, installed or not, its state, its
 * version and where that version stands upstream, with its controls in the row's menu. What
 * the operator must know about an installation (a MongoDB without authorization, a Redis
 * without a password) is the one notice above; an installation is followed where it started.
 */
export function EnginesPage() {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const { header, tabs, dialogs } = useDatabasesHeader();
  const engines = useQuery(enginesQuery());
  const databases = useQuery(databasesQuery());
  const followed = useFollowedJob();
  const [jobEngine, setJobEngine] = useState<{ name: string; install: boolean } | null>(null);
  const [logsOf, setLogsOf] = useState<Engine | null>(null);
  const [uninstalling, setUninstalling] = useState<Engine | null>(null);
  const [creatingOn, setCreatingOn] = useState<string | null>(null);

  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: databaseKeys.engines });
    void queryClient.invalidateQueries({ queryKey: databaseKeys.lists });
  };

  const control = useMutation({
    mutationFn: ({ engine, verb }: { engine: Engine; verb: EngineVerb }) =>
      request("post", `/api/databases/engines/{engine}/${verb}`, { params: { engine: engine.name } }),
    onSuccess: (_result, { engine, verb }) => {
      refresh();
      toast.success(verbWords(t, verb, engine.display_name).done);
    },
    onError: (error, { engine, verb }) => reportActionError(verbWords(t, verb, engine.display_name).failed, error),
  });

  const install = useMutation({
    mutationFn: (engine: Engine) => request("post", "/api/databases/engines/{engine}/install", { params: { engine: engine.name } }),
    onSuccess: (accepted, engine) => {
      setJobEngine({ name: engine.display_name, install: true });
      followed.follow(accepted.job_id);
      void queryClient.invalidateQueries({ queryKey: jobKeys.active });
    },
    onError: (error, engine) => reportActionError(t("databases.engines.installFailed", { engine: engine.display_name }), error),
  });

  const counts = useMemo(() => {
    const byEngine = new Map<string, number>();
    for (const database of databases.data?.databases ?? []) byEngine.set(database.engine, (byEngine.get(database.engine) ?? 0) + 1);
    return byEngine;
  }, [databases.data]);

  const list = sortEngines(engines.data?.engines ?? []);
  const warnings = list.flatMap((engine) => (engine.warnings ?? []).map((warning) => ({ engine: engine.display_name, warning })));

  const columns: Column<Engine>[] = [
    {
      id: "engine",
      header: t("databases.engines.engine"),
      card: "title",
      cell: (engine) => <span className="font-medium text-fg">{engine.display_name}</span>,
    },
    {
      id: "state",
      header: t("databases.engines.state"),
      width: "w-36",
      card: "status",
      cell: (engine) => {
        const view = engineState(engine);
        return <StatusPill appearance="inline" size="sm" state={view.state} label={t(view.label)} />;
      },
    },
    {
      id: "version",
      header: t("databases.engines.version"),
      width: "w-32",
      cell: (engine) => (engine.installed && engine.version ? <Mono>{engine.version}</Mono> : <EmptyCell reason={t("databases.engines.noVersion")} />),
    },
    {
      id: "support",
      header: t("databases.engines.support"),
      hideBelow: "md",
      cell: (engine) => {
        const view = supportView(engine.support);
        const text = engine.support ? supportText(t, engine.support) : null;
        if (view === null || text === null) return <EmptyCell reason={t("databases.engines.noSupport")} />;
        const Warning = ICONS.warning;
        return (
          <span className="flex min-w-0 items-center gap-1.5 whitespace-normal">
            {view.warn ? <Warning aria-hidden="true" className="size-icon-sm shrink-0 text-warn" /> : null}
            <span className={view.warn ? "text-fg" : "text-fg-muted"}>{text}</span>
          </span>
        );
      },
    },
    {
      id: "databases",
      header: t("databases.engines.databases"),
      align: "end",
      mono: true,
      width: "w-28",
      cell: (engine) => (engine.running ? String(counts.get(engine.name) ?? 0) : <EmptyCell reason={t("databases.engines.notRunning")} />),
    },
    {
      id: "port",
      header: t("databases.engines.port"),
      align: "end",
      mono: true,
      width: "w-24",
      hideBelow: "sm",
      cell: (engine) => String(engine.port),
    },
  ];

  const rowActions = (engine: Engine) => (
    <Menu align="end" trigger={<IconButton label={t("databases.engines.actionsFor", { engine: engine.display_name })} icon={<ICONS.more />} size="sm" tooltip={false} />}>
      {!engine.installed ? (
        <MenuItem icon={<Download />} disabled={install.isPending || jobEngine !== null} onClick={() => install.mutate(engine)}>
          {t("databases.engines.install")}
        </MenuItem>
      ) : (
        <>
          {engine.running ? (
            <MenuItem icon={<Plus />} disabled={!(can(engine, "sql") || can(engine, "documents"))} onClick={() => setCreatingOn(engine.name)}>
              {t("databases.engines.newDatabase")}
            </MenuItem>
          ) : (
            <MenuItem icon={<Play />} disabled={control.isPending} onClick={() => control.mutate({ engine, verb: "start" })}>
              {t("databases.engines.start")}
            </MenuItem>
          )}
          <MenuItem icon={<ScrollText />} onClick={() => setLogsOf(engine)}>
            {t("databases.engines.logs")}
          </MenuItem>
          <MenuSeparator />
          <MenuItem icon={<RotateCw />} disabled={control.isPending} onClick={() => control.mutate({ engine, verb: "restart" })}>
            {t("databases.engines.restart")}
          </MenuItem>
          {engine.running ? (
            <MenuItem icon={<Square />} disabled={control.isPending} onClick={() => control.mutate({ engine, verb: "stop" })}>
              {t("databases.engines.stop")}
            </MenuItem>
          ) : null}
          <MenuSeparator />
          <MenuItem icon={<Trash2 />} destructive onClick={() => setUninstalling(engine)}>
            {t("databases.engines.uninstall")}
          </MenuItem>
        </>
      )}
    </Menu>
  );

  const job = followed.job;
  let notice;
  if (job !== null && jobEngine !== null) {
    notice = (
      <DatabaseJobProgress
        job={job}
        words={
          jobEngine.install
            ? {
                running: t("databases.engines.installing", { engine: jobEngine.name }),
                succeeded: t("databases.engines.installed", { engine: jobEngine.name }),
                failed: t("databases.engines.installFailed", { engine: jobEngine.name }),
              }
            : {
                running: t("databases.engines.uninstalling", { engine: jobEngine.name }),
                succeeded: t("databases.engines.uninstalled", { engine: jobEngine.name }),
                failed: t("databases.engines.uninstallFailed", { engine: jobEngine.name }),
              }
        }
        onDismiss={() => {
          followed.dismiss();
          setJobEngine(null);
          refresh();
        }}
      />
    );
  } else if (warnings.length > 0) {
    notice = (
      <Notice tone="warning" title={t("databases.engines.warningsTitle", { count: warnings.length })}>
        <ul className="flex flex-col gap-1">
          {warnings.map(({ engine, warning }) => (
            <li key={`${engine}:${warning}`}>
              <span className="font-medium text-fg">{engine}</span> <span translate="no">{warning}</span>
            </li>
          ))}
        </ul>
      </Notice>
    );
  }

  return (
    <ListPage
      header={header}
      tabs={tabs}
      {...(notice !== undefined ? { notice } : {})}
      {...(engines.isPending ? {} : { footer: <CommandHint command="noust db engines" label={t("databases.common.fromTerminal")} /> })}
    >
      {engines.isError && engines.data === undefined ? (
        <ErrorBlock error={engines.error} title={t("databases.engines.couldNotLoad")} onRetry={() => void engines.refetch()} retrying={engines.isRefetching} />
      ) : (
        <DataTable
          columns={columns}
          rows={list}
          getRowId={(engine) => engine.name}
          caption={t("databases.engines.caption")}
          rowActions={rowActions}
          loading={engines.isPending}
          skeletonRows={4}
          mobile="cards"
        />
      )}
      {dialogs}
      {logsOf !== null ? <EngineLogs engine={logsOf} onClose={() => setLogsOf(null)} /> : null}
      <CreateDatabaseDialog key={creatingOn ?? ""} open={creatingOn !== null} onOpenChange={(open) => (open ? undefined : setCreatingOn(null))} {...(creatingOn !== null ? { engine: creatingOn } : {})} />
      {uninstalling !== null ? (
        <ConfirmDialog
          open
          onOpenChange={(open) => (open ? undefined : setUninstalling(null))}
          title={t("databases.engines.uninstallTitle", { engine: uninstalling.display_name })}
          description={t("databases.engines.uninstallDescription", { engine: uninstalling.display_name, count: counts.get(uninstalling.name) ?? 0 })}
          actionLabel={t("databases.engines.uninstallAction", { engine: uninstalling.display_name })}
          confirmText={uninstalling.name}
          server={node}
          onConfirm={async () => {
            const accepted = await request("post", "/api/databases/engines/{engine}/uninstall", { params: { engine: uninstalling.name }, query: { purge: false } });
            setJobEngine({ name: uninstalling.display_name, install: false });
            followed.follow(accepted.job_id);
            void queryClient.invalidateQueries({ queryKey: jobKeys.active });
          }}
        />
      ) : null}
    </ListPage>
  );
}
