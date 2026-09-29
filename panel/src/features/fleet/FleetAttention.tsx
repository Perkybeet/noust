import { useMutation, useQueryClient } from "@tanstack/react-query";
import { RotateCw, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";

import { RelativeTime } from "../../components/page/RelativeTime";
import { Section } from "../../components/page/Section";
import { useAnnounceChange } from "../../components/page/useAnnounceChange";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusGlyph } from "../../components/ui/StatusPill";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { cx } from "../../lib/cx";
import { describeError } from "../../lib/errors";
import type { AttentionItem, Severity } from "../overview/attention";
import { SeverityGlyph, summaryText, validUntilText } from "../overview/NeedsAttention";
import { ServerLink } from "./links";
import { nodeKeys, sshAddress, testNode } from "./nodes";
import { serverKey } from "./useFleet";
import type { FleetServer } from "./useFleet";

const LINK =
  "rounded-[4px] font-medium text-accent-fg hover:underline hover:underline-offset-2 focus-visible:outline-2 focus-visible:outline-focus";
const SUBJECT =
  "min-w-0 truncate rounded-[4px] text-14 font-medium text-fg hover:underline hover:underline-offset-2 focus-visible:outline-2 focus-visible:outline-focus";

/** The page of a server that owns what an item is about. */
function subjectPath(item: AttentionItem): string {
  switch (item.subject.kind) {
    case "app":
      return `/apps/${encodeURIComponent(item.subject.domain)}`;
    case "certificate":
      return "/domains";
    case "units":
      return "/services";
    case "unit":
      return `/services/${encodeURIComponent(item.subject.name)}`;
    case "monitor":
      return "/server";
  }
}

/** Where each problem of an app is dealt with, on its own server. */
function ItemActions({ t, server, item }: { t: T; server: FleetServer; item: AttentionItem }) {
  if (item.subject.kind !== "app") return null;
  const domain = item.subject.domain;
  const base = `/apps/${encodeURIComponent(domain)}`;
  const kinds = new Set(item.reasons.map((reason) => reason.kind));
  const deploymentId = item.reasons.find((reason) => reason.deploymentId !== undefined)?.deploymentId;
  return (
    <div className="flex shrink-0 items-center gap-3 text-13">
      {deploymentId !== undefined ? (
        <ServerLink
          node={server.node}
          path={`${base}/deployments/${String(deploymentId)}`}
          aria-label={t("overview.attention.viewLogAria", { domain })}
          className={LINK}
        >
          {t("overview.attention.viewLog")}
        </ServerLink>
      ) : null}
      {kinds.has("state") || kinds.has("deploy") ? (
        <ServerLink node={server.node} path={`${base}/diagnose`} aria-label={t("overview.attention.diagnoseAria", { domain })} className={LINK}>
          {t("overview.attention.diagnose")}
        </ServerLink>
      ) : null}
      {kinds.has("certificate") ? (
        <ServerLink node={server.node} path={`${base}/domains`} aria-label={t("overview.attention.certificateAria", { domain })} className={LINK}>
          {t("overview.attention.certificate")}
        </ServerLink>
      ) : null}
    </div>
  );
}

/** The server an item is on, beside its title: what the fleet adds to the overview's list. */
function ServerTag({ t, server }: { t: T; server: FleetServer }) {
  return (
    <span className="shrink-0 text-12 text-fg-muted">
      {t.rich("servers.fleet.attention.onServer", {
        server: (
          <span translate="no" className="mono text-fg">
            {server.name}
          </span>
        ),
      })}
    </span>
  );
}

function Row({ severity, srLabel, children, actions }: { severity: Severity; srLabel: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <li
      data-severity={severity}
      className="grid grid-cols-[1rem_minmax(0,1fr)] gap-x-3 gap-y-1 px-4 py-3 sm:grid-cols-[1rem_minmax(0,1fr)_auto]"
    >
      <span className="flex h-5 items-center">
        <SeverityGlyph severity={severity} />
        <span className="sr-only">{srLabel}</span>
      </span>
      <div className="flex min-w-0 flex-col gap-1">{children}</div>
      {actions !== undefined ? <div className="col-start-2 sm:col-start-3 sm:row-start-1 sm:self-start">{actions}</div> : null}
    </li>
  );
}

function AttentionRow({ t, server, item }: { t: T; server: FleetServer; item: AttentionItem }) {
  const when = item.reasons.find((reason) => reason.when)?.when ?? null;
  const title = <span translate="no">{item.subject.kind === "units" ? t("overview.attention.services") : item.title}</span>;
  return (
    <Row
      severity={item.severity}
      srLabel={item.severity === "fail" ? t("overview.attention.srFailure") : t("overview.attention.srWarning")}
      actions={<ItemActions t={t} server={server} item={item} />}
    >
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <ServerLink node={server.node} path={subjectPath(item)} className={cx(SUBJECT, item.subject.kind === "unit" && "mono text-13")}>
          {title}
        </ServerLink>
        <ServerTag t={t} server={server} />
        {when ? <RelativeTime value={when} className="shrink-0 text-12 text-fg-faint" /> : null}
      </div>
      <ul className="flex min-w-0 flex-col gap-1">
        {item.reasons.map((reason, index) => {
          const detail = reason.detail ?? (reason.validUntil !== undefined ? validUntilText(t, reason.validUntil) : undefined);
          return (
            <li key={`${reason.summary.key}-${String(index)}`} className="flex min-w-0 flex-col">
              <span className={cx("text-13", reason.severity === "fail" ? "text-fg" : "text-fg-muted")}>{summaryText(t, reason.summary)}</span>
              {detail !== undefined ? (
                <code translate="no" title={detail} className="truncate text-12 text-fg-muted">
                  {detail}
                </code>
              ) : null}
            </li>
          );
        })}
      </ul>
    </Row>
  );
}

/** Tests a server again, from the row that says it cannot be reached. */
function TestAgain({ t, name }: { t: T; name: string }) {
  const queryClient = useQueryClient();
  const test = useMutation({
    mutationFn: () => testNode(name),
    onSuccess: (result) => {
      if (result.reachable) {
        toast.success(t("servers.settings.testedToast", { name, latency: result.latencyMs ?? 0 }), {
          ...(result.version !== null ? { description: t("servers.settings.testedDescription", { version: result.version }) } : {}),
        });
      } else {
        toast.error(t("servers.settings.testFailedToast", { name }), {
          ...(result.error !== null ? { description: result.error } : {}),
          ...(result.details !== null ? { detail: result.details } : {}),
        });
      }
    },
    onError: (error) => {
      const described = describeError(error);
      toast.error(t("servers.settings.testFailedToast", { name }), {
        ...(described.hint !== null ? { description: described.hint } : {}),
        detail: described.detail,
      });
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: nodeKeys.all });
      void queryClient.invalidateQueries({ queryKey: ["fleet", name] });
    },
  });
  return (
    <Button
      size="sm"
      icon={<RotateCw aria-hidden="true" />}
      loading={test.isPending}
      aria-label={t("servers.settings.testLabel", { name })}
      onClick={() => {
        test.mutate();
      }}
    >
      {t("servers.fleet.attention.testAgain")}
    </Button>
  );
}

/** A server the central cannot reach: ssh's own words, verbatim, under the fix. */
function DownRow({ t, server }: { t: T; server: FleetServer }) {
  const refused = server.reachability === "refused";
  const address = server.record === null ? "" : sshAddress(server.record);
  const hint = describeError(server.unreachableError).hint;
  return (
    <Row severity="fail" srLabel={t("overview.attention.srFailure")} actions={server.node !== null ? <TestAgain t={t} name={server.node} /> : undefined}>
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-2">
        <span translate="no" className="mono text-14 font-medium text-fg">
          {server.name}
        </span>
        {server.lastSeen !== null ? <RelativeTime value={server.lastSeen} className="shrink-0 text-12 text-fg-faint" /> : null}
      </div>
      <p className="text-13 text-fg">{refused ? t("servers.fleet.attention.refused") : t("servers.fleet.attention.unreachable")}</p>
      <p className="text-13 text-pretty text-fg-muted">
        {server.unreachableError !== null && hint !== null
          ? hint
          : refused
            ? t("servers.fleet.attention.refusedFix")
            : t.rich("servers.fleet.attention.unreachableFix", {
                address: (
                  <span translate="no" className="mono text-fg">
                    {address}
                  </span>
                ),
              })}
      </p>
      {server.unreachableOutput !== null ? (
        <SystemOutput
          label={t("common.errorBlock.systemSaidLabel", { title: server.name })}
          maxHeight="max-h-40"
          className="mt-1 rounded-control border border-border bg-bg-sunken px-3 py-2"
        >
          {server.unreachableOutput}
        </SystemOutput>
      ) : null}
    </Row>
  );
}

/** A server that answered, but not with its machine: the fix above, its own words below. */
function ReadFailedRow({ t, server }: { t: T; server: FleetServer }) {
  const described = describeError(server.machineError);
  return (
    <Row severity="fail" srLabel={t("overview.attention.srFailure")}>
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-2">
        <span translate="no" className="mono text-14 font-medium text-fg">
          {server.name}
        </span>
        {server.node === null ? <span className="text-12 text-fg-muted">{t("servers.thisServer")}</span> : null}
      </div>
      <p className="text-13 text-fg">{t("servers.fleet.attention.machineFailed")}</p>
      {described.hint !== null ? <p className="text-13 text-pretty text-fg-muted">{described.hint}</p> : null}
      <SystemOutput
        label={t("common.errorBlock.systemSaidLabel", { title: server.name })}
        maxHeight="max-h-40"
        className="mt-1 rounded-control border border-border bg-bg-sunken px-3 py-2"
      >
        {described.detail}
      </SystemOutput>
    </Row>
  );
}

function Unchecked({ t, server }: { t: T; server: FleetServer }) {
  return (
    <>
      {server.unchecked.map((source) => (
        <p key={source.source} className="flex min-w-0 items-baseline gap-2 text-12 text-fg-muted">
          <TriangleAlert aria-hidden="true" className="size-3 shrink-0 translate-y-0.5 text-warn" />
          <span className="min-w-0">
            <span translate="no" className="mono text-fg">
              {server.name}
            </span>{" "}
            {t("servers.fleet.attention.unchecked", { source: t(`servers.fleet.attention.sources.${source.source}`) })}{" "}
            <code className="break-words text-fg">{describeError(source.error).detail}</code>
          </span>
        </p>
      ))}
    </>
  );
}

export interface FleetAttentionProps {
  servers: readonly FleetServer[];
  loading: boolean;
}

/**
 * "Needs attention" across the fleet: servers the central cannot reach first, then every
 * server's own problems (the overview's rules, applied on each), worst first, each linking to
 * the server's page where it is fixed.
 */
export function FleetAttention({ servers, loading }: FleetAttentionProps) {
  const t = useT();
  const down = servers.filter((server) => server.reachability === "unreachable" || server.reachability === "refused");
  const failedReads = servers.filter((server) => server.machineError !== null);
  const items = servers
    .flatMap((server) => server.attention.map((item) => ({ server, item })))
    .sort(
      (a, b) =>
        (a.item.severity === "fail" ? 0 : 1) - (b.item.severity === "fail" ? 0 : 1) ||
        a.server.name.localeCompare(b.server.name) ||
        a.item.title.localeCompare(b.item.title),
    );
  const count = loading ? null : down.length + failedReads.length + items.length;
  const worstFails = down.length > 0 || failedReads.length > 0 || items.some(({ item }) => item.severity === "fail");

  useAnnounceChange(
    count === null ? null : String(count),
    count === null ? null : count === 0 ? t("overview.attention.announceEmpty") : t("overview.attention.announceCount", { count }),
    worstFails ? "assertive" : "polite",
  );

  let body: ReactNode;
  if (loading) {
    body = (
      <div aria-busy="true" className="rounded-card border border-border bg-surface px-4 py-3.5 shadow-raised">
        <span className="sr-only">{t("overview.attention.checking")}</span>
        <div aria-hidden="true" className="flex flex-col gap-2">
          <Skeleton className="h-3.5 w-48" />
          <Skeleton className="h-3 w-72 max-w-full" />
        </div>
      </div>
    );
  } else if (count === 0) {
    body = (
      <div className="flex items-start gap-3 rounded-card border border-border bg-surface px-4 py-3.5 shadow-raised">
        <span className="flex h-5 items-center">
          <StatusGlyph state="running" size={14} className="text-ok" />
        </span>
        <p className="text-13 text-fg-muted">{t("servers.fleet.attention.allClear", { count: servers.length })}</p>
      </div>
    );
  } else {
    body = (
      <ul className="divide-y divide-border rounded-card border border-border bg-surface shadow-raised">
        {down.map((server) => (
          <DownRow key={`down:${serverKey(server)}`} t={t} server={server} />
        ))}
        {failedReads.map((server) => (
          <ReadFailedRow key={`read:${serverKey(server)}`} t={t} server={server} />
        ))}
        {items.map(({ server, item }) => (
          <AttentionRow key={`${serverKey(server)}:${item.id}`} t={t} server={server} item={item} />
        ))}
      </ul>
    );
  }

  return (
    <Section
      title={t("servers.fleet.attention.title")}
      description={t("servers.fleet.attention.description")}
      badge={
        count !== null && count > 0 ? (
          <Badge tone={worstFails ? "fail" : "warn"}>
            <span className="sr-only">{t("overview.attention.itemsSr")}</span>
            {count}
          </Badge>
        ) : undefined
      }
    >
      {body}
      {servers.some((server) => server.unchecked.length > 0) ? (
        <div className="-mt-1 flex flex-col gap-1">
          {servers.map((server) => (
            <Unchecked key={serverKey(server)} t={t} server={server} />
          ))}
        </div>
      ) : null}
    </Section>
  );
}
