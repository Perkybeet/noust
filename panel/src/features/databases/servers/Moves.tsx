import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { migrationsQuery } from "../../../api/queries/databaseInstances";
import type { Migration } from "../../../api/queries/databaseInstances";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { RowMenu } from "../../../components/page/RowMenu";
import { Section } from "../../../components/page/Section";
import { Badge } from "../../../components/ui/Badge";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { MenuItem } from "../../../components/ui/Menu";
import { Mono } from "../../../components/ui/Mono";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { StatusPill } from "../../../components/ui/StatusPill";
import type { Status } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { olderServer } from "../../app/settings/queries";
import type { InstanceActions } from "../instances/useInstanceActions";

/** The state a move is in, as the status vocabulary tells it (colour, shape and word). */
function moveStatus(state: string): Status {
  switch (state) {
    case "done":
      return "running";
    case "failed":
      return "failed";
    case "rolled_back":
      return "stopped";
    default:
      return "deploying";
  }
}

function moveState(t: T, state: string): string {
  switch (state) {
    case "done":
      return t("common.words.done");
    case "failed":
      return t("databases.moves.states.failed");
    case "rolled_back":
      return t("databases.moves.states.rolledBack");
    case "rolling_back":
      return t("databases.moves.states.rollingBack");
    default:
      return t("databases.moves.states.moving");
  }
}

/**
 * The moves of a server's databases to a newer version, and the way back of each (`GET .../instances/migrations`).
 * Nothing is drawn by a server that has made none, or that is too old to keep them.
 */
export function Moves({ actions }: { actions: Pick<InstanceActions, "busy" | "follow"> }) {
  const t = useT();
  const { node } = useNode();
  const query = useQuery(migrationsQuery());
  const [rollingBack, setRollingBack] = useState<Migration | null>(null);
  if (query.isError && olderServer(query.error)) return null;
  const rows = query.data?.migrations ?? [];
  // Nothing is drawn until it is known there is something: a server that never made a move would see a table appear and go.
  if (query.data === undefined || rows.length === 0) return null;
  const columns: Column<Migration>[] = [
    {
      id: "id",
      header: t("databases.moves.number"),
      width: "w-20",
      mono: true,
      card: "title",
      cell: (row) => String(row.id),
    },
    {
      id: "move",
      header: t("databases.moves.move"),
      cell: (row) => (
        <span className="flex min-w-0 flex-wrap items-center gap-x-2">
          <Mono tone="default">{row.source}</Mono>
          <span className="text-fg-muted">{t("databases.moves.to")}</span>
          <Mono tone="default">{row.target}</Mono>
          <Badge mono>{row.to_version}</Badge>
        </span>
      ),
    },
    {
      id: "state",
      header: t("databases.moves.state"),
      width: "w-36",
      card: "status",
      cell: (row) => <StatusPill size="sm" state={moveStatus(row.state)} label={moveState(t, row.state)} />,
    },
    {
      id: "databases",
      header: t("databases.moves.databases"),
      width: "w-32",
      hideBelow: "sm",
      cell: (row) => (row.moved ?? []).map((entry) => entry.database).join(", ") || t("databases.moves.none"),
    },
    { id: "when", header: t("databases.moves.when"), width: "w-36", hideBelow: "md", cell: (row) => <RelativeTime value={row.created_at} /> },
  ];
  return (
    <Section title={t("databases.moves.title")} description={t("databases.moves.description")}>
      <DataTable
        columns={columns}
        rows={rows}
        getRowId={(row) => String(row.id)}
        caption={t("databases.moves.caption")}
        rowActions={(row) =>
          row.can_roll_back ? (
            <RowMenu name={`#${String(row.id)}`}>
              <MenuItem icon={<OBJECT_ICONS.rollback />} disabled={actions.busy} onClick={() => setRollingBack(row)}>
                {t("databases.moves.rollback")}
              </MenuItem>
            </RowMenu>
          ) : null
        }
      />
      {rollingBack !== null ? (
        <ConfirmDialog
          open
          friction="simple"
          onOpenChange={(open) => (open ? undefined : setRollingBack(null))}
          server={node}
          title={t("databases.moves.rollbackTitle", { number: rollingBack.id })}
          description={t("databases.moves.rollbackDescription", { source: rollingBack.source, target: rollingBack.target })}
          actionLabel={t("databases.moves.rollbackAction")}
          onConfirm={async () => {
            const accepted = await request("post", "/api/databases/instances/migrations/{migration_id}/rollback", { params: { migration_id: rollingBack.id } });
            actions.follow(accepted.job_id, {
              running: t("databases.moves.rollingBack", { number: rollingBack.id }),
              succeeded: t("databases.moves.rolledBack", { number: rollingBack.id }),
              failed: t("databases.moves.rollbackFailed", { number: rollingBack.id }),
            });
          }}
        />
      ) : null}
    </Section>
  );
}
