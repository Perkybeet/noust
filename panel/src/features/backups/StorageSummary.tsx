import { useQuery } from "@tanstack/react-query";
import { Archive, HardDrive } from "lucide-react";

import { backupStorageQuery } from "../../api/queries/backups";
import type { BackupStorage } from "../../api/queries/backups";
import { CommandHint } from "../../components/page/CommandHint";
import { IconButton } from "../../components/ui/IconButton";
import { ICONS } from "../../components/ui/icons";
import { Mono } from "../../components/ui/Mono";
import { Popover } from "../../components/ui/Popover";
import { Meter } from "../../components/ui/Progress";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusGlyph, stateTextClass } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { formatBytes } from "../../lib/format";

/** The Meter's own thresholds: the summary line turns amber and red with it. */
const WARN = 0.75;
const FAIL = 0.9;

/** The filesystem the backup directory is on, when the server could read it. */
export function backupFilesystem(storage: BackupStorage): { total: number; free: number; used: number } | null {
  const total = storage.filesystem_total ?? null;
  const free = storage.filesystem_free ?? null;
  if (total === null || free === null || total <= 0) return null;
  return { total, free, used: Math.max(0, total - free) };
}

/** How full that filesystem is, in the Meter's words: nothing to say, high, or critical. */
export function diskLevel(filesystem: { total: number; used: number }): "normal" | "warn" | "fail" {
  const ratio = filesystem.used / filesystem.total;
  return ratio >= FAIL ? "fail" : ratio >= WARN ? "warn" : "normal";
}

/**
 * What the backups add up to and how much room is left for them, as one line of facts in the
 * page header: the count and size, then the free space on the disk the backup directory is on.
 * That disk is the directory's own filesystem, not the machine's root disk and not a quota
 * (Noust sets none): the popover beside it says so, with the directory and a meter.
 */
export function StorageSummary() {
  const t = useT();
  const storage = useQuery(backupStorageQuery());

  if (storage.isError && storage.data === undefined) {
    return <span className="text-fg-faint">{t("backups.storage.loadError")}</span>;
  }
  if (storage.data === undefined) {
    // The loaded line's height: its tallest part is the 20px info button.
    return (
      <span aria-hidden="true" className="flex h-5 items-center gap-3">
        <Skeleton className="h-3 w-32" />
        <Skeleton className="h-3 w-48" />
      </span>
    );
  }

  const data = storage.data;
  const filesystem = backupFilesystem(data);
  const level = filesystem === null ? "normal" : diskLevel(filesystem);

  return (
    <>
      <span className="flex items-center gap-1.5">
        <Archive aria-hidden="true" className="size-icon-sm text-fg-faint" />
        <span className="tabular-nums">
          {t("backups.storage.total", { count: data.backup_count, size: formatBytes(data.total_size, t.locale) })}
        </span>
      </span>
      <span className="flex items-center gap-1.5">
        {level === "normal" ? (
          <HardDrive aria-hidden="true" className="size-icon-sm text-fg-faint" />
        ) : (
          <StatusGlyph state={level === "fail" ? "failed" : "warning"} className={stateTextClass(level === "fail" ? "failed" : "warning")} />
        )}
        {level !== "normal" ? <span className="sr-only">{t(level === "fail" ? "common.meter.critical" : "common.meter.high")}</span> : null}
        <span className={cx("tabular-nums", level !== "normal" && "font-medium text-fg")}>
          {filesystem === null
            ? t("backups.storage.diskUnknown")
            : t("backups.storage.disk", { free: formatBytes(filesystem.free, t.locale), total: formatBytes(filesystem.total, t.locale) })}
        </span>
        <Popover
          trigger={<IconButton label={t("backups.storage.about")} icon={<ICONS.info />} size="sm" className="-my-1" />}
          title={t("backups.storage.aboutTitle")}
          description={t("backups.storage.aboutDescription")}
          align="start"
        >
          <div className="flex flex-col gap-3">
            {filesystem !== null ? (
              <Meter
                label={t("backups.storage.diskLabel")}
                value={filesystem.used}
                max={filesystem.total}
                size="sm"
                valueText={t("backups.storage.diskValueText", { used: formatBytes(filesystem.used, t.locale), total: formatBytes(filesystem.total, t.locale) })}
              />
            ) : null}
            <p className="text-13 text-fg-muted">{t.rich("backups.storage.keptIn", { path: <Mono tone="default">{data.path}</Mono> })}</p>
            <CommandHint command="noust backup storage" />
          </div>
        </Popover>
      </span>
    </>
  );
}
