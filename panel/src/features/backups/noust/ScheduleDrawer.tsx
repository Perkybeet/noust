import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../../api/client";
import { selfBackupKeys } from "../../../api/queries/selfBackup";
import type { SelfSchedule } from "../../../api/queries/selfBackup";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Drawer } from "../../../components/ui/Drawer";
import { Field, FieldGroup } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { useT } from "../../../i18n";
import { validTime } from "../../databases/backups/schedule";
import { KeepFields } from "../policy/KeepFields";
import { GbField } from "../policy/GbField";
import { MAX_COUNT, MAX_DAYS, destinationsValid, validGb, validLimit } from "../policy/limits";
import { RemoteOnlyField } from "../policy/RemoteOnlyField";
import { ScheduleFields } from "../policy/ScheduleFields";
import { SendToFields } from "../policy/SendToFields";
import { bodyOf, formOf, passphraseKnown, passphraseProblem } from "./data";
import type { SelfForm } from "./data";

type ErrorKey = "schedule" | "time" | "count" | "days" | "gb" | "destinations" | "passphrase";

export interface ScheduleDrawerProps {
  schedule: SelfSchedule | undefined;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * The schedule of Noust's own backup, as one form in a drawer: when, how many bundles to keep
 * here and where else each goes, and the passphrase the timer seals them with, which the server
 * keeps in its secret store for it. Without one stored it is required; with one stored, empty
 * keeps it. Saving asks "Confirm it's you": a schedule is a root timer that carries every
 * secret the server has away.
 */
export function ScheduleDrawer({ schedule, open, onOpenChange }: ScheduleDrawerProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const [form, setForm] = useState<SelfForm>(() => formOf(schedule));
  const [errors, setErrors] = useState<Partial<Record<ErrorKey, string>>>({});
  const set = (patch: Partial<SelfForm>): void => setForm((previous) => ({ ...previous, ...patch }));
  const known = schedule !== undefined && passphraseKnown(schedule);

  const save = useMutation({
    mutationFn: () => request("put", "/api/self-backup/schedule", { body: bodyOf(form) }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: selfBackupKeys.all });
      onOpenChange(false);
    },
    onError: (error) => {
      if (!isApiError(error) || error.fields === null) return;
      const found: typeof errors = {};
      if (error.fields["schedule"] !== undefined) found.schedule = error.fields["schedule"];
      if (error.fields["passphrase"] !== undefined) found.passphrase = error.fields["passphrase"];
      if (Object.keys(found).length > 0) setErrors(found);
    },
  });

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const found: typeof errors = {};
    if (form.schedule.frequency === "custom" && form.schedule.custom.trim() === "") found.schedule = t("databases.policy.customRequired");
    if (form.schedule.frequency !== "hourly" && form.schedule.frequency !== "custom" && !validTime(form.schedule.time)) found.time = t("databases.policy.timeInvalid");
    if (!validLimit(form.count, MAX_COUNT)) found.count = t("databases.policy.countInvalid");
    if (!validLimit(form.days, MAX_DAYS)) found.days = t("databases.policy.daysInvalid");
    if (!validGb(form.gb)) found.gb = t("databases.policy.gbInvalid");
    if (!destinationsValid(form.sends)) found.destinations = t("databases.policy.destinationLimitInvalid");
    if (form.passphrase !== "" || !known) {
      if (form.passphrase === "") found.passphrase = t("selfBackup.schedule.passphraseRequired");
      else {
        const problem = passphraseProblem(form.passphrase, form.repeat);
        if (problem !== null) found.passphrase = t(problem === "short" ? "selfBackup.passphrase.short" : problem === "long" ? "selfBackup.passphrase.long" : "selfBackup.passphrase.mismatch");
      }
    }
    setErrors(found);
    if (Object.keys(found).length === 0) save.mutate();
  };

  const formId = "self-backup-schedule-form";

  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      size="md"
      title={t("selfBackup.schedule.title")}
      description={t("selfBackup.schedule.description")}
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>{t("selfBackup.schedule.cancel")}</Button>
          <Button type="submit" form={formId} variant="primary" loading={save.isPending}>
            {t("selfBackup.schedule.save")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-6">
        <ScheduleFields value={form.schedule} onChange={(next) => set({ schedule: next })} scheduleError={errors.schedule} timeError={errors.time} />

        <KeepFields
          words={{
            legend: t("selfBackup.schedule.keepHere"),
            count: t("selfBackup.schedule.count"),
            countHelp: t("selfBackup.schedule.countHelp"),
            days: t("selfBackup.schedule.days"),
            daysHelp: t("selfBackup.schedule.daysHelp"),
            note: t("selfBackup.schedule.note"),
          }}
          count={form.count}
          days={form.days}
          onCount={(count) => set({ count })}
          onDays={(days) => set({ days })}
          countError={errors.count}
          daysError={errors.days}
        >
          <GbField label={t("selfBackup.schedule.gb")} help={t("selfBackup.schedule.gbHelp")} value={form.gb} onChange={(gb) => set({ gb })} error={errors.gb} />
        </KeepFields>

        <SendToFields
          enabled={open}
          words={{
            legend: t("selfBackup.schedule.sendTo"),
            none: t("selfBackup.schedule.noDestinations"),
            noneHint: t("selfBackup.schedule.noDestinationsHint"),
            failed: t("selfBackup.schedule.destinationsFailed"),
            keepAll: t("selfBackup.schedule.keepAll"),
            countSuffix: t("selfBackup.schedule.countSuffix"),
            daysSuffix: t("selfBackup.schedule.daysSuffix"),
            countFor: (destination) => t("selfBackup.schedule.destinationCount", { destination }),
            daysFor: (destination) => t("selfBackup.schedule.destinationDays", { destination }),
            gbFor: (destination) => t("selfBackup.schedule.destinationGb", { destination }),
          }}
          value={form.sends}
          onChange={(sends) => set({ sends })}
          error={errors.destinations}
        />
        <RemoteOnlyField
          words={{
            label: t("selfBackup.schedule.remoteOnly"),
            help: t("selfBackup.schedule.remoteOnlyHelp"),
            needsDestination: t("selfBackup.schedule.remoteOnlyNeedsDestination"),
          }}
          value={form.remoteOnly}
          onChange={(remoteOnly) => set({ remoteOnly })}
          destinations={form.sends.length}
        />

        <FieldGroup legend={t("selfBackup.schedule.passphraseTitle")} description={known ? t("selfBackup.schedule.passphraseKept") : t("selfBackup.schedule.passphraseNew")}>
          <Field label={t("selfBackup.passphrase.label")} optional={known} description={t("selfBackup.passphrase.help")} error={errors.passphrase}>
            <Input mono type="password" value={form.passphrase} onValueChange={(passphrase: string) => set({ passphrase })} autoComplete="new-password" autoCapitalize="off" spellCheck={false} />
          </Field>
          {form.passphrase !== "" ? (
            <Field label={t("selfBackup.passphrase.repeat")} description={t("selfBackup.passphrase.repeatHelp")}>
              <Input mono type="password" value={form.repeat} onValueChange={(repeat: string) => set({ repeat })} autoComplete="new-password" autoCapitalize="off" spellCheck={false} />
            </Field>
          ) : null}
        </FieldGroup>

        <Checkbox checked={form.enabled} onCheckedChange={(enabled) => set({ enabled })} label={t("selfBackup.schedule.enabled")} description={t("selfBackup.schedule.enabledHelp")} />
        {save.isError && !(isApiError(save.error) && save.error.fields !== null && (save.error.fields["schedule"] !== undefined || save.error.fields["passphrase"] !== undefined)) ? (
          <ErrorBlock live compact error={save.error} title={t("selfBackup.schedule.saveFailed")} />
        ) : null}
      </form>
    </Drawer>
  );
}
