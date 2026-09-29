import { Link } from "@tanstack/react-router";
import { Network, Plus, Settings2 } from "lucide-react";

import { PageHeader } from "../../app/PageHeader";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Section, Sections } from "../../components/page/Section";
import { Badge } from "../../components/ui/Badge";
import { buttonClassName } from "../../components/ui/Button";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyState } from "../../components/ui/EmptyState";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusGlyph } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatPercent } from "../../lib/format";
import type { HubArea } from "../central/central";
import { useCentral } from "../central/central";
import { CentralLockedNotice } from "../central/CentralLockedNotice";
import { FleetAttention } from "./FleetAttention";
import { ServerLink } from "./links";
import { ReachabilityPill } from "./ReachabilityPill";
import { serverKey, useFleet } from "./useFleet";
import type { FleetServer } from "./useFleet";

const NAME_LINK =
  "rounded-[4px] font-medium text-fg hover:underline hover:underline-offset-2 focus-visible:outline-2 focus-visible:outline-focus";

function AddServerLink({ variant = "primary" }: { variant?: "primary" | "secondary" }) {
  const t = useT();
  return (
    <Link to="/settings/servers" search={{ add: true }} className={buttonClassName(variant)}>
      <Plus aria-hidden="true" />
      {t("servers.fleet.addServer")}
    </Link>
  );
}

/** A reading, or why there is none: loading, or not read (down, locked, failed). */
function Reading({ t, server, value }: { t: T; server: FleetServer; value: number | undefined }) {
  if (value !== undefined) return <span className="mono text-13 text-fg">{formatPercent(value, t.locale)}</span>;
  if (server.loading) return <Skeleton className="h-3.5 w-10" />;
  return <span className="text-13 text-fg-faint">{t("servers.fleet.notRead")}</span>;
}

function Missing({ t, server }: { t: T; server: FleetServer }) {
  if (server.loading) return <Skeleton className="h-3.5 w-16" />;
  return <span className="text-13 text-fg-faint">{server.reachability === "locked" ? t("servers.reachability.locked") : t("servers.fleet.notRead")}</span>;
}

/** A count of something wrong: red with its glyph when there are any, a quiet word when none. */
function FailCount({ count, text, none }: { count: number; text: string; none: string }) {
  if (count === 0) return <span className="text-13 text-fg-muted">{none}</span>;
  return (
    <span className="inline-flex items-center gap-1 text-13 font-medium text-fail">
      <StatusGlyph state="failed" size={10} />
      {text}
    </span>
  );
}

function columnsFor(t: T, centralVersion: string | null): readonly Column<FleetServer>[] {
  return [
    {
      id: "server",
      header: t("servers.fleet.column.server"),
      sortValue: (server) => (server.node === null ? "" : server.name),
      cell: (server) => (
        <span className="flex min-w-0 flex-col items-start gap-0.5">
          <span className="flex items-center gap-2">
            <ServerLink node={server.node} path="/" className={`${NAME_LINK} mono text-13`} aria-label={t("servers.fleet.open", { name: server.name })}>
              <span translate="no">{server.name}</span>
            </ServerLink>
            {server.hub ? <Badge>{t("servers.hubBadge")}</Badge> : null}
          </span>
          {server.node === null ? <span className="text-12 text-fg-muted">{t("servers.thisServer")}</span> : null}
        </span>
      ),
    },
    {
      id: "reachability",
      header: t("servers.fleet.column.reachability"),
      cell: (server) => <ReachabilityPill reachability={server.reachability} />,
    },
    {
      id: "version",
      header: t("servers.fleet.column.version"),
      hideBelow: "md",
      cell: (server) =>
        server.version === null ? (
          <span className="text-13 text-fg-faint">{t("servers.fleet.notRead")}</span>
        ) : (
          <span className="flex flex-wrap items-center gap-1.5">
            <span translate="no" className="mono text-13 text-fg">
              {server.version}
            </span>
            {server.versionMismatch ? (
              <Badge tone="warn">
                <StatusGlyph state="warning" size={10} />
                <span aria-hidden="true">{t("servers.fleet.versionMismatch")}</span>
                <span className="sr-only">
                  {t("servers.fleet.versionMismatchLabel", { version: server.version, central: centralVersion ?? "" })}
                </span>
              </Badge>
            ) : null}
          </span>
        ),
    },
    {
      id: "cpu",
      header: t("servers.fleet.column.cpu"),
      align: "end",
      hideBelow: "lg",
      sortValue: (server) => server.machine?.cpu_percent ?? null,
      cell: (server) => <Reading t={t} server={server} value={server.machine?.cpu_percent} />,
    },
    {
      id: "memory",
      header: t("servers.fleet.column.memory"),
      align: "end",
      hideBelow: "lg",
      sortValue: (server) => server.machine?.memory.percent ?? null,
      cell: (server) => <Reading t={t} server={server} value={server.machine?.memory.percent} />,
    },
    {
      id: "disk",
      header: t("servers.fleet.column.disk"),
      align: "end",
      hideBelow: "md",
      sortValue: (server) => server.machine?.disk.percent ?? null,
      cell: (server) => <Reading t={t} server={server} value={server.machine?.disk.percent} />,
    },
    {
      id: "apps",
      header: t("servers.fleet.column.apps"),
      hideBelow: "sm",
      cell: (server) => {
        if (server.hub) return <span className="text-13 text-fg-muted">{t("servers.fleet.hubNoApps")}</span>;
        const apps = server.machine?.apps;
        if (apps === undefined) return <Missing t={t} server={server} />;
        return (
          <span className="flex flex-col gap-0.5">
            <span className="text-13 text-fg">{t("servers.fleet.appsRunning", { count: apps.running })}</span>
            {apps.failed > 0 ? (
              <FailCount count={apps.failed} text={t("servers.fleet.appsFailed", { count: apps.failed })} none="" />
            ) : null}
          </span>
        );
      },
    },
    {
      id: "units",
      header: t("servers.fleet.column.units"),
      hideBelow: "md",
      cell: (server) => {
        const units = server.machine?.units;
        if (units === undefined) return <Missing t={t} server={server} />;
        return <FailCount count={units.failed} text={t("servers.fleet.unitsFailed", { count: units.failed })} none={t("servers.fleet.unitsNoneFailed")} />;
      },
    },
    {
      id: "certificates",
      header: t("servers.fleet.column.certificates"),
      hideBelow: "lg",
      cell: (server) => {
        if (server.hub) return <span className="text-13 text-fg-muted">{t("servers.fleet.hubNoApps")}</span>;
        if (server.certsExpiring === null) return <Missing t={t} server={server} />;
        if (server.certsExpiring === 0) return <span className="text-13 text-fg-muted">{t("servers.fleet.certsNoneExpiring")}</span>;
        return (
          <span className="inline-flex items-center gap-1 text-13 font-medium text-warn">
            <StatusGlyph state="warning" size={10} />
            {t("servers.fleet.certsExpiring", { count: server.certsExpiring })}
          </span>
        );
      },
    },
    {
      id: "last-seen",
      header: t("servers.fleet.column.lastSeen"),
      hideBelow: "md",
      cell: (server) =>
        server.node === null ? (
          <span className="text-13 text-fg-muted">{t("servers.fleet.now")}</span>
        ) : (
          <RelativeTime value={server.lastSeen} fallback={t("servers.fleet.never")} className="text-13 text-fg-muted" />
        ),
    },
  ];
}

/** Why a hub opened the fleet instead of the page asked for, and what to do. */
function HubNotice({ hasServers }: { hasServers: boolean }) {
  const t = useT();
  return (
    <div role="note" className="flex items-start gap-2.5 rounded-card border border-border bg-surface px-4 py-3 shadow-raised">
      <Network aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-fg-muted" />
      <div className="flex min-w-0 flex-col gap-0.5">
        <p className="text-13 font-medium text-fg">{t("servers.fleet.hub.title")}</p>
        <p className="text-13 text-pretty text-fg-muted">
          {hasServers ? t("servers.fleet.hub.description") : t("servers.fleet.hub.noServers")}
        </p>
      </div>
    </div>
  );
}

export interface FleetPageProps {
  /** Set when a hub was asked for one of the pages it does not have. */
  hub?: HubArea | "overview" | undefined;
}

/**
 * The fleet at a glance: this server and every server the central manages, one row each, and
 * everything that needs attention anywhere, linking into the server where it is fixed.
 */
export function FleetPage({ hub }: FleetPageProps) {
  const t = useT();
  const central = useCentral();
  const fleet = useFleet();
  const columns = columnsFor(t, fleet.centralVersion);
  const [, ...managed] = fleet.servers;
  const hasServers = managed.length > 0;
  const empty = !fleet.nodesPending && fleet.nodesError === null && !hasServers;
  const attentionLoading = fleet.nodesPending || fleet.servers.some((server) => server.loading);

  return (
    <>
      <PageHeader
        title={t("servers.fleet.title")}
        description={t("servers.fleet.description")}
        actions={
          hasServers ? (
            <>
              <Link to="/settings/servers" className={buttonClassName("secondary")}>
                <Settings2 aria-hidden="true" />
                {t("servers.fleet.manageServers")}
              </Link>
              <AddServerLink />
            </>
          ) : undefined
        }
      />
      <Sections>
        {central.locked ? <CentralLockedNotice /> : null}
        {hub !== undefined || central.role === "hub" ? <HubNotice hasServers={hasServers} /> : null}
        {fleet.nodesError !== null ? (
          <ErrorBlock
            error={fleet.nodesError}
            title={t("common.queryState.couldNotLoad", { label: t("servers.fleet.loadingLabel") })}
            onRetry={fleet.refetchNodes}
          />
        ) : null}
        {empty ? (
          <EmptyState
            level={2}
            icon={<Network />}
            title={t("servers.fleet.empty.title")}
            description={t("servers.fleet.empty.description")}
            action={<AddServerLink />}
            command="noust node key web-2"
          />
        ) : (
          <>
            <FleetAttention servers={fleet.servers} loading={attentionLoading} />
            <Section title={t("servers.fleet.serversTitle")} description={t("servers.fleet.serversDescription")}>
              <DataTable
                caption={t("servers.fleet.tableCaption")}
                columns={columns}
                rows={fleet.servers}
                getRowId={serverKey}
                loading={fleet.nodesPending}
                skeletonRows={2}
              />
            </Section>
          </>
        )}
      </Sections>
    </>
  );
}
