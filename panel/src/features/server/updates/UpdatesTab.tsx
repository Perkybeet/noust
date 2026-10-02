/**
 * The operating system's updates: what is pending (security ones marked and listed first),
 * installing them as a job, whether a reboot is due and why, the services still running old
 * libraries and restarting them, the distribution's automatic updates, and the last runs.
 *
 * Noust never reboots after an update by itself: the reboot is the operator's, scheduled from
 * here with its pre-checks in view.
 */

import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { CommandHint } from "../../../components/page/CommandHint";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { Drawer } from "../../../components/ui/Drawer";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import type { Status } from "../../../components/ui/StatusPill";
import { FeatureState } from "../../../components/ui/FeatureState";
import { Switch } from "../../../components/ui/Switch";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { NodeCapabilityGate } from "../../../nodes/capability";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { ActionDialog } from "../ActionDialog";
import { ServerErrorBlock, explainServerError } from "../errors";
import { usePowerDialog } from "../PowerDialog";
import { SERVER_CAPABILITY, restartPlanQuery, updateRunsQuery, updatesQuery } from "../queries";
import type { UpdatePackage, UpdateRun, UpdateScope, Updates } from "../queries";
import { useServerJob } from "../serverJob";
import { TabToolbar } from "../TabToolbar";
import { ApplyDialog, Names } from "./ApplyDialog";

/** Rows before "Show all": enough to see every security update on most days. */
const SHOWN_PACKAGES = 8;

/** Security first, kernels among them first, then by name: what to install first reads first. */
export function sortPackages(packages: readonly UpdatePackage[]): UpdatePackage[] {
  return [...packages].sort(
    (a, b) => Number(b.security) - Number(a.security) || Number(b.kernel) - Number(a.kernel) || a.name.localeCompare(b.name),
  );
}

/**
 * The suite an update comes from, as short as it can be said: apt names every origin that
 * carries it ("Ubuntu:24.04/noble-updates, Ubuntu:24.04/noble-security"), and the security one
 * is the one that matters. The whole origin stays in the tooltip.
 */
export function suiteOf(row: Pick<UpdatePackage, "origin" | "security">): string {
  const suites = row.origin.split(", ").map((part) => part.split("/").at(-1) ?? part);
  return (row.security ? suites.find((suite) => suite.endsWith("-security")) : undefined) ?? suites[0] ?? row.origin;
}

function packageColumns(t: T): Column<UpdatePackage>[] {
  return [
    {
      id: "name",
      header: t("server.updates.table.package"),
      card: "title",
      cell: (row) => (
        <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <Mono>{row.name}</Mono>
          {row.security ? <Badge tone="warn">{t("server.updates.table.security")}</Badge> : null}
          {row.kernel ? <Badge>{t("server.updates.table.kernel")}</Badge> : null}
        </span>
      ),
      sortValue: (row) => row.name,
    },
    {
      id: "installed",
      header: t("server.updates.table.installed"),
      mono: true,
      hideBelow: "sm",
      cell: (row) => (row.installed ? <Mono tone="muted">{row.installed}</Mono> : <EmptyCell reason={t("server.updates.table.newPackage")} />),
    },
    { id: "candidate", header: t("server.updates.table.candidate"), mono: true, cell: (row) => <Mono>{row.candidate}</Mono> },
    {
      id: "origin",
      header: t("server.updates.table.origin"),
      hideBelow: "lg",
      cell: (row) =>
        row.advisory ? (
          <Mono tone="muted">{row.advisory}</Mono>
        ) : row.origin !== "" ? (
          <Mono tone="muted" truncate title={row.origin}>
            {suiteOf(row)}
          </Mono>
        ) : (
          <EmptyCell />
        ),
    },
  ];
}

function runState(status: string): Status {
  switch (status) {
    case "succeeded":
    case "completed":
    case "ok":
      return "running";
    case "failed":
      return "failed";
    case "running":
      return "deploying";
    default:
      return "unknown";
  }
}

function runLabel(t: T, status: string): string {
  switch (runState(status)) {
    case "running":
      return t("server.updates.runs.succeeded");
    case "failed":
      return t("server.updates.runs.failed");
    case "deploying":
      return t("server.updates.runs.running");
    default:
      return status;
  }
}

function RecentRuns() {
  const t = useT();
  const runs = useQuery(updateRunsQuery());
  const [open, setOpen] = useState<UpdateRun | null>(null);
  if (runs.isError || runs.data?.length === 0) return null;
  const columns: Column<UpdateRun>[] = [
    {
      id: "started",
      header: t("server.updates.runs.started"),
      card: "title",
      cell: (row) => <RelativeTime value={row.started_at} />,
    },
    { id: "status", header: t("server.updates.runs.state"), width: "w-32", card: "status", cell: (row) => <StatusPill state={runState(row.status)} label={runLabel(t, row.status)} appearance="inline" size="sm" /> },
    {
      id: "scope",
      header: t("server.updates.runs.scope"),
      cell: (row) => (row.scope === "security" ? t("server.updates.runs.scopeSecurity") : t("server.updates.runs.scopeAll")),
    },
    { id: "packages", header: t("server.updates.runs.packages"), align: "end", mono: true, cell: (row) => String((row.packages ?? []).length) },
    { id: "actor", header: t("server.updates.runs.actor"), hideBelow: "md", cell: (row) => (row.actor ? <Mono tone="muted">{row.actor}</Mono> : <EmptyCell />) },
  ];
  return (
    <Card level={2} title={t("server.updates.runs.title")} padding="none">
      <DataTable
        columns={columns}
        rows={runs.data ?? []}
        getRowId={(row) => row.id}
        caption={t("server.updates.runs.caption")}
        loading={runs.isPending}
        skeletonRows={3}
        density="compact"
        mobile="cards"
        onRowActivate={setOpen}
      />
      <Drawer
        open={open !== null}
        onOpenChange={(next) => {
          if (!next) setOpen(null);
        }}
        size="lg"
        title={t("server.updates.runs.drawerTitle")}
        description={open ? <RelativeTime value={open.started_at} /> : undefined}
      >
        {open !== null ? (
          <div className="flex flex-col gap-4">
            {open.error ? <ServerErrorBlock compact error={{ detail: open.error }} title={t("server.updates.runs.failedTitle")} /> : null}
            {(open.packages ?? []).length > 0 ? <Names names={open.packages ?? []} label={t("server.updates.runs.packagesLabel")} /> : null}
            <SystemOutput label={t("server.updates.runs.outputLabel")} maxHeight="max-h-96">
              {(open.tail ?? []).length > 0 ? (open.tail ?? []).join("\n") : t("server.updates.runs.noOutput")}
            </SystemOutput>
          </div>
        ) : null}
      </Drawer>
    </Card>
  );
}

function AutoUpdates({ updates }: { updates: Updates }) {
  const t = useT();
  const { node } = useNode();
  const jobs = useServerJob();
  const auto = updates.auto;
  const change = useMutation({
    mutationFn: (enabled: boolean) => request("put", "/api/server/updates/auto", { body: { enabled, security_only: true } }),
    onSuccess: (accepted) => jobs.track(accepted.job_id, "auto"),
    onError: (error) => reportActionError(t("server.job.auto.failed"), explainServerError(t, error, node)),
  });
  return (
    <FeatureState
      state={auto.enabled ? "on" : "off"}
      title={auto.enabled ? t("server.updates.auto.onTitle") : t("server.updates.auto.offTitle")}
      action={
        <Switch
          label={t("server.updates.auto.label")}
          checked={auto.enabled}
          disabled={!auto.supported || change.isPending || jobs.busy}
          onCheckedChange={(checked) => change.mutate(checked)}
        />
      }
    >
      <div className="flex min-w-0 flex-col gap-2">
        <p>
          {auto.supported
            ? auto.reboots
              ? t("server.updates.auto.descriptionReboots")
              : t("server.updates.auto.description")
            : t("server.updates.auto.unsupported")}
        </p>
        <p className="text-12 text-fg-muted">
          {t.rich("server.updates.auto.mechanism", { mechanism: <Mono>{auto.mechanism}</Mono> })}
          {auto.enabled && auto.security_only === false ? ` ${t("server.updates.auto.allUpdates")}` : ""}
          {auto.last_run ? (
            <>
              {" "}
              {t.rich("server.updates.auto.lastRun", { when: <RelativeTime value={auto.last_run} /> })}
            </>
          ) : null}
        </p>
        {!auto.supported && auto.detail !== "" ? <p className="text-12 text-fg-muted">{auto.detail}</p> : null}
      </div>
    </FeatureState>
  );
}

function Reboot({ updates }: { updates: Updates }) {
  const t = useT();
  const power = usePowerDialog();
  const reboot = updates.reboot;
  if (!reboot.required) return null;
  return (
    <Notice
      tone="warning"
      title={
        reboot.since
          ? t.rich("server.updates.reboot.titleSince", { when: <RelativeTime value={reboot.since} /> })
          : t("server.updates.reboot.title")
      }
    >
      <div className="flex flex-col gap-1.5">
        <p>{t("server.updates.reboot.description")}</p>
        {(reboot.packages ?? []).length > 0 ? <p>{t.rich("server.updates.reboot.packages", { packages: <Mono>{(reboot.packages ?? []).join(", ")}</Mono> })}</p> : null}
        {/* What the detector said, verbatim. */}
        {(reboot.reasons ?? []).map((reason) => (
          <p key={reason} className="text-12 text-fg-muted">
            {reason}
          </p>
        ))}
        {/* Under the reasons, not beside them: a long notice keeps its width on a phone. */}
        <div className="pt-1.5">
          <Button size="sm" onClick={() => power.open("reboot")}>
            {t("server.updates.reboot.schedule")}
          </Button>
        </div>
      </div>
    </Notice>
  );
}

/**
 * Restarting the services an update left on replaced libraries: an interruption that loses no
 * data, so one question (opened on Cancel), after reading which restart and in what order, which
 * are left for a reboot and why, and, when the console is among them, that it restarts last and
 * this page reconnects by itself.
 */
function RestartDialog({ onClose }: { onClose: () => void }) {
  const t = useT();
  const jobs = useServerJob();
  const plan = useQuery(restartPlanQuery());
  const restart = plan.data?.restart ?? [];
  const refused = plan.data?.refused ?? [];
  const none = plan.data !== undefined && restart.length === 0;
  return (
    <ActionDialog
      title={t("server.updates.stale.dialogTitle")}
      description={t("server.updates.stale.dialogDescription")}
      actionLabel={restart.length > 0 ? t("server.updates.stale.action", { count: restart.length }) : t("server.updates.stale.actionNone")}
      disabled={plan.data === undefined || none}
      // A refusal (409, with each unit and why) stays in the dialog, explained by ServerErrorBlock.
      onConfirm={async () => {
        const accepted = await request("post", "/api/server/updates/restarts", { body: {} });
        jobs.track(accepted.job_id, "restarts");
      }}
      onClose={onClose}
    >
      {plan.isError && plan.data === undefined ? (
        <ServerErrorBlock compact error={plan.error} title={t("server.updates.stale.planFailed")} onRetry={() => void plan.refetch()} />
      ) : plan.data === undefined ? (
        <div aria-busy="true" className="flex flex-col gap-2">
          <span className="sr-only">{t("server.updates.stale.loading")}</span>
          <Skeleton className="h-4 w-40" />
          <Skeleton className="h-16 w-full rounded-control" />
        </div>
      ) : (
        <>
          {none ? (
            <Notice tone="warning">{t("server.updates.stale.noneCanRestart")}</Notice>
          ) : (
            <div className="flex flex-col gap-1.5">
              <p className="text-13 font-medium text-fg">{t("server.updates.stale.order")}</p>
              <ol aria-label={t("server.updates.stale.order")} className="flex flex-col gap-1 rounded-control border border-border bg-bg-sunken p-2.5">
                {restart.map((unit) => (
                  <li key={unit} className="text-12">
                    <Mono>{unit}</Mono>
                  </li>
                ))}
              </ol>
            </div>
          )}
          {refused.length > 0 ? (
            <div className="flex flex-col gap-1.5">
              <p className="text-13 font-medium text-fg">{t("server.updates.stale.refusedTitle")}</p>
              <ul aria-label={t("server.updates.stale.refusedLabel")} className="flex flex-col gap-1.5">
                {refused.map((item) => (
                  <li key={item.unit} className="flex flex-col text-13">
                    <Mono>{item.unit}</Mono>
                    {/* The backend's reason, verbatim. */}
                    <span className="text-12 text-fg-muted">{item.reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {plan.data.restarts_console && !none ? <Notice>{t("server.updates.stale.consoleLast")}</Notice> : null}
        </>
      )}
    </ActionDialog>
  );
}

function StaleServices({ updates }: { updates: Updates }) {
  const t = useT();
  const jobs = useServerJob();
  const [restarting, setRestarting] = useState(false);
  if (updates.stale_services.length === 0) return null;
  return (
    <Card
      level={2}
      title={t("server.updates.stale.title", { count: updates.stale_services.length })}
      description={t("server.updates.stale.description")}
      padding="sm"
    >
      <div className="flex flex-col gap-3">
        <Names names={updates.stale_services} label={t("server.updates.stale.label")} />
        <div>
          <Button size="sm" disabled={jobs.busy} onClick={() => setRestarting(true)}>
            {t("server.updates.stale.restart")}
          </Button>
        </div>
      </div>
      {restarting ? <RestartDialog onClose={() => setRestarting(false)} /> : null}
    </Card>
  );
}

function Summary({ updates }: { updates: Updates }) {
  const t = useT();
  const checked = updates.checked_at ? t.rich("server.updates.checked", { when: <RelativeTime value={updates.checked_at} /> }) : null;
  return (
    <span>
      {updates.pending === 0
        ? t("server.updates.upToDate")
        : updates.security > 0
          ? t("server.updates.pendingSecurity", { count: updates.pending, security: updates.security })
          : t("server.updates.pending", { count: updates.pending })}
      {checked !== null ? <> · {checked}</> : null}
    </span>
  );
}

function Packages({ updates }: { updates: Updates }) {
  const t = useT();
  const [all, setAll] = useState(false);
  const sorted = sortPackages(updates.packages);
  const shown = all ? sorted : sorted.slice(0, SHOWN_PACKAGES);
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <DataTable
        columns={packageColumns(t)}
        rows={shown}
        getRowId={(row) => row.name}
        caption={t("server.updates.table.caption")}
        mobile="cards"
        density="compact"
      />
      {sorted.length > SHOWN_PACKAGES ? (
        <div>
          <Button size="sm" variant="ghost" onClick={() => setAll((current) => !current)}>
            {all ? t("server.updates.showFewer") : t("server.updates.showAll", { count: sorted.length })}
          </Button>
        </div>
      ) : null}
    </div>
  );
}

function UpdatesView() {
  const t = useT();
  const { node } = useNode();
  const jobs = useServerJob();
  const updates = useQuery(updatesQuery());
  const [applying, setApplying] = useState<UpdateScope | null>(null);

  const refresh = useMutation({
    mutationFn: () => request("post", "/api/server/updates/refresh"),
    onSuccess: (accepted) => jobs.track(accepted.job_id, "refresh"),
    onError: (error) => reportActionError(t("server.job.refresh.failed"), explainServerError(t, error, node)),
  });
  const repair = useMutation({
    mutationFn: () => request("post", "/api/server/updates/repair"),
    onSuccess: (accepted) => jobs.track(accepted.job_id, "repair"),
    onError: (error) => reportActionError(t("server.job.repair.failed"), explainServerError(t, error, node)),
  });

  if (updates.isError && updates.data === undefined) {
    return <ServerErrorBlock error={updates.error} title={t("server.updates.loadFailed")} onRetry={() => void updates.refetch()} retrying={updates.isRefetching} />;
  }
  if (updates.data === undefined) {
    return (
      <div aria-busy="true" className="flex flex-col gap-4">
        <span className="sr-only">{t("server.updates.loading")}</span>
        <div className="flex items-center justify-between gap-4">
          <Skeleton className="h-5 w-72" />
          <Skeleton className="h-control-md w-56 rounded-control" />
        </div>
        <Skeleton className="h-20 w-full rounded-card" />
        <Skeleton className="h-72 w-full rounded-card" />
      </div>
    );
  }
  const data = updates.data;
  const busy = jobs.busy;
  const checkNow = (
    <Button disabled={busy} loading={refresh.isPending} onClick={() => refresh.mutate()}>
      {t("server.updates.checkNow")}
    </Button>
  );
  const actions = !data.supported ? undefined : (
    <>
      {checkNow}
      {data.pending > 0 && data.security > 0 ? (
        <>
          <Button disabled={busy} onClick={() => setApplying("all")}>
            {t("server.updates.installAll", { count: data.pending })}
          </Button>
          <Button variant="primary" disabled={busy} onClick={() => setApplying("security")}>
            {t("server.updates.installSecurity", { count: data.security })}
          </Button>
        </>
      ) : data.pending > 0 ? (
        <Button variant="primary" disabled={busy} onClick={() => setApplying("all")}>
          {t("server.updates.installAll", { count: data.pending })}
        </Button>
      ) : null}
    </>
  );

  return (
    <div className="flex min-w-0 flex-col gap-6">
      <TabToolbar summary={data.supported ? <Summary updates={data} /> : t("server.updates.unmanaged")} actions={actions} />
      {!data.supported ? (
        <Notice title={t("server.updates.unsupportedTitle")}>{data.reason ?? t("server.updates.unsupportedNoReason")}</Notice>
      ) : null}
      {data.error ? <ServerErrorBlock compact error={{ detail: data.error }} title={t("server.updates.listFailed")} /> : null}
      {data.broken ? (
        <Notice
          tone="error"
          title={t("server.updates.brokenTitle")}
          action={
            <Button size="sm" disabled={busy} loading={repair.isPending} onClick={() => repair.mutate()}>
              {t("server.updates.repair")}
            </Button>
          }
        >
          {t("server.updates.brokenDescription")}
        </Notice>
      ) : null}
      <Reboot updates={data} />
      <div className="grid min-w-0 items-start gap-6 xl:grid-cols-3">
        <div className="flex min-w-0 flex-col gap-4 xl:col-span-2">
          {data.supported && data.pending === 0 ? (
            <EmptyState variant="inline" title={t("server.updates.nothingPending")} />
          ) : data.supported ? (
            <Packages updates={data} />
          ) : null}
          {data.kept_back.length > 0 ? (
            <p className="text-13 text-fg-muted">{t.rich("server.updates.keptBack", { packages: <Mono>{data.kept_back.join(", ")}</Mono> })}</p>
          ) : null}
          {data.holds.length > 0 ? <p className="text-13 text-fg-muted">{t.rich("server.updates.holds", { packages: <Mono>{data.holds.join(", ")}</Mono> })}</p> : null}
          {data.notes.map((note) => (
            <p key={note} className="text-12 text-fg-muted">
              {note}
            </p>
          ))}
        </div>
        <div className="flex min-w-0 flex-col gap-4">
          <AutoUpdates updates={data} />
          <StaleServices updates={data} />
        </div>
      </div>
      <RecentRuns />
      <CommandHint command="noust server updates apply --security-only" label={t("server.fromTerminal")} />
      {applying !== null ? <ApplyDialog scope={applying} keptBack={data.kept_back} onClose={() => setApplying(null)} /> : null}
    </div>
  );
}

/** The Updates tab. */
export function UpdatesTab() {
  return (
    <NodeCapabilityGate capability={SERVER_CAPABILITY}>
      <UpdatesView />
    </NodeCapabilityGate>
  );
}
