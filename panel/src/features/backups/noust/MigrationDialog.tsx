import { useMutation, useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../../api/client";
import { backupDestinationsQuery } from "../../../api/queries/backupDestinations";
import { migrationPlanQuery } from "../../../api/queries/serverMigration";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import type { Accepted } from "../../databases/jobs";
import { LoadingRegion } from "../../../components/page/LoadingRegion";

export interface MigrationDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The job that copies everything, to follow. */
  onQueued: (accepted: Accepted) => void;
}

/**
 * "Move to another server": which destination both servers reach, what moving would take (every
 * application with its names, databases and data, the engines the new server needs, what stops it)
 * and the passphrase Noust's own state is sealed under. Nothing is copied until the button, which
 * queues the preparation as a job (`POST /api/server/migration/prepare`, sudo mode); nothing on this
 * server is stopped or changed by it.
 */
export function MigrationDialog({ open, onOpenChange, onQueued }: MigrationDialogProps) {
  const t = useT();
  const formId = useId();
  const destinations = useQuery({ ...backupDestinationsQuery(), enabled: open });
  const [destination, setDestination] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [pressed, setPressed] = useState(false);
  const plan = useQuery({ ...migrationPlanQuery(destination), enabled: open && destination !== "" });
  const options = (destinations.data?.destinations ?? []).map((entry) => ({ value: entry.name, label: entry.name }));
  const stored = plan.data?.passphrase_stored === true;
  const blockers = plan.data?.blockers ?? [];
  const start = useMutation({
    mutationFn: () =>
      request("post", "/api/server/migration/prepare", {
        body: { destination, passphrase: passphrase === "" ? null : passphrase, apps: [], data: true },
      }),
    onSuccess: (accepted) => {
      onQueued(accepted);
      close(false);
    },
  });

  const close = (next: boolean): void => {
    if (!next && start.isPending) return;
    onOpenChange(next);
    if (!next) {
      setDestination("");
      setPassphrase("");
      setPressed(false);
      start.reset();
    }
  };
  const ready = plan.data !== undefined && blockers.length === 0 && (stored || passphrase !== "");
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (ready) start.mutate();
    else setPressed(true);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={close}
      size="lg"
      title={t("selfBackup.migrate.title")}
      description={t("selfBackup.migrate.description")}
      footer={
        <>
          <Button disabled={start.isPending} onClick={() => close(false)}>
            {t("selfBackup.schedule.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={start.isPending} disabled={blockers.length > 0}>
            {t("selfBackup.migrate.start")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        <Field
          label={t("selfBackup.migrate.destination")}
          nativeLabel={false}
          description={t("selfBackup.migrate.destinationHelp")}
          error={pressed && destination === "" ? t("selfBackup.migrate.choose") : undefined}
        >
          <Select
            aria-label={t("selfBackup.migrate.destination")}
            value={destination}
            onValueChange={setDestination}
            placeholder={destinations.isPending ? t("backups.fields.loadingDestinations") : t("selfBackup.migrate.choose")}
            options={options}
            disabled={destinations.isPending || options.length === 0}
          />
        </Field>
        {!destinations.isPending && options.length === 0 ? <p className="text-13 text-fg-muted">{t("selfBackup.migrate.noDestinations")}</p> : null}
        {destination !== "" && plan.isPending ? (
          <LoadingRegion label={t("selfBackup.migrate.planning")}>
            <Skeleton className="h-28 w-full rounded-control" />
          </LoadingRegion>
        ) : null}
        {plan.isError ? <ErrorBlock compact error={plan.error} title={t("selfBackup.migrate.planFailed")} /> : null}
        {plan.data !== undefined ? (
          <div className="flex flex-col gap-4">
            {blockers.length > 0 ? (
              <Notice tone="error" title={t("selfBackup.migrate.blockedTitle")} live>
                <ul className="flex list-disc flex-col gap-1 pl-5">
                  {blockers.map((blocker) => (
                    <li key={blocker}>{blocker}</li>
                  ))}
                </ul>
              </Notice>
            ) : null}
            <div className="flex flex-col gap-2">
              <p className="text-13 font-medium text-fg">{t("selfBackup.migrate.takes")}</p>
              {(plan.data.applications ?? []).length === 0 ? (
                <p className="text-13 text-fg-muted">{t("selfBackup.migrate.noApplications")}</p>
              ) : (
                <ul className="flex flex-col divide-y divide-border">
                  {(plan.data.applications ?? []).map((entry) => (
                    <li key={String(entry["domain"])} className="flex min-w-0 flex-col gap-0.5 py-2 first:pt-0 last:pb-0">
                      <Mono tone="default">{String(entry["domain"])}</Mono>
                      <span className="text-12 text-fg-muted">
                        {t("selfBackup.migrate.applicationFacts", {
                          names: Array.isArray(entry["names"]) ? (entry["names"] as unknown[]).map(String).join(", ") : "",
                          data: typeof entry["data"] === "number" ? entry["data"] : 0,
                        })}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              {(plan.data.databases ?? []).length > 0 ? (
                <p className="text-13 text-fg-muted">{t("selfBackup.migrate.databases", { databases: (plan.data.databases ?? []).join(", ") })}</p>
              ) : null}
              {(plan.data.engines ?? []).length > 0 ? (
                <p className="text-13 text-fg-muted">{t("selfBackup.migrate.engines", { engines: (plan.data.engines ?? []).join(", ") })}</p>
              ) : null}
            </div>
            {(plan.data.warnings ?? []).length > 0 ? (
              <Notice title={t("selfBackup.migrate.warningsTitle")}>
                <ul className="flex list-disc flex-col gap-1 pl-5">
                  {(plan.data.warnings ?? []).map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              </Notice>
            ) : null}
            <Field
              label={t("selfBackup.migrate.passphrase")}
              optional={stored}
              description={stored ? t("selfBackup.migrate.passphraseStored") : t("selfBackup.migrate.passphraseHelp")}
              error={pressed && blockers.length === 0 && !stored && passphrase === "" ? t("backups.fields.required") : undefined}
            >
              <Input mono type="password" value={passphrase} onValueChange={(value: string) => setPassphrase(value)} autoComplete="new-password" autoCapitalize="off" spellCheck={false} />
            </Field>
          </div>
        ) : null}
        {start.isError ? <ErrorBlock live compact error={start.error} title={t("selfBackup.migrate.failed")} /> : null}
        <CommandHint command="noust server migrate prepare --to DESTINATION" label={t("backups.common.fromTerminal")} />
      </form>
    </Dialog>
  );
}
