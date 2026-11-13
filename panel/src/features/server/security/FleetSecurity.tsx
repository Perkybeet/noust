/**
 * Security from "All servers": the servers, each with what its own security says (how many checks need a look, who is
 * blocked now and whether a password opens a session), and the way to open its page. What this area holds belongs to one
 * machine, so there is nothing to add up: opening a server from here is choosing it in the selector. Each reading is
 * the server's own answer, asked of it through the central; one that cannot answer (an older Noust, a server that is
 * down) says so in its cell and does not take the row away.
 */

import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useMemo } from "react";

import { api } from "../../../api/client";
import { DetailPage } from "../../../components/page/DetailPage";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { Mono } from "../../../components/ui/Mono";
import { StatusGlyph, StatusPill, stateTextClass } from "../../../components/ui/StatusPill";
import { buttonClassName } from "../../../components/ui/Button";
import { useT } from "../../../i18n";
import { NODE_STATE, nodeStatus, useServerList } from "../../../nodes/servers";
import { useFeature } from "../../central/features";
import { nodeGet } from "../../fleet/nodes";
import type { Fail2ban, SecurityOverview, SshStatus } from "../queries";

interface ServerRow {
  /** The node's name on this central, or null for the central's own machine. */
  node: string | null;
  label: string;
}

/**
 * What a server answers to one read of the security area; a server that cannot answer is not an error of the page. The
 * console's own client is bound to one server and this page reads many, so a node is asked through the central's proxy
 * by name, the way the rest of the fleet does, and this machine directly.
 */
function useReading<T>(node: string | null, name: string, path: `/api/${string}`) {
  return useQuery({
    // The fleet's entries are the central's own (never partitioned by the server on screen): one reading per server, kept under its name.
    queryKey: ["fleet", "security", node ?? "", name] as const,
    queryFn: ({ signal }) => (node === null ? api<T>("GET", path, undefined, { signal }) : nodeGet<T>(node, path, signal)),
    retry: false,
    staleTime: 30_000,
  });
}

function Checks({ node }: { node: string | null }) {
  const t = useT();
  const overview = useReading<SecurityOverview>(node, "overview", "/api/server/security");
  const counts = overview.data?.counts;
  if (counts === null || counts === undefined) return <EmptyCell reason={overview.isError ? t("server.security.fleet.notRead") : t("server.security.fleet.notChecked")} />;
  const open = counts.critical + counts.warning;
  if (open === 0) return <StatusPill state="running" label={t("server.security.fleet.well")} appearance="inline" size="sm" />;
  return (
    <span className={`inline-flex items-center gap-1.5 text-13 font-medium ${stateTextClass(counts.critical > 0 ? "failed" : "warning")}`}>
      <StatusGlyph state={counts.critical > 0 ? "failed" : "warning"} size={12} />
      {t("server.security.fleet.findings", { count: open })}
    </span>
  );
}

function Blocks({ node }: { node: string | null }) {
  const t = useT();
  const bans = useReading<Fail2ban>(node, "fail2ban", "/api/server/security/fail2ban");
  if (bans.data === undefined) return <EmptyCell {...(bans.isError ? { reason: t("server.security.fleet.notRead") } : {})} />;
  if (!bans.data.installed) return <EmptyCell reason={t("server.security.fleet.noFail2ban")} />;
  return <Mono className="text-13">{bans.data.jails.reduce((sum, jail) => sum + jail.currently_banned, 0)}</Mono>;
}

function Passwords({ node }: { node: string | null }) {
  const t = useT();
  const ssh = useReading<SshStatus>(node, "ssh", "/api/server/security/ssh");
  const accepted = ssh.data?.passwords_accepted;
  if (accepted === null || accepted === undefined) return <EmptyCell {...(ssh.isError ? { reason: t("server.security.fleet.notRead") } : {})} />;
  return <span className="text-13 text-fg">{accepted ? t("common.words.yes") : t("common.words.no")}</span>;
}

/** The area's page of one server, opened as the selector would: the server is chosen, then the page is its. */
export function FleetSecurity() {
  const t = useT();
  const servers = useServerList();
  const checksHere = useFeature("server_security");
  const label = t("nav.security.label");
  // The central is a row where it can check its machine: its own table says (a hub in a container has nothing to check).
  const rows = useMemo<ServerRow[]>(
    () => [...(checksHere ? [{ node: null, label: servers.hostname ?? t("fleet.selector.thisServer") }] : []), ...servers.nodes.map((node) => ({ node: node.name, label: node.name }))],
    [checksHere, servers.hostname, servers.nodes, t],
  );
  const columns: Column<ServerRow>[] = [
    {
      id: "server",
      header: t("fleet.machineArea.server"),
      card: "title",
      cell: (row) => (
        <span className="flex min-w-0 items-center gap-2">
          <Mono truncate className="font-medium">
            {row.label}
          </Mono>
          {row.node === null ? <span className="text-12 text-fg-muted">{t("fleet.machineArea.central")}</span> : null}
        </span>
      ),
    },
    {
      id: "state",
      header: t("fleet.column.state"),
      card: "status",
      cell: (row) => {
        const node = row.node === null ? undefined : servers.nodes.find((candidate) => candidate.name === row.node);
        const view = node === undefined ? undefined : NODE_STATE[nodeStatus(node)];
        return view === undefined ? <StatusPill state="running" label={t("fleet.selector.status.reachable")} appearance="inline" size="sm" /> : <StatusPill state={view.state} label={t(view.label)} appearance="inline" size="sm" />;
      },
    },
    { id: "checks", header: t("server.security.views.checks"), card: "meta", cell: (row) => <Checks node={row.node} /> },
    { id: "blocks", header: t("server.security.fleet.blocksNow"), hideBelow: "md", card: "meta", align: "end", cell: (row) => <Blocks node={row.node} /> },
    { id: "passwords", header: t("server.security.fleet.passwords"), hideBelow: "md", card: "meta", cell: (row) => <Passwords node={row.node} /> },
  ];
  return (
    <DetailPage header={{ title: label, description: t("server.security.fleet.description") }}>
      <DataTable
        columns={columns}
        rows={rows}
        getRowId={(row) => row.node ?? "central"}
        caption={t("server.security.fleet.caption")}
        rowActions={(row) => (
          <Link to="/security" search={{ node: row.node ?? undefined }} className={buttonClassName("secondary", "sm")}>
            {t("fleet.machineArea.open", { area: label, server: row.label })}
          </Link>
        )}
      />
    </DetailPage>
  );
}
