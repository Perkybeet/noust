/**
 * Servers > Software of the fleet (4.2, item 118): what is installed on each server, from the central's one
 * aggregated answer. Choosing a server opens its own Software page, where the detail is and where a piece is
 * removed; nothing is installed or removed from here, because that is a decision about one machine and it asks
 * that machine's own permission and ceiling.
 *
 * A server that runs a Noust of before 4.2 has no software to read. The central says so (`unsupported`) and the
 * row says it as it is, with the way to update it: never "nothing installed", which would be a lie about a machine.
 */

import { Link } from "@tanstack/react-router";

import { QueryState } from "../../../components/page/QueryState";
import { buttonClassName } from "../../../components/ui/Button";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { Mono } from "../../../components/ui/Mono";
import { SkeletonRows } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { ServerLink } from "../../fleet/links";
import { outcomeKind, serverNames } from "../../fleet/data";
import type { FleetRow, NodeOutcome } from "../../fleet/data";
import { PartialNotice, ServerCell, useFleetView } from "../../fleet/parts";

/** One server of the fleet as this view draws it: what it said, or why it said nothing. */
interface Row {
  name: string;
  local: boolean;
  outcome: NodeOutcome;
  /** The central's row for it; none when the server did not answer or runs a Noust of before 4.2. */
  answer: FleetRow | undefined;
}

function count(row: FleetRow | undefined, key: string): number | null {
  const value = row?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function rowsOf(items: readonly FleetRow[], nodes: readonly NodeOutcome[], names: readonly string[]): Row[] {
  const answers = new Map(items.map((item) => [typeof item["node"] === "string" ? item["node"] : "", item]));
  return names.flatMap((name) => {
    const outcome = nodes.find((candidate) => candidate.name === name);
    return outcome === undefined ? [] : [{ name, local: outcome.local, outcome, answer: answers.get(name) }];
  });
}

/** What a server that gave no software says in its place, in words. */
function silence(t: T, row: Row): string | null {
  if (row.answer !== undefined) return row.answer["managed"] === false ? t("software.fleet.unmanaged") : null;
  switch (outcomeKind(row.outcome)) {
    case "unsupported":
      return row.outcome.version != null && row.outcome.version !== "" ? t("software.fleet.older", { version: row.outcome.version }) : t("software.fleet.olderNoVersion");
    case "unreachable":
    case "stale":
      return t("software.fleet.unreachable");
    case "forbidden":
      return t("software.fleet.forbidden");
    default:
      return t("software.fleet.failed");
  }
}

/**
 * A count, and on a phone, where a card has no column header to say what it counts, the words before it (the column's own).
 * `sm:hidden` is the width where the table takes over and the header says it.
 */
function numberCell(row: Row, what: string, read: (answer: FleetRow) => number | null) {
  const value = row.answer === undefined ? null : read(row.answer);
  if (value === null) return <EmptyCell />;
  return (
    <span>
      <span className="text-fg-muted sm:hidden">{what}: </span>
      <Mono>{String(value)}</Mono>
    </span>
  );
}

function columns(t: T, outcomes: ReadonlyMap<string, NodeOutcome>): Column<Row>[] {
  return [
    {
      id: "server",
      header: t("software.fleet.server"),
      card: "title",
      stacked: (row) => silence(t, row) !== null,
      sortValue: (row) => (row.local ? "" : row.name),
      cell: (row) => {
        const said = silence(t, row);
        return (
          <span className="flex min-w-0 flex-col items-start gap-0.5">
            <ServerCell origin={{ node: row.name, local: row.local, href: "/server/software" }} outcome={outcomes.get(row.name)} />
            {said !== null ? <span className="text-12 text-pretty whitespace-normal text-fg-muted">{said}</span> : null}
          </span>
        );
      },
    },
    { id: "installed", header: t("software.fleet.installed"), width: "w-32", card: "meta", cell: (row) => numberCell(row, t("software.fleet.installed"), (answer) => count(answer, "installed")) },
    {
      id: "missing",
      header: t("software.fleet.missing"),
      width: "w-32",
      card: "meta",
      cell: (row) =>
        numberCell(row, t("software.fleet.missing"), (answer) => {
          const total = count(answer, "total");
          const installed = count(answer, "installed");
          return total === null || installed === null ? null : total - installed;
        }),
    },
    {
      id: "unknown",
      header: t("software.fleet.unknown"),
      width: "w-44",
      hideBelow: "md",
      card: "meta",
      cell: (row) =>
        numberCell(row, t("software.fleet.unknown"), (answer) => {
          const installed = count(answer, "installed");
          const byNoust = count(answer, "by_noust");
          return installed === null || byNoust === null ? null : installed - byNoust;
        }),
    },
  ];
}

function RowAction({ t, row }: { t: T; row: Row }) {
  if (row.answer === undefined) {
    // An older Noust cannot say its software, and updating it is what makes it able to; any other silence is not ours to fix.
    return outcomeKind(row.outcome) === "unsupported" ? (
      <Link to="/fleet/updates" search={{ server: row.name }} aria-label={t("software.fleet.updateNamed", { server: row.name })} className={buttonClassName("secondary", "sm")}>
        {t("software.fleet.update")}
      </Link>
    ) : null;
  }
  return (
    <ServerLink node={row.local ? null : row.name} path="/server/software" aria-label={t("software.fleet.open", { server: row.name })} className={buttonClassName("secondary", "sm")}>
      {t("software.fleet.openAction")}
    </ServerLink>
  );
}

/** The software of every server of the fleet. */
export function FleetSoftware() {
  const t = useT();
  const view = useFleetView("software");
  return (
    <QueryState query={view} label={t("software.fleet.label")} skeleton={<SkeletonRows rows={4} />}>
      {(data) => {
        const said = new Set(data.nodes.filter((outcome) => outcomeKind(outcome) === "unsupported").map((outcome) => outcome.name));
        return (
          <div className="flex min-w-0 flex-col gap-4">
            <p className="max-w-measure text-14 text-pretty text-fg-muted">{t("software.fleet.description")}</p>
            {/* An older Noust is said in its own row; the others that did not answer fully are said here, with what they printed. */}
            <PartialNotice view={data} skip={said} />
            <DataTable
              caption={t("software.fleet.caption")}
              columns={columns(t, view.outcomes)}
              rows={rowsOf(data.items, data.nodes, serverNames(data))}
              getRowId={(row) => row.name}
              rowActions={(row) => <RowAction t={t} row={row} />}
            />
          </div>
        );
      }}
    </QueryState>
  );
}
