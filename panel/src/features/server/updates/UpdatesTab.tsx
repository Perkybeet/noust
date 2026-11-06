/**
 * The operating system's updates: what is pending (security ones marked and listed first),
 * installing them as a job, whether a reboot is due and why, the services still running old
 * libraries and restarting them, the distribution's automatic updates, and the last runs.
 *
 * Noust never reboots after an update by itself: the reboot is the operator's, scheduled from
 * here with its pre-checks in view.
 */

import { Download, RotateCw, Wrench } from "lucide-react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { Can, GuardedButton, ReadOnlyLine, useCan } from "../../../components/page/permissions";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { Drawer } from "../../../components/ui/Drawer";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { SkeletonCard, SkeletonText } from "../../../components/ui/Skeleton";
import { Switch } from "../../../components/ui/Switch";
import { StatusPill } from "../../../components/ui/StatusPill";
import type { Status } from "../../../components/ui/StatusPill";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { NodeCapabilityGate } from "../../../nodes/capability";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { ServerErrorBlock, explainServerError, useServerConfirm, ServerQueryState } from "../errors";
import { usePowerDialog } from "../PowerDialog";
import { SERVER_CAPABILITY, restartPlanQuery, updateRunsQuery, updatesQuery } from "../queries";
import type { UpdatePackage, UpdateRun, UpdateScope, Updates } from "../queries";
import { useServerJob } from "../serverJob";
import { HeaderActions } from "../headerSlot";
import { NoustSection } from "../system/NoustUpdate";
import { ApplyDialog, Names } from "./ApplyDialog";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { namedActor } from "../../activity/data";

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
          {row.security ? <StatusPill state="warning" label={t("server.updates.table.security")} appearance="inline" size="sm" /> : null}
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
    { id: "actor", header: t("server.updates.runs.actor"), hideBelow: "md", cell: (row) => (row.actor ? <Mono tone="muted">{namedActor(t, row.actor)}</Mono> : <EmptyCell />) },
  ];
  return (
    <SurfaceSection title={t("server.updates.runs.title")} icon={OBJECT_ICONS.activity} flush>
      <DataTable
        embedded
        columns={columns}
        rows={runs.data ?? []}
        getRowId={(row) => row.id}
        caption={t("server.updates.runs.caption")}
        loading={runs.isPending}
        skeletonRows={3}
        density="compact"
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
    </SurfaceSection>
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
  // A change already running on the server is not this switch's own: it waits, and says so.
  const reason = !auto.supported ? t("server.updates.auto.unsupported") : jobs.busy && !change.isPending ? t("server.updates.auto.busy") : null;
  const can = useCan("server.manage");
  return (
    <SurfaceSection
      title={t("server.updates.auto.title")}
      icon={OBJECT_ICONS.update}
      description={auto.reboots ? t("server.updates.auto.descriptionReboots") : t("server.updates.auto.description")}
      // The switch is beside the name of what it turns on, as a section's master switch is (HD-1).
      status={
        <Switch
          aria-label={t("server.updates.auto.label")}
          checked={auto.enabled}
          busy={change.isPending}
          disabled={reason !== null || !can}
          onCheckedChange={(checked) => change.mutate(checked)}
        />
      }
    >
      <p className="text-12 text-fg-muted">
        {reason !== null ? <>{reason} </> : null}
        {t.rich("server.updates.auto.mechanism", { mechanism: <Mono>{auto.mechanism}</Mono> })}
        {auto.enabled && auto.security_only === false ? ` ${t("server.updates.auto.allUpdates")}` : ""}
        {auto.last_run ? (
          <>
            {" "}
            {t.rich("server.updates.auto.lastRun", { when: <RelativeTime value={auto.last_run} /> })}
          </>
        ) : null}
        {!auto.supported && auto.detail !== "" ? ` ${auto.detail}` : ""}
      </p>
    </SurfaceSection>
  );
}

function Reboot({ updates }: { updates: Updates }) {
  const t = useT();
  const power = usePowerDialog();
  const [why, setWhy] = useState(false);
  const reboot = updates.reboot;
  if (!reboot.required) return null;
  const packages = reboot.packages ?? [];
  const reasons = reboot.reasons ?? [];
  return (
    <>
      <Notice
        variant="banner"
        tone="warning"
        title={reboot.since ? t.rich("server.updates.reboot.titleSince", { when: <RelativeTime value={reboot.since} /> }) : t("server.updates.reboot.title")}
      >
        {/* The action sits under the text on a phone and beside it from `sm`, so a long sentence keeps its line; the reasons are a button in the sentence. */}
        <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
          <span>
            {t("server.updates.reboot.description")}{" "}
            {packages.length > 0 || reasons.length > 0 ? (
              <Button size="sm" variant="ghost" className="align-baseline" onClick={() => setWhy(true)}>
                {t("server.updates.reboot.why")}
              </Button>
            ) : null}
          </span>
          <Button icon={<RotateCw aria-hidden="true" />} size="sm" className="self-start sm:shrink-0 sm:self-auto" onClick={() => power.open("reboot")}>
            {t("server.updates.reboot.schedule")}
          </Button>
        </div>
      </Notice>
      <Drawer open={why} onOpenChange={setWhy} size="md" title={t("server.updates.reboot.whyTitle")} description={t("server.updates.reboot.whyDescription")}>
        <div className="flex flex-col gap-4">
          {packages.length > 0 ? <Names names={packages} label={t("server.updates.reboot.packagesLabel")} /> : null}
          {/* What the detector said, verbatim. */}
          {reasons.length > 0 ? <SystemOutput label={t("server.updates.reboot.reasonsLabel")}>{reasons.join("\n")}</SystemOutput> : null}
        </div>
      </Drawer>
    </>
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
  const confirmation = useServerConfirm();
  const jobs = useServerJob();
  const plan = useQuery(restartPlanQuery());
  const restart = plan.data?.restart ?? [];
  const refused = plan.data?.refused ?? [];
  const none = plan.data !== undefined && restart.length === 0;
  return (
    <ConfirmDialog
      {...confirmation}
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      friction="simple"
      destructive={false}
      size="md"
      title={t("server.updates.stale.dialogTitle")}
      description={t("server.updates.stale.dialogDescription")}
      actionLabel={restart.length > 0 ? t("server.updates.stale.action", { count: restart.length }) : t("server.updates.stale.actionNone")}
      ready={plan.data !== undefined && !none}
      // A refusal (409, with each unit and why) stays in the dialog, explained by the server area.
      onConfirm={async () => {
        const accepted = await request("post", "/api/server/updates/restarts", { body: {} });
        jobs.track(accepted.job_id, "restarts");
      }}
    >
      <ServerQueryState query={plan} label={t("server.updates.stale.planWhat")} skeleton={<SkeletonText lines={3} />}>
        {(data) => (
          <>
            {none ? (
              <p className="text-13 text-fg-muted">{t("server.updates.stale.noneCanRestart")}</p>
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
            {data.restarts_console && !none ? <p className="text-13 text-fg-muted">{t("server.updates.stale.consoleLast")}</p> : null}
          </>
        )}
      </ServerQueryState>
    </ConfirmDialog>
  );
}

function StaleServices({ updates }: { updates: Updates }) {
  const t = useT();
  const jobs = useServerJob();
  const [restarting, setRestarting] = useState(false);
  if (updates.stale_services.length === 0) return null;
  return (
    <SurfaceSection
      title={t("server.updates.stale.title", { count: updates.stale_services.length })}
      icon={OBJECT_ICONS.service}
      description={t("server.updates.stale.description")}
      actions={
        <Can permission="server.manage">
          <Button icon={<RotateCw aria-hidden="true" />} size="sm" disabled={jobs.busy} onClick={() => setRestarting(true)}>
            {t("server.updates.stale.restart")}
          </Button>
        </Can>
      }
    >
      <Names names={updates.stale_services} label={t("server.updates.stale.label")} />
      {restarting ? <RestartDialog onClose={() => setRestarting(false)} /> : null}
    </SurfaceSection>
  );
}

/** The packages the system has waiting, security ones first, in a slab with its own head: how many, since when, and the way to look again. */
function SystemPackages({ updates, busy }: { updates: Updates; busy: boolean }) {
  const t = useT();
  const { node } = useNode();
  const jobs = useServerJob();
  const [all, setAll] = useState(false);
  const sorted = sortPackages(updates.packages);
  const shown = all ? sorted : sorted.slice(0, SHOWN_PACKAGES);
  const refresh = useMutation({
    mutationFn: () => request("post", "/api/server/updates/refresh"),
    onSuccess: (accepted) => jobs.track(accepted.job_id, "refresh"),
    onError: (error) => reportActionError(t("server.job.refresh.failed"), explainServerError(t, error, node)),
  });
  const checked = updates.checked_at ? t.rich("server.updates.checked", { when: <RelativeTime value={updates.checked_at} /> }) : null;
  return (
    <SurfaceSection
      title={t("server.updates.systemTitle")}
      icon={OBJECT_ICONS.packages}
      description={
        <>
          {updates.pending === 0
            ? t("server.updates.upToDate")
            : updates.security > 0
              ? t("server.updates.pendingSecurity", { count: updates.pending, security: updates.security })
              : t("server.updates.pending", { count: updates.pending })}
          {checked !== null ? <> · {checked}</> : null}
        </>
      }
      actions={
        <Can permission="server.manage">
          <Button size="sm" icon={<RotateCw aria-hidden="true" />} disabled={busy} loading={refresh.isPending} onClick={() => refresh.mutate()}>
            {t("server.updates.checkNow")}
          </Button>
        </Can>
      }
      flush={sorted.length > 0}
    >
      {updates.pending === 0 ? (
        <EmptyState variant="inline" title={t("server.updates.nothingPending")} />
      ) : (
        <div className="flex min-w-0 flex-col">
          <DataTable embedded columns={packageColumns(t)} rows={shown} getRowId={(row) => row.name} caption={t("server.updates.table.caption")} density="compact" />
          {sorted.length > SHOWN_PACKAGES ? (
            <div className="border-t border-border px-5 py-2">
              <Button size="sm" variant="ghost" onClick={() => setAll((current) => !current)}>
                {all ? t("server.updates.showFewer") : t("server.updates.showAll", { count: sorted.length })}
              </Button>
            </div>
          ) : null}
        </div>
      )}
      {updates.kept_back.length > 0 || updates.holds.length > 0 || updates.notes.length > 0 ? (
        <div className="flex flex-col gap-1.5 border-t border-border px-5 py-3">
          {updates.kept_back.length > 0 ? <p className="text-13 text-fg-muted">{t.rich("server.updates.keptBack", { packages: <Mono>{updates.kept_back.join(", ")}</Mono> })}</p> : null}
          {updates.holds.length > 0 ? <p className="text-13 text-fg-muted">{t.rich("server.updates.holds", { packages: <Mono>{updates.holds.join(", ")}</Mono> })}</p> : null}
          {updates.notes.map((note) => (
            <p key={note} className="text-12 text-fg-muted">
              {note}
            </p>
          ))}
        </div>
      ) : null}
    </SurfaceSection>
  );
}

function UpdatesView() {
  const t = useT();
  const updates = useQuery(updatesQuery());
  return (
    <ServerQueryState
      query={updates}
      label={t("server.updates.label")}
      skeleton={
        <div className="flex flex-col gap-6">
          <div className="grid gap-6 lg:grid-cols-2">
            <SkeletonCard lines={2} />
            <SkeletonCard lines={2} />
          </div>
          <SkeletonCard lines={6} />
        </div>
      }
    >
      {(data) => <UpdatesBody data={data} />}
    </ServerQueryState>
  );
}

function UpdatesBody({ data }: { data: Updates }) {
  const t = useT();
  const { node } = useNode();
  const jobs = useServerJob();
  const [applying, setApplying] = useState<UpdateScope | null>(null);

  const repair = useMutation({
    mutationFn: () => request("post", "/api/server/updates/repair"),
    onSuccess: (accepted) => jobs.track(accepted.job_id, "repair"),
    onError: (error) => reportActionError(t("server.job.repair.failed"), explainServerError(t, error, node)),
  });

  const busy = jobs.busy;
  // What the page does, at the right of its title: the security ones first when there are some, and the whole list.
  const actions =
    !data.supported || data.pending === 0 ? null : (
      <HeaderActions>
        {data.security > 0 ? (
          <GuardedButton permission="server.manage" icon={<Download aria-hidden="true" />} disabled={busy} onClick={() => setApplying("security")}>
            {t("server.updates.installSecurity", { count: data.security })}
          </GuardedButton>
        ) : null}
        <GuardedButton permission="server.manage" variant="primary" icon={<Download aria-hidden="true" />} disabled={busy} onClick={() => setApplying("all")}>
          {t("server.updates.installAll", { count: data.pending })}
        </GuardedButton>
      </HeaderActions>
    );

  return (
    <div className="flex min-w-0 flex-col gap-6">
      {actions}
      <ReadOnlyLine permission="server.manage" />
      {!data.supported ? (
        <SurfaceSection title={t("server.updates.unmanaged")} icon={OBJECT_ICONS.packages} description={data.reason ?? t("server.updates.unsupportedNoReason")} />
      ) : null}
      {data.error ? <ServerErrorBlock compact error={{ detail: data.error }} title={t("server.updates.listFailed")} /> : null}
      {data.broken ? (
        <Notice
          tone="error"
          title={t("server.updates.brokenTitle")}
          action={
            <Can permission="server.manage">
              <Button icon={<Wrench aria-hidden="true" />} size="sm" disabled={busy} loading={repair.isPending} onClick={() => repair.mutate()}>
                {t("server.updates.repair")}
              </Button>
            </Can>
          }
        >
          {t("server.updates.brokenDescription")}
        </Notice>
      ) : null}
      <Reboot updates={data} />
      <div className="grid min-w-0 items-start gap-6 lg:grid-cols-2">
        <NoustSection />
        <AutoUpdates updates={data} />
      </div>
      {data.supported ? <SystemPackages updates={data} busy={busy} /> : null}
      <StaleServices updates={data} />
      <RecentRuns />
      {applying !== null ? <ApplyDialog scope={applying} keptBack={data.kept_back} onClose={() => setApplying(null)} /> : null}
    </div>
  );
}

/** The Updates section: Noust's own update and the system's, in one page. */
export function UpdatesTab() {
  return (
    <NodeCapabilityGate capability={SERVER_CAPABILITY}>
      <UpdatesView />
    </NodeCapabilityGate>
  );
}
