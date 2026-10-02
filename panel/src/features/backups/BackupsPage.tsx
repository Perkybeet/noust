import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Archive, Play } from "lucide-react";
import { useMemo, useState } from "react";

import { appsQuery } from "../../api/queries/apps";
import { backupSchedulesQuery, backupsQuery } from "../../api/queries/backups";
import type { BackupSchedule } from "../../api/queries/backups";
import { CommandHint } from "../../components/page/CommandHint";
import { FilterBar } from "../../components/page/FilterBar";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button, buttonClassName } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { FeatureState } from "../../components/ui/FeatureState";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import { BackupHistoryDrawer } from "./BackupHistoryDrawer";
import { BackupsListPage } from "./BackupsListPage";
import type { BackupRow } from "./BackupsTable";
import { coverageRows, filterCoverage, isCoverageFiltered } from "./coverage";
import type { CoverageRow, CoverageSearch } from "./coverage";
import { CoverageTable } from "./CoverageTable";
import { CreateBackupDialog } from "./CreateBackupDialog";
import { MisplacedBackupsNotice, useMisplacedBackups } from "./MisplacedBackupsNotice";
import { PushBackupDialog } from "./PushBackupDialog";
import { RestoreBackupDialog } from "./RestoreBackupDialog";
import { ScheduleDialog } from "./ScheduleDialog";
import { useBackupActions } from "./useBackupActions";
import { useBackupRefresh } from "./useBackupRefresh";

const ALL = "all";

type SearchPatch = { [K in keyof CoverageSearch]?: CoverageSearch[K] | undefined };

/** The dialog the page has open, with what it acts on. */
type Open =
  | { kind: "create"; domain?: string }
  | { kind: "restore"; backup: BackupRow }
  | { kind: "copy"; backup: BackupRow }
  | { kind: "delete"; backup: BackupRow }
  | { kind: "schedule"; domain: string; existing: BackupSchedule | null }
  | null;

export interface BackupsPageProps {
  search: CoverageSearch;
  onSearchChange: (search: CoverageSearch, options?: { replace?: boolean }) => void;
}

/**
 * The Backups tab: is every application protected, and restore this one. One row per
 * application (its state, its newest backup, what schedules the next, where copies go); a row
 * opens that application's backups in a drawer, where each can be checked, restored, copied
 * to a destination or deleted. Creating a backup by hand is the header's action.
 */
export function BackupsPage({ search, onSearchChange }: BackupsPageProps) {
  const t = useT();
  useBackupRefresh();
  const apps = useQuery(appsQuery());
  const backups = useQuery(backupsQuery(null));
  const schedules = useQuery(backupSchedulesQuery());
  const misplaced = useMisplacedBackups();
  const { remove } = useBackupActions();
  const [open, setOpen] = useState<Open>(null);

  const rows = useMemo(
    () => coverageRows({ apps: apps.data?.apps ?? [], backups: backups.data?.backups ?? [], schedules: schedules.data?.schedules ?? [] }),
    [apps.data, backups.data, schedules.data],
  );
  const shown = useMemo(() => filterCoverage(rows, search), [rows, search]);
  const filtered = isCoverageFiltered(search);
  const loading = apps.isPending || backups.isPending;
  const hasApps = (apps.data?.apps.length ?? 0) > 0;
  const nothing = !loading && rows.length === 0;
  const noneBackedUp = !loading && hasApps && (backups.data?.backups.length ?? 0) === 0;
  const drawerRow = search.domain === undefined ? null : (rows.find((row) => row.domain === search.domain) ?? null);

  const set = (patch: SearchPatch, replace = true): void => {
    const next: SearchPatch = { ...search, ...patch };
    const clean: CoverageSearch = {};
    if (next.q) clean.q = next.q;
    if (next.show) clean.show = next.show;
    if (next.domain) clean.domain = next.domain;
    onSearchChange(clean, { replace });
  };
  const openRow = (row: CoverageRow): void => set({ domain: row.domain }, false);
  const openSchedule = (row: CoverageRow): void => setOpen({ kind: "schedule", domain: row.domain, existing: row.schedule });
  const close = (): void => setOpen(null);

  const backUpButton = hasApps ? (
    <Button variant="primary" icon={<Play aria-hidden="true" />} onClick={() => setOpen({ kind: "create" })}>
      {t("backups.page.backUpNow")}
    </Button>
  ) : undefined;

  const notice = misplaced ? (
    <MisplacedBackupsNotice />
  ) : noneBackedUp ? (
    // Scheduled backups are off for every application: the feature's state, not a message.
    <FeatureState
      state="off"
      title={t("backups.coverage.noneBackedUp.title")}
      action={
        <Button size="sm" onClick={() => setOpen({ kind: "schedule", domain: "", existing: null })}>
          {t("backups.coverage.noneBackedUp.action")}
        </Button>
      }
    >
      {t("backups.coverage.noneBackedUp.description")}
    </FeatureState>
  ) : undefined;

  let content;
  if (backups.isError && backups.data === undefined) {
    content = <ErrorBlock error={backups.error} title={t("backups.page.loadError")} onRetry={() => void backups.refetch()} retrying={backups.isRefetching} />;
  } else if (nothing) {
    content = (
      <EmptyState
        variant="firstUse"
        icon={<Archive />}
        title={t("backups.coverage.empty.title")}
        description={t("backups.coverage.empty.description")}
        action={
          <Link to="/apps/new" className={buttonClassName("primary")}>
            {t("backups.coverage.empty.action")}
          </Link>
        }
        command="noust create -d example.com -s https://github.com/you/app"
      />
    );
  } else {
    content = (
      <CoverageTable
        rows={shown}
        caption={filtered ? t("backups.coverage.captionFiltered") : t("backups.coverage.caption")}
        loading={loading}
        skeletonRows={Math.max(1, Math.min(apps.data?.apps.length ?? 5, 20))}
        empty={
          <EmptyState
            variant="inline"
            title={t("backups.coverage.noMatch")}
            action={
              <Button size="sm" variant="ghost" onClick={() => onSearchChange(search.domain !== undefined ? { domain: search.domain } : {})}>
                {t("backups.common.clearFilters")}
              </Button>
            }
          />
        }
        onOpen={openRow}
        onBackUp={(domain) => setOpen({ kind: "create", domain })}
        onRestore={(backup) => setOpen({ kind: "restore", backup })}
        onSchedule={openSchedule}
      />
    );
  }

  return (
    <BackupsListPage
      {...(backUpButton !== undefined ? { primaryAction: backUpButton } : {})}
      {...(notice !== undefined ? { notice } : {})}
      {...(nothing
        ? {}
        : {
            filters: (
              <FilterBar
                label={t("backups.coverage.filterLabel")}
                search={{
                  value: search.q ?? "",
                  onChange: (value) => set({ q: value === "" ? undefined : value }),
                  label: t("backups.coverage.searchLabel"),
                  placeholder: t("backups.coverage.searchPlaceholder"),
                }}
                filters={
                  <Select
                    aria-label={t("backups.coverage.showLabel")}
                    value={search.show ?? ALL}
                    onValueChange={(value) => set({ show: value === "attention" ? "attention" : undefined })}
                    options={[
                      { value: ALL, label: t("backups.coverage.showAll") },
                      { value: "attention", label: t("backups.coverage.showAttention") },
                    ]}
                  />
                }
                count={
                  loading
                    ? undefined
                    : filtered
                      ? t("backups.coverage.countFiltered", { shown: shown.length, total: rows.length })
                      : t("backups.coverage.count", { count: rows.length })
                }
              />
            ),
          })}
      // The first-use state says its own command: one per view.
      {...(nothing ? {} : { footer: <CommandHint command="noust backup list" label={t("backups.common.fromTerminal")} /> })}
    >
      {content}

      <BackupHistoryDrawer
        row={drawerRow}
        domain={search.domain ?? null}
        loading={loading}
        onClose={() => set({ domain: undefined })}
        onBackUp={(domain) => setOpen({ kind: "create", domain })}
        onRestore={(backup) => setOpen({ kind: "restore", backup })}
        onCopy={(backup) => setOpen({ kind: "copy", backup })}
        onDelete={(backup) => setOpen({ kind: "delete", backup })}
      />

      {/* Mounted per use and keyed: a dialog opened for another backup or application starts
          fresh, with none of the previous one's typing. */}
      {open?.kind === "create" ? (
        <CreateBackupDialog key={open.domain ?? "*"} open onOpenChange={(next) => !next && close()} {...(open.domain !== undefined ? { domain: open.domain } : {})} />
      ) : null}
      {open?.kind === "restore" ? (
        <RestoreBackupDialog key={open.backup.backup_id} backup={open.backup} open onOpenChange={(next) => !next && close()} />
      ) : null}
      {open?.kind === "copy" ? <PushBackupDialog key={open.backup.backup_id} backup={open.backup} open onOpenChange={(next) => !next && close()} /> : null}
      {open?.kind === "delete" ? (
        <ConfirmDialog
          key={open.backup.backup_id}
          open
          onOpenChange={(next) => !next && close()}
          title={t("backups.deleteDialog.title", { domain: open.backup.domain })}
          description={t("backups.deleteDialog.description", { id: open.backup.backup_id })}
          confirmText={open.backup.backup_id}
          actionLabel={t("backups.deleteDialog.action")}
          onConfirm={async () => {
            await remove.mutateAsync(open.backup.backup_id);
          }}
        />
      ) : null}
      {open?.kind === "schedule" ? (
        <ScheduleDialog
          key={open.domain}
          open
          onOpenChange={(next) => !next && close()}
          {...(open.existing !== null ? { existing: open.existing } : open.domain !== "" ? { domain: open.domain } : {})}
        />
      ) : null}
    </BackupsListPage>
  );
}
