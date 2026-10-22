import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, CalendarClock, CloudUpload, Play, ShieldQuestion, Trash2 } from "lucide-react";
import { useCallback, useState } from "react";

import { ElevationCancelledError, request } from "../../../api/client";
import { selfBackupKeys, selfBackupQuery } from "../../../api/queries/selfBackup";
import type { Bundle, SelfSchedule } from "../../../api/queries/selfBackup";
import { CommandHint } from "../../../components/page/CommandHint";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { RowMenu } from "../../../components/page/RowMenu";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { DataTable } from "../../../components/ui/DataTable";
import { EmptyState } from "../../../components/ui/EmptyState";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { ICONS } from "../../../components/ui/icons";
import { MenuItem, MenuSeparator } from "../../../components/ui/Menu";
import { Mono } from "../../../components/ui/Mono";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { backupRetentionWords } from "../policy/retention";
import { formatBytes } from "../../../lib/format";
import { useNode } from "../../../nodes/useNode";
import type { Accepted } from "../../databases/jobs";
import { useJobInHand } from "../../jobs/useJobInHand";
import type { JobWords } from "../../jobs/useJobInHand";
import { BackupsListPage } from "../BackupsListPage";
import { PushToDestinationDialog } from "../PushToDestinationDialog";
import { isFalse, isTrue, textOf } from "../policy/record";
import { scheduleWords } from "../volumes/data";
import { bundleKind, passphraseKnown, readSchedule } from "./data";
import { MigrationDialog } from "./MigrationDialog";
import { MigrationReport } from "./MigrationReport";
import { readMigrationReport } from "./migration";
import { PassphraseDialog } from "./PassphraseDialog";
import { RestoreInfoDialog } from "./RestoreInfoDialog";
import { ScheduleDrawer } from "./ScheduleDrawer";

function problemWords(t: T, problem: "noPassphrase" | "timerMissing" | "lastFailed" | "locked"): string {
  switch (problem) {
    case "noPassphrase":
      return t("selfBackup.state.problems.noPassphrase");
    case "timerMissing":
      return t("selfBackup.state.problems.timerMissing");
    case "lastFailed":
      return t("selfBackup.state.problems.lastFailed");
    case "locked":
      return t("selfBackup.state.problems.locked");
  }
}

/** Whether the schedule's passphrase is kept, in words. */
function passphraseWords(t: T, schedule: SelfSchedule): string {
  if (schedule.passphrase_stored === true) return t("selfBackup.facts.passphraseKept");
  if (schedule.passphrase_stored === false) return t("selfBackup.facts.passphraseNotKept");
  return t("selfBackup.facts.passphraseUnknown");
}

type Passphrase = { kind: "create" } | { kind: "verify"; bundle: Bundle } | null;

/**
 * The Noust tab: Noust's own state in encrypted bundles. The schedule's state is the notice
 * above the table, its details in a drawer; each bundle can be checked (restorable, without
 * restoring it), sent to a destination or deleted. A bundle is never restored or downloaded
 * from here: restoring replaces the store the console runs on, so the row says the command.
 */
export function SelfBackupPage() {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const overview = useQuery(selfBackupQuery());
  const refresh = useCallback(() => void queryClient.invalidateQueries({ queryKey: selfBackupKeys.all }), [queryClient]);
  const job = useJobInHand(refresh);
  const [scheduling, setScheduling] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [asking, setAsking] = useState<Passphrase>(null);
  const [pushing, setPushing] = useState<Bundle | null>(null);
  const [restoring, setRestoring] = useState<Bundle | null>(null);
  const [deleting, setDeleting] = useState<Bundle | null>(null);
  const [failure, setFailure] = useState<unknown>(null);
  const [migrating, setMigrating] = useState(false);
  const [migrationJob, setMigrationJob] = useState<string | null>(null);

  const data = overview.data;
  const schedule = data?.schedule;
  const bundles = data?.bundles ?? [];
  const reading = schedule === undefined ? null : readSchedule(schedule);
  const known = schedule !== undefined && passphraseKnown(schedule);

  const words = (key: "create" | "run"): JobWords => ({ running: t(`selfBackup.job.${key}.running`), succeeded: t(`selfBackup.job.${key}.succeeded`), failed: t(`selfBackup.job.${key}.failed`) });
  const verifyWords = (name: string): JobWords => ({
    running: t("selfBackup.job.verify.running", { name }),
    succeeded: t("selfBackup.job.verify.succeeded", { name }),
    failed: t("selfBackup.job.verify.failed", { name }),
  });

  const create = async (passphrase: string | null): Promise<void> => {
    const accepted: Accepted = await request("post", "/api/self-backup", { body: { passphrase } });
    job.track(accepted, words("create"));
  };
  const verify = async (bundle: Bundle, passphrase: string | null): Promise<void> => {
    const accepted: Accepted = await request("post", "/api/self-backup/{name}/verify", { params: { name: bundle.name }, body: { passphrase } });
    job.track(accepted, verifyWords(bundle.name));
  };
  const run = async (): Promise<void> => {
    const accepted: Accepted = await request("post", "/api/self-backup/run");
    job.track(accepted, words("run"));
  };

  // What the console starts without asking for anything (the server keeps the passphrase): a
  // refusal is shown above the table, and declining "Confirm it's you" is not one.
  const attempt = (action: () => Promise<void>): void => {
    setFailure(null);
    action().catch((error: unknown) => {
      if (!(error instanceof ElevationCancelledError)) setFailure(error);
    });
  };
  const backUpNow = (): void => {
    if (known) attempt(() => create(null));
    else setAsking({ kind: "create" });
  };

  const stateWord = reading === null ? "" : reading.health === "ok" ? t("selfBackup.state.on") : reading.health === "paused" ? t("selfBackup.state.paused") : reading.health === "problem" ? t("selfBackup.state.problem") : t("selfBackup.state.off");

  // The schedule is a card: its state is the pill after the title, its actions are the card's, and its
  // facts and problems are the body, so nothing is said once above the table and again in it.
  const stateCard =
    schedule === undefined || reading === null ? null : (
      <Card
        icon={CalendarClock}
        level={2}
        title={t("selfBackup.state.title")}
        status={<StatusPill size="sm" state={reading.health === "ok" ? "running" : reading.health === "problem" ? "warning" : "stopped"} label={stateWord} />}
        description={reading.health === "off" ? t("selfBackup.state.offBody") : reading.health === "paused" ? t("selfBackup.state.pausedBody") : undefined}
        actions={
          <>
            {reading.health === "ok" || reading.health === "problem" ? (
              <Button size="sm" icon={<Play aria-hidden="true" />} disabled={job.busy} onClick={() => attempt(run)}>
                {t("selfBackup.state.runNow")}
              </Button>
            ) : null}
            <Button size="sm" variant={schedule.configured ? "secondary" : "primary"} icon={<CalendarClock aria-hidden="true" />} onClick={() => setScheduling(true)}>
              {schedule.configured ? t("selfBackup.state.edit") : t("selfBackup.state.set")}
            </Button>
            {schedule.configured ? (
              <Button size="sm" variant="ghost" icon={<Trash2 aria-hidden="true" />} onClick={() => setRemoving(true)}>
                {t("selfBackup.schedule.remove")}
              </Button>
            ) : null}
          </>
        }
      >
        {schedule.configured || reading.problems.length > 0 ? (
          <div className="flex min-w-0 flex-col gap-3">
            {reading.problems.map((problem) => (
              <InlineMessage key={problem} tone={problem === "lastFailed" ? "error" : "warning"}>
                {problemWords(t, problem)}
              </InlineMessage>
            ))}
            {schedule.configured ? (
              <>
                <KeyValueList
                  items={[
                    {
                      label: t("selfBackup.facts.when"),
                      value: scheduleWords(schedule.schedule, t.locale),
                      mono: false,
                      copy: false,
                      ...(textOf(schedule.timer, "next_run") !== "" ? { hint: t("selfBackup.facts.nextRun", { when: textOf(schedule.timer, "next_run") }) } : {}),
                    },
                    { label: t("selfBackup.facts.keeps"), value: backupRetentionWords(t, schedule.retention_count, schedule.retention_days), mono: false, copy: false },
                    {
                      label: t("selfBackup.facts.sendsTo"),
                      value:
                        (schedule.destinations ?? []).length === 0 ? (
                          t("selfBackup.facts.onlyHere")
                        ) : (
                          <span className="flex flex-col gap-0.5">
                            {(schedule.destinations ?? []).map((destination) => {
                              const name = textOf(destination, "name");
                              return (
                                <span key={name} className="flex flex-wrap items-center gap-x-2">
                                  <Mono>{name}</Mono>
                                  {isTrue(destination, "encrypted") ? <span className="text-12 text-fg-muted">{t("selfBackup.facts.encrypted")}</span> : null}
                                  {isFalse(destination, "exists") ? <span className="text-12 text-fail">{t("selfBackup.facts.gone")}</span> : null}
                                </span>
                              );
                            })}
                          </span>
                        ),
                      mono: false,
                      copy: false,
                    },
                    { label: t("selfBackup.facts.passphrase"), value: passphraseWords(t, schedule), mono: false, copy: false },
                    { label: t("selfBackup.facts.lastRun"), value: schedule.last_run_at ? <RelativeTime value={schedule.last_run_at} /> : t("selfBackup.facts.notYet"), mono: false, copy: false },
                    {
                      label: t("selfBackup.facts.lastVerified"),
                      value: schedule.last_verified_at ? (
                        <span className="inline-flex items-center gap-2">
                          <RelativeTime value={schedule.last_verified_at} />
                          <StatusPill appearance="inline" size="sm" state={schedule.last_verify_status === "failed" ? "failed" : "running"} label={schedule.last_verify_status === "failed" ? t("selfBackup.facts.verifyFailed") : t("selfBackup.facts.verifyOk")} />
                        </span>
                      ) : (
                        t("selfBackup.facts.notYet")
                      ),
                      mono: false,
                      copy: false,
                    },
                  ]}
                />
                {schedule.last_status === "failed" && schedule.last_error ? (
                  <SystemOutput label={t("selfBackup.facts.lastError")} maxHeight="max-h-40">
                    {schedule.last_error}
                  </SystemOutput>
                ) : null}
              </>
            ) : null}
          </div>
        ) : undefined}
      </Card>
    );

  const migration = job.job !== null && job.job.id === migrationJob && job.job.state === "succeeded" ? readMigrationReport(job.job.result) : null;

  return (
    <BackupsListPage
      secondaryActions={
        <Button disabled={job.busy} onClick={() => setMigrating(true)}>
          {t("selfBackup.migrate.open")}
        </Button>
      }
      primaryAction={
        <Button variant="primary" icon={<ICONS.add aria-hidden="true" />} disabled={job.busy} onClick={backUpNow}>
          {t("selfBackup.backUpNow")}
        </Button>
      }
      footer={<CommandHint command="noust self-backup schedule set --schedule daily --keep 7" label={t("common.words.fromTerminal")} />}
    >
      <div className="flex min-w-0 flex-col gap-6">
        {job.notice}
        {migration !== null ? <MigrationReport report={migration} /> : null}
        {failure !== null ? <ErrorBlock live compact error={failure} title={t("selfBackup.passphrase.failed")} /> : null}
        {overview.isError && data === undefined ? (
          <ErrorBlock error={overview.error} title={t("selfBackup.loadFailed")} onRetry={() => void overview.refetch()} retrying={overview.isRefetching} />
        ) : (
          <>
            {stateCard}
            <p className="max-w-measure text-13 text-fg-muted">{t("selfBackup.holds")}</p>
            <DataTable<Bundle>
              caption={t("selfBackup.caption")}
              rows={bundles}
              loading={data === undefined}
              skeletonRows={3}
              getRowId={(bundle) => bundle.name}
              mobile="cards"
              empty={<EmptyState variant="inline" title={t("selfBackup.table.empty")} description={t("selfBackup.table.emptyHint")} />}
              columns={[
                {
                  id: "name",
                  header: t("selfBackup.table.bundle"),
                  card: "title",
                  cell: (bundle) => (
                    <span className="flex min-w-0 flex-col">
                      <Mono tone="default" truncate>
                        {bundle.name}
                      </Mono>
                      {bundle.host ? (
                        <Mono tone="faint" truncate>
                          {bundle.host}
                        </Mono>
                      ) : null}
                    </span>
                  ),
                },
                {
                  id: "kind",
                  header: t("selfBackup.table.kind"),
                  width: "w-44",
                  hideBelow: "sm",
                  cell: (bundle) => (
                    <span className="inline-flex flex-wrap items-center gap-2">
                      <Badge>{t(`selfBackup.table.kinds.${bundleKind(bundle)}`)}</Badge>
                      {bundle.sidecar_mac === "invalid" ? <StatusPill appearance="inline" size="sm" state="failed" label={t("selfBackup.table.macInvalid")} /> : null}
                    </span>
                  ),
                },
                { id: "taken", header: t("selfBackup.table.taken"), width: "w-36", card: "status", cell: (bundle) => (bundle.created_at ? <RelativeTime value={bundle.created_at} /> : null) },
                {
                  id: "where",
                  header: t("selfBackup.table.where"),
                  hideBelow: "md",
                  cell: (bundle) =>
                    (bundle.remote_copies ?? []).length === 0 ? (
                      <span className="text-fg-muted">{t("selfBackup.table.onlyHere")}</span>
                    ) : (
                      <span className="inline-flex flex-wrap gap-1">
                        {(bundle.remote_copies ?? []).map((copy, index) => (
                          <Badge key={textOf(copy, "destination") || String(index)} mono>
                            {textOf(copy, "destination")}
                          </Badge>
                        ))}
                      </span>
                    ),
                },
                {
                  id: "size",
                  header: t("selfBackup.table.size"),
                  align: "end",
                  mono: true,
                  width: "w-28",
                  cell: (bundle) => formatBytes(bundle.size, t.locale),
                },
              ]}
              rowActions={(bundle) => (
                <RowMenu name={bundle.name}>
                  <MenuItem icon={<ShieldQuestion />} disabled={job.busy} onClick={() => (known ? attempt(() => verify(bundle, null)) : setAsking({ kind: "verify", bundle }))}>
                    {t("selfBackup.table.verify")}
                  </MenuItem>
                  <MenuItem icon={<CloudUpload />} disabled={job.busy} onClick={() => setPushing(bundle)}>
                    {t("selfBackup.table.push")}
                  </MenuItem>
                  <MenuItem icon={<BookOpen />} onClick={() => setRestoring(bundle)}>
                    {t("selfBackup.table.restore")}
                  </MenuItem>
                  <MenuSeparator />
                  <MenuItem icon={<Trash2 />} destructive onClick={() => setDeleting(bundle)}>
                    {t("common.words.delete")}
                  </MenuItem>
                </RowMenu>
              )}
            />
          </>
        )}
      </div>

      {scheduling ? <ScheduleDrawer key={schedule?.updated_at ?? "new"} schedule={schedule} open onOpenChange={(next) => setScheduling(next)} /> : null}
      {asking?.kind === "create" ? (
        <PassphraseDialog
          open
          onOpenChange={(next) => (next ? undefined : setAsking(null))}
          repeat
          title={t("selfBackup.passphrase.createTitle")}
          description={t("selfBackup.passphrase.createDescription")}
          submit={t("common.words.backUpNow")}
          onSubmit={(passphrase) => create(passphrase)}
        />
      ) : null}
      {asking?.kind === "verify" ? (
        <PassphraseDialog
          open
          onOpenChange={(next) => (next ? undefined : setAsking(null))}
          repeat={false}
          title={t("selfBackup.passphrase.verifyTitle", { name: asking.bundle.name })}
          description={t("selfBackup.passphrase.verifyDescription")}
          submit={t("selfBackup.passphrase.verify")}
          onSubmit={(passphrase) => verify(asking.bundle, passphrase)}
        />
      ) : null}
      {pushing !== null ? (
        <PushToDestinationDialog
          open
          onOpenChange={(next) => (next ? undefined : setPushing(null))}
          words={{
            title: t("selfBackup.push.title", { name: pushing.name }),
            description: t("selfBackup.push.description"),
            destination: t("selfBackup.push.destination"),
            choose: t("selfBackup.push.choose"),
            none: t("selfBackup.push.none"),
            action: t("selfBackup.push.action"),
            failed: t("selfBackup.push.failed"),
          }}
          onPush={async (destination) => {
            const accepted: Accepted = await request("post", "/api/self-backup/{name}/push", { params: { name: pushing.name }, body: { destination } });
            job.track(accepted, {
              running: t("selfBackup.job.push.running", { name: pushing.name, destination }),
              succeeded: t("selfBackup.job.push.succeeded", { name: pushing.name, destination }),
              failed: t("selfBackup.job.push.failed", { name: pushing.name, destination }),
            });
          }}
        />
      ) : null}
      {restoring !== null ? <RestoreInfoDialog name={restoring.name} open onOpenChange={(next) => (next ? undefined : setRestoring(null))} /> : null}
      {deleting !== null ? (
        <ConfirmDialog
          friction="type"
          open
          onOpenChange={(next) => (next ? undefined : setDeleting(null))}
          title={t("common.words.deleteNamed", { name: deleting.name })}
          description={t("selfBackup.delete.description")}
          actionLabel={t("selfBackup.delete.action")}
          confirmText={deleting.name}
          server={node}
          onConfirm={async () => {
            await request("delete", "/api/self-backup/{name}", { params: { name: deleting.name } });
            refresh();
          }}
        />
      ) : null}
      <MigrationDialog
        open={migrating}
        onOpenChange={setMigrating}
        onQueued={(accepted) => {
          setMigrationJob(accepted.job_id);
          job.track(accepted, { running: t("selfBackup.migrate.job.running"), succeeded: t("selfBackup.migrate.job.succeeded"), failed: t("selfBackup.migrate.job.failed") });
        }}
      />
      <ConfirmDialog
        friction="simple"
        open={removing}
        onOpenChange={setRemoving}
        title={t("selfBackup.schedule.removeTitle")}
        description={t("selfBackup.schedule.removeDescription")}
        actionLabel={t("selfBackup.schedule.removeAction")}
        server={node}
        onConfirm={async () => {
          await request("delete", "/api/self-backup/schedule");
          refresh();
        }}
      />
    </BackupsListPage>
  );
}
