/**
 * Docker across every server, in two pages that are each machine's own and cannot be put in one list: the summary (what asks for the
 * operator on any server, worst first, with the server on each row, and under it one row for each server with its engine and its counters)
 * and the engine (one row for each server: which Docker it has and what does not fit in it). A server that runs a Noust that does not
 * count Docker says so in its row, in one sentence, and its page still opens, because that page is where it is explained.
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { dockerViewQuery, rowsOf } from "../../../api/queries/dockerFleet";
import type { FleetAttention, FleetRow, FleetSummary as FleetSummaryRow } from "../../../api/queries/dockerFleet";
import { DashboardPage } from "../../../components/page/DashboardPage";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Sections } from "../../../components/page/Section";
import { ScopeLine } from "../../../components/page/ScopeLine";
import { ServersTable } from "../../../components/page/ServersTable";
import type { ServersTableRow } from "../../../components/page/ServersTable";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { DetailPage } from "../../../components/page/DetailPage";
import { EmptyState } from "../../../components/ui/EmptyState";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { Attention } from "../SummaryPage";
import { fleetServersOf } from "./servers";

/** What a server's row of the engine view says: whether Docker is usable, why not, and the versions it has. */
type EngineLine = FleetRow & { usable?: boolean; state?: string; info?: { server_version?: string; compose_version?: string; server_api?: string } };

const open = (server: { name: string }) => ({ to: "/server/docker", search: { node: server.name } });
const openEngine = (server: { name: string }) => ({ to: "/server/docker/engine", search: { node: server.name } });

export function FleetSummary() {
  const t = useT();
  const attention = useQuery(dockerViewQuery("attention"));
  const summary = useQuery(dockerViewQuery("summary"));
  const servers = useMemo(() => fleetServersOf(summary.data), [summary.data]);
  const items = rowsOf<FleetAttention>(attention.data) ?? [];
  const summaryRows = summary.data;
  const lines = useMemo((): ServersTableRow[] => {
    const byNode = new Map((rowsOf<FleetSummaryRow>(summary.data) ?? []).map((row) => [row.node, row]));
    return servers.map((server) => {
      const row = byNode.get(server.name);
      if (row === undefined) return { server };
      if (!row.available) return { server, summary: t("docker.fleet.noDocker"), state: { status: "stopped", label: t("docker.engine.states.missing") } };
      const containers = row.counts?.["containers"] ?? {};
      const needs = row.attention?.length ?? 0;
      return {
        server,
        summary: t("docker.fleet.serverLine", { running: containers["running"] ?? 0, total: containers["total"] ?? 0 }),
        ...(row.engine?.info.server_version !== undefined && row.engine.info.server_version !== "" ? { note: t("docker.summary.version", { version: row.engine.info.server_version }) } : {}),
        state: needs === 0 ? { status: "running", label: t("docker.fleet.nothingNeeds") } : { status: "warning", label: t("docker.fleet.needs", { count: needs }) },
      };
    });
  }, [summary.data, servers, t]);
  return (
    <DashboardPage header={{ title: t("docker.summary.title"), description: t("docker.fleet.summary.description") }}>
      {summary.isError && summaryRows === undefined ? (
        <ErrorBlock error={summary.error} title={t("docker.summary.failed")} onRetry={() => void summary.refetch()} retrying={summary.isRefetching} />
      ) : summaryRows === undefined ? (
        <LoadingRegion label={t("docker.fleet.loading")}>
          <Skeleton className="h-64 w-full" />
        </LoadingRegion>
      ) : (
        <Sections surfaces>
          <ScopeLine scope={{ kind: "all", servers }} serverTarget={open} />
          <SurfaceSection title={t("docker.attention.title")} icon={OBJECT_ICONS.alert} {...(items.length > 0 ? { description: t("docker.attention.description") } : {})} flush>
            {items.length > 0 ? (
              <Attention items={items} fleet onCleanup={() => undefined} />
            ) : (
              <div className="px-5 py-3">
                <EmptyState variant="inline" title={t("docker.attention.nothing")} />
              </div>
            )}
          </SurfaceSection>
          <SurfaceSection title={t("docker.fleet.servers.title")} icon={OBJECT_ICONS.server} description={t("docker.fleet.servers.description")} flush>
            <ServersTable caption={t("docker.fleet.servers.caption")} summaryHeader={t("docker.fleet.servers.header")} rows={lines} open={open} />
          </SurfaceSection>
        </Sections>
      )}
    </DashboardPage>
  );
}

export function FleetEngine() {
  const t = useT();
  const engine = useQuery(dockerViewQuery("engine"));
  const engineView = engine.data;
  const servers = useMemo(() => fleetServersOf(engineView), [engineView]);
  const lines = useMemo((): ServersTableRow[] => {
    const byNode = new Map((rowsOf<EngineLine>(engine.data) ?? []).map((row) => [row.node, row]));
    return servers.map((server) => {
      const row = byNode.get(server.name);
      if (row === undefined) return { server };
      const info = row.info;
      return {
        server,
        summary: row.usable === true && info?.server_version !== undefined ? t("docker.fleet.engineLine", { version: info.server_version, compose: info.compose_version ?? "-" }) : t(`docker.engine.states.${row.state === "unreachable" || row.state === "too_old" ? row.state : "missing"}`),
        state: row.usable === true ? { status: "running", label: t("docker.fleet.usable") } : { status: row.state === "missing" ? "stopped" : "failed", label: t(`docker.engine.states.${row.state === "unreachable" || row.state === "too_old" ? row.state : "missing"}`) },
      };
    });
  }, [engine.data, servers, t]);
  return (
    <DetailPage header={{ title: t("docker.engine.title"), description: t("docker.fleet.engine.description") }}>
      {engine.isError && engineView === undefined ? (
        <ErrorBlock error={engine.error} title={t("docker.engine.failed")} onRetry={() => void engine.refetch()} retrying={engine.isRefetching} />
      ) : engineView === undefined ? (
        <LoadingRegion label={t("docker.fleet.loading")}>
          <Skeleton className="h-64 w-full" />
        </LoadingRegion>
      ) : (
        <Sections>
          <ScopeLine scope={{ kind: "all", servers }} serverTarget={openEngine} />
          <ServersTable caption={t("docker.fleet.engine.caption")} summaryHeader={t("docker.fleet.engine.header")} rows={lines} open={openEngine} />
        </Sections>
      )}
    </DetailPage>
  );
}
