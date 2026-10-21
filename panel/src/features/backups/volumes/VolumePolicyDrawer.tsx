import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../../api/client";
import { appsQuery } from "../../../api/queries/apps";
import { CONSISTENCY_MODES, volumeKeys, volumeTargetsQuery } from "../../../api/queries/volumeBackups";
import type { Consistency, VolumePolicy } from "../../../api/queries/volumeBackups";
import { LoadingRegion } from "../../../components/page";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ChoiceCards } from "../../../components/ui/ChoiceCards";
import { Drawer } from "../../../components/ui/Drawer";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Field } from "../../../components/ui/Field";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { Mono } from "../../../components/ui/Mono";
import { Select } from "../../../components/ui/Select";
import { Skeleton } from "../../../components/ui/Skeleton";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";
import { validTime } from "../../databases/backups/schedule";
import { KeepFields } from "../policy/KeepFields";
import { GbField } from "../policy/GbField";
import { MAX_COUNT, MAX_DAYS, destinationsValid, validGb, validLimit } from "../policy/limits";
import { RemoteOnlyField } from "../policy/RemoteOnlyField";
import { ScheduleFields } from "../policy/ScheduleFields";
import { SendToFields } from "../policy/SendToFields";
import { bodyOf, formOf, parsePaths, validPath } from "./data";
import type { VolumeForm } from "./data";

type ErrorKey = "domain" | "schedule" | "time" | "count" | "days" | "gb" | "destinations" | "targets" | "paths";

export interface VolumePolicyDrawerProps {
  /** The application, when it is chosen already (editing, or opened from its row). */
  domain: string | null;
  policy: VolumePolicy | null;
  /** Applications that already have a policy: a new policy is not offered for them. */
  taken: ReadonlySet<string>;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * An application's data backup policy, as one form in a drawer: which application, which pieces
 * of its data (everything it declares, or the ones chosen from what it does declare, with folders
 * of your own added), when, how many to keep here and where else each goes, and how still the
 * data is kept while it is copied. Saving installs its timer, in sudo mode.
 */
export function VolumePolicyDrawer({ domain: fixed, policy, taken, open, onOpenChange }: VolumePolicyDrawerProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const apps = useQuery({ ...appsQuery(), enabled: open && fixed === null });
  const [form, setForm] = useState<VolumeForm>(() => formOf(fixed ?? "", policy));
  const [errors, setErrors] = useState<Partial<Record<ErrorKey, string>>>({});
  const set = (patch: Partial<VolumeForm>): void => setForm((previous) => ({ ...previous, ...patch }));
  const domain = fixed ?? form.domain;
  const targets = useQuery({ ...volumeTargetsQuery(domain, policy?.extra_paths ?? []), enabled: open && domain !== "" });
  const choices = (apps.data?.apps ?? []).filter((app) => !taken.has(app.domain));

  const save = useMutation({
    mutationFn: () => request("put", "/api/volume-backups/policies/{domain}", { params: { domain }, body: bodyOf(form) }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: volumeKeys.all });
      onOpenChange(false);
    },
    onError: (error) => {
      if (isApiError(error) && error.fields?.["schedule"] !== undefined) setErrors({ schedule: error.fields["schedule"] });
    },
  });

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const found: Partial<Record<ErrorKey, string>> = {};
    if (domain === "") found.domain = t("volumeBackups.form.applicationRequired");
    if (form.schedule.frequency === "custom" && form.schedule.custom.trim() === "") found.schedule = t("databases.policy.customRequired");
    if (form.schedule.frequency !== "hourly" && form.schedule.frequency !== "custom" && !validTime(form.schedule.time)) found.time = t("databases.policy.timeInvalid");
    if (!validLimit(form.count, MAX_COUNT)) found.count = t("databases.policy.countInvalid");
    if (!validLimit(form.days, MAX_DAYS)) found.days = t("databases.policy.daysInvalid");
    if (!validGb(form.gb)) found.gb = t("databases.policy.gbInvalid");
    if (!destinationsValid(form.sends)) found.destinations = t("databases.policy.destinationLimitInvalid");
    const paths = parsePaths(form.extraPaths);
    const bad = paths.filter((path) => !validPath(path));
    if (bad.length > 0) found.paths = t("volumeBackups.form.pathsInvalid", { entries: bad.join(", ") });
    if (!form.everything && form.targets.length === 0 && paths.length === 0) found.targets = t("volumeBackups.form.pickOne");
    setErrors(found);
    if (Object.keys(found).length === 0) save.mutate();
  };

  const formId = "volume-policy-form";
  const declared = targets.data ?? [];

  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      size="md"
      title={policy !== null ? t("volumeBackups.form.editTitle", { domain }) : t("volumeBackups.form.newTitle")}
      description={t("volumeBackups.form.description")}
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>{t("volumeBackups.form.cancel")}</Button>
          <Button type="submit" form={formId} variant="primary" loading={save.isPending}>
            {t("volumeBackups.form.save")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-6">
        {fixed === null ? (
          <Field label={t("volumeBackups.form.application")} nativeLabel={false} error={errors.domain}>
            <Select
              mono
              value={form.domain === "" ? null : form.domain}
              placeholder={t("volumeBackups.form.applicationPlaceholder")}
              onValueChange={(value) => set({ domain: value, targets: [] })}
              options={choices.map((app) => ({ value: app.domain, label: app.domain }))}
            />
          </Field>
        ) : null}
        {fixed === null && apps.data !== undefined && choices.length === 0 ? <EmptyState variant="inline" title={t("volumeBackups.form.noApps")} /> : null}

        <div className="flex min-w-0 flex-col gap-4">
          <ChoiceCards<"everything" | "own">
            legend={t("volumeBackups.form.covers")}
            value={form.everything ? "everything" : "own"}
            onValueChange={(value) => set({ everything: value === "everything" })}
            options={[
              { value: "everything", label: t("volumeBackups.form.everything"), description: t("volumeBackups.form.everythingHelp") },
              { value: "own", label: t("volumeBackups.form.chooseOwn"), description: domain === "" ? t("volumeBackups.form.chooseOwnHelpNone") : t("volumeBackups.form.chooseOwnHelp", { domain }) },
            ]}
          />
          {!form.everything ? (
            <div className="flex min-w-0 flex-col gap-3">
              {domain === "" ? null : targets.isError ? (
                <ErrorBlock compact error={targets.error} title={t("volumeBackups.form.targetsFailed")} onRetry={() => void targets.refetch()} />
              ) : targets.data === undefined ? (
                <LoadingRegion label={t("volumeBackups.form.targetsLoading")}>
                  <Skeleton className="h-16 w-full rounded-card" />
                </LoadingRegion>
              ) : declared.length === 0 ? (
                <EmptyState variant="inline" title={t("volumeBackups.form.noTargets")} />
              ) : (
                <ul className="flex flex-col gap-2.5">
                  {declared.map((target) => (
                    <li key={target.key}>
                      <Checkbox
                        checked={form.targets.includes(target.key)}
                        onCheckedChange={(checked) => set({ targets: checked ? [...form.targets, target.key] : form.targets.filter((key) => key !== target.key) })}
                        label={
                          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                            <Mono tone="default">{target.source}</Mono>
                            <Badge>{target.kind === "docker" ? t("volumeBackups.form.kind.docker") : t("volumeBackups.form.kind.path")}</Badge>
                            {!target.exists ? <span className="text-12 text-fg-muted">{t("volumeBackups.form.missing")}</span> : null}
                          </span>
                        }
                        description={t("volumeBackups.form.declaredBy", { source: target.declared_by })}
                      />
                    </li>
                  ))}
                </ul>
              )}
              {errors.targets !== undefined ? <InlineMessage tone="error">{errors.targets}</InlineMessage> : null}
            </div>
          ) : null}
          <Field label={t("volumeBackups.form.extraPaths")} optional description={t("volumeBackups.form.extraPathsHelp")} error={errors.paths}>
            <Textarea mono rows={2} value={form.extraPaths} onChange={(event) => set({ extraPaths: event.target.value })} autoComplete="off" spellCheck={false} />
          </Field>
        </div>

        <ScheduleFields value={form.schedule} onChange={(schedule) => set({ schedule })} scheduleError={errors.schedule} timeError={errors.time} />

        <KeepFields
          words={{
            legend: t("volumeBackups.form.keepHere"),
            count: t("volumeBackups.form.count"),
            countHelp: t("volumeBackups.form.countHelp"),
            days: t("volumeBackups.form.days"),
            daysHelp: t("volumeBackups.form.daysHelp"),
            note: t("volumeBackups.form.note"),
          }}
          count={form.count}
          days={form.days}
          onCount={(count) => set({ count })}
          onDays={(days) => set({ days })}
          countError={errors.count}
          daysError={errors.days}
        >
          <GbField label={t("volumeBackups.form.gb")} help={t("volumeBackups.form.gbHelp")} value={form.gb} onChange={(gb) => set({ gb })} error={errors.gb} />
        </KeepFields>

        <SendToFields
          enabled={open}
          words={{
            legend: t("volumeBackups.form.sendTo"),
            none: t("volumeBackups.form.noDestinations"),
            noneHint: t("volumeBackups.form.noDestinationsHint"),
            failed: t("volumeBackups.form.destinationsFailed"),
            keepAll: t("volumeBackups.form.keepAll"),
            countSuffix: t("volumeBackups.form.backupsSuffix"),
            daysSuffix: t("volumeBackups.form.daysSuffix"),
            countFor: (destination) => t("volumeBackups.form.destinationCount", { destination }),
            daysFor: (destination) => t("volumeBackups.form.destinationDays", { destination }),
            gbFor: (destination) => t("volumeBackups.form.destinationGb", { destination }),
          }}
          value={form.sends}
          onChange={(sends) => set({ sends })}
          error={errors.destinations}
        />
        <RemoteOnlyField
          words={{
            label: t("volumeBackups.form.remoteOnly"),
            help: t("volumeBackups.form.remoteOnlyHelp"),
            needsDestination: t("volumeBackups.form.remoteOnlyNeedsDestination"),
          }}
          value={form.remoteOnly}
          onChange={(remoteOnly) => set({ remoteOnly })}
          destinations={form.sends.length}
        />

        <ChoiceCards<Consistency>
          legend={t("volumeBackups.consistency.legend")}
          value={form.consistency}
          onValueChange={(consistency) => set({ consistency })}
          options={CONSISTENCY_MODES.map((mode) => ({ value: mode, label: t(`volumeBackups.consistency.${mode}`), description: t(`volumeBackups.consistency.${mode}Help`) }))}
        />

        <Checkbox checked={form.enabled} onCheckedChange={(enabled) => set({ enabled })} label={t("volumeBackups.form.enabled")} description={t("volumeBackups.form.enabledHelp")} />
        {save.isError && !(isApiError(save.error) && save.error.fields?.["schedule"] !== undefined) ? <ErrorBlock live compact error={save.error} title={t("volumeBackups.form.saveFailed")} /> : null}
      </form>
    </Drawer>
  );
}
