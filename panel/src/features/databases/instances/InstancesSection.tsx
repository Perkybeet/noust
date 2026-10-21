import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpFromLine, Gauge, Lock, Play, Plus, Replace, Square, Trash2, Undo2 } from "lucide-react";
import { useState } from "react";

import { request } from "../../../api/client";
import { instanceKeys, instancesQuery, migrationsQuery } from "../../../api/queries/databaseInstances";
import type { DbInstance, Migration } from "../../../api/queries/databaseInstances";
import { databaseKeys } from "../../../api/queries/databases";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Section } from "../../../components/page/Section";
import { Subsection } from "../../../components/page/Subsection";
import { RowMenu } from "../../../components/page/RowMenu";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { DataTable } from "../../../components/ui/DataTable";
import { EmptyState } from "../../../components/ui/EmptyState";
import type { Column } from "../../../components/ui/DataTable";
import { MenuItem, MenuSeparator } from "../../../components/ui/Menu";
import { Mono } from "../../../components/ui/Mono";
import { StatusPill } from "../../../components/ui/StatusPill";
import type { Status } from "../../../components/ui/StatusPill";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { olderServer } from "../../app/settings/queries";
import { reportActionError } from "../../apps/useAppActions";
import { modeWord } from "./TlsDialog";
import type { InstanceActions } from "./useInstanceActions";

/** What Docker says of an instance, as the state the console draws and its word. */
function stateOf(t: T, instance: DbInstance): { state: "running" | "stopped" | "deploying" | "unknown"; word: string } {
  if (instance.state !== "ready") return { state: "deploying", word: t("databases.instances.states.changing") };
  if (instance.running) return { state: "running", word: t("databases.instances.states.running") };
  if (instance.status === "stopped") return { state: "stopped", word: t("databases.instances.states.stopped") };
  return { state: "unknown", word: t("databases.instances.states.other") };
}

/** A move's state in words. */
/** The state a move is in, as the status vocabulary tells it (colour, shape and word). */
function moveStatus(state: string): Status {
  switch (state) {
    case "done":
      return "running";
    case "failed":
      return "failed";
    case "rolled_back":
      return "stopped";
    default:
      return "deploying";
  }
}

function moveState(t: T, state: string): string {
  switch (state) {
    case "done":
      return t("databases.moves.states.done");
    case "failed":
      return t("databases.moves.states.failed");
    case "rolled_back":
      return t("databases.moves.states.rolledBack");
    case "rolling_back":
      return t("databases.moves.states.rollingBack");
    default:
      return t("databases.moves.states.moving");
  }
}

/** The moves of an engine to a newer version, and the way back of each. */
function Moves({ actions }: { actions: InstanceActions }) {
  const t = useT();
  const { node } = useNode();
  const query = useQuery(migrationsQuery());
  const [rollingBack, setRollingBack] = useState<Migration | null>(null);
  if (query.isError && olderServer(query.error)) return null;
  const rows = query.data?.migrations ?? [];
  if (query.data !== undefined && rows.length === 0) return null;
  const columns: Column<Migration>[] = [
    {
      id: "id",
      header: t("databases.moves.number"),
      width: "w-20",
      mono: true,
      card: "title",
      cell: (row) => String(row.id),
    },
    {
      id: "move",
      header: t("databases.moves.move"),
      cell: (row) => (
        <span className="flex min-w-0 flex-wrap items-center gap-x-2">
          <Mono tone="default">{row.source}</Mono>
          <span aria-hidden="true">→</span>
          <Mono tone="default">{row.target}</Mono>
          <Badge mono>{row.to_version}</Badge>
        </span>
      ),
    },
    {
      id: "state",
      header: t("databases.moves.state"),
      width: "w-36",
      card: "status",
      cell: (row) => <StatusPill size="sm" state={moveStatus(row.state)} label={moveState(t, row.state)} />,
    },
    {
      id: "databases",
      header: t("databases.moves.databases"),
      width: "w-32",
      hideBelow: "sm",
      cell: (row) => (row.moved ?? []).map((entry) => entry.database).join(", ") || t("databases.moves.none"),
    },
    { id: "when", header: t("databases.moves.when"), width: "w-36", hideBelow: "md", cell: (row) => <RelativeTime value={row.created_at} /> },
  ];
  return (
    <Subsection title={t("databases.moves.title")} description={t("databases.moves.description")} level={3}>
      <DataTable
        columns={columns}
        rows={rows}
        getRowId={(row) => String(row.id)}
        caption={t("databases.moves.caption")}
        loading={query.isPending}
        skeletonRows={2}
        mobile="cards"
        rowActions={(row) =>
          row.can_roll_back ? (
            <RowMenu name={`#${String(row.id)}`}>
              <MenuItem icon={<Undo2 />} disabled={actions.busy} onClick={() => setRollingBack(row)}>
                {t("databases.moves.rollback")}
              </MenuItem>
            </RowMenu>
          ) : null
        }
      />
      {rollingBack !== null ? (
        <ConfirmDialog
          open
          friction="simple"
          onOpenChange={(open) => (open ? undefined : setRollingBack(null))}
          server={node}
          title={t("databases.moves.rollbackTitle", { number: rollingBack.id })}
          description={t("databases.moves.rollbackDescription", { source: rollingBack.source, target: rollingBack.target })}
          actionLabel={t("databases.moves.rollbackAction")}
          onConfirm={async () => {
            const accepted = await request("post", "/api/databases/instances/migrations/{migration_id}/rollback", { params: { migration_id: rollingBack.id } });
            actions.follow(accepted.job_id, {
              running: t("databases.moves.rollingBack", { number: rollingBack.id }),
              succeeded: t("databases.moves.rolledBack", { number: rollingBack.id }),
              failed: t("databases.moves.rollbackFailed", { number: rollingBack.id }),
            });
          }}
        />
      ) : null}
    </Subsection>
  );
}

/**
 * The engines Noust runs itself in containers, beside the server's (`/api/databases/instances`):
 * another version of an engine, or one the server lacks, each from the engine's official image
 * pinned by digest, on 127.0.0.1 only and held to a memory limit. Making one, starting, stopping,
 * moving it to the newest image of its version, moving its databases to a newer major version
 * and removing it are all here; an instance, once made, is an engine like the others on every
 * other tab. Nothing is shown by a server that has no instances.
 */
export function InstancesSection({ actions }: { actions: InstanceActions }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const query = useQuery(instancesQuery());
  const [removing, setRemoving] = useState<DbInstance | null>(null);
  const [upgrading, setUpgrading] = useState<DbInstance | null>(null);

  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: instanceKeys.all });
    void queryClient.invalidateQueries({ queryKey: databaseKeys.engines });
  };
  const stop = useMutation({
    mutationFn: (instance: DbInstance) => request("post", "/api/databases/instances/{name}/stop", { params: { name: instance.name } }),
    onSuccess: (_, instance) => {
      refresh();
      toast.success(t("databases.instances.stoppedToast", { name: instance.name }));
    },
    onError: (error, instance) => reportActionError(t("databases.instances.stopFailedToast", { name: instance.name }), error),
  });
  const start = useMutation({
    mutationFn: (instance: DbInstance) => request("post", "/api/databases/instances/{name}/start", { params: { name: instance.name } }),
    onSuccess: (accepted, instance) =>
      actions.follow(accepted.job_id, {
        running: t("databases.instances.starting", { name: instance.name }),
        succeeded: t("databases.instances.started", { name: instance.name }),
        failed: t("databases.instances.startFailed", { name: instance.name }),
      }),
    onError: (error, instance) => reportActionError(t("databases.instances.startFailedToast", { name: instance.name }), error),
  });

  // A server older than 3.4 has no instances to show; a failure to read them is said.
  if (query.isError && olderServer(query.error)) return null;
  const instances = query.data?.instances ?? [];
  const budget = query.data?.budget;

  const columns: Column<DbInstance>[] = [
    { id: "name", header: t("databases.instances.name"), card: "title", cell: (row) => <Mono tone="default">{row.name}</Mono> },
    {
      id: "state",
      header: t("databases.engines.state"),
      width: "w-36",
      card: "status",
      cell: (row) => {
        const view = stateOf(t, row);
        return <StatusPill appearance="inline" size="sm" state={view.state} label={view.word} />;
      },
    },
    {
      id: "engine",
      header: t("databases.instances.engine"),
      width: "w-44",
      cell: (row) => (
        <span className="flex items-center gap-1.5">
          <span>{row.display_name}</span>
          <Mono tone="muted">{row.version}</Mono>
        </span>
      ),
    },
    {
      id: "listens",
      header: t("databases.instances.listensOn"),
      width: "w-40",
      hideBelow: "sm",
      mono: true,
      cell: (row) => `127.0.0.1:${String(row.host_port)}`,
    },
    {
      id: "memory",
      header: t("databases.instances.memory"),
      width: "w-28",
      align: "end",
      mono: true,
      hideBelow: "md",
      cell: (row) => `${String(row.memory_limit_mb)} MB`,
    },
    {
      id: "tls",
      header: t("databases.tls.column"),
      width: "w-32",
      hideBelow: "md",
      cell: (row) => <StatusPill size="sm" state={row.tls_mode === "off" ? "stopped" : "running"} label={modeWord(t, row.tls_mode)} />,
    },
    { id: "key", header: t("databases.instances.key"), hideBelow: "lg", cell: (row) => <Mono tone="muted">{row.key}</Mono> },
  ];

  return (
    <Section
      title={t("databases.instances.runTitle")}
      description={t("databases.instances.runDescription")}
      actions={
        <Button icon={<Plus aria-hidden="true" />} disabled={actions.busy} onClick={actions.openCreate}>
          {t("databases.newInstance.open")}
        </Button>
      }
    >
      {query.isError ? (
        <ErrorBlock compact error={query.error} title={t("databases.instances.readFailed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />
      ) : (
        <>
          <DataTable
            columns={columns}
            rows={instances}
            getRowId={(row) => row.name}
            caption={t("databases.instances.runCaption")}
            loading={query.isPending}
            skeletonRows={2}
            mobile="cards"
            empty={<EmptyState variant="inline" title={t("databases.instances.runEmpty")} />}
            rowActions={(row) => (
              <RowMenu name={row.name}>
                {row.running ? (
                  <MenuItem icon={<Square />} disabled={stop.isPending} onClick={() => stop.mutate(row)}>
                    {t("databases.engines.stop")}
                  </MenuItem>
                ) : (
                  <MenuItem icon={<Play />} disabled={start.isPending || row.state !== "ready"} onClick={() => start.mutate(row)}>
                    {t("databases.engines.start")}
                  </MenuItem>
                )}
                <MenuItem icon={<ArrowUpFromLine />} disabled={actions.busy || !row.running} onClick={() => setUpgrading(row)}>
                  {t("databases.instances.upgrade")}
                </MenuItem>
                <MenuItem
                  icon={<Replace />}
                  disabled={actions.busy || !row.running}
                  onClick={() => actions.openMigrate({ name: row.name, flavour: row.flavour, version: row.version, label: `${row.display_name} ${row.version} (${row.name})` })}
                >
                  {t("databases.migrate.open")}
                </MenuItem>
                <MenuItem icon={<Lock />} disabled={actions.busy || !row.running} onClick={() => actions.openTls(row)}>
                  {t("databases.tls.open")}
                </MenuItem>
                <MenuItem icon={<Gauge />} disabled={actions.busy || !row.running} onClick={() => actions.openTuning(row)}>
                  {t("databases.tuning.open")}
                </MenuItem>
                <MenuSeparator />
                <MenuItem icon={<Trash2 />} destructive onClick={() => setRemoving(row)}>
                  {t("databases.instances.remove")}
                </MenuItem>
              </RowMenu>
            )}
          />
          {budget !== undefined && budget.server_mb > 0 ? (
            <p className="max-w-measure text-12 text-fg-muted">
              {t("databases.instances.budget", { allocated: budget.allocated_mb, allocatable: budget.allocatable_mb, left: budget.available_mb })}
            </p>
          ) : null}
        </>
      )}
      <Moves actions={actions} />
      {upgrading !== null ? (
        <ConfirmDialog
          open
          friction="simple"
          onOpenChange={(open) => (open ? undefined : setUpgrading(null))}
          server={node}
          title={t("databases.instances.upgradeTitle", { name: upgrading.name })}
          description={t("databases.instances.upgradeDescription", { version: upgrading.version })}
          actionLabel={t("databases.instances.upgradeAction")}
          onConfirm={async () => {
            const accepted = await request("post", "/api/databases/instances/{name}/upgrade", { params: { name: upgrading.name }, query: { backup: true }, body: null });
            actions.follow(accepted.job_id, {
              running: t("databases.instances.upgrading", { name: upgrading.name }),
              succeeded: t("databases.instances.upgraded", { name: upgrading.name }),
              failed: t("databases.instances.upgradeFailed", { name: upgrading.name }),
            });
          }}
        />
      ) : null}
      {removing !== null ? (
        <ConfirmDialog
          open
          onOpenChange={(open) => (open ? undefined : setRemoving(null))}
          server={node}
          title={t("databases.instances.removeTitle", { name: removing.name })}
          description={t("databases.instances.removeDescription")}
          actionLabel={t("databases.instances.removeAction")}
          confirmText={removing.name}
          onConfirm={async () => {
            const accepted = await request("delete", "/api/databases/instances/{name}", { params: { name: removing.name }, query: { confirm: true } });
            actions.follow(accepted.job_id, {
              running: t("databases.instances.removing", { name: removing.name }),
              succeeded: t("databases.instances.removed", { name: removing.name }),
              failed: t("databases.instances.removeFailed", { name: removing.name }),
            });
          }}
        />
      ) : null}
    </Section>
  );
}
