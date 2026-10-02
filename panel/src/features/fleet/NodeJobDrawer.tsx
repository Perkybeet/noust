/**
 * One server's part of a fleet action (owner item 61, spec 3.2 section 8.7): what happened
 * there. Its failure first, verbatim with its fix; then its state, how long it really took,
 * its batch and what the job returned said plainly (packages updated and whether a reboot is
 * due, the Noust versions from and to); the node's own job log in the console's LogViewer,
 * live over the job WebSocket the central relays while it runs and whole once it ended, a tab
 * per job when the server ran several; "Each application" only for the actions that work per
 * application; and the way to the job on the server itself.
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { request } from "../../api/client";
import { runOnNode } from "../../api/nodeScope";
import { isJobFinished } from "../../api/queries/jobs";
import type { Job } from "../../api/queries/jobs";
import { KeyValueList } from "../../components/page/KeyValueList";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { useNow } from "../../components/page/clock";
import { Drawer } from "../../components/ui/Drawer";
import { LogViewer } from "../../components/ui/LogViewer";
import type { LogLine } from "../../components/ui/LogViewer";
import { Mono } from "../../components/ui/Mono";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusGlyph, StatusPill, stateTextClass } from "../../components/ui/StatusPill";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { Tab, TabList, TabPanel, Tabs } from "../../components/ui/Tabs";
import { textLinkClassName } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { cx } from "../../lib/cx";
import { formatDate, formatDuration, parseTimestamp } from "../../lib/format";
import { useJobStream } from "../../realtime/sockets";
import { NODE_JOB_VIEW, SKIP_REASONS, batchOf, nodeErrorOf, nodeJobState, textOf } from "./data";
import type { FleetNodeState } from "./data";
import { HrefLink, ServerLink } from "./links";

/** The actions that act on each application, and so have a result per application. */
export const PER_APP_ACTIONS: ReadonlySet<string> = new Set(["apps_update", "apps_restart", "backups_run", "backups_verify"]);

/** How many bytes of a finished job's log are read: the end, which is where it says how it ended. */
const LOG_TAIL = 512 * 1024;

/** A node's job and its captured log, under the central's own keys: the node is in the key. */
const nodeJobKeys = {
  job: (node: string, id: string) => ["fleet", "node-job", node, id] as const,
  log: (node: string, id: string) => ["fleet", "node-job", node, id, "log"] as const,
};

function reasonWords(t: T, node: FleetNodeState): string | null {
  if (node.reason === null || node.reason === undefined) return null;
  const key = SKIP_REASONS[node.reason];
  return key !== undefined ? t(key) : node.reason;
}

/** How long it took, or has been running for: the real duration, not "started 3 min ago". */
function Duration({ node, t }: { node: FleetNodeState; t: T }) {
  const start = parseTimestamp(node.started_at);
  const end = parseTimestamp(node.ended_at);
  const now = useNow(() => (end === null ? 1_000 : 3_600_000));
  if (start === null) return <span className="text-fg-muted">{t("fleet.jobs.notYet")}</span>;
  const seconds = Math.max(0, Math.round(((end?.getTime() ?? now) - start.getTime()) / 1000));
  return <span>{end === null ? t("fleet.jobs.drawer.runningFor", { duration: formatDuration(seconds, t.locale) }) : formatDuration(seconds, t.locale)}</span>;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === "string") : [];
}

/**
 * What the job returned, said plainly, from the central's record of the server (`items`) and
 * the node's own job result: for a system update, how many packages, which, whether a reboot is
 * due and which services still run old libraries; for Noust's update, from which version to
 * which; for a certificate renewal, each certificate renewed with its names and new expiry.
 * Empty when the job returned nothing worth saying.
 */
export function outcomeLines(t: T, action: string, items: readonly Record<string, unknown>[], result: Record<string, unknown> | null): string[] {
  const first = items[0] ?? {};
  if (action === "os_updates") {
    const names = stringList(result?.["packages"]);
    const count = names.length > 0 ? names.length : typeof first["packages"] === "number" ? first["packages"] : null;
    const reboot = typeof result?.["reboot_required"] === "boolean" ? result["reboot_required"] : first["reboot_required"];
    const stale = stringList(result?.["stale_services"]);
    const lines: string[] = [];
    if (count !== null) lines.push(count === 0 ? t("fleet.jobs.drawer.noPackages") : t("fleet.jobs.drawer.packages", { count }));
    if (names.length > 0) lines.push(t("fleet.jobs.drawer.packageNames", { names: names.join(", ") }));
    if (reboot === true) lines.push(t("fleet.jobs.drawer.rebootDue"));
    else if (reboot === false) lines.push(t("fleet.jobs.drawer.noReboot"));
    if (stale.length > 0) lines.push(t("fleet.jobs.drawer.staleServices", { count: stale.length, names: stale.join(", ") }));
    return lines;
  }
  if (action === "certs_renew") {
    // A server older than 3.2 does not say which ones: nothing rather than a guess.
    const renewed = result?.["renewed"];
    if (!Array.isArray(renewed)) return [];
    if (renewed.length === 0) return [t("fleet.jobs.drawer.noCertificates")];
    const lines = [t("fleet.jobs.drawer.certificates", { count: renewed.length })];
    for (const entry of renewed) {
      if (entry === null || typeof entry !== "object") continue;
      const record = entry as Record<string, unknown>;
      const domains = stringList(record["domains"]);
      const names = domains.length > 0 ? domains.join(", ") : (textOf(record["name"]) ?? "");
      const expiry = calendarDay(textOf(record["expiry"]));
      lines.push(expiry !== null ? t("fleet.jobs.drawer.certificateUntil", { names, date: formatDate(expiry, {}, t.locale) }) : t("fleet.jobs.drawer.certificateRenewed", { names }));
    }
    return lines;
  }
  if (action === "noust_update") {
    const from = textOf(first["from_version"]);
    const to = textOf(first["to_version"]);
    return from !== null && to !== null ? [t("fleet.jobs.drawer.noustFromTo", { from, to })] : [];
  }
  return [];
}

/** A `YYYY-MM-DD` day, as certbot prints an expiry, at local midnight so no time zone moves it. */
function calendarDay(value: string | null): Date | null {
  const match = value !== null ? /^(\d{4})-(\d{2})-(\d{2})$/.exec(value) : null;
  if (match === null) return null;
  const day = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return Number.isNaN(day.getTime()) ? null : day;
}

function entriesToLines(job: Job | null): LogLine[] {
  return (job?.logs ?? []).flatMap((entry, id) => {
    const message = entry["message"];
    if (typeof message !== "string") return [];
    const stamp = entry["timestamp"];
    return [{ id, text: message, ...(typeof stamp === "string" ? { ts: stamp.slice(11, 19) } : {}) }];
  });
}

function contentToLines(content: string): LogLine[] {
  if (content === "") return [];
  return content.split("\n").map((text, id) => ({ id, text }));
}

/** One job on the server: followed live while it runs, its whole log once it ended. */
function NodeJobLog({ server, jobId }: { server: string; jobId: string }) {
  const t = useT();
  const job = useQuery({
    queryKey: nodeJobKeys.job(server, jobId),
    queryFn: ({ signal }) => runOnNode(server, () => request("get", "/api/jobs/{job_id}", { params: { job_id: jobId }, signal })),
    refetchInterval: (query) => (query.state.data !== undefined && isJobFinished(query.state.data) ? false : 3_000),
  });
  const finished = job.data !== undefined && isJobFinished(job.data);
  const stream = useJobStream(job.data !== undefined && !finished ? jobId : null, { node: server });
  const log = useQuery({
    queryKey: nodeJobKeys.log(server, jobId),
    queryFn: ({ signal }) => runOnNode(server, () => request("get", "/api/jobs/{job_id}/log", { params: { job_id: jobId }, query: { tail: LOG_TAIL }, signal })),
    enabled: finished,
  });
  const current: Job | null = stream.job ?? job.data ?? null;
  const lines = useMemo(() => (finished && log.data !== undefined && log.data.content !== "" ? contentToLines(log.data.content) : entriesToLines(current)), [finished, log.data, current]);

  if (job.isError && job.data === undefined) {
    return <ErrorBlock compact error={job.error} title={t("fleet.jobs.drawer.logFailed", { name: server })} onRetry={() => void job.refetch()} />;
  }
  if (job.data === undefined) {
    return (
      <div aria-busy="true">
        <span className="sr-only">{t("fleet.jobs.drawer.logLoading", { name: server })}</span>
        <Skeleton className="h-80 w-full rounded-card" />
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      {!finished ? (
        <p className="flex items-center gap-1.5 text-12 text-fg-muted">
          <StatusGlyph state={stream.status === "open" ? "deploying" : "queued"} size={10} />
          {stream.status === "open" ? t("fleet.jobs.drawer.live") : t("fleet.jobs.drawer.connecting")}
        </p>
      ) : null}
      <LogViewer
        lines={lines}
        follow={!finished}
        height={420}
        label={t("fleet.jobs.drawer.logLabel", { name: server, job: jobId })}
        filename={`${server}-${jobId}.log`}
        emptyMessage={finished ? t("fleet.jobs.drawer.logEmpty") : t("fleet.jobs.drawer.logWaiting")}
      />
      {log.data?.truncated ? <p className="text-12 text-fg-faint">{t("fleet.jobs.drawer.logTruncated")}</p> : null}
    </div>
  );
}

function Logs({ server, jobs, t }: { server: string; jobs: readonly string[]; t: T }) {
  const [first] = jobs;
  if (first === undefined) return <p className="text-13 text-fg-muted">{t("fleet.jobs.drawer.noJobs")}</p>;
  if (jobs.length === 1) return <NodeJobLog server={server} jobId={first} />;
  return (
    <Tabs defaultValue={first}>
      <TabList aria-label={t("fleet.jobs.drawer.jobsLabel", { name: server })}>
        {jobs.map((id, index) => (
          <Tab key={id} value={id}>
            {t("fleet.jobs.drawer.jobTab", { number: index + 1 })}
          </Tab>
        ))}
      </TabList>
      {jobs.map((id) => (
        <TabPanel key={id} value={id} className="pt-3">
          <NodeJobLog server={server} jobId={id} />
        </TabPanel>
      ))}
    </Tabs>
  );
}

/** Each application's result, with its domain and a way to its deployment or its backup. */
function Applications({ server, items, t }: { server: string; items: readonly Record<string, unknown>[]; t: T }) {
  return (
    <div className="flex flex-col gap-1.5">
      <p className="text-13 font-medium text-fg">{t("fleet.jobs.items")}</p>
      <ul aria-label={t("fleet.jobs.items")} className="flex flex-col divide-y divide-border">
        {items.map((item, index) => {
          const domain = textOf(item["domain"]) ?? textOf(item["name"]) ?? String(index + 1);
          const itemView = NODE_JOB_VIEW[nodeJobState(textOf(item["state"]) ?? "failed")];
          const message = textOf(item["message"]);
          const output = textOf(item["output"]);
          const deployment = typeof item["deployment_id"] === "number" ? item["deployment_id"] : null;
          const backup = textOf(item["backup_id"]);
          return (
            <li key={`${domain}-${String(index)}`} className="flex min-w-0 flex-col gap-1 py-2">
              <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                <span className={cx("inline-flex items-center gap-1 text-13", stateTextClass(itemView.state))}>
                  <StatusGlyph state={itemView.state} size={10} />
                  <span className="text-fg">{t(itemView.label)}</span>
                </span>
                <Mono truncate>{domain}</Mono>
                {deployment !== null ? (
                  <ServerLink node={server} path={`/apps/${domain}/deployments/${String(deployment)}`} className={textLinkClassName("ui")}>
                    {t("fleet.jobs.drawer.deploymentLink", { id: deployment })}
                  </ServerLink>
                ) : backup !== null ? (
                  <ServerLink node={server} path="/backups" className={textLinkClassName("ui")}>
                    {t("fleet.jobs.drawer.backupLink", { id: backup })}
                  </ServerLink>
                ) : null}
              </span>
              {message !== null ? <span className="text-13 text-fg-muted">{message}</span> : null}
              {output !== null ? (
                <SystemOutput label={t("fleet.jobs.output", { name: domain })} maxHeight="max-h-40">
                  {output}
                </SystemOutput>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export interface NodeJobDrawerProps {
  node: FleetNodeState;
  /** The action's name: a known one, or one a newer central runs, described by its items alone. */
  action: string;
  canary: unknown;
  onClose: () => void;
}

export function NodeJobDrawer({ node, action, canary, onClose }: NodeJobDrawerProps) {
  const t = useT();
  const view = NODE_JOB_VIEW[nodeJobState(node.state)];
  const error = nodeErrorOf(node);
  const items = node.items ?? [];
  const jobs = node.node_jobs ?? [];
  const batch = batchOf(node, canary);
  const reason = reasonWords(t, node);
  // The node's own job carries the full result (a system update's package names), under the
  // same key the log reads it with.
  const result = useNodeJobResult(node.node, jobs[0] ?? null);
  const outcome = outcomeLines(t, action, items, result);

  const facts: KeyValueItem[] = [
    { label: t("fleet.column.state"), value: <StatusPill state={view.state} label={t(view.label)} appearance="inline" size="sm" />, copy: false, mono: false },
    ...(reason !== null ? [{ label: t("fleet.jobs.why"), value: reason, copy: false as const, mono: false }] : []),
    { label: t("fleet.column.step"), value: node.step ?? null },
    { label: t("fleet.jobs.started"), value: node.started_at !== null && node.started_at !== undefined ? <RelativeTime value={node.started_at} /> : null, copy: false, mono: false },
    { label: t("fleet.column.took"), value: <Duration node={node} t={t} />, copy: false, mono: false },
    { label: t("fleet.column.batch"), value: batch.canary ? t("fleet.jobs.canary") : String(batch.number), copy: false, mono: false },
  ];
  // The job on the server, found in its activity by its id.
  const href = jobs[0] !== undefined ? `${node.href}?q=${encodeURIComponent(jobs[0])}` : node.href;

  return (
    <Drawer
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      size="lg"
      title={t("fleet.jobs.drawerTitle", { name: node.node })}
      description={t(view.label)}
    >
      <div className="flex flex-col gap-5">
        {error.message !== null ? (
          <ErrorBlock
            error={{ detail: error.message, ...(node.output ? { output: node.output } : {}) }}
            title={t("fleet.jobs.drawer.failedOn", { name: node.node })}
            {...(error.hint !== null ? { hint: error.hint } : {})}
          />
        ) : null}
        <KeyValueList items={facts} empty={t("fleet.jobs.notYet")} />
        {outcome.length > 0 ? (
          <div className="flex flex-col gap-1.5">
            <p className="text-13 font-medium text-fg">{t("fleet.jobs.drawer.outcomeTitle")}</p>
            <ul aria-label={t("fleet.jobs.drawer.outcomeTitle")} className="flex list-disc flex-col gap-1 pl-5 text-13 text-pretty text-fg">
              {outcome.map((line) => (
                <li key={line} className="break-words">
                  {line}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {error.message === null && node.output !== null && node.output !== undefined && node.output !== "" ? (
          <div className="flex flex-col gap-1.5">
            <p className="text-13 font-medium text-fg">{t("fleet.jobs.output", { name: node.node })}</p>
            <SystemOutput label={t("fleet.jobs.output", { name: node.node })} maxHeight="max-h-96">
              {node.output}
            </SystemOutput>
          </div>
        ) : null}
        {PER_APP_ACTIONS.has(action) && items.length > 0 ? <Applications server={node.node} items={items} t={t} /> : null}
        <div className="flex flex-col gap-1.5">
          <p className="text-13 font-medium text-fg">{t("fleet.jobs.drawer.logTitle")}</p>
          <Logs server={node.node} jobs={jobs} t={t} />
        </div>
        <HrefLink href={href} className={cx(textLinkClassName("ui"), "self-start")}>
          {t("fleet.jobs.drawer.openOnServer", { name: node.node })}
        </HrefLink>
      </div>
    </Drawer>
  );
}

/** The result of the node's first job, once read: the same query the log reads. */
function useNodeJobResult(server: string, jobId: string | null): Record<string, unknown> | null {
  const job = useQuery({
    queryKey: nodeJobKeys.job(server, jobId ?? ""),
    queryFn: ({ signal }) => runOnNode(server, () => request("get", "/api/jobs/{job_id}", { params: { job_id: jobId ?? "" }, signal })),
    enabled: jobId !== null,
  });
  const result = job.data?.result;
  return result !== null && result !== undefined && typeof result === "object" ? result : null;
}
