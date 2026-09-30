import { FileCheck, RotateCcw, UploadCloud } from "lucide-react";
import type { ReactNode } from "react";
import { useState } from "react";

import type { BackupList } from "../../api/queries/backups";
import { RelativeTime } from "../../components/page/RelativeTime";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { IconButton } from "../../components/ui/IconButton";
import { ICONS } from "../../components/ui/icons";
import { Menu, MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { StatusPill } from "../../components/ui/StatusPill";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { PlainKey, T } from "../../i18n";
import { describeError } from "../../lib/errors";
import { formatBytes, parseTimestamp } from "../../lib/format";
import { useBackupActions } from "./useBackupActions";

export type BackupRow = BackupList["backups"][number];

/** What a backup holds beyond the application's files, in the order it matters. */
const EXTRAS: readonly [keyof BackupRow, PlainKey][] = [
  ["includes_env", "backups.history.parts.env"],
  ["has_database", "backups.history.parts.databases"],
  ["includes_node_modules", "backups.history.parts.modules"],
  ["includes_build", "backups.history.parts.build"],
];

/** "Files, .env and databases": what a backup holds, as a list the language joins. */
export function includesWords(backup: BackupRow, t: T): string {
  const parts = [t("backups.history.parts.files"), ...EXTRAS.filter(([flag]) => backup[flag] === true).map(([, key]) => t(key))];
  const list = new Intl.ListFormat(t.locale, { style: "long", type: "conjunction" }).format(parts);
  return list.charAt(0).toLocaleUpperCase(t.locale) + list.slice(1);
}

/**
 * What the backup itself last recorded (`last_verified_at`, `verified_ok`), plus the moment
 * this session is waiting on a fresh check. The server, not the session, is the source of
 * truth: a page reload shows the same verdict, not "not checked" again.
 */
function IntegrityCell({ backup, checking, t }: { backup: BackupRow; checking: boolean; t: T }) {
  if (checking) return <StatusPill state="deploying" label={t("backups.history.integrity.checking")} appearance="inline" size="sm" />;
  if (backup.verified_ok === true || backup.verified_ok === false) {
    return (
      <span className="flex flex-col gap-0.5">
        <StatusPill
          state={backup.verified_ok ? "running" : "failed"}
          label={backup.verified_ok ? t("backups.history.integrity.verified") : t("backups.history.integrity.failed")}
          appearance="inline"
          size="sm"
        />
        <RelativeTime value={backup.last_verified_at} className="text-12 text-fg-faint" />
      </span>
    );
  }
  return <StatusPill state="stopped" label={t("backups.history.integrity.never")} appearance="inline" size="sm" />;
}

export interface BackupActionHandlers {
  onRestore: (backup: BackupRow) => void;
  onCopy: (backup: BackupRow) => void;
  onDelete: (backup: BackupRow) => void;
}

function RowActions({ backup, checking, onVerify, handlers }: { backup: BackupRow; checking: boolean; onVerify: () => void; handlers: BackupActionHandlers }) {
  const t = useT();
  return (
    <Menu
      align="end"
      trigger={<IconButton label={t("backups.history.actionsFor", { id: backup.backup_id })} icon={<ICONS.more />} size="sm" tooltip={false} />}
    >
      <MenuItem icon={<FileCheck />} disabled={checking} onClick={onVerify}>
        {t("backups.history.actions.verify")}
      </MenuItem>
      <MenuItem icon={<RotateCcw />} onClick={() => handlers.onRestore(backup)}>
        {t("backups.history.actions.restore")}
      </MenuItem>
      <MenuItem icon={<UploadCloud />} onClick={() => handlers.onCopy(backup)}>
        {t("backups.history.actions.copyTo")}
      </MenuItem>
      <MenuSeparator />
      <MenuItem icon={<ICONS.delete />} destructive onClick={() => handlers.onDelete(backup)}>
        {t("backups.history.actions.delete")}
      </MenuItem>
    </Menu>
  );
}

export interface BackupsTableProps extends BackupActionHandlers {
  backups: readonly BackupRow[];
  caption: string;
  loading?: boolean;
  skeletonRows?: number;
  empty?: ReactNode;
}

/**
 * One application's backups, newest first: when, what they hold, their size, and their last
 * integrity check against their checksum - `last_verified_at` and `verified_ok`, as the backup
 * itself records them, so the state survives a reload instead of resetting to "not checked".
 */
export function BackupsTable({ backups, caption, loading = false, skeletonRows, empty, ...handlers }: BackupsTableProps) {
  const t = useT();
  const { verify } = useBackupActions();
  const [checking, setChecking] = useState<ReadonlySet<string>>(new Set());

  const settle = (id: string): void => {
    setChecking((current) => {
      const next = new Set(current);
      next.delete(id);
      return next;
    });
  };

  const onVerify = (backup: BackupRow): void => {
    setChecking((current) => new Set(current).add(backup.backup_id));
    verify.mutate(backup.backup_id, {
      onSuccess: (result) => {
        settle(backup.backup_id);
        if (result.valid) {
          toast.success(t("backups.history.toast.verified", { id: backup.backup_id }));
        } else {
          toast.error(t("backups.history.toast.verifyFailed", { id: backup.backup_id }), {
            detail: [...(result.errors ?? []), ...(result.warnings ?? [])].join("\n"),
          });
        }
      },
      onError: (error) => {
        settle(backup.backup_id);
        toast.error(t("backups.history.toast.verifyError", { id: backup.backup_id }), { detail: describeError(error).detail });
      },
    });
  };

  const columns: Column<BackupRow>[] = [
    {
      id: "created",
      header: t("backups.history.columns.created"),
      card: "title",
      cell: (row) => (
        <span className="flex min-w-0 flex-col">
          <RelativeTime value={row.timestamp} className="text-fg" />
          <span className="truncate text-12 font-normal text-fg-muted">
            {[row.description.trim(), includesWords(row, t)].filter((part) => part !== "").join(" · ")}
          </span>
        </span>
      ),
      sortValue: (row) => parseTimestamp(row.timestamp)?.getTime() ?? null,
    },
    {
      id: "size",
      header: t("backups.history.columns.size"),
      align: "end",
      mono: true,
      width: "w-24",
      cell: (row) => formatBytes(row.size, t.locale),
      sortValue: (row) => row.size,
    },
    {
      id: "integrity",
      header: t("backups.history.columns.integrity"),
      width: "w-36",
      card: "status",
      cell: (row) => <IntegrityCell backup={row} checking={checking.has(row.backup_id)} t={t} />,
    },
  ];

  return (
    <DataTable
      mobile="cards"
      columns={columns}
      rows={backups}
      getRowId={(row) => row.backup_id}
      caption={caption}
      loading={loading}
      {...(skeletonRows !== undefined ? { skeletonRows } : {})}
      {...(empty !== undefined ? { empty } : {})}
      rowActions={(row) => <RowActions backup={row} checking={checking.has(row.backup_id)} onVerify={() => onVerify(row)} handlers={handlers} />}
      defaultSort={{ column: "created", direction: "descending" }}
    />
  );
}
