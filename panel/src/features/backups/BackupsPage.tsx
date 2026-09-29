import { useQuery } from "@tanstack/react-query";
import { Archive, Plus, X } from "lucide-react";
import { useMemo } from "react";

import { backupSchedulesQuery, backupStorageQuery, backupsQuery } from "../../api/queries/backups";
import { PageHeader } from "../../app/PageHeader";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import { Section } from "../../components/page/Section";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { EmptyState } from "../../components/ui/EmptyState";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import { BackupsTable } from "./BackupsTable";
import { CreateBackupDialog } from "./CreateBackupDialog";
import { DestinationsSection } from "./DestinationsSection";
import { backupDomains, filterBackups, isFiltered } from "./filters";
import type { BackupsSearch } from "./filters";
import { MisplacedBackupsNotice } from "./MisplacedBackupsNotice";
import { SchedulesSection } from "./SchedulesSection";
import { StorageUsageBar } from "./StorageUsageBar";
import { useBackupRefresh } from "./useBackupRefresh";

const ALL = "all";
/** The most placeholder rows worth drawing: past a screenful, more only lengthens the page below the fold. */
const MAX_SKELETON_ROWS = 20;

type SearchPatch = { [K in keyof BackupsSearch]?: BackupsSearch[K] | undefined };

export interface BackupsPageProps {
  search: BackupsSearch;
  onSearchChange: (search: BackupsSearch, options?: { replace?: boolean }) => void;
}

/** Every backup on the machine, its storage footprint, and the schedules that create more of them. */
export function BackupsPage({ search, onSearchChange }: BackupsPageProps) {
  const t = useT();
  useBackupRefresh();
  const backups = useQuery(backupsQuery(search.domain ?? null));
  const all = useMemo(() => backups.data?.backups ?? [], [backups.data]);
  const shown = useMemo(() => filterBackups(all, search), [all, search]);
  const domains = useMemo(() => backupDomains(all), [all]);
  const filtered = isFiltered(search);
  // The storage summary counts every backup and usually answers first: the list's placeholder
  // holds that many rows, so the schedules below do not jump when the list lands.
  const storage = useQuery(backupStorageQuery());
  const expected = search.domain === undefined ? storage.data?.backup_count : undefined;
  // Fetched from the start, though drawn only once the list is in (below).
  useQuery(backupSchedulesQuery());

  const set = (patch: SearchPatch): void => {
    const next: SearchPatch = { ...search, ...patch };
    const clean: BackupsSearch = {};
    if (next.domain) clean.domain = next.domain;
    if (next.database) clean.database = true;
    onSearchChange(clean);
  };

  return (
    <>
      <PageHeader
        title={t("backups.page.title")}
        description={t("backups.page.description")}
        actions={
          <CreateBackupDialog
            trigger={
              <Button variant="primary" icon={<Plus aria-hidden="true" />}>
                {t("backups.page.newBackup")}
              </Button>
            }
          />
        }
      />
      <div className="flex flex-col gap-8">
        <StorageUsageBar />
        <MisplacedBackupsNotice />

        <Section title={t("backups.page.title")}>
          {backups.isError && backups.data === undefined ? (
            <ErrorBlock error={backups.error} title={t("backups.page.loadError")} onRetry={() => void backups.refetch()} retrying={backups.isRefetching} />
          ) : backups.data !== undefined && all.length === 0 ? (
            <EmptyState
              icon={<Archive />}
              title={t("backups.page.empty.title")}
              description={t("backups.page.empty.description")}
              action={
                <CreateBackupDialog
                  trigger={
                    <Button variant="primary" icon={<Plus aria-hidden="true" />}>
                      {t("backups.page.newBackup")}
                    </Button>
                  }
                />
              }
              command="noust backup create <domain>"
              className="py-16"
            />
          ) : (
            <div className="flex flex-col gap-4">
              <div role="search" aria-label={t("backups.page.filterAria")} className="flex flex-wrap items-center gap-2">
                <Select
                  aria-label={t("backups.fields.application")}
                  size="sm"
                  value={search.domain ?? ALL}
                  onValueChange={(value) => set({ domain: value === ALL ? undefined : value })}
                  options={[{ value: ALL, label: t("backups.page.everyApplication") }, ...domains.map((domain) => ({ value: domain, label: domain }))]}
                />
                <Checkbox
                  checked={search.database === true}
                  onCheckedChange={(checked) => set({ database: checked ? true : undefined })}
                  label={t("backups.page.includesDatabaseFilter")}
                />
                {filtered ? (
                  <Button size="sm" variant="ghost" icon={<X aria-hidden="true" />} onClick={() => onSearchChange({})}>
                    {t("backups.common.clearFilters")}
                  </Button>
                ) : null}
              </div>
              <BackupsTable
                backups={shown}
                caption={filtered ? t("backups.table.captionFiltered") : t("backups.table.captionAll")}
                loading={backups.isPending}
                {...(expected !== undefined && expected > 0 ? { skeletonRows: Math.min(expected, MAX_SKELETON_ROWS) } : {})}
                empty={
                  <EmptyState
                    title={t("backups.page.noMatch.title")}
                    description={t("backups.page.noMatch.description")}
                    action={
                      <Button icon={<X aria-hidden="true" />} onClick={() => onSearchChange({})}>
                        {t("backups.common.clearFilters")}
                      </Button>
                    }
                    className="border-0 py-8"
                  />
                }
              />
              <CommandHint command="noust backup list" label={t("backups.common.fromTerminal")} />
            </div>
          )}
        </Section>

        {/* Under a list whose length is not known until it loads: drawn once it has, so
            destinations and schedules never jump down the page as the backups arrive above them. */}
        {backups.data !== undefined || backups.isError ? (
          <>
            <DestinationsSection />
            <SchedulesSection />
          </>
        ) : null}
      </div>
    </>
  );
}
