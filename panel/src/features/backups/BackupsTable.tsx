import { Link } from "@tanstack/react-router";
import { MoreHorizontal, RotateCcw, ShieldCheck, Trash2, UploadCloud } from "lucide-react";
import type { ReactNode } from "react";
import { useState } from "react";

import type { Backup, BackupList } from "../../api/queries/backups";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Badge } from "../../components/ui/Badge";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { IconButton } from "../../components/ui/IconButton";
import { Menu, MenuItem } from "../../components/ui/Menu";
import { StatusPill } from "../../components/ui/StatusPill";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { describeError } from "../../lib/errors";
import { PushBackupDialog } from "./PushBackupDialog";
import { RestoreBackupDialog } from "./RestoreBackupDialog";
import { useBackupActions } from "./useBackupActions";

export type BackupRow = BackupList["backups"][number];

interface Includes {
  label: string;
  present: boolean;
}

function includesOf(backup: BackupRow, t: T): Includes[] {
  return [
    { label: t("backups.table.includes.env"), present: backup.includes_env },
    { label: t("backups.table.includes.database"), present: backup.has_database },
    { label: t("backups.table.includes.modules"), present: backup.includes_node_modules },
    { label: t("backups.table.includes.build"), present: backup.includes_build },
  ];
}

/**
 * What the backup itself last recorded (`last_verified_at`, `verified_ok`), plus the moment
 * this session is waiting on a fresh check. The server, not the session, is the source of
 * truth: a page reload shows the same verdict, not "not checked" again.
 */
function VerifiedCell({ backup, checking, t }: { backup: BackupRow; checking: boolean; t: T }) {
  if (checking) return <StatusPill state="deploying" label={t("backups.table.verified.checking")} appearance="inline" size="sm" />;
  if (backup.verified_ok === true) {
    return (
      <span className="flex flex-col gap-0.5">
        <StatusPill state="running" label={t("backups.table.verified.verified")} appearance="inline" size="sm" />
        <RelativeTime value={backup.last_verified_at} className="text-12 text-fg-faint" />
      </span>
    );
  }
  if (backup.verified_ok === false) {
    return (
      <span className="flex flex-col gap-0.5">
        <StatusPill state="failed" label={t("backups.table.verified.failed")} appearance="inline" size="sm" />
        <RelativeTime value={backup.last_verified_at} className="text-12 text-fg-faint" />
      </span>
    );
  }
  return <StatusPill state="stopped" label={t("backups.table.verified.never")} appearance="inline" size="sm" />;
}

function RowActions({
  backup,
  checking,
  onVerify,
}: {
  backup: BackupRow;
  checking: boolean;
  onVerify: () => void;
}) {
  const t = useT();
  const { remove } = useBackupActions();
  const [restoreOpen, setRestoreOpen] = useState(false);
  const [pushOpen, setPushOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);

  return (
    <>
      <Menu
        align="end"
        trigger={<IconButton label={t("backups.table.actionsFor", { id: backup.backup_id })} icon={<MoreHorizontal />} size="sm" tooltip={false} />}
      >
        <MenuItem icon={<ShieldCheck />} disabled={checking} onClick={onVerify}>
          {t("backups.table.actions.verify")}
        </MenuItem>
        <MenuItem icon={<RotateCcw />} onClick={() => setRestoreOpen(true)}>
          {t("backups.common.restore")}
        </MenuItem>
        <MenuItem icon={<UploadCloud />} onClick={() => setPushOpen(true)}>
          {t("backups.table.actions.copyTo")}
        </MenuItem>
        <MenuItem icon={<Trash2 />} destructive onClick={() => setConfirmOpen(true)}>
          {t("backups.table.actions.delete")}
        </MenuItem>
      </Menu>
      <RestoreBackupDialog backup={backup} open={restoreOpen} onOpenChange={setRestoreOpen} />
      <PushBackupDialog backup={backup} open={pushOpen} onOpenChange={setPushOpen} />
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={t("backups.table.deleteDialog.title", { id: backup.backup_id })}
        description={t("backups.table.deleteDialog.description", { domain: backup.domain })}
        confirmText={backup.backup_id}
        actionLabel={t("backups.table.deleteDialog.action")}
        onConfirm={async () => {
          await remove.mutateAsync(backup.backup_id);
        }}
      />
    </>
  );
}

export interface BackupsTableProps {
  backups: readonly BackupRow[];
  caption: string;
  loading?: boolean;
  /** Rows to hold while loading: the number the storage summary already counted, when known. */
  skeletonRows?: number;
  empty?: ReactNode;
}

/**
 * Every backup, newest first: what app, when, its size, what it includes, and its last
 * verification against its checksum - `last_verified_at` and `verified_ok`, as the backup
 * itself records them, so the state survives a reload instead of resetting to "not checked".
 */
export function BackupsTable({ backups, caption, loading = false, skeletonRows, empty }: BackupsTableProps) {
  const t = useT();
  const { verify } = useBackupActions();
  const [checking, setChecking] = useState<ReadonlySet<string>>(new Set());

  const onVerify = (backup: Backup): void => {
    setChecking((current) => new Set(current).add(backup.backup_id));
    verify.mutate(backup.backup_id, {
      onSuccess: (result) => {
        setChecking((current) => {
          const next = new Set(current);
          next.delete(backup.backup_id);
          return next;
        });
        if (result.valid) {
          toast.success(t("backups.table.toast.verified", { id: backup.backup_id }));
        } else {
          toast.error(t("backups.table.toast.verifyFailed", { id: backup.backup_id }), {
            detail: [...(result.errors ?? []), ...(result.warnings ?? [])].join("\n"),
          });
        }
      },
      onError: (error) => {
        setChecking((current) => {
          const next = new Set(current);
          next.delete(backup.backup_id);
          return next;
        });
        toast.error(t("backups.table.toast.verifyError", { id: backup.backup_id }), { detail: describeError(error).detail });
      },
    });
  };

  const columns: Column<BackupRow>[] = [
    {
      id: "domain",
      header: t("backups.fields.application"),
      cell: (row) => (
        <Link
          to="/apps/$domain"
          params={{ domain: row.domain }}
          className="-mx-1 rounded-[4px] px-1 py-0.5 font-medium text-fg hover:underline hover:underline-offset-2 focus-visible:outline-2 focus-visible:outline-focus"
        >
          {row.domain}
        </Link>
      ),
      sortValue: (row) => row.domain,
    },
    {
      id: "created",
      header: t("backups.table.columns.created"),
      width: "w-36",
      cell: (row) => <RelativeTime value={row.timestamp} />,
      sortValue: (row) => row.timestamp,
    },
    {
      id: "size",
      header: t("backups.table.columns.size"),
      align: "end",
      mono: true,
      width: "w-24",
      // On a phone the row keeps what, when and whether it was verified.
      hideBelow: "sm",
      cell: (row) => row.size_human,
      sortValue: (row) => row.size,
    },
    {
      id: "includes",
      header: t("backups.table.columns.includes"),
      hideBelow: "md",
      cell: (row) => (
        <span className="flex flex-wrap gap-1">
          {includesOf(row, t)
            .filter((item) => item.present)
            .map((item) => (
              <Badge key={item.label} mono>
                {item.label}
              </Badge>
            ))}
        </span>
      ),
    },
    {
      id: "verified",
      header: t("backups.table.columns.verified"),
      width: "w-36",
      cell: (row) => <VerifiedCell backup={row} checking={checking.has(row.backup_id)} t={t} />,
    },
  ];

  return (
    <DataTable
      columns={columns}
      rows={backups}
      getRowId={(row) => row.backup_id}
      caption={caption}
      loading={loading}
      {...(skeletonRows !== undefined ? { skeletonRows } : {})}
      {...(empty !== undefined ? { empty } : {})}
      rowActions={(row) => (
        <RowActions backup={row} checking={checking.has(row.backup_id)} onVerify={() => onVerify(row)} />
      )}
      defaultSort={{ column: "created", direction: "descending" }}
    />
  );
}
