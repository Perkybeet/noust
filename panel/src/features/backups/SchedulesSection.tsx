import { useQuery } from "@tanstack/react-query";
import { CalendarClock, MoreHorizontal, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import type { BackupSchedule } from "../../api/queries/backups";
import { backupSchedulesQuery } from "../../api/queries/backups";
import { QueryState } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Section } from "../../components/page/Section";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyState } from "../../components/ui/EmptyState";
import { IconButton } from "../../components/ui/IconButton";
import { Menu, MenuItem } from "../../components/ui/Menu";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { ScheduleDialog } from "./ScheduleDialog";
import { useBackupActions } from "./useBackupActions";

function ScheduleActions({ schedule }: { schedule: BackupSchedule }) {
  const t = useT();
  const { deleteSchedule } = useBackupActions();
  const [editOpen, setEditOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  return (
    <>
      <Menu
        align="end"
        trigger={<IconButton label={t("backups.schedules.actionsFor", { domain: schedule.domain })} icon={<MoreHorizontal />} size="sm" tooltip={false} />}
      >
        <MenuItem icon={<CalendarClock />} onClick={() => setEditOpen(true)}>
          {t("backups.common.edit")}
        </MenuItem>
        <MenuItem icon={<Trash2 />} destructive onClick={() => setConfirmOpen(true)}>
          {t("backups.schedules.removeAction")}
        </MenuItem>
      </Menu>
      <ScheduleDialog existing={schedule} open={editOpen} onOpenChange={setEditOpen} />
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={t("backups.schedules.removeDialog.title", { domain: schedule.domain })}
        description={t("backups.schedules.removeDialog.description")}
        confirmText={schedule.domain}
        actionLabel={t("backups.schedules.removeAction")}
        destructive
        onConfirm={async () => {
          await deleteSchedule.mutateAsync(schedule.domain);
        }}
      />
    </>
  );
}

/** Automatic backups on a systemd timer, one per application, with the next run systemd reports. */
export function SchedulesSection() {
  const t = useT();
  const schedules = useQuery(backupSchedulesQuery());
  const [createOpen, setCreateOpen] = useState(false);

  const columns: Column<BackupSchedule>[] = [
    { id: "domain", header: t("backups.fields.application"), cell: (row) => row.domain, sortValue: (row) => row.domain },
    {
      id: "schedule",
      header: t("backups.schedules.columns.schedule"),
      cell: (row) => (
        <span className="flex flex-col">
          <span className="capitalize text-fg">{row.schedule}</span>
          <span translate="no" className="mono text-12 text-fg-faint">
            {row.on_calendar}
          </span>
        </span>
      ),
    },
    {
      id: "next_run",
      header: t("backups.schedules.columns.nextRun"),
      cell: (row) => (row.next_run === "pending" ? <span className="text-fg-faint">{t("backups.schedules.pending")}</span> : <RelativeTime value={row.next_run} />),
    },
    {
      id: "last_run",
      header: t("backups.schedules.columns.lastRun"),
      hideBelow: "sm",
      cell: (row) => (row.last_run === "never" ? <span className="text-fg-faint">{t("time.never")}</span> : <RelativeTime value={row.last_run} />),
    },
    {
      id: "retention",
      header: t("backups.schedules.columns.retention"),
      hideBelow: "md",
      // Null is not unknown: it is backup.max_per_app over every backup, what an adopted 2.1
      // timer has and what the operator chose when they left it on the server default.
      cell: (row) =>
        (row.retention_count ?? null) === null && (row.retention_days ?? null) === null ? (
          <span className="text-fg-muted">{t("backups.schedules.serverDefault")}</span>
        ) : (
          <span className="flex flex-wrap gap-1">
            {row.retention_count !== null && row.retention_count !== undefined ? (
              <Badge mono>{t("backups.schedules.retentionBackupsBadge", { count: row.retention_count })}</Badge>
            ) : null}
            {row.retention_days !== null && row.retention_days !== undefined ? (
              <Badge mono>{t("backups.schedules.retentionDaysBadge", { count: row.retention_days })}</Badge>
            ) : null}
          </span>
        ),
    },
    {
      id: "destinations",
      header: t("backups.schedules.columns.destinations"),
      hideBelow: "lg",
      cell: (row) =>
        row.destinations !== undefined && row.destinations.length > 0 ? (
          <span className="flex flex-wrap gap-1">
            {row.destinations.map((destination) => (
              <Badge key={destination.name} mono>
                {destination.name}
              </Badge>
            ))}
          </span>
        ) : (
          <span className="text-fg-faint">{t("backups.schedules.localOnly")}</span>
        ),
    },
  ];

  return (
    <Section
      title={t("backups.schedules.sectionTitle")}
      description={t("backups.schedules.sectionDescription")}
      // Secondary: the page's primary is New backup. Hidden while the empty state offers it.
      actions={
        schedules.data !== undefined && schedules.data.schedules.length > 0 ? (
          <Button size="sm" icon={<Plus aria-hidden="true" />} onClick={() => setCreateOpen(true)}>
            {t("backups.schedules.newSchedule")}
          </Button>
        ) : undefined
      }
    >
      <QueryState
        query={schedules}
        label={t("backups.schedules.queryLabel")}
        skeleton={
          <div aria-hidden="true" className="flex flex-col gap-2">
            {[0, 1].map((i) => (
              <Skeleton key={i} className="h-10 rounded-card" />
            ))}
          </div>
        }
        isEmpty={(data) => data.schedules.length === 0}
        empty={
          <EmptyState
            icon={<CalendarClock />}
            title={t("backups.schedules.empty.title")}
            description={t("backups.schedules.empty.description")}
            action={
              <Button icon={<Plus aria-hidden="true" />} onClick={() => setCreateOpen(true)}>
                {t("backups.schedules.newSchedule")}
              </Button>
            }
            command="noust backup schedule <domain> --schedule daily"
          />
        }
      >
        {(data) => (
          <DataTable
            columns={columns}
            rows={data.schedules}
            getRowId={(row) => row.domain}
            caption={t("backups.schedules.caption")}
            rowActions={(row) => <ScheduleActions schedule={row} />}
            defaultSort={{ column: "domain", direction: "ascending" }}
          />
        )}
      </QueryState>
      <ScheduleDialog open={createOpen} onOpenChange={setCreateOpen} />
    </Section>
  );
}
