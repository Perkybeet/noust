import { useQuery } from "@tanstack/react-query";

import { backupStorageQuery } from "../../api/queries/backups";
import { CommandHint } from "../../components/page/CommandHint";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { useT } from "../../i18n";

/** Whether the storage report found backups outside the backup directory. */
export function useMisplacedBackups(): boolean {
  const storage = useQuery(backupStorageQuery());
  return (storage.data?.misplaced ?? []).length > 0;
}

/**
 * Backups Noust found outside the configured backup directory - in the old default one, or
 * where an empty `backup.directory` once sent them - which this page does not list and a
 * restore cannot reach until they are imported. Each place comes with the exact command that
 * moves them in; nothing is shown when there are none.
 */
export function MisplacedBackupsNotice() {
  const t = useT();
  const storage = useQuery(backupStorageQuery());
  const misplaced = storage.data?.misplaced ?? [];
  if (storage.data === undefined || misplaced.length === 0) return null;
  const total = misplaced.reduce((sum, found) => sum + found.count, 0);

  return (
    <Notice tone="warning" title={t("backups.misplaced.heading", { count: total })}>
      <div className="flex min-w-0 flex-col gap-3">
        <p className="max-w-measure">{t.rich("backups.misplaced.intro", { path: <Mono tone="default">{storage.data.path}</Mono> })}</p>
        <ul className="flex min-w-0 flex-col gap-2">
          {misplaced.map((found) => (
            <li key={found.directory} className="flex min-w-0 flex-col gap-1">
              <p className="text-fg">
                {t.rich("backups.misplaced.foundIn", { count: found.count, directory: <Mono tone="default">{found.directory}</Mono> })}
              </p>
              <CommandHint command={found.command} label={t("backups.misplaced.importLabel")} />
            </li>
          ))}
        </ul>
        <p className="text-12">{t.rich("backups.misplaced.dryRunHint", { flag: <Mono tone="default">--dry-run</Mono> })}</p>
      </div>
    </Notice>
  );
}
