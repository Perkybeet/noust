import { useQueryClient } from "@tanstack/react-query";
import { ArchiveRestore, ShieldQuestion } from "lucide-react";
import { useState } from "react";

import { request } from "../../../api/client";
import { volumeKeys } from "../../../api/queries/volumeBackups";
import type { VolumeBackup } from "../../../api/queries/volumeBackups";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Section } from "../../../components/page/Section";
import { RowMenu } from "../../../components/page/RowMenu";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { DataTable } from "../../../components/ui/DataTable";
import { Drawer } from "../../../components/ui/Drawer";
import { EmptyState } from "../../../components/ui/EmptyState";
import { MenuItem, MenuSeparator } from "../../../components/ui/Menu";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { Mono } from "../../../components/ui/Mono";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { backupRetentionWords } from "../policy/retention";
import type { Accepted } from "../../databases/jobs";
import type { JobWords } from "../../jobs/useJobInHand";
import { formatBytes } from "../../../lib/format";
import { useNode } from "../../../nodes/useNode";
import { PushToDestinationDialog } from "../PushToDestinationDialog";
import { isTrue, textOf } from "../policy/record";
import { backupKind, checkOf, scheduleWords, totalBytes, viewOf } from "./data";
import type { VolumeRow } from "./data";
import { ICONS } from "../../../components/ui/icons";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";

export interface VolumeDetailDrawerProps {
  row: VolumeRow;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onEdit: () => void;
  /** Queues the policy's run, and follows it on the page. */
  onRun: () => void;
  onRemove: () => void;
  onQueued: (accepted: Accepted, words: JobWords) => void;
  busy: boolean;
}

/** What a policy backs up, in words: everything it declares, or the pieces it names. */
export function coversWords(t: T, targets: readonly string[], extraPaths: readonly string[]): string {
  if (targets.length === 0 && extraPaths.length === 0) return t("volumeBackups.everything");
  const count = targets.length + extraPaths.length;
  return t("volumeBackups.pieces", { count });
}

function BackupActions({ backup, busy, onRestore, onVerify, onPush, onDelete }: { backup: VolumeBackup; busy: boolean; onRestore: () => void; onVerify: () => void; onPush: () => void; onDelete: () => void }) {
  const t = useT();
  return (
    <RowMenu name={backup.id}>
      <MenuItem icon={<ArchiveRestore />} disabled={busy} onClick={onRestore}>
        {t("volumeBackups.table.restore")}
      </MenuItem>
      <MenuItem icon={<ShieldQuestion />} disabled={busy} onClick={onVerify}>
        {t("volumeBackups.table.verify")}
      </MenuItem>
      <MenuItem icon={<OBJECT_ICONS.destination />} disabled={busy} onClick={onPush}>
        {t("volumeBackups.table.push")}
      </MenuItem>
      <MenuSeparator />
      <MenuItem icon={<ICONS.delete />} destructive onClick={onDelete}>
        {t("common.words.delete")}
      </MenuItem>
    </RowMenu>
  );
}

/**
 * One application's data: its policy (when, what, how long it keeps, where it sends, how the
 * last run went) and every backup taken, each with its check, where it was sent and what can be
 * done with it: put it back over the data (typing the application's name, with a copy of what is
 * there first), check it, send it to a destination, delete it.
 */
export function VolumeDetailDrawer({ row, open, onOpenChange, onEdit, onRun, onRemove, onQueued, busy }: VolumeDetailDrawerProps) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const [restoring, setRestoring] = useState<VolumeBackup | null>(null);
  const [safety, setSafety] = useState(true);
  const [pushing, setPushing] = useState<VolumeBackup | null>(null);
  const [deleting, setDeleting] = useState<VolumeBackup | null>(null);
  const policy = row.policy;
  const view = viewOf(row.state);
  const refresh = (): void => void queryClient.invalidateQueries({ queryKey: volumeKeys.all });

  const verify = async (backup: VolumeBackup): Promise<void> => {
    const accepted = await request("post", "/api/volume-backups/{backup_id}/verify", { params: { backup_id: backup.id }, query: { deep: false } });
    onQueued(accepted, {
      running: t("volumeBackups.job.verify.running", { id: backup.id }),
      succeeded: t("volumeBackups.job.verify.succeeded", { id: backup.id }),
      failed: t("volumeBackups.job.verify.failed", { id: backup.id }),
    });
  };

  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      size="lg"
      title={<Mono tone="default">{row.domain}</Mono>}
      description={t("volumeBackups.detail.description")}
    >
      <div className="flex min-w-0 flex-col gap-8">
        <Section
          level={3}
          title={t("volumeBackups.detail.policy")} icon={OBJECT_ICONS.schedule}
          actions={
            policy !== null ? (
              <>
                <Button size="sm" disabled={busy} onClick={onRun}>
                  {t("common.words.backUpNow")}
                </Button>
                <Button size="sm" onClick={onEdit}>
                  {t("volumeBackups.actions.edit")}
                </Button>
                <RowMenu name={row.domain}>
                  <MenuItem icon={<ICONS.delete />} destructive onClick={onRemove}>
                    {t("volumeBackups.actions.remove")}
                  </MenuItem>
                </RowMenu>
              </>
            ) : undefined
          }
        >
          {policy === null ? (
            <div className="flex flex-col items-start gap-3">
              <StatusPill appearance="inline" size="sm" state={view.state} label={t(view.label)} />
              <p className="max-w-measure text-13 text-fg-muted">{t("volumeBackups.detail.noPolicy")}</p>
              <Button size="sm" onClick={onEdit}>
                {t("volumeBackups.detail.setPolicy")}
              </Button>
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              <StatusPill appearance="inline" size="sm" state={view.state} label={t(view.label)} />
              <KeyValueList
                items={[
                  {
                    label: t("volumeBackups.detail.when"),
                    value: scheduleWords(policy.schedule, t.locale),
                    mono: false,
                    copy: false,
                    ...(textOf(policy.timer, "next_run") !== "" ? { hint: t("volumeBackups.detail.nextRun", { when: textOf(policy.timer, "next_run") }) } : {}),
                  },
                  { label: t("volumeBackups.detail.covers"), value: coversWords(t, policy.targets ?? [], []), mono: false, copy: false },
                  ...((policy.extra_paths ?? []).length > 0 ? [{ label: t("volumeBackups.detail.extra"), value: (policy.extra_paths ?? []).join(", ") }] : []),
                  { label: t("volumeBackups.detail.consistency"), value: t(`volumeBackups.consistency.${policy.consistency === "pause" || policy.consistency === "stop" ? policy.consistency : "live"}`), mono: false, copy: false },
                  { label: t("volumeBackups.detail.keeps"), value: backupRetentionWords(t, policy.retention_count, policy.retention_days), mono: false, copy: false },
                  {
                    label: t("volumeBackups.detail.sendsTo"),
                    value:
                      (policy.destinations ?? []).length === 0 ? (
                        t("volumeBackups.detail.onlyHere")
                      ) : (
                        <span className="flex flex-col gap-0.5">
                          {(policy.destinations ?? []).map((destination) => (
                            <span key={destination.name} className="flex flex-wrap items-center gap-x-2">
                              <Mono>{destination.name}</Mono>
                              <span className="text-12 text-fg-muted">{backupRetentionWords(t, destination.retention_count, destination.retention_days)}</span>
                              {!destination.exists ? <span className="text-12 text-fail">{t("volumeBackups.detail.gone")}</span> : null}
                            </span>
                          ))}
                        </span>
                      ),
                    mono: false,
                    copy: false,
                  },
                  { label: t("volumeBackups.detail.lastRun"), value: policy.last_run_at ? <RelativeTime value={policy.last_run_at} /> : t("volumeBackups.detail.notYet"), mono: false, copy: false },
                ]}
              />
              {policy.last_status === "failed" && policy.last_error ? (
                <SystemOutput label={t("volumeBackups.detail.lastError")} maxHeight="max-h-40">
                  {policy.last_error}
                </SystemOutput>
              ) : null}
              {policy.enabled && !isTrue(policy.timer, "installed") ?<InlineMessage tone="error">{t("volumeBackups.detail.timerMissing")}</InlineMessage> : null}
            </div>
          )}
        </Section>

        <Section level={3} title={t("volumeBackups.detail.backups")} icon={OBJECT_ICONS.backup}>
          <DataTable<VolumeBackup>
            caption={t("volumeBackups.table.caption", { domain: row.domain })}
            density="compact"
            rows={row.backups}
            getRowId={(backup) => backup.id}
            empty={<EmptyState variant="inline" title={t("volumeBackups.detail.noBackups")} description={t("volumeBackups.detail.noBackupsHint")} />}
            columns={[
              {
                id: "taken",
                header: t("volumeBackups.table.taken"),
                card: "title",
                cell: (backup) => (
                  <span className="flex min-w-0 flex-col">
                    <RelativeTime value={backup.created_at} className="font-medium text-fg" />
                    <Mono tone="faint" truncate>
                      {backup.id}
                    </Mono>
                  </span>
                ),
              },
              {
                id: "piece",
                header: t("volumeBackups.table.piece"),
                hideBelow: "sm",
                cell: (backup) => (
                  <span className="flex min-w-0 flex-wrap items-center gap-2">
                    <Mono>{backup.target}</Mono>
                    <Badge>{t(`volumeBackups.table.kind.${backupKind(backup)}`)}</Badge>
                  </span>
                ),
              },
              {
                id: "check",
                header: t("volumeBackups.table.check"),
                width: "w-36",
                card: "status",
                cell: (backup) => {
                  const check = checkOf(backup);
                  return check === "checked" ? (
                    <StatusPill appearance="inline" size="sm" state="running" label={t("volumeBackups.table.checked")} />
                  ) : check === "failed" ? (
                    <StatusPill appearance="inline" size="sm" state="failed" label={t("volumeBackups.table.checkFailed")} />
                  ) : (
                    <StatusPill appearance="inline" size="sm" state="stopped" label={t("volumeBackups.table.notChecked")} />
                  );
                },
              },
              {
                id: "where",
                header: t("volumeBackups.table.where"),
                hideBelow: "md",
                cell: (backup) =>
                  (backup.remote_copies ?? []).length === 0 ? (
                    <span className="text-fg-muted">{t("volumeBackups.table.onlyHere")}</span>
                  ) : (
                    <span className="inline-flex flex-wrap gap-1">
                      {(backup.remote_copies ?? []).map((copy, index) => (
                        <Badge key={textOf(copy, "destination") || String(index)} mono>
                          {textOf(copy, "destination")}
                        </Badge>
                      ))}
                    </span>
                  ),
              },
              {
                id: "size",
                header: t("volumeBackups.table.size"),
                align: "end",
                mono: true,
                width: "w-28",
                cell: (backup) => formatBytes(backup.size_bytes, t.locale),
              },
            ]}
            rowActions={(backup) => (
              <BackupActions
                backup={backup}
                busy={busy}
                onRestore={() => {
                  setSafety(true);
                  setRestoring(backup);
                }}
                onVerify={() => void verify(backup)}
                onPush={() => setPushing(backup)}
                onDelete={() => setDeleting(backup)}
              />
            )}
          />
          {row.backups.length > 0 ? <p className="text-12 text-fg-muted">{formatBytes(totalBytes(row.backups), t.locale)}</p> : null}
        </Section>
      </div>

      {restoring !== null ? (
        <ConfirmDialog
          friction="type"
          open
          onOpenChange={(next) => (next ? undefined : setRestoring(null))}
          title={t("volumeBackups.restore.title", { domain: row.domain })}
          description={t("volumeBackups.restore.description", { id: restoring.id })}
          actionLabel={t("volumeBackups.restore.action")}
          confirmText={row.domain}
          server={node}
          onConfirm={async () => {
            const accepted = await request("post", "/api/volume-backups/{backup_id}/restore", { params: { backup_id: restoring.id }, body: { safety_backup: safety } });
            onQueued(accepted, {
              running: t("volumeBackups.job.restore.running", { domain: row.domain }),
              succeeded: t("volumeBackups.job.restore.succeeded", { domain: row.domain }),
              failed: t("volumeBackups.job.restore.failed", { domain: row.domain }),
            });
            setRestoring(null);
          }}
        >
          <p className="text-13 text-fg-muted">{t.rich("volumeBackups.restore.source", { piece: <Mono key="piece">{restoring.target}</Mono>, when: <RelativeTime key="when" value={restoring.created_at} /> })}</p>
          <Checkbox checked={safety} onCheckedChange={setSafety} label={t("volumeBackups.restore.safety")} description={t("volumeBackups.restore.safetyHelp")} />
        </ConfirmDialog>
      ) : null}
      {pushing !== null ? (
        <PushToDestinationDialog
          open
          onOpenChange={(next) => (next ? undefined : setPushing(null))}
          words={{
            title: t("volumeBackups.push.title", { id: pushing.id }),
            description: t("volumeBackups.push.description"),
            destination: t("volumeBackups.push.destination"),
            choose: t("volumeBackups.push.choose"),
            none: t("volumeBackups.push.none"),
            action: t("volumeBackups.push.action"),
            failed: t("volumeBackups.push.failed"),
          }}
          onPush={async (destination) => {
            const accepted = await request("post", "/api/volume-backups/{backup_id}/push", { params: { backup_id: pushing.id }, body: { destination } });
            onQueued(accepted, {
              running: t("volumeBackups.job.push.running", { id: pushing.id, destination }),
              succeeded: t("volumeBackups.job.push.succeeded", { id: pushing.id, destination }),
              failed: t("volumeBackups.job.push.failed", { id: pushing.id, destination }),
            });
          }}
        />
      ) : null}
      {deleting !== null ? (
        <ConfirmDialog
          friction="type"
          open
          onOpenChange={(next) => (next ? undefined : setDeleting(null))}
          title={t("volumeBackups.delete.title", { id: deleting.id })}
          description={t("volumeBackups.delete.description")}
          actionLabel={t("volumeBackups.delete.action")}
          confirmText={deleting.id}
          server={node}
          onConfirm={async () => {
            await request("delete", "/api/volume-backups/{backup_id}", { params: { backup_id: deleting.id } });
            refresh();
          }}
        />
      ) : null}
    </Drawer>
  );
}
