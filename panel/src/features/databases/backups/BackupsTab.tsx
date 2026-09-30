import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArchiveRestore, CloudUpload, Download, FlaskConical, ShieldQuestion, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import { request } from "../../../api/client";
import { backupDestinationsQuery } from "../../../api/queries/backupDestinations";
import { databaseBackupsQuery, databaseKeys, databaseOverviewQuery, policyQuery, remoteDumpsQuery } from "../../../api/queries/databases";
import type { DatabaseBackup, RemoteDump } from "../../../api/queries/databases";
import { jobKeys } from "../../../api/queries/jobs";
import { CommandHint } from "../../../components/page/CommandHint";
import { KeyValueList, KeyValueListSkeleton } from "../../../components/page/KeyValueList";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Section } from "../../../components/page/Section";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { DataTable } from "../../../components/ui/DataTable";
import { Dialog } from "../../../components/ui/Dialog";
import { Drawer } from "../../../components/ui/Drawer";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Field } from "../../../components/ui/Field";
import { IconButton } from "../../../components/ui/IconButton";
import { ICONS } from "../../../components/ui/icons";
import { Menu, MenuItem, MenuSeparator } from "../../../components/ui/Menu";
import { Mono } from "../../../components/ui/Mono";
import { Select } from "../../../components/ui/Select";
import { StatusPill } from "../../../components/ui/StatusPill";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { useT } from "../../../i18n";
import type { PlainKey } from "../../../i18n";
import { formatBytes } from "../../../lib/format";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { can, isSlotEngine } from "../engines";
import { useDatabaseJob } from "../jobs";
import { policyProtection, protectionView } from "../protection";
import { VerifiedMark } from "../VerifiedMark";
import { downloadDump } from "./download";
import { PolicyDrawer } from "./PolicyDrawer";
import { RestoreDialog } from "./RestoreDialog";
import type { RestoreSource } from "./RestoreDialog";
import { policyScheduleWords, retentionWords } from "./schedule";

const KIND_WORDS: Readonly<Record<string, PlainKey>> = {
  manual: "databases.dumps.kind.manual",
  scheduled: "databases.dumps.kind.scheduled",
  safety: "databases.dumps.kind.safety",
  unknown: "databases.dumps.kind.unknown",
};

const FORMAT_WORDS: Readonly<Record<string, string>> = {
  custom: "pg_dump -Fc",
  plain: "SQL",
  tar: "tar",
  rdb: "RDB",
  aof: "AOF",
  archive: "mongodump",
};

/** The policy: its state, when it runs, what it keeps, where it sends, and how the last run went. */
function PolicyCard({ engine, name, onEdit }: { engine: string; name: string; onEdit: () => void }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const policy = useQuery(policyQuery(engine, name));
  const [removing, setRemoving] = useState(false);
  const data = policy.data;
  const view = protectionView(policyProtection(data));

  return (
    <Card
      title={t("databases.backups.policy.title")}
      level={2}
      padding="sm"
      actions={
        data?.configured ? (
          <>
            <Button size="sm" onClick={onEdit}>
              {t("databases.backups.policy.edit")}
            </Button>
            <Menu align="end" trigger={<IconButton size="sm" label={t("databases.backups.policy.more")} icon={<ICONS.more />} />}>
              <MenuItem icon={<Trash2 />} destructive onClick={() => setRemoving(true)}>
                {t("databases.backups.policy.remove")}
              </MenuItem>
            </Menu>
          </>
        ) : undefined
      }
    >
      {policy.isError ? (
        <ErrorBlock compact error={policy.error} title={t("databases.backups.policy.loadFailed")} onRetry={() => void policy.refetch()} />
      ) : data === undefined ? (
        <KeyValueListSkeleton rows={6} />
      ) : !data.configured ? (
        <div className="flex flex-col items-start gap-3">
          <StatusPill appearance="inline" size="sm" state={view.state} label={t(view.label)} />
          <p className="max-w-measure text-13 text-fg-muted">{t("databases.backups.policy.none")}</p>
          <Button size="sm" onClick={onEdit}>
            {t("databases.backups.policy.set")}
          </Button>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <StatusPill appearance="inline" size="sm" state={view.state} label={t(view.label)} />
          <KeyValueList
            items={[
              {
                label: t("databases.backups.policy.when"),
                value: policyScheduleWords(data, t.locale),
                mono: false,
                copy: false,
                ...(data.timer?.next_run ? { hint: t.rich("databases.backups.policy.nextRun", { when: <RelativeTime value={data.timer.next_run} /> }) } : {}),
              },
              { label: t("databases.backups.policy.keepsHere"), value: retentionWords(t, data.retention_count, data.retention_days), mono: false, copy: false },
              {
                label: t("databases.backups.policy.sendsTo"),
                value:
                  (data.destinations ?? []).length === 0 ? (
                    t("databases.backups.policy.onlyHere")
                  ) : (
                    <span className="flex flex-col gap-0.5">
                      {(data.destinations ?? []).map((destination) => (
                        <span key={destination.name} className="flex flex-wrap items-center gap-x-2">
                          <Mono>{destination.name}</Mono>
                          <span className="text-12 text-fg-muted">{retentionWords(t, destination.retention_count, destination.retention_days)}</span>
                          {!destination.exists ? <span className="text-12 text-fail">{t("databases.backups.policy.destinationGone")}</span> : null}
                        </span>
                      ))}
                    </span>
                  ),
                mono: false,
                copy: false,
              },
              {
                label: t("databases.backups.policy.proof"),
                value: data.verify_restore ? t("databases.backups.policy.testRestoreOn") : t("databases.backups.policy.checkOnly"),
                mono: false,
                copy: false,
              },
              {
                label: t("databases.backups.policy.lastRun"),
                value: data.last_run_at ? <RelativeTime value={data.last_run_at} /> : t("databases.backups.policy.notYet"),
                mono: false,
                copy: false,
              },
            ]}
          />
          {data.last_status === "failed" && data.last_error ? (
            <SystemOutput label={t("databases.backups.policy.lastError")} maxHeight="max-h-40">
              {data.last_error}
            </SystemOutput>
          ) : null}
          {data.enabled && data.timer?.installed !== true ? <p className="text-12 text-fail">{t("databases.backups.policy.timerMissing")}</p> : null}
        </div>
      )}
      <ConfirmDialog
        friction="simple"
        open={removing}
        onOpenChange={setRemoving}
        title={t("databases.backups.policy.removeTitle", { name })}
        description={t("databases.backups.policy.removeDescription")}
        actionLabel={t("databases.backups.policy.removeAction")}
        server={node}
        onConfirm={async () => {
          await request("delete", "/api/databases/backup-policies/{engine}/{database}", { params: { engine, database: name } });
          await queryClient.invalidateQueries({ queryKey: databaseKeys.policy(engine, name) });
          void queryClient.invalidateQueries({ queryKey: databaseKeys.policies });
        }}
      />
    </Card>
  );
}

/** A dump's whole record: its digest, what checking it found, where copies of it went. */
function DumpDrawer({ dump, onClose }: { dump: DatabaseBackup; onClose: () => void }) {
  const t = useT();
  return (
    <Drawer open onOpenChange={(open) => (open ? undefined : onClose())} size="md" title={<Mono tone="default">{dump.name}</Mono>} description={<RelativeTime value={dump.created} />}>
      <div className="flex flex-col gap-5">
        <KeyValueList
          items={[
            { label: t("databases.dumps.size"), value: formatBytes(dump.size, t.locale), copy: false },
            { label: t("databases.dumps.format"), value: FORMAT_WORDS[dump.format] ?? dump.format },
            { label: t("databases.dumps.kindLabel"), value: t(KIND_WORDS[dump.kind] ?? "databases.dumps.kind.unknown"), mono: false, copy: false },
            { label: t("databases.dumps.path"), value: dump.path },
            ...(dump.sha256 ? [{ label: "SHA-256", value: dump.sha256 }] : []),
            { label: t("databases.dumps.check"), value: <VerifiedMark status={dump.verify_status} />, copy: false },
          ]}
        />
        {dump.verify_detail ? (
          <SystemOutput label={t("databases.dumps.checkSaid")} maxHeight="max-h-48">
            {dump.verify_detail}
          </SystemOutput>
        ) : null}
        {dump.restore_test_status ? (
          <div className="flex flex-col gap-2">
            <p className="text-13 font-medium text-fg">{t("databases.dumps.testRestore")}</p>
            <VerifiedMark status={dump.restore_test_status} />
            {dump.restore_test_detail ? (
              <SystemOutput label={t("databases.dumps.testSaid")} maxHeight="max-h-48">
                {dump.restore_test_detail}
              </SystemOutput>
            ) : null}
          </div>
        ) : null}
        <div className="flex flex-col gap-2">
          <p className="text-13 font-medium text-fg">{t("databases.dumps.copies")}</p>
          {(dump.destinations ?? []).length === 0 ? (
            <p className="text-13 text-fg-muted">{t("databases.dumps.noCopies")}</p>
          ) : (
            <ul className="flex flex-col gap-1.5 text-13">
              {(dump.destinations ?? []).map((copy) => (
                <li key={copy.destination} className="flex flex-wrap items-center gap-x-2">
                  <Mono>{copy.destination}</Mono>
                  {copy.pushed_at ? <RelativeTime value={copy.pushed_at} className="text-fg-muted" /> : null}
                  {copy.verified_by ? <span className="text-12 text-fg-faint">{t("databases.dumps.verifiedBy", { method: copy.verified_by })}</span> : null}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Drawer>
  );
}

function PushDialog({ dump, onClose, onPush }: { dump: DatabaseBackup | null; onClose: () => void; onPush: (destination: string) => Promise<void> }) {
  const t = useT();
  const destinations = useQuery({ ...backupDestinationsQuery(), enabled: dump !== null });
  const [destination, setDestination] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const list = destinations.data?.destinations ?? [];
  return (
    <Dialog
      open={dump !== null}
      onOpenChange={(open) => (open ? undefined : onClose())}
      size="sm"
      title={t("databases.dumps.pushTitle")}
      description={t.rich("databases.dumps.pushDescription", { dump: <Mono>{dump?.name ?? ""}</Mono> })}
      footer={
        <>
          <Button onClick={onClose}>{t("databases.common.cancel")}</Button>
          <Button
            variant="primary"
            loading={pending}
            disabled={destination === null}
            onClick={() => {
              if (destination === null) return;
              setPending(true);
              setFailure(null);
              onPush(destination).then(
                () => {
                  setPending(false);
                  onClose();
                },
                (error: unknown) => {
                  setPending(false);
                  setFailure(error);
                },
              );
            }}
          >
            {t("databases.dumps.pushAction")}
          </Button>
        </>
      }
    >
      {list.length === 0 && destinations.data !== undefined ? (
        <EmptyState variant="inline" title={t("databases.policy.noDestinations")} description={t("databases.policy.noDestinationsHint")} />
      ) : (
        <div className="flex flex-col gap-4">
          <Field label={t("databases.dumps.destination")} nativeLabel={false}>
            <Select value={destination} placeholder={t("databases.dumps.chooseDestination")} onValueChange={setDestination} options={list.map((item) => ({ value: item.name, label: item.name }))} />
          </Field>
          {failure !== null ? <ErrorBlock live compact error={failure} title={t("databases.dumps.pushFailed")} /> : null}
        </div>
      )}
    </Dialog>
  );
}

/** What a destination holds of this database: copies this server sent, and any another server did. */
function RemoteDumps({ engine, name, onRestore }: { engine: string; name: string; onRestore: (source: RestoreSource) => void }) {
  const t = useT();
  const destinations = useQuery(backupDestinationsQuery());
  const list = destinations.data?.destinations ?? [];
  const [chosen, setChosen] = useState<string | null>(null);
  const destination = chosen ?? list[0]?.name ?? "";
  const remote = useQuery(remoteDumpsQuery(engine, name, destination));
  if (destinations.data !== undefined && list.length === 0) return null;
  return (
    <Section
      title={t("databases.remote.title")}
      description={t("databases.remote.description")}
      actions={
        list.length > 1 ? (
          <Select size="sm" aria-label={t("databases.remote.destinationLabel")} value={destination} onValueChange={setChosen} options={list.map((item) => ({ value: item.name, label: item.name }))} />
        ) : undefined
      }
    >
      {remote.isError ? (
        <ErrorBlock compact error={remote.error} title={t("databases.remote.failed", { destination })} onRetry={() => void remote.refetch()} retrying={remote.isRefetching} />
      ) : remote.data?.dumps.length === 0 ? (
        <EmptyState variant="inline" title={t("databases.remote.empty", { destination })} />
      ) : (
        <DataTable<RemoteDump>
          caption={t("databases.remote.caption", { destination })}
          density="compact"
          rows={remote.data?.dumps ?? []}
          loading={remote.data === undefined}
          skeletonRows={3}
          getRowId={(dump) => dump.name}
          mobile="cards"
          empty={<EmptyState variant="inline" title={t("databases.remote.empty", { destination })} />}
          columns={[
            {
              id: "name",
              header: t("databases.dumps.dump"),
              card: "title",
              cell: (dump) => (
                <span className="flex items-center gap-2">
                  <Mono truncate>{dump.name}</Mono>
                  {!dump.own ? <Badge>{t("databases.remote.otherServer")}</Badge> : null}
                </span>
              ),
            },
            { id: "modified", header: t("databases.remote.sent"), width: "w-40", cell: (dump) => <RelativeTime value={dump.created ?? dump.modified} /> },
            {
              id: "local",
              header: t("databases.remote.here"),
              width: "w-32",
              cell: (dump) => (dump.local ? t("databases.remote.alsoHere") : t("databases.remote.onlyThere")),
            },
            {
              id: "size",
              header: t("databases.dumps.size"),
              align: "end",
              mono: true,
              width: "w-28",
              cell: (dump) => (dump.size != null ? formatBytes(dump.size, t.locale) : <EmptyCell reason={t("databases.remote.noSize")} />),
            },
          ]}
          rowActions={(dump) => (
            <Button size="sm" variant="ghost" icon={<ArchiveRestore aria-hidden="true" />} onClick={() => onRestore({ dump: dump.name, destination })}>
              {t("databases.dumps.restore")}
            </Button>
          )}
        />
      )}
    </Section>
  );
}

/**
 * A database's backups: the policy that makes them (when, what it keeps, where it sends, how it
 * proves them), then every dump on this server with what checking it found and where its copies
 * are, and what the destinations hold. Every long action is a job in the page's job slot.
 */
export function BackupsTab({ engine, name }: { engine: string; name: string }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const job = useDatabaseJob();
  const overview = useQuery(databaseOverviewQuery(engine, name));
  const dumps = useQuery(databaseBackupsQuery(engine, name));
  const [editing, setEditing] = useState(false);
  const [restoring, setRestoring] = useState<RestoreSource | null>(null);
  const [pushing, setPushing] = useState<DatabaseBackup | null>(null);
  const [deleting, setDeleting] = useState<DatabaseBackup | null>(null);
  const [opened, setOpened] = useState<DatabaseBackup | null>(null);
  const policy = useQuery(policyQuery(engine, name));
  const slot = isSlotEngine({ capabilities: overview.data?.capabilities ?? [] });
  const formats = engine === "postgresql";

  const rows = useMemo(() => [...(dumps.data?.backups ?? [])].sort((a, b) => Date.parse(b.created) - Date.parse(a.created)), [dumps.data]);

  const queued = (): void => void queryClient.invalidateQueries({ queryKey: jobKeys.active });

  const verify = useMutation({
    mutationFn: ({ dump, restoreTest }: { dump: DatabaseBackup; restoreTest: boolean }) =>
      request("post", "/api/databases/backups/{name}/verify", { params: { name: dump.name }, body: { engine, restore_test: restoreTest } }),
    onSuccess: (accepted, { restoreTest }) => {
      job.track(accepted, restoreTest ? "testRestore" : "verify");
      queued();
    },
    onError: (error) => reportActionError(t("databases.dumps.verifyFailed"), error),
  });

  const download = useMutation({
    mutationFn: (dump: DatabaseBackup) => downloadDump(engine, dump.name),
    onError: (error) => reportActionError(t("databases.dumps.downloadFailed"), error),
  });

  return (
    <div className="flex min-w-0 flex-col gap-8">
      <PolicyCard engine={engine} name={name} onEdit={() => setEditing(true)} />

      <Section title={t("databases.dumps.title")} description={t("databases.dumps.description")}>
        {dumps.isError && dumps.data === undefined ? (
          <ErrorBlock error={dumps.error} title={t("databases.dumps.loadFailed")} onRetry={() => void dumps.refetch()} retrying={dumps.isRefetching} />
        ) : (
          <DataTable<DatabaseBackup>
            caption={t("databases.dumps.caption", { name })}
            rows={rows}
            loading={dumps.isPending}
            skeletonRows={4}
            getRowId={(dump) => dump.name}
            onRowActivate={setOpened}
            mobile="cards"
            empty={<EmptyState variant="inline" title={t("databases.dumps.empty")} description={t("databases.dumps.emptyHint")} />}
            columns={[
              {
                id: "taken",
                header: t("databases.dumps.taken"),
                card: "title",
                cell: (dump) => (
                  <span className="flex min-w-0 flex-col">
                    <RelativeTime value={dump.created} className="font-medium text-fg" />
                    <Mono tone="faint" truncate>
                      {dump.name}
                    </Mono>
                  </span>
                ),
              },
              { id: "check", header: t("databases.dumps.check"), width: "w-36", card: "status", cell: (dump) => <VerifiedMark status={dump.verify_status} /> },
              { id: "kind", header: t("databases.dumps.kindLabel"), width: "w-36", hideBelow: "md", cell: (dump) => t(KIND_WORDS[dump.kind] ?? "databases.dumps.kind.unknown") },
              {
                id: "where",
                header: t("databases.dumps.where"),
                hideBelow: "sm",
                cell: (dump) =>
                  (dump.destinations ?? []).length === 0 ? (
                    <span className="text-fg-muted">{t("databases.backups.policy.onlyHere")}</span>
                  ) : (
                    <span className="text-fg-muted">
                      {t.rich("databases.dumps.hereAnd", {
                        destinations: (
                          <span className="inline-flex flex-wrap gap-1">
                            {(dump.destinations ?? []).map((copy) => (
                              <Badge key={copy.destination} mono>
                                {copy.destination}
                              </Badge>
                            ))}
                          </span>
                        ),
                      })}
                    </span>
                  ),
              },
              {
                id: "size",
                header: t("databases.dumps.size"),
                align: "end",
                mono: true,
                width: "w-28",
                cell: (dump) => formatBytes(dump.size, t.locale),
              },
            ]}
            rowActions={(dump) => (
              <Menu align="end" trigger={<IconButton size="sm" label={t("databases.dumps.actionsFor", { name: dump.name })} icon={<ICONS.more />} tooltip={false} />}>
                <MenuItem icon={<ArchiveRestore />} disabled={job.busy} onClick={() => setRestoring({ dump: dump.name })}>
                  {t("databases.dumps.restoreEllipsis")}
                </MenuItem>
                <MenuItem icon={<ShieldQuestion />} disabled={job.busy} onClick={() => verify.mutate({ dump, restoreTest: false })}>
                  {t("databases.dumps.verify")}
                </MenuItem>
                {!slot ? (
                  <MenuItem icon={<FlaskConical />} disabled={job.busy} onClick={() => verify.mutate({ dump, restoreTest: true })}>
                    {t("databases.dumps.testRestoreAction")}
                  </MenuItem>
                ) : null}
                <MenuItem icon={<CloudUpload />} disabled={job.busy} onClick={() => setPushing(dump)}>
                  {t("databases.dumps.push")}
                </MenuItem>
                <MenuItem icon={<Download />} onClick={() => download.mutate(dump)}>
                  {t("databases.dumps.download")}
                </MenuItem>
                <MenuSeparator />
                <MenuItem icon={<Trash2 />} destructive onClick={() => setDeleting(dump)}>
                  {t("databases.dumps.delete")}
                </MenuItem>
              </Menu>
            )}
          />
        )}
      </Section>

      <RemoteDumps engine={engine} name={name} onRestore={setRestoring} />

      <CommandHint command={`noust db backup-schedule set ${name} -e ${engine} --schedule daily`} label={t("databases.common.fromTerminal")} />

      {editing ? (
        <PolicyDrawer
          key={policy.dataUpdatedAt}
          engine={engine}
          name={name}
          policy={policy.data}
          formats={formats}
          testRestore={!slot && can({ capabilities: overview.data?.capabilities ?? [] }, "dump")}
          open={editing}
          onOpenChange={setEditing}
        />
      ) : null}
      <RestoreDialog
        key={restoring?.dump ?? "none"}
        engine={engine}
        name={name}
        source={restoring}
        replaceOnly={slot}
        onClose={() => setRestoring(null)}
        onQueued={(accepted, target, database) => job.track(accepted, target === "new" ? "restoreNew" : "restore", database)}
      />
      <PushDialog
        key={pushing?.name ?? "none"}
        dump={pushing}
        onClose={() => setPushing(null)}
        onPush={async (destination) => {
          if (pushing === null) return;
          const accepted = await request("post", "/api/databases/backups/{name}/push", { params: { name: pushing.name }, body: { engine, destination } });
          job.track(accepted, "push", destination);
          queued();
        }}
      />
      {deleting !== null ? (
        <ConfirmDialog
          open
          onOpenChange={(open) => (open ? undefined : setDeleting(null))}
          title={t("databases.dumps.deleteTitle")}
          description={t("databases.dumps.deleteDescription", { dump: deleting.name, count: (deleting.destinations ?? []).length })}
          actionLabel={t("databases.dumps.deleteAction")}
          confirmText={deleting.name}
          server={node}
          onConfirm={async () => {
            await request("delete", "/api/databases/backups/{name}", { params: { name: deleting.name }, query: { engine } });
            await queryClient.invalidateQueries({ queryKey: databaseKeys.allBackups });
          }}
        />
      ) : null}
      {opened !== null ? <DumpDrawer dump={opened} onClose={() => setOpened(null)} /> : null}
    </div>
  );
}
