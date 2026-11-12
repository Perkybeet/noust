import type { ReactNode } from "react";

import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { useT } from "../../../i18n";
import { ServerName } from "./FaceMark";

/** One server in a table of servers: what it holds, how it stands, and what can be done with it. */
export interface ServerLine {
  name: string;
  central?: boolean;
  /** What the server holds. */
  value: ReactNode;
  /** How it stands, drawn by `FaceMark` or a word of the section's own. */
  state: ReactNode;
  /** One sentence under the name: why it is so. */
  note?: ReactNode;
  /** The row's menu: a `RowMenu`. */
  menu?: ReactNode;
}

/**
 * One table of servers for everything that is a server's: what the fleet holds for each (the panel of a setting) and
 * what each holds by itself (secret managers, log shipping, code hosts, a server's policy). One row a server, the
 * value it holds, how it stands, and its actions in a menu. On a phone a row is a card with the name over its state.
 */
export function ServersTable({ lines, caption, valueHeader, embedded = true }: { lines: readonly ServerLine[]; caption: string; valueHeader: string; embedded?: boolean }) {
  const t = useT();
  const columns: Column<ServerLine>[] = [
    {
      id: "server",
      header: t("fleetSettings.panel.columnServer"),
      card: "title",
      width: "w-56",
      stacked: (line) => line.note !== undefined,
      cell: (line) => (
        <span className="flex min-w-0 flex-col gap-0.5">
          <ServerName name={line.name} {...(line.central === true ? { central: true } : {})} />
          {line.note !== undefined ? <span className="text-12 text-pretty text-fg-muted">{line.note}</span> : null}
        </span>
      ),
    },
    { id: "value", header: valueHeader, card: "meta", cell: (line) => <span className="text-13 text-fg">{line.value}</span> },
    { id: "state", header: t("fleetSettings.panel.columnState"), card: "status", width: "w-52", cell: (line) => line.state },
  ];
  return <DataTable caption={caption} columns={columns} rows={lines} getRowId={(line) => line.name} embedded={embedded} rowActions={(line) => line.menu} />;
}
