import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Download, Play, Plus, RotateCw, ScrollText, SlidersHorizontal, Square, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import type { ReactNode } from "react";

import { request } from "../../api/client";
import { databaseKeys, databasesQuery, engineLogsQuery, enginesQuery, exposureQuery } from "../../api/queries/databases";
import type { Engine } from "../../api/queries/databases";
import { jobKeys, useFollowedJob } from "../../api/queries/jobs";
import { CommandHint } from "../../components/page/CommandHint";
import { ListPage } from "../../components/page/ListPage";
import { ErrorBlock } from "../../components/page/QueryState";
import { Section } from "../../components/page/Section";
import { Button } from "../../components/ui/Button";
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
import { can, containerCounts, engineFamily, engineName, engineState, instancePlace, isContainer, sortEngines, sortInstances, supportText, supportView } from "./engines";
import { InstallEngineDialog } from "./InstallEngineDialog";
import { DatabaseJobProgress } from "./jobs";
import { useDatabasesHeader } from "./listHeader";
import { TEXT_LINK } from "./ui";

type EngineVerb = "start" | "stop" | "restart";

/** The engine's journal (or a container's log), verbatim, in a drawer: why it will not start is usually there. */
function EngineLogs({ engine, onClose }: { engine: Engine; onClose: () => void }) {
  const t = useT();
  const logs = useQuery(engineLogsQuery(engine.name));
  const lines = useMemo(() => (logs.data?.logs ?? "").split("\n").map((text, id) => ({ id, text })), [logs.data]);
  const name = isContainer(engine) ? instancePlace(engine) : engine.display_name;
  const unit = isContainer(engine) ? engine.container : engine.service;
  return (
    <Drawer
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      size="lg"
      title={t("databases.engines.logsTitle", { engine: name })}
      description={unit ? <Mono>{unit}</Mono> : undefined}
    >
      {logs.isError && logs.data === undefined ? (
        <ErrorBlock compact error={logs.error} title={t("databases.engines.logsFailed")} onRetry={() => void logs.refetch()} />
      ) : logs.data === undefined ? (
        <div aria-busy="true">
          <span className="sr-only">{t("databases.engines.logsLoading")}</span>
          <Skeleton className="h-80 w-full rounded-card" />
        </div>
      ) : (
        <LogViewer lines={lines} label={t("databases.engines.logsLabel", { engine: name })} filename={`${engine.name}.log`} height={560} />
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

/** How an engine is named in a toast or a title: a container by where it runs. */
function nameOf(engine: Engine): string {
  return isContainer(engine) ? instancePlace(engine) : engine.display_name;
}

/** What Noust may do inside a container, in words; a limited or refused access also by its glyph. */
function AccessCell({ engine, t }: { engine: Engine; t: T }) {
  const Warning = ICONS.warning;
  const Error = ICONS.error;
  switch (engine.access) {
    case "full":
      return <span className="text-fg-muted">{t("databases.instances.accessFull")}</span>;
    case "limited":
      return (
        <span className="flex min-w-0 items-center gap-1.5 whitespace-normal">
          <Warning aria-hidden="true" className="size-icon-sm shrink-0 text-warn" />
          <span className="text-fg">{t("databases.instances.accessLimited")}</span>
        </span>
      );
    case "refused":
      return (
        <span className="flex min-w-0 items-center gap-1.5">
          <Error aria-hidden="true" className="size-icon-sm shrink-0 text-fail" />
          <span className="text-fg">{t("databases.instances.accessRefused")}</span>
        </span>
      );
    default:
      return <EmptyCell reason={t("databases.instances.accessUnknown")} />;
  }
}

/**
 * The engines tab of Databases: every engine Noust can run on this server, installed or not,
 * its state, its version and where that version stands upstream, with its controls in the
 * row's menu; then the engines running in containers, which their image decides and Noust
 * neither installs nor configures. What the operator must know about an installation (a
 * MongoDB without authorization, a Redis without a password) is the one notice above; an
 * installation is followed where it started.
 */
export function EnginesPage() {
  const t = useT();
  const { node } = useNode();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { header, tabs, dialogs } = useDatabasesHeader();
  const engines = useQuery(enginesQuery());
  const databases = useQuery(databasesQuery());
  const exposure = useQuery(exposureQuery());
  const followed = useFollowedJob();
  const [jobEngine, setJobEngine] = useState<{ name: string; install: boolean } | null>(null);
  const [logsOf, setLogsOf] = useState<Engine | null>(null);
  const [uninstalling, setUninstalling] = useState<Engine | null>(null);
  const [creatingOn, setCreatingOn] = useState<string | null>(null);
  // `null`: closed; otherwise open, with the engine whose row opened it, if any.
  const [installing, setInstalling] = useState<{ engine?: string } | null>(null);
  // A container that is an application's database is stopped or restarted only once asked.
  const [interrupting, setInterrupting] = useState<{ engine: Engine; verb: "stop" | "restart" } | null>(null);

  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: databaseKeys.engines });
    void queryClient.invalidateQueries({ queryKey: databaseKeys.lists });
  };

  const runVerb = async ({ engine, verb }: { engine: Engine; verb: EngineVerb }): Promise<void> => {
    await request("post", `/api/databases/engines/{engine}/${verb}`, { params: { engine: engine.name } });
    refresh();
    toast.success(verbWords(t, verb, nameOf(engine)).done);
  };

  const control = useMutation({
    mutationFn: runVerb,
    onError: (error, { engine, verb }) => reportActionError(verbWords(t, verb, nameOf(engine)).failed, error),
  });

  const act = (engine: Engine, verb: EngineVerb): void => {
    if (verb !== "start" && isContainer(engine) && engine.app) setInterrupting({ engine, verb });
    else control.mutate({ engine, verb });
  };

  const counts = useMemo(() => {
    const byEngine = new Map<string, number>();
    for (const database of databases.data?.databases ?? []) byEngine.set(database.engine, (byEngine.get(database.engine) ?? 0) + 1);
    return byEngine;
  }, [databases.data]);

  const all = engines.data?.engines ?? [];
  const hosts = sortEngines(all.filter((engine) => !isContainer(engine)));
  const containers = sortInstances(all.filter(isContainer));
  const warnings = all.flatMap((engine) => (engine.warnings ?? []).map((warning) => ({ engine: nameOf(engine), warning })));
  const firewalled = exposure.data?.firewalled ?? [];
  const inContainers = containerCounts(all);

  const stateColumn: Column<Engine> = {
    id: "state",
    header: t("databases.engines.state"),
    width: "w-36",
    card: "status",
    cell: (engine) => {
      const view = engineState(engine);
      return <StatusPill appearance="inline" size="sm" state={view.state} label={t(view.label)} />;
    },
  };
  // A server's engine that is not installed may still run in a container: said beside its state,
  // so "Redis, not installed" above "Redis, running" below does not read as a contradiction.
  const hostStateColumn: Column<Engine> = {
    ...stateColumn,
    cell: (engine) => {
      const family = engineFamily(engine);
      const elsewhere = engine.installed || family === null ? 0 : (inContainers.get(family) ?? 0);
      return (
        <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 whitespace-normal">
          {stateColumn.cell(engine)}
          {elsewhere > 0 ? <span className="text-12 text-fg-muted">{t("databases.instances.inContainers", { count: elsewhere })}</span> : null}
        </span>
      );
    },
  };
  const countColumn: Column<Engine> = {
    id: "databases",
    header: t("databases.engines.databases"),
    align: "end",
    mono: true,
    width: "w-28",
    cell: (engine) => (engine.running ? String(counts.get(engine.name) ?? 0) : <EmptyCell reason={t("databases.engines.notRunning")} />),
  };

  const columns: Column<Engine>[] = [
    {
      id: "engine",
      header: t("databases.engines.engine"),
      card: "title",
      cell: (engine) => <span className="font-medium text-fg">{engine.display_name}</span>,
    },
    hostStateColumn,
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
    countColumn,
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

  const containerColumns: Column<Engine>[] = [
    {
      id: "container",
      header: t("databases.instances.container"),
      card: "title",
      cell: (engine) => (
        <Mono tone="default" truncate>
          {instancePlace(engine)}
        </Mono>
      ),
    },
    stateColumn,
    {
      id: "engine",
      header: t("databases.instances.engine"),
      width: "w-40",
      cell: (engine) => (
        <span className="flex items-center gap-1.5">
          <span>{engine.display_name}</span>
          {engine.version ? <Mono tone="muted">{engine.version}</Mono> : null}
        </span>
      ),
    },
    {
      id: "image",
      header: t("databases.instances.image"),
      hideBelow: "md",
      cell: (engine) =>
        engine.image ? (
          <Mono tone="muted" truncate>
            {engine.image}
          </Mono>
        ) : (
          <EmptyCell reason={t("databases.instances.noImage")} />
        ),
    },
    {
      id: "app",
      header: t("databases.instances.app"),
      hideBelow: "sm",
      cell: (engine) =>
        engine.app ? (
          <Link to="/apps/$domain" params={{ domain: engine.app }} translate="no" className={`${TEXT_LINK} truncate`}>
            {engine.app}
          </Link>
        ) : (
          <EmptyCell reason={t("databases.instances.noApp")} />
        ),
    },
    {
      id: "access",
      header: t("databases.instances.access"),
      width: "w-48",
      hideBelow: "md",
      cell: (engine) => <AccessCell engine={engine} t={t} />,
    },
    countColumn,
  ];

  // While an installation or removal is followed, another is not offered.
  const busy = jobEngine !== null;
  const rowActions = (engine: Engine) => {
    const container = isContainer(engine);
    return (
      <Menu align="end" trigger={<IconButton label={t("databases.engines.actionsFor", { engine: nameOf(engine) })} icon={<ICONS.more />} size="sm" tooltip={false} />}>
        {!engine.installed && !container ? (
          <MenuItem icon={<Download />} disabled={busy} onClick={() => setInstalling({ engine: engine.name })}>
            {t("databases.engines.install")}
          </MenuItem>
        ) : (
          <>
            {engine.running ? (
              <MenuItem icon={<Plus />} disabled={!(can(engine, "sql") || can(engine, "documents"))} onClick={() => setCreatingOn(engine.name)}>
                {t("databases.engines.newDatabase")}
              </MenuItem>
            ) : (
              <MenuItem icon={<Play />} disabled={control.isPending} onClick={() => act(engine, "start")}>
                {t("databases.engines.start")}
              </MenuItem>
            )}
            {!container ? (
              <MenuItem icon={<SlidersHorizontal />} onClick={() => void navigate({ to: "/databases/engines/$engine/settings", params: { engine: engine.name } })}>
                {t("databases.engines.settings")}
              </MenuItem>
            ) : null}
            <MenuItem icon={<ScrollText />} onClick={() => setLogsOf(engine)}>
              {t("databases.engines.logs")}
            </MenuItem>
            <MenuSeparator />
            <MenuItem icon={<RotateCw />} disabled={control.isPending} onClick={() => act(engine, "restart")}>
              {t("databases.engines.restart")}
            </MenuItem>
            {engine.running ? (
              <MenuItem icon={<Square />} disabled={control.isPending} onClick={() => act(engine, "stop")}>
                {t("databases.engines.stop")}
              </MenuItem>
            ) : null}
            {!container ? (
              <>
                <MenuSeparator />
                <MenuItem icon={<Trash2 />} destructive onClick={() => setUninstalling(engine)}>
                  {t("databases.engines.uninstall")}
                </MenuItem>
              </>
            ) : null}
          </>
        )}
      </Menu>
    );
  };

  const job = followed.job;
  let notice: ReactNode;
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

  const limited = containers.some((engine) => engine.access === "limited");
  // Untitled while it is the only list; once there are containers it says which engines these are.
  const hostEngines = (
    <div className="flex min-w-0 flex-col gap-3">
      <DataTable
        columns={columns}
        rows={hosts}
        getRowId={(engine) => engine.name}
        caption={t("databases.engines.caption")}
        rowActions={rowActions}
        loading={engines.isPending}
        skeletonRows={4}
        mobile="cards"
      />
      {firewalled.length > 0 ? (
        <p className="max-w-measure text-12 text-fg-muted">
          {t("databases.engines.firewalled", {
            count: firewalled.length,
            ports: firewalled.map((port) => `${port.container ?? engineName(port.engine, all)} ${port.address}:${String(port.port)}`).join(", "),
          })}
        </p>
      ) : null}
    </div>
  );
  return (
    <ListPage
      header={{
        ...header,
        secondaryActions: (
          <Button icon={<Download aria-hidden="true" />} disabled={busy} onClick={() => setInstalling({})}>
            {t("databases.engines.installEngine")}
          </Button>
        ),
      }}
      tabs={tabs}
      {...(notice !== undefined ? { notice } : {})}
      {...(engines.isPending ? {} : { footer: <CommandHint command="noust db engines" label={t("databases.common.fromTerminal")} /> })}
    >
      {engines.isError && engines.data === undefined ? (
        <ErrorBlock error={engines.error} title={t("databases.engines.couldNotLoad")} onRetry={() => void engines.refetch()} retrying={engines.isRefetching} />
      ) : (
        <div className="flex min-w-0 flex-col gap-8">
          {containers.length > 0 ? (
            <Section title={t("databases.instances.hostTitle")} description={t("databases.instances.hostDescription")}>
              {hostEngines}
            </Section>
          ) : (
            hostEngines
          )}
          {containers.length > 0 ? (
            <Section title={t("databases.instances.title")} description={t("databases.instances.description")}>
              <DataTable
                columns={containerColumns}
                rows={containers}
                getRowId={(engine) => engine.name}
                caption={t("databases.instances.caption")}
                rowActions={rowActions}
                mobile="cards"
              />
              {limited ? <p className="max-w-measure text-12 text-fg-muted">{t("databases.instances.limitedNote")}</p> : null}
            </Section>
          ) : null}
        </div>
      )}
      {dialogs}
      {logsOf !== null ? <EngineLogs engine={logsOf} onClose={() => setLogsOf(null)} /> : null}
      <CreateDatabaseDialog key={creatingOn ?? ""} open={creatingOn !== null} onOpenChange={(open) => (open ? undefined : setCreatingOn(null))} {...(creatingOn !== null ? { engine: creatingOn } : {})} />
      {installing !== null ? (
        <InstallEngineDialog
          open
          onOpenChange={(open) => (open ? undefined : setInstalling(null))}
          {...(installing.engine !== undefined ? { engine: installing.engine } : {})}
          onQueued={(accepted, name) => {
            setJobEngine({ name, install: true });
            followed.follow(accepted.job_id);
            void queryClient.invalidateQueries({ queryKey: jobKeys.active });
          }}
        />
      ) : null}
      {interrupting !== null ? (
        <ConfirmDialog
          open
          friction="simple"
          onOpenChange={(open) => (open ? undefined : setInterrupting(null))}
          title={
            interrupting.verb === "stop"
              ? t("databases.instances.stopTitle", { place: instancePlace(interrupting.engine) })
              : t("databases.instances.restartTitle", { place: instancePlace(interrupting.engine) })
          }
          description={
            interrupting.verb === "stop"
              ? t("databases.instances.stopDescription", { place: instancePlace(interrupting.engine), app: interrupting.engine.app ?? "" })
              : t("databases.instances.restartDescription", { place: instancePlace(interrupting.engine), app: interrupting.engine.app ?? "" })
          }
          actionLabel={interrupting.verb === "stop" ? t("databases.instances.stopAction") : t("databases.instances.restartAction")}
          server={node}
          onConfirm={() => runVerb(interrupting)}
        />
      ) : null}
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
