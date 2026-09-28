import { Link, useNavigate } from "@tanstack/react-router";
import { MoreHorizontal, SquareTerminal, Trash2 } from "lucide-react";
import type { ReactNode } from "react";
import { useState } from "react";

import type { Database } from "../../api/queries/databases";
import { Badge } from "../../components/ui/Badge";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { IconButton } from "../../components/ui/IconButton";
import { Menu, MenuItem } from "../../components/ui/Menu";
import { useT } from "../../i18n";
import { formatCount } from "../../lib/format";
import { engineLabel } from "./data";
import { useDatabaseActions } from "./useDatabaseActions";

/** One row of the databases table: the engine's own report, name unique per engine. */
export type DatabaseRow = Database;

function RowActions({ database }: { database: DatabaseRow }) {
  const t = useT();
  const navigate = useNavigate();
  const { dropDatabase } = useDatabaseActions();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const isSlot = database.engine === "redis";
  return (
    <>
      <Menu
        align="end"
        trigger={<IconButton label={t("databases.table.actionsFor", { name: database.name })} icon={<MoreHorizontal />} size="sm" tooltip={false} />}
      >
        <MenuItem
          icon={<SquareTerminal />}
          onClick={() =>
            void navigate({ to: "/databases/$engine/$name", params: { engine: database.engine, name: database.name } })
          }
        >
          {t("databases.table.open")}
        </MenuItem>
        <MenuItem icon={<Trash2 />} destructive onClick={() => setConfirmOpen(true)}>
          {t("databases.table.dropDatabase")}
        </MenuItem>
      </Menu>
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={t("databases.table.dropTitle", { name: database.name })}
        description={t(isSlot ? "databases.table.dropDescriptionSlot" : "databases.table.dropDescriptionDatabase", {
          name: database.name,
          engine: engineLabel(database.engine),
        })}
        confirmText={database.name}
        actionLabel={t("databases.table.dropDatabase")}
        onConfirm={async () => {
          await dropDatabase.mutateAsync({ engine: database.engine, name: database.name });
        }}
      />
    </>
  );
}

export interface DatabasesTableProps {
  databases: readonly DatabaseRow[];
  caption: string;
  loading?: boolean;
  empty?: ReactNode;
  className?: string;
}

/** Every database an installed, running engine reports, across engines unless filtered. */
export function DatabasesTable({ databases, caption, loading = false, empty, className }: DatabasesTableProps) {
  const t = useT();
  const columns: Column<DatabaseRow>[] = [
    {
      id: "engine",
      header: t("databases.fields.engine"),
      width: "w-36",
      cell: (row) => <Badge tone="neutral">{engineLabel(row.engine)}</Badge>,
      sortValue: (row) => row.engine,
    },
    {
      id: "name",
      header: t("databases.fields.database"),
      mono: true,
      cell: (row) => (
        <Link
          to="/databases/$engine/$name"
          params={{ engine: row.engine, name: row.name }}
          className="-mx-1 rounded-[4px] px-1 py-0.5 font-medium text-fg hover:underline hover:underline-offset-2 focus-visible:outline-2 focus-visible:outline-focus"
        >
          {row.name}
        </Link>
      ),
      sortValue: (row) => row.name,
    },
    {
      id: "owner",
      header: t("databases.fields.owner"),
      mono: true,
      hideBelow: "md",
      cell: (row) => (row.owner ? <span className="text-fg-muted">{row.owner}</span> : <span className="text-fg-faint">-</span>),
      sortValue: (row) => row.owner ?? null,
    },
    {
      id: "tables",
      header: t("databases.fields.tables"),
      align: "end",
      mono: true,
      hideBelow: "sm",
      cell: (row) => <span className="text-fg-muted">{formatCount(row.tables)}</span>,
      sortValue: (row) => row.tables,
    },
    {
      id: "size",
      header: t("databases.fields.size"),
      align: "end",
      mono: true,
      width: "w-28",
      cell: (row) => (row.size ? <span className="text-fg-muted">{row.size}</span> : <span className="text-fg-faint">-</span>),
      sortValue: (row) => row.size ?? null,
    },
  ];

  return (
    <DataTable
      columns={columns}
      rows={databases}
      getRowId={(row) => `${row.engine}/${row.name}`}
      caption={caption}
      loading={loading}
      {...(empty !== undefined ? { empty } : {})}
      rowActions={(row) => <RowActions database={row} />}
      defaultSort={{ column: "name", direction: "ascending" }}
      {...(className !== undefined ? { className } : {})}
    />
  );
}
