import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../../api/client";
import { databaseKeys } from "../../../api/queries/databases";
import type { BackupPolicy, PolicyBody } from "../../../api/queries/databases";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Drawer } from "../../../components/ui/Drawer";
import { Field } from "../../../components/ui/Field";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { Input } from "../../../components/ui/Input";
import { Select } from "../../../components/ui/Select";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { KeepFields } from "../../backups/policy/KeepFields";
import { MAX_COUNT, MAX_DAYS, destinationBody, destinationsValid, gbValue, limitText, limitValue, validGb, validLimit } from "../../backups/policy/limits";
import type { DestinationDraft } from "../../backups/policy/limits";
import { ScheduleFields } from "../../backups/policy/ScheduleFields";
import { SendToFields } from "../../backups/policy/SendToFields";
import { engineFamilyOf } from "../engineKey";
import { engineName } from "../engines";
import { scheduleExpression, scheduleForm, validTime } from "./schedule";
import type { ScheduleForm } from "./schedule";
import { MAX_EXCLUSIONS, exclusionKinds, exclusionText, parseNames } from "./scope";
import type { ExclusionKind, PolicyScope } from "./scope";

/** What a kind of exclusion is called and explained as, on this engine (MongoDB's tables are collections). */
export function exclusionWords(t: T, kind: ExclusionKind, engine: string): { label: string; help: string } {
  if (kind === "databases") return { label: t("policyScope.drawer.databases"), help: t("policyScope.drawer.databasesHelp") };
  if (kind === "schemas") return { label: t("policyScope.drawer.schemas"), help: t("policyScope.drawer.schemasHelp") };
  return engineFamilyOf(engine) === "mongodb"
    ? { label: t("policyScope.drawer.collections"), help: t("policyScope.drawer.collectionsHelp") }
    : { label: t("policyScope.drawer.tables"), help: t("policyScope.drawer.tablesHelp") };
}

export interface PolicyDrawerProps {
  engine: string;
  /** The database, or `*` for the policy over every database of the engine. */
  name: string;
  /** `all` edits the policy that covers every database of the engine (stored under `*`). */
  scope?: PolicyScope;
  policy: BackupPolicy | undefined;
  /** PostgreSQL chooses its dump format. */
  formats: boolean;
  /** Test-restoring each dump is not available for Redis. */
  testRestore: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

type ErrorKey = "schedule" | "time" | "count" | "days" | "gb" | "destinations" | "remote" | ExclusionKind;

/**
 * A database's backup policy, as one form in a drawer: when (said back in plain words), how
 * many dumps and for how long to keep them here, where else each one goes (with that place's
 * own retention), what it leaves out (whole databases, schemas, tables or collections, for the
 * engines that can) and whether each dump is proven by loading it into a throwaway database.
 * Over every database of an engine (`scope="all"`) it is one policy for the ones that exist and
 * the ones created later. Saving it installs its timer, in sudo mode.
 */
export function PolicyDrawer({ engine, name, scope = "database", policy, formats, testRestore, open, onOpenChange }: PolicyDrawerProps) {
  const t = useT();
  const kinds = exclusionKinds(engine, scope);
  const display = engineName(engine);
  const queryClient = useQueryClient();
  const configured = policy?.configured === true;
  const [schedule, setSchedule] = useState<ScheduleForm>(() => scheduleForm(configured ? policy.schedule : null));
  const [count, setCount] = useState(configured ? limitText(policy.retention_count) : "7");
  const [days, setDays] = useState(configured ? limitText(policy.retention_days) : "30");
  const [gb, setGb] = useState(configured ? limitText(policy.retention_gb) : "");
  const [sends, setSends] = useState<DestinationDraft[]>(() =>
    configured ? (policy.destinations ?? []).map((item) => ({ name: item.name, count: limitText(item.retention_count), days: limitText(item.retention_days), gb: limitText(item.retention_gb) })) : [],
  );
  const [format, setFormat] = useState(policy?.dump_format ?? "custom");
  const [verify, setVerify] = useState(policy?.verify_restore ?? false);
  const [remote, setRemote] = useState(policy?.remote_only ?? false);
  const [enabled, setEnabled] = useState(configured ? policy.enabled : true);
  const [errors, setErrors] = useState<Partial<Record<ErrorKey, string>>>({});
  const [left, setLeft] = useState<Record<ExclusionKind, string>>(() => ({
    databases: exclusionText(policy, "databases"),
    schemas: exclusionText(policy, "schemas"),
    tables: exclusionText(policy, "tables"),
  }));
  const namesOf = (kind: ExclusionKind): string[] => (kinds.includes(kind) ? parseNames(left[kind], kind).names : []);

  const save = useMutation({
    mutationFn: () => {
      const body: PolicyBody = {
        schedule: scheduleExpression(schedule),
        retention_count: limitValue(count),
        retention_days: limitValue(days),
        retention_gb: gbValue(gb),
        remote_only: remote && sends.length > 0,
        destinations: destinationBody(sends),
        dump_format: formats ? format : null,
        verify_restore: testRestore && verify,
        enabled,
        exclude_databases: namesOf("databases"),
        exclude_schemas: namesOf("schemas"),
        exclude_tables: namesOf("tables"),
      };
      return request("put", "/api/databases/backup-policies/{engine}/{database}", { params: { engine, database: name }, body });
    },
    onSuccess: (saved) => {
      queryClient.setQueryData(databaseKeys.policy(engine, name), saved);
      void queryClient.invalidateQueries({ queryKey: databaseKeys.policies });
      onOpenChange(false);
    },
    onError: (error) => {
      if (!isApiError(error) || error.fields === null) return;
      const found: typeof errors = {};
      if (error.fields["schedule"] !== undefined) found.schedule = error.fields["schedule"];
      // The server names the lists as it does in the request, the form as the operator reads them.
      if (error.fields["exclude_databases"] !== undefined) found.databases = error.fields["exclude_databases"];
      if (error.fields["exclude_schemas"] !== undefined) found.schemas = error.fields["exclude_schemas"];
      if (error.fields["exclude_tables"] !== undefined) found.tables = error.fields["exclude_tables"];
      if (Object.keys(found).length > 0) setErrors(found);
    },
  });

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const found: typeof errors = {};
    if (schedule.frequency === "custom" && schedule.custom.trim() === "") found.schedule = t("databases.policy.customRequired");
    if (schedule.frequency !== "hourly" && schedule.frequency !== "custom" && !validTime(schedule.time)) found.time = t("databases.policy.timeInvalid");
    if (!validLimit(count, MAX_COUNT)) found.count = t("databases.policy.countInvalid");
    if (!validLimit(days, MAX_DAYS)) found.days = t("databases.policy.daysInvalid");
    if (!validGb(gb)) found.gb = t("databases.policy.gbInvalid");
    if (!destinationsValid(sends)) found.destinations = t("databases.policy.destinationLimitInvalid");
    if (remote && sends.length === 0) found.remote = t("databases.policy.remoteOnlyNeedsDestination");
    for (const kind of kinds) {
      const parsed = parseNames(left[kind], kind);
      if (parsed.invalid.length > 0) found[kind] = t("policyScope.drawer.invalid", { entries: parsed.invalid.join(", ") });
      else if (parsed.tooMany) found[kind] = t("policyScope.drawer.tooMany", { max: MAX_EXCLUSIONS });
    }
    setErrors(found);
    if (Object.keys(found).length === 0) save.mutate();
  };

  const formId = "backup-policy-form";

  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      size="md"
      title={scope === "all" ? t("policyScope.drawer.titleAll", { engine: display }) : configured ? t("databases.policy.editTitle") : t("databases.policy.newTitle")}
      description={scope === "all" ? t("policyScope.drawer.descriptionAll", { engine: display }) : t("databases.policy.description", { name })}
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>{t("databases.common.cancel")}</Button>
          <Button type="submit" form={formId} variant="primary" loading={save.isPending}>
            {t("databases.policy.save")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-6">
        <ScheduleFields value={schedule} onChange={setSchedule} scheduleError={errors.schedule} timeError={errors.time} />

        <KeepFields
          words={{
            legend: t("databases.policy.keepHere"),
            count: t("databases.policy.count"),
            countHelp: t("databases.policy.countHelp"),
            days: t("databases.policy.days"),
            daysHelp: t("databases.policy.daysHelp"),
            note: t("databases.policy.retentionNote"),
          }}
          count={count}
          days={days}
          onCount={setCount}
          onDays={setDays}
          countError={errors.count}
          daysError={errors.days}
        >
          <Field label={t("databases.policy.gb")} optional description={t("databases.policy.gbHelp")} error={errors.gb} className="w-44">
            <Input mono inputMode="decimal" value={gb} onValueChange={setGb} suffix="GB" />
          </Field>
        </KeepFields>

        <SendToFields
          enabled={open}
          words={{
            legend: t("databases.policy.sendTo"),
            none: t("databases.policy.noDestinations"),
            noneHint: t("databases.policy.noDestinationsHint"),
            failed: t("databases.policy.destinationsFailed"),
            keepAll: t("databases.policy.keepAll"),
            countSuffix: t("databases.policy.dumpsSuffix"),
            daysSuffix: t("databases.policy.daysSuffix"),
            countFor: (destination) => t("databases.policy.destinationCount", { destination }),
            daysFor: (destination) => t("databases.policy.destinationDays", { destination }),
            gbFor: (destination) => t("databases.policy.destinationGb", { destination }),
          }}
          value={sends}
          onChange={setSends}
          error={errors.destinations}
        />
        {sends.length > 0 || remote ? (
          <div className="flex flex-col gap-1">
            <Checkbox checked={remote} onCheckedChange={setRemote} label={t("databases.policy.remoteOnly")} description={t("databases.policy.remoteOnlyHelp")} />
            {errors.remote !== undefined ? <InlineMessage tone="error">{errors.remote}</InlineMessage> : null}
          </div>
        ) : null}

        {kinds.length > 0 ? (
          <fieldset className="flex min-w-0 flex-col gap-5">
            <legend className="mb-3 text-13 font-medium text-fg">{t("policyScope.drawer.leaveOut")}</legend>
            {kinds.map((kind) => {
              const words = exclusionWords(t, kind, engine);
              return (
                <Field key={kind} label={words.label} optional description={`${words.help} ${t("policyScope.drawer.names")}`} error={errors[kind]}>
                  <Textarea
                    mono
                    rows={2}
                    value={left[kind]}
                    onChange={(event) => setLeft((previous) => ({ ...previous, [kind]: event.target.value }))}
                    autoComplete="off"
                    spellCheck={false}
                  />
                </Field>
              );
            })}
            <p className="text-12 text-fg-faint">{t("policyScope.drawer.leaveOutHelp")}</p>
          </fieldset>
        ) : null}

        <fieldset className="flex min-w-0 flex-col gap-4">
          <legend className="mb-3 text-13 font-medium text-fg">{t("databases.policy.proof")}</legend>
          {formats ? (
            <Field label={t("databases.policy.format")} nativeLabel={false} description={t("databases.policy.formatHelp")}>
              <Select
                value={format}
                onValueChange={(value) => setFormat(value)}
                options={[
                  { value: "custom", label: t("databases.policy.formatCustom") },
                  { value: "plain", label: t("databases.policy.formatPlain") },
                  { value: "tar", label: t("databases.policy.formatTar") },
                ]}
              />
            </Field>
          ) : null}
          {testRestore ? <Checkbox checked={verify} onCheckedChange={setVerify} label={t("databases.policy.verifyRestore")} description={t("databases.policy.verifyRestoreHelp")} /> : null}
          <Checkbox checked={enabled} onCheckedChange={setEnabled} label={t("databases.policy.enabled")} description={t("databases.policy.enabledHelp")} />
        </fieldset>
        {save.isError && !(isApiError(save.error) && save.error.fields?.["schedule"]) ? <ErrorBlock live compact error={save.error} title={t("databases.policy.saveFailed")} /> : null}
      </form>
    </Drawer>
  );
}
