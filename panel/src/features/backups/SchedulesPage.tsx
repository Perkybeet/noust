import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { CalendarClock } from "lucide-react";
import { useState } from "react";

import { appsQuery } from "../../api/queries/apps";
import type { BackupSchedule } from "../../api/queries/backups";
import { backupSchedulesQuery } from "../../api/queries/backups";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Button, buttonClassName } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyState } from "../../components/ui/EmptyState";
import { IconButton } from "../../components/ui/IconButton";
import { ICONS } from "../../components/ui/icons";
import { Menu, MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { calendarWords } from "../cron/data";
import { BackupsListPage } from "./BackupsListPage";
import { ScheduleDialog } from "./ScheduleDialog";
import { useBackupActions } from "./useBackupActions";

/** How much a schedule keeps, in words: its own limits, or the server's default. */
function Keeps({ schedule, serverDefault, t }: { schedule: BackupSchedule; serverDefault: number | undefined; t: T }) {
  const count = schedule.retention_count ?? null;
  const days = schedule.retention_days ?? null;
  // Null is not unknown: it is backup.max_per_app over every backup, what an adopted 2.1 timer
  // has and what the operator chose when they left it on the server default.
  if (count === null && days === null) {
    return (
      <span className="text-fg-muted">
        {serverDefault !== undefined ? t("backups.schedules.serverDefault", { count: serverDefault }) : t("backups.schedules.serverDefaultUnknown")}
      </span>
    );
  }
  return (
    <span className="flex flex-col">
      {count !== null ? <span>{t("backups.schedules.keepsLast", { count })}</span> : null}
      {days !== null ? <span className="text-12 text-fg-muted">{t("backups.schedules.keepsDays", { count: days })}</span> : null}
    </span>
  );
}

function ScheduleActions({ schedule, onEdit, onRemove }: { schedule: BackupSchedule; onEdit: () => void; onRemove: () => void }) {
  const t = useT();
  return (
    <Menu align="end" trigger={<IconButton label={t("backups.schedules.actionsFor", { domain: schedule.domain })} icon={<ICONS.more />} size="sm" tooltip={false} />}>
      <MenuItem icon={<CalendarClock />} onClick={onEdit}>
        {t("backups.common.edit")}
      </MenuItem>
      <MenuSeparator />
      <MenuItem icon={<ICONS.delete />} destructive onClick={onRemove}>
        {t("backups.schedules.removeAction")}
      </MenuItem>
    </Menu>
  );
}

/**
 * The Schedules tab: what backs each application up by itself, when it next runs and ran
 * last, how many backups it keeps, and where each copy is also sent.
 */
export function SchedulesPage() {
  const t = useT();
  const schedules = useQuery(backupSchedulesQuery());
  const apps = useQuery(appsQuery());
  const { deleteSchedule } = useBackupActions();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<BackupSchedule | null>(null);
  const [removing, setRemoving] = useState<BackupSchedule | null>(null);
  const serverDefault = schedules.data?.default_retention_count;
  const hasApps = (apps.data?.apps.length ?? 0) > 0;
  const empty = schedules.data?.schedules.length === 0;

  const columns: Column<BackupSchedule>[] = [
    { id: "domain", header: t("backups.fields.application"), cell: (row) => row.domain, sortValue: (row) => row.domain },
    {
      id: "schedule",
      header: t("backups.schedules.columns.schedule"),
      cell: (row) => (
        <span className="inline-flex min-w-0 flex-col align-top">
          <span>{calendarWords(row.schedule, row.on_calendar, t.locale)}</span>
          <Mono tone="faint" truncate className="text-12 max-sm:hidden">
            {row.on_calendar}
          </Mono>
        </span>
      ),
    },
    {
      id: "next_run",
      header: t("backups.schedules.columns.nextRun"),
      width: "w-32",
      cell: (row) => (row.next_run === "pending" ? <span className="text-fg-muted">{t("backups.schedules.pending")}</span> : <RelativeTime value={row.next_run} />),
    },
    {
      id: "last_run",
      header: t("backups.schedules.columns.lastRun"),
      width: "w-32",
      hideBelow: "sm",
      cell: (row) => (row.last_run === "never" ? <span className="text-fg-muted">{t("time.never")}</span> : <RelativeTime value={row.last_run} />),
    },
    {
      id: "keeps",
      header: t("backups.schedules.columns.keeps"),
      hideBelow: "md",
      card: "hidden",
      cell: (row) => <Keeps schedule={row} serverDefault={serverDefault} t={t} />,
    },
    {
      id: "destinations",
      header: t("backups.schedules.columns.destinations"),
      hideBelow: "lg",
      card: "hidden",
      cell: (row) => {
        const names = (row.destinations ?? []).map((destination) => destination.name);
        return names.length > 0 ? (
          <Mono truncate title={names.join(", ")} className="max-w-48">
            {names.join(", ")}
          </Mono>
        ) : (
          <span className="text-fg-muted">{t("backups.schedules.localOnly")}</span>
        );
      },
    },
  ];

  const newButton = (variant: "primary" | "secondary") => (
    <Button variant={variant} icon={<ICONS.add aria-hidden="true" />} onClick={() => setCreating(true)}>
      {t("backups.schedules.newSchedule")}
    </Button>
  );

  let content;
  if (schedules.isError && schedules.data === undefined) {
    content = <ErrorBlock error={schedules.error} title={t("backups.schedules.loadError")} onRetry={() => void schedules.refetch()} retrying={schedules.isRefetching} />;
  } else if (empty) {
    content = (
      <EmptyState
        variant="firstUse"
        icon={<CalendarClock />}
        title={t("backups.schedules.empty.title")}
        description={hasApps || apps.isPending ? t("backups.schedules.empty.description") : t("backups.schedules.empty.noApps")}
        action={
          hasApps || apps.isPending ? (
            newButton("secondary")
          ) : (
            <Link to="/apps/new" className={buttonClassName("primary")}>
              {t("backups.coverage.empty.action")}
            </Link>
          )
        }
        command={hasApps || apps.isPending ? "noust backup schedule create <domain> --schedule daily" : "noust create -d example.com -s https://github.com/you/app"}
      />
    );
  } else {
    content = (
      <DataTable
        mobile="cards"
        columns={columns}
        rows={schedules.data?.schedules ?? []}
        getRowId={(row) => row.domain}
        caption={t("backups.schedules.caption")}
        loading={schedules.isPending}
        skeletonRows={2}
        rowActions={(row) => <ScheduleActions schedule={row} onEdit={() => setEditing(row)} onRemove={() => setRemoving(row)} />}
        defaultSort={{ column: "domain", direction: "ascending" }}
      />
    );
  }

  return (
    <BackupsListPage
      {...(hasApps ? { primaryAction: newButton("primary") } : {})}
      {...(empty ? {} : { footer: <CommandHint command="noust backup schedule list" label={t("backups.common.fromTerminal")} /> })}
    >
      {content}
      {creating ? <ScheduleDialog open onOpenChange={(next) => !next && setCreating(false)} /> : null}
      {editing !== null ? <ScheduleDialog key={editing.domain} existing={editing} open onOpenChange={(next) => !next && setEditing(null)} /> : null}
      <ConfirmDialog
        friction="simple"
        open={removing !== null}
        onOpenChange={(next) => {
          if (!next) setRemoving(null);
        }}
        title={t("backups.schedules.removeDialog.title", { domain: removing?.domain ?? "" })}
        description={t("backups.schedules.removeDialog.description")}
        actionLabel={t("backups.schedules.removeAction")}
        onConfirm={async () => {
          if (removing === null) return;
          await deleteSchedule.mutateAsync(removing.domain);
        }}
      />
    </BackupsListPage>
  );
}
