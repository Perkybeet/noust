import { Link } from "@tanstack/react-router";
import { useMemo } from "react";
import type { ReactNode } from "react";

import { useT } from "../../i18n";
import { buttonClassName } from "../ui/Button";
import { DataTable } from "../ui/DataTable";
import type { Column } from "../ui/DataTable";
import { StatusPill } from "../ui/StatusPill";
import type { Status } from "../ui/StatusPill";
import { answerOf } from "./fleetServers";
import type { FleetServer, ServerTarget } from "./fleetServers";
import { ServerCell } from "./ServerCell";

export interface ServersTableRow {
  server: FleetServer;
  /** What the section says of this server, in one line ("Active, 9 rules"). Of a server that did not answer now: the last it gave. */
  summary?: ReactNode;
  /** A second, smaller line under it ("Changed 12 d ago"). */
  note?: string;
  /** Its state in the section's own words ("Filters", "Does not filter"): a colour, a shape and the word. */
  state?: { status: Status; label: string };
}

export interface ServersTableProps {
  /** Names the table for assistive technology: "Firewall of every server". */
  caption: string;
  /** The header of the summary column ("Firewall"). Without it there is no such column: a door and nothing more. */
  summaryHeader?: string;
  /** One row for each server that has the section, in the order the page wants. `undefined`: not read yet. */
  rows: readonly ServersTableRow[] | undefined;
  /**
   * Where "Open on web-01" goes. It chooses that server in the selector (the words say it) and opens the section's
   * page on it; what the selector becomes is the page's to say, the kit never decides it.
   */
  open: (server: FleetServer) => ServerTarget;
}

/** The state of a server that has no row of its own to give: what its way of answering says. */
function answerState(server: FleetServer, t: ReturnType<typeof useT>): { status: Status; label: string } | null {
  const answer = answerOf(server);
  if (answer.kind === "unsupported") return { status: "unknown", label: t("fleetCompare.table.notAvailable") };
  if (answer.kind === "unreachable") return { status: "failed", label: t("fleetCompare.table.notAnswering") };
  return null;
}

/**
 * What a section shows with All servers when its content is each machine's own and cannot be put in one list: one row
 * for each server with a summary of what the section says of it, and the way in, "Open on web-01", which is a link
 * (it goes somewhere) and chooses that server. It is also the door of the tools of a single machine (Files, Terminal):
 * the server and the way in, nothing to compare. A server whose Noust does not know the view says so in its row and its
 * page still opens, because that page is where the missing view is explained; a server that gave nothing says it.
 */
export function ServersTable({ caption, summaryHeader, rows, open }: ServersTableProps) {
  const t = useT();
  const withState = rows?.some((row) => row.state !== undefined || answerState(row.server, t) !== null) ?? false;
  const columns = useMemo<Column<ServersTableRow>[]>(() => {
    const server: Column<ServersTableRow> = {
      id: "server",
      header: t("fleetCompare.names.server"),
      card: "title",
      width: "w-44",
      sortValue: (row) => row.server.name,
      cell: (row) => <ServerCell server={row.server} />,
      stacked: (row) => answerOf(row.server).kind === "stale",
    };
    const summary: Column<ServersTableRow> = {
      id: "summary",
      header: summaryHeader ?? "",
      card: "meta",
      stacked: (row) => row.note !== undefined,
      cell: (row) => {
        const kind = answerOf(row.server).kind;
        if (kind === "unsupported") return <span className="text-13 text-fg-muted">{t("fleetCompare.table.noView")}</span>;
        if (kind === "unreachable") return <span className="text-13 text-fg-muted">{t("fleetCompare.table.noAnswer")}</span>;
        return (
          <span className="flex min-w-0 flex-col">
            <span className="text-13 text-fg">{row.summary}</span>
            {row.note !== undefined ? <span className="text-12 text-fg-muted">{row.note}</span> : null}
          </span>
        );
      },
    };
    const state: Column<ServersTableRow> = {
      id: "state",
      header: t("fleetCompare.table.state"),
      card: "status",
      width: "w-40",
      cell: (row) => {
        const own = answerState(row.server, t) ?? row.state;
        return own === undefined ? null : <StatusPill state={own.status} label={own.label} appearance="inline" size="sm" />;
      },
    };
    return [server, ...(summaryHeader !== undefined ? [summary] : []), ...(withState ? [state] : [])];
  }, [t, summaryHeader, withState]);

  return (
    <DataTable<ServersTableRow>
      caption={caption}
      columns={columns}
      rows={rows}
      getRowId={(row) => row.server.name}
      rowActions={(row) => {
        const target = open(row.server);
        return (
          <Link to={target.to} search={target.search as never} className={buttonClassName("secondary", "sm")}>
            {t("fleetCompare.table.openOn", { server: row.server.name })}
          </Link>
        );
      }}
    />
  );
}
