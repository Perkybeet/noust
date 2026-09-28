import { useQuery } from "@tanstack/react-query";
import { MoreHorizontal, Plus, ShieldMinus, ShieldPlus, Trash2, UserRound } from "lucide-react";
import { useState } from "react";

import type { DatabaseUser, Engine } from "../../api/queries/databases";
import { databaseUsersQuery } from "../../api/queries/databases";
import { QueryState } from "../../components/page/QueryState";
import { Section } from "../../components/page/Section";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyState } from "../../components/ui/EmptyState";
import { IconButton } from "../../components/ui/IconButton";
import { Menu, MenuItem } from "../../components/ui/Menu";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import { CreateUserDialog } from "./CreateUserDialog";
import { engineLabel } from "./data";
import { GrantDialog } from "./GrantDialog";
import type { GrantMode } from "./GrantDialog";
import { useDatabaseActions } from "./useDatabaseActions";

function UserActions({ user }: { user: DatabaseUser }) {
  const t = useT();
  const { deleteUser } = useDatabaseActions();
  const [grantMode, setGrantMode] = useState<GrantMode | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  return (
    <>
      <Menu
        align="end"
        trigger={<IconButton label={t("databases.table.actionsFor", { name: user.username })} icon={<MoreHorizontal />} size="sm" tooltip={false} />}
      >
        <MenuItem icon={<ShieldPlus />} onClick={() => setGrantMode("grant")}>
          {t("databases.users.grantPrivileges")}
        </MenuItem>
        <MenuItem icon={<ShieldMinus />} onClick={() => setGrantMode("revoke")}>
          {t("databases.users.revokePrivileges")}
        </MenuItem>
        <MenuItem icon={<Trash2 />} destructive onClick={() => setConfirmOpen(true)}>
          {t("databases.users.deleteUser")}
        </MenuItem>
      </Menu>
      <GrantDialog
        mode={grantMode ?? "grant"}
        user={user}
        open={grantMode !== null}
        onOpenChange={(next) => {
          if (!next) setGrantMode(null);
        }}
      />
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={t("databases.users.deleteTitle", { username: user.username })}
        description={t("databases.users.deleteDescription", { username: user.username, engine: engineLabel(user.engine) })}
        confirmText={user.username}
        actionLabel={t("databases.users.deleteUser")}
        onConfirm={async () => {
          await deleteUser.mutateAsync({ engine: user.engine, username: user.username, host: user.host });
        }}
      />
    </>
  );
}

export interface UsersPanelProps {
  engines: readonly Engine[];
  /** The engines are still loading: which of them run is not known yet. */
  loading?: boolean;
  engine: string;
  onEngineChange: (engine: string) => void;
}

/** The users of one engine, with grant, revoke and delete - engine-scoped, like the CLI's `wasm db user` commands. */
export function UsersPanel({ engines, loading = false, engine, onEngineChange }: UsersPanelProps) {
  const t = useT();
  const users = useQuery({ ...databaseUsersQuery(engine), enabled: engine !== "" });
  const runnable = engines.filter((item) => item.installed && item.running);

  const columns: Column<DatabaseUser>[] = [
    { id: "username", header: t("databases.fields.username"), mono: true, cell: (row) => row.username, sortValue: (row) => row.username },
    { id: "host", header: t("databases.fields.host"), mono: true, width: "w-40", hideBelow: "sm", cell: (row) => row.host, sortValue: (row) => row.host },
    {
      id: "privileges",
      header: t("databases.fields.privileges"),
      cell: (row) =>
        (row.privileges ?? []).length === 0 ? (
          <span className="text-fg-faint">{t("databases.users.defaultPrivileges")}</span>
        ) : (
          <span className="flex flex-wrap gap-1">
            {(row.privileges ?? []).map((privilege) => (
              <Badge key={privilege} mono>
                {privilege}
              </Badge>
            ))}
          </span>
        ),
    },
  ];

  return (
    <Section
      title={t("databases.users.title")}
      description={t("databases.users.description")}
      actions={
        <>
          <Select
            aria-label={t("databases.fields.engine")}
            size="sm"
            value={engine}
            onValueChange={onEngineChange}
            options={runnable.map((item) => ({ value: item.name, label: engineLabel(item.name) }))}
            disabled={runnable.length === 0}
          />
          <CreateUserDialog
            engines={runnable}
            trigger={
              <Button size="sm" icon={<Plus aria-hidden="true" />} disabled={runnable.length === 0}>
                {t("databases.users.newUser")}
              </Button>
            }
          />
        </>
      }
    >
      {loading ? (
        // The table's own placeholder: until the engines answer, "No running engine" would be
        // a guess, and a card of that size swapped for a table moved everything around it.
        <div aria-busy="true">
          <span className="sr-only">{t("databases.users.loading")}</span>
          <DataTable columns={columns} rows={[]} getRowId={() => ""} caption={t("databases.users.tableCaption")} loading />
        </div>
      ) : runnable.length === 0 ? (
        <EmptyState
          level={3}
          icon={<UserRound />}
          title={t("databases.users.noEngineTitle")}
          description={t("databases.users.noEngineDescription")}
        />
      ) : (
        <QueryState
          query={users}
          label={t("databases.queryLabels.users")}
          skeleton={
            <DataTable
              columns={columns}
              rows={[]}
              getRowId={() => ""}
              caption={t("databases.users.tableCaptionOn", { engine: engineLabel(engine) })}
              loading
            />
          }
          isEmpty={(data) => data.users.length === 0}
          empty={
            <EmptyState
              icon={<UserRound />}
              title={t("databases.users.emptyTitle")}
              description={t("databases.users.emptyDescription", { engine: engineLabel(engine) })}
            />
          }
        >
          {(data) => (
            <DataTable
              columns={columns}
              rows={data.users}
              getRowId={(row) => `${row.username}@${row.host}`}
              caption={t("databases.users.tableCaptionOn", { engine: engineLabel(engine) })}
              rowActions={(row) => <UserActions user={row} />}
              defaultSort={{ column: "username", direction: "ascending" }}
            />
          )}
        </QueryState>
      )}
    </Section>
  );
}
